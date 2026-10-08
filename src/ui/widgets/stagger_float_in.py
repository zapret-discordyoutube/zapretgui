"""Блоки выплывают снизу вверх по очереди, когда контейнер показывают.

Помощник вешается на контейнер: содержимое каждой страницы (BasePage) и
вкладки «О программе». При каждом показе он проходит по виджетам его
раскладки сверху вниз, и каждый видимый виджет (блок) выплывает: сначала
прозрачный и чуть ниже своего места, потом плавно встаёт на место.

Как это нарисовано. Блок один раз снимается в картинку — только та его
часть, что сейчас видна в окне. Настоящий блок на время полёта прячется
маской (место в раскладке за ним остаётся, соседи не прыгают), а картинку
рисует один прозрачный слой поверх контейнера. Кадры идут по общему такту
``ui.frame_clock``. В конце маска снимается — на месте картинки стоит
настоящий блок.

Раньше у каждого блока был свой графический эффект, своя анимация и свой
таймер, а блок перерисовывался со всеми детьми на CPU несколько раз за
полёт (замер на win10: 43% главного потока при переключении страниц; общий
замер 30 страниц: появление стоило в 2,4 раза дороже, чем сборка страницы
и её первый показ вместе). Теперь блок рисуется один раз, а кадр — это
наложение готовых картинок. Картинка кладётся точка в точку экрана: при
масштабе 125–150 % растянутое наложение шло у Qt медленным путём.

Снимок делается не при показе, а когда до блока дошла очередь: снимки
расходятся по кадрам, а содержимое, пришедшее за время ожидания, попадает
в картинку. Если блок изменил размер или получил новых детей уже в полёте,
он сразу встаёт на место — картинка устарела.

Выплывают только блоки, которые сейчас видны на экране.

Появление есть у каждой страницы, выключать его нельзя. Если виджет большой
и рисуется вручную (например, сетка плиток hosts) и ему нужен вход по
частям, ему дают метод ``play_float_in(delay_ms)``: помощник вызывает его
в общей очереди, а при скрытии — ``finish_float_in()``. Такой вход рисуется
теми же константами и той же плавностью (``float_in_progress``).

``skip_float_in`` — только для виджетов, у которых вход уже есть свой
(вкладки «О программе» со своим помощником, девиз, шапка с глобусом);
архитектурная проверка не пускает его в другие файлы.
"""

from __future__ import annotations

import math
import time

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QPointF, QRect, Qt, QTimer
from PyQt6.QtGui import QPainter, QPixmap, QRegion, QTransform
from PyQt6.QtWidgets import QWidget

from ui.animation_policy import are_live_animations_enabled
from ui.frame_clock import BASE_FRAME_MS, frame_clock


FLOAT_IN_DURATION_MS = 460
FLOAT_IN_STEP_MS = 70
FLOAT_IN_RISE_PX = 16.0
# Дальше этого номера блоки идут без дополнительной задержки.
_MAX_STAGGERED = 8
# Снимок больше этого (в точках экрана) не делаем: блок встаёт на место сразу.
_MAX_SNAPSHOT_PIXELS = 4_000_000
# Запас к концу полёта для страховочного таймера (см. _BlockFloatIn._arm_deadline).
_DEADLINE_MARGIN_MS = 250
_CONTROLLER_ATTR = "_zapret_stagger_float_in"
NO_FLOAT_IN_ATTR = "_zapret_no_float_in"
# Виджет-группа (блок страницы): выплывает не он сам, а виджеты его раскладки
# по очереди — как если бы они лежали прямо в контейнере.
FLOAT_IN_GROUP_ATTR = "_zapret_float_in_group"
# Метод «своего входа» у виджета: play_float_in(delay_ms) / finish_float_in().
OWN_FLOAT_IN_METHOD = "play_float_in"
OWN_FLOAT_IN_FINISH = "finish_float_in"
# Маска «ничего не видно»: пустая область у Qt означает «маски нет», поэтому
# берём одну точку за пределами виджета.
_HIDDEN_MASK = QRegion(QRect(-2, -2, 1, 1))
_SHIFT_ONLY = (QTransform.TransformationType.TxNone, QTransform.TransformationType.TxTranslate)
# Слои появления по контейнерам: ключ — адрес контейнера на стороне Qt.
# Слой живёт, только пока в контейнере что-то летит.
_ENGINES: dict[int, "_BlockFloatIn"] = {}
# События блока, после которых его картинка устарела.
_STALE_EVENTS = (
    QEvent.Type.Resize,
    QEvent.Type.Hide,
    QEvent.Type.ParentChange,
)
_CHILD_EVENTS = (QEvent.Type.ChildAdded, QEvent.Type.ChildRemoved)


def float_in_progress(elapsed_ms: float) -> float:
    """Доля пройденного пути выплывания (0..1) с той же плавностью OutCubic."""
    linear = max(0.0, min(1.0, float(elapsed_ms) / FLOAT_IN_DURATION_MS))
    return 1.0 - (1.0 - linear) ** 3


class _Block:
    """Один выплывающий виджет: когда начинает и какой картинкой рисуется."""

    __slots__ = ("widget", "starts_at", "pixmap", "rect", "drawn")

    def __init__(self, widget: QWidget, starts_at: float) -> None:
        self.widget = widget
        self.starts_at = starts_at
        # Картинка видимой части блока в точках экрана; None — ещё не снята.
        self.pixmap: QPixmap | None = None
        # Где эта часть лежит в контейнере.
        self.rect = QRect()
        # Что нарисовано в прошлом кадре: (шаг прозрачности, сдвиг в точках экрана).
        self.drawn: tuple[int, int] | None = None

    def progress(self, now: float) -> float:
        return float_in_progress((now - self.starts_at) * 1000.0)


class _Overlay(QWidget):
    """Прозрачный слой поверх контейнера: рисует картинки летящих блоков."""

    def __init__(self, container: QWidget, engine: "_BlockFloatIn") -> None:
        super().__init__(container)
        self._engine = engine
        self.setObjectName("floatInOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setGeometry(container.rect())
        self.raise_()
        self.show()

    def paintEvent(self, event) -> None:  # noqa: N802
        blocks = [block for block in self._engine._blocks if block.pixmap is not None]
        if not blocks:
            return
        now = time.monotonic()
        painter = QPainter(self)
        # Дальше координаты painter — это точки экрана: картинка кладётся
        # точка в точку, без растяжения (иначе при 125–150 % Qt идёт
        # медленным путём, а картинка мылится).
        to_device = painter.deviceTransform()
        scale = to_device.m11() or 1.0
        shift_x, shift_y = to_device.dx(), to_device.dy()
        if to_device.type() in _SHIFT_ONLY:
            painter.setWorldTransform(QTransform.fromTranslate(-shift_x, -shift_y))
        else:
            inverted, invertible = to_device.inverted()
            if not invertible:
                painter.end()
                return
            painter.setWorldTransform(inverted)
        for block in blocks:
            progress = block.progress(now)
            if progress <= 0.0 or progress >= 1.0:
                continue
            painter.setOpacity(progress)
            rise = FLOAT_IN_RISE_PX * (1.0 - progress)
            painter.drawPixmap(
                QPointF(
                    round(block.rect.x() * scale + shift_x),
                    round((block.rect.y() + rise) * scale + shift_y),
                ),
                block.pixmap,
            )
        painter.end()


class _BlockFloatIn(QObject):
    """Появление блоков одного контейнера: один слой, один такт кадров."""

    def __init__(self, container: QWidget) -> None:
        super().__init__(container)
        self._container = container
        self._blocks: list[_Block] = []
        self._overlay: _Overlay | None = None
        self._frames = frame_clock().subscribe(self._on_frame, interval_ms=BASE_FRAME_MS, owner=self)
        # Страховка: такт кадров стоит при заблокированном сеансе и выключенном
        # дисплее. Без неё блоки остались бы спрятанными до его возобновления.
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self._on_deadline)
        container.installEventFilter(self)

    # ── блоки ────────────────────────────────────────────────────────────

    def add(self, widget: QWidget, delay_ms: int) -> None:
        previous = self.block_of(widget)
        if previous is not None:
            self._release(previous)
        widget.setMask(_HIDDEN_MASK)
        widget.installEventFilter(self)
        self._blocks.append(_Block(widget, time.monotonic() + max(0, int(delay_ms)) / 1000.0))
        if self._overlay is None:
            self._overlay = _Overlay(self._container, self)
        else:
            self._overlay.raise_()
        self._frames.start()
        self._arm_deadline()

    def block_of(self, widget: QWidget) -> _Block | None:
        for block in self._blocks:
            if block.widget is widget:
                return block
        return None

    def has_blocks(self) -> bool:
        return bool(self._blocks)

    def release(self, widget: QWidget) -> None:
        """Блок сразу встаёт на место."""
        block = self.block_of(widget)
        if block is not None:
            self._release(block)
            self._stop_if_idle()

    def release_all(self) -> None:
        for block in tuple(self._blocks):
            self._release(block)
        self._stop_if_idle()

    def _release(self, block: _Block) -> None:
        try:
            self._blocks.remove(block)
        except ValueError:
            return
        widget = block.widget
        if not sip.isdeleted(widget):
            widget.removeEventFilter(self)
            widget.clearMask()
        # Стираем последний кадр картинки через контейнер, а не через слой:
        # слой к моменту перерисовки может быть уже убран.
        if not block.rect.isEmpty() and not sip.isdeleted(self._container):
            self._container.update(self._flight_area(block))

    def _stop_if_idle(self) -> None:
        if self._blocks:
            return
        self._frames.stop()
        self._deadline.stop()
        overlay, self._overlay = self._overlay, None
        if overlay is not None and not sip.isdeleted(overlay):
            # Слой прозрачный и уже пустой: его уход не должен перерисовывать
            # всю страницу. Обычные hide() и удаление помечают грязной всю его
            # площадь, поэтому сначала выключаем ему обновления и вынимаем из
            # контейнера (он при этом скрывается и окном не становится).
            # Места блоков уже обновлены через контейнер.
            overlay.setUpdatesEnabled(False)
            overlay.setParent(None)
            overlay.deleteLater()
        container = self._container
        if not sip.isdeleted(container):
            container.removeEventFilter(self)
            if _ENGINES.get(sip.unwrapinstance(container)) is self:
                del _ENGINES[sip.unwrapinstance(container)]
        self.deleteLater()

    # ── кадры ────────────────────────────────────────────────────────────

    def _on_frame(self) -> None:
        if sip.isdeleted(self._container):
            return
        now = time.monotonic()
        # Видимая часть контейнера нужна только блокам, до которых дошла
        # очередь: считаем её не чаще раза за кадр.
        visible: QRect | None = None
        for block in tuple(self._blocks):
            widget = block.widget
            if sip.isdeleted(widget):
                self._blocks.remove(block)
                continue
            if now < block.starts_at:
                continue
            if block.pixmap is None:
                if visible is None:
                    visible = self._container.visibleRegion().boundingRect()
                if not self._snapshot(block, visible):
                    self._release(block)
                    continue
            progress = block.progress(now)
            if progress >= 1.0:
                self._release(block)
                continue
            scale = block.pixmap.height() / max(1, block.rect.height())
            drawn = (round(progress * 255), round(FLOAT_IN_RISE_PX * (1.0 - progress) * scale))
            if drawn != block.drawn:
                block.drawn = drawn
                self._update_overlay(block)
        self._stop_if_idle()

    def _snapshot(self, block: _Block, visible: QRect) -> bool:
        """Снимает видимую часть блока. False — выплывать нечему или некому смотреть."""
        widget = block.widget
        if widget.isHidden():
            return False
        part = widget.geometry().intersected(visible)
        if part.isEmpty():
            return False
        pixmap = widget.grab(part.translated(-widget.pos()))
        if pixmap.isNull() or pixmap.width() * pixmap.height() > _MAX_SNAPSHOT_PIXELS:
            return False
        # Картинка — в точках экрана: слой кладёт её точка в точку.
        pixmap.setDevicePixelRatio(1.0)
        block.pixmap = pixmap
        block.rect = part
        return True

    @staticmethod
    def _flight_area(block: _Block) -> QRect:
        """Место блока на слое вместе с запасом на подъём."""
        return block.rect.adjusted(-1, -1, 1, math.ceil(FLOAT_IN_RISE_PX) + 1)

    def _update_overlay(self, block: _Block) -> None:
        overlay = self._overlay
        if overlay is None or sip.isdeleted(overlay) or block.rect.isEmpty():
            return
        overlay.update(self._flight_area(block))

    # ── страховка и события ──────────────────────────────────────────────

    def _arm_deadline(self) -> None:
        last = max(block.starts_at for block in self._blocks)
        wait_ms = (last - time.monotonic()) * 1000.0 + FLOAT_IN_DURATION_MS + _DEADLINE_MARGIN_MS
        self._deadline.start(max(1, int(wait_ms)))

    def _on_deadline(self) -> None:
        now = time.monotonic()
        for block in tuple(self._blocks):
            if (now - block.starts_at) * 1000.0 >= FLOAT_IN_DURATION_MS:
                self._release(block)
        if self._blocks:
            self._arm_deadline()
        self._stop_if_idle()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        kind = event.type()
        if watched is self._container:
            if kind == QEvent.Type.Resize:
                if self._overlay is not None:
                    self._overlay.setGeometry(self._container.rect())
            elif kind == QEvent.Type.Hide:
                self.release_all()
            return False
        block = self.block_of(watched)
        if block is None:
            return False
        if kind in _STALE_EVENTS:
            # До снимка блок ещё раскладывают (только что собранный блок
            # получает свой первый размер): это попадёт в картинку.
            if kind != QEvent.Type.Resize or block.pixmap is not None:
                self.release(watched)
        elif kind == QEvent.Type.Move:
            if block.pixmap is not None:
                # Блок подвинули (выше появилось содержимое): картинка та же,
                # летит к новому месту.
                self._update_overlay(block)
                block.rect.translate(event.pos() - event.oldPos())
                self._update_overlay(block)
        elif kind in _CHILD_EVENTS:
            # До снимка блок ещё могут достраивать — это попадёт в картинку.
            if block.pixmap is not None and event.child().isWidgetType():
                self.release(watched)
        return False


def _engine_of(container: QWidget, *, create: bool) -> _BlockFloatIn | None:
    key = sip.unwrapinstance(container)
    engine = _ENGINES.get(key)
    if engine is not None and (sip.isdeleted(engine) or engine.parent() is not container):
        # Контейнер удалён вместе со слоем, а его адрес достался другому виджету.
        del _ENGINES[key]
        engine = None
    if engine is None and create:
        engine = _BlockFloatIn(container)
        _ENGINES[key] = engine
        # Контейнер удалили прямо в полёте: слой уходит вместе с ним.
        engine.destroyed.connect(lambda *_, key=key, mark=id(engine): _forget_engine(key, mark))
    return engine


def _forget_engine(key: int, mark: int) -> None:
    engine = _ENGINES.get(key)
    if engine is not None and id(engine) == mark:
        del _ENGINES[key]


def _can_animate(widget: QWidget) -> bool:
    if not are_live_animations_enabled():
        return False
    window = widget.window()
    if window is not None and window.isMinimized():
        return False
    # Экран никто не видит (сеанс заблокирован, дисплей выключен).
    return not frame_clock().is_paused()


def _start_block(widget: QWidget, delay_ms: int) -> bool:
    container = widget.parentWidget()
    if container is None:
        return False
    flying = _engine_of(container, create=False)
    if flying is not None and flying.block_of(widget) is not None:
        # Виджет уже выплывает (его маска — наша): начинаем заново.
        flying.add(widget, delay_ms)
        return True
    if widget.graphicsEffect() is not None or not widget.mask().isEmpty():
        # Чужой эффект (тень и т.п.) и чужую маску не подменяем.
        return False
    engine = _engine_of(container, create=True)
    engine.add(widget, delay_ms)
    return True


def is_floating_in(widget: QWidget) -> bool:
    """Виджет сейчас выплывает (ещё не встал на место)."""
    return float_in_progress_of(widget) is not None


def float_in_progress_of(widget: QWidget) -> float | None:
    """Доля пути выплывающего виджета (0..1). None — он не выплывает."""
    if sip.isdeleted(widget):
        return None
    container = widget.parentWidget()
    engine = _engine_of(container, create=False) if container is not None else None
    block = engine.block_of(widget) if engine is not None else None
    if block is None:
        return None
    return block.progress(time.monotonic())


def _is_hidden_on_purpose(widget: QWidget) -> bool:
    """Виджет спрятан кодом, а не просто ещё не показан.

    Виджет, только что добавленный на видимую страницу, Qt показывает в
    следующем обороте цикла событий; до этого он тоже «скрыт».
    """
    return widget.isHidden() and widget.testAttribute(Qt.WidgetAttribute.WA_WState_ExplicitShowHide)


def _layout_targets(container: QWidget, *, just_built: bool = False) -> list[QWidget]:
    """Виджеты раскладки контейнера, которым есть смысл выплывать.

    ``just_built`` — контейнер собран только что и ещё не разложен: видно ли
    виджет в окне, проверит уже слой появления в момент снимка.
    """
    layout = container.layout()
    if layout is None:
        return []
    result: list[QWidget] = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget() if item is not None else None
        if widget is None:
            continue
        if _is_hidden_on_purpose(widget) if just_built else widget.isHidden():
            continue
        if widget.__dict__.get(NO_FLOAT_IN_ATTR):
            continue
        if widget.__dict__.get(FLOAT_IN_GROUP_ATTR):
            result.extend(_layout_targets(widget, just_built=just_built))
            continue
        if widget.graphicsEffect() is not None and not _has_own_float_in(widget):
            # Чужой эффект (тень и т.п.) не подменяем.
            continue
        if not just_built and widget.visibleRegion().isEmpty():
            # Ниже края окна: выплывать там некому смотреть.
            continue
        result.append(widget)
    return result


class StaggeredFloatIn(QObject):
    """Подключается к контейнеру и оживляет его при каждом показе."""

    def __init__(self, container: QWidget) -> None:
        super().__init__(container)
        self._container = container
        # Виджеты со своим входом, запущенные в этот показ.
        self._own: list[QWidget] = []
        # Группы, чьи виджеты запущены в этот показ (у группы свой слой).
        self._groups: list[QWidget] = []
        container.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._container:
            kind = event.type()
            if kind == QEvent.Type.Show:
                # Раскладка к этому моменту ещё может досчитываться.
                QTimer.singleShot(0, self.play)
            elif kind == QEvent.Type.Hide:
                self.finish_all()
        return False

    def _engines(self) -> list[_BlockFloatIn]:
        engines = []
        for container in (self._container, *self._groups):
            if sip.isdeleted(container):
                continue
            engine = _engine_of(container, create=False)
            if engine is not None:
                engines.append(engine)
        return engines

    def is_running(self) -> bool:
        return any(engine.has_blocks() for engine in self._engines()) or bool(self._own)

    def _targets(self) -> list[QWidget]:
        return _layout_targets(self._container)

    def play(self) -> None:
        if sip.isdeleted(self._container) or not self._container.isVisible():
            return
        if not _can_animate(self._container):
            return
        self.finish_all()
        for order, widget in enumerate(self._targets()):
            delay = min(order, _MAX_STAGGERED) * FLOAT_IN_STEP_MS
            if _has_own_float_in(widget):
                getattr(widget, OWN_FLOAT_IN_METHOD)(delay)
                self._own.append(widget)
            elif _start_block(widget, delay):
                group = widget.parentWidget()
                if group is not self._container and group not in self._groups:
                    self._groups.append(group)

    def finish_all(self) -> None:
        for engine in self._engines():
            engine.release_all()
        self._groups = []
        own, self._own = self._own, []
        for widget in own:
            finish = getattr(widget, OWN_FLOAT_IN_FINISH, None)
            if not sip.isdeleted(widget) and callable(finish):
                finish()


def float_in_group(group: QWidget) -> None:
    """Виджеты только что собранной группы выплывают по очереди.

    Для блока страницы, который достроили уже после её показа: те его
    виджеты, что видны в окне, выплывают; остальные сразу стоят на месте.
    """
    if sip.isdeleted(group) or not group.isVisible() or not _can_animate(group):
        return
    for order, widget in enumerate(_layout_targets(group, just_built=True)):
        delay = min(order, _MAX_STAGGERED) * FLOAT_IN_STEP_MS
        if _has_own_float_in(widget):
            getattr(widget, OWN_FLOAT_IN_METHOD)(delay)
        else:
            _start_block(widget, delay)


def _has_own_float_in(widget: QWidget) -> bool:
    return callable(getattr(widget, OWN_FLOAT_IN_METHOD, None))


def attach_stagger_float_in(container: QWidget) -> StaggeredFloatIn:
    """Подключает выплывание блоков к контейнеру (один раз на контейнер)."""
    controller = container.__dict__.get(_CONTROLLER_ATTR)
    if controller is None:
        controller = StaggeredFloatIn(container)
        container.__dict__[_CONTROLLER_ATTR] = controller
    return controller


def stagger_float_in(container: QWidget) -> StaggeredFloatIn | None:
    try:
        return container.__dict__.get(_CONTROLLER_ATTR)
    except Exception:
        return None


def float_in(widget: QWidget, *, delay_ms: int = 0) -> bool:
    """Один виджет выплывает снизу (новая строка результата, достроенный блок).

    Возвращает False, если выплывания не будет: анимации выключены, окно
    свёрнуто, виджет ещё никуда не вставлен или у него уже есть свой эффект.
    Виджет, которого сейчас не видно в окне (ниже края), встаёт на место без
    полёта: выплывать там некому смотреть.
    """
    if sip.isdeleted(widget) or not _can_animate(widget):
        return False
    return _start_block(widget, delay_ms)


def skip_float_in(widget: QWidget) -> QWidget:
    """Помечает виджет, у которого вход уже есть свой (например, девиз).

    Не для того, чтобы просто выключить появление: большой виджет с ручной
    отрисовкой получает ``play_float_in``. Разрешённые файлы — в
    ``app.architecture_checks.check_skip_float_in_is_allowlisted``.
    """
    widget.__dict__[NO_FLOAT_IN_ATTR] = True
    return widget


__all__ = [
    "FLOAT_IN_DURATION_MS",
    "FLOAT_IN_GROUP_ATTR",
    "FLOAT_IN_RISE_PX",
    "FLOAT_IN_STEP_MS",
    "StaggeredFloatIn",
    "attach_stagger_float_in",
    "float_in",
    "float_in_group",
    "float_in_progress",
    "float_in_progress_of",
    "is_floating_in",
    "skip_float_in",
    "stagger_float_in",
]
