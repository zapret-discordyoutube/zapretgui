from __future__ import annotations

from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtProperty, pyqtSignal, QTimer
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from PyQt6.QtGui import QIcon
import qtawesome as qta

from ui.accessibility import set_control_accessibility, set_state_text
from ui.combo_accessibility import set_combo_items_accessibility
from ui.theme import (
    get_cached_qta_pixmap,
    get_theme_tokens,
    get_themed_qta_icon,
)
from ui.animation_policy import register_managed_animation, start_managed_animation
from ui.theme_refresh import ThemeRefreshBinding
from ui.widgets.aligned_switch import AlignedSwitchButton
from qfluentwidgets import (
    ComboBox,
    SpinBox,
    InfoBadge,
    InfoLevel as _InfoLevel,
    SettingCard as FluentSettingCard,
    RadioButton,
    IndicatorPosition,
)

_HAS_INFO_BADGE = InfoBadge is not None and _InfoLevel is not None


def _should_use_info_badge(info_badge_cls=InfoBadge, info_level_cls=_InfoLevel) -> bool:
    return bool(_HAS_INFO_BADGE and info_badge_cls is not None and info_level_cls is not None)


def _build_theme_refresh_key(tokens) -> tuple[str, str, str]:
    return (str(tokens.theme_name), str(tokens.accent_hex), str(tokens.font_family_qss))


def _set_stylesheet_if_changed(widget, qss: str) -> None:
    if widget is None:
        return
    value = str(qss or "")
    try:
        if str(widget.styleSheet()) == value:
            return
    except Exception:
        pass
    try:
        widget.setStyleSheet(value)
    except Exception:
        pass


def _apply_setting_card_text_styles(title_label, desc_label, tokens=None) -> None:
    theme_tokens = tokens or get_theme_tokens()
    font_family = str(getattr(theme_tokens, "font_family_qss", "") or "'Segoe UI Variable', 'Segoe UI', Arial, sans-serif")
    title_color = str(getattr(theme_tokens, "fg", "") or "rgba(255, 255, 255, 0.92)")
    desc_color = str(getattr(theme_tokens, "fg_muted", "") or "rgba(255, 255, 255, 0.65)")
    _set_stylesheet_if_changed(
        title_label,
        (
            f"color: {title_color}; "
            f"font-family: {font_family}; "
            "font-size: 13px; "
            "font-weight: 600; "
            "background: transparent;"
        ),
    )
    _set_stylesheet_if_changed(
        desc_label,
        (
            f"color: {desc_color}; "
            f"font-family: {font_family}; "
            "font-size: 12px; "
            "background: transparent;"
        ),
    )


class Win11ToggleRow(FluentSettingCard):
    """Строка с toggle switch в стиле Windows 11."""

    toggled = pyqtSignal(bool)

    def __init__(self, icon_name: str, title: str, description: str = "", icon_color: str = "", parent=None):
        self._icon_name = icon_name
        self._icon_color = icon_color
        self._title_label = None
        self._desc_label = None
        self._icon_label = None
        self._switch_button = None
        self._accessible_title = str(title or "")
        self._accessible_description = str(description or "")
        self._programmatic_set_checked = False

        initial_tokens = get_theme_tokens()
        FluentSettingCard.__init__(
            self,
            QIcon(),
            title,
            description or None,
            parent=parent,
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        try:
            self.setIconSize(18, 18)
        except Exception:
            pass
        self._icon_label = getattr(self, "iconLabel", None)
        self._title_label = getattr(self, "titleLabel", None)
        self._desc_label = getattr(self, "contentLabel", None)

        # Подписи «Вкл.»/«Выкл.» и постоянную ширину задаёт сам AlignedSwitchButton.
        self._switch_button = AlignedSwitchButton(self)
        self._apply_text_styles(initial_tokens)

        if self._switch_button is not None:
            try:
                self.hBoxLayout.addWidget(self._switch_button, 0, Qt.AlignmentFlag.AlignRight)
                self.hBoxLayout.addSpacing(16)
            except Exception:
                pass

        if self._switch_button is not None:
            try:
                signal = getattr(self._switch_button, "toggled", None) or getattr(
                    self._switch_button,
                    "checkedChanged",
                    None,
                )
                if signal is not None:
                    signal.connect(self._on_switch_toggled)
            except Exception:
                pass
        self._update_toggle_accessibility()
        self._theme_refresh = ThemeRefreshBinding(
            self,
            self._apply_theme_refresh,
            key_builder=_build_theme_refresh_key,
        )
        self._schedule_initial_icon_refresh(initial_tokens)

    def _resolved_icon_color(self, tokens=None) -> str:
        theme_tokens = tokens or get_theme_tokens()
        c = str(self._icon_color or "").strip()
        if not c:
            return theme_tokens.accent_hex
        return c

    def _refresh_icon(self, tokens=None) -> None:
        theme_tokens = tokens or get_theme_tokens()
        icon_label = self._icon_label
        if icon_label is None:
            return
        try:
            icon_label.setIcon(self._build_icon(theme_tokens))
        except Exception:
            try:
                color = self._resolved_icon_color(theme_tokens)
                icon_label.setPixmap(get_cached_qta_pixmap(self._icon_name, color=color, size=18))
            except Exception:
                return

    def _schedule_initial_icon_refresh(self, tokens=None) -> None:
        def _apply_icon() -> None:
            self._refresh_icon(tokens)

        try:
            QTimer.singleShot(250, _apply_icon)
        except Exception:
            _apply_icon()

    def _build_icon(self, tokens=None) -> QIcon:
        theme_tokens = tokens or get_theme_tokens()
        try:
            return get_themed_qta_icon(self._icon_name, color=self._resolved_icon_color(theme_tokens))
        except Exception:
            return QIcon()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        self._refresh_icon(tokens)
        self._apply_text_styles(tokens)

    def _apply_text_styles(self, tokens=None) -> None:
        _apply_setting_card_text_styles(self._title_label, self._desc_label, tokens)

    def setChecked(self, checked: bool, block_signals: bool = False):
        toggle = getattr(self, "_switch_button", None)
        if toggle is None:
            return
        next_checked = bool(checked)
        try:
            if bool(toggle.isChecked()) == next_checked:
                return
        except Exception:
            pass
        self._programmatic_set_checked = True
        try:
            if block_signals:
                toggle.blockSignals(True)
            try:
                toggle.setChecked(next_checked)
            finally:
                if block_signals:
                    toggle.blockSignals(False)
        finally:
            self._programmatic_set_checked = False
        self._update_toggle_accessibility()

    def isChecked(self) -> bool:
        toggle = getattr(self, "_switch_button", None)
        if toggle is None:
            return False
        return bool(toggle.isChecked())

    def set_texts(self, title: str, description: str = "") -> None:
        next_title = str(title or "")
        next_description = str(description or "")
        text_key = (next_title, next_description)
        try:
            title_label = getattr(self, "_title_label", None)
            desc_label = getattr(self, "_desc_label", None)
            current_title = str(title_label.text() if title_label is not None else "")
            current_description = str(desc_label.text() if desc_label is not None else "")
            if (current_title, current_description) == text_key:
                self._last_win11_toggle_texts_key = text_key
                return
        except Exception:
            if getattr(self, "_last_win11_toggle_texts_key", None) == text_key:
                return
        try:
            self.setTitle(next_title)
            self.setContent(next_description)
            self._accessible_title = next_title
            self._accessible_description = next_description
            self._last_win11_toggle_texts_key = text_key
            self._update_toggle_accessibility()
        except Exception:
            pass

    def _on_switch_toggled(self, checked: bool) -> None:
        self._update_toggle_accessibility()
        if bool(getattr(self, "_programmatic_set_checked", False)):
            return
        self.toggled.emit(bool(checked))

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            toggle = getattr(self, "_switch_button", None)
            if toggle is not None:
                toggle.setChecked(not self.isChecked())
                event.accept()
                return
        super().keyPressEvent(event)

    def _update_toggle_accessibility(self) -> None:
        state = "включено" if self.isChecked() else "выключено"
        title = str(self.__dict__.get("_accessible_title", "") or "").strip()
        description = str(self.__dict__.get("_accessible_description", "") or "")
        name = f"{title}, {state}".strip(", ")
        set_control_accessibility(self, name=name, description=description)
        set_state_text(self, name)
        toggle = self.__dict__.get("_switch_button")
        if toggle is not None:
            set_control_accessibility(toggle, name=name, description=description)
            set_state_text(toggle, name)


class Win11RadioOption(FluentSettingCard):
    """Радио-опция в стиле Windows 11."""

    clicked = pyqtSignal()

    def __init__(
        self,
        title: str,
        description: str,
        icon_name: str | None = None,
        icon_color: str = "",
        recommended: bool = False,
        recommended_badge: str = "рекомендуется",
        parent=None,
    ):
        initial_tokens = get_theme_tokens()
        FluentSettingCard.__init__(
            self,
            self._build_icon(icon_name, icon_color, initial_tokens),
            title,
            description or None,
            parent=parent,
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._selected = False
        self._hover = False
        self._recommended = recommended
        self._accessible_title = str(title or "")
        self._accessible_description = str(description or "")
        self._recommended_badge_text = str(recommended_badge or "") if recommended else ""

        self._icon_name = icon_name
        self._icon_color = icon_color
        self._icon_label = getattr(self, "iconLabel", None)
        self._radio_button = RadioButton(self)
        self._radio_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._badge_label = None
        self._title_label = getattr(self, "titleLabel", None)
        self._desc_label = getattr(self, "contentLabel", None)
        self._applying_theme_styles = False

        try:
            self.setIconSize(24 if icon_name else 0, 24 if icon_name else 0)
            if not icon_name and self._icon_label is not None:
                self._icon_label.hide()
        except Exception:
            pass
        _apply_setting_card_text_styles(self._title_label, self._desc_label, initial_tokens)

        try:
            self.hBoxLayout.insertWidget(0, self._radio_button, 0, Qt.AlignmentFlag.AlignLeft)
            self.hBoxLayout.insertSpacing(1, 12)
            self._radio_button.clicked.connect(self.clicked.emit)
        except Exception:
            pass

        if recommended:
            if _should_use_info_badge():
                self._badge_label = InfoBadge(recommended_badge, level=_InfoLevel.ATTENTION)
            else:
                self._badge_label = QLabel(recommended_badge)
                self._badge_label.setStyleSheet(
                    "QLabel { background: #0078d4; color: #fff; font-size: 10px; font-weight: 600; padding: 2px 6px; border-radius: 3px; }"
                )
            self.hBoxLayout.insertWidget(max(0, self.hBoxLayout.count() - 1), self._badge_label, 0, Qt.AlignmentFlag.AlignRight)

        self._update_style(initial_tokens)
        self._update_accessibility()
        self._theme_refresh = ThemeRefreshBinding(
            self,
            self._apply_theme_refresh,
            key_builder=_build_theme_refresh_key,
        )

    def _resolved_icon_color(self, tokens=None) -> str:
        theme_tokens = tokens or get_theme_tokens()
        c = str(self._icon_color or "").strip()
        if not c:
            return theme_tokens.accent_hex
        return c

    def _build_icon(self, icon_name: str | None = None, icon_color: str = "", tokens=None) -> QIcon:
        if icon_name is None:
            icon_name = self.__dict__.get("_icon_name")
        if not icon_name:
            return QIcon()
        theme_tokens = tokens or get_theme_tokens()
        color = str(icon_color or self.__dict__.get("_icon_color", "") or "").strip()
        if not color:
            color = theme_tokens.accent_hex
        try:
            return get_themed_qta_icon(str(icon_name), color=color)
        except Exception:
            return QIcon()

    def _refresh_icon(self, tokens=None) -> None:
        if self._icon_label is None or not self._icon_name:
            return
        theme_tokens = tokens or get_theme_tokens()
        try:
            self._icon_label.setIcon(self._build_icon(tokens=theme_tokens))
        except Exception:
            try:
                self._icon_label.setPixmap(
                    get_cached_qta_pixmap(
                        self._icon_name,
                        color=self._resolved_icon_color(theme_tokens),
                        size=24,
                    )
                )
            except Exception:
                return

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        self._refresh_icon(tokens)
        self._update_style(tokens)

    def setSelected(self, selected: bool):
        selected = bool(selected)
        if self._selected == selected:
            self._update_accessibility()
            return
        self._selected = selected
        try:
            self._radio_button.setChecked(selected)
        except Exception:
            pass
        self._update_style()
        self._update_accessibility()

    def isSelected(self) -> bool:
        return self._selected

    def set_texts(self, title: str, description: str, recommended_badge: str | None = None) -> None:
        next_title = str(title or "")
        next_description = str(description or "")
        next_badge = None if recommended_badge is None else str(recommended_badge or "")
        text_key = (next_title, next_description, next_badge)
        try:
            title_label = getattr(self, "_title_label", None)
            desc_label = getattr(self, "_desc_label", None)
            badge_label = getattr(self, "_badge_label", None)
            current_title = str(title_label.text() if title_label is not None else "")
            current_description = str(desc_label.text() if desc_label is not None else "")
            current_badge = None
            if next_badge is not None and badge_label is not None and hasattr(badge_label, "text"):
                current_badge = str(badge_label.text())
            if (current_title, current_description, current_badge) == text_key:
                self._last_win11_radio_texts_key = text_key
                return
        except Exception:
            if getattr(self, "_last_win11_radio_texts_key", None) == text_key:
                return
        try:
            if self._title_label is not None:
                self._title_label.setText(next_title)
            if self._desc_label is not None:
                self._desc_label.setText(next_description)
            if next_badge is not None and self._badge_label is not None and hasattr(self._badge_label, "setText"):
                self._badge_label.setText(next_badge)
            self._accessible_title = next_title
            self._accessible_description = next_description
            if next_badge is not None:
                self._recommended_badge_text = next_badge if self._recommended else ""
            self._last_win11_radio_texts_key = text_key
            self._update_accessibility()
        except Exception:
            pass

    def _update_accessibility(self) -> None:
        state = "выбрано" if self._selected else "не выбрано"
        parts = [str(self._accessible_title or "").strip(), state]
        badge = str(getattr(self, "_recommended_badge_text", "") or "").strip()
        if badge:
            parts.append(badge)
        name = ", ".join(part for part in parts if part)
        set_control_accessibility(self, name=name, description=self._accessible_description)
        set_state_text(self, name)

    def _update_style(self, tokens=None):
        if self._applying_theme_styles:
            return

        self._applying_theme_styles = True
        try:
            theme_tokens = tokens or get_theme_tokens()
            _apply_setting_card_text_styles(self._title_label, self._desc_label, theme_tokens)
        finally:
            self._applying_theme_styles = False

        self.update()

    def enterEvent(self, event):
        self._hover = True
        self._update_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._update_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Left, Qt.Key.Key_Down, Qt.Key.Key_Right):
            direction = -1 if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Left) else 1
            if self._activate_neighbor_option(direction):
                event.accept()
                return
        super().keyPressEvent(event)

    def _activate_neighbor_option(self, direction: int) -> bool:
        options = self._radio_options_in_layout()
        if not options or self not in options:
            return False
        current_index = options.index(self)
        next_index = current_index + int(direction)
        if next_index < 0 or next_index >= len(options):
            return False
        option = options[next_index]
        option.setFocus(Qt.FocusReason.TabFocusReason)
        option.clicked.emit()
        return True

    def _radio_options_in_layout(self) -> list["Win11RadioOption"]:
        parent = self.parentWidget()
        while parent is not None:
            layout = parent.layout()
            options = self._radio_options_in_containing_layout(layout)
            if options:
                return options
            parent = parent.parentWidget()
        return []

    def _radio_options_in_containing_layout(self, layout) -> list["Win11RadioOption"]:
        if layout is None:
            return []
        options: list[Win11RadioOption] = []
        contains_self = False
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item is None:
                continue
            widget = item.widget()
            if widget is self:
                contains_self = True
            if isinstance(widget, Win11RadioOption) and widget.isEnabled():
                options.append(widget)
            nested_options = self._radio_options_in_containing_layout(item.layout())
            if nested_options:
                return nested_options
        if not contains_self:
            return []
        return options

class Win11NumberRow(FluentSettingCard):
    """Строка с числовым вводом в стиле Windows 11."""

    valueChanged = pyqtSignal(int)

    def __init__(
        self,
        icon_name: str,
        title: str,
        description: str = "",
        icon_color: str = "",
        min_val: int = 0,
        max_val: int = 999,
        default_val: int = 10,
        suffix: str = "",
        parent=None,
    ):
        self._icon_name = icon_name
        self._icon_color = icon_color
        self._title_label = None
        self._desc_label = None
        self._icon_label = None
        self._applying_theme_styles = False
        self._accessible_title = str(title or "")
        self._accessible_description = str(description or "")
        initial_tokens = get_theme_tokens()

        super().__init__(
            self._build_icon(initial_tokens),
            title,
            description or None,
            parent=parent,
        )
        self.setIconSize(18, 18)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._icon_label = getattr(self, "iconLabel", None)
        self._title_label = getattr(self, "titleLabel", None)
        self._desc_label = getattr(self, "contentLabel", None)
        layout = getattr(self, "hBoxLayout", None)

        self.spinbox = SpinBox()
        self.spinbox.setMinimum(min_val)
        self.spinbox.setMaximum(max_val)
        self.spinbox.setValue(default_val)
        self.spinbox.setSuffix(suffix)
        self.spinbox.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spinbox.setFixedWidth(160)
        self.spinbox.valueChanged.connect(self._on_spinbox_value_changed)
        layout.addWidget(self.spinbox)
        layout.addSpacing(16)
        try:
            self.setFocusProxy(self.spinbox)
        except Exception:
            pass
        self._apply_text_styles(initial_tokens)
        self._update_number_accessibility()
        self._theme_refresh = ThemeRefreshBinding(
            self,
            self._apply_theme_refresh,
            key_builder=_build_theme_refresh_key,
        )

    def _resolved_icon_color(self, tokens=None) -> str:
        theme_tokens = tokens or get_theme_tokens()
        c = str(self._icon_color or "").strip()
        if not c:
            return theme_tokens.accent_hex
        return c

    def _refresh_icon(self, tokens=None) -> None:
        theme_tokens = tokens or get_theme_tokens()
        icon_label = self._icon_label
        if icon_label is None:
            return
        try:
            icon_label.setIcon(self._build_icon(theme_tokens))
        except Exception:
            try:
                icon_label.setPixmap(
                    get_cached_qta_pixmap(
                        self._icon_name,
                        color=self._resolved_icon_color(theme_tokens),
                        size=18,
                    )
                )
            except Exception:
                return

    def _build_icon(self, tokens=None) -> QIcon:
        theme_tokens = tokens or get_theme_tokens()
        try:
            return get_themed_qta_icon(self._icon_name, color=self._resolved_icon_color(theme_tokens))
        except Exception:
            return QIcon()

    def _apply_theme_styles(self, tokens=None) -> None:
        self._apply_text_styles(tokens)

    def _apply_text_styles(self, tokens=None) -> None:
        _apply_setting_card_text_styles(self._title_label, self._desc_label, tokens)

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        self._applying_theme_styles = True
        try:
            self._refresh_icon(tokens)
            self._apply_theme_styles(tokens)
        finally:
            self._applying_theme_styles = False

    def setValue(self, value: int, block_signals: bool = False):
        next_value = int(value)
        try:
            if int(self.spinbox.value()) == next_value:
                return
        except Exception:
            pass
        if block_signals:
            self.spinbox.blockSignals(True)
        self.spinbox.setValue(next_value)
        if block_signals:
            self.spinbox.blockSignals(False)
        self._update_number_accessibility()

    def value(self) -> int:
        return self.spinbox.value()

    def set_texts(self, title: str, description: str = "") -> None:
        next_title = str(title or "")
        next_description = str(description or "")
        text_key = (next_title, next_description)
        try:
            title_label = getattr(self, "_title_label", None)
            desc_label = getattr(self, "_desc_label", None)
            current_title = str(title_label.text() if title_label is not None else "")
            current_description = str(desc_label.text() if desc_label is not None else "")
            if (current_title, current_description) == text_key:
                self._last_win11_number_texts_key = text_key
                return
        except Exception:
            if getattr(self, "_last_win11_number_texts_key", None) == text_key:
                return
        try:
            self.setTitle(next_title)
            self.setContent(next_description)
            self._accessible_title = next_title
            self._accessible_description = next_description
            self._last_win11_number_texts_key = text_key
            self._update_number_accessibility()
        except Exception:
            pass

    def _on_spinbox_value_changed(self, value: int) -> None:
        self._update_number_accessibility()
        self.valueChanged.emit(int(value))

    def keyPressEvent(self, event):  # noqa: N802
        try:
            key = event.key()
        except Exception:
            key = None
        if key == Qt.Key.Key_Up:
            self.spinbox.stepUp()
            event.accept()
            return
        if key == Qt.Key.Key_Down:
            self.spinbox.stepDown()
            event.accept()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.spinbox.setFocus()
            event.accept()
            return
        super().keyPressEvent(event)

    def _update_number_accessibility(self) -> None:
        title = str(self.__dict__.get("_accessible_title", "") or "").strip()
        description = str(self.__dict__.get("_accessible_description", "") or "")
        try:
            value = self.spinbox.value()
        except Exception:
            value = ""
        name = f"{title}, значение: {value}".strip(", ")
        set_control_accessibility(self, name=name, description=description)
        set_state_text(self, name)
        spinbox = self.__dict__.get("spinbox")
        if spinbox is not None:
            set_control_accessibility(spinbox, name=name, description=description)
            set_state_text(spinbox, name)


class Win11ComboRow(FluentSettingCard):
    """Строка с выпадающим списком в стиле Windows 11."""

    currentIndexChanged = pyqtSignal(int)
    currentTextChanged = pyqtSignal(str)

    def __init__(
        self,
        icon_name: str,
        title: str,
        description: str = "",
        icon_color: str = "",
        items: list | None = None,
        parent=None,
    ):
        self._icon_name = icon_name
        self._icon_color = icon_color
        self._title_label = None
        self._desc_label = None
        self._icon_label = None
        self._applying_theme_styles = False
        self._accessible_title = str(title or "")
        self._accessible_description = str(description or "")
        initial_tokens = get_theme_tokens()

        super().__init__(
            self._build_icon(initial_tokens),
            title,
            description or None,
            parent=parent,
        )
        self.setIconSize(18, 18)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._icon_label = getattr(self, "iconLabel", None)
        self._title_label = getattr(self, "titleLabel", None)
        self._desc_label = getattr(self, "contentLabel", None)
        layout = getattr(self, "hBoxLayout", None)

        self.combo = ComboBox()
        self.combo.setFixedWidth(160)

        if items:
            for text, data in items:
                self.combo.addItem(text, userData=data)

        self.combo.currentIndexChanged.connect(self._on_combo_index_changed)
        self.combo.currentTextChanged.connect(self._on_combo_text_changed)
        layout.addWidget(self.combo)
        layout.addSpacing(16)
        try:
            self.setFocusProxy(self.combo)
        except Exception:
            pass
        self._apply_text_styles(initial_tokens)
        self._update_combo_accessibility()
        self._theme_refresh = ThemeRefreshBinding(
            self,
            self._apply_theme_refresh,
            key_builder=_build_theme_refresh_key,
        )

    def _resolved_icon_color(self, tokens=None) -> str:
        theme_tokens = tokens or get_theme_tokens()
        c = str(self._icon_color or "").strip()
        if not c:
            return theme_tokens.accent_hex
        return c

    def _refresh_icon(self, tokens=None) -> None:
        theme_tokens = tokens or get_theme_tokens()
        icon_label = self._icon_label
        if icon_label is None:
            return
        try:
            icon_label.setIcon(self._build_icon(theme_tokens))
        except Exception:
            try:
                icon_label.setPixmap(
                    get_cached_qta_pixmap(
                        self._icon_name,
                        color=self._resolved_icon_color(theme_tokens),
                        size=18,
                    )
                )
            except Exception:
                return

    def _build_icon(self, tokens=None) -> QIcon:
        theme_tokens = tokens or get_theme_tokens()
        try:
            return get_themed_qta_icon(self._icon_name, color=self._resolved_icon_color(theme_tokens))
        except Exception:
            return QIcon()

    def _apply_theme_styles(self, tokens=None) -> None:
        self._apply_text_styles(tokens)

    def _apply_text_styles(self, tokens=None) -> None:
        _apply_setting_card_text_styles(self._title_label, self._desc_label, tokens)

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        self._applying_theme_styles = True
        try:
            self._refresh_icon(tokens)
            self._apply_theme_styles(tokens)
        finally:
            self._applying_theme_styles = False

    def setCurrentData(self, data, block_signals: bool = False):
        index = self.combo.findData(data)
        if index < 0:
            return
        try:
            if self.combo.currentIndex() == index:
                return
        except Exception:
            pass
        if block_signals:
            self.combo.blockSignals(True)
        self.combo.setCurrentIndex(index)
        if block_signals:
            self.combo.blockSignals(False)
        self._update_combo_accessibility()

    def currentData(self):
        return self.combo.currentData()

    def setCurrentIndex(self, index: int, block_signals: bool = False):
        next_index = int(index)
        try:
            if self.combo.currentIndex() == next_index:
                return
        except Exception:
            pass
        if block_signals:
            self.combo.blockSignals(True)
        self.combo.setCurrentIndex(next_index)
        if block_signals:
            self.combo.blockSignals(False)
        self._update_combo_accessibility()

    def currentIndex(self) -> int:
        return self.combo.currentIndex()

    def set_texts(self, title: str, description: str = "") -> None:
        next_title = str(title or "")
        next_description = str(description or "")
        text_key = (next_title, next_description)
        try:
            title_label = getattr(self, "_title_label", None)
            desc_label = getattr(self, "_desc_label", None)
            current_title = str(title_label.text() if title_label is not None else "")
            current_description = str(desc_label.text() if desc_label is not None else "")
            if (current_title, current_description) == text_key:
                self._last_win11_combo_texts_key = text_key
                return
        except Exception:
            if getattr(self, "_last_win11_combo_texts_key", None) == text_key:
                return
        try:
            self.setTitle(next_title)
            self.setContent(next_description)
            self._accessible_title = next_title
            self._accessible_description = next_description
            self._last_win11_combo_texts_key = text_key
            self._update_combo_accessibility()
        except Exception:
            pass

    def refresh_accessibility(self) -> None:
        self._update_combo_accessibility()

    def _on_combo_index_changed(self, index: int) -> None:
        self._update_combo_accessibility()
        self.currentIndexChanged.emit(int(index))

    def _on_combo_text_changed(self, text: str) -> None:
        self._update_combo_accessibility()
        self.currentTextChanged.emit(str(text))

    def keyPressEvent(self, event):  # noqa: N802
        try:
            key = event.key()
        except Exception:
            key = None
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Right):
            if self.combo.count() > 0:
                self.setCurrentIndex(min(self.combo.currentIndex() + 1, self.combo.count() - 1))
                event.accept()
                return
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Left):
            if self.combo.count() > 0:
                self.setCurrentIndex(max(self.combo.currentIndex() - 1, 0))
                event.accept()
                return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.combo.setFocus()
            self.combo.showPopup()
            event.accept()
            return
        super().keyPressEvent(event)

    def _update_combo_accessibility(self) -> None:
        title = str(self.__dict__.get("_accessible_title", "") or "").strip()
        description = str(self.__dict__.get("_accessible_description", "") or "")
        try:
            text = self.combo.currentText()
        except Exception:
            text = ""
        name = f"{title}, выбрано: {text}".strip(", ")
        set_control_accessibility(self, name=name, description=description)
        set_state_text(self, name)
        combo = self.__dict__.get("combo")
        if combo is not None:
            set_control_accessibility(combo, name=name, description=description)
            set_state_text(combo, name)
            set_combo_items_accessibility(combo, name=title)


__all__ = [
    "Win11ToggleRow",
    "Win11RadioOption",
    "Win11NumberRow",
    "Win11ComboRow",
]
