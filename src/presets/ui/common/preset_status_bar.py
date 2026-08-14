from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget
from qfluentwidgets import CaptionLabel

from settings.mode import ZAPRET1_MODE, ZAPRET2_MODE, normalize_launch_method
from ui.accessibility import set_state_text
from ui.fluent_widgets import set_tooltip
from ui.pulsing_dot import PulsingDot
from ui.theme import get_theme_tokens
from ui.widgets.win11_spinner import Win11Spinner


@dataclass(frozen=True, slots=True)
class PresetStatusPlan:
    text: str
    mode: str
    indicator: str


def _winws_name_for_method(launch_method: str | None) -> str:
    method = normalize_launch_method(launch_method, default=ZAPRET2_MODE)
    if method == ZAPRET1_MODE:
        return "winws"
    return "winws2"


def build_preset_status_plan(
    status: str,
    *,
    launch_method: str | None,
    text: str = "",
) -> PresetStatusPlan:
    status_key = str(status or "").strip().lower()
    custom_text = str(text or "").strip()

    if status_key == "loading":
        return PresetStatusPlan("Загрузка пресета...", "busy", "spinner")
    if status_key == "loaded":
        return PresetStatusPlan("Пресет загружен", "success", "pulse")
    if status_key == "selected_stopped":
        return PresetStatusPlan(
            f"Пресет выбран, {_winws_name_for_method(launch_method)} не запущен",
            "stopped",
            "dot",
        )
    if status_key == "selected":
        return PresetStatusPlan("Пресет выбран", "success", "pulse")
    if status_key == "applying":
        return PresetStatusPlan("Применяем пресет...", "busy", "spinner")
    if status_key == "applied":
        return PresetStatusPlan("Пресет применён", "success", "pulse")
    if status_key == "saving":
        return PresetStatusPlan("Сохраняем изменения...", "busy", "spinner")
    if status_key == "saved":
        return PresetStatusPlan(custom_text or "Изменения сохранены", "success", "pulse")
    if status_key == "dirty":
        return PresetStatusPlan("Есть несохранённые изменения", "neutral", "none")
    if status_key == "error":
        return PresetStatusPlan(custom_text or "Ошибка", "error", "none")
    return PresetStatusPlan(custom_text or "Готово", "neutral", "none")


def build_runtime_preset_status_plan(
    *,
    base_status: str,
    launch_method: str | None,
    runtime_launch_method: str | None,
    launch_busy: bool,
    launch_busy_text: str,
    last_status_message: str,
    base_text: str = "",
) -> PresetStatusPlan:
    method = normalize_launch_method(launch_method, default=ZAPRET2_MODE)
    runtime_method = normalize_launch_method(runtime_launch_method, default=method)

    if runtime_method == method and bool(launch_busy):
        busy_text = str(launch_busy_text or "").strip()
        if busy_text:
            return PresetStatusPlan(busy_text, "busy", "spinner")
        return build_preset_status_plan("applying", launch_method=method)

    message = str(last_status_message or "").strip()
    if runtime_method == method:
        if "Пресет успешно применён" in message or "Пресет применён" in message:
            return build_preset_status_plan("applied", launch_method=method)
        if "Ошибка переключения пресета" in message:
            return build_preset_status_plan("error", launch_method=method, text=message)

    return build_preset_status_plan(base_status, launch_method=method, text=base_text)


def _status_theme_key() -> tuple[str, bool]:
    try:
        tokens = get_theme_tokens()
        return str(tokens.accent_hex), bool(tokens.is_light)
    except Exception:
        return "", False


def set_text_if_changed(widget, text: str) -> bool:
    value = str(text or "")
    try:
        if str(widget.text()) == value:
            return False
    except Exception:
        pass
    widget.setText(value)
    return True


def set_style_sheet_if_changed(widget, style: str) -> bool:
    value = str(style or "")
    try:
        if str(widget.styleSheet()) == value:
            return False
    except Exception:
        pass
    widget.setStyleSheet(value)
    return True


def set_pulse_dot_color_if_changed(widget: PulsingDot, color: str) -> bool:
    value = str(color or "")
    if getattr(widget, "_last_preset_status_color", None) == value:
        return False
    setattr(widget, "_last_preset_status_color", value)
    widget.set_color(value)
    return True


def _preset_status_state_text(text: str) -> str:
    value = str(text or "").strip()
    return f"Статус пресета: {value}" if value else "Статус пресета"


class PresetStatusBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_plan: PresetStatusPlan | None = None
        self._last_theme_key: tuple[str, bool] | None = None
        self._last_indicator: str | None = None
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(24)

        self.spinner = Win11Spinner(size=16, parent=self)
        self.spinner.hide()

        self.pulse_dot = PulsingDot(self, size=16)
        self.pulse_dot.hide()

        self.text_label = CaptionLabel("", self)
        self.text_label.setWordWrap(False)
        self.text_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.pulse_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.text_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)

        # Правый индикатор редактора (строка/колонка/выделение) — отдельная
        # надпись, чтобы не конкурировать со статусом сохранения слева.
        self.detail_label = CaptionLabel("", self)
        self.detail_label.setWordWrap(False)
        self.detail_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.detail_label.hide()
        layout.addWidget(self.detail_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.set_plan(build_preset_status_plan("neutral", launch_method=ZAPRET2_MODE))

    def set_detail_text(self, text: str) -> None:
        """Показывает вспомогательный текст справа (позиция курсора в редакторе)."""
        value = str(text or "")
        changed = set_text_if_changed(self.detail_label, value)
        self.detail_label.setVisible(bool(value))
        if changed or value:
            set_state_text(self.detail_label, value or "Позиция курсора")

    def set_plan(self, plan: PresetStatusPlan) -> None:
        indicator = str(plan.indicator or "none").strip().lower()
        mode = str(plan.mode or "neutral").strip().lower()
        normalized_plan = PresetStatusPlan(str(plan.text or ""), mode, indicator)
        theme_key = _status_theme_key()
        if normalized_plan == self._last_plan and theme_key == self._last_theme_key:
            return
        self._last_plan = normalized_plan
        self._last_theme_key = theme_key
        indicator_changed = indicator != self._last_indicator
        if indicator_changed:
            self._last_indicator = indicator
            if indicator == "spinner":
                self.pulse_dot.stop_pulse()
                self.pulse_dot.hide()
                self.spinner.start()
            elif indicator == "pulse":
                self.spinner.stop()
                self.pulse_dot.setVisible(True)
                self.pulse_dot.start_pulse()
            elif indicator == "dot":
                self.spinner.stop()
                self.pulse_dot.stop_pulse()
                self.pulse_dot.setVisible(True)
            else:
                self.spinner.stop()
                self.pulse_dot.stop_pulse()
                self.pulse_dot.hide()

        set_text_if_changed(self.text_label, normalized_plan.text)
        state_text = _preset_status_state_text(normalized_plan.text)
        set_state_text(self, state_text)
        set_state_text(self.text_label, state_text)
        set_state_text(self.spinner, state_text)
        set_state_text(self.pulse_dot, state_text)
        self._apply_mode_style(mode)

    def _apply_mode_style(self, mode: str) -> None:
        try:
            tokens = get_theme_tokens()
            accent = tokens.accent_hex
            is_light = bool(tokens.is_light)
        except Exception:
            accent = "#5caee8"
            is_light = False

        if mode in {"error", "stopped"}:
            color = "#d83b01"
        elif mode in {"success", "busy"}:
            color = accent
        else:
            color = "#5f6368" if is_light else "#b8b8b8"

        set_pulse_dot_color_if_changed(self.pulse_dot, color)
        set_style_sheet_if_changed(self.text_label, f"color: {color};")
        muted = "#5f6368" if is_light else "#b8b8b8"
        set_style_sheet_if_changed(self.detail_label, f"color: {muted};")


class PresetStatusIcon(QWidget):
    def __init__(self, parent=None, *, size: int = 24):
        super().__init__(parent)
        self._last_plan: PresetStatusPlan | None = None
        self._last_theme_key: tuple[str, bool] | None = None
        self._last_indicator: str | None = None
        self._icon_size = max(16, int(size))
        box_size = self._icon_size + 4
        self.setFixedSize(box_size, box_size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.spinner = Win11Spinner(size=self._icon_size, parent=self)
        self.spinner.hide()

        self.pulse_dot = PulsingDot(self, size=self._icon_size)
        self.pulse_dot.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.pulse_dot, 0, Qt.AlignmentFlag.AlignCenter)

        self.set_plan(build_preset_status_plan("neutral", launch_method=ZAPRET2_MODE))

    def set_plan(self, plan: PresetStatusPlan) -> None:
        indicator = str(plan.indicator or "none").strip().lower()
        mode = str(plan.mode or "neutral").strip().lower()
        normalized_plan = PresetStatusPlan(str(plan.text or ""), mode, indicator)
        theme_key = _status_theme_key()
        if normalized_plan == self._last_plan and theme_key == self._last_theme_key:
            return
        self._last_plan = normalized_plan
        self._last_theme_key = theme_key
        indicator_changed = indicator != self._last_indicator
        if indicator_changed:
            self._last_indicator = indicator
            if indicator == "spinner":
                self.pulse_dot.stop_pulse()
                self.pulse_dot.hide()
                self.spinner.start()
            elif indicator == "pulse":
                self.spinner.stop()
                self.pulse_dot.setVisible(True)
                self.pulse_dot.start_pulse()
            elif indicator == "dot":
                self.spinner.stop()
                self.pulse_dot.stop_pulse()
                self.pulse_dot.setVisible(True)
            else:
                self.spinner.stop()
                self.pulse_dot.stop_pulse()
                self.pulse_dot.hide()

        set_tooltip(self, normalized_plan.text)
        state_text = _preset_status_state_text(normalized_plan.text)
        set_state_text(self, state_text)
        set_state_text(self.spinner, state_text)
        set_state_text(self.pulse_dot, state_text)
        if indicator_changed:
            self.setVisible(indicator in {"spinner", "pulse", "dot"})
        self._apply_mode_style(mode)

    def _apply_mode_style(self, mode: str) -> None:
        if mode in {"error", "stopped"}:
            background = "#d83b01"
        elif mode in {"success", "busy"}:
            background = "#8cc63f"
        else:
            try:
                is_light = bool(get_theme_tokens().is_light)
            except Exception:
                is_light = False
            background = "#5f6368" if is_light else "#6f7378"

        set_pulse_dot_color_if_changed(self.pulse_dot, background)


__all__ = [
    "PresetStatusBar",
    "PresetStatusIcon",
    "PresetStatusPlan",
    "build_preset_status_plan",
    "build_runtime_preset_status_plan",
]
