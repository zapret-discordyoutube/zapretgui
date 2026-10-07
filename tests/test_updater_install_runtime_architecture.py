from __future__ import annotations

"""Устройство установки: страница только показывает, работой владеют сервисы."""

import inspect
import unittest

from updater.download import service as install_service
from updater.ui import page


class UpdaterInstallArchitectureTest(unittest.TestCase):
    def test_page_owns_no_download_dpi_or_installer_work(self) -> None:
        source = inspect.getsource(page)

        for forbidden in ("shutdown_sync", "restart(", "start_supervised_installation", "download_artifact", "QThread"):
            self.assertNotIn(forbidden, source)

    def test_installer_success_requests_exit_on_main_thread(self) -> None:
        source = inspect.getsource(install_service.UpdateInstallService._on_task_done)

        self.assertIn("QTimer.singleShot", source)
        # Без прощального экрана: программа закрывается не по команде человека.
        self.assertIn("request_exit(stop_dpi=False, farewell=False)", source)
        self.assertNotIn("os._exit", inspect.getsource(install_service))


if __name__ == "__main__":
    unittest.main()
