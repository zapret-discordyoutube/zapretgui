from __future__ import annotations

"""Слушатель сигнала сервера о новой версии («длинный запрос»)."""

import unittest
from unittest.mock import patch

from updater.release import watch
from updater.release.watch import ReleaseWatcher, WaitEndpoint

FORGEJO = WaitEndpoint("Forgejo", "https://git.example/api/zapret/wait", True)
MIRROR = WaitEndpoint("Зеркало", "https://192.0.2.1:888/api/wait", False)


class _Response:
    def __init__(self, payload, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise OSError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Harness:
    """Сервер и часы слушателя: ответы идут по сценарию, паузы не ждут."""

    def __init__(self, test: unittest.TestCase, script: list, *, enabled=lambda: True) -> None:
        self.script = list(script)
        self.requests: list[tuple[str, bool]] = []
        self.pauses: list[float] = []
        self.released: list[str] = []
        self.queued: list[str] = []
        self.now = 0.0
        self.watcher = ReleaseWatcher(
            channel="dev",
            current_version="21.1.7.118",
            on_release=self.released.append,
            on_queued=self.queued.append,
            is_enabled=enabled,
            endpoints=lambda: (FORGEJO, MIRROR),
            session_factory=lambda: self,
            clock=lambda: self.now,
        )
        patcher = patch.object(self.watcher, "_pause", side_effect=self._pause)
        patcher.start()
        test.addCleanup(patcher.stop)
        log_patcher = patch.object(watch, "log")
        log_patcher.start()
        test.addCleanup(log_patcher.stop)

    # Сессия requests.
    def get(self, url: str, *, timeout, verify, headers):
        self.requests.append((url, verify))
        if not self.script:
            self.watcher.stop()
            raise OSError("сценарий кончился")
        step = self.script.pop(0)
        probe = "hold=0" in url
        took, answer = step if isinstance(step, tuple) else (0.2 if probe else 300.0, step)
        self.now += took
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def close(self) -> None:
        pass

    def _pause(self, seconds: float) -> bool:
        self.pauses.append(float(seconds))
        return self.watcher._stop.is_set()

    def run(self) -> None:
        self.watcher.run()


def _quiet(version: str = "21.1.7.118") -> _Response:
    return _Response({"channel": "dev", "version": version, "changed": False})


def _news(version: str) -> _Response:
    return _Response({"channel": "dev", "version": version, "changed": True})


def _queued(version: str) -> _Response:
    return _Response({"channel": "dev", "version": version, "changed": False, "queued": True})


class ReleaseWatcherTests(unittest.TestCase):
    def test_first_question_is_a_probe_then_questions_wait(self) -> None:
        harness = _Harness(self, [_quiet(), _quiet(), _quiet()])

        harness.run()

        urls = [url for url, _verify in harness.requests]
        # Проба сразу показывает, что служба на месте; дальше — долгие вопросы.
        self.assertIn("hold=0", urls[0])
        self.assertNotIn("hold=", urls[1])
        self.assertNotIn("hold=", urls[2])
        self.assertIn("known=21.1.7.118", urls[0])
        self.assertIn("channel=dev", urls[0])

    def test_permission_is_reported_once_and_version_becomes_known(self) -> None:
        harness = _Harness(self, [_quiet(), _news("21.1.7.119"), _quiet("21.1.7.119")])

        harness.run()

        self.assertEqual(harness.released, ["21.1.7.119"])
        # Следующий вопрос — уже про новую версию: о ней разрешение больше не придёт.
        self.assertIn("known=21.1.7.119", harness.requests[2][0])

    def test_permission_may_come_right_in_the_probe(self) -> None:
        # Программу запустили, когда её очередь уже прошла.
        harness = _Harness(self, [_news("21.1.7.119")])

        harness.run()

        self.assertEqual(harness.released, ["21.1.7.119"])

    def test_queued_version_is_reported_once_and_is_not_a_permission(self) -> None:
        harness = _Harness(self, [_queued("21.1.7.119"), _queued("21.1.7.119"), _news("21.1.7.119")])

        harness.run()

        self.assertEqual(harness.queued, ["21.1.7.119"])
        self.assertEqual(harness.released, ["21.1.7.119"])
        self.assertEqual(harness.watcher.queued_version, "")
        # В очереди программа по-прежнему спрашивает про свою версию.
        self.assertIn("known=21.1.7.118", harness.requests[2][0])

    def test_watcher_tells_whether_the_server_queue_is_reachable(self) -> None:
        harness = _Harness(self, [_quiet()])
        self.assertIsNone(harness.watcher.reachable)

        harness.run()

        self.assertIs(harness.watcher.reachable, True)
        self.assertIs(harness.watcher.wait_until_probed(0), True)

    def test_reachable_after_the_probe(self) -> None:
        seen: list = []
        harness = _Harness(self, [_quiet(), _quiet()])
        original = harness.get

        def get(url, **kwargs):
            seen.append(harness.watcher.reachable)
            return original(url, **kwargs)

        harness.get = get
        harness.run()

        self.assertEqual(seen[:2], [None, True])

    def test_quiet_answer_after_full_hold_is_asked_again_without_pause(self) -> None:
        harness = _Harness(self, [_quiet(), _quiet(), _quiet()])

        harness.run()

        self.assertEqual(harness.released, [])
        self.assertNotIn(watch.QUICK_ANSWER_PAUSE_SECONDS, harness.pauses)

    def test_older_or_same_version_is_not_news(self) -> None:
        # Отставшее зеркало: «можно» с версией не новее известной.
        harness = _Harness(self, [_quiet(), _news("21.1.7.118"), _news("21.1.7.90")])

        harness.run()

        self.assertEqual(harness.released, [])

    def test_broken_answers_do_not_turn_into_a_request_storm(self) -> None:
        # Чужой прокси или старый сервер отвечает мгновенно и без разрешения.
        harness = _Harness(
            self,
            [_quiet(), (0.1, _quiet()), (0.1, _news("чепуха")), (0.1, _queued("21.1.7.119")), (0.1, _Response({}))],
        )

        harness.run()

        self.assertEqual(harness.released, [])
        self.assertEqual(harness.pauses[:4], [watch.QUICK_ANSWER_PAUSE_SECONDS] * 4)

    def test_failed_source_hands_over_to_the_next_one_and_returns(self) -> None:
        harness = _Harness(
            self,
            [OSError("Forgejo недоступен"), _quiet(), _news("21.1.7.119"), _quiet("21.1.7.119")],
        )

        harness.run()

        urls = [url for url, _verify in harness.requests]
        self.assertEqual(harness.released, ["21.1.7.119"])
        self.assertTrue(urls[0].startswith(FORGEJO.url))
        # На зеркале — проба и один полный вопрос с ожиданием…
        self.assertTrue(urls[1].startswith(MIRROR.url) and "hold=0" in urls[1])
        self.assertTrue(urls[2].startswith(MIRROR.url) and "hold=" not in urls[2])
        # …после чего слушатель снова пробует Forgejo.
        self.assertTrue(urls[3].startswith(FORGEJO.url) and "hold=0" in urls[3])
        # Сертификат зеркала самоподписанный, у Forgejo — проверяется.
        self.assertEqual([verify for _url, verify in harness.requests[:2]], [True, False])
        self.assertEqual(harness.pauses[0], watch.NEXT_SOURCE_PAUSE_SECONDS)

    def test_pauses_grow_when_nobody_answers(self) -> None:
        harness = _Harness(self, [OSError("нет сети")] * 6)

        harness.run()

        rounds = [pause for pause in harness.pauses if pause != watch.NEXT_SOURCE_PAUSE_SECONDS]
        self.assertEqual(rounds[:3], [60.0, 120.0, 240.0])
        self.assertIs(harness.watcher.reachable, False)

    def test_http_error_counts_as_failure(self) -> None:
        # На сервере ещё нет службы ожидания: 404.
        harness = _Harness(self, [_Response({}, status=404), _news("21.1.7.119")])

        harness.run()

        self.assertEqual(harness.released, ["21.1.7.119"])
        self.assertTrue(harness.requests[1][0].startswith(MIRROR.url))

    def test_disabled_auto_update_holds_no_connection(self) -> None:
        states = iter([False, False, True])
        harness = _Harness(self, [_news("21.1.7.119")], enabled=lambda: next(states, True))

        harness.run()

        self.assertEqual(harness.pauses[:2], [watch.DISABLED_RECHECK_SECONDS] * 2)
        self.assertEqual(harness.released, ["21.1.7.119"])

    def test_failing_listener_of_the_signal_does_not_stop_the_watcher(self) -> None:
        harness = _Harness(self, [_news("21.1.7.119"), _news("21.1.7.120")])
        seen: list[str] = []

        def on_release(version: str) -> None:
            seen.append(version)
            raise RuntimeError("окно уже закрыто")

        harness.watcher._on_release = on_release

        harness.run()

        self.assertEqual(seen, ["21.1.7.119", "21.1.7.120"])


class WaitEndpointsTests(unittest.TestCase):
    def test_forgejo_goes_first_then_mirrors(self) -> None:
        servers = [
            {"name": "Первое", "host": "192.0.2.1", "https_port": 888, "http_port": 887},
            {"name": "Второе", "host": "192.0.2.2", "https_port": 888, "http_port": 887},
        ]
        with patch("updater.release.mirrors.mirror_servers", return_value=servers):
            endpoints = watch.wait_endpoints()

        self.assertEqual(
            [(item.name, item.url, item.verify_ssl) for item in endpoints],
            [
                ("Forgejo", "https://git.zapret.moe/api/zapret/wait", True),
                ("Первое", "https://192.0.2.1:888/api/wait", False),
                ("Второе", "https://192.0.2.2:888/api/wait", False),
            ],
        )


if __name__ == "__main__":
    unittest.main()
