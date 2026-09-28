from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel, QTableWidget, QWidget
from qfluentwidgets import PushButton


class TableAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _add_strategy_scan_result_row(self, table: QTableWidget) -> None:
        from blockcheck.ui.strategy_scan_page_results_workflow import add_strategy_result_row

        class _Feature:
            def build_result_presentation(self, _result, *, scan_cursor: int):
                self.scan_cursor = scan_cursor
                return SimpleNamespace(
                    number_text="1",
                    strategy_name="TLS fake",
                    strategy_tooltip="Подмена TLS",
                    status_text="OK",
                    status_tone="success",
                    status_tooltip="Стратегия сработала",
                    time_text="120 ms",
                    can_apply=True,
                    stored_row={"strategy": "TLS fake"},
                )

        add_strategy_result_row(
            blockcheck_feature=_Feature(),
            table=table,
            result=SimpleNamespace(strategy_args="--lua-desync=fake", strategy_name="TLS fake"),
            scan_cursor=0,
            tr_fn=lambda _key, default: default,
            push_button_cls=PushButton,
            on_apply_strategy=lambda _args, _name: None,
        )

    def test_strategy_scan_result_row_has_screen_reader_text(self) -> None:
        table = QTableWidget(0, 5)

        self._add_strategy_scan_result_row(table)

        expected = "Строка 1. Стратегия TLS fake, статус OK, время 120 ms. Доступно действие: применить."
        self.assertEqual(table.item(0, 1).data(Qt.ItemDataRole.AccessibleTextRole), expected)
        self.assertEqual(table.item(0, 2).data(Qt.ItemDataRole.AccessibleTextRole), expected)
        self.assertEqual(table.item(0, 3).data(Qt.ItemDataRole.AccessibleTextRole), expected)
        self.assertEqual(table.cellWidget(0, 4).accessibleName(), "Применить стратегию TLS fake")

    def test_strategy_scan_table_reports_current_row_to_screen_reader(self) -> None:
        table = QTableWidget(0, 5)

        self._add_strategy_scan_result_row(table)
        row_text = table.item(0, 1).data(Qt.ItemDataRole.AccessibleTextRole)

        table.setCurrentCell(0, 2)

        self.assertEqual(table.property("screenReaderStateText"), row_text)

    def test_updater_server_row_has_screen_reader_text(self) -> None:
        from updater.ui.table_view import render_server_row

        table = QTableWidget(1, 4)

        render_server_row(
            table,
            row=0,
            server_name="server-1",
            status={"status": "online", "response_time": 0.12, "stable_version": "1.2.3", "dev_version": "1.2.4"},
            channel="stable",
            language="ru",
            accent_hex="#52c477",
        )

        text = table.item(0, 1).data(Qt.ItemDataRole.AccessibleTextRole)

        self.assertIn("Сервер server-1", text)
        self.assertIn("статус Онлайн", text)

    def test_updater_servers_table_reports_current_row_to_screen_reader(self) -> None:
        from updater.ui.table_view import render_server_row

        table = QTableWidget(1, 4)

        render_server_row(
            table,
            row=0,
            server_name="server-1",
            status={"status": "online", "response_time": 0.12, "stable_version": "1.2.3", "dev_version": "1.2.4"},
            channel="stable",
            language="ru",
            accent_hex="#52c477",
        )
        row_text = table.item(0, 0).data(Qt.ItemDataRole.AccessibleTextRole)

        table.setCurrentCell(0, 1)

        self.assertEqual(table.property("screenReaderStateText"), row_text)

    def test_updater_servers_table_reset_restores_empty_screen_reader_state(self) -> None:
        from updater.ui.main_build import build_servers_table_widget
        from updater.ui.table_view import render_server_row, reset_server_rows

        class _TableState:
            def __init__(self) -> None:
                self.reset_called = False

            def reset(self) -> None:
                self.reset_called = True

        table = build_servers_table_widget(tr_fn=lambda _key, default: default)
        table.setRowCount(1)
        render_server_row(
            table,
            row=0,
            server_name="server-1",
            status={"status": "online", "response_time": 0.12, "stable_version": "1.2.3", "dev_version": "1.2.4"},
            channel="stable",
            language="ru",
            accent_hex="#52c477",
        )
        table.setCurrentCell(0, 1)
        table_state = _TableState()

        reset_server_rows(table, table_state=table_state)

        self.assertTrue(table_state.reset_called)
        self.assertEqual(table.rowCount(), 0)
        self.assertEqual(table.accessibleName(), "Серверы обновлений: строки пока не загружены")
        self.assertEqual(
            table.property("screenReaderStateText"),
            "Серверы обновлений: строки пока не загружены",
        )

    def test_updater_servers_table_has_screen_reader_name(self) -> None:
        from updater.ui.main_build import build_servers_table_widget

        table = build_servers_table_widget(tr_fn=lambda _key, default: default)

        self.assertEqual(table.accessibleName(), "Серверы обновлений: строки пока не загружены")
        self.assertEqual(
            table.property("screenReaderStateText"),
            "Серверы обновлений: строки пока не загружены",
        )
        self.assertIn("статус и версии", table.accessibleDescription())

    def test_updater_active_server_legend_has_screen_reader_text(self) -> None:
        from updater.ui.main_build import build_servers_header_widgets

        parent = QWidget()
        self.addCleanup(parent.deleteLater)

        widgets = build_servers_header_widgets(
            tr_fn=lambda _key, default: default,
            parent=parent,
            on_about_clicked=lambda: None,
        )

        expected = "Легенда серверов обновлений: активный сервер"
        self.assertEqual(widgets.legend_active_label.accessibleName(), expected)
        self.assertEqual(
            widgets.legend_active_label.property("screenReaderStateText"),
            expected,
        )

    def test_updater_server_headers_have_screen_reader_text(self) -> None:
        from updater.ui.main_build import build_servers_header_widgets

        parent = QWidget()
        self.addCleanup(parent.deleteLater)

        widgets = build_servers_header_widgets(
            tr_fn=lambda _key, default: default,
            parent=parent,
            on_about_clicked=lambda: None,
        )

        self.assertEqual(widgets.page_title_label.accessibleName(), "Страница: Серверы")
        self.assertEqual(widgets.servers_title_label.accessibleName(), "Раздел: Серверы обновлений")


if __name__ == "__main__":
    unittest.main()
