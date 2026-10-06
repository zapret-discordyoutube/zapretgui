"""Общие объекты Qt живут в потоке окна, кто бы их ни создал.

Шина событий пресетов и менеджер Telegram Proxy создаются по первому
требованию, и первым может прийти фоновый поток. QObject принадлежит потоку,
который его создал: сигналы такого объекта уходили бы в поток без цикла
событий либо выполнялись бы прямо в фоновом потоке и оттуда трогали окно.
"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from app.ui_thread_marshaller import ensure_window_thread_affinity


class _Bus(QObject):
    changed = pyqtSignal(str)


def _in_background(callback):
    result: list = []
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            result.append(callback())
        except BaseException as exc:  # noqa: BLE001 - тест должен увидеть любую ошибку
            errors.append(exc)

    worker = threading.Thread(target=_run, name="StartupQueue-presets")
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "фоновый поток не вернулся"
    assert errors == [], errors
    return result[0]


class WindowThreadAffinityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_object_created_in_window_thread_is_left_alone(self) -> None:
        bus = _Bus()

        self.assertFalse(ensure_window_thread_affinity(bus, "Шина"))
        self.assertIs(bus.thread(), self._app.thread())

    def test_object_created_in_background_is_moved_to_window_thread(self) -> None:
        def _create():
            bus = _Bus()
            moved = ensure_window_thread_affinity(bus, "Шина")
            return bus, moved

        bus, moved = _in_background(_create)

        self.assertTrue(moved)
        self.assertIs(bus.thread(), self._app.thread())

    def test_signal_of_adopted_object_reaches_a_plain_callback_in_window_thread(self) -> None:
        """Без переноса обычная функция-слот выполнилась бы в фоновом потоке или не выполнилась вовсе."""
        bus = _in_background(lambda: (lambda created: (ensure_window_thread_affinity(created, "Шина"), created)[1])(_Bus()))
        received_in: list[int] = []
        bus.changed.connect(lambda _name: received_in.append(threading.get_ident()))

        _in_background(lambda: bus.changed.emit("Default.txt"))
        self.assertEqual(received_in, [])
        self._app.processEvents()

        self.assertEqual(received_in, [threading.get_ident()])

    def test_preset_signal_buses_live_in_window_thread_even_when_built_by_a_worker(self) -> None:
        from core.paths import AppPaths
        from presets.services_bundle import create_preset_services

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)

        services = _in_background(lambda: create_preset_services(AppPaths(user_root=root, local_root=root)))

        self.assertIs(services.preset_store_winws2.thread(), self._app.thread())
        self.assertIs(services.preset_store_winws1.thread(), self._app.thread())

    def test_preset_services_are_built_once_for_two_threads_at_a_time(self) -> None:
        from app.feature_facades.presets import PresetsFeature

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        from core.paths import AppPaths

        feature = PresetsFeature.create(AppPaths(user_root=root, local_root=root))
        built: list[object] = []
        gate = threading.Barrier(2)

        def _get():
            gate.wait(5)
            built.append(feature._preset_services())

        workers = [threading.Thread(target=_get) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)

        self.assertEqual(len(built), 2)
        self.assertIs(built[0], built[1])

    def test_telegram_proxy_manager_lives_in_window_thread_even_when_first_asked_by_a_worker(self) -> None:
        from telegram_proxy import manager as manager_module

        with patch.object(manager_module, "_shared_proxy_manager", None):
            manager = _in_background(manager_module.get_proxy_manager)
            self.addCleanup(manager.deleteLater)

            self.assertIs(manager.thread(), self._app.thread())
            self.assertIs(manager_module.get_proxy_manager(), manager)


if __name__ == "__main__":
    unittest.main()
