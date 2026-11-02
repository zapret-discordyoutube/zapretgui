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
    def _run(self, outcomes_by_call, *, running=True, visit=None, retry=None, quic_visit=None):
        from blockcheck import browser_check

        calls = iter(outcomes_by_call)
        visit = visit or browser_check.BrowserVisit(browser_check.BROWSER_MISSING)
        self.retries: list[tuple[str, ...]] = []

        def open_browser(host, *, cancelled, extra=(), net_log=False, quic=False):
            if quic:
                return quic_visit or visit
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
        result = self._run_with_browser(lambda host, quic=False, **_k: opened if quic else next(answers))
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


class ForTheirBrowserTests(unittest.TestCase):
    """Совет и кнопки — для браузера человека по умолчанию."""

    def _ech(self, family: str, title: str) -> check.ApplyCheck:
        from blockcheck import browser_diagnosis as bd

        return check.ApplyCheck(
            check.CHECK_BROWSER_FIXED, "страница не загрузилась за 20 с", browser="Edge", fixes=(bd.FIX_ECH,),
            address="172.67.186.237", probe_address="104.21.32.151", user_browser=family, user_browser_title=title,
        )

    def test_firefox_user_gets_only_the_firefox_step_and_a_button(self) -> None:
        result = self._ech("firefox", "Firefox")
        buttons = check.apply_check_buttons(result)
        self.assertEqual([button["text"] for button in buttons], ["Выключить ECH в Firefox"])
        self.assertEqual(buttons[0]["value"], "off_ech:firefox:Firefox")
        _kind, _title, body = check.apply_check_message("www.castingcall.club", result, with_buttons=True)
        self.assertIn("выключите ECH в Firefox — кнопкой ниже или вручную: about:config", body)
        self.assertNotIn("reg add", body)
        # Без кнопок текст на них не ссылается.
        _kind, _title, plain = check.apply_check_message("www.castingcall.club", result)
        self.assertNotIn("кнопкой ниже", plain)

    def test_edge_user_gets_the_edge_rule(self) -> None:
        _kind, _title, body = check.apply_check_message("x.com", self._ech("edge", "Edge"), with_buttons=True)
        self.assertIn(r"reg add HKCU\Software\Policies\Microsoft\Edge /v EncryptedClientHelloEnabled", body)
        self.assertNotIn("about:config", body)

    def test_browser_the_program_cannot_switch_gets_all_steps_and_no_button(self) -> None:
        result = self._ech("other_chromium", "Яндекс Браузер")
        self.assertEqual(check.apply_check_buttons(result), [])
        _kind, _title, body = check.apply_check_message("x.com", result, with_buttons=False)
        self.assertIn("about:config", body)
        self.assertIn("reg add", body)

    def test_proxy_and_ipv6_buttons(self) -> None:
        from blockcheck import browser_diagnosis as bd

        proxy = check.ApplyCheck(check.CHECK_BROWSER_FIXED, "", browser="Edge", fixes=(bd.FIX_PROXY,), proxy="127.0.0.1:8080")
        self.assertEqual([button["kind"] for button in check.apply_check_buttons(proxy)], ["disable_proxy"])
        ipv6 = check.ApplyCheck(check.CHECK_BROWSER_FIXED, "", browser="Edge", fixes=(bd.FIX_ADDRESS,), address="2606:4700::1", probe_address="1.1.1.1")
        self.assertEqual([button["value"] for button in check.apply_check_buttons(ipv6)], ["open_adapters"])
        # Не доказано — кнопок нет.
        self.assertEqual(check.apply_check_buttons(check.ApplyCheck(check.CHECK_BROWSER_FAILED, "")), [])


class ProgressTests(unittest.TestCase):
    def test_each_step_of_the_search_is_told(self) -> None:
        from blockcheck import browser_check
        from blockcheck.browser_netlog import HUNG, Connection, NetFacts
        from blockcheck.default_browser import DefaultBrowser

        net = NetFacts((Connection("discord.com", "1.1.1.1", True, HUNG),))
        failed = browser_check.BrowserVisit(browser_check.BROWSER_TIMEOUT, "", "страница не загрузилась за 20 с", "msedge.exe", net=net)
        opened = browser_check.BrowserVisit(browser_check.BROWSER_OK, browser="msedge.exe")
        lines: list[str] = []

        def open_browser(host, *, cancelled, extra=(), net_log=False, quic=False):
            return opened if extra else failed

        with (
            patch("blockcheck.strategy_search.probes.resolve_target_addresses", return_value=(["1.1.1.1", "1.1.2.1"], "")),
            patch("blockcheck.strategy_search.probes.probe_https_addresses", return_value=[OK, OK]),
            patch.object(check, "wait_until_running", return_value=True),
        ):
            cancel = SocketCancel()
            cancel.sleep = lambda seconds: True
            result = check.run_apply_check(
                "discord.com", is_running=lambda: True, restarting=False, cancel=cancel, open_browser=open_browser,
                say=lines.append, user_browser=DefaultBrowser("firefox", "Firefox"),
            )
        self.assertEqual((result.code, result.user_browser), (check.CHECK_BROWSER_FIXED, "firefox"))
        text = "\n".join(lines)
        self.assertIn("Проба открыла discord.com", text)
        self.assertIn("Edge не открыл discord.com: страница не загрузилась за 20 с. Ищу причину по журналу сети браузера: шёл на 1.1.1.1, прятал имя сайта (ECH).", text)
        self.assertIn("Открываю discord.com ещё раз — без ECH: открылся.", text)


class BrowserFixActionTests(unittest.TestCase):
    def test_turning_ech_off_offers_undo(self) -> None:
        from blockcheck import browser_fixes

        registry = {}

        class Registry:
            def set_dword(self, path, name, value):
                registry[(path, name)] = value

            def delete_value(self, path, name):
                registry.pop((path, name), None)

            def key_is_empty(self, path):
                return not any(key[0] == path for key in registry)

            def delete_key(self, path):
                pass

        ok, text, buttons = browser_fixes.run_fix("off_ech:edge:Edge", registry=Registry())
        self.assertTrue(ok)
        self.assertIn("Закройте все окна Edge", text)
        self.assertEqual([(button["text"], button["value"]) for button in buttons], [("Вернуть как было", "restore_ech:edge:Edge")])
        ok, _text, buttons = browser_fixes.run_fix("restore_ech:edge:Edge", registry=Registry())
        self.assertTrue(ok)
        self.assertEqual((registry, buttons), ({}, []))


class ProfileThatCatchesTheSiteTests(unittest.TestCase):
    """Проба через пресет не открыла: называется профиль, который движок берёт для сайта."""

    PRESET = "--new\n--name=Общий\n--filter-tcp=443\n--hostlist-domains=castingcall.club,example.com\n--lua-desync=fake\n" \
        "--new\n--filter-tcp=443\n--hostlist-domains=castingcall.club\n--lua-desync=multisplit\n"

    def _failed_run(self, preset: str):
        lines: list[str] = []
        with (
            patch("blockcheck.strategy_search.probes.resolve_target_addresses", return_value=(["104.21.32.151"], "")),
            patch("blockcheck.strategy_search.probes.probe_https_addresses", return_value=[RESET]),
            patch.object(check, "wait_until_running", return_value=True),
        ):
            cancel = SocketCancel()
            cancel.sleep = lambda seconds: True
            result = check.run_apply_check(
                "www.castingcall.club", is_running=lambda: True, restarting=False, cancel=cancel, say=lines.append, read_preset=lambda: preset
            )
        return result, lines

    def test_profile_above_is_named_with_the_step_to_move_the_site_profile(self) -> None:
        result, lines = self._failed_run(self.PRESET)
        self.assertEqual((result.code, result.profile_number, result.profile_title, result.profile_own), (check.CHECK_FAILED, 1, "Общий", False))
        _kind, title, body = check.apply_check_message("www.castingcall.club", result)
        self.assertEqual(title, "В вашем пресете сайт не открылся")
        self.assertIn("сайт забирает профиль №1 «Общий» — www.castingcall.club записан прямо в нём", body)
        self.assertIn("перетащите профиль сайта выше «Общий»", body)
        self.assertNotIn("Скорее всего", body)
        self.assertIn("Движок берёт www.castingcall.club профилем №1 «Общий»", "\n".join(lines))

    def test_site_already_in_its_own_profile_is_not_an_order_problem(self) -> None:
        own_first = "--new\n--filter-tcp=443\n--hostlist-domains=castingcall.club\n--lua-desync=multisplit\n"
        result, _lines = self._failed_run(own_first)
        self.assertTrue(result.profile_own)
        _kind, _title, body = check.apply_check_message("www.castingcall.club", result)
        self.assertIn("Порядок ни при чём", body)
        self.assertNotIn("перетащите", body)

    def test_no_profile_takes_the_site(self) -> None:
        result, _lines = self._failed_run("--new\n--name=Другое\n--filter-tcp=443\n--hostlist-domains=example.com\n--lua-desync=fake\n")
        self.assertTrue(result.profile_none)
        _kind, _title, body = check.apply_check_message("www.castingcall.club", result)
        self.assertIn("Ни один включённый профиль", body)

    def test_unreadable_preset_falls_back_to_the_careful_wording(self) -> None:
        def broken():
            raise OSError("нет файла")

        lines: list[str] = []
        with (
            patch("blockcheck.strategy_search.probes.resolve_target_addresses", return_value=(["104.21.32.151"], "")),
            patch("blockcheck.strategy_search.probes.probe_https_addresses", return_value=[RESET]),
            patch.object(check, "wait_until_running", return_value=True),
        ):
            cancel = SocketCancel()
            cancel.sleep = lambda seconds: True
            result = check.run_apply_check("x.com", is_running=lambda: True, restarting=False, cancel=cancel, say=lines.append, read_preset=broken)
        _kind, _title, body = check.apply_check_message("x.com", result)
        self.assertIn("Скорее всего", body)



class QuicTests(unittest.TestCase):
    """Без QUIC браузер сайт открыл — проверка ещё и с QUIC, как работают браузеры."""

    def _run(self, quic_visit):
        from blockcheck import browser_check
        from blockcheck.default_browser import DefaultBrowser

        opened = browser_check.BrowserVisit(browser_check.BROWSER_OK, browser="msedge.exe")
        lines: list[str] = []

        def open_browser(host, *, cancelled, extra=(), net_log=False, quic=False):
            return quic_visit if quic else opened

        with (
            patch("blockcheck.strategy_search.probes.resolve_target_addresses", return_value=(["1.1.1.1"], "")),
            patch("blockcheck.strategy_search.probes.probe_https_addresses", return_value=[OK]),
            patch.object(check, "wait_until_running", return_value=True),
        ):
            cancel = SocketCancel()
            cancel.sleep = lambda seconds: True
            result = check.run_apply_check(
                "discord.com", is_running=lambda: True, restarting=False, cancel=cancel, open_browser=open_browser,
                say=lines.append, user_browser=DefaultBrowser("firefox", "Firefox"),
            )
        return result, "\n".join(lines)

    def test_site_that_fails_only_with_quic_gets_its_own_warning_and_button(self) -> None:
        from blockcheck import browser_check

        failed = browser_check.BrowserVisit(browser_check.BROWSER_TIMEOUT, "", "страница не загрузилась за 20 с", "msedge.exe")
        result, said = self._run(failed)
        self.assertEqual((result.code, result.text), (check.CHECK_BROWSER_QUIC, "страница не загрузилась за 20 с"))
        self.assertIn("С QUIC Edge не открыл discord.com", said)
        buttons = check.apply_check_buttons(result)
        self.assertEqual([(button["text"], button["value"]) for button in buttons], [("Выключить QUIC в Firefox", "off_quic:firefox:Firefox")])
        kind, title, body = check.apply_check_message("discord.com", result, with_buttons=True)
        self.assertEqual((kind, title), ("warning", "С QUIC сайт не открывается"))
        self.assertIn("выключите QUIC в Firefox — кнопкой ниже или вручную: about:config → network.http.http3.enable = false", body)

    def test_quic_that_opens_or_stays_silent_keeps_the_site_working(self) -> None:
        from blockcheck import browser_check

        result, said = self._run(browser_check.BrowserVisit(browser_check.BROWSER_OK, browser="msedge.exe"))
        self.assertEqual(result.code, check.CHECK_OK)
        self.assertIn("С QUIC Edge тоже открыл", said)
        silent = browser_check.BrowserVisit(browser_check.BROWSER_NO_ANSWER, "", "браузер не ответил", "msedge.exe")
        result, said = self._run(silent)
        self.assertEqual(result.code, check.CHECK_OK)
        self.assertIn("промолчал", said)
