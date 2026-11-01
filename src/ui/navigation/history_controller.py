"""Навигатор окна: ведёт журнал экранов и открывает экраны из него.

Одно место решает, что считать шагом:

* Смена страницы — всегда по сигналу самого стека страниц окна
  (``currentChanged``). Неважно, кто переключил страницу: боковое меню,
  кнопка на другой странице, поиск или библиотечный ``switchTo``.
* Смена экрана внутри страницы — по сигналам страницы (все есть в ``BasePage``):

  - ``navigation_screen_changed`` — человек открыл другой экран: новый шаг;
  - ``navigation_screen_corrected`` — страница сама уточнила экран, на котором
    человек уже стоит (пришли данные, раздел оказался недоступен, подробности
    закрылись, потому что предмета больше нет): текущая запись заменяется,
    «вперёд» не теряется;
  - ``navigation_screen_returned`` — человек вернулся на экран выше внутри
    страницы (Esc в отчёте, шаг строки пути): если этот экран стоит в журнале
    прямо позади, указатель сдвигается назад, и «вперёд» вернёт обратно;
  - ``navigation_screen_unavailable`` — открытого экрана больше нет (профиль
    удалён): запись выбрасывается, шаг продолжается к соседней;
  - ``navigation_screens_renamed(old, new)`` + ``rename_navigation_screen`` —
    открытый предмет сменил ключ, оставшись тем же: записи переписываются.

Запись откладывается до конца витка событий: страница и её вложенный экран
часто меняются подряд, а в журнал должен попасть один шаг.

Что открыто на странице, страница сообщает через ``navigation_screen()``, а
открывает записанное через ``restore_navigation_screen(screen)``. Ключ экрана
строится из устойчивого имени предмета (uid профиля, имя файла пресета, ключ
вкладки), а не из снимка данных: открывая запись, страница находит предмет
заново и отвечает False, если его больше нет.

Ссылки «наверх» (строка пути, «Отмена», закрытие подробностей) идут через
``navigate_up``: если человек пришёл как раз оттуда, это шаг «назад», а не новый
шаг вперёд. Иначе «назад» после такой ссылки возвращал бы в только что
закрытый экран.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QTimer

from app.page_names import PageName
from log.log import log
from ui.block_build import ensure_page_blocks
from ui.navigation.history import HistoryEntry, NavigationHistory, ScreenState


# Виды изменения экрана по возрастанию веса (см. _schedule_commit).
_CORRECTION, _RETURN, _STEP = 0, 1, 2


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
        # Чем считать изменения этого витка: новый шаг важнее возврата, возврат — поправки.
        self._commit_kind = _CORRECTION
        # Экскурсия по программе водит человека по страницам сама: это не его шаги.
        self._suspended = 0
        self._resume_index = -1
        # Куда шёл последний переход по журналу: туда же продолжать, если
        # открытый экран оказался недоступен.
        self._last_step = -1

    # ------------------------------------------------------------------
    # Подключение
    # ------------------------------------------------------------------
    def install(self, stacked_widget) -> None:
        """Стек страниц окна — единственный источник правды о смене страницы."""
        signal = getattr(stacked_widget, "currentChanged", None)
        if signal is not None:
            signal.connect(lambda _index: self.note_screen_changed())
        # Стартовая страница — первый экран журнала.
        self.note_screen_changed()

    def attach_page(self, page) -> None:
        for name, slot in (
            ("navigation_screen_changed", self.note_screen_changed),
            ("navigation_screen_corrected", self.note_screen_corrected),
            ("navigation_screen_returned", self.note_screen_returned),
            ("navigation_screen_unavailable", lambda page=page: self.screen_unavailable(page)),
            ("navigation_screens_renamed", lambda old, new, page=page: self.rename_screens(page, old, new)),
        ):
            signal = getattr(page, name, None)
            connect = getattr(signal, "connect", None)
            if callable(connect):
                connect(slot)
        # Esc на странице — тот же шаг «назад», что и кнопка в шапке окна;
        # закрытие вложенного экрана — шаг «наверх».
        give_back = getattr(page, "set_navigation_back", None)
        if callable(give_back):
            give_back(self.go_back)
        give_up = getattr(page, "set_navigation_up", None)
        if callable(give_up):
            give_up(lambda key, open_screen, page=page: self.navigate_up_within(page, key, open_screen))

    def suspend(self) -> None:
        """Пока идёт экскурсия, переходы не пишутся в журнал и кнопки истории молчат."""
        if not self._suspended:
            # Экран, с которого экскурсия началась: после неё человек вернётся сюда.
            self.commit()
            self._resume_index = self.history.index
        self._suspended += 1

    def resume(self) -> None:
        """Экскурсия закончилась: человек возвращается на экран, с которого её начал.
        Журнал при этом не меняется — экскурсия в нём следов не оставляет."""
        if not self._suspended:
            return
        self._suspended -= 1
        if self._suspended:
            return
        index, self._resume_index = self._resume_index, -1
        entry = self.history.current()
        if entry is None or index != self.history.index:
            self.note_screen_changed()
            return
        if self._current_entry() != entry and not self._open(index):
            # Тот экран больше не открыть: остаёмся, где экскурсия закончилась.
            self.note_screen_changed()

    # ------------------------------------------------------------------
    # Запись
    # ------------------------------------------------------------------
    def note_screen_changed(self) -> None:
        """Человек открыл другой экран: новый шаг."""
        self._schedule_commit(_STEP)

    def note_screen_returned(self) -> None:
        """Человек вернулся на экран выше внутри страницы."""
        self._schedule_commit(_RETURN)

    def note_screen_corrected(self) -> None:
        """Страница уточнила экран, на котором человек уже стоит: та же запись."""
        self._schedule_commit(_CORRECTION)

    def _schedule_commit(self, kind: int) -> None:
        if self._restoring or self._suspended:
            return
        self._commit_kind = max(self._commit_kind, kind)
        if self._commit_pending:
            return
        self._commit_pending = True
        QTimer.singleShot(0, self._commit_if_pending)

    def _commit_if_pending(self) -> None:
        if self._commit_pending:
            self.commit()

    def commit(self) -> None:
        """Записывает экран, который открыт прямо сейчас."""
        kind = self._commit_kind
        self._commit_pending = False
        self._commit_kind = _CORRECTION
        if self._suspended:
            return
        entry = self._current_entry()
        if entry is None:
            return
        history = self.history
        current = history.current()
        if kind == _RETURN:
            previous_index = history.index - 1
            entries = history.entries()
            if 0 <= previous_index < len(entries) and entries[previous_index].same_screen(entry):
                history.move_to(previous_index)
                history.replace_current(entry)
                return
        if kind == _CORRECTION and current is not None and current.page == entry.page:
            history.replace_current(entry)
        else:
            history.visit(entry)
            if not self._restoring:
                # Новый экран открыт самим человеком, а не шагом по журналу: прежний
                # шаг «вперёд» больше не говорит, в какую сторону он шёл.
                self._last_step = -1

    def _current_entry(self) -> HistoryEntry | None:
        try:
            page = self._page_host.current_page()
            page_name = self._page_host.page_name_of(page)
        except Exception:
            return None
        if page_name is None:
            return None
        return HistoryEntry(page_name, read_page_screen(page))

    def rename_screens(self, page, old: str, new: str) -> None:
        """Предмет на странице сменил ключ: записи этой страницы переходят на новый."""
        rename = getattr(page, "rename_navigation_screen", None)
        if not callable(rename):
            return
        page_name = self._page_name(page)
        if page_name is None:
            return

        def transform(entry: HistoryEntry) -> HistoryEntry:
            if entry.page != page_name:
                return entry
            screen = rename(entry.screen, old, new)
            return entry if screen == entry.screen else HistoryEntry(entry.page, screen)

        self.history.rewrite(transform)

    def prune_unavailable(self) -> None:
        """Режим сменился: страницы, которых в нём нет, уходят из журнала сразу,
        чтобы кнопка «назад» не горела там, где шагать некуда."""
        self.history.prune(lambda entry: self._page_host.is_page_allowed(entry.page))

    def _page_name(self, page) -> PageName | None:
        try:
            return self._page_host.page_name_of(page)
        except Exception:
            return None

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

    def go_to_index(self, index: int) -> bool:
        """Открывает экран, выбранный в списке истории."""
        if self._suspended:
            return False
        self.commit()
        if not 0 <= index < len(self.history.entries()) or index == self.history.index:
            return False
        self._last_step = 1 if index > self.history.index else -1
        if self._open(index):
            return True
        self.history.drop(index)
        self.commit()
        return False

    def navigate_up(self, page_name: PageName, open_page: Callable[[], object]) -> bool:
        """Ссылка «наверх» на другую страницу. Если человек пришёл как раз с неё,
        это шаг «назад» к ней — через все экраны покидаемой страницы (профиль →
        его текст → «Настройка пресета»): журнал не растёт, и «назад» не вернёт в
        закрытый экран, а «вперёд» — вернёт."""
        index = self._index_before_current_page()
        entries = self.history.entries()
        if index is not None and entries[index].page == page_name and self.go_to_index(index):
            return True
        return bool(open_page())

    def _index_before_current_page(self) -> int | None:
        """Запись, с которой человек пришёл на текущую страницу (её экраны пропускаются)."""
        if self._suspended:
            return None
        self.commit()
        entries = self.history.entries()
        index = self.history.index
        if not 0 <= index < len(entries):
            return None
        current_page = entries[index].page
        index -= 1
        while index >= 0 and entries[index].page == current_page:
            index -= 1
        return index if index >= 0 else None

    def navigate_up_within(self, page, key: str | None, open_screen: Callable[[], object]) -> bool:
        """Закрытие вложенного экрана страницы (подробности, отчёт) — к экрану ``key``;
        ``None`` — к любому экрану этой страницы, с которого человек пришёл."""
        page_name = self._page_name(page)
        if page_name is not None and self._previous_is(
            lambda entry: entry.page == page_name and (key is None or entry.screen.key == key)
        ) and self.go_back():
            return True
        open_screen()
        return True

    def _previous_is(self, match: Callable[[HistoryEntry], bool]) -> bool:
        if self._suspended:
            return False
        self.commit()
        index = self.history.index - 1
        entries = self.history.entries()
        return 0 <= index < len(entries) and match(entries[index])

    def screen_unavailable(self, page) -> None:
        """Страница не смогла показать свой экран (предмета больше нет). Запись
        выбрасывается, и человек попадает на соседний экран в ту же сторону, куда шёл."""
        if self._suspended or self._page_name(page) is None:
            return
        if not self._restoring:
            # Экран мог ещё не попасть в журнал (ответ пришёл в том же витке).
            self.commit()
        current = self.history.current()
        if current is None or current.page != self._page_name(page):
            return
        index = self.history.index
        step = self._last_step
        self.history.drop(index)
        # После удаления позади текущей записи стоит index - 1, впереди — index.
        target = index if step > 0 else index - 1
        while 0 <= target < len(self.history.entries()):
            if self._open(target):
                return
            self.history.drop(target)
            if step < 0:
                target -= 1

    def _travel(self, step: int) -> bool:
        if self._suspended:
            return False
        self.commit()
        self._last_step = step
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
            self._commit_kind = _CORRECTION
        self.history.move_to(index)
        return True

    # ------------------------------------------------------------------
    # Подписи для списка истории
    # ------------------------------------------------------------------
    def describe(self, entry: HistoryEntry) -> str:
        label = self._page_label(entry.page)
        title = self._screen_title(entry) or str(entry.screen.title or "").strip()
        if label and title and title != label:
            return f"{label} — {title}"
        return title or label or entry.page.name

    def _screen_title(self, entry: HistoryEntry) -> str:
        """Подпись экрана на текущем языке: её даёт страница (вкладки); пусто — записанная."""
        page = self._page_host.get_loaded_page(entry.page)
        titled = getattr(page, "navigation_screen_title", None)
        if not callable(titled):
            return ""
        try:
            return str(titled(entry.screen) or "").strip()
        except Exception:
            return ""

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
