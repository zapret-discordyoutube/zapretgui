"""Сборка виджетов страницы «Фейки» (без логики)."""

from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QHeaderView, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BreadcrumbBar,
    CaptionLabel,
    FluentIcon,
    LineEdit,
    PushButton,
    SearchLineEdit,
    SimpleCardWidget,
    StrongBodyLabel,
    TableWidget,
)

from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_widgets import QuickActionsBar, RefreshButton

FAKES_COLUMNS = ["Имя", "Файл", "Тип", "SNI", "Используется", "Описание"]

BLOB_LINE_PLACEHOLDER = "Выберите фейк в таблице"

SHIPPED_FAKE_HINT = (
    "Обычно вписывать её вручную не нужно: когда вы выбираете готовую стратегию, программа "
    "сама дописывает в пресет строки --blob= для нужных ей фейков."
)
USER_FAKE_HINT = (
    "Чтобы пользоваться своим фейком, укажите в стратегии blob=<имя фейка> и вставьте эту "
    "строку в текст пресета. Если стратегия из списка готовых ссылается на этот фейк, "
    "строку при выборе стратегии программа допишет сама."
)


def _configure_table(table: TableWidget) -> None:
    table.setColumnCount(len(FAKES_COLUMNS))
    table.setRowCount(0)
    table.setHorizontalHeaderLabels(FAKES_COLUMNS)
    table.setBorderVisible(True)
    table.setBorderRadius(8)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(34)
    table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(TableWidget.SelectionMode.SingleSelection)
    table.setMinimumHeight(360)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    table.setWordWrap(False)
    table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    # Длинный текст обрезается многоточием посередине, а не растягивает таблицу.
    table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
    header = table.horizontalHeader()
    for column in range(len(FAKES_COLUMNS) - 1):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(len(FAKES_COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)


def build_fakes_page_ui(page) -> SimpleNamespace:
    """Создаёт виджеты страницы и кладёт их в layout страницы."""
    ui = SimpleNamespace()

    # Заголовок страницы показывают хлебные крошки «Управление › Фейки».
    if page.title_label is not None:
        page._title_key = None
        page.title_label.setText("")
        page.title_label.hide()
    ui.breadcrumb = BreadcrumbBar(page.content)
    page.layout.insertWidget(0, ui.breadcrumb)

    # --- Действия и поиск ---
    ui.actions_bar = QuickActionsBar(page.content)
    ui.add_btn = PushButton(FluentIcon.ADD, "Добавить свой фейк…", ui.actions_bar)
    set_control_accessibility(
        ui.add_btn,
        name="Добавить свой фейк",
        description="Выбрать .bin-файл и дать ему имя, чтобы стратегии могли ссылаться на него.",
    )
    ui.delete_btn = PushButton(FluentIcon.DELETE, "Удалить", ui.actions_bar)
    ui.delete_btn.setEnabled(False)
    set_control_accessibility(
        ui.delete_btn,
        name="Удалить свой фейк",
        description="Удаляет выбранный свой фейк. Встроенные фейки удалить нельзя.",
    )
    ui.open_folder_btn = PushButton(FluentIcon.FOLDER, "Открыть папку", ui.actions_bar)
    set_control_accessibility(
        ui.open_folder_btn,
        name="Открыть папку своих фейков",
        description="Открывает папку user/fakes, где лежат файлы своих фейков.",
    )
    ui.refresh_btn = RefreshButton("Обновить", parent=ui.actions_bar)
    ui.actions_bar.add_buttons([ui.add_btn, ui.delete_btn, ui.open_folder_btn, ui.refresh_btn])
    page.add_widget(ui.actions_bar)

    filter_row = QWidget(page.content)
    filter_layout = QHBoxLayout(filter_row)
    filter_layout.setContentsMargins(0, 0, 0, 0)
    filter_layout.setSpacing(12)
    ui.search_edit = SearchLineEdit(filter_row)
    ui.search_edit.setPlaceholderText("Поиск по имени, файлу, типу или SNI")
    ui.search_edit.setFixedWidth(320)
    set_control_accessibility(
        ui.search_edit,
        name="Поиск фейков",
        description="Фильтрует таблицу по имени, файлу, типу, SNI или описанию.",
    )
    filter_layout.addWidget(ui.search_edit)
    ui.summary_label = CaptionLabel("Загрузка…", filter_row)
    ui.summary_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    ui.summary_label.setMinimumWidth(120)
    filter_layout.addWidget(ui.summary_label, 1)
    page.add_widget(filter_row)

    # --- Таблица ---
    ui.table = TableWidget(page.content)
    _configure_table(ui.table)
    set_control_accessibility(
        ui.table,
        name="Таблица фейков",
        description="Встроенные фейки winws2 и свои фейки. Выберите строку, чтобы увидеть строку --blob= для пресета.",
    )
    set_state_text(ui.table, "Таблица фейков: загрузка")
    page.add_widget(ui.table)

    # --- Готовая строка --blob= для выбранного фейка ---
    ui.blob_card = SimpleCardWidget(page.content)
    blob_layout = QVBoxLayout(ui.blob_card)
    blob_layout.setContentsMargins(16, 12, 16, 12)
    blob_layout.setSpacing(8)
    ui.blob_title = StrongBodyLabel("Строка для пресета", ui.blob_card)
    blob_layout.addWidget(ui.blob_title)
    blob_row = QHBoxLayout()
    blob_row.setSpacing(8)
    ui.blob_line_edit = LineEdit(ui.blob_card)
    ui.blob_line_edit.setReadOnly(True)
    ui.blob_line_edit.setPlaceholderText(BLOB_LINE_PLACEHOLDER)
    set_control_accessibility(
        ui.blob_line_edit,
        name="Строка --blob= выбранного фейка",
        description="Готовая строка объявления фейка для текста пресета.",
    )
    blob_row.addWidget(ui.blob_line_edit, 1)
    ui.copy_btn = PushButton(FluentIcon.COPY, "Скопировать", ui.blob_card)
    ui.copy_btn.setEnabled(False)
    set_control_accessibility(
        ui.copy_btn,
        name="Скопировать строку --blob=",
        description="Копирует строку объявления выбранного фейка в буфер обмена.",
    )
    blob_row.addWidget(ui.copy_btn)
    blob_layout.addLayout(blob_row)
    ui.blob_hint = CaptionLabel(SHIPPED_FAKE_HINT, ui.blob_card)
    ui.blob_hint.setWordWrap(True)
    blob_layout.addWidget(ui.blob_hint)
    page.add_widget(ui.blob_card)

    return ui


__all__ = ["BLOB_LINE_PLACEHOLDER", "FAKES_COLUMNS", "SHIPPED_FAKE_HINT", "USER_FAKE_HINT", "build_fakes_page_ui"]
