from __future__ import annotations

"""Человеко-читаемое объяснение отчёта целостности.

Одна причина — один текст. Прежняя формулировка «Переустановите программу»
ничего не объясняла и не подсказывала, что делать.
"""

from dataclasses import dataclass

from .models import IntegrityCause, IntegrityReport


_MAX_LISTED_FILES = 3


@dataclass(frozen=True, slots=True)
class IntegrityMessage:
    title: str
    content: str

    def as_line(self) -> str:
        return f"{self.title}: {self.content}" if self.content else self.title


def _file_list(paths: tuple[str, ...]) -> str:
    names = [path.rsplit("/", 1)[-1] for path in paths]
    if len(names) <= _MAX_LISTED_FILES:
        return ", ".join(names)
    head = ", ".join(names[:_MAX_LISTED_FILES])
    return f"{head} и ещё {len(names) - _MAX_LISTED_FILES}"


def describe_report(report: IntegrityReport, *, repair_started: bool = False) -> IntegrityMessage:
    tail = (
        " Программа восстанавливает поставку."
        if repair_started
        else " Восстановите программу через страницу обновлений."
    )

    if report.cause is IntegrityCause.REMOVED_AFTER_INSTALL:
        return IntegrityMessage(
            title="Файлы программы удалены после установки",
            content=(
                f"Не хватает: {_file_list(report.missing)}. "
                "Чаще всего их забирает в карантин антивирус." + tail
            ),
        )

    if report.cause is IntegrityCause.INCOMPLETE_INSTALL:
        return IntegrityMessage(
            title="Установка неполная",
            content=f"Не хватает: {_file_list(report.missing)}." + tail,
        )

    if report.cause is IntegrityCause.CORRUPTED:
        return IntegrityMessage(
            title="Файлы программы повреждены",
            content=f"Не совпадают с поставкой: {_file_list(report.corrupted)}." + tail,
        )

    return IntegrityMessage(title="Проверка целостности", content="")


__all__ = ["IntegrityMessage", "describe_report"]
