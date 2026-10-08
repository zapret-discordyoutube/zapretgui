# ui/pages/base_page.py
"""Базовый класс для страниц — использует qfluentwidgets ScrollArea."""

import time as _time
from PyQt6.QtCore import Qt, QEvent, QPoint, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QBoxLayout, QWidget, QVBoxLayout, QFrame, QSizePolicy,
)
from qfluentwidgets import (
    BodyLabel,
    PlainTextEdit as _FluentPlainTextEdit,
    ScrollArea as _FluentScrollArea,
    StrongBodyLabel,
    TextEdit as _FluentTextEdit,
    TitleLabel,
)

from app.ui_texts import tr as tr_catalog, normalize_language
from ui.accessibility import remove_scrollbar_arrow_buttons_from_tab_order, set_state_text
from ui.block_build import OPEN_BUDGET_MS, LazyBlock, block_build_queue
from ui.navigation.history import ScreenState
from ui.performance_metrics import log_page_timing
from ui.smooth_scroll import (
    apply_editor_smooth_scroll_preference,
    apply_page_smooth_scroll_preference,
    apply_smooth_scroll_mode,
)
from ui.widgets.stagger_float_in import attach_stagger_float_in, float_in_group


class ScrollBlockingPlainTextEdit(_FluentPlainTextEdit):
    """PlainTextEdit с fluent-скроллбарами, не пропускающий прокрутку к родителю.

    SmoothScrollDelegate (из qfluentwidgets) намеренно пропускает wheel-событие
    к родителю когда достигнута граница скролла (return False в eventFilter).
    Переопределяем wheelEvent чтобы принять событие и не дать BasePage прокрутиться.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("noDrag", True)
        apply_editor_smooth_scroll_preference(self)

    def set_smooth_scroll_enabled(self, enabled: bool) -> None:
        apply_smooth_scroll_mode(self, enabled)

    def wheelEvent(self, event):
        # SmoothScrollDelegate поглощает событие когда НЕ у границы (возвращает True),
        # поэтому этот метод вызывается ТОЛЬКО у границы скролла.
        event.accept()


class ScrollBlockingTextEdit(_FluentTextEdit):
    """TextEdit с fluent-скроллбарами, не пропускающий прокрутку к родителю."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("noDrag", True)
        apply_editor_smooth_scroll_preference(self)

    def set_smooth_scroll_enabled(self, enabled: bool) -> None:
        apply_smooth_scroll_mode(self, enabled)

    def wheelEvent(self, event):
        event.accept()


# Блок, которого нет в раскладке страницы или который лежит на скрытой
# вкладке: «начинается» заведомо ниже любого окна.
_FAR_BELOW = 10 ** 6
# Показ страницы считается продолжением щелчка, если от щелчка прошло меньше
# этого: тогда время её сборки идёт в счёт времени на открытие.
_OPEN_CLICK_WINDOW_MS = 2_000.0


class BasePage(_FluentScrollArea):
    """Базовый класс для страниц контента.

    Uses qfluentwidgets ScrollArea for smooth Fluent-style scrolling.
    The public API (self.layout, add_widget, add_spacing, add_section_title,
    self.title_label, self.subtitle_label) is shared by all pages.
    """

    # Внутри страницы открылся другой экран (вкладка, отчёт, подробности):
    # журнал экранов окна запишет его как шаг для кнопки «назад».
    navigation_screen_changed = pyqtSignal()

    def set_navigation_back(self, go_back) -> None:
        """Журнал экранов окна даёт странице свой шаг «назад»: им отвечает Esc."""
        self._navigation_back = go_back

    def navigation_step_back(self) -> bool:
        """Вернуться на экран, где человек был до этого. False — возвращаться некуда."""
        go_back = getattr(self, "_navigation_back", None)
        if not callable(go_back):
            return False
        try:
            return bool(go_back())
        except Exception:
            return False

    def _typing_in_text_field(self) -> bool:
        """Фокус в поле ввода: там Esc — клавиша поля, а не команда «назад»."""
        focused = QApplication.focusWidget()
        if focused is None:
            return False
        # По имени класса Qt: так распознаются и обычные поля, и их fluent-обёртки.
        if not any(focused.inherits(name) for name in ("QLineEdit", "QPlainTextEdit", "QTextEdit")):
            return False
        read_only = getattr(focused, "isReadOnly", None)
        return not (callable(read_only) and read_only())

    def keyPressEvent(self, event):  # noqa: N802
        # Esc — шаг назад по журналу экранов, как кнопка «назад» в шапке окна.
        # Клавиша доходит сюда, только если её не забрал виджет в фокусе
        # (открытое меню, строка поиска, диалог закрываются ею сами).
        if (
            event.key() == Qt.Key.Key_Escape
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and not self._typing_in_text_field()
            and self.navigation_step_back()
        ):
            event.accept()
            return
        super().keyPressEvent(event)

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        parent=None,
        *,
        title_key: str | None = None,
        subtitle_key: str | None = None,
    ):
        super().__init__(parent)
        self._ui_language = self._resolve_ui_language()
        self._title_key = title_key
        self._subtitle_key = subtitle_key
        self._title_fallback = title
        self._subtitle_fallback = subtitle
        self._section_title_bindings: list[tuple[object, str, str]] = []
        self._page_registry_name = None
        self._page_first_activation_done = False
        self._page_lifecycle_generation = 0
        self._page_load_generation = 0
        self._page_open_metric_started_at = 0.0
        self._page_open_metric_first_show = True
        # Страница встроена вкладкой в другую страницу: свой заголовок скрыт
        # насовсем, смена языка его не возвращает.
        self._page_header_hidden = False
        self._content_paint_metric_targets: dict[int, dict[str, object]] = {}
        self._content_paint_metric_next_token = 0
        self._ready_callbacks: list[object] = []
        self._cleanup_in_progress = False
        # Блоки страницы, которые собираются позже (см. add_lazy_block).
        self._lazy_blocks: dict[str, LazyBlock] = {}
        # Какой блок создаёт какой атрибут страницы: обращение к атрибуту
        # несобранного блока достраивает блок (см. __getattr__).
        self._lazy_attr_blocks: dict[str, str] = {}
        # Блоки, достроенные обращением к их атрибуту: (блок, атрибут).
        self._forced_blocks: list[tuple[str, str]] = []
        # Раскладка блока, который собирается прямо сейчас: в неё кладут
        # add_widget, add_spacing и add_section_title.
        self._block_layouts: list[QVBoxLayout] = []
        self._building_first_screen = False
        self._page_theme_refresh = self._create_page_theme_refresh_if_needed()

        # Ensure objectName is set (required by FluentWindow.addSubInterface)
        if not self.objectName():
            self.setObjectName(self.__class__.__name__)

        if self._title_key:
            title = tr_catalog(self._title_key, language=self._ui_language, default=title)
        if self._subtitle_key:
            subtitle = tr_catalog(self._subtitle_key, language=self._ui_language, default=subtitle)

        # --- ScrollArea config ---
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            "QScrollArea { background-color: transparent; border: none; }"
        )

        # Применяем обычную прокрутку для страниц и списков.
        apply_page_smooth_scroll_preference(self)
        remove_scrollbar_arrow_buttons_from_tab_order(self)

        # --- Content container ---
        self.content = QWidget(self)
        self.content.setStyleSheet("background-color: transparent;")
        # Expanding horizontally so the content fills the viewport width and
        # word-wrapped labels can actually wrap instead of overflowing.
        self.content.setMinimumWidth(0)
        self.content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setWidget(self.content)

        # --- Main layout ---
        self.vBoxLayout = QVBoxLayout(self.content)
        self.vBoxLayout.setContentsMargins(36, 28, 36, 28)
        self.vBoxLayout.setSpacing(16)
        self.vBoxLayout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Public layout alias used by page subclasses.
        self.layout = self.vBoxLayout

        # --- Title ---
        self.title_label = TitleLabel(self.content)
        self.title_label.setText(title)
        set_state_text(self.title_label, f"Заголовок страницы: {title}")
        self.vBoxLayout.addWidget(self.title_label)

        # --- Subtitle ---
        if subtitle:
            self.subtitle_label = BodyLabel(self.content)
            self.subtitle_label.setText(subtitle)
            set_state_text(self.subtitle_label, f"Описание страницы: {subtitle}")
            self.subtitle_label.setWordWrap(True)
            # QLabel сохраняет слишком широкую подсказку размера даже при
            # включённом переносе. Не учитываем её по горизонтали, чтобы
            # компоновка сужала описание до окна, а Qt переносил строки.
            self.subtitle_label.setMinimumWidth(0)
            self.subtitle_label.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred,
            )
            self.vBoxLayout.addWidget(self.subtitle_label)
        else:
            self.subtitle_label = None

        # При каждом открытии страницы её карточки выплывают снизу по очереди.
        attach_stagger_float_in(self.content)

    def _set_page_registry_name(self, page_name) -> None:
        self._page_registry_name = page_name

    def _page_label(self):
        return self._page_registry_name or self.__class__.__name__

    def _resolve_page_budget(self, budget_attr: str) -> int | None:
        page_name = getattr(self, "_page_registry_name", None)
        if page_name is None:
            return None
        try:
            from ui.page_registry import get_page_performance_profile

            return int(getattr(get_page_performance_profile(page_name), budget_attr))
        except Exception:
            return None

    def navigation_screen(self) -> ScreenState:
        """Что сейчас открыто внутри страницы. Страницы без вложенных экранов не переопределяют."""
        return ScreenState()

    def restore_navigation_screen(self, screen: ScreenState) -> bool:
        """Открывает записанный экран заново; False — такого экрана больше нет."""
        return not screen.key

    def on_page_activated(self) -> None:
        pass

    def on_page_hidden(self) -> None:
        pass

    def invalidate_page_cache(self, reason: str) -> None:
        self.cancel_page_loads(reason=reason)

    def issue_page_load_token(self, *, reason: str = "") -> int:
        return self._issue_page_load_token(reason=reason)

    def is_page_load_token_current(self, token: int) -> bool:
        return self._is_page_load_token_current(token)

    def cancel_page_loads(self, *, reason: str = "") -> None:
        self._cancel_page_loads(reason=reason)

    def onboarding_target(self, name: str):
        """Виджет, который подсвечивает обучающий тур. None — такой цели нет."""
        _ = name
        return None

    def is_page_ready(self) -> bool:
        return bool(self.isVisible()) and not bool(getattr(self, "_cleanup_in_progress", False))

    def _begin_page_open_metric(self, page_name, *, started_at: float, first_show: bool) -> None:
        self._page_registry_name = page_name
        try:
            self._page_open_metric_started_at = float(started_at)
        except Exception:
            self._page_open_metric_started_at = _time.perf_counter()
        self._page_open_metric_first_show = bool(first_show)

    def _auto_mark_content_ready_after_activation(self) -> bool:
        return True

    def mark_content_ready(
        self,
        *,
        stage: str = "content.ready",
        extra: str = "",
        started_at: float | None = None,
    ) -> None:
        # Метрика измеряет готовность ВИДИМОЙ страницы. Если готовность пришла
        # после ухода со страницы (поздний worker), замер бессмыслен и даёт
        # артефакты вида "content.ready 28067ms".
        try:
            if not self.isVisible():
                return
        except Exception:
            return
        if started_at is None:
            started_at = float(self.__dict__.get("_page_open_metric_started_at", 0.0) or 0.0)
        if started_at <= 0:
            started_at = _time.perf_counter()
        elapsed_ms = (_time.perf_counter() - started_at) * 1000.0
        first_show = bool(self.__dict__.get("_page_open_metric_first_show", True))
        phase = "first" if first_show else "repeat"
        budget_attr = "first_show_budget_ms" if first_show else "repeat_show_budget_ms"
        log_page_timing(
            self._page_label(),
            f"{stage}.{phase}",
            elapsed_ms,
            budget_ms=self._resolve_page_budget(budget_attr),
            extra=extra,
            important=True,
            threshold_ms=0,
        )

    def mark_content_ready_after_next_paint(
        self,
        target,
        *,
        stage: str = "content.painted",
        extra: str = "",
        timeout_ms: int = 1_500,
    ) -> None:
        paint_target = self._resolve_content_paint_target(target)
        if paint_target is None:
            self.mark_content_ready(stage=stage, extra=f"{extra}; paint_target=missing" if extra else "paint_target=missing")
            return

        self._content_paint_metric_next_token = int(
            self.__dict__.get("_content_paint_metric_next_token", 0) or 0
        ) + 1
        token = self._content_paint_metric_next_token
        pending = self.__dict__.setdefault("_content_paint_metric_targets", {})
        pending[id(paint_target)] = {
            "target": paint_target,
            "token": token,
            "stage": str(stage or "content.painted"),
            "extra": str(extra or ""),
            "started_at": float(self.__dict__.get("_page_open_metric_started_at", 0.0) or 0.0),
        }

        try:
            paint_target.installEventFilter(self)
        except Exception:
            pending.pop(id(paint_target), None)
            self.mark_content_ready(stage=stage, extra=f"{extra}; paint_filter=failed" if extra else "paint_filter=failed")
            return
        try:
            paint_target.update()
        except Exception:
            pass

        def _timeout() -> None:
            self._finish_content_paint_metric(paint_target, token, timeout=True)

        try:
            QTimer.singleShot(max(1, int(timeout_ms)), _timeout)
        except Exception:
            pass

    @staticmethod
    def _resolve_content_paint_target(target):
        if target is None:
            return None
        inner_view = getattr(target, "_view", None)
        viewport = getattr(inner_view, "viewport", None)
        if callable(viewport):
            try:
                resolved = viewport()
                if resolved is not None:
                    return resolved
            except Exception:
                pass
        viewport = getattr(target, "viewport", None)
        if callable(viewport):
            try:
                resolved = viewport()
                if resolved is not None:
                    return resolved
            except Exception:
                pass
        return target

    def _finish_content_paint_metric(self, target, token: int, *, timeout: bool = False) -> None:
        pending = self.__dict__.get("_content_paint_metric_targets") or {}
        data = pending.get(id(target))
        if not data or int(data.get("token") or 0) != int(token):
            return
        pending.pop(id(target), None)
        try:
            target.removeEventFilter(self)
        except Exception:
            pass
        extra = str(data.get("extra") or "")
        if timeout:
            extra = f"{extra}; paint=timeout" if extra else "paint=timeout"
        self.mark_content_ready(
            stage=str(data.get("stage") or "content.painted"),
            extra=extra,
            started_at=float(data.get("started_at") or 0.0),
        )

    def eventFilter(self, watched, event):  # noqa: N802
        try:
            event_type = event.type()
        except Exception:
            event_type = None
        if event_type == QEvent.Type.Paint:
            pending = self.__dict__.get("_content_paint_metric_targets") or {}
            data = pending.get(id(watched))
            if data:
                self._finish_content_paint_metric(watched, int(data.get("token") or 0))
        try:
            return bool(super().eventFilter(watched, event))
        except Exception:
            return False

    def run_when_page_ready(self, callback) -> bool:
        if not callable(callback):
            return False
        if bool(getattr(self, "_cleanup_in_progress", False)):
            return False
        if self.is_page_ready():
            self._schedule_lifecycle_action(callback)
            return True
        self._ready_callbacks.append(callback)
        return False

    def _resolve_ui_language(self) -> str:
        try:
            from settings.appearance import peek_warmed_ui_language

            return normalize_language(peek_warmed_ui_language())
        except Exception:
            return normalize_language(None)

    # ------------------------------------------------------------------
    # Shared helpers used by page classes
    # ------------------------------------------------------------------

    def hide_page_header(self) -> None:
        """Прячет заголовок и описание страницы, встроенной вкладкой в другую.

        У внешней страницы свой заголовок и отступы, поэтому свои здесь только
        дублируют его и съедают место.
        """
        self._page_header_hidden = True
        if self.title_label is not None:
            self.title_label.setVisible(False)
        if self.subtitle_label is not None:
            self.subtitle_label.setVisible(False)
        self.vBoxLayout.setContentsMargins(0, 8, 0, 0)

    def _build_layout(self) -> QVBoxLayout:
        """Куда сейчас кладут виджеты: в собираемый блок или прямо на страницу."""
        return self._block_layouts[-1] if self._block_layouts else self.vBoxLayout

    def add_widget(self, widget: QWidget, stretch: int = 0):
        """Добавляет виджет на страницу"""
        self._build_layout().addWidget(widget, stretch)

    def add_spacing(self, height: int = 16):
        """Добавляет вертикальный отступ"""
        from PyQt6.QtWidgets import QSpacerItem
        spacer = QSpacerItem(0, height, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._build_layout().addItem(spacer)

    # ------------------------------------------------------------------
    # Блоки, которые собираются позже (см. ui.block_build)
    # ------------------------------------------------------------------

    def add_lazy_block(
        self,
        name: str,
        build,
        *,
        estimated_height: int,
        provides: tuple[str, ...] = (),
        layout=None,
    ) -> LazyBlock:
        """Отводит на странице место под блок, который соберётся позже.

        ``build()`` вызывается, когда блок понадобился: при показе страницы,
        если он попадает в первый экран; когда до него долистали; иначе —
        в паузе. Внутри ``build`` виджеты кладут как обычно — ``add_widget``,
        ``add_spacing``, ``add_section_title``: они попадают в блок.

        ``estimated_height`` — примерная высота блока: столько места он
        занимает, пока не собран.

        ``provides`` — имена атрибутов страницы, которые создаёт строитель
        (``self._host_edit`` и т.п.). Обращение к такому атрибуту, пока блок
        не собран, достраивает блок на месте — код страницы не обязан знать,
        собран ли блок. Это страховка от ошибок, а не способ работы: блок,
        который так достраивается при открытии страницы, ничего не экономит.
        Поэтому то, что нужно виджетам блока при рождении (подключить
        сигналы, подставить текущие настройки и тексты), делает сам
        строитель в конце сборки.

        ``layout`` — раскладка, в которую встаёт блок, если он лежит не прямо
        на странице, а внутри её вкладки или карточки.
        """
        if name in self._lazy_blocks:
            raise ValueError(f"Блок {name!r} уже есть на странице")
        target = layout if layout is not None else self.vBoxLayout
        holder = target.parentWidget() if layout is not None else self.content
        block = LazyBlock(
            name,
            lambda built_block: self._fill_lazy_block(built_block, build),
            estimated_height=estimated_height,
            spacing=target.spacing(),
            after_built=self._after_lazy_block_built,
            parent=holder if holder is not None else self.content,
        )
        self._lazy_blocks[name] = block
        for attr in provides:
            self._lazy_attr_blocks[str(attr)] = name
        target.addWidget(block)
        return block

    def __getattr__(self, name: str):
        # Сюда Python приходит, только когда обычный поиск атрибута ничего
        # не нашёл. Если этот атрибут создаёт несобранный блок — достраиваем.
        owners = self.__dict__.get("_lazy_attr_blocks")
        if owners:
            block_name = owners.get(name)
            if block_name is not None:
                block = self.__dict__["_lazy_blocks"].get(block_name)
                if block is not None and block.ensure_built():
                    self.__dict__["_forced_blocks"].append((block_name, name))
                    if name in self.__dict__:
                        return self.__dict__[name]
        return super().__getattr__(name)

    def _fill_lazy_block(self, block: LazyBlock, build) -> None:
        self._height_of_block_above = self._block_height_if_above_viewport(block)
        self._block_layouts.append(block.layout())
        try:
            build()
        finally:
            self._block_layouts.pop()

    def _after_lazy_block_built(self, block: LazyBlock) -> None:
        above = self.__dict__.pop("_height_of_block_above", None)
        if above is not None:
            self._keep_view_in_place(block, above)
        if not self._building_first_screen:
            # Блок достроен уже после показа страницы: его видимые виджеты
            # выплывают. Блоки первого экрана выплывают вместе со страницей.
            float_in_group(block)

    def _block_height_if_above_viewport(self, block: LazyBlock) -> int | None:
        """Высота несобранного блока, если он целиком выше видимой области."""
        try:
            if not self.isVisible():
                return None
            scrolled = int(self.verticalScrollBar().value())
            if scrolled <= 0:
                return None
            # Блок может лежать не прямо на странице, а внутри её вкладки.
            bottom = block.mapTo(self.content, QPoint(0, block.height())).y()
            if bottom > scrolled:
                return None
            return int(block.height())
        except Exception:
            return None

    def _keep_view_in_place(self, block: LazyBlock, old_height: int) -> None:
        """Блок выше видимой области изменил высоту: то, на что человек смотрит, не должно уехать."""
        try:
            # Раскладку досчитываем сейчас, а не в следующем обороте цикла
            # событий: иначе один кадр содержимое стояло бы со сдвигом.
            QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
            delta = int(block.height()) - int(old_height)
            if delta:
                bar = self.verticalScrollBar()
                bar.setValue(bar.value() + delta)
        except Exception:
            pass

    def lazy_block(self, name: str) -> LazyBlock | None:
        return self._lazy_blocks.get(name)

    def ensure_block(self, name: str) -> bool:
        """Достраивает блок сейчас. False — такого блока нет."""
        block = self._lazy_blocks.get(name)
        if block is None:
            return False
        block.ensure_built()
        return True

    def ensure_all_blocks(self) -> None:
        """Достраивает страницу целиком: нужна тому, кто обращается к любым её виджетам."""
        for block in tuple(self._lazy_blocks.values()):
            block.ensure_built()

    def is_page_built(self) -> bool:
        """Все блоки страницы собраны (у страницы без блоков — всегда)."""
        return all(block.is_built() for block in self._lazy_blocks.values())

    def build_first_screen_blocks(self, *, budget_ms: float | None = None) -> None:
        """Собирает блоки, которые попадают в первый экран.

        Вызывается при показе страницы — сразу, без таймеров. Фоновая
        подготовка страницы про запас вызывает это заранее, чтобы щелчок по
        ней не платил за первый экран.

        ``budget_ms`` — сколько времени на это осталось. Когда оно вышло,
        остальные блоки первого экрана остаются пустыми местами: после
        первого кадра они сами попросятся в очередь (их место перерисовалось)
        и выплывут. Без него первый экран собирается целиком.
        """
        pending = [block for block in self._lazy_blocks.values() if not block.is_built()]
        if not pending:
            return
        width, limit = self._first_screen_size()
        deadline = None if budget_ms is None else _time.perf_counter() + float(budget_ms) / 1000.0
        self._building_first_screen = True
        try:
            for block in pending:
                if block.is_built():
                    # Достроен попутно: к его виджету обратился другой блок.
                    continue
                if self._estimated_block_top(block, width) >= limit:
                    # Ниже края окна или на скрытой вкладке.
                    continue
                if deadline is not None and _time.perf_counter() >= deadline:
                    break
                block.ensure_built()
        finally:
            self._building_first_screen = False

    def _open_budget_left_ms(self) -> float:
        """Сколько времени на открытие у страницы осталось к моменту показа.

        Счёт идёт от щелчка (``_begin_page_open_metric``): сборка страницы
        уже потратила часть времени. Страница, собранная заранее или
        показанная без щелчка, начинает с полного запаса.
        """
        spent_ms = (_time.perf_counter() - float(self._page_open_metric_started_at)) * 1000.0
        if not 0.0 <= spent_ms < _OPEN_CLICK_WINDOW_MS:
            spent_ms = 0.0
        return float(OPEN_BUDGET_MS) - spent_ms

    def _first_screen_size(self) -> tuple[int, int]:
        """Ширина и высота видимой области, какой она будет на экране.

        В момент показа страница ещё не получила свой размер: стопка страниц
        раздаёт его в следующем обороте цикла событий. Поэтому размер берём у
        того, в ком страница лежит, — она займёт его целиком.
        """
        try:
            host = self.parentWidget()
            if host is not None and host.width() >= 200 and host.height() >= 200:
                return int(host.width()), int(host.height())
            viewport = self.viewport()
            if viewport.width() >= 200 and viewport.height() >= 200:
                return int(viewport.width()), int(viewport.height())
        except Exception:
            pass
        return 900, 700

    def _estimated_block_top(self, block: LazyBlock, page_width: int = 0) -> int:
        """Где примерно начинается блок, пока раскладка страницы ещё не посчитана.

        Блок на скрытой вкладке страницы «начинается» далеко за краем окна:
        в первый экран он не попадает.
        """
        top = self._top_inside_layout(self.vBoxLayout, block, max(0, int(page_width)))
        return _FAR_BELOW if top is None else top

    @classmethod
    def _top_inside_layout(cls, layout, block: LazyBlock, width: int) -> int | None:
        """Отступ блока от верха раскладки. None — блока в ней нет или он скрыт."""
        margins = layout.contentsMargins()
        top = int(margins.top())
        inner_width = max(0, int(width) - margins.left() - margins.right())
        # Высоты соседей складываются только в вертикальной раскладке: в
        # строке и в стопке вкладок сосед стоит сбоку или на том же месте.
        vertical = isinstance(layout, QBoxLayout) and layout.direction() in (
            QBoxLayout.Direction.TopToBottom,
            QBoxLayout.Direction.BottomToTop,
        )
        spacing = max(0, int(layout.spacing())) if vertical else 0
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item is None:
                continue
            widget = item.widget()
            if widget is block:
                return top
            inside = None
            if widget is not None:
                if widget.isHidden() and widget.testAttribute(Qt.WidgetAttribute.WA_WState_ExplicitShowHide):
                    continue
                child_layout = widget.layout()
                if child_layout is not None and widget.isAncestorOf(block):
                    inside = cls._top_inside_layout(child_layout, block, inner_width)
            elif item.layout() is not None:
                inside = cls._top_inside_layout(item.layout(), block, inner_width)
            if inside is not None:
                return top + inside
            if not vertical:
                continue
            height = cls._estimated_item_height(item, inner_width)
            if height is not None:
                # Промежуток раскладка ставит только между виджетами:
                # отступ-распорка занимает свою высоту и ничего сверх неё.
                top += height + (spacing if widget is not None else 0)
        return None

    @classmethod
    def _estimated_item_height(cls, item, width: int = 0) -> int | None:
        """Примерная высота элемента раскладки. None — элемент места не занимает.

        Спрашиваем сам виджет, а не элемент раскладки: виджет, только что
        добавленный на видимую страницу, Qt показывает в следующем обороте
        цикла событий, и до этого раскладка считает его высоту нулевой.
        ``width`` — ширина, которая ему достанется: текст с переносом строк
        без неё отвечает высотой для узкой колонки.
        """
        widget = item.widget()
        if widget is None:
            return max(0, int(item.sizeHint().height()))
        if widget.isHidden() and widget.testAttribute(Qt.WidgetAttribute.WA_WState_ExplicitShowHide):
            return None
        if isinstance(widget, LazyBlock) and widget.is_built():
            inner = widget.layout()
            total = 0
            widgets = 0
            for index in range(inner.count()):
                inner_item = inner.itemAt(index)
                height = cls._estimated_item_height(inner_item, width)
                if height is None:
                    continue
                total += height
                widgets += 1 if inner_item.widget() is not None else 0
            return total + max(0, int(inner.spacing())) * max(0, widgets - 1)
        height = -1
        if width > 0 and widget.hasHeightForWidth():
            height = int(widget.heightForWidth(width))
        if height < 0:
            height = int(widget.sizeHint().height())
        height = max(height, int(widget.minimumHeight()))
        return max(0, min(height, int(widget.maximumHeight())))

    def add_section_title(
        self,
        text: str = "",
        return_widget: bool = False,
        *,
        text_key: str | None = None,
    ):
        """Добавляет заголовок секции"""
        fallback_text = text
        if text_key:
            text = tr_catalog(text_key, language=self._ui_language, default=text or text_key)

        label = StrongBodyLabel(self.content)
        label.setText(text)
        set_state_text(label, f"Раздел страницы: {text}")
        label.setProperty("tone", "primary")
        if text_key:
            self._section_title_bindings.append((label, text_key, fallback_text or text_key))
        self._build_layout().addWidget(label)
        if return_widget:
            return label

    def set_ui_language(self, language: str) -> None:
        self._ui_language = normalize_language(language)
        self._retranslate_base_texts()

    def _apply_page_theme(self, tokens=None, force: bool = False) -> None:
        _ = tokens
        _ = force

    def _create_page_theme_refresh_if_needed(self):
        if type(self)._apply_page_theme is BasePage._apply_page_theme:
            return None
        from ui.theme_refresh import ThemeRefreshBinding

        return ThemeRefreshBinding(
            self,
            self._apply_page_theme,
            is_build_pending=lambda: False,
        )

    def _flush_page_theme_refresh(self) -> None:
        started_at = _time.perf_counter()
        try:
            if self._page_theme_refresh is None:
                return
            self._page_theme_refresh.flush_pending()
        except Exception:
            pass
        finally:
            self._log_show_step_timing("qt_show.theme_flush", started_at)

    def _schedule_page_theme_refresh_flush(self) -> None:
        QTimer.singleShot(0, self._flush_page_theme_refresh)

    def _sync_content_width_to_viewport(self) -> None:
        """Не даёт странице становиться шире видимой области."""

        try:
            width = max(0, int(self.viewport().width()))
            if width > 0 and self.content.maximumWidth() != width:
                self.content.setMaximumWidth(width)
                self.content.updateGeometry()
        except Exception:
            pass

    def _log_show_step_timing(self, stage: str, started_at: float, *, threshold_ms: int = 15) -> None:
        elapsed_ms = (_time.perf_counter() - started_at) * 1000
        if elapsed_ms < int(threshold_ms):
            return
        log_page_timing(self._page_label(), stage, elapsed_ms)

    def showEvent(self, event):  # noqa: N802 (Qt override)
        super().showEvent(event)
        step_started_at = _time.perf_counter()
        self._sync_content_width_to_viewport()
        self._log_show_step_timing("qt_show.sync_width", step_started_at)
        if self._lazy_blocks and not self.is_page_built():
            step_started_at = _time.perf_counter()
            self.build_first_screen_blocks(budget_ms=self._open_budget_left_ms())
            self._log_show_step_timing("qt_show.first_screen_blocks", step_started_at)
            # Остальные блоки достроятся в паузах, пока страница открыта.
            block_build_queue().page_shown()
        step_started_at = _time.perf_counter()
        self._flush_ready_callbacks()
        self._log_show_step_timing("qt_show.ready_callbacks", step_started_at)
        step_started_at = _time.perf_counter()
        self._schedule_activation()
        self._log_show_step_timing("qt_show.schedule_activation", step_started_at)
        step_started_at = _time.perf_counter()
        self._schedule_page_theme_refresh_flush()
        self._log_show_step_timing("qt_show.schedule_theme_flush", step_started_at)
        self.request_keyboard_focus()

    def request_keyboard_focus(self) -> None:
        """Просит страницу поставить фокус на первый удобный для клавиатуры элемент."""

        self._schedule_first_keyboard_focus()

    def _schedule_first_keyboard_focus(self) -> None:
        # Фокус при открытии просят дважды: сама страница при показе и окно
        # после переключения. Поиск обходит все виджеты страницы — хватит
        # одного раза.
        if self.__dict__.get("_keyboard_focus_scheduled"):
            return
        self._keyboard_focus_scheduled = True
        QTimer.singleShot(0, self._focus_first_keyboard_control_if_needed)

    def _focus_first_keyboard_control_if_needed(self) -> None:
        """Ставит фокус на первый управляемый с клавиатуры элемент страницы."""

        self._keyboard_focus_scheduled = False
        if not self.isVisible():
            return
        if self._has_focus_inside_page():
            return
        target = self._first_keyboard_focus_control()
        if target is None:
            return
        try:
            target.setFocus(Qt.FocusReason.OtherFocusReason)
        except Exception:
            pass

    def _has_focus_inside_page(self) -> bool:
        focus_widget = QApplication.focusWidget()
        if focus_widget is None:
            return False
        if focus_widget is self or focus_widget is self.content:
            return True
        try:
            return bool(self.isAncestorOf(focus_widget))
        except Exception:
            return False

    def _first_keyboard_focus_control(self):
        for widget in self._iter_keyboard_focus_candidates():
            if self._is_keyboard_focus_control(widget):
                return widget
        return None

    def _iter_keyboard_focus_candidates(self):
        try:
            return tuple(self.content.findChildren(QWidget))
        except Exception:
            return ()

    @staticmethod
    def _is_keyboard_focus_control(widget) -> bool:
        try:
            if not widget.isEnabled() or not widget.isVisible():
                return False
        except Exception:
            return False
        try:
            object_name = str(widget.objectName() or "")
        except Exception:
            object_name = ""
        if object_name in {"lineEditButton"}:
            return False
        if type(widget).__name__ in {"ArrowButton", "Indicator"}:
            return False
        try:
            policy = widget.focusPolicy()
        except Exception:
            return False
        return policy in (
            Qt.FocusPolicy.TabFocus,
            Qt.FocusPolicy.StrongFocus,
            Qt.FocusPolicy.WheelFocus,
        )

    def resizeEvent(self, event):  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._sync_content_width_to_viewport()

    def hideEvent(self, event):  # noqa: N802 (Qt override)
        self._cancel_page_lifecycle(reason="hidden")
        self.cancel_page_loads(reason="hidden")
        try:
            self.on_page_hidden()
        except Exception:
            pass
        super().hideEvent(event)

    def _run_page_activation(self, token: int) -> None:
        if not self._is_page_lifecycle_token_current(token):
            return
        if not self.isVisible():
            return

        first_show = not bool(self._page_first_activation_done)
        if first_show:
            self._page_first_activation_done = True

        started_at = _time.perf_counter()
        try:
            self.on_page_activated()
        except Exception:
            pass
        finally:
            log_page_timing(
                self._page_label(),
                "open.activation.first" if first_show else "open.activation.repeat",
                (_time.perf_counter() - started_at) * 1000,
                budget_ms=(
                    self._resolve_page_budget("first_show_budget_ms")
                    if first_show
                    else self._resolve_page_budget("repeat_show_budget_ms")
                ),
                important=True,
                threshold_ms=0,
            )
            if self._auto_mark_content_ready_after_activation():
                self.mark_content_ready()

    def _issue_page_lifecycle_token(self, *, reason: str = "") -> int:
        _ = reason
        self._page_lifecycle_generation += 1
        return self._page_lifecycle_generation

    def _schedule_activation(self) -> None:
        token = self._issue_page_lifecycle_token(reason="show:activate")
        self._schedule_lifecycle_action(lambda t=token: self._run_page_activation(t))

    def _cancel_page_lifecycle(self, *, reason: str = "") -> int:
        _ = reason
        self._page_lifecycle_generation += 1
        return self._page_lifecycle_generation

    def _is_page_lifecycle_token_current(self, token: int) -> bool:
        return int(token) == int(self._page_lifecycle_generation)

    def _issue_page_load_token(self, *, reason: str = "") -> int:
        _ = reason
        self._page_load_generation += 1
        return self._page_load_generation

    def _cancel_page_loads(self, *, reason: str = "") -> int:
        _ = reason
        self._page_load_generation += 1
        return self._page_load_generation

    def _is_page_load_token_current(self, token: int) -> bool:
        return int(token) == int(self._page_load_generation)

    @staticmethod
    def _schedule_lifecycle_action(callback) -> None:
        QTimer.singleShot(0, callback)

    def _flush_ready_callbacks(self) -> None:
        if not self.is_page_ready():
            return
        callbacks = list(self._ready_callbacks)
        self._ready_callbacks.clear()
        for callback in callbacks:
            if callable(callback):
                self._schedule_lifecycle_action(callback)

    def cleanup(self) -> None:
        self._cleanup_in_progress = True
        self._ready_callbacks.clear()
        self._cancel_page_lifecycle(reason="cleanup")
        self.cancel_page_loads(reason="cleanup")
        try:
            self._page_theme_refresh.cleanup()
        except Exception:
            pass

    def _retranslate_base_texts(self) -> None:
        if self._title_key and hasattr(self, "title_label") and self.title_label is not None:
            try:
                title_text = tr_catalog(
                    self._title_key,
                    language=self._ui_language,
                    default=self._title_fallback,
                )
                self.title_label.setText(title_text)
                set_state_text(self.title_label, f"Заголовок страницы: {title_text}")
            except Exception:
                pass

        subtitle_text = ""
        if self._subtitle_key:
            subtitle_text = tr_catalog(
                self._subtitle_key,
                language=self._ui_language,
                default=self._subtitle_fallback,
            )

        if hasattr(self, "subtitle_label") and self.subtitle_label is not None:
            try:
                if self._subtitle_key:
                    self.subtitle_label.setText(subtitle_text)
                self.subtitle_label.setVisible(
                    not self._page_header_hidden and bool(self.subtitle_label.text().strip())
                )
                if self.subtitle_label.text().strip():
                    set_state_text(self.subtitle_label, f"Описание страницы: {self.subtitle_label.text()}")
            except Exception:
                pass

        for label, text_key, fallback_text in list(self._section_title_bindings):
            if label is None:
                continue
            try:
                text_setter = getattr(label, "setText", None)
                if callable(text_setter):
                    section_text = tr_catalog(
                        text_key,
                        language=self._ui_language,
                        default=fallback_text,
                    )
                    text_setter(section_text)
                    set_state_text(label, f"Раздел страницы: {section_text}")
            except Exception:
                pass
