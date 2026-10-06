"""Build-helper верхней части и таблицы Servers page."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QHeaderView

from qfluentwidgets import (
    BreadcrumbBar,
    CaptionLabel,
    StrongBodyLabel,
    TableWidget,
    TitleLabel,
)

from ui.accessibility import set_control_accessibility, set_state_text
from updater.ui.active_server_icon import ACTIVE_SERVER_ICON_SIZE, ActiveServerLegendIcon


def set_active_server_legend_accessibility(label) -> None:
    text = "Легенда серверов обновлений: активный сервер"
    set_state_text(label, text)


def _label_text(label) -> str:
    try:
        value = label.text()
    except Exception:
        value = getattr(label, "text", "")
    return " ".join(str(value or "").strip().split())


def set_servers_page_title_accessibility(label) -> None:
    text = f"Страница: {_label_text(label)}"
    set_state_text(label, text)


def set_servers_section_title_accessibility(label) -> None:
    text = f"Раздел: {_label_text(label)}"
    set_state_text(label, text)


def rebuild_servers_breadcrumb(breadcrumb, *, tr_fn) -> None:
    breadcrumb.blockSignals(True)
    try:
        breadcrumb.clear()
        breadcrumb.addItem("about", tr_fn("page.servers.breadcrumb.about", "О программе"))
        breadcrumb.addItem("servers", tr_fn("page.servers.title", "Серверы"))
    finally:
        breadcrumb.blockSignals(False)


def handle_servers_breadcrumb_item_changed(key, *, breadcrumb, tr_fn, on_about_clicked) -> None:
    # Клик по крошке уже удалил из BreadcrumbBar элементы правее выбранного —
    # восстанавливаем полный путь до навигации, иначе при возврате на страницу
    # серверов крошка "Серверы" остаётся обрезанной навсегда.
    rebuild_servers_breadcrumb(breadcrumb, tr_fn=tr_fn)
    if key == "about":
        on_about_clicked()


@dataclass(slots=True)
class ServersHeaderWidgets:
    header_widget: QWidget
    breadcrumb: BreadcrumbBar
    page_title_label: object
    servers_header_widget: QWidget
    servers_title_label: object
    legend_active_label: object
    legend_active_icon: object


def build_servers_header_widgets(*, tr_fn, parent, on_about_clicked) -> ServersHeaderWidgets:
    header = QWidget()
    header_layout = QVBoxLayout(header)
    header_layout.setContentsMargins(0, 0, 0, 8)
    header_layout.setSpacing(4)

    breadcrumb = BreadcrumbBar(parent)
    rebuild_servers_breadcrumb(breadcrumb, tr_fn=tr_fn)
    breadcrumb.currentItemChanged.connect(
        lambda key: handle_servers_breadcrumb_item_changed(
            key,
            breadcrumb=breadcrumb,
            tr_fn=tr_fn,
            on_about_clicked=on_about_clicked,
        )
    )
    header_layout.addWidget(breadcrumb)

    page_title_label = TitleLabel(tr_fn("page.servers.title", "Серверы"))
    set_servers_page_title_accessibility(page_title_label)
    header_layout.addWidget(page_title_label)

    servers_header = QHBoxLayout()
    servers_title_label = StrongBodyLabel(
        tr_fn("page.servers.section.update_servers", "Серверы обновлений")
    )
    set_servers_section_title_accessibility(servers_title_label)
    servers_header.addWidget(servers_title_label)
    servers_header.addStretch()

    # Тот же значок, что стоит в таблице у активного сервера.
    legend_active_icon = ActiveServerLegendIcon()
    servers_header.addWidget(legend_active_icon)
    servers_header.addSpacing(5)
    legend_active_label = CaptionLabel(tr_fn("page.servers.legend.active", "активный"))
    set_active_server_legend_accessibility(legend_active_label)
    servers_header.addWidget(legend_active_label)

    servers_header_widget = QWidget()
    servers_header_widget.setLayout(servers_header)

    return ServersHeaderWidgets(
        header_widget=header,
        breadcrumb=breadcrumb,
        page_title_label=page_title_label,
        servers_header_widget=servers_header_widget,
        servers_title_label=servers_title_label,
        legend_active_label=legend_active_label,
        legend_active_icon=legend_active_icon,
    )


def build_servers_table_widget(*, tr_fn):
    table = TableWidget()
    set_control_accessibility(
        table,
        name=tr_fn("page.servers.table.accessible_name", "Серверы обновлений"),
        description=tr_fn(
            "page.servers.table.accessible_description",
            "Показывает сервер, статус и версии обновлений. Перемещайтесь по строкам стрелками.",
        ),
    )
    set_state_text(
        table,
        tr_fn("page.servers.table.loading_state", "Серверы обновлений: строки пока не загружены"),
    )
    table.setColumnCount(4)
    table.setRowCount(0)
    table.setBorderVisible(True)
    table.setBorderRadius(8)
    table.setHorizontalHeaderLabels([
        tr_fn("page.servers.table.header.server", "Сервер"),
        tr_fn("page.servers.table.header.status", "Статус"),
        tr_fn("page.servers.table.header.time", "Время"),
        tr_fn("page.servers.table.header.versions", "Версии"),
    ])
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(36)
    table.setIconSize(QSize(ACTIVE_SERVER_ICON_SIZE, ACTIVE_SERVER_ICON_SIZE))
    table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
    return table
