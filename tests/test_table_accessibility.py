from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QTableWidget, QWidget


class TableAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_strategy_scan_result_row_has_screen_reader_text(self) -> None:
        from blockcheck.ui.strategy_scan_page_results_workflow import add_strategy_result_row
        from blockcheck.ui.strategy_scan_widgets import StrategyResultsView

        class _Feature:
            def build_result_presentation(self, _result, *, row_number: int):
                return SimpleNamespace(
                    number_text=str(row_number),
                    strategy_name="TLS fake",
                    strategy_tooltip="Подмена TLS",
                    status_text="Работает 3/3",
                    status_tone="success",
                    status_tooltip="Стратегия сработала",
                    time_text="120",
                    can_apply=True,
                    stored_row={"strategy": "TLS fake", "verdict": "working"},
                )

        applied = []
        view = StrategyResultsView()
        add_strategy_result_row(
            blockcheck_feature=_Feature(),
            results_view=view,
            result=SimpleNamespace(strategy_args="--lua-desync=fake", strategy_name="TLS fake", success=True),
            row_number=1,
            on_apply_strategy=applied.append,
        )

        row = view.working_group.rows()[0]
        expected = "Строка 1. Стратегия TLS fake, статус Работает 3/3, время 120. Доступно действие: применить."
        self.assertEqual(row.property("screenReaderStateText"), expected)
        self.assertEqual(row.apply_button.accessibleName(), "Применить стратегию TLS fake")
        row.apply_button.click()
        self.assertEqual(len(applied), 1)
        self.assertIn("надёжно работают 1", view.property("screenReaderStateText"))

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
