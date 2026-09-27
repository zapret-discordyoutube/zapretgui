from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QRectF, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from qfluentwidgets import CaptionLabel, FlowLayout, StrongBodyLabel, SubtitleLabel

from app.ui_texts import tr as tr_catalog
from donater.premium_display import TIER_UNKNOWN, PremiumDisplay
from presets.ui.control.top_summary_plan import build_premium_summary, build_profiles_value
from ui.accessibility import set_control_accessibility, set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.widgets.motion_icon import MotionIcon


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


class ControlTopSummaryItem(QWidget):
    clicked = pyqtSignal()

    def __init__(
        self,
        *,
        icon_name: str,
        prominent: bool = False,
        clickable: bool = False,
        initial_icon_delay_ms: int = 0,
        parent=None,
    ):
        super().__init__(parent)
        self._icon_name = str(icon_name or "fa5s.circle")
        self._clickable = bool(clickable)
        self._last_texts: tuple[str, str, str] | None = None
        self._last_icon_theme_key: tuple[str, str] | None = None
        self._icon_override = None
        self._icon_label = MotionIcon(self, size=24)
        self._caption_label = CaptionLabel(self)
        self._value_label = SubtitleLabel(self) if prominent else StrongBodyLabel(self)
        self._details_label = CaptionLabel(self)
        self._details_label.setWordWrap(True)
        self._details_label.setVisible(False)

        if self._clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout = QHBoxLayout(self)
        # Снизу небольшой запас под акцентную черту при смене значения.
        layout.setContentsMargins(0, 0, 0, 3)
        layout.setSpacing(10)
        layout.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        text_layout.addWidget(self._caption_label)
        text_layout.addWidget(self._value_label)
        text_layout.addWidget(self._details_label)
        layout.addLayout(text_layout, 1)

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
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
        set_text_if_changed(self._value_label, value_text)
        set_text_if_changed(self._details_label, details_text)
        set_visible_if_changed(self._details_label, bool(details_text.strip()))
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
                if self._clickable
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

    def show_value_frame(self, value: str) -> None:
        """Промежуточный кадр анимации: меняет только видимый текст значения."""
        set_text_if_changed(self._value_label, value)

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
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
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

    def mousePressEvent(self, event):  # noqa: N802
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if self._clickable and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _refresh_icon(self, tokens=None) -> None:
        from ui.theme import get_cached_qta_pixmap, get_theme_tokens

        if self.__dict__.get("_icon_override") is not None:
            return
        theme_tokens = tokens or get_theme_tokens()
        accent_hex = str(getattr(theme_tokens, "accent_hex", "") or "")
        icon_key = (self._icon_name, accent_hex)
        if self.__dict__.get("_last_icon_theme_key") == icon_key:
            return
        self.__dict__["_last_icon_theme_key"] = icon_key
        self._icon_label.setPixmap(
            get_cached_qta_pixmap(self._icon_name, color=accent_hex, size=22)
        )

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        if force:
            self._last_icon_theme_key = None
        self._refresh_icon(tokens)

    def _activate_theme_refresh(self) -> None:
        if self._theme_refresh is None:
            from ui.theme_refresh import ThemeRefreshBinding

            self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._refresh_icon()


class ControlTopSummaryWidget(QWidget):
    presetClicked = pyqtSignal()
    profilesClicked = pyqtSignal()
    premiumClicked = pyqtSignal()

    def __init__(self, *, language: str, mode_value: str, initial_icon_delay_ms: int = 0, parent=None):
        super().__init__(parent)
        self._language = str(language or "ru")
        self._mode_value = str(mode_value or "")
        self._preset_value = ""
        self._profile_count: int | None = None
        self._profiles_visible = True
        # До ответа сервера статус неизвестен — это не Free.
        self._premium_display = PremiumDisplay(tier=TIER_UNKNOWN)

        self.preset_item = ControlTopSummaryItem(
            icon_name="fa5s.folder-open",
            prominent=True,
            clickable=True,
            initial_icon_delay_ms=initial_icon_delay_ms,
            parent=self,
        )
        self.profiles_item = ControlTopSummaryItem(
            icon_name="fa5s.list-ul",
            clickable=True,
            initial_icon_delay_ms=initial_icon_delay_ms,
            parent=self,
        )
        self.mode_item = ControlTopSummaryItem(
            icon_name="fa5s.shield-alt",
            initial_icon_delay_ms=initial_icon_delay_ms,
            parent=self,
        )
        self.premium_item = ControlTopSummaryItem(
            icon_name="fa5s.star",
            clickable=True,
            initial_icon_delay_ms=initial_icon_delay_ms,
            parent=self,
        )

        self.preset_item.clicked.connect(self.presetClicked.emit)
        self.profiles_item.clicked.connect(self.profilesClicked.emit)
        self.premium_item.clicked.connect(self.premiumClicked.emit)

        layout = FlowLayout(self, needAni=False, isTight=True)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setHorizontalSpacing(36)
        layout.setVerticalSpacing(14)
        layout.addWidget(self.preset_item)
        layout.addWidget(self.profiles_item)
        layout.addWidget(self.mode_item)
        layout.addWidget(self.premium_item)

        self.preset_item.setMinimumWidth(260)
        for item in (self.profiles_item, self.mode_item, self.premium_item):
            item.setMinimumWidth(120)

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
            caption=tr_catalog("page.control.summary.preset.caption", language=language, default="Текущий preset"),
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
