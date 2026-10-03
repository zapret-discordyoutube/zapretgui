from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QRectF, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QVBoxLayout

from qfluentwidgets import CaptionLabel, StrongBodyLabel, SubtitleLabel, isDarkTheme

from app.ui_texts import tr as tr_catalog
from donater.premium_display import TIER_UNKNOWN, PremiumDisplay
from presets.ui.control.top_summary_plan import build_premium_summary, build_profiles_value
from ui.accessibility import set_control_accessibility, set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.widgets.icon_loop import icon_loop_conductor
from ui.widgets.motion_icon import MotionIcon
from ui.widgets.tile_grid import CHEVRON_ROOM, SoftTile, TileGrid


# Число профилей не перескакивает, а быстро «докручивается» до нового значения.
PROFILE_COUNT_ROLL_MS = 480
# Звезда Free/Premium изредка поблёскивает; Premium ещё и мягко светится золотом.
PREMIUM_TWINKLE_INTERVAL_MS = 7000
PREMIUM_GLOW_COLOR = "#f5c542"
# Изменившийся пункт сводки «всплывает»: новое значение проявляется, а под
# пунктом коротко вспыхивает и гаснет подсветка цвета акцента.
CHANGE_POP_MS = 900
CHANGE_HIGHLIGHT_ALPHA = 0.22
# Если изменение случилось на другой странице, его показывают при возврате
# на главную — с небольшой паузой, чтобы переход страницы успел закончиться.
PENDING_CHANGE_DELAY_MS = 220
# Плитки сводки: поля внутри плитки, наименьшая ширина и высота ряда.
TILE_MARGIN_X = 14
TILE_MARGIN_Y = 10
TILE_MIN_WIDTH = 170
TILE_ROW_HEIGHT = 72
SUMMARY_ICON_SIZE = 24
# Ряд значков сервисов в плитке «Профили»: значки выскакивают по очереди.
STRIP_ICON_SIZE = 18
STRIP_STEP = 24
STRIP_MAX_ICONS = 6
STRIP_POP_MS = 520


def set_visible_if_changed(widget, visible: bool) -> bool:
    value = bool(visible)
    try:
        current = not bool(widget.isHidden())
    except Exception:
        try:
            current = bool(widget.isVisible())
        except Exception:
            current = not value
    if current == value:
        return False
    widget.setVisible(value)
    return True


def set_text_if_changed(widget, text: str) -> bool:
    value = str(text or "")
    try:
        if str(widget.text()) == value:
            return False
    except Exception:
        pass
    widget.setText(value)
    return True


class ControlTopSummaryItem(SoftTile):
    """Плитка сводки: значок, подпись и значение. Нажимаемая ведёт в свой раздел."""

    def __init__(
        self,
        *,
        icon_name: str,
        prominent: bool = False,
        clickable: bool = False,
        initial_icon_delay_ms: int = 0,
        parent=None,
    ):
        super().__init__(parent, clickable=clickable)
        self._icon_name = str(icon_name or "preset")
        self._shown_value = ""
        self._strip: tuple[tuple[str, str], ...] = ()
        self._strip_t = 1.0
        self._strip_pop = None
        self._last_texts: tuple[str, str, str] | None = None
        self._last_icon_theme_key: tuple[str, str] | None = None
        self._icon_override = None
        self._icon_label = MotionIcon(self, size=24)
        self._caption_label = CaptionLabel(self)
        self._value_label = SubtitleLabel(self) if prominent else StrongBodyLabel(self)
        self._details_label = CaptionLabel(self)
        self._details_label.setVisible(False)

        layout = QHBoxLayout(self)
        # Справа место под стрелку, которая проявляется при наведении.
        right = TILE_MARGIN_X + (CHEVRON_ROOM if clickable else 0)
        layout.setContentsMargins(TILE_MARGIN_X, TILE_MARGIN_Y, right, TILE_MARGIN_Y)
        layout.setSpacing(12)
        layout.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        text_layout.addWidget(self._caption_label)
        text_layout.addWidget(self._value_label)
        text_layout.addWidget(self._details_label)
        text_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(text_layout, 1)

        self._theme_refresh = None
        delay_ms = max(0, int(initial_icon_delay_ms or 0))
        self._schedule_icon_refresh(delay_ms)

    def _schedule_icon_refresh(self, delay_ms: int) -> None:
        if delay_ms > 0:
            QTimer.singleShot(delay_ms, self._activate_theme_refresh)
        else:
            self._activate_theme_refresh()

    def set_texts(self, *, caption: str, value: str, details: str = "") -> None:
        next_texts = (str(caption or ""), str(value or ""), str(details or ""))
        if self._last_texts == next_texts:
            return
        self._last_texts = next_texts
        caption_text, value_text, details_text = next_texts
        set_text_if_changed(self._caption_label, caption_text)
        set_visible_if_changed(self._caption_label, bool(caption_text.strip()))
        self._shown_value = value_text
        set_visible_if_changed(self._details_label, bool(details_text.strip()))
        self._fit_texts()
        accessible_parts = []
        if caption_text.strip():
            accessible_parts.append(f"{caption_text}: {value_text}")
        elif value_text.strip():
            accessible_parts.append(value_text)
        if details_text.strip():
            accessible_parts.append(details_text)
        if accessible_parts:
            accessible_text = ", ".join(accessible_parts)
            description = (
                "Нажмите Enter или Пробел, чтобы открыть связанный раздел."
                if self.is_clickable()
                else None
            )
            set_control_accessibility(self, name=accessible_text, description=description)
            set_state_text(self, accessible_text)

    def set_icon_override(self, pixmap) -> None:
        """Своя картинка вместо значка темы (золотая звезда Premium); None — вернуть."""
        if pixmap is None:
            if self.__dict__.get("_icon_override") is None:
                return
            self.__dict__["_icon_override"] = None
            self.__dict__["_last_icon_theme_key"] = None
            self._refresh_icon()
            return
        self.__dict__["_icon_override"] = pixmap
        self._icon_label.setPixmap(pixmap)

    def set_icon_strip(self, icons) -> None:
        """Ряд маленьких значков справа: (имя значка, цвет). Новые выскакивают по очереди."""
        strip = tuple((str(name or ""), str(color or "")) for name, color in (icons or ()) if name)
        if strip == self._strip:
            return
        self._strip = strip
        self._strip_t = 1.0
        if strip and are_live_animations_enabled() and self.isVisible():
            pop = self._strip_pop
            if pop is None:
                pop = QVariantAnimation(self)
                pop.setStartValue(0.0)
                pop.setEndValue(1.0)
                pop.setDuration(STRIP_POP_MS)
                pop.valueChanged.connect(self._on_strip_pop_value)
                self._strip_pop = pop
            pop.stop()
            self._strip_t = 0.0
            pop.start()
        self.update()

    def icon_strip(self) -> tuple[tuple[str, str], ...]:
        return self._strip

    def _on_strip_pop_value(self, value) -> None:
        try:
            self._strip_t = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def strip_capacity(self) -> int:
        """Сколько значков помещается между текстом и правым краем плитки."""
        metrics_width = max(
            self._caption_label.fontMetrics().horizontalAdvance(self._caption_label.text()),
            self._value_label.fontMetrics().horizontalAdvance(self._value_label.text()),
        )
        text_right = self._value_label.geometry().left() + metrics_width + 14
        strip_right = self.width() - TILE_MARGIN_X - (CHEVRON_ROOM if self.is_clickable() else 0)
        return max(0, min(STRIP_MAX_ICONS, int((strip_right - text_right) // STRIP_STEP)))

    def _paint_icon_strip(self, painter: QPainter) -> None:
        capacity = self.strip_capacity()
        if not self._strip or capacity <= 0:
            return
        from profile.ui.profile_icon import profile_icon_pixmap

        # Если все значки не помещаются, последнее место занимает подпись «+N».
        hidden = len(self._strip) - capacity
        shown = self._strip[: capacity - 1] if hidden > 0 else self._strip[:capacity]
        hidden = len(self._strip) - len(shown)
        slots = len(shown) + (1 if hidden > 0 else 0)
        strip_right = self.width() - TILE_MARGIN_X - (CHEVRON_ROOM if self.is_clickable() else 0)
        left = strip_right - slots * STRIP_STEP + (STRIP_STEP - STRIP_ICON_SIZE)
        center_y = self.height() / 2
        if hidden > 0:
            painter.setOpacity(min(1.0, self._strip_t * 1.4))
            painter.setFont(self._caption_label.font())
            painter.setPen(QColor(255, 255, 255, 165) if isDarkTheme() else QColor(0, 0, 0, 150))
            painter.drawText(
                QRectF(left + len(shown) * STRIP_STEP - 3, 0, STRIP_STEP + 6, self.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                f"+{hidden}",
            )
            painter.setPen(Qt.PenStyle.NoPen)
        for index, (icon_name, color) in enumerate(shown):
            # Каждый следующий значок выскакивает чуть позже предыдущего.
            local = min(1.0, max(0.0, (self._strip_t * (1.0 + 0.12 * len(shown)) - 0.12 * index)))
            if local <= 0.0:
                continue
            # Выскакивает с небольшим перелётом и садится на место.
            scale = 1.0 + 2.70158 * (local - 1.0) ** 3 + 1.70158 * (local - 1.0) ** 2
            pixmap = profile_icon_pixmap(icon_name, color=color, size=STRIP_ICON_SIZE)
            if pixmap.isNull():
                continue
            side = STRIP_ICON_SIZE * scale
            center_x = left + index * STRIP_STEP + STRIP_ICON_SIZE / 2
            painter.setOpacity(min(1.0, local * 1.6))
            painter.drawPixmap(
                QRectF(center_x - side / 2, center_y - side / 2, side, side), pixmap, QRectF(pixmap.rect())
            )
        painter.setOpacity(1.0)

    def show_value_frame(self, value: str) -> None:
        """Промежуточный кадр анимации: меняет только видимый текст значения."""
        self._shown_value = str(value or "")
        self._fit_texts()

    def _fit_texts(self) -> None:
        """Длинное значение (имя пресета) не вылезает из плитки: обрезается с «…»."""
        value = self._shown_value
        details = self._last_texts[2] if self._last_texts else ""
        tooltip = ""
        if self.isVisible():
            room = self.width() - self._value_label.geometry().left() - self.layout().contentsMargins().right()
            if room > 20:
                fitted = self._value_label.fontMetrics().elidedText(value, Qt.TextElideMode.ElideRight, room)
                if fitted != value:
                    tooltip = self._last_texts[1] if self._last_texts else value
                    value = fitted
                details = self._details_label.fontMetrics().elidedText(details, Qt.TextElideMode.ElideRight, room)
        set_text_if_changed(self._value_label, value)
        set_text_if_changed(self._details_label, details)
        if self.toolTip() != tooltip:
            from ui.fluent_widgets import set_tooltip

            set_tooltip(self, tooltip)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_texts()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._fit_texts()

    def bounce_icon(self) -> None:
        self._icon_label.bounce()

    def play_change(self) -> None:
        """Показывает, что значение пункта сменилось: подсветка, проявление, прыжок."""
        if not are_live_animations_enabled() or not self.isVisible():
            return
        self._icon_label.bounce()
        pop = self.__dict__.get("_change_pop")
        if pop is None:
            # QVariantAnimation, а не QPropertyAnimation: при выключенных
            # анимациях WinUI общий fallback подменяет QPropertyAnimation.start.
            pop = QVariantAnimation(self)
            pop.setStartValue(0.0)
            pop.setEndValue(1.0)
            pop.setDuration(CHANGE_POP_MS)
            pop.valueChanged.connect(self._on_change_pop_value)
            pop.finished.connect(self._on_change_pop_finished)
            self.__dict__["_change_pop"] = pop
        pop.stop()
        effect = self._value_label.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self._value_label)
            self._value_label.setGraphicsEffect(effect)
        effect.setOpacity(0.0)
        self.__dict__["_change_t"] = 0.0
        pop.start()

    def is_change_playing(self) -> bool:
        pop = self.__dict__.get("_change_pop")
        return pop is not None and pop.state() == QVariantAnimation.State.Running

    def _on_change_pop_value(self, value) -> None:
        try:
            t = float(value)
        except (TypeError, ValueError):
            return
        self.__dict__["_change_t"] = t
        effect = self._value_label.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            # Текст проявляется за первую треть, подсветка гаснет за всё время.
            effect.setOpacity(min(1.0, t / 0.35))
        self.update()

    def _on_change_pop_finished(self) -> None:
        self.__dict__["_change_t"] = 0.0
        # Эффект прозрачности убираем: в покое метка рисуется как обычно.
        self._value_label.setGraphicsEffect(None)
        self.update()

    def hideEvent(self, event) -> None:  # noqa: N802
        pop = self.__dict__.get("_change_pop")
        if pop is not None and pop.state() != QVariantAnimation.State.Stopped:
            pop.stop()
            self._on_change_pop_finished()
        strip_pop = self._strip_pop
        if strip_pop is not None:
            strip_pop.stop()
        self._strip_t = 1.0
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._strip:
            strip_painter = QPainter(self)
            strip_painter.setRenderHints(
                QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
            )
            self._paint_icon_strip(strip_painter)
            strip_painter.end()
        t = float(self.__dict__.get("_change_t", 0.0) or 0.0)
        if t <= 0.0:
            return
        from ui.theme import get_theme_tokens

        accent = QColor(str(getattr(get_theme_tokens(), "accent_hex", "") or "#5caee8"))
        target = QRectF(self._value_label.geometry())
        width = min(target.width(), float(self._value_label.fontMetrics().horizontalAdvance(self._value_label.text()) + 2))
        target.setWidth(max(8.0, width))

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        # Мягкая плашка под новым значением: быстро вспыхивает и плавно гаснет.
        fill = QColor(accent)
        fill.setAlphaF(CHANGE_HIGHLIGHT_ALPHA * min(1.0, t / 0.12) * (1.0 - t) ** 1.6)
        painter.setBrush(fill)
        painter.drawRoundedRect(target.adjusted(-5, 0, 5, 0).intersected(QRectF(self.rect())), 6, 6)

        # Акцентная черта пробегает под значением слева направо и тает.
        sweep = 1.0 - (1.0 - min(1.0, t / 0.45)) ** 3
        line = QColor(accent)
        line.setAlphaF(0.9 * (1.0 - max(0.0, (t - 0.45) / 0.55)))
        painter.setBrush(line)
        bar_y = min(float(self.height()) - 2.0, target.bottom() + 1.0)
        painter.drawRoundedRect(QRectF(target.left(), bar_y, target.width() * sweep, 2.0), 1.0, 1.0)
        painter.end()

    def _refresh_icon(self, tokens=None) -> None:
        from ui.theme import get_theme_tokens

        if self.__dict__.get("_icon_override") is not None:
            return
        theme_tokens = tokens or get_theme_tokens()
        accent_hex = str(getattr(theme_tokens, "accent_hex", "") or "") or "#5caee8"
        icon_key = (self._icon_name, accent_hex)
        if self.__dict__.get("_last_icon_theme_key") == icon_key:
            return
        self.__dict__["_last_icon_theme_key"] = icon_key
        # Свой значок из линий (ui.widgets.line_icons), а не шрифтовой: чёткий на любом масштабе.
        self._icon_label.set_line_icon(self._icon_name, color=accent_hex, size=SUMMARY_ICON_SIZE)

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        if force:
            self._last_icon_theme_key = None
        self._refresh_icon(tokens)

    def _activate_theme_refresh(self) -> None:
        if self._theme_refresh is None:
            from ui.theme_refresh import ThemeRefreshBinding

            self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._refresh_icon()


class ControlTopSummaryWidget(TileGrid):
    presetClicked = pyqtSignal()
    profilesClicked = pyqtSignal()
    premiumClicked = pyqtSignal()

    def __init__(self, *, language: str, mode_value: str, initial_icon_delay_ms: int = 0, parent=None):
        super().__init__(parent, min_tile_width=TILE_MIN_WIDTH, spacing=12, row_height=TILE_ROW_HEIGHT)
        self._language = str(language or "ru")
        self._mode_value = str(mode_value or "")
        self._preset_value = ""
        self._profile_count: int | None = None
        self._profiles_visible = True
        # До ответа сервера статус неизвестен — это не Free.
        self._premium_display = PremiumDisplay(tier=TIER_UNKNOWN)

        self.preset_item = ControlTopSummaryItem(
            icon_name="preset",
            prominent=True,
            clickable=True,
            initial_icon_delay_ms=initial_icon_delay_ms,
            parent=self,
        )
        self.profiles_item = ControlTopSummaryItem(
            icon_name="profiles",
            clickable=True,
            initial_icon_delay_ms=initial_icon_delay_ms,
            parent=self,
        )
        self.mode_item = ControlTopSummaryItem(
            icon_name="mode",
            initial_icon_delay_ms=initial_icon_delay_ms,
            parent=self,
        )
        self.premium_item = ControlTopSummaryItem(
            icon_name="star",
            clickable=True,
            initial_icon_delay_ms=initial_icon_delay_ms,
            parent=self,
        )

        self.preset_item.clicked.connect(self.presetClicked.emit)
        self.profiles_item.clicked.connect(self.profilesClicked.emit)
        self.premium_item.clicked.connect(self.premiumClicked.emit)

        # Значки плиток по очереди играют свои короткие жесты; звезда Premium
        # не в очереди — она поблёскивает сама. Очередь общая со страницей.
        conductor = icon_loop_conductor(parent if parent is not None else self)
        for item in (self.preset_item, self.profiles_item, self.mode_item):
            conductor.register(item._icon_label)

        # Плитка пресета шире остальных: в ней самое длинное значение.
        self.add_tile(self.preset_item, weight=1.9)
        self.add_tile(self.profiles_item, weight=1.4)
        self.add_tile(self.mode_item, weight=0.85)
        self.add_tile(self.premium_item, weight=1.05)

        self._profile_roll = QVariantAnimation(self)
        self._profile_roll.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._profile_roll.setDuration(PROFILE_COUNT_ROLL_MS)
        self._profile_roll.valueChanged.connect(self._on_profile_roll_value)
        self._profile_roll.finished.connect(self._on_profile_roll_finished)

        # Изменения, случившиеся, пока главная была скрыта (пресет переключили
        # на другой странице), показываются при возврате на неё.
        self._last_known_profile_count: int | None = None
        self._pending_items: list[ControlTopSummaryItem] = []
        self._pending_roll_from: int | None = None
        self._pending_timer = QTimer(self)
        self._pending_timer.setSingleShot(True)
        self._pending_timer.timeout.connect(self._play_pending_changes)

        self.retranslate()
        self._apply_premium_icon_mood()

    def set_language(self, language: str) -> None:
        next_language = str(language or "ru")
        if self._language == next_language:
            return
        self._language = next_language
        self.retranslate()

    def set_preset(self, value: str) -> None:
        next_value = str(value or "")
        if self._preset_value == next_value:
            return
        previous = self._preset_value
        self._preset_value = next_value
        self.retranslate()
        # Первое заполнение после запуска — не изменение, показывать нечего.
        if previous:
            self._announce_change(self.preset_item)

    def set_profile_count(self, enabled_count: int | None) -> None:
        if self._profile_count == enabled_count:
            return
        self._profile_count = enabled_count
        self.retranslate()
        if not isinstance(enabled_count, int):
            # «Проверяем...» между пересчётами: помним прошлое число и ждём новое.
            self._profile_roll.stop()
            return
        previous = self._last_known_profile_count
        self._last_known_profile_count = enabled_count
        if not isinstance(previous, int) or previous == enabled_count:
            return
        if self._can_play_now():
            self._roll_profile_count(previous, enabled_count)
            self.profiles_item.play_change()
        elif are_live_animations_enabled():
            if self._pending_roll_from is None:
                self._pending_roll_from = previous
            self._remember_pending(self.profiles_item)

    def set_profile_icons(self, icons) -> None:
        """Значки сервисов из включённых профилей — видно, что именно разблокировано."""
        self.profiles_item.set_icon_strip(icons)

    def _can_play_now(self) -> bool:
        if not are_live_animations_enabled() or not self.isVisible():
            return False
        window = self.window()
        return window is None or not window.isMinimized()

    def _announce_change(self, item: "ControlTopSummaryItem") -> None:
        if self._can_play_now():
            item.play_change()
        elif are_live_animations_enabled():
            self._remember_pending(item)

    def _remember_pending(self, item: "ControlTopSummaryItem") -> None:
        if item not in self._pending_items:
            self._pending_items.append(item)

    def has_pending_changes(self) -> bool:
        return bool(self._pending_items)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._pending_items:
            self._pending_timer.start(PENDING_CHANGE_DELAY_MS)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._pending_timer.stop()
        super().hideEvent(event)

    def _play_pending_changes(self) -> None:
        if not self._can_play_now():
            return
        items, self._pending_items = self._pending_items, []
        roll_from, self._pending_roll_from = self._pending_roll_from, None
        for item in items:
            if item is self.profiles_item and isinstance(roll_from, int) and isinstance(self._profile_count, int):
                self.profiles_item.show_value_frame(build_profiles_value(roll_from, language=self._language))
                self._roll_profile_count(roll_from, self._profile_count)
            item.play_change()

    def _roll_profile_count(self, start: int, end: int) -> None:
        if not self._can_play_now():
            self._profile_roll.stop()
            return
        current = self._profile_roll.currentValue()
        if self._profile_roll.state() == QVariantAnimation.State.Running and current is not None:
            start = int(round(float(current)))
        self._profile_roll.stop()
        self._profile_roll.setStartValue(float(start))
        self._profile_roll.setEndValue(float(end))
        self._profile_roll.start()

    def _on_profile_roll_value(self, value) -> None:
        try:
            count = int(round(float(value)))
        except (TypeError, ValueError):
            return
        self.profiles_item.show_value_frame(build_profiles_value(count, language=self._language))

    def _on_profile_roll_finished(self) -> None:
        self.profiles_item.show_value_frame(
            build_profiles_value(self._profile_count, language=self._language)
        )

    def set_profiles_visible(self, visible: bool) -> None:
        value = bool(visible)
        if self._profiles_visible == value:
            return
        self._profiles_visible = value
        set_visible_if_changed(self.profiles_item, value)

    def set_premium(self, display: PremiumDisplay) -> None:
        if self._premium_display == display:
            return
        previous = self._premium_display
        self._premium_display = display
        self.retranslate()
        self._apply_premium_icon_mood()
        if previous.is_known and display.is_known and previous.is_premium != display.is_premium:
            self._announce_change(self.premium_item)

    def _apply_premium_icon_mood(self) -> None:
        icon = self.premium_item._icon_label
        display = self._premium_display
        is_premium = display.is_known and display.is_premium
        if is_premium:
            from ui.widgets.star_glyph import render_star_pixmap

            self.premium_item.set_icon_override(render_star_pixmap(22))
        else:
            self.premium_item.set_icon_override(None)
        icon.set_glow(PREMIUM_GLOW_COLOR if is_premium else None)
        icon.set_idle_twinkle(PREMIUM_TWINKLE_INTERVAL_MS if display.is_known else 0)

    def retranslate(self) -> None:
        language = self._language
        self.preset_item.set_texts(
            caption=tr_catalog("page.control.summary.preset.caption", language=language, default="Текущий пресет"),
            value=self._preset_value
            or tr_catalog("page.winws2_control.preset.not_selected", language=language, default="Не выбран"),
        )
        self.profiles_item.set_texts(
            caption=tr_catalog("page.control.summary.profiles.caption", language=language, default="Профили"),
            value=build_profiles_value(self._profile_count, language=language),
        )
        self.mode_item.set_texts(
            caption=tr_catalog("page.control.summary.mode.caption", language=language, default="Текущий режим"),
            value=self._mode_value,
        )
        premium_title, premium_details = build_premium_summary(self._premium_display, language=language)
        self.premium_item.set_texts(caption="", value=premium_title, details=premium_details)
