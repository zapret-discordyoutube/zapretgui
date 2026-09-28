from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget
from qfluentwidgets import CaptionLabel, TransparentPushButton

from settings.mode import ZAPRET1_MODE, ZAPRET2_MODE, normalize_launch_method
from ui.accessibility import set_control_accessibility, set_state_text
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


_PROBLEM_COLORS = {
    "error": ("#c42b1c", "#ff6b61"),
    "warning": ("#9d5d00", "#f2c14e"),
}


def _plural(count: int, one: str, few: str, many: str) -> str:
    tail = count % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def build_problems_counter_text(errors: int, warnings: int) -> str:
    """«2 ошибки, 1 предупреждение»; пусто, если проблем нет."""
    parts = []
    if errors:
        parts.append(f"{errors} {_plural(errors, 'ошибка', 'ошибки', 'ошибок')}")
    if warnings:
        parts.append(f"{warnings} {_plural(warnings, 'предупреждение', 'предупреждения', 'предупреждений')}")
    return ", ".join(parts)


def _preset_status_state_text(text: str) -> str:
    value = str(text or "").strip()
    return f"Статус пресета: {value}" if value else "Статус пресета"


class PresetStatusBar(QWidget):
    problemsClicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_plan: PresetStatusPlan | None = None
        self._last_theme_key: tuple[str, bool] | None = None
        self._last_indicator: str | None = None
        # Длинное сообщение (например, причина ошибки запуска) переносится на
        # следующие строки, а не обрезается: высота строки статуса растёт.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.setMinimumHeight(24)

        self.spinner = Win11Spinner(size=16, parent=self)
        self.spinner.hide()

        self.pulse_dot = PulsingDot(self, size=16)
        self.pulse_dot.hide()

        self.text_label = CaptionLabel("", self)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.pulse_dot, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.text_label, 3, Qt.AlignmentFlag.AlignVCenter)

        # Проверка текста пресета: что не так в строке курсора и общий счётчик.
        # Клик по счётчику переводит курсор к следующей проблеме (как F8).
        self._problem_severity = ""
        self._line_problem_severity = ""
        self.problem_label = CaptionLabel("", self)
        self.problem_label.setWordWrap(True)
        self.problem_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.problem_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.problem_label.hide()
        layout.addWidget(self.problem_label, 2, Qt.AlignmentFlag.AlignVCenter)

        self.problems_button = TransparentPushButton("", self)
        self.problems_button.setFixedHeight(22)
        self.problems_button.hide()
        self.problems_button.clicked.connect(self.problemsClicked.emit)
        set_control_accessibility(
            self.problems_button,
            name="Проблемы в тексте пресета",
            description="Переходит к следующей ошибке или предупреждению в тексте (клавиша F8).",
        )
        layout.addWidget(self.problems_button, 0, Qt.AlignmentFlag.AlignVCenter)

        # Правый индикатор редактора (строка/колонка/выделение) — отдельная
        # надпись, чтобы не конкурировать со статусом сохранения слева.
        self.detail_label = CaptionLabel("", self)
        self.detail_label.setWordWrap(False)
        self.detail_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.detail_label.hide()
        layout.addWidget(self.detail_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.set_plan(build_preset_status_plan("neutral", launch_method=ZAPRET2_MODE))

    def set_problems(self, *, errors: int, warnings: int, current_message: str = "",
                     current_severity: str = "") -> None:
        """Счётчик проблем проверки текста и сообщение для строки курсора."""
        counter = build_problems_counter_text(int(errors), int(warnings))
        severity = "error" if errors else ("warning" if warnings else "")
        set_text_if_changed(self.problems_button, counter)
        self.problems_button.setVisible(bool(counter))
        set_tooltip(self.problems_button, f"{counter}. Нажмите, чтобы перейти к следующей (F8)." if counter else "")
        set_state_text(self.problems_button, counter or "Проблем нет")

        message = str(current_message or "").strip()
        shown = " ".join(message.split())
        set_text_if_changed(self.problem_label, shown)
        self.problem_label.setVisible(bool(shown))
        set_tooltip(self.problem_label, message)
        set_state_text(self.problem_label, message or "В этой строке проблем нет")

        self._problem_severity = severity
        self._line_problem_severity = str(current_severity or "")
        self._apply_problem_colors()

    def _apply_problem_colors(self) -> None:
        try:
            is_light = bool(get_theme_tokens().is_light)
        except Exception:
            is_light = False
        index = 0 if is_light else 1
        muted = "#5f6368" if is_light else "#b8b8b8"
        counter_color = _PROBLEM_COLORS.get(self._problem_severity, (muted, muted))[index]
        line_color = _PROBLEM_COLORS.get(self._line_problem_severity, (muted, muted))[index]
        set_style_sheet_if_changed(self.problems_button, f"color: {counter_color};")
        set_style_sheet_if_changed(self.problem_label, f"color: {line_color};")

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
        self._apply_problem_colors()


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
