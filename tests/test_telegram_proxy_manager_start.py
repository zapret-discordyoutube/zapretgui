"""Telegram Proxy: два одновременных запуска (автозапуск и кнопка) дают один прокси."""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from telegram_proxy import manager as manager_module


class _SlowRuntime:
    created = 0
    lock = threading.Lock()

    def __init__(self, **kwargs) -> None:
        with _SlowRuntime.lock:
            _SlowRuntime.created += 1
        self.port = kwargs.get("port", 1353)
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> bool:
        time.sleep(0.2)  # привязка к порту не мгновенная
        self._running = True
        return True

    def stop(self) -> None:
        self._running = False


class TelegramProxyManagerStartTests(unittest.TestCase):
    def test_two_starts_at_once_create_one_runtime(self) -> None:
        _SlowRuntime.created = 0
        with patch.object(manager_module, "TelegramProxyRuntime", _SlowRuntime):
            manager = manager_module.TelegramProxyManager()
            results: list[bool] = []
            threads = [threading.Thread(target=lambda: results.append(manager.start_proxy())) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)

        self.assertEqual(_SlowRuntime.created, 1)
        self.assertEqual(sorted(results), [False, True])
        self.assertTrue(manager.is_running)


if __name__ == "__main__":
    unittest.main()
