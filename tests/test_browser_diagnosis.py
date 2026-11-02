"""Почему браузер не открыл сайт: каждое отличие от пробы проверяется новым открытием (браузер подменён)."""

from __future__ import annotations

import unittest

from blockcheck import browser_diagnosis as bd
from blockcheck.browser_check import (
    BROWSER_FAILED,
    BROWSER_OK,
    BROWSER_TIMEOUT,
    BrowserVisit,
)
from blockcheck.browser_netlog import HUNG, Connection, NetFacts

HOST = "www.castingcall.club"
PROBE = ("104.21.32.151", "172.67.186.237")


def _failed(net: NetFacts | None, state: str = BROWSER_TIMEOUT, text: str = "страница не загрузилась за 20 с") -> BrowserVisit:
    return BrowserVisit(state, "", text, "msedge.exe", net=net)


class _Browser:
    """Открывает сайт, только когда в ключах есть ``opens_with`` (все части)."""

    def __init__(self, *opens_with: str) -> None:
        self.opens_with = opens_with
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args: tuple[str, ...]) -> BrowserVisit:
        self.calls.append(args)
        line = " ".join(args)
        if self.opens_with and all(part in line for part in self.opens_with):
            return BrowserVisit(BROWSER_OK, browser="msedge.exe")
        return BrowserVisit(BROWSER_FAILED, "ERR_CONNECTION_CLOSED", "соединение закрыто без ответа", "msedge.exe")


class DiagnosisTests(unittest.TestCase):
    def test_castingcall_ech_is_proven_by_opening_without_it(self) -> None:
        # Как на win10: обычное приветствие закрыто, затем то же с ECH — молчит.
        net = NetFacts(
            (
                Connection(HOST, "104.21.32.151", False, "ERR_CONNECTION_CLOSED"),
                Connection(HOST, "172.67.186.237", True, HUNG),
            ),
            secure_dns=frozenset({HOST}),
        )
        browser = _Browser("MAP www.castingcall.club 172.67.186.237")
        result = bd.diagnose(HOST, _failed(net), probe_addresses=PROBE, visit=browser)
        self.assertEqual((result.code, result.fixes), (bd.DIAG_FIXED, (bd.FIX_ECH,)))
        # Только то, на что указывает журнал: тот же адрес, что выбрал браузер, но мимо DNS — значит, без ECH.
        self.assertEqual(browser.calls, [("--host-resolver-rules=MAP www.castingcall.club 172.67.186.237",)])

    def test_browser_on_ipv6_is_proven_by_the_probe_address(self) -> None:
        net = NetFacts((Connection(HOST, "2606:4700::6815:2097", False, HUNG),))
        browser = _Browser("MAP www.castingcall.club 104.21.32.151")
        result = bd.diagnose(HOST, _failed(net), probe_addresses=PROBE, visit=browser)
        self.assertEqual((result.code, result.fixes), (bd.DIAG_FIXED, (bd.FIX_ADDRESS,)))
        self.assertEqual((result.address, result.probe_address), ("2606:4700::6815:2097", "104.21.32.151"))

    def test_proxy_from_windows_settings_is_proven_by_going_direct(self) -> None:
        net = NetFacts((Connection(HOST, "104.21.32.151", False, "ERR_CONNECTION_RESET"),), proxies={HOST: "127.0.0.1:8080"})
        result = bd.diagnose(HOST, _failed(net), probe_addresses=PROBE, visit=_Browser("--no-proxy-server"))
        self.assertEqual((result.code, result.fixes, result.proxy), (bd.DIAG_FIXED, (bd.FIX_PROXY,), "127.0.0.1:8080"))

    def test_page_waiting_for_other_names_is_not_a_path_problem(self) -> None:
        net = NetFacts(
            (Connection(HOST, "104.21.32.151", False, "", 9000),),
            failed_requests=((HOST, HUNG), ("cdn.castingcall.club", HUNG), ("cdn.castingcall.club", HUNG), ("api.example.com", "ERR_CONNECTION_RESET")),
        )
        browser = _Browser("anything")
        result = bd.diagnose(HOST, _failed(net), probe_addresses=PROBE, visit=browser)
        self.assertEqual((result.code, result.hosts), (bd.DIAG_PARTS, ("cdn.castingcall.club", "api.example.com")))
        self.assertEqual(browser.calls, [])

    def test_nothing_alone_helps_then_everything_together_is_tried(self) -> None:
        net = NetFacts((Connection(HOST, "2606:4700::1", True, HUNG),), secure_dns=frozenset({HOST}))
        browser = _Browser("MAP www.castingcall.club 104.21.32.151", "--no-proxy-server")
        result = bd.diagnose(HOST, _failed(net), probe_addresses=PROBE, visit=browser)
        self.assertEqual(result.code, bd.DIAG_FIXED)
        self.assertEqual(set(result.fixes), {bd.FIX_ECH, bd.FIX_ADDRESS, bd.FIX_PROXY})
        self.assertTrue(result.secure_dns)
        # По одному — то, на что указал журнал: ECH (карта на свой адрес IPv6), затем адрес пробы; затем всё разом.
        self.assertEqual(browser.calls[0], ("--host-resolver-rules=MAP www.castingcall.club [2606:4700::1]",))
        self.assertEqual(len(browser.calls), 3)
        self.assertEqual(result.tried_fixes, (bd.FIX_ECH, bd.FIX_ADDRESS, bd.FIX_PROXY))

    def test_nothing_helps_is_said_with_the_browser_error(self) -> None:
        net = NetFacts((Connection(HOST, "104.21.32.151", False, "ERR_CONNECTION_RESET"),))
        browser = _Browser()
        # Журнал ни на что не указал: ни ECH, ни другого адреса, ни прокси.
        result = bd.diagnose(HOST, _failed(net, BROWSER_FAILED, "соединение сброшено"), probe_addresses=PROBE, visit=browser)
        self.assertEqual(result.code, bd.DIAG_BROWSER_CUT)
        self.assertEqual(result.reason, "соединение сброшено")
        # Поэтому пробуется всё разом, один раз.
        self.assertEqual(len(browser.calls), 1)

    def test_no_net_log_means_every_difference_is_checked(self) -> None:
        browser = _Browser("--no-proxy-server")
        result = bd.diagnose(HOST, _failed(None), probe_addresses=PROBE, visit=browser)
        self.assertEqual((result.code, result.fixes), (bd.DIAG_FIXED, (bd.FIX_PROXY,)))
        # Без журнала ECH отдельно не выключить: проверяются адрес пробы (он и без ECH) и прокси.
        self.assertEqual(len(browser.calls), 2)

    def test_empty_net_log_of_a_killed_browser_is_no_facts(self) -> None:
        # Зависший браузер гасится, журнал остаётся пустым: это не «отличий нет», а «фактов нет» — проверяется всё.
        browser = _Browser("MAP www.castingcall.club 104.21.32.151")
        result = bd.diagnose(HOST, _failed(NetFacts()), probe_addresses=PROBE, visit=browser)
        self.assertEqual((result.code, result.fixes), (bd.DIAG_FIXED, (bd.FIX_ADDRESS,)))

    def test_stop_ends_the_search(self) -> None:
        browser = _Browser()
        result = bd.diagnose(HOST, _failed(None), probe_addresses=PROBE, visit=browser, cancelled=lambda: True)
        # Ничего не пробовали — и вывода нет.
        self.assertEqual(result.code, bd.DIAG_UNCLEAR)
        self.assertEqual(browser.calls, [])

    def test_silent_retries_are_repeated_and_never_called_a_failure(self) -> None:
        # Так было на win10: первый раз Edge не дождался страницы, повторы не сказали ничего и журнал оставили пустым.
        net = NetFacts((Connection(HOST, "172.67.186.237", True, HUNG),))
        calls: list[tuple[str, ...]] = []

        def silent(args):
            calls.append(args)
            return BrowserVisit("no_answer", "", "браузер не ответил", "msedge.exe", net=NetFacts())

        result = bd.diagnose(HOST, _failed(net), probe_addresses=PROBE, visit=silent)
        self.assertEqual(result.code, bd.DIAG_UNCLEAR)
        # Каждый повтор — дважды: ECH отдельно и всё разом.
        self.assertEqual(len(calls), 4)

    def test_retry_whose_log_shows_the_site_connection_passing_proves_the_change(self) -> None:
        # Страница не дождалась, но соединение с сайтом в повторе прошло, а в первый раз — нет: изменение помогло.
        net = NetFacts((Connection(HOST, "172.67.186.237", True, HUNG),))

        def retry(args):
            passed = NetFacts((Connection(HOST, "172.67.186.237", False, "", 9000),))
            return BrowserVisit(BROWSER_TIMEOUT, "", "страница не загрузилась за 20 с", "msedge.exe", net=passed)

        result = bd.diagnose(HOST, _failed(net), probe_addresses=PROBE, visit=retry)
        self.assertEqual((result.code, result.fixes), (bd.DIAG_FIXED, (bd.FIX_ECH,)))

    def test_connection_that_passed_already_the_first_time_proves_nothing(self) -> None:
        net = NetFacts((Connection(HOST, "104.21.32.151", False, "", 9000),))

        def retry(args):
            return BrowserVisit(BROWSER_TIMEOUT, "", "страница не загрузилась за 20 с", "msedge.exe", net=net)

        result = bd.diagnose(HOST, _failed(net), probe_addresses=PROBE, visit=retry)
        self.assertEqual(result.code, bd.DIAG_BROWSER_CUT)

    def test_switches_checked_on_edge_156(self) -> None:
        # Адрес пробы уже включает «без ECH»; ECH отдельно — карта на свой адрес браузера; --disable-features не работает.
        args = bd.browser_args((bd.FIX_ECH, bd.FIX_ADDRESS, bd.FIX_PROXY), host=HOST, probe_address="104.21.32.151", own_address="172.67.186.237")
        self.assertEqual(args, ("--host-resolver-rules=MAP www.castingcall.club 104.21.32.151", "--no-proxy-server"))
        only_ech = bd.browser_args((bd.FIX_ECH,), host=HOST, probe_address="104.21.32.151", own_address="172.67.186.237")
        self.assertEqual(only_ech, ("--host-resolver-rules=MAP www.castingcall.club 172.67.186.237",))
        self.assertFalse(any("disable-features" in arg for arg in (*args, *only_ech)))


if __name__ == "__main__":
    unittest.main()
