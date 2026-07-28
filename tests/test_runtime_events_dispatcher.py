from __future__ import annotations

import dataclasses
import unittest
import weakref

from PyQt6.QtWidgets import QApplication

from app.feature_facades.runtime_parts import RuntimeEvents


class RuntimeEventsDispatcherTests(unittest.TestCase):
    """Диспетчер runtime-событий обязан подключаться без ошибок.

    Регрессия: RuntimeEvents объявили `dataclass(slots=True)` без
    `weakref_slot`, а его методы подключаются к сигналам через
    QueuedConnection. PyQt берёт на приёмник слабую ссылку, для объекта без
    `__weakref__` это невозможно, и connect падал SystemError. В логе это
    выглядело как «Ошибка startup-шага launch runtime», после чего DPI при
    старте не запускался вовсе.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_events_object_supports_weak_references(self) -> None:
        events = RuntimeEvents(runtime_service=None)

        self.assertIsNotNone(weakref.ref(events))

    def test_dataclass_keeps_slots_and_weakref_slot(self) -> None:
        params = RuntimeEvents.__dataclass_params__

        self.assertTrue(getattr(params, "slots", False))
        self.assertTrue(getattr(params, "weakref_slot", False))
        self.assertIn("__weakref__", RuntimeEvents.__slots__)

    def test_ensure_dispatcher_connects_queued_signals(self) -> None:
        events = RuntimeEvents(runtime_service=None)

        dispatcher = events.ensure_dispatcher()

        self.assertIsNotNone(dispatcher)
        self.assertIs(events.ensure_dispatcher(), dispatcher)

    def test_dispatched_signal_reaches_handler(self) -> None:
        events = RuntimeEvents(runtime_service=None)
        seen: list[str] = []
        events.ui_port = _RecordingUiPort(seen)

        events.publish_status("работает")
        QApplication.processEvents()

        self.assertEqual(seen, ["работает"])


@dataclasses.dataclass
class _RecordingUiPort:
    seen: list[str]

    def set_status(self, text: str) -> None:
        self.seen.append(text)


if __name__ == "__main__":
    unittest.main()
