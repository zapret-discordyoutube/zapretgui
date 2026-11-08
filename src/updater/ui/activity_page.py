"""Страница «Кто подключается»: кто обращался к этому компьютеру за файлами обновления.

Открывается кнопкой на странице «Серверы», путь в хлебных крошках «Серверы → Кто подключается».
Показывает, работает ли раздача по сети и через интернет, и журнал обращений: когда, откуда
(локальная сеть или интернет), с какого адреса, что просили, сколько байт ушло и чем кончилось.
Журнал лежит только в памяти, пока программа запущена; адреса на диск не пишутся.
Страница читает готовый снимок из памяти каждые две секунды, пока она на экране.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QAbstractItemView, QHBoxLayout, QHeaderView, QSizePolicy, QTableWidgetItem, QWidget
from qfluentwidgets import BodyLabel, BreadcrumbBar, CaptionLabel, FluentIcon, PushButton, TableWidget

from app.ui_texts import tr as tr_catalog
from ui.accessibility import set_breadcrumb_accessibility, set_control_accessibility, set_item_accessible_text, set_state_text
from ui.fluent_widgets import SettingsCard
from ui.pages.base_page import BasePage
from zapret_update import share_activity_view as activity_view

REFRESH_MS = 2000
ROWS_SHOWN = 300
COLUMNS = ("time", "dir", "via", "peer", "what", "size", "result")
_COLUMN_DEFAULTS = {
    "time": "Время",
    "dir": "Кто к кому",
    "via": "Откуда",
    "peer": "Адрес",
    "what": "Что",
    "size": "Размер",
    "result": "Итог",
}


class SharingActivityPage(BasePage):
    """Журнал обращений к раздаче обновлений."""

    def __init__(self, parent=None, *, deps):
        super().__init__("Кто подключается", "", parent, title_key="page.sharing_activity.title")
        self._feature = deps.updater_feature
        self._open_servers_page = deps.open_servers_page
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._last_signature: object = None
        self._build_ui()
        self._retranslate()
        self.refresh()

    def _tr(self, key: str, default: str) -> str:
        return tr_catalog(key, language=self._ui_language, default=default)

    # ── построение ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Заголовок заменяют хлебные крошки «Серверы → Кто подключается».
        if self.title_label is not None:
            self.title_label.hide()
        if self.subtitle_label is not None:
            self.subtitle_label.hide()
        self.breadcrumb = BreadcrumbBar(self.content)
        self.breadcrumb.currentItemChanged.connect(self._on_breadcrumb_item_changed)
        self.add_widget(self.breadcrumb)

        self.status_card = SettingsCard(parent=self.content)
        # Карточка не растягивается по высоте окна: свободное место забирает таблица или пустота внизу.
        self.status_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.status_labels: list[BodyLabel] = []
        for _ in range(2):
            label = BodyLabel(self.status_card)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.status_card.main_layout.addWidget(label)
            self.status_labels.append(label)
        self.notes_label = CaptionLabel(self.status_card)
        self.notes_label.setWordWrap(True)
        self.notes_label.hide()
        self.status_card.main_layout.addWidget(self.notes_label)
        self.add_widget(self.status_card)

        bar = QWidget(self.content)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(12)
        self.summary_label = BodyLabel(bar)
        bar_layout.addWidget(self.summary_label, 1)
        self.clear_button = PushButton(FluentIcon.DELETE, "", bar)
        self.clear_button.clicked.connect(self._clear)
        bar_layout.addWidget(self.clear_button)
        self.add_widget(bar)

        self.table = TableWidget(self.content)
        self.table.setColumnCount(len(COLUMNS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.setBorderVisible(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COLUMNS.index("what"), QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(320)
        self.add_widget(self.table, stretch=100)

        self.empty_label = CaptionLabel(self.content)
        self.empty_label.setWordWrap(True)
        self.add_widget(self.empty_label)
        self.hint_label = CaptionLabel(self.content)
        self.hint_label.setWordWrap(True)
        self.add_widget(self.hint_label)
        # Пока таблица спрятана (журнал пуст), лишнее место уходит сюда, а не растаскивает надписи по окну.
        self.vBoxLayout.addStretch(1)

    def _retranslate(self) -> None:
        tr = self._tr
        items = (tr("page.servers.title", "Серверы"), tr("page.sharing_activity.title", "Кто подключается"))
        self.breadcrumb.blockSignals(True)
        try:
            self.breadcrumb.clear()
            self.breadcrumb.addItem("servers", items[0])
            self.breadcrumb.addItem("activity", items[1])
            set_breadcrumb_accessibility(self.breadcrumb, items)
        finally:
            self.breadcrumb.blockSignals(False)
        self.clear_button.setText(tr("page.sharing_activity.clear", "Очистить журнал"))
        set_control_accessibility(self.clear_button, name=tr("page.sharing_activity.clear", "Очистить журнал"))
        self.table.setHorizontalHeaderLabels(
            [tr(f"page.sharing_activity.column.{key}", _COLUMN_DEFAULTS[key]) for key in COLUMNS]
        )
        set_control_accessibility(
            self.table,
            name=tr("page.sharing_activity.table", "Журнал обращений к раздаче обновлений"),
            description=tr(
                "page.sharing_activity.table.description",
                "Каждая строка: время, кто к кому обращался, откуда, адрес, что просили, размер и итог.",
            ),
        )
        self.hint_label.setText(
            tr(
                "page.sharing_activity.hint",
                "Журнал хранится, только пока программа запущена: адреса других компьютеров на диск не записываются.",
            )
        )
        self._last_signature = None
        self.refresh()

    # ── данные ─────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Перерисовывает страницу по снимку из памяти; не трогает диск и сеть."""
        try:
            snapshot = self._feature.sharing_activity()
        except Exception:  # noqa: BLE001 — страница не должна падать из-за журнала
            snapshot = {"available": False}
        events = list(snapshot.get("events", ()))[:ROWS_SHOWN]
        signature = (
            tuple(snapshot.get("notes", ())), snapshot.get("lan_port"), snapshot.get("internet_running"),
            snapshot.get("internet_address"), snapshot.get("available"), len(events),
            events[0].when if events else 0,
        )
        if signature == self._last_signature:
            return  # ничего не изменилось: таблицу не перестраиваем
        self._last_signature = signature
        tr = self._tr
        lines = activity_view.status_lines(snapshot, tr)
        for label, text in zip(self.status_labels, lines + [""] * len(self.status_labels)):
            label.setText(text)
            label.setVisible(bool(text))
            set_state_text(label, text)
        notes = list(snapshot.get("notes", ()))
        self.notes_label.setText("\n".join(notes))
        self.notes_label.setVisible(bool(notes))
        self.summary_label.setText(activity_view.summary_text(snapshot.get("summary", {}), tr))
        self.table.setRowCount(len(events))
        for row, event in enumerate(events):
            texts = activity_view.row_texts(event, tr)
            description = ", ".join(texts)
            for column, text in enumerate(texts):
                item = QTableWidgetItem(text)
                set_item_accessible_text(item, description if column == 0 else text)
                self.table.setItem(row, column, item)
        self.table.setVisible(bool(events))
        if events:
            self.empty_label.hide()
        else:
            key = "page.sharing_activity.empty" if snapshot.get("available") else "page.sharing_activity.empty.off"
            default = (
                "Пока никто не подключался. Когда другой компьютер возьмёт у вас файл или спросит про версию, это появится здесь."
                if snapshot.get("available")
                else "Раздача выключена или ещё не запустилась: включите её на странице «Серверы»."
            )
            self.empty_label.setText(tr(key, default))
            self.empty_label.show()

    def _clear(self) -> None:
        try:
            self._feature.clear_sharing_activity()
        finally:
            self._last_signature = None
            self.refresh()

    def _on_breadcrumb_item_changed(self, key: str) -> None:
        self._retranslate()  # клик по крошке обрезал путь: восстанавливаем его для следующего захода
        if key == "servers":
            self._open_servers_page()

    # ── жизненный цикл ─────────────────────────────────────────────────

    def on_page_activated(self) -> None:
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802 — имя задано Qt
        super().showEvent(event)
        self._timer.start()
        self.refresh()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def set_ui_language(self, language: str) -> None:
        super().set_ui_language(language)
        self._retranslate()


__all__ = ["SharingActivityPage"]
