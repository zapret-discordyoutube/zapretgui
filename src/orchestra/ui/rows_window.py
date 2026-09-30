"""Показ длинных списков оркестратора порциями.

Каждая строка списка — отдельный набор виджетов (поле номера стратегии,
кнопки, подписи для экранного чтеца). Закреплённых UDP-адресов бывают
тысячи, поэтому страница строит только первые ROWS_PAGE_SIZE строк,
подходящих под поиск, а остальные открываются кнопкой «Показать ещё».
Поиск фильтрует данные, а не прячет уже созданные виджеты.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

from PyQt6.QtCore import QTimer
from qfluentwidgets import PushButton

from ui.accessibility import set_control_accessibility

ROWS_PAGE_SIZE = 100
SEARCH_DEBOUNCE_MS = 150

T = TypeVar("T")


def matching_items(items: Iterable[T], search: str, text_of: Callable[[T], str]) -> list[T]:
    needle = str(search or "").lower().strip()
    if not needle:
        return list(items)
    return [item for item in items if needle in str(text_of(item) or "").lower()]


class RowsWindow:
    """Предел показанных строк, кнопка «Показать ещё» и отложенный поиск."""

    def __init__(self, page, *, on_refresh: Callable[[], None], tr: Callable[..., str]) -> None:
        self.limit = ROWS_PAGE_SIZE
        self._on_refresh = on_refresh
        self._tr = tr
        self.button = PushButton(page)
        self.button.hide()
        self.button.clicked.connect(self._show_more)
        self._search_timer = QTimer(page)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(on_refresh)

    def on_search_changed(self, *_args) -> None:
        self.limit = ROWS_PAGE_SIZE
        self._search_timer.start()

    def stop(self) -> None:
        self._search_timer.stop()

    def _show_more(self) -> None:
        self.limit += ROWS_PAGE_SIZE
        self._on_refresh()

    def update_button(self, *, shown: int, total: int) -> None:
        hidden = max(0, int(total) - int(shown))
        if hidden <= 0:
            self.button.hide()
            return
        text = self._tr(
            "page.orchestra.rows.show_more",
            "Показать ещё {count} (скрыто {hidden})",
            count=min(hidden, ROWS_PAGE_SIZE),
            hidden=hidden,
        )
        self.button.setText(text)
        set_control_accessibility(
            self.button,
            name=text,
            description="Показывает следующую часть списка. Сузить список можно поиском.",
        )
        self.button.show()


__all__ = ["ROWS_PAGE_SIZE", "RowsWindow", "matching_items"]
