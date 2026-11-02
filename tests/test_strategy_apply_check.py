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
    def _run(self, outcomes_by_call, *, running=True, visit=None, retry=None):
        from blockcheck import browser_check

        calls = iter(outcomes_by_call)
        visit = visit or browser_check.BrowserVisit(browser_check.BROWSER_MISSING)
        self.retries: list[tuple[str, ...]] = []

        def open_browser(host, *, cancelled, extra=(), net_log=False):
            if not extra:
                return visit
            self.retries.append(tuple(extra))
            return retry(extra) if retry else browser_check.BrowserVisit(browser_check.BROWSER_FAILED, "", "сброшено", "msedge.exe")

        with (
            patch("blockcheck.strategy_search.probes.resolve_target_addresses", return_value=(["1.1.1.1", "1.1.2.1"], "")),
            patch("blockcheck.strategy_search.probes.probe_https_addresses", side_effect=lambda host, addresses, cancel: next(calls)),
            patch.object(check, "wait_until_running", return_value=running),
        ):
            cancel = SocketCancel()
            cancel.sleep = lambda seconds: True
            return check.run_apply_check("discord.com", is_running=lambda: True, restarting=False, cancel=cancel, open_browser=open_browser)

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
        self.assertEqual((opened.code, opened.browser), (check.CHECK_OK, "Edge"))
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
        self.assertEqual((result.code, result.browser), (check.CHECK_OK, "Edge"))
        # Молчит и второй раз — не «работает» и не «не открылся»: проверить не вышло, с тем, что вернул браузер.
        result = self._run_with_browser(lambda host, **_k: silent)
        self.assertEqual(result.code, check.CHECK_BROWSER_UNKNOWN)
        kind, _title, body = check.apply_check_message("discord.com", result)
        self.assertEqual(kind, "info")
        self.assertIn("о сайте это ничего не говорит", body)
        self.assertIn("Что сделать", body)

    def test_every_installed_browser_is_tried_while_they_stay_silent(self) -> None:
        from blockcheck import browser_check

        tried: list[str] = []

        def visit(host, *, cancelled, browser, net_log):
            tried.append(browser)
            if browser.endswith("chrome.exe"):
                return browser_check.BrowserVisit(browser_check.BROWSER_OK, browser="chrome.exe")
            return browser_check.BrowserVisit(browser_check.BROWSER_NO_ANSWER, browser="msedge.exe", details="код выхода 1")

        with (
            patch.object(browser_check, "find_browsers", return_value=[r"C:\Edge\msedge.exe", r"C:\Chrome\chrome.exe"]),
            patch.object(browser_check, "open_in_browser", side_effect=visit),
        ):
            visits = check._browser_visits("discord.com", cancel=SocketCancel(), open_browser=None)
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

    def test_browser_failure_is_followed_by_a_search_for_the_cause(self) -> None:
        # Как www.castingcall.club на win10: Edge прятал имя (ECH); без ECH сайт открылся — причина доказана.
        from blockcheck import browser_check
        from blockcheck import browser_diagnosis as bd
        from blockcheck.browser_netlog import HUNG, Connection, NetFacts

        net = NetFacts((Connection("discord.com", "1.1.1.1", True, HUNG),))
        failed = browser_check.BrowserVisit(browser_check.BROWSER_TIMEOUT, "", "страница не загрузилась за 20 с", "msedge.exe", net=net)
        opened = browser_check.BrowserVisit(browser_check.BROWSER_OK, browser="msedge.exe")
        result = self._run([[OK, OK]], visit=failed, retry=lambda extra: opened if extra == ("--host-resolver-rules=MAP discord.com 1.1.1.1",) else failed)
        self.assertEqual((result.code, result.fixes, result.browser), (check.CHECK_BROWSER_FIXED, (bd.FIX_ECH,), "Edge"))
        self.assertEqual(self.retries, [("--host-resolver-rules=MAP discord.com 1.1.1.1",)])
        kind, _title, body = check.apply_check_message("discord.com", result)
        self.assertEqual(kind, "warning")
        self.assertIn("без ECH", body)
        self.assertIn("Что сделать: выключите в своём браузере ECH", body)
        self.assertIn("network.dns.echconfig.enabled", body)
        # Ничего не помогло — так и сказано, с ошибкой браузера и с тем, что пробовали.
        result = self._run([[OK, OK]], visit=failed)
        self.assertEqual(result.code, check.CHECK_BROWSER_FAILED)
        _kind, _title, body = check.apply_check_message("discord.com", result)
        self.assertIn("без ECH", body)
        self.assertIn("Что сделать", body)

    def test_silent_browser_whose_log_shows_failed_site_connections_is_diagnosed(self) -> None:
        # Так Edge 156 на win10 с www.castingcall.club: ни страницы, ни ошибки, а в журнале — три зависших соединения с ECH.
        # Это не «проверить не вышло», а неудача с фактами: повторять не нужно, нужно искать причину.
        from blockcheck import browser_check
        from blockcheck import browser_diagnosis as bd
        from blockcheck.browser_netlog import HUNG, Connection, NetFacts

        net = NetFacts(tuple(Connection("discord.com", "1.1.1.1", True, HUNG) for _ in range(3)))
        silent = browser_check.BrowserVisit(browser_check.BROWSER_NO_ANSWER, "", "браузер не ответил", "msedge.exe", net=net)
        opened = browser_check.BrowserVisit(browser_check.BROWSER_OK, browser="msedge.exe")
        result = self._run([[OK, OK]], visit=silent, retry=lambda extra: opened if extra == ("--host-resolver-rules=MAP discord.com 1.1.1.1",) else silent)
        self.assertEqual((result.code, result.fixes, result.text), (check.CHECK_BROWSER_FIXED, (bd.FIX_ECH,), "соединения с сайтом не ответили"))
        # Пустой журнал — настоящее молчание: проверить не вышло.
        empty = browser_check.BrowserVisit(browser_check.BROWSER_NO_ANSWER, "", "браузер не ответил", "msedge.exe", net=NetFacts())
        self.assertEqual(self._run([[OK, OK]], visit=empty).code, check.CHECK_BROWSER_UNKNOWN)

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
    def test_every_message_says_what_was_checked_and_what_to_do(self) -> None:
        from blockcheck import browser_diagnosis as bd

        kind, title, body = check.apply_check_message("discord.com", check.ApplyCheck(check.CHECK_OK, browser="Edge"))
        self.assertEqual((kind, title), ("success", "Стратегия работает"))
        self.assertIn("пробой программы и браузером Edge", body)
        self.assertIsNone(check.apply_check_message("discord.com", check.ApplyCheck(check.CHECK_SKIPPED, "Zapret не запустился")))
        cases = [
            check.ApplyCheck(check.CHECK_FAILED, "соединение сброшено"),
            check.ApplyCheck(check.CHECK_BROWSER_FAILED, "соединение сброшено", browser="Edge"),
            check.ApplyCheck(check.CHECK_BROWSER_UNKNOWN, "браузер не ответил", browser="Edge"),
            check.ApplyCheck(check.CHECK_BROWSER_PARTS, "страница не загрузилась", browser="Edge", hosts=("cdn.discordapp.com",)),
            check.ApplyCheck(check.CHECK_BROWSER_FIXED, "сброшено", browser="Edge", fixes=(bd.FIX_PROXY,), proxy="127.0.0.1:8080"),
        ]
        for case in cases:
            with self.subTest(case.code):
                _kind, _title, body = check.apply_check_message("discord.com", case)
                self.assertIn("Что сделать:", body)
                self.assertNotIn("msedge.exe", body)

    def test_address_advice_depends_on_where_the_browser_went(self) -> None:
        from blockcheck import browser_diagnosis as bd

        ipv6 = check.ApplyCheck(check.CHECK_BROWSER_FIXED, "", browser="Edge", fixes=(bd.FIX_ADDRESS,), address="2606:4700::1", probe_address="104.21.32.151")
        _kind, _title, body = check.apply_check_message("x.com", ipv6)
        self.assertIn("IP версии 6", body)
        self.assertIn("сам браузер шёл на 2606:4700::1", body)
        # Другой адрес дал браузеру его шифрованный DNS (так по журналу сети) — совет про DNS браузера.
        doh = check.ApplyCheck(
            check.CHECK_BROWSER_FIXED, "", browser="Edge", fixes=(bd.FIX_ADDRESS,), address="9.9.9.9", probe_address="104.21.32.151", secure_dns=True
        )
        _kind, _title, body = check.apply_check_message("x.com", doh)
        self.assertIn("защищённый DNS", body)
        self.assertNotIn("()", body)
        # Журнал этого не показал — про DNS браузера не говорим.
        plain = check.ApplyCheck(check.CHECK_BROWSER_FIXED, "", browser="Edge", fixes=(bd.FIX_ADDRESS,), address="9.9.9.9", probe_address="104.21.32.151")
        _kind, _title, body = check.apply_check_message("x.com", plain)
        self.assertNotIn("защищённый DNS", body)
        self.assertIn("запустите подбор ещё раз", body)

    def test_parts_name_the_hosts_to_pick_a_strategy_for(self) -> None:
        parts = check.ApplyCheck(check.CHECK_BROWSER_PARTS, "", browser="Edge", hosts=("a.example", "b.example", "c.example", "d.example"))
        _kind, _title, body = check.apply_check_message("x.com", parts)
        self.assertIn("a.example, b.example, c.example и другие", body)
        self.assertIn("подберите стратегию для a.example", body)
