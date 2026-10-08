"""Блочная сборка страниц: сначала видимое, остальное — позже.

Страницу нельзя собрать в фоновом потоке: виджеты Qt создаются только в
потоке окна, и на время сборки окно замирает. Раньше страница собиралась
целиком за один присест — включая то, что лежит ниже края окна и чего
человек ещё не видит. На главной странице это 59% элементов, на отчёте
BlockCheck — почти всё: из карточек результатов в окно не попадала ни одна.

Здесь страница делится на блоки. Блок — это кусок страницы со своим местом
в раскладке (``LazyBlock``): пока он не собран, место под него занято пустым
виджетом примерной высоты, поэтому полоса прокрутки сразу почти верной длины.

Когда блок собирается:

* блоки первого экрана — при показе страницы, без таймеров, пока страница
  укладывается во время на открытие (``OPEN_BUDGET_MS``): на быстром
  компьютере человек сразу видит готовый экран. Что не уложилось —
  собирается после первого кадра и выплывает, так что окно не замирает и
  на слабом (так страница делает сама, см.
  ``BasePage.build_first_screen_blocks``);
* блок, до которого долистали, — сразу, как только его место попало в окно
  (пустой виджет получил событие перерисовки). Собранный блок выплывает;
* остальные блоки открытой страницы — по одному, в паузах: когда человек не
  держит кнопку мыши, когда отыграло появление страницы и когда молчит
  фоновая дорожка запуска. Рядом с занятым фоновым потоком сборка идёт в
  разы дольше (каждое обращение к Qt отпускает общий замок Python), поэтому
  на время сборки блока дорожка не начинает новую задачу — как и при
  сборке страниц про запас (``main.post_startup_idle_tasks``);
* блоки скрытой страницы ждут, пока её откроют.

Код, которому нужна страница целиком (команды страницам, обучающий тур,
шаг «назад» по журналу экранов), достраивает её сам: ``ensure_all_blocks``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import contextmanager

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from ui.user_idle import user_idle_ms
from ui.widgets.stagger_float_in import FLOAT_IN_GROUP_ATTR


# Сколько времени от щелчка страница может потратить, прежде чем показ
# перестанет собирать блоки первого экрана. Полкадра: если сборка страницы
# уже заняла заметное время, первый кадр не ждёт ещё и блоков — они
# собираются сразу после него, по одному присесту (``VISIBLE_BUDGET_MS``)
# между перерисовками, и выплывают в свой черёд. Страница, чей конструктор
# строит только каркас, успевает собрать при показе первый блок, так что
# первый кадр не бывает пустым. Замер (Telegram Proxy): до первого кадра
# 45 мс → 24 мс, самая долгая заминка 45 мс → 27 мс.
OPEN_BUDGET_MS = 8
# Сколько подряд собирать блоки, до которых долистали, прежде чем отдать
# управление окну (перерисовка, ввод). Блок не делится: если он один дольше
# этого, он всё равно соберётся целиком.
VISIBLE_BUDGET_MS = 12
# Перерыв между фоновыми блоками: окно успевает перерисоваться и принять ввод.
BACKGROUND_GAP_MS = 60
# Фоновые блоки начинаются не раньше, чем отыграет появление страницы:
# кадры появления важнее блоков, которых пока не видно.
BACKGROUND_START_DELAY_MS = 900
# Сколько человек должен не трогать мышь и клавиатуру, чтобы собрать блок.
BACKGROUND_IDLE_MS = 250
# Как часто проверять, не наступила ли пауза.
BACKGROUND_POLL_MS = 120
# Дольше этого блок паузы не ждёт: иначе при постоянном движении мыши
# страница так и осталась бы недостроенной, а пролистывание упиралось бы в
# сборку каждого блока.
BACKGROUND_WAIT_MAX_MS = 4_000
_QUEUE_ATTR = "_zapret_block_build_queue"


@contextmanager
def gui_build_turn():
    """Пока поток окна собирает страницу или блок, фоновая дорожка запуска новую задачу не начинает."""
    try:
        from main.post_startup_threading import gui_build_turn
    except Exception:
        yield
        return
    with gui_build_turn():
        yield


def _is_background_busy() -> bool:
    try:
        from main.post_startup_threading import is_local_lane_busy

        return bool(is_local_lane_busy())
    except Exception:
        return False


class LazyBlock(QWidget):
    """Место под блок страницы, который собирается позже."""

    built = pyqtSignal()

    def __init__(
        self,
        name: str,
        build: Callable[["LazyBlock"], None],
        *,
        estimated_height: int,
        spacing: int = 16,
        after_built: Callable[["LazyBlock"], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.name = str(name)
        self._build = build
        # Вызывается, когда блок собран и уже занял свою настоящую высоту.
        self._after_built = after_built
        self._built = False
        self._building = False
        self.setObjectName(f"lazyBlock_{self.name}")
        # При появлении страницы выплывает не блок целиком, а его карточки
        # по очереди — так же, как если бы они лежали прямо на странице.
        self.__dict__[FLOAT_IN_GROUP_ATTR] = True
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(int(spacing))
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        # Пока блок не собран, держим под него место: полоса прокрутки сразу
        # почти верной длины, а блок ниже не «подпрыгивает» вверх.
        self.setMinimumHeight(max(1, int(estimated_height)))
        block_build_queue().register(self)

    def is_built(self) -> bool:
        return self._built

    def ensure_built(self) -> bool:
        """Собирает блок сейчас. False — он уже собран или собирается."""
        if self._built or self._building:
            return False
        self._building = True
        # Блок на открытой странице на время сборки прячем: виджет, который
        # добавляют в видимое дерево, Qt сразу оформляет, раскладывает и
        # показывает — по одному, это в полтора раза дороже, чем собрать
        # скрытый блок и показать его один раз. Страница при этом не
        # дёргается: раскладка пересчитается уже с готовым блоком.
        shown = self.isVisible()
        if shown:
            self.hide()
        try:
            with gui_build_turn():
                self._build(self)
        finally:
            self._building = False
            # Блок считается собранным и после ошибки: иначе его строитель
            # вызывался бы снова на каждой перерисовке.
            self._built = True
            self._build = None
            self.setMinimumHeight(0)
            if shown:
                self.show()
            block_build_queue().forget(self)
        after_built, self._after_built = self._after_built, None
        if after_built is not None:
            after_built(self)
        self.built.emit()
        return True

    def paintEvent(self, event) -> None:  # noqa: N802
        # Место блока попало в окно: человек до него долистал. Достраивать
        # дерево виджетов прямо из перерисовки нельзя — просим очередь.
        if not self._built and not self._building:
            block_build_queue().request_visible(self)


class DeferredFill(QObject):
    """Наполнение уже стоящего на странице виджета, которое ждёт, пока он понадобится.

    Для списков и сеток, которые наполняются по событию (пришёл итог
    проверки): карточки, которых не видно, незачем создавать в тот же
    присест. Наполнение выполняется сразу, если виджет виден в окне; иначе —
    когда до него долистали или в паузе, по тем же правилам, что и блоки
    страницы (см. ``BlockBuildQueue``).

    Пока наполнение ждёт, виджет пуст: его хозяин держит под него место
    примерной высоты, а код, которому нужно содержимое прямо сейчас,
    вызывает ``ensure_built``.
    """

    def __init__(self, widget: QWidget, name: str) -> None:
        super().__init__(widget)
        self._widget = widget
        self.name = str(name)
        self._job: Callable[[], None] | None = None
        self._wanted: Callable[[QRect], bool] | None = None

    def schedule(
        self,
        job: Callable[[], None],
        *,
        wanted: Callable[[QRect], bool] | None = None,
        run_now_if_visible: bool = True,
    ) -> bool:
        """Назначает наполнение. True — выполнено сразу (виджет виден в окне).

        Новое наполнение заменяет то, которое ещё ждёт: показывать нужно
        последние данные.

        ``wanted(rect)`` — для виджета, наполненного частично: нужна ли
        следующая часть, если в окне показалась область ``rect`` виджета.
        Без него наполнение нужно, как только видна любая часть виджета.
        ``run_now_if_visible=False`` — не выполнять сразу, даже если виджет
        виден: так следующая часть уходит в очередь, а не в тот же присест.
        """
        waiting = self._job is not None
        self._job = job
        self._wanted = wanted
        widget = self._widget
        if run_now_if_visible and widget.isVisible() and not widget.visibleRegion().isEmpty():
            self.ensure_built()
            return True
        if not waiting:
            widget.installEventFilter(self)
            block_build_queue().register(self)
        return False

    def cancel(self) -> None:
        """Наполнение больше не нужно (виджет очищают)."""
        if self._job is None:
            return
        self._job = None
        self._stop_waiting()

    def is_built(self) -> bool:
        return self._job is None

    def ensure_built(self) -> bool:
        """Выполняет наполнение сейчас. False — ждать было нечего."""
        job, self._job = self._job, None
        if job is None:
            return False
        self._wanted = None
        self._stop_waiting()
        with gui_build_turn():
            job()
        return True

    def _stop_waiting(self) -> None:
        if not sip.isdeleted(self._widget):
            self._widget.removeEventFilter(self)
        block_build_queue().forget(self)

    # Очередь спрашивает у блока, открыта ли его страница.
    def isVisible(self) -> bool:  # noqa: N802 - как у QWidget
        return self._widget.isVisible()

    def window(self) -> QWidget:
        return self._widget.window()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        # Место виджета попало в окно: человек до него долистал.
        if event.type() == QEvent.Type.Paint and self._job is not None:
            wanted = self._wanted
            if wanted is None or wanted(event.rect()):
                block_build_queue().request_visible(self)
        return False


class BlockBuildQueue(QObject):
    """Очередь несобранных блоков: какие и когда собирать."""

    def __init__(
        self,
        *,
        idle_ms: Callable[[], int | None] = user_idle_ms,
        is_background_busy: Callable[[], bool] = _is_background_busy,
        mouse_pressed: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._idle_ms = idle_ms
        self._is_background_busy = is_background_busy
        self._mouse_pressed = mouse_pressed or _is_mouse_pressed
        self._clock = clock
        # Несобранные блоки в порядке создания (на странице — сверху вниз).
        self._pending: list[LazyBlock | DeferredFill] = []
        # Блоки, до которых долистали.
        self._visible: list[LazyBlock | DeferredFill] = []
        # С какого момента фоновый блок ждёт своей паузы.
        self._waiting_since: float | None = None
        # Раньше этого момента фоновые блоки не собираются.
        self._background_not_before = 0.0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timer)

    # ── учёт ─────────────────────────────────────────────────────────────

    def register(self, block: LazyBlock) -> None:
        if block not in self._pending:
            self._pending.append(block)

    def forget(self, block: LazyBlock) -> None:
        for blocks in (self._pending, self._visible):
            try:
                blocks.remove(block)
            except ValueError:
                pass

    def pending_count(self) -> int:
        self._drop_deleted()
        return len(self._pending)

    def request_visible(self, block: LazyBlock) -> None:
        """Место блока видно в окне: собрать его в ближайший оборот цикла событий."""
        if block.is_built() or block in self._visible:
            return
        self._visible.append(block)
        self._arm(0)

    def page_shown(self) -> None:
        """Страницу открыли: её остальные блоки можно достраивать в паузах."""
        self._background_not_before = self._clock() + BACKGROUND_START_DELAY_MS / 1000.0
        self._waiting_since = None
        if self._pending:
            self._arm(BACKGROUND_START_DELAY_MS)

    # ── сборка ───────────────────────────────────────────────────────────

    def _arm(self, delay_ms: int) -> None:
        delay = max(0, int(delay_ms))
        if self._timer.isActive() and self._timer.remainingTime() <= delay:
            return
        self._timer.start(delay)

    def _drop_deleted(self) -> None:
        self._pending = [block for block in self._pending if not sip.isdeleted(block)]
        self._visible = [block for block in self._visible if not sip.isdeleted(block)]

    def _on_timer(self) -> None:
        self._drop_deleted()
        if self._visible:
            self._build_visible()
            if self._visible:
                self._arm(0)
                return
        if not self._pending:
            return
        again_ms = self._build_one_in_background()
        if again_ms is not None:
            self._arm(again_ms)

    def _build_visible(self) -> None:
        deadline = time.perf_counter() + VISIBLE_BUDGET_MS / 1000.0
        while self._visible:
            block = self._visible.pop(0)
            if not block.is_built():
                self._build(block)
            if time.perf_counter() >= deadline:
                break

    @staticmethod
    def _build(block: LazyBlock) -> None:
        try:
            block.ensure_built()
        except Exception as exc:
            # Ошибка одного блока не должна останавливать очередь: остальные
            # блоки страницы всё равно нужно достроить.
            try:
                from log.log import log

                log(f"Блок {block.name} не собран: {exc}", "WARNING")
            except Exception:
                pass

    def _next_background_block(self) -> LazyBlock | None:
        for block in self._pending:
            # Блоки скрытой страницы ждут, пока её откроют; при свёрнутом
            # окне достраивать тоже некому смотреть.
            if block.isVisible() and not block.window().isMinimized():
                return block
        return None

    def _build_one_in_background(self) -> int | None:
        """Собирает один фоновый блок, если сейчас можно.

        Возвращает, через сколько прийти снова; None — приходить незачем,
        пока какую-нибудь страницу не откроют (``page_shown``).
        """
        now = self._clock()
        wait_ms = (self._background_not_before - now) * 1000.0
        if wait_ms > 0:
            return int(wait_ms) + 1
        block = self._next_background_block()
        if block is None:
            self._waiting_since = None
            return None
        if self._waiting_since is None:
            self._waiting_since = now
        if (now - self._waiting_since) * 1000.0 < BACKGROUND_WAIT_MAX_MS and not self._is_good_moment():
            return BACKGROUND_POLL_MS
        self._waiting_since = None
        self._build(block)
        return BACKGROUND_GAP_MS

    def _is_good_moment(self) -> bool:
        if self._safe(self._mouse_pressed, default=False):
            return False
        if self._safe(self._is_background_busy, default=False):
            return False
        idle = self._safe(self._idle_ms, default=None)
        return idle is None or int(idle) >= BACKGROUND_IDLE_MS

    @staticmethod
    def _safe(fn, *, default):
        try:
            return fn()
        except Exception:
            return default


def _is_mouse_pressed() -> bool:
    return QGuiApplication.mouseButtons() != Qt.MouseButton.NoButton


def ensure_page_blocks(page) -> None:
    """Достраивает страницу целиком, если она собирается блоками.

    Для кода, который обращается к любым виджетам страницы: команды
    страницам, обучающий тур, шаг «назад» по журналу экранов.
    """
    ensure_all_blocks = getattr(page, "ensure_all_blocks", None)
    if callable(ensure_all_blocks):
        ensure_all_blocks()


def block_build_queue() -> BlockBuildQueue:
    """Общая очередь блоков приложения (создаётся при первом обращении)."""
    app = QCoreApplication.instance()
    queue = getattr(app, _QUEUE_ATTR, None) if app is not None else None
    if queue is None:
        queue = BlockBuildQueue(parent=app)
        if app is not None:
            setattr(app, _QUEUE_ATTR, queue)
    return queue


__all__ = [
    "BACKGROUND_GAP_MS",
    "BACKGROUND_IDLE_MS",
    "BACKGROUND_POLL_MS",
    "BACKGROUND_START_DELAY_MS",
    "BACKGROUND_WAIT_MAX_MS",
    "BlockBuildQueue",
    "DeferredFill",
    "LazyBlock",
    "OPEN_BUDGET_MS",
    "VISIBLE_BUDGET_MS",
    "block_build_queue",
    "ensure_page_blocks",
    "gui_build_turn",
]
