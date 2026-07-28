from __future__ import annotations

import sys
import threading
import unittest
from unittest.mock import patch

from main import entry


class ImportWarmupTests(unittest.TestCase):
    """Тяжёлые импорты греются в фоне, чтобы не рвать кадр посреди работы."""

    def test_asyncio_is_warmed_up(self) -> None:
        # Первое обращение к Telegram Proxy тянет asyncio вместе с
        # asyncio.windows_events — в логе это давало рывок на ~64 мс.
        self.assertIn("asyncio", entry.IMPORT_WARMUP_MODULES)

    def test_failing_import_does_not_stop_the_rest(self) -> None:
        warmed = entry.warm_up_modules(("zapret_no_such_module_xyz", "asyncio"))

        self.assertEqual(warmed, ("asyncio",))
        self.assertIn("asyncio", sys.modules)

    def test_warmup_runs_in_background_daemon_thread(self) -> None:
        started: list[threading.Thread] = []
        real_thread = threading.Thread

        def _record(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            started.append(thread)
            return thread

        with patch("threading.Thread", side_effect=_record):
            entry.start_qtawesome_warmup()

        self.assertEqual(len(started), 1)
        thread = started[0]
        self.assertTrue(thread.daemon)
        self.assertEqual(thread.name, "import-warmup")
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())

    def test_warmup_thread_imports_every_listed_module(self) -> None:
        with patch.object(entry, "IMPORT_WARMUP_MODULES", ("json", "base64")):
            entry.start_qtawesome_warmup()
            for thread in threading.enumerate():
                if thread.name == "import-warmup":
                    thread.join(timeout=10)

        self.assertIn("json", sys.modules)
        self.assertIn("base64", sys.modules)


if __name__ == "__main__":
    unittest.main()
