"""Службы с подписчиками зовут их только в потоке окна.

Подписчики службы настроек программы и координатора проверки обновлений —
страницы. Опубликовать новое состояние может фоновый работник (загрузка
настроек после сохранения, фоновая проверка обновлений), и подписчик,
вызванный прямо из него, тронул бы переключатели из чужого потока: на Windows
это останавливает программу целиком.
"""

from __future__ import annotations

import os
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.ui_thread_marshaller import install_shared_ui_thread_marshaller, shared_ui_thread_marshaller
from core.runtime.program_settings_runtime_service import ProgramSettingsRuntimeService
from core.runtime.ui_thread_delivery import deliver_in_ui_thread
from core.runtime.update_check_coordinator import UpdateCheckCoordinator


def _in_background(callback) -> None:
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            callback()
        except BaseException as exc:  # noqa: BLE001 - тест должен увидеть любую ошибку
            errors.append(exc)

    worker = threading.Thread(target=_run, name="ProgramSettingsLoadWorker")
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "фоновый поток не вернулся"
    assert errors == [], errors


class DeliverInUiThreadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])
        install_shared_ui_thread_marshaller()

    def test_without_marshaller_delivery_is_immediate(self) -> None:
        called: list[int] = []

        deliver_in_ui_thread(None, lambda: called.append(1))
        deliver_in_ui_thread(lambda: None, lambda: called.append(2))

        self.assertEqual(called, [1, 2])

    def test_call_from_window_thread_is_immediate(self) -> None:
        called: list[int] = []

        deliver_in_ui_thread(shared_ui_thread_marshaller, lambda: called.append(threading.get_ident()))

        self.assertEqual(called, [threading.get_ident()])

    def test_call_from_background_waits_for_the_window_thread(self) -> None:
        called: list[int] = []

        _in_background(
            lambda: deliver_in_ui_thread(shared_ui_thread_marshaller, lambda: called.append(threading.get_ident()))
        )
        self.assertEqual(called, [])
        self._app.processEvents()

        self.assertEqual(called, [threading.get_ident()])

    def test_unknown_thread_is_treated_as_foreign(self) -> None:
        class _Broken:
            posted: list = []

            def is_ui_thread(self):
                raise RuntimeError("объект удалён")

            def post(self, action):
                self.posted.append(action)

        broken = _Broken()
        called: list[int] = []

        deliver_in_ui_thread(lambda: broken, lambda: called.append(1))

        self.assertEqual(called, [])
        self.assertEqual(len(broken.posted), 1)


class ProgramSettingsSubscribersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])
        install_shared_ui_thread_marshaller()

    def _service(self) -> ProgramSettingsRuntimeService:
        return ProgramSettingsRuntimeService(ui_thread_marshaller_provider=shared_ui_thread_marshaller)

    def _snapshot(self, service, *, auto_dpi: bool):
        return service._build_snapshot(
            auto_dpi_enabled=auto_dpi,
            gui_autostart_enabled=False,
            tray_close_mode="normal",
            defender_disabled=False,
            max_blocked=False,
            russian_state_media_blocked=False,
        )

    def test_page_is_called_in_window_thread_when_a_worker_publishes(self) -> None:
        service = self._service()
        called_in: list[int] = []

        def _page_callback(_snapshot) -> None:
            called_in.append(threading.get_ident())

        service.subscribe(_page_callback)
        snapshot = self._snapshot(service, auto_dpi=True)

        _in_background(lambda: service.publish_snapshot(snapshot))
        # Работник страницу не тронул.
        self.assertEqual(called_in, [])
        self._app.processEvents()

        self.assertEqual(called_in, [threading.get_ident()])

    def test_page_that_unsubscribed_before_delivery_is_not_called(self) -> None:
        service = self._service()
        called: list[int] = []

        def _page_callback(_snapshot) -> None:
            called.append(1)

        unsubscribe = service.subscribe(_page_callback)
        snapshot = self._snapshot(service, auto_dpi=True)
        _in_background(lambda: service.publish_snapshot(snapshot))

        unsubscribe()
        self._app.processEvents()

        self.assertEqual(called, [])

    def test_publish_from_window_thread_stays_immediate(self) -> None:
        service = self._service()
        called: list[int] = []

        def _page_callback(_snapshot) -> None:
            called.append(1)

        service.subscribe(_page_callback)

        service.publish_snapshot(self._snapshot(service, auto_dpi=True))

        self.assertEqual(called, [1])

    def test_feature_builds_the_service_with_the_shared_marshaller(self) -> None:
        from app.feature_facades.program_settings import ProgramSettingsFeature

        with patch(
            "core.runtime.program_settings_runtime_service.peek_warmed_tray_close_mode",
            return_value=None,
        ):
            service = ProgramSettingsFeature().runtime_service

        self.assertIs(service._ui_thread_marshaller_provider, shared_ui_thread_marshaller)


class UpdateCheckSubscribersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])
        install_shared_ui_thread_marshaller()

    def test_page_is_called_in_window_thread_when_a_background_check_starts(self) -> None:
        coordinator = UpdateCheckCoordinator(ui_thread_marshaller_provider=shared_ui_thread_marshaller)
        called_in: list[int] = []

        def _page_callback(_snapshot) -> None:
            called_in.append(threading.get_ident())

        coordinator.subscribe(_page_callback)

        _in_background(lambda: coordinator.begin(source="startup"))
        self.assertEqual(called_in, [])
        self._app.processEvents()

        self.assertEqual(called_in, [threading.get_ident()])

    def test_feature_builds_the_coordinator_with_the_shared_marshaller(self) -> None:
        from app.feature_facades.updater import UpdaterFeature

        coordinator = UpdaterFeature().check_coordinator

        self.assertIs(coordinator._ui_thread_marshaller_provider, shared_ui_thread_marshaller)


if __name__ == "__main__":
    unittest.main()
