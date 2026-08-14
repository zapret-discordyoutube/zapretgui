"""Диалог перехода к строке (Ctrl+G)."""

from __future__ import annotations

from qfluentwidgets import BodyLabel, SpinBox

from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_dialog import MessageBoxBase
from ui.message_box_accessibility import set_message_box_button_accessibility


class GotoLineDialog(MessageBoxBase):
    """Спрашивает номер строки в пределах документа."""

    def __init__(self, parent=None, *, total_lines: int = 1, current_line: int = 1) -> None:
        super().__init__(parent)
        total = max(1, int(total_lines))
        current = max(1, min(int(current_line), total))

        self.titleLabel = BodyLabel(f"Перейти к строке (1—{total})", self)
        self.lineSpinBox = SpinBox(self)
        self.lineSpinBox.setRange(1, total)
        self.lineSpinBox.setValue(current)
        set_control_accessibility(
            self.lineSpinBox,
            name="Номер строки",
            description=f"Введите номер строки от 1 до {total}.",
        )
        set_state_text(self.lineSpinBox, "Номер строки")

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.lineSpinBox)

        self.yesButton.setText("Перейти")
        self.cancelButton.setText("Отмена")
        set_message_box_button_accessibility(
            self,
            yes_name="Перейти к строке",
            yes_description="Переводит курсор на указанную строку.",
            cancel_name="Отмена перехода",
            cancel_description="Закрывает диалог без перехода.",
        )
        self.widget.setMinimumWidth(320)

    def selected_line(self) -> int:
        return int(self.lineSpinBox.value())


def prompt_goto_line(parent, *, total_lines: int, current_line: int) -> int | None:
    """Показывает диалог и возвращает выбранную строку либо None."""
    dialog = GotoLineDialog(parent, total_lines=total_lines, current_line=current_line)
    if not dialog.exec():
        return None
    return dialog.selected_line()
