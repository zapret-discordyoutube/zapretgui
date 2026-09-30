from __future__ import annotations

"""Настоящая страница «Серверы», собранная так же, как в программе.

Сетевая работа подменена, всё остальное — настоящее: виджеты, сервисы,
сигналы и общий координатор проверок.
"""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtWidgets import QApplication, QWidget

from app.feature_facades.updater import UpdaterFeature
from app.page_names import PageName
from updater.check.flow import CheckOutcome


def _wait(predicate, timeout: float = 5.0) -> bool:
    app = QApplication.instance()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


class ServersPageAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        QApplication.instance() or QApplication([])
        from ui.page_deps.system import build_servers_page_kwargs
        from updater.ui.page import ServersPage

        self.feature = UpdaterFeature()
        self.request_exit = Mock()
        runtime_feature = SimpleNamespace(
            is_any_running=Mock(return_value=False),
            shutdown_sync_from_worker=Mock(),
            is_available=Mock(return_value=True),
            restart=Mock(),
            objects=SimpleNamespace(runtime_service=None),
        )
        kwargs = build_servers_page_kwargs(
            page_name=PageName.SERVERS,
            runtime_feature=runtime_feature,
            updater_feature=self.feature,
            external_actions_feature=SimpleNamespace(create_open_url_worker=Mock()),
            show_page=Mock(),
            request_exit=self.request_exit,
        )
        self.downloaded = Mock()

        def run_check(_channel, *, language, emit_row, dpi):
            emit_row("Forgejo API", {"status": "online", "response_time": 0.01, "update_source": True})
            return CheckOutcome({"version": "999.0.0.1", "release_notes": "новое", "source": "Forgejo"})

        def run_install(_version, *, token, dpi, on_stage, on_progress, on_downloaded, splash=None):
            on_stage("Скачивание обновления…")
            on_progress(100, 10, 10)
            on_downloaded()
            self.downloaded()
            on_stage("Запуск установщика…")

        kwargs["check_service"]._run_check = run_check
        kwargs["install_service"]._run_install = run_install
        # Страница стоит в окне программы: окно обновления ложится поверх него.
        self.host = QWidget()
        self.host.resize(1200, 800)
        self.page = ServersPage(**kwargs)
        self.page.setParent(self.host)
        self.host.show()
        self.addCleanup(self._close_page)

    def _close_page(self) -> None:
        self.page.cleanup()
        self.host.deleteLater()
        QApplication.instance().processEvents()

    def _dialog(self):
        self.assertTrue(_wait(lambda: self.page._update_dialog is not None and self.page._update_dialog.isVisible()))
        return self.page._update_dialog

    def test_manual_check_opens_update_window_and_installs_until_exit(self) -> None:
        self.page._request_check_updates()

        self.assertTrue(_wait(lambda: self.feature.current_update_check_snapshot().phase == "completed"))
        self.assertTrue(_wait(lambda: self.page.servers_table.rowCount() >= 1))
        self.assertEqual(self.page._found_source, "Forgejo")
        dialog = self._dialog()
        self.assertIn("новое", dialog.browser.toPlainText())

        dialog.install_btn.click()

        self.assertTrue(_wait(lambda: self.request_exit.called))
        self.downloaded.assert_called_once_with()
        self.request_exit.assert_called_once_with(stop_dpi=False)

    def test_startup_result_opens_window_and_details_bring_it_back_after_later(self) -> None:
        token = self.feature.begin_update_check(source="startup")
        self.feature.finish_update_check(
            {"has_update": True, "version": "999.0.0.1", "release_notes": "новое", "error": None},
            source="startup",
            token=token,
        )

        self._dialog().later_btn.click()
        self.assertTrue(_wait(lambda: self.page._update_dialog is None))
        self.assertFalse(self.page.update_card.details_btn.isHidden())

        self.page.update_card.details_btn.click()

        self.assertIn("новое", self._dialog().browser.toPlainText())

    def test_window_waits_while_app_is_hidden_in_tray(self) -> None:
        """Окно обновления не всплывает без хозяина: ждёт, пока программу откроют."""
        self.host.hide()
        token = self.feature.begin_update_check(source="startup")
        self.feature.finish_update_check(
            {"has_update": True, "version": "999.0.0.1", "release_notes": "новое", "error": None},
            source="startup",
            token=token,
        )
        QApplication.instance().processEvents()
        self.assertIsNone(self.page._update_dialog)
        self.assertTrue(self.page._dialog_wait_timer.isActive())

        self.host.show()

        self.assertIn("новое", self._dialog().browser.toPlainText())
        self.assertFalse(self.page._dialog_wait_timer.isActive())

    def test_check_error_is_shown_as_error(self) -> None:
        self.page._check_service._run_check = lambda *_args, **_kwargs: CheckOutcome(None, "Forgejo: нет ответа")

        self.page._request_check_updates()

        self.assertTrue(_wait(lambda: self.feature.current_update_check_snapshot().phase == "error"))


if __name__ == "__main__":
    unittest.main()
