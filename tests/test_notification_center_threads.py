"""Центр уведомлений можно вызывать из любого потока.

Показ уведомления создаёт элементы окна и запускает таймеры Qt — это разрешено
только в потоке окна. В 21.1.7.19 фоновая задача запуска вызвала notify()
напрямую, плашка начала создаваться в фоновом потоке, и программа встала
целиком на «Ждём подтверждение процесса winws». Теперь вызов из чужого потока
только ставит уведомление в очередь потока окна.
"""

from __future__ import annotations

import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app_notifications import advisory_notification
from ui import window_notification_center as center_module
from ui.window_notification_center import WindowNotificationCenter


def _payload(queue: str = "immediate") -> dict:
    return advisory_notification(
        level="info",
        title="Встроенные пресеты обновлены",
        content="Пресет заменён новой версией встроенного пресета.",
        source="presets.builtin_update",
        presentation="infobar",
        queue=queue,
        dedupe_key="presets.builtin_update",
    )


class NotificationCenterThreadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _center(self, *, startup_ready: bool = True) -> WindowNotificationCenter:
        center = WindowNotificationCenter(
            None,
            startup_state=SimpleNamespace(post_init_ready=startup_ready, background_init_started=startup_ready),
            runtime_actions=Mock(),
            create_open_url_worker=Mock(),
            create_notification_action_worker=Mock(),
            show_tray_notification=Mock(),
            show_page=Mock(),
            is_window_visible=lambda: True,
            is_window_minimized=lambda: False,
        )
        self.addCleanup(center.deleteLater)
        return center

    def _call_from_background(self, callback) -> None:
        errors: list[BaseException] = []

        def _run() -> None:
            try:
                callback()
            except BaseException as exc:  # noqa: BLE001 - тест должен увидеть любую ошибку
                errors.append(exc)

        worker = threading.Thread(target=_run)
        worker.start()
        worker.join(5)
        self.assertFalse(worker.is_alive(), "вызов из фонового потока не вернулся")
        self.assertEqual(errors, [])

    def test_notice_from_window_thread_is_shown_at_once(self) -> None:
        center = self._center()

        with patch.object(center, "_present_notification") as present:
            center.notify(_payload())

        present.assert_called_once()

    def test_notice_from_background_thread_is_shown_only_in_window_thread(self) -> None:
        center = self._center()
        shown_in: list[int] = []

        with patch.object(
            center,
            "_present_notification",
            side_effect=lambda _payload: shown_in.append(threading.get_ident()),
        ):
            self._call_from_background(lambda: center.notify(_payload()))
            # Фоновый поток ничего не показал сам: показ ждёт очереди окна.
            self.assertEqual(shown_in, [])

            self._app.processEvents()

        self.assertEqual(shown_in, [threading.get_ident()])

    def test_startup_queue_is_touched_only_in_window_thread(self) -> None:
        center = self._center(startup_ready=False)
        started_in: list[int] = []
        real_schedule = center.schedule_startup_notification_queue

        def _schedule(delay_ms: int = 0) -> None:
            started_in.append(threading.get_ident())
            real_schedule(delay_ms)

        with patch.object(center, "schedule_startup_notification_queue", side_effect=_schedule):
            self._call_from_background(lambda: center.notify(_payload("startup")))
            self.assertEqual(center._startup_notification_queue, [])
            self.assertEqual(started_in, [])

            self._app.processEvents()

        # Таймер очереди запускается из потока окна: из чужого потока Qt этого не разрешает.
        self.assertEqual(len(center._startup_notification_queue), 1)
        self.assertTrue(started_in)
        self.assertEqual(set(started_in), {threading.get_ident()})
        center._startup_notification_timer.stop()

    def test_background_notice_is_dropped_when_it_cannot_reach_the_window_thread(self) -> None:
        center = self._center()

        with (
            patch.object(center, "_present_notification") as present,
            patch.object(center_module.QMetaObject, "invokeMethod", side_effect=RuntimeError("окно закрыто")),
            patch.object(center_module, "log") as log,
        ):
            self._call_from_background(lambda: center.notify(_payload()))
            self._app.processEvents()

        # Лучше потерять уведомление, чем показать его из фонового потока.
        present.assert_not_called()
        self.assertTrue(any("не доставлено" in str(call.args[0]) for call in log.call_args_list))

    def test_post_startup_tasks_get_the_same_safe_entry(self) -> None:
        """Фоновым задачам запуска выдаётся notify центра — он обязан быть безопасным сам."""
        import inspect

        source = inspect.getsource(WindowNotificationCenter.notify)
        self.assertIn("_called_from_own_thread", source)
        self.assertIn("notify_threadsafe", source)


if __name__ == "__main__":
    unittest.main()
