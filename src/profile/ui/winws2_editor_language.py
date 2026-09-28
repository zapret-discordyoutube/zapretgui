"""Подключение языка winws2 к текстовому редактору пресета или профиля.

Цепочка такая:

1. ``Winws2EditorLanguageController`` вешает на ``CodeEditor`` поддержку языка
   (``Winws2EditorLanguage``): проверку текста, подсказки, описания,
   быстрые действия. Всё это чистые функции ``profile.winws2_language``.
2. Сведения с диска — есть ли файлы из пресета и какие фейки в реестре —
   собирает фоновый рабочий поток (``Winws2LanguageFactsWorker``) после
   загрузки текста и через секунду после правок. Когда он заканчивает,
   редактор проверяет текст заново уже с этими сведениями.

Проверка ничего не пишет в пресет: исправления применяются только по
выбору пользователя, как обычная правка текста.
"""

from __future__ import annotations

from typing import Callable, Mapping

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from profile.winws2_language import (
    FileFacts,
    LanguageContext,
    collect_winws2_file_facts,
    complete_winws2,
    describe_winws2_at,
    diagnose_winws2_text,
    quick_actions_winws2,
)
from ui.one_shot_worker_runtime import OneShotWorkerRuntime

FILE_FACTS_DELAY_MS = 1000
LANGUAGE_SHORTCUTS_HINT = (
    "Подсказки появляются при наборе, Ctrl+Пробел — показать их вручную. "
    "Ошибки подчёркиваются, F8 — к следующей ошибке, Ctrl+. — быстрые исправления."
)


def load_winws2_fake_values() -> dict[str, str]:
    """Реестр фейков (встроенный + свои): имя → значение для ``--blob=``.

    Читает SQLite и папку своих фейков — вызывать только вне GUI-потока.
    """
    from fakes.public import load_effective_fakes_catalog

    catalog = load_effective_fakes_catalog()
    return {name: str(entry.blob_value()) for name, entry in catalog.entries.items()}


def default_application_root() -> str:
    from config.runtime_layout import APPLICATION_PATHS

    return str(APPLICATION_PATHS.root)


class Winws2EditorLanguage:
    """Поддержка языка winws2 для ``CodeEditor`` (см. ``ui.code_editor.language``)."""

    def __init__(self, *, fragment: bool = False, preset_text: Callable[[], str | None] | None = None) -> None:
        self.fragment = bool(fragment)
        self._preset_text = preset_text
        self.file_facts: FileFacts | None = None
        self.fake_values: Mapping[str, str] = {}

    def context(self) -> LanguageContext:
        preset_text = None
        if self.fragment and self._preset_text is not None:
            try:
                preset_text = self._preset_text()
            except Exception:
                preset_text = None
        return LanguageContext(fake_values=self.fake_values, file_facts=self.file_facts, preset_text=preset_text)

    def diagnose(self, text: str):
        return diagnose_winws2_text(text, fragment=self.fragment, context=self.context())

    def complete(self, text: str, line: int, column: int, *, explicit: bool):
        return complete_winws2(
            text, line, column, fragment=self.fragment, context=self.context(), explicit=bool(explicit)
        )

    def describe(self, text: str, line: int, column: int) -> str:
        return describe_winws2_at(text, line, column, context=self.context())

    def quick_actions(self, text: str, line: int, column: int):
        return quick_actions_winws2(text, line, column, fragment=self.fragment, context=self.context())


class Winws2LanguageFactsWorker(QThread):
    """Фоновая проверка файлов из пресета и чтение реестра фейков."""

    loaded = pyqtSignal(int, object)

    def __init__(
        self,
        request_id: int,
        *,
        text: str,
        root: str,
        fragment: bool,
        load_fake_values: Callable[[], Mapping[str, str]] | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._request_id = int(request_id)
        self._text = str(text or "")
        self._root = str(root or "")
        self._fragment = bool(fragment)
        self._load_fake_values = load_fake_values

    def run(self) -> None:
        facts: FileFacts | None = None
        fake_values: Mapping[str, str] | None = None
        if self._root:
            try:
                facts = collect_winws2_file_facts(self._text, self._root, fragment=self._fragment)
            except Exception:
                facts = None
        if self._load_fake_values is not None:
            try:
                fake_values = dict(self._load_fake_values() or {})
            except Exception:
                fake_values = None
        self.loaded.emit(self._request_id, (facts, fake_values))


class Winws2EditorLanguageController(QObject):
    """Держит поддержку языка редактора и обновляет сведения с диска в фоне."""

    def __init__(
        self,
        editor,
        *,
        current_text: Callable[[], str],
        fragment: bool = False,
        preset_text: Callable[[], str | None] | None = None,
        application_root: Callable[[], str] | None = default_application_root,
        load_fake_values: Callable[[], Mapping[str, str]] | None = load_winws2_fake_values,
        parent=None,
    ) -> None:
        super().__init__(parent if parent is not None else editor)
        self._editor = editor
        # Текст редактора читает страница своим кэширующим методом
        # (границы чтения GUI-текста — tests/test_gui_direct_work_contract.py).
        self._current_text = current_text
        self._application_root = application_root
        self._load_fake_values = load_fake_values
        self._fake_values_loaded = False
        self._scan_pending = False
        self._runtime = OneShotWorkerRuntime()
        self.language = Winws2EditorLanguage(fragment=fragment, preset_text=preset_text)

        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.timeout.connect(self._start_scan)
        editor.contentEdited.connect(self.schedule_scan)
        editor.set_language_support(self.language)
        description = str(editor.accessibleDescription() or "").strip()
        if LANGUAGE_SHORTCUTS_HINT not in description:
            editor.setAccessibleDescription(f"{description} {LANGUAGE_SHORTCUTS_HINT}".strip())
        if str(current_text() or "").strip():
            self.schedule_scan(0)

    def schedule_scan(self, delay_ms: int | None = None) -> None:
        delay = FILE_FACTS_DELAY_MS if delay_ms is None or delay_ms is False else int(delay_ms)
        self._scan_timer.start(max(0, delay))

    def _root(self) -> str:
        if self._application_root is None:
            return ""
        try:
            return str(self._application_root() or "")
        except Exception:
            return ""

    def _start_scan(self) -> None:
        if self._runtime.is_running():
            self._scan_pending = True
            return
        text = str(self._current_text() or "")
        if not text.strip():
            return
        load_fakes = None if self._fake_values_loaded else self._load_fake_values
        self._runtime.start_qthread_worker(
            worker_factory=lambda request_id: Winws2LanguageFactsWorker(
                request_id,
                text=text,
                root=self._root(),
                fragment=self.language.fragment,
                load_fake_values=load_fakes,
                parent=self,
            ),
            on_loaded=self._on_facts_loaded,
            on_finished=self._on_scan_finished,
        )

    def _on_facts_loaded(self, request_id: int, payload) -> None:
        if not self._runtime.is_current(request_id):
            return
        facts, fake_values = payload if isinstance(payload, tuple) else (None, None)
        self.language.file_facts = facts
        if fake_values is not None:
            self.language.fake_values = fake_values
            self._fake_values_loaded = True
        self._editor.refresh_diagnostics()

    def _on_scan_finished(self, *_args) -> None:
        if self._scan_pending:
            self._scan_pending = False
            self.schedule_scan(0)

    def cleanup(self) -> None:
        self._scan_timer.stop()
        self._scan_pending = False
        self._runtime.next_request_id()
        try:
            self._editor.contentEdited.disconnect(self.schedule_scan)
        except (TypeError, RuntimeError):
            pass


__all__ = [
    "LANGUAGE_SHORTCUTS_HINT",
    "Winws2EditorLanguage",
    "Winws2EditorLanguageController",
    "Winws2LanguageFactsWorker",
    "default_application_root",
    "load_winws2_fake_values",
]
