"""Страница «Фейки»: встроенные фейки winws2 и свои фейки пользователя.

Страница только показывает данные и вызывает готовые действия. Реестр,
настройки и файлы читает и меняет фоновый worker из ``FakesFeature``.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QFileDialog, QTableWidgetItem
from qfluentwidgets import InfoBar, InfoBarPosition

from ui.accessibility import set_state_text
from ui.fluent_dialog import MessageBox
from ui.message_box_accessibility import set_message_box_button_accessibility
from ui.one_shot_worker_runtime import OneShotWorkerRuntime
from ui.pages.base_page import BasePage
from ui.widgets.fluent_item_tooltip import set_fluent_item_tooltip

from .build import BLOB_LINE_PLACEHOLDER, SHIPPED_FAKE_HINT, USER_FAKE_HINT, build_fakes_page_ui
from .dialogs import AddUserFakeDialog

_KIND_TITLES = {
    "tls": "TLS",
    "quic": "QUIC",
    "http": "HTTP",
    "stun": "STUN",
    "dht": "DHT",
    "discord": "Discord",
    "wireguard": "WireGuard",
    "dtls": "DTLS",
    "zeros": "Нули",
    "other": "Другое",
}

_USER_MARK = "свой"
_USER_COLOR = QColor("#60cdff")
_MISSING_COLOR = QColor("#e05454")

# Длинное hex-значение (например, 64 байта нулей) растягивало столбец «Файл» на
# всю таблицу. В ячейке показываем начало и размер; полное значение — в подсказке
# и в строке для пресета.
_HEX_PREVIEW_DIGITS = 8


def _bytes_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "байт"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "байта"
    return "байт"


def compact_file_label(label: str) -> str:
    text = str(label or "")
    if not text.lower().startswith("0x"):
        return text
    digits = text[2:]
    if len(digits) <= _HEX_PREVIEW_DIGITS:
        return text
    size = len(digits) // 2
    return f"0x{digits[:_HEX_PREVIEW_DIGITS]}… ({size} {_bytes_word(size)})"


def _strategies_text(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        word = "стратегия"
    elif 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        word = "стратегии"
    else:
        word = "стратегий"
    return f"{count} {word}"


def _row_matches(row, text: str) -> bool:
    if not text:
        return True
    haystack = " ".join(
        (
            row.name,
            row.file_label,
            row.kind,
            _KIND_TITLES.get(row.kind, row.kind),
            row.sni,
            row.description,
            _USER_MARK if row.is_user else "",
        )
    ).casefold()
    return all(part in haystack for part in text.casefold().split())


class FakesPage(BasePage):
    def __init__(self, parent=None, *, deps):
        super().__init__(
            "Фейки",
            "Фейки winws2: пакеты, которые стратегия отправляет вместо настоящих. "
            "Здесь можно посмотреть встроенные и добавить свои",
            parent,
            title_key="page.fakes.title",
            subtitle_key="page.fakes.subtitle",
        )
        self._deps = deps
        self._snapshot = None
        self._snapshot_dirty = True
        self._filtered: list = []
        self._load_runtime = OneShotWorkerRuntime()
        self._action_runtime = OneShotWorkerRuntime()
        self._folder_runtime = OneShotWorkerRuntime()
        self._ui = build_fakes_page_ui(self)
        self._rebuild_breadcrumb()
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._refresh_table)
        self._connect_signals()
        self._update_action_state()

    # ------------------------------------------------------------ setup

    def _connect_signals(self) -> None:
        ui = self._ui
        ui.breadcrumb.currentItemChanged.connect(self._on_breadcrumb_item_changed)
        ui.add_btn.clicked.connect(self._on_add_clicked)
        ui.delete_btn.clicked.connect(self._on_delete_clicked)
        ui.open_folder_btn.clicked.connect(self._on_open_folder_clicked)
        ui.refresh_btn.clicked.connect(lambda: self._load_snapshot())
        ui.copy_btn.clicked.connect(self._on_copy_clicked)
        ui.search_edit.textChanged.connect(lambda _text: self._filter_timer.start())
        ui.table.itemSelectionChanged.connect(self._on_selection_changed)

    def _rebuild_breadcrumb(self) -> None:
        breadcrumb = self._ui.breadcrumb
        breadcrumb.blockSignals(True)
        try:
            breadcrumb.clear()
            breadcrumb.addItem("control", self._tr_text("page.fakes.breadcrumb.control", "Управление"))
            breadcrumb.addItem("fakes", self._tr_text("page.fakes.title", "Фейки"))
        finally:
            breadcrumb.blockSignals(False)

    def _tr_text(self, key: str, default: str) -> str:
        from app.ui_texts import tr as tr_catalog

        return tr_catalog(key, language=self._ui_language, default=default)

    def _on_breadcrumb_item_changed(self, key: str) -> None:
        # Клик по крошке удаляет элементы правее — восстанавливаем полный путь.
        self._rebuild_breadcrumb()
        if key == "control":
            self._deps.open_control_page()

    def set_ui_language(self, language: str) -> None:
        super().set_ui_language(language)
        self._rebuild_breadcrumb()

    def on_page_activated(self) -> None:
        super().on_page_activated()
        if self._snapshot_dirty:
            self._load_snapshot()

    def cleanup(self) -> None:
        super().cleanup()
        self._filter_timer.stop()
        for runtime in (self._load_runtime, self._action_runtime, self._folder_runtime):
            runtime.stop(blocking=False)

    # ------------------------------------------------------------ загрузка

    def _load_snapshot(self) -> None:
        if self._cleanup_in_progress:
            return
        self._snapshot_dirty = False
        self._ui.refresh_btn.set_loading(True)
        if self._snapshot is None:
            self._ui.summary_label.setText("Загрузка…")
        self._load_runtime.stop(blocking=False)
        self._load_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._deps.create_snapshot_worker(request_id, parent=self),
            on_loaded=self._on_snapshot_loaded,
            on_failed=self._on_snapshot_failed,
        )

    def _on_snapshot_loaded(self, request_id: int, snapshot) -> None:
        if not self._load_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        self._ui.refresh_btn.set_loading(False)
        self.apply_snapshot(snapshot)

    def _on_snapshot_failed(self, request_id: int, message: str) -> None:
        if not self._load_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        self._ui.refresh_btn.set_loading(False)
        self._snapshot_dirty = True
        self._ui.summary_label.setText(f"Не удалось прочитать фейки: {message}")

    def apply_snapshot(self, snapshot) -> None:
        """Показать готовые данные страницы (строки таблицы и правила имён)."""
        self._snapshot = snapshot
        self._refresh_table()
        self._update_action_state()

    def _summary_text(self) -> str:
        snapshot = self._snapshot
        if snapshot is None:
            return "Загрузка…"
        rows = snapshot.rows
        own = sum(1 for row in rows if row.is_user)
        parts = [f"Всего: {len(rows)}", f"своих: {own}"]
        if len(self._filtered) != len(rows):
            parts.append(f"показано: {len(self._filtered)}")
        text = " · ".join(parts)
        if snapshot.catalog_error:
            text += f" · реестр фейков недоступен: {snapshot.catalog_error}"
        if snapshot.usage_error:
            text += f" · не удалось посчитать стратегии: {snapshot.usage_error}"
        return text

    # ------------------------------------------------------------ таблица

    def _refresh_table(self) -> None:
        ui = self._ui
        rows = self._snapshot.rows if self._snapshot is not None else ()
        selected = self._selected_row()
        selected_name = selected.name if selected is not None else ""
        text = ui.search_edit.text().strip()
        self._filtered = [row for row in rows if _row_matches(row, text)]
        table = ui.table
        table.setUpdatesEnabled(False)
        table.blockSignals(True)
        try:
            table.clearSelection()
            table.setRowCount(len(self._filtered))
            for index, row in enumerate(self._filtered):
                self._set_table_row(index, row)
            if selected_name:
                for index, row in enumerate(self._filtered):
                    if row.name == selected_name:
                        table.selectRow(index)
                        break
        finally:
            table.blockSignals(False)
            table.setUpdatesEnabled(True)
        set_state_text(table, f"Таблица фейков: {len(self._filtered)} строк")
        ui.summary_label.setText(self._summary_text())
        self._on_selection_changed()

    def _set_table_row(self, index: int, row) -> None:
        if row.is_user:
            used = "—"
        elif row.used_by is None:
            used = "?"
        else:
            used = _strategies_text(row.used_by) if row.used_by else "не используется"
        full_file_label = row.file_label
        file_label = compact_file_label(full_file_label)
        if row.file_missing:
            file_label = f"{file_label} (файл не найден)"
        values = [
            f"{row.name}  · {_USER_MARK}" if row.is_user else row.name,
            file_label,
            _KIND_TITLES.get(row.kind, row.kind),
            row.sni or "—",
            used,
            row.description or "—",
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, row.name)
                if row.is_user:
                    item.setForeground(_USER_COLOR)
            if column == 1 and row.file_missing:
                item.setForeground(_MISSING_COLOR)
            if column == 1:
                set_fluent_item_tooltip(item, full_file_label)
            elif column == 5:
                set_fluent_item_tooltip(item, value)
            self._ui.table.setItem(index, column, item)

    def _selected_row(self):
        table = self._ui.table
        rows = {item.row() for item in table.selectedItems()}
        if len(rows) != 1:
            return None
        index = rows.pop()
        if 0 <= index < len(self._filtered):
            return self._filtered[index]
        return None

    def _on_selection_changed(self) -> None:
        row = self._selected_row()
        ui = self._ui
        if row is None:
            ui.blob_line_edit.setText("")
            ui.blob_line_edit.setPlaceholderText(BLOB_LINE_PLACEHOLDER)
        else:
            ui.blob_line_edit.setText(row.blob_line)
        ui.blob_hint.setText(USER_FAKE_HINT if row is not None and row.is_user else SHIPPED_FAKE_HINT)
        ui.copy_btn.setEnabled(row is not None)
        self._update_action_state()

    def _update_action_state(self) -> None:
        ui = self._ui
        busy = self._action_runtime.is_running()
        row = self._selected_row()
        ui.add_btn.setEnabled(self._snapshot is not None and not busy)
        ui.delete_btn.setEnabled(row is not None and bool(row.is_user) and not busy)

    def _on_copy_clicked(self) -> None:
        text = self._ui.blob_line_edit.text()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self._show_info("Скопировано", text)

    # ------------------------------------------------------------ добавить

    def _choose_fake_file(self) -> str:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выбрать файл фейка",
            "",
            "Файлы фейков (*.bin);;Все файлы (*.*)",
        )
        return str(path or "")

    def _ask_fake_name(self, file_path: str):
        """(имя, описание) из диалога или None, если пользователь отменил."""
        file_name = file_path.replace("\\", "/").rsplit("/", 1)[-1]
        dialog = AddUserFakeDialog(rules=self._snapshot.rules, file_name=file_name, parent=self)
        if not dialog.exec():
            return None
        return dialog.fake_name(), dialog.fake_description()

    def _on_add_clicked(self) -> None:
        if self._snapshot is None or self._action_runtime.is_running():
            return
        path = self._choose_fake_file()
        if not path:
            return
        answer = self._ask_fake_name(path)
        if answer is None:
            return
        name, description = answer
        self._start_action(
            lambda request_id: self._deps.create_import_worker(
                request_id,
                source_path=path,
                name=name,
                description=description,
                parent=self,
            ),
            success_title="Фейк добавлен",
            success_text=lambda entry: entry.blob_line(),
            error_title="Фейк не добавлен",
        )

    # ------------------------------------------------------------ удалить

    def _confirm_delete(self, name: str) -> bool:
        title = "Удалить свой фейк?"
        body = (
            f"Фейк «{name}» и его файл будут удалены.\n\n"
            "Если какой-то пресет уже объявляет этот фейк строкой --blob=, такой пресет "
            "не запустится, пока вы не уберёте эту строку или не выберете другую стратегию."
        )
        box = MessageBox(title, body, self.window())
        set_message_box_button_accessibility(
            box,
            yes_name="Удалить фейк",
            yes_description=body,
            cancel_name="Не удалять",
            cancel_description="Закрывает диалог, фейк остаётся.",
        )
        return bool(box.exec())

    def _on_delete_clicked(self) -> None:
        row = self._selected_row()
        if row is None or not row.is_user or self._action_runtime.is_running():
            return
        name = row.name
        if not self._confirm_delete(name):
            return
        self._start_action(
            lambda request_id: self._deps.create_delete_worker(request_id, name=name, parent=self),
            success_title="Фейк удалён",
            success_text=lambda _result: name,
            error_title="Фейк не удалён",
        )

    # ------------------------------------------------------------ общее

    def _start_action(self, worker_factory, *, success_title: str, success_text, error_title: str) -> None:
        def _on_loaded(request_id: int, result) -> None:
            if not self._action_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
                return
            self._show_info(success_title, success_text(result))
            self._load_snapshot()

        def _on_failed(request_id: int, message: str) -> None:
            if not self._action_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
                return
            self._show_error(error_title, message)
            self._update_action_state()

        self._action_runtime.start_qthread_worker(
            worker_factory=worker_factory,
            on_loaded=_on_loaded,
            on_failed=_on_failed,
            on_finished=lambda *_args: self._update_action_state(),
        )
        self._update_action_state()

    def _on_open_folder_clicked(self) -> None:
        def _on_failed(request_id: int, _action_name: str, message: str) -> None:
            if not self._folder_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
                return
            self._show_error("Не удалось открыть папку", message)

        self._folder_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._deps.create_open_folder_worker(request_id, parent=self),
            on_failed=_on_failed,
        )

    def _show_info(self, title: str, text: str) -> None:
        InfoBar.success(
            title=title,
            content=text,
            parent=self,
            duration=2500,
            position=InfoBarPosition.TOP,
        )

    def _show_error(self, title: str, text: str) -> None:
        InfoBar.error(
            title=title,
            content=text,
            parent=self,
            duration=5000,
            position=InfoBarPosition.TOP,
        )


__all__ = ["FakesPage"]
