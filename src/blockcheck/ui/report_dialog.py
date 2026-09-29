"""Окно с подробным техническим отчётом BlockCheck.

Отчёт открывается по кнопке, а не висит на странице: новичку он не нужен, а
поддержке — нужен целиком (адреса, ответы DNS, время ответа каждого сервера).
"""

from __future__ import annotations

from ui.log_report_dialog import LogReportDialog


class BlockcheckReportDialog(LogReportDialog):
    """Показывает текст отчёта; «Скопировать» кладёт его в буфер обмена."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(
            title="Подробный отчёт BlockCheck",
            text=text,
            empty_text="Проверка ещё не запускалась.",
            description="Технические подробности проверки: адреса, ответы DNS и время ответа серверов.",
            parent=parent,
        )


def show_report_dialog(parent, text: str) -> None:
    BlockcheckReportDialog(text, parent).exec()
