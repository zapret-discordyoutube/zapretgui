"""Ведёт журнал экранов окна и открывает экраны из него.

Цепочка такая: страница открылась или сменила вложенный экран → журнал
узнаёт об этом через ``note_screen_changed`` → в конце текущего витка
событий спрашивает у открытой страницы, что на ней сейчас показано, и
записывает это. Кнопки «назад» и «вперёд» зовут ``go_back`` / ``go_forward``:
журнал показывает нужную страницу и просит её открыть записанный экран.

Страница участвует тремя вещами (все есть в ``BasePage``):
``navigation_screen()`` — что открыто сейчас,
``restore_navigation_screen(screen)`` — открыть это заново,
сигнал ``navigation_screen_changed`` — вложенный экран сменился.
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer

from app.page_names import PageName
from log.log import log
from ui.block_build import ensure_page_blocks
from ui.navigation.history import HistoryEntry, NavigationHistory, ScreenState


def read_page_screen(page) -> ScreenState:
    reader = getattr(page, "navigation_screen", None)
    if not callable(reader):
        return ScreenState()
    try:
        screen = reader()
    except Exception as exc:
        log(f"[NAV_HISTORY] страница не сообщила свой экран: {exc}", "WARNING")
        return ScreenState()
    return screen if isinstance(screen, ScreenState) else ScreenState()


class WindowNavigationHistory:
    def __init__(self, window, page_host) -> None:
        self._window = window
        self._page_host = page_host
        self.history = NavigationHistory()
        self._restoring = False
        self._commit_pending = False

    # ------------------------------------------------------------------
    # Запись
    # ------------------------------------------------------------------
    def attach_page(self, page) -> None:
        signal = getattr(page, "navigation_screen_changed", None)
        connect = getattr(signal, "connect", None)
        if callable(connect):
            connect(self.note_screen_changed)
        # Esc на странице — тот же шаг «назад», что и кнопка в шапке окна.
        give_back = getattr(page, "set_navigation_back", None)
        if callable(give_back):
            give_back(self.go_back)

    def note_screen_changed(self) -> None:
        """Что-то открылось. Запись откладывается до конца витка событий: страница
        и её вложенный экран часто меняются подряд, а в журнал должен попасть один шаг."""
        if self._restoring or self._commit_pending:
            return
        self._commit_pending = True
        QTimer.singleShot(0, self._commit_if_pending)

    def _commit_if_pending(self) -> None:
        if self._commit_pending:
            self.commit()

    def commit(self) -> None:
        """Записывает экран, который открыт прямо сейчас."""
        self._commit_pending = False
        entry = self._current_entry()
        if entry is not None:
            self.history.visit(entry)

    def _current_entry(self) -> HistoryEntry | None:
        try:
            page = self._page_host.current_page()
            page_name = self._page_host.page_name_of(page)
        except Exception:
            return None
        if page_name is None:
            return None
        return HistoryEntry(page_name, read_page_screen(page))

    # ------------------------------------------------------------------
    # Переходы
    # ------------------------------------------------------------------
    def can_go_back(self) -> bool:
        return self.history.can_go_back()

    def can_go_forward(self) -> bool:
        return self.history.can_go_forward()

    def go_back(self) -> bool:
        return self._travel(-1)

    def go_forward(self) -> bool:
        return self._travel(1)

    def go_to(self, entry: HistoryEntry) -> bool:
        """Открывает экран, выбранный в списке истории."""
        self.commit()
        for index, candidate in enumerate(self.history.entries()):
            if candidate is entry:
                if self._open(index):
                    return True
                self.history.drop(index)
                self.commit()
                return False
        return False

    def _travel(self, step: int) -> bool:
        self.commit()
        while True:
            # Экран, который уже нельзя открыть (страница недоступна в этом режиме,
            # отчёт устарел), выбрасывается, и шаг делается к следующему за ним.
            index = self.history.index + step
            if not 0 <= index < len(self.history.entries()):
                self.commit()
                return False
            if self._open(index):
                return True
            self.history.drop(index)

    def _open(self, index: int) -> bool:
        entry = self.history.entries()[index]
        self._restoring = True
        try:
            if not self._page_host.show_page(entry.page, allow_internal=True):
                return False
            page = self._page_host.get_loaded_page(entry.page)
            # Записанный экран может лежать в блоке, который ещё не собран.
            ensure_page_blocks(page)
            restore = getattr(page, "restore_navigation_screen", None)
            restored = bool(restore(entry.screen)) if callable(restore) else not entry.screen.key
            if not restored or read_page_screen(page).key != entry.screen.key:
                return False
        except Exception as exc:
            log(f"[NAV_HISTORY] не удалось открыть экран {entry.page.name}: {exc}", "WARNING")
            return False
        finally:
            self._restoring = False
            self._commit_pending = False
        self.history.move_to(index)
        return True

    # ------------------------------------------------------------------
    # Подписи для списка истории
    # ------------------------------------------------------------------
    def describe(self, entry: HistoryEntry) -> str:
        label = self._page_label(entry.page)
        title = str(entry.screen.title or "").strip()
        if label and title and title != label:
            return f"{label} — {title}"
        return title or label or entry.page.name

    def _page_label(self, page_name: PageName) -> str:
        from ui.navigation.text_sync import get_nav_label

        label = str(get_nav_label(self._window, page_name) or "")
        if label and label != page_name.name:
            return label
        # У вложенных страниц нет пункта в боковом меню — берём их собственный заголовок.
        title_label = getattr(self._page_host.get_loaded_page(page_name), "title_label", None)
        text = getattr(title_label, "text", None)
        return str(text() or "") if callable(text) else ""


__all__ = ["WindowNavigationHistory", "read_page_screen"]
