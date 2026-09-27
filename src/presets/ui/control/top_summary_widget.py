from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from qfluentwidgets import CaptionLabel, FlowLayout, StrongBodyLabel, SubtitleLabel

from app.ui_texts import tr as tr_catalog
from donater.premium_display import TIER_UNKNOWN, PremiumDisplay
from presets.ui.control.top_summary_plan import build_premium_summary, build_profiles_value
from ui.accessibility import set_control_accessibility, set_state_text
from ui.animation_policy import are_animations_enabled
from ui.widgets.motion_icon import MotionIcon


# Число профилей не перескакивает, а быстро «докручивается» до нового значения.
PROFILE_COUNT_ROLL_MS = 480
# Звезда Free/Premium изредка поблёскивает; Premium ещё и мягко светится золотом.
PREMIUM_TWINKLE_INTERVAL_MS = 9000
PREMIUM_GLOW_COLOR = "#f5c542"


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
        layout.setContentsMargins(0, 0, 0, 0)
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

    def show_value_frame(self, value: str) -> None:
        """Промежуточный кадр анимации: меняет только видимый текст значения."""
        set_text_if_changed(self._value_label, value)

    def bounce_icon(self) -> None:
        self._icon_label.bounce()

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
        # Первое заполнение после запуска — не изменение, прыгать не нужно.
        if previous:
            self.preset_item.bounce_icon()

    def set_profile_count(self, enabled_count: int | None) -> None:
        if self._profile_count == enabled_count:
            return
        previous = self._profile_count
        self._profile_count = enabled_count
        self.retranslate()
        if isinstance(previous, int) and isinstance(enabled_count, int):
            self._roll_profile_count(previous, enabled_count)
            self.profiles_item.bounce_icon()
        else:
            self._profile_roll.stop()

    def _roll_profile_count(self, start: int, end: int) -> None:
        if not are_animations_enabled() or not self.isVisible():
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
            self.premium_item.bounce_icon()

    def _apply_premium_icon_mood(self) -> None:
        icon = self.premium_item._icon_label
        display = self._premium_display
        icon.set_glow(PREMIUM_GLOW_COLOR if display.is_known and display.is_premium else None)
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
