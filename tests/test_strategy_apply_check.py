"""Проверка сайта после «Применить» — уже через пресет пользователя."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from blockcheck import strategy_apply_check as check
from blockcheck.strategy_search import verdict as rules
from blockcheck.strategy_search.verdict import ProbeOutcome
from utils.socket_cancel import SocketCancel

OK = ProbeOutcome(rules.PROBE_OK, "HTTP 200", 50.0, body_size=60_000)
RESET = ProbeOutcome(rules.PROBE_BLOCKED, "соединение сброшено", 30.0, "reset")


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> bool:
        self.now += seconds
        return True


class WaitTests(unittest.TestCase):
    def test_waits_for_zapret_then_settles(self) -> None:
        clock = _Clock()
        states = iter([False, False, True])
        self.assertTrue(
            check.wait_until_running(lambda: next(states), restarting=False, sleep=clock.sleep, cancelled=lambda: False, clock=clock)
        )
        self.assertAlmostEqual(clock.now, 2 * check.POLL_SECONDS + check.READY_SETTLE_SECONDS)

    def test_zapret_that_never_starts_gives_up(self) -> None:
        clock = _Clock()
        self.assertFalse(
            check.wait_until_running(lambda: False, restarting=False, sleep=clock.sleep, cancelled=lambda: False, clock=clock)
        )

    def test_restart_waits_for_the_preset_to_be_reread(self) -> None:
        clock = _Clock()
        check.wait_until_running(lambda: True, restarting=True, sleep=clock.sleep, cancelled=lambda: False, clock=clock)
        self.assertGreaterEqual(clock.now, check.RESTART_SETTLE_SECONDS)


class CheckSiteTests(unittest.TestCase):
    def _run(self, outcomes_by_call, *, running=True, visit=None):
        from blockcheck import browser_check

        calls = iter(outcomes_by_call)
        visit = visit or browser_check.BrowserVisit(browser_check.BROWSER_MISSING)
        with (
            patch("blockcheck.strategy_search.probes.resolve_target_addresses", return_value=(["1.1.1.1", "1.1.2.1"], "")),
            patch("blockcheck.strategy_search.probes.probe_https_addresses", side_effect=lambda host, addresses, cancel: next(calls)),
            patch.object(check, "wait_until_running", return_value=running),
        ):
            cancel = SocketCancel()
            cancel.sleep = lambda seconds: True
            return check.run_apply_check(
                "discord.com", is_running=lambda: True, restarting=False, cancel=cancel, open_browser=lambda host, **_k: visit
            )

    def test_site_open_through_preset_is_ok(self) -> None:
        self.assertEqual(self._run([[OK, OK]]).code, check.CHECK_OK)

    def test_failure_is_rechecked_once_before_reporting(self) -> None:
        self.assertEqual(self._run([[RESET, OK], [OK, OK]]).code, check.CHECK_OK)
        failed = self._run([[RESET, OK], [RESET, RESET]])
        self.assertEqual(failed.code, check.CHECK_FAILED)
        self.assertIn("сброшено", failed.text)

    def test_real_browser_confirms_what_the_probe_saw(self) -> None:
        from blockcheck import browser_check

        opened = self._run([[OK, OK]], visit=browser_check.BrowserVisit(browser_check.BROWSER_OK, browser="msedge.exe"))
        self.assertEqual(opened.code, check.CHECK_OK)
        self.assertIn("msedge.exe", opened.text)
        refused = browser_check.BrowserVisit(browser_check.BROWSER_FAILED, "ERR_CONNECTION_RESET", "соединение сброшено", "msedge.exe")
        failed = self._run([[OK, OK]], visit=refused)
        self.assertEqual(failed.code, check.CHECK_BROWSER_FAILED)
        self.assertIn("сброшено", failed.text)
        # Проба не открыла сайт — браузер не нужен: итог уже ясен.
        self.assertEqual(self._run([[RESET], [RESET]], visit=refused).code, check.CHECK_FAILED)

    def test_silent_browser_is_retried_and_then_said_to_be_unchecked(self) -> None:
        from blockcheck import browser_check

        silent = browser_check.BrowserVisit(browser_check.BROWSER_NO_ANSWER, browser="msedge.exe", details="код выхода 0; вывод пуст")
        opened = browser_check.BrowserVisit(browser_check.BROWSER_OK, browser="msedge.exe")
        answers = iter([silent, opened])
        result = self._run_with_browser(lambda host, **_k: next(answers))
        self.assertEqual(result.code, check.CHECK_OK)
        self.assertIn("открылся и в браузере", result.text)
        # Молчит и второй раз — не «работает» и не «не открылся»: проверить не вышло, с тем, что вернул браузер.
        result = self._run_with_browser(lambda host, **_k: silent)
        self.assertEqual(result.code, check.CHECK_BROWSER_UNKNOWN)
        self.assertIn("код выхода 0", result.text)
        kind, _title, body = check.apply_check_message("discord.com", result.code, result.text)
        self.assertEqual(kind, "info")
        self.assertIn("сбой проверки", body)

    def test_every_installed_browser_is_tried_while_they_stay_silent(self) -> None:
        from blockcheck import browser_check

        tried: list[str] = []

        def visit(host, *, cancelled, browser):
            tried.append(browser)
            if browser.endswith("chrome.exe"):
                return browser_check.BrowserVisit(browser_check.BROWSER_OK, browser="chrome.exe")
            return browser_check.BrowserVisit(browser_check.BROWSER_NO_ANSWER, browser="msedge.exe", details="код выхода 1")

        with (
            patch.object(browser_check, "find_browsers", return_value=[r"C:\Edge\msedge.exe", r"C:\Chrome\chrome.exe"]),
            patch.object(browser_check, "open_in_browser", side_effect=visit),
        ):
            visits = check._browser_visits("discord.com", cancel=SocketCancel())
        self.assertEqual(tried, [r"C:\Edge\msedge.exe", r"C:\Edge\msedge.exe", r"C:\Chrome\chrome.exe"])
        self.assertTrue(visits[-1].ok)

    def _run_with_browser(self, open_browser):
        with (
            patch("blockcheck.strategy_search.probes.resolve_target_addresses", return_value=(["1.1.1.1", "1.1.2.1"], "")),
            patch("blockcheck.strategy_search.probes.probe_https_addresses", return_value=[OK, OK]),
            patch.object(check, "wait_until_running", return_value=True),
        ):
            cancel = SocketCancel()
            cancel.sleep = lambda seconds: True
            return check.run_apply_check("discord.com", is_running=lambda: True, restarting=False, cancel=cancel, open_browser=open_browser)

    def test_zapret_not_started_is_skipped(self) -> None:
        self.assertEqual(self._run([], running=False).code, check.CHECK_SKIPPED)

    def test_short_answer_is_topped_up_with_volume(self) -> None:
        short = ProbeOutcome(rules.PROBE_OK, "HTTP 200", 50.0, body_size=500)
        cut = ProbeOutcome(rules.PROBE_BLOCKED, "соединение замирает после ~16 КБ", 50.0, "reset", 500, rules.VOLUME_CUT)
        with patch("blockcheck.strategy_search.probes.probe_volume", return_value=cut):
            result = self._run([[short], [short]])
        self.assertEqual(result.code, check.CHECK_FAILED)
        self.assertIn("16 КБ", result.text)


class CompanionTests(unittest.TestCase):
    """Сервис из нескольких имён: после «Применить» открываться должны все."""

    def _run(self, answers: dict):
        asked: list[str] = []

        def probe(host, addresses, cancel):
            asked.append(host)
            return answers[host]

        with (
            patch("blockcheck.strategy_search.probes.resolve_target_addresses", return_value=(["1.1.1.1", "1.1.2.1"], "")),
            patch("blockcheck.strategy_search.probes.probe_https_addresses", side_effect=probe),
        ):
            return check.check_site("www.youtube.com", cancel=SocketCancel(), companions=("rr1---sn-x.googlevideo.com", "i.ytimg.com")), asked

    def test_page_open_but_video_server_closed_is_a_failure_naming_it(self) -> None:
        result, asked = self._run({"www.youtube.com": [OK], "rr1---sn-x.googlevideo.com": [RESET], "i.ytimg.com": [OK]})

        self.assertEqual(result.code, check.CHECK_FAILED)
        self.assertTrue(result.text.startswith("rr1---sn-x.googlevideo.com:"))
        # Дальше первого закрытого имени не идём.
        self.assertEqual(asked, ["www.youtube.com", "rr1---sn-x.googlevideo.com"])

    def test_every_name_open_is_ok(self) -> None:
        result, asked = self._run({"www.youtube.com": [OK], "rr1---sn-x.googlevideo.com": [OK], "i.ytimg.com": [OK]})

        self.assertEqual(result.code, check.CHECK_OK)
        self.assertEqual(len(asked), 3)

    def test_name_that_cannot_be_resolved_is_skipped(self) -> None:
        with patch("blockcheck.strategy_search.probes.resolve_target_addresses", return_value=([], "нет адреса")):
            self.assertEqual(check.check_companions(("a.example",), cancel=SocketCancel()).code, check.CHECK_OK)


class MessageTests(unittest.TestCase):
    def test_messages(self) -> None:
        kind, _title, body = check.apply_check_message("discord.com", check.CHECK_OK, "")
        self.assertEqual(kind, "success")
        self.assertIn("discord.com", body)
        kind, _title, body = check.apply_check_message("discord.com", check.CHECK_FAILED, "соединение сброшено")
        self.assertEqual(kind, "warning")
        self.assertIn("профиль", body)
        self.assertIsNone(check.apply_check_message("discord.com", check.CHECK_SKIPPED, "Zapret не запустился"))
        kind, title, _body = check.apply_check_message("discord.com", check.CHECK_BROWSER_FAILED, "msedge.exe: сброшено")
        self.assertEqual((kind, title), ("warning", "В браузере сайт не открылся"))
