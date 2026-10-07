from __future__ import annotations

"""Выбор источника выпуска: Forgejo главный, зеркала — запасные.

Проверяется то, что пользователь видит: при сбое — понятная ошибка, а не
«обновлений нет»; мёртвое зеркало не мешает живому; медленный Forgejo не
заставляет ждать его тайм-ауты впустую.
"""

import threading
import time
import unittest
from unittest.mock import patch

from updater.release import mirrors
from updater.release.resolver import lookup_latest_release


def _release(version: str, source: str) -> dict:
    return {"version": version, "source": source}


def _fail(message: str):
    def fetch(_channel: str) -> dict:
        raise RuntimeError(message)

    return fetch


class ReleaseSourceOrderTests(unittest.TestCase):
    def test_forgejo_answer_wins_and_mirrors_are_not_asked(self) -> None:
        asked: list[str] = []

        def fetch_mirrors(channel: str) -> dict:
            asked.append(channel)
            return _release("1.0.0.9", "mirror")

        lookup = lookup_latest_release(
            "dev",
            fetch_forgejo=lambda _channel: _release("1.0.0.8", "Forgejo"),
            fetch_mirrors=fetch_mirrors,
        )

        self.assertEqual(lookup.release["source"], "Forgejo")
        self.assertEqual(asked, [])

    def test_mirror_answers_when_forgejo_fails(self) -> None:
        lookup = lookup_latest_release(
            "dev",
            fetch_forgejo=_fail("список выпусков недоступен"),
            fetch_mirrors=lambda _channel: _release("1.0.0.8", "VPS 1 (HTTPS)"),
        )

        self.assertTrue(lookup.ok)
        self.assertEqual(lookup.release["source"], "VPS 1 (HTTPS)")

    def test_total_failure_is_an_error_with_both_reasons(self) -> None:
        """Раньше сбой всех источников показывался как «Обновлений нет»."""
        lookup = lookup_latest_release(
            "dev",
            fetch_forgejo=_fail("forgejo down"),
            fetch_mirrors=_fail("mirrors down"),
        )

        self.assertFalse(lookup.ok)
        self.assertIsNone(lookup.release)
        self.assertIn("forgejo down", lookup.error)
        self.assertIn("mirrors down", lookup.error)

    def test_silent_forgejo_does_not_hold_back_a_ready_mirror_answer(self) -> None:
        """Раньше готовый ответ зеркала ждал весь срок молчащего Forgejo."""
        forgejo_released = threading.Event()
        self.addCleanup(forgejo_released.set)

        def slow_forgejo(_channel: str) -> dict:
            forgejo_released.wait(10.0)
            raise RuntimeError("тайм-аут")

        started = time.monotonic()
        lookup = lookup_latest_release(
            "dev",
            fetch_forgejo=slow_forgejo,
            fetch_mirrors=lambda _channel: _release("1.0.0.8", "mirror"),
            fallback_delay=0.05,
            forgejo_deadline=10.0,
        )

        self.assertTrue(lookup.ok)
        self.assertEqual(lookup.release["source"], "mirror")
        self.assertLess(time.monotonic() - started, 2.0)

    def test_forgejo_inside_its_head_start_wins_and_mirrors_are_not_asked(self) -> None:
        asked: list[str] = []

        def late_forgejo(_channel: str) -> dict:
            time.sleep(0.1)
            return _release("1.0.0.8", "Forgejo")

        def fetch_mirrors(channel: str) -> dict:
            asked.append(channel)
            return _release("1.0.0.7", "mirror")

        lookup = lookup_latest_release(
            "dev",
            fetch_forgejo=late_forgejo,
            fetch_mirrors=fetch_mirrors,
            fallback_delay=2.0,
        )

        self.assertEqual(lookup.release["source"], "Forgejo")
        self.assertEqual(asked, [])

    def test_late_forgejo_still_answers_when_mirrors_fail(self) -> None:
        def late_forgejo(_channel: str) -> dict:
            time.sleep(0.2)
            return _release("1.0.0.8", "Forgejo")

        lookup = lookup_latest_release(
            "dev",
            fetch_forgejo=late_forgejo,
            fetch_mirrors=_fail("mirrors down"),
            fallback_delay=0.01,
        )

        self.assertEqual(lookup.release["source"], "Forgejo")

    def test_silent_everything_is_an_error_after_the_deadlines(self) -> None:
        released = threading.Event()
        self.addCleanup(released.set)

        def silent(_channel: str) -> dict:
            released.wait(10.0)
            raise RuntimeError("тайм-аут")

        started = time.monotonic()
        lookup = lookup_latest_release(
            "dev",
            fetch_forgejo=silent,
            fetch_mirrors=silent,
            fallback_delay=0.01,
            forgejo_deadline=0.1,
            mirrors_deadline=0.1,
        )

        self.assertFalse(lookup.ok)
        self.assertEqual(lookup.error.count("нет ответа за отведённое время"), 2)
        self.assertLess(time.monotonic() - started, 2.0)


class MirrorReleaseTests(unittest.TestCase):
    SERVERS = [
        {"id": "dead", "name": "VPS 1", "host": "dead.example", "https_port": 888, "http_port": 887},
        {"id": "live", "name": "VPS 2", "host": "live.example", "https_port": 888, "http_port": 887},
    ]
    VERSIONS = {
        "dev": {
            "version": "21.1.5.80",
            "file_name": "Zapret2Setup_DEV_21_1_5_80.exe",
            "file_size": 1000,
            "sha256": "cd" * 32,
        }
    }

    def _fake_fetch_versions(self, server: dict):
        if server["id"] == "dead":
            raise mirrors.MirrorReleaseError("HTTPS: не удалось подключиться; HTTP: не удалось подключиться")
        return self.VERSIONS, "HTTPS", f"https://{server['host']}:888", False, 0.05

    def test_dead_first_mirror_does_not_block_the_next_one(self) -> None:
        """Раньше неудачное первое зеркало не блокировалось, и до второго очередь не доходила."""
        with patch.object(mirrors, "fetch_versions", side_effect=self._fake_fetch_versions):
            release = mirrors.fetch_latest_release("dev", servers=self.SERVERS, timeout=2.0)

        self.assertEqual(release["version"], "21.1.5.80")
        self.assertEqual(release["source"], "VPS 2 (HTTPS)")
        self.assertEqual(release["update_url"], "https://live.example:888/download/Zapret2Setup_DEV_21_1_5_80.exe")

    def test_incomplete_mirror_answer_is_rejected_not_completed_from_elsewhere(self) -> None:
        incomplete = {"dev": {"version": "21.1.5.80", "file_name": "Zapret2Setup_DEV_21_1_5_80.exe"}}

        with self.assertRaises(Exception):
            mirrors.release_from_versions(
                incomplete,
                "dev",
                base_url="https://live.example:888",
                verify_ssl=False,
                source="VPS 2 (HTTPS)",
            )

    def test_all_mirrors_down_is_an_error_naming_each(self) -> None:
        with patch.object(mirrors, "fetch_versions", side_effect=mirrors.MirrorReleaseError("нет связи")):
            with self.assertRaisesRegex(mirrors.MirrorReleaseError, "VPS 1.*VPS 2|VPS 2.*VPS 1"):
                mirrors.fetch_latest_release("dev", servers=self.SERVERS, timeout=2.0)

    def test_download_sources_prefer_https_on_every_mirror(self) -> None:
        with patch.object(mirrors, "VPS_SERVERS", self.SERVERS):
            sources = mirrors.download_sources("file.exe")

        self.assertEqual(
            [url for url, _verify in sources],
            [
                "https://dead.example:888/download/file.exe",
                "https://live.example:888/download/file.exe",
                "http://dead.example:887/download/file.exe",
                "http://live.example:887/download/file.exe",
            ],
        )


if __name__ == "__main__":
    unittest.main()
