import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from blockcheck.ui.result_cards_model import build_cards, build_counters
from diagnostics import block_cause as bc
from diagnostics import protocol_probe as pp
from diagnostics import services
from utils.socket_cancel import SocketCancel


def _facts(tls12=None, tls13=None, http=None, http_ms=None, cancelled=False):
    return pp.ProtocolFacts("x.com", "1.2.3.4", tls12, tls13, http, http_ms, cancelled)


class ProtocolJudgeTests(unittest.TestCase):
    def test_each_road_gets_its_own_line(self) -> None:
        lines = pp.judge(
            _facts(
                bc.HelloResult(bc.HELLO_OK, ms=120.4),
                bc.HelloResult(bc.HELLO_RESET),
                bc.HttpFacts(status=301, location="https://x.com/"),
                35.0,
            )
        )

        self.assertEqual([(line.title, line.state, line.word) for line in lines], [
            ("TLS 1.2", "ok", "работает"), ("TLS 1.3", "fail", "сброс"), ("HTTP", "ok", "переход"),
        ])
        self.assertIn("120 мс", lines[0].text)
        self.assertIn("35 мс", lines[2].text)

    def test_server_refusing_a_version_is_not_a_block(self) -> None:
        [line] = pp.judge(_facts(tls12=bc.HelloResult(bc.HELLO_ALERT)))

        self.assertEqual((line.state, line.word), ("info", "нет у сервера"))
        self.assertIn("не блокировка", line.text)

    def test_silent_port_80_is_unknown_not_blocked(self) -> None:
        [line] = pp.judge(_facts(http=bc.HttpFacts()))

        self.assertEqual((line.state, line.word), ("unknown", "не ответил"))

    def test_provider_stub_page_is_named(self) -> None:
        [line] = pp.judge(_facts(http=bc.HttpFacts(status=451), http_ms=10.0))

        self.assertEqual((line.state, line.word), ("fail", "заглушка"))

    def test_other_tls_outcomes(self) -> None:
        words = {
            bc.HELLO_TIMEOUT: ("fail", "молчит"),
            bc.HELLO_CONNECT: ("unknown", "нет соединения"),
            bc.HELLO_GARBAGE: ("fail", "чужой ответ"),
        }
        for kind, expected in words.items():
            with self.subTest(kind=kind):
                [line] = pp.judge(_facts(tls13=bc.HelloResult(kind)))
                self.assertEqual((line.state, line.word), expected)

    def test_cancelled_probe_gives_no_lines(self) -> None:
        self.assertEqual(pp.judge(None), ())
        self.assertEqual(pp.judge(_facts(tls12=bc.HelloResult(bc.HELLO_OK, ms=1.0), cancelled=True)), ())
        self.assertEqual(pp.judge(_facts(tls12=bc.HelloResult(bc.HELLO_CANCELLED))), ())

    def test_collect_asks_each_version_separately(self) -> None:
        asked = []

        def _hello(ip, name, *, cancel, version=""):
            asked.append((ip, name, version))
            return bc.HelloResult(bc.HELLO_OK, ms=5.0)

        with (
            ThreadPoolExecutor(max_workers=4) as pool,
            patch.object(pp, "tls_hello", side_effect=_hello),
            patch.object(pp, "http_probe", return_value=bc.HttpFacts(status=200)),
            patch.object(pp.browser_hello, "send_hello", return_value=bc.HelloResult(bc.HELLO_OK, ms=7.0)),
        ):
            facts = pp.collect("x.com", "1.2.3.4", submit=pool.submit, cancel=SocketCancel())

        self.assertEqual(sorted(asked), [("1.2.3.4", "x.com", "1.2"), ("1.2.3.4", "x.com", "1.3")])
        self.assertEqual(facts.http.status, 200)
        self.assertIsNotNone(facts.http_ms)

    def test_hello_context_is_pinned_to_one_version(self) -> None:
        import ssl

        only12 = bc._hello_context(bc.TLS_1_2)
        only13 = bc._hello_context(bc.TLS_1_3)
        self.assertEqual((only12.minimum_version, only12.maximum_version), (ssl.TLSVersion.TLSv1_2,) * 2)
        self.assertEqual((only13.minimum_version, only13.maximum_version), (ssl.TLSVersion.TLSv1_3,) * 2)
        self.assertIsNot(bc._hello_context(), only12)
        self.assertIs(bc._hello_context(bc.TLS_1_2), only12)


class ProtocolCardsTests(unittest.TestCase):
    REPORT = {
        "services": [
            {
                "key": "x",
                "label": "X",
                "level": "ok",
                "targets": [
                    {
                        "host": "x.com",
                        "purpose": "сайт",
                        "main": True,
                        "ok": True,
                        "address": "104.244.42.1",
                        "hosts_stale": True,
                        "tried": [
                            {"address": "151.101.130.146", "result": "connect"},
                            {"address": "104.244.42.1", "result": "ok"},
                        ],
                        "protocols": [
                            {"key": "tls12", "title": "TLS 1.2", "state": "ok", "word": "работает", "text": "за 20 мс"},
                            {"key": "tls13", "title": "TLS 1.3", "state": "fail", "word": "сброс", "text": "сброшено"},
                        ],
                    }
                ],
            }
        ]
    }

    def test_site_card_shows_roads_stale_hosts_and_tried_addresses(self) -> None:
        [card] = build_cards(self.REPORT)

        self.assertIn(("TLS 1.2: работает", "ok"), card.chips)
        self.assertIn(("TLS 1.3: сброс", "fail"), card.chips)
        self.assertIn(("запись в hosts устарела", "warn"), card.chips)
        rows = {line.name: line.text for line in card.sections[0].lines}
        self.assertEqual(rows["Адрес сервера"], "104.244.42.1")
        self.assertEqual(rows["Какие адреса пробовали"], "151.101.130.146 — нет соединения, 104.244.42.1 — открылся")
        self.assertIn("записанный в нём адрес не работает", rows["Файл hosts"])
        self.assertIn("браузер пойдёт по записи", rows["Файл hosts"])
        self.assertEqual(rows["TLS 1.3"], "сброшено")
        counters = {counter.caption: counter.value for counter in build_counters(self.REPORT)}
        self.assertEqual(counters["проб TLS 1.2 / 1.3 / Chrome / HTTP"], 2)


class ServiceListTests(unittest.TestCase):
    def test_full_check_adds_the_long_list_and_quick_checks_do_not(self) -> None:
        main = services.build_services("main")
        everything = services.build_services("all")
        full = services.build_services("full", ["my.example"])

        self.assertEqual(set(main), {"discord", "youtube"})
        self.assertNotIn("github", everything)
        self.assertGreaterEqual(len(full), 35)
        self.assertTrue({"github", "whatsapp", "chatgpt", "user:my.example"} <= set(full))

    def test_no_host_is_listed_twice(self) -> None:
        hosts = [target.host for service in services.build_services("full").values() for target in service.targets]

        self.assertEqual(len(hosts), len(set(hosts)))

    def test_every_service_has_one_main_address(self) -> None:
        for key, service in services.build_services("full").items():
            with self.subTest(service=key):
                self.assertEqual(sum(1 for target in service.targets if target.main), 1)


if __name__ == "__main__":
    unittest.main()
