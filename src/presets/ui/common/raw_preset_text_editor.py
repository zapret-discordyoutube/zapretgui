from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QEvent, QObject, QTimer

from ui.accessibility import set_control_accessibility, set_state_text
from ui.code_editor.editor import CodeEditor, build_cursor_status_text
from ui.code_editor.find_bar import FindReplaceBar
from ui.code_editor.find_controller import FindController
from ui.code_editor.syntax import PresetSyntaxHighlighter
from ui.fluent_widgets import set_tooltip


class RawPresetTextEditor(QObject):
    """Текстовый редактор preset-а: поиск, кэш текста и автосохранение."""

    def __init__(
        self,
        parent,
        *,
        request_save: Callable[..., bool],
        set_footer: Callable[[str], None],
        cleanup_in_progress: Callable[[], bool],
        set_cursor_status: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(None)
        self._request_save = request_save
        self._set_footer = set_footer
        self._cleanup_in_progress = cleanup_in_progress
        self._set_cursor_status = set_cursor_status
        self.text_snapshot: str | None = None
        self.content_loaded_once = False
        self.content_dirty = True
        self.content_publish_pending = False
        self.cache_update_suspended = False
        self.show_scheduled = False

        self.find_bar = FindReplaceBar(parent)
        self.search_input = self.find_bar.search_input
        set_tooltip(self.search_input, "Найти строку в тексте открытого пресета (Ctrl+F).")
        search_input_name = "Поиск по тексту пресета"
        set_control_accessibility(
            self.search_input,
            name=search_input_name,
            description=(
                "Введите текст, чтобы найти строку внутри открытого пресета. "
                "Enter ищет дальше, Shift+Enter ищет назад, F3 повторяет поиск. "
                "После ввода перейдите к тексту пресета клавишей Tab или нажмите Стрелка вниз."
            ),
        )
        set_state_text(self.search_input, search_input_name)

        self.editor = CodeEditor(
            parent,
            highlighter_factory=lambda document: PresetSyntaxHighlighter(document),
        )
        editor_name = "Текст открытого пресета"
        set_control_accessibility(
            self.editor,
            name=editor_name,
            description=(
                "Здесь можно читать и редактировать содержимое открытого пресета. "
                "Ctrl+F — поиск, Ctrl+H — замена, Ctrl+G — переход к строке, "
                "Ctrl+D — дублировать строку, Ctrl+/ — комментарий, "
                "Alt со стрелками — перенос строки, Ctrl с колесом мыши — масштаб."
            ),
        )
        set_state_text(self.editor, editor_name)
        # contentEdited, а не textChanged: перекраска синтаксиса при смене темы
        # тоже эмитит textChanged, и пресет ложно становился «изменённым».
        self.editor.contentEdited.connect(self.on_text_changed)
        self.editor.cursorStatusChanged.connect(self.on_cursor_status_changed)
        self.editor.installEventFilter(self)
        try:
            self.editor.viewport().installEventFilter(self)
        except Exception:
            pass

        self.find_controller = FindController(self.editor, self.find_bar, parent=self)

        self.save_timer = QTimer(parent)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(lambda: self.request_save(publish_content_changed=False))
        self.commit_timer = QTimer(parent)
        self.commit_timer.setSingleShot(True)
        self.commit_timer.timeout.connect(self.commit_pending_content_change)
        self.setParent(parent)

    def install_application_event_filter(self, app) -> bool:
        if app is None:
            return False
        app.installEventFilter(self)
        return True

    def remove_application_event_filter(self, app) -> None:
        if app is None:
            return
        try:
            app.removeEventFilter(self)
        except Exception:
            pass

    def request_save(self, *, publish_content_changed: bool = False) -> bool:
        return bool(self._request_save(publish_content_changed=bool(publish_content_changed)))

    def reset_for_new_file(self) -> None:
        self.text_snapshot = None
        self.content_dirty = True
        self.content_publish_pending = False

    def apply_loaded_text(self, text: str) -> bool:
        value = str(text or "")
        if self.current_text() == value:
            return False
        self.cache_update_suspended = True
        try:
            self.editor.setPlainText(value)
        finally:
            self.cache_update_suspended = False
        self.text_snapshot = value
        return True

    def apply_external_text_update(self, text: str) -> bool:
        if self.has_local_unpublished_changes():
            self.report_external_update_skipped()
            return False
        self.apply_loaded_text(text)
        self.content_loaded_once = True
        self.content_dirty = False
        return True

    def report_external_update_skipped(self) -> None:
        self.content_dirty = True
        self._set_footer("Файл изменился снаружи. Ваши правки не перезаписаны.")

    def has_local_unpublished_changes(self) -> bool:
        if self.content_publish_pending:
            return True
        try:
            if self.save_timer.isActive() or self.commit_timer.isActive():
                return True
        except Exception:
            pass
        return False

    def resolve_save_text(self, source_text) -> str:
        if source_text is not None:
            return str(source_text or "")
        return self.current_text()

    def current_text(self) -> str:
        """Текущий текст редактора пресета.

        Документ QPlainTextEdit — единственный источник правды; кэш — только
        мемоизация с инвалидацией по textChanged. Инкрементального патчинга по
        contentsChange здесь быть не должно: Qt учитывает финальный разделитель
        блока в charsAdded/charsRemoved, и позиционная математика молча теряла
        вставленный из буфера текст."""
        cached = self.text_snapshot
        if cached is not None:
            return str(cached or "")
        try:
            text = str(self.editor.toPlainText() or "")
        except Exception:
            return ""
        self.text_snapshot = text
        return text

    def search_text(self, text: str) -> bool:
        """Поиск по вводу в поле: переход к первому совпадению от начала."""
        query = str(text or "")
        if query != str(self.search_input.text() or ""):
            self.search_input.setText(query)
            return bool(self.find_controller.result.count)
        return bool(self.find_controller.search_text(query))

    def find_next(self, *, reverse: bool = False) -> bool:
        return bool(self.find_controller.find_next(reverse=bool(reverse)))

    def on_cursor_status_changed(self, status) -> None:
        callback = self._set_cursor_status
        if callback is None:
            return
        try:
            callback(build_cursor_status_text(status))
        except Exception:
            pass

    def on_text_changed(self) -> None:
        # Правка инвалидирует мемо: текст перечитывается из документа целиком
        # при следующем обращении (current_text).
        self.text_snapshot = None
        if self.cache_update_suspended:
            return
        if self._cleanup_in_progress():
            return
        self.content_dirty = True
        self.content_publish_pending = True
        self.save_timer.stop()
        self.commit_timer.stop()
        self.save_timer.start(900)
        self._set_footer("Изменения...")

    def commit_pending_content_change(self) -> None:
        if self._cleanup_in_progress() or not self.content_publish_pending:
            return
        if self.save_timer.isActive():
            self.save_timer.stop()
        self.request_save(publish_content_changed=True)

    def schedule_pending_content_commit(self) -> None:
        if self._cleanup_in_progress() or not self.content_publish_pending:
            return
        self.commit_timer.start(0)

    def hide_for_next_switch(self) -> None:
        try:
            self.editor.setVisible(False)
        except Exception:
            pass
        self.show_scheduled = False

    def schedule_show_after_page_switch(self) -> None:
        if self._cleanup_in_progress() or self.show_scheduled:
            return
        self.show_scheduled = True
        try:
            QTimer.singleShot(0, self.show_after_page_switch)
        except Exception:
            self.show_after_page_switch()

    def show_after_page_switch(self) -> None:
        self.show_scheduled = False
        if self._cleanup_in_progress():
            return
        try:
            self.editor.setVisible(True)
        except Exception:
            pass

    def is_editor_object(self, obj) -> bool:
        if obj is None:
            return False
        current = obj
        while current is not None:
            if current is self.editor:
                return True
            try:
                current = current.parent()
            except Exception:
                return False
        return False

    def eventFilter(self, obj, event):  # noqa: N802
        return self.handle_event(obj, event)

    def handle_event(self, obj, event) -> bool:
        # Клавиши поля поиска (Enter/Shift+Enter/Стрелка вниз/F3/Esc) обрабатывает
        # сама панель FindReplaceBar — здесь остаётся только коммит правок.
        event_type = event.type()
        if event_type in {QEvent.Type.FocusOut, QEvent.Type.Leave} and self.is_editor_object(obj):
            self.schedule_pending_content_commit()
        elif (
            event_type == QEvent.Type.MouseButtonPress
            and self.content_publish_pending
            and not self.is_editor_object(obj)
        ):
            self.schedule_pending_content_commit()
        return False

    def cleanup(self, app=None) -> None:
        try:
            self.commit_pending_content_change()
        except Exception:
            pass
        try:
            self.find_controller.cleanup()
        except Exception:
            pass
        try:
            self.editor.removeEventFilter(self)
        except Exception:
            pass
        try:
            self.editor.viewport().removeEventFilter(self)
        except Exception:
            pass
        self.save_timer.stop()
        self.commit_timer.stop()
        self.show_scheduled = False
        self.remove_application_event_filter(app)
