from __future__ import annotations

import inspect
import json
import socket
import unittest
from unittest.mock import patch

from app.feature_facades.dns import build_dns_feature
from dns import commands as dns_commands
from dns import domain_lookup as engine
from dns import domain_lookup_plans as plans
from dns import domain_lookup_worker
from utils.cert_names import CertNames
from utils.dns_wire import (
    STATUS_NXDOMAIN,
    STATUS_OK,
    STATUS_TIMEOUT,
    TYPE_A,
    TYPE_AAAA,
    TYPE_CNAME,
    TYPE_PTR,
    TYPE_TXT,
    DnsQueryResult,
    DnsRecord,
)
from diagnostics.path_trace import FilterFacts, Hop, RouteTrace
from diagnostics.quic_probe import QUIC_BLOCKED_BY_NAME, QUIC_OK, QuicVerdict
from utils.windows_icmp import HOP_ROUTER, HOP_SILENT, HOP_TARGET, WindowsPingResult

GOOD = engine.DnsServer("Хороший", "9.9.9.9")
STUB = engine.DnsServer("Провайдер", "10.0.0.53", engine.SERVER_SYSTEM)
DEAD = engine.DnsServer("Молчун", "203.0.113.9")
SECURE = engine.DnsServer("Шифрованный", "1.1.1.1", engine.SERVER_DOH)


def _answer(rtype: int, *values: str) -> DnsQueryResult:
    records = tuple(DnsRecord(name="x", rtype=rtype, ttl=60, value=value) for value in values)
    return DnsQueryResult(status=STATUS_OK, rcode=0, records=records, elapsed_ms=12.0)


def _fake_query(server, name, rtype, **_kwargs):
    if server == DEAD.address:
        return DnsQueryResult(status=STATUS_TIMEOUT)
    if name.endswith(".in-addr.arpa"):
        return _answer(TYPE_PTR, "host.example.net.")
    if name.endswith(".origin.asn.cymru.com"):
        return _answer(TYPE_TXT, "64500 | 93.184.216.0/24 | US | arin | 2020-01-01")
    if name.startswith("AS64500."):
        return _answer(TYPE_TXT, "64500 | US | arin | 2020-01-01 | EXAMPLE-NET, US")
    if server == STUB.address:
        return _answer(TYPE_A, "195.82.146.214") if rtype == TYPE_A else DnsQueryResult(status="empty", rcode=0)
    if rtype == TYPE_A:
        return _answer(TYPE_A, "93.184.216.34")
    if rtype == TYPE_AAAA:
        return _answer(TYPE_AAAA, "2606:2800:220:1::1")
    return DnsQueryResult(status="empty", rcode=0)


def _fake_http(method, url, **kwargs):
    if "thc.org" in url:
        page = kwargs["json"].get("page_state")
        rows = [{"domain": "second.example"}] if page else [{"domain": "first.example"}, {"domain": "bad name"}]
        return 200, json.dumps({"matching_records": 2, "domains": rows, "next_page_state": "" if page else "next"})
    if "shodan" in url:
        return 200, json.dumps({"hostnames": ["shodan.example"], "ports": [80, 443]})
    if "ripe.net" in url:
        return 200, json.dumps(
            {"data": {"resource": "93.184.216.0/24", "announced": True, "asns": [{"asn": 64500, "holder": "EXAMPLE"}]}}
        )
    raise AssertionError(url)


class _Network:
    """Подменяет всю сеть движка: тесты не выходят в интернет."""

    def __init__(self, test: unittest.TestCase, *, http=_fake_http, route=None, quic=None) -> None:
        self.http_calls: list[str] = []
        # Путь и QUIC ходят в сеть сами: по умолчанию «трассировка недоступна» и «QUIC не проверяли».
        self.route = route or (lambda ip: RouteTrace(target=ip, supported=False))
        self.quic = quic or (lambda domain, ip: (None, None))
        # Проверка сайта «как в BlockCheck» тоже ходит в сеть: по умолчанию её итога нет.
        self.site = None
        self.site_calls: list[str] = []

        def site_spy(domain, should_stop):
            self.site_calls.append(domain)
            return self.site

        def http_spy(method, url, **kwargs):
            self.http_calls.append(url)
            return http(method, url, **kwargs)

        ping = WindowsPingResult(ok=True, average_ms=20.0, sent=4, received=3, min_ms=10.0, max_ms=30.0, ttl=57)
        patches = [
            patch.object(engine, "query_server", _fake_query),
            patch.object(engine, "query_doh", _fake_query),
            patch.object(engine, "query_tcp", _fake_query),
            patch.object(engine, "canary_answered", lambda _domain: False),
            patch.object(engine, "trace_route", lambda ip, **_k: self.route(ip)),
            patch.object(engine, "_inspect_quic", lambda domain, ip, max_ttl, cancel: self.quic(domain, ip)),
            patch.object(engine, "_http", http_spy),
            patch.object(engine, "_check_site", site_spy),
            # Подсказка через hosts читает каталог и файл hosts и пробует адреса по сети.
            patch.object(engine, "_hosts_help", lambda domain: engine.HostsHelp()),
            patch.object(engine, "_hosts_road", lambda domain, offered, cancel: None),
            patch.object(engine, "fetch_cert_names", lambda ip, server_name=None: CertNames("ok", ("cert.example",), "cn.example")),
            patch.object(engine, "_tcp_connect", lambda ip, port=443: engine.TcpReport(ip, port, "ok", 5.0)),
            patch("utils.windows_icmp.ping_ipv4_host_winapi", lambda *a, **k: ping),
            patch("utils.windows_icmp.ping_ipv6_winapi", lambda *a, **k: ping),
            patch("utils.net_resolve.resolve_addrinfo", lambda host, **k: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))]),
        ]
        for item in patches:
            item.start()
            test.addCleanup(item.stop)


class WaveTests(unittest.TestCase):
    """Две волны: после того, что делает и браузер, подробный разбор идёт всегда."""

    def _run(self, site_level):
        net = _Network(self)
        net.site = {"level": site_level, "headline": "example.com"}
        asked: list[str] = []

        def query(server, name, rtype, **kwargs):
            asked.append(server)
            return _fake_query(server, name, rtype, **kwargs)

        canary: list[str] = []
        with (
            patch.object(engine, "query_server", query),
            patch.object(engine, "canary_answered", lambda domain: canary.append(domain) or False),
        ):
            report = engine.run_domain_lookup("example.com", servers=[SECURE, GOOD])
        return report, net, asked, canary

    def test_open_site_with_honest_dns_still_gets_the_full_check(self) -> None:
        report, net, asked, canary = self._run("ok")

        self.assertEqual(net.site_calls, ["example.com"])
        # Подробный разбор идёт всегда (решение пользователя 2026-10-17): общедоступные
        # DNS-серверы, пробный запрос, пинг и путь — даже когда сайт открылся.
        self.assertIn(GOOD.address, asked)
        self.assertEqual(canary, ["example.com"])
        self.assertIsNotNone(report.ping)
        self.assertIsNotNone(report.route)
        from dns.ui.domain_lookup_cards import KEY_PING, build_lookup_cards

        ping_card = next(card for card in build_lookup_cards(report) if card.key == KEY_PING)
        self.assertNotEqual(ping_card.status, "Подробный разбор не понадобился")

    def test_closed_site_gets_the_full_check(self) -> None:
        report, _net, asked, canary = self._run("fail")

        self.assertIn(GOOD.address, asked)
        self.assertEqual(canary, ["example.com"])
        self.assertIsNotNone(report.ping)
        self.assertIsNotNone(report.route)

    def test_address_typed_by_hand_is_checked_in_full(self) -> None:
        _Network(self)
        report = engine.run_domain_lookup("93.184.216.34", servers=[SECURE, GOOD])
        self.assertIsNotNone(report.ping)


class TargetTests(unittest.TestCase):
    def test_links_ports_and_case_are_cleaned(self) -> None:
        cases = {
            "https://Example.COM/page?x=1": (engine.KIND_DOMAIN, "example.com"),
            "example.com:8443": (engine.KIND_DOMAIN, "example.com"),
            " 8.8.8.8 ": (engine.KIND_IP, "8.8.8.8"),
            "1.2.3.4:443": (engine.KIND_IP, "1.2.3.4"),
            "[2001:db8::1]:443": (engine.KIND_IP, "2001:db8::1"),
            "2001:db8::1": (engine.KIND_IP, "2001:db8::1"),
            "пример.рф": (engine.KIND_DOMAIN, "пример.рф"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(engine.normalize_target(raw)[:2], expected)

    def test_garbage_is_rejected_with_explanation(self) -> None:
        for raw in ("", "   ", "localhost", "exa mple.com", "http://", "-bad-.com"):
            with self.subTest(raw=raw):
                kind, _value, error = engine.normalize_target(raw)
                self.assertEqual(kind, engine.KIND_INVALID)
                self.assertTrue(error)

    def test_address_classes(self) -> None:
        self.assertEqual(engine.classify_ip("93.184.216.34"), (engine.LEVEL_OK, ""))
        level, note = engine.classify_ip("195.82.146.214")
        self.assertEqual(level, engine.LEVEL_FAIL)
        self.assertIn("Ростелеком", note)
        self.assertEqual(engine.classify_ip("127.0.0.1")[0], engine.LEVEL_FAIL)
        self.assertEqual(engine.classify_ip("192.168.1.1")[0], engine.LEVEL_WARN)
        self.assertIn("VPN", engine.classify_ip("198.18.0.5")[1])


class AnswersTests(unittest.TestCase):
    def test_different_addresses_are_not_marked(self) -> None:
        answers = engine.annotate_answers(
            [
                engine.ResolverAnswer(GOOD, STATUS_OK, ipv4=("1.1.1.1",)),
                engine.ResolverAnswer(SECURE, STATUS_OK, ipv4=("8.8.8.8",)),
            ]
        )

        self.assertEqual([item.level for item in answers], [engine.LEVEL_OK, engine.LEVEL_OK])

    def test_nxdomain_is_suspicious_only_when_others_resolve(self) -> None:
        lonely = engine.annotate_answers([engine.ResolverAnswer(GOOD, STATUS_NXDOMAIN)])
        mixed = engine.annotate_answers(
            [engine.ResolverAnswer(GOOD, STATUS_OK, ipv4=("1.1.1.1",)), engine.ResolverAnswer(STUB, STATUS_NXDOMAIN)]
        )

        self.assertEqual(lonely[0].level, engine.LEVEL_UNKNOWN)
        self.assertEqual(mixed[1].level, engine.LEVEL_WARN)

    def test_primary_ip_prefers_windows_and_skips_stubs(self) -> None:
        windows = engine.DnsServer("Windows", "", engine.SERVER_WINDOWS)
        answers = [
            engine.ResolverAnswer(STUB, STATUS_OK, ipv4=("195.82.146.214",)),
            engine.ResolverAnswer(GOOD, STATUS_OK, ipv4=("1.1.1.1",)),
            engine.ResolverAnswer(windows, STATUS_OK, ipv4=("195.82.146.214", "2.2.2.2")),
        ]

        self.assertEqual(engine.pick_primary_ip(answers), "2.2.2.2")
        self.assertEqual(engine.pick_primary_ip(answers[:2]), "1.1.1.1")
        self.assertEqual(engine.pick_primary_ip(answers[:1]), "195.82.146.214")
        self.assertEqual(engine.pick_primary_ip([]), "")


    def test_primary_ip_goes_windows_then_system_then_encrypted_then_most_common(self) -> None:
        # У крупного сайта «самый частый» адрес — далёкий зарубежный узел: компьютер на него не попадёт.
        far = [engine.ResolverAnswer(engine.DnsServer(f"Дальний {n}", f"203.0.113.{n}"), STATUS_OK, ipv4=("5.5.5.5",)) for n in (1, 2, 3)]
        system = engine.ResolverAnswer(engine.DnsServer("", "8.8.4.4", engine.SERVER_SYSTEM), STATUS_OK, ipv4=("3.3.3.3",))
        secure = engine.ResolverAnswer(SECURE, STATUS_OK, ipv4=("4.4.4.4",))
        windows = engine.ResolverAnswer(engine.DnsServer("Windows", "", engine.SERVER_WINDOWS), STATUS_NXDOMAIN)

        self.assertEqual(engine.pick_primary_ip([*far, secure, system, windows]), "3.3.3.3")
        self.assertEqual(engine.pick_primary_ip([*far, secure, windows]), "4.4.4.4")
        self.assertEqual(engine.pick_primary_ip([*far, windows]), "5.5.5.5")

    def test_primary_ip_takes_tcp_answer_of_system_server_with_forged_denial(self) -> None:
        system = engine.ResolverAnswer(
            engine.DnsServer("", "8.8.8.8", engine.SERVER_SYSTEM), STATUS_NXDOMAIN, tcp_ipv4=("3.3.3.3",)
        )
        far = engine.ResolverAnswer(GOOD, STATUS_OK, ipv4=("5.5.5.5",))

        self.assertEqual(engine.pick_primary_ip([far, system]), "3.3.3.3")

    def test_windows_without_address_is_a_finding_when_servers_know_the_site(self) -> None:
        windows = engine.DnsServer("Windows", "", engine.SERVER_WINDOWS)
        blind = engine.annotate_answers(
            [engine.ResolverAnswer(windows, STATUS_NXDOMAIN), engine.ResolverAnswer(GOOD, STATUS_OK, ipv4=("1.1.1.1",))]
        )
        nobody = engine.annotate_answers([engine.ResolverAnswer(windows, STATUS_NXDOMAIN), engine.ResolverAnswer(GOOD, STATUS_NXDOMAIN)])

        self.assertEqual(blind[0].level, engine.LEVEL_WARN)
        self.assertEqual(blind[0].note, engine.NOTE_WINDOWS_BLIND)
        self.assertTrue(engine.windows_blind(blind))
        # Домена и правда нет: Windows не виновата.
        self.assertEqual(nobody[0].level, engine.LEVEL_UNKNOWN)
        self.assertFalse(engine.windows_blind(nobody))

    def test_denial_is_forged_when_the_same_server_names_the_address_over_tcp(self) -> None:
        plain = engine.DnsServer("Google", "8.8.8.8", engine.SERVER_SYSTEM)
        answers = engine.annotate_answers([engine.ResolverAnswer(plain, STATUS_NXDOMAIN, tcp_ipv4=("64.233.165.93",))])

        self.assertEqual(engine.forged_answers(answers), answers)
        self.assertEqual(answers[0].level, engine.LEVEL_WARN)
        self.assertIn("по TCP тот же сервер адрес называет (64.233.165.93)", answers[0].note)

    def test_denial_is_forged_when_the_same_server_names_the_address_encrypted(self) -> None:
        plain = engine.DnsServer("OpenDNS", "208.67.222.222")
        secure = engine.DnsServer("OpenDNS (шифрованный)", "208.67.222.222", engine.SERVER_DOH)
        other = engine.DnsServer("Чужой", "203.0.113.5")
        answers = engine.annotate_answers(
            [
                engine.ResolverAnswer(secure, STATUS_OK, ipv4=("142.251.142.238",)),
                engine.ResolverAnswer(plain, STATUS_NXDOMAIN),
                engine.ResolverAnswer(other, STATUS_NXDOMAIN),
            ]
        )

        self.assertEqual([item.server.label for item in engine.forged_answers(answers)], ["OpenDNS"])
        self.assertEqual(answers[1].note, engine.NOTE_FORGED)
        # У сервера без шифрованной пары доказательства нет: он просто отвечает не как остальные.
        self.assertEqual(answers[2].note, "у других серверов адрес есть")

    def test_forged_mark_counts_only_next_to_honest_answers(self) -> None:
        marked = engine.ResolverAnswer(GOOD, STATUS_NXDOMAIN, forged=True)
        honest = engine.ResolverAnswer(DEAD, STATUS_OK, ipv4=("1.1.1.1",))

        self.assertEqual(engine.forged_answers([marked]), ())
        self.assertEqual(engine.forged_answers([marked, honest]), (marked,))
        self.assertEqual(engine.annotate_answers([marked, honest])[0].note, engine.NOTE_FORGED_MARK)


class AskTests(unittest.TestCase):
    def test_denial_over_udp_is_rechecked_over_tcp(self) -> None:
        denial = DnsQueryResult(status=STATUS_NXDOMAIN, rcode=3, elapsed_ms=6.0, authoritative=True, authority_count=0)
        with (
            patch.object(engine, "query_server", lambda *a, **k: denial),
            patch.object(engine, "query_tcp", lambda *a, **k: _answer(TYPE_A, "64.233.165.93")) as _tcp,
        ):
            answer = engine._ask_server(GOOD, "youtube.com")

        self.assertEqual(answer.status, STATUS_NXDOMAIN)
        self.assertTrue(answer.forged)
        self.assertEqual(answer.tcp_ipv4, ("64.233.165.93",))

    def test_honest_answer_is_not_rechecked(self) -> None:
        with (
            patch.object(engine, "query_server", _fake_query),
            patch.object(engine, "query_tcp", side_effect=AssertionError("лишний запрос по TCP")),
        ):
            answer = engine._ask_server(GOOD, "example.com")

        self.assertEqual(answer.ipv4, ("93.184.216.34",))
        self.assertEqual(answer.tcp_ipv4, ())

    def test_windows_says_why_there_is_no_address(self) -> None:
        from utils.net_resolve import DNSTimeoutError

        cases = (
            (socket.gaierror(socket.EAI_NONAME, "нет такого имени"), STATUS_NXDOMAIN),
            # Так тот же отказ выглядит в Windows: «getaddrinfo failed» с кодом Winsock.
            (socket.gaierror(11001, "getaddrinfo failed"), STATUS_NXDOMAIN),
            (socket.gaierror(11004, "getaddrinfo failed"), STATUS_NXDOMAIN),
            (DNSTimeoutError("молчит"), STATUS_TIMEOUT),
            (socket.gaierror(socket.EAI_AGAIN, "сбой"), "error"),
        )
        for error, expected in cases:
            with self.subTest(expected=expected), patch("utils.net_resolve.resolve_addrinfo", side_effect=error):
                self.assertEqual(engine._ask_windows("youtube.com").status, expected)

    def test_windows_answer_keeps_both_families(self) -> None:
        infos = [
            (socket.AF_INET, 0, 0, "", ("1.2.3.4", 0)),
            (socket.AF_INET6, 0, 0, "", ("2001:db8::1", 0, 0, 0)),
            (socket.AF_INET, 0, 0, "", ("1.2.3.4", 0)),
        ]
        with patch("utils.net_resolve.resolve_addrinfo", return_value=infos):
            answer = engine._ask_windows("example.com")

        self.assertEqual((answer.status, answer.ipv4, answer.ipv6), (STATUS_OK, ("1.2.3.4",), ("2001:db8::1",)))

    def test_placeholder_certificate_is_not_a_neighbour(self) -> None:
        # Google на запрос без имени сайта отдаёт сертификат на несуществующее имя invalid2.invalid.
        with patch.object(engine, "fetch_cert_names", lambda ip, server_name=None: CertNames("ok", (), "invalid2.invalid")):
            source = engine._lookup_cert("142.251.110.190", engine.SOURCE_CERT_DEFAULT, None)

        self.assertEqual((source.status, source.names), (engine.SOURCE_SKIPPED, ()))
        self.assertIn("сертификат-пустышку (invalid2.invalid)", source.detail)

    def test_named_certificate_failure_blames_the_name_only_when_the_site_does_not_open(self) -> None:
        failed = CertNames("timeout", (), "", "сервер не ответил вовремя")
        with patch.object(engine, "fetch_cert_names", lambda ip, server_name=None: failed):
            blocked = engine._lookup_cert("142.251.151.4", engine.SOURCE_CERT_NAMED, "www.youtube.com", False)
            opens = engine._lookup_cert("142.251.151.4", engine.SOURCE_CERT_NAMED, "www.youtube.com", True)

        self.assertIn("блокируют по имени", blocked.detail)
        self.assertNotIn("блокируют", opens.detail)
        self.assertIn("сам сайт при этом открывается", opens.detail)
        self.assertIn("сервер не ответил вовремя", opens.detail)

    def test_address_stage_follows_the_address_that_opened_the_site(self) -> None:
        site = {"targets": [{"main": True, "ok": True, "address": "142.251.153.4"}, {"main": False, "ok": True, "address": "9.9.9.9"}]}

        self.assertEqual(engine.opened_address(site), "142.251.153.4")
        self.assertEqual(engine.opened_address({"targets": [{"main": True, "ok": False, "address": "1.1.1.1"}]}), "")
        self.assertEqual(engine.opened_address({"targets": [{"main": True, "ok": True, "address": "2001:db8::1"}]}), "")
        self.assertEqual(engine.opened_address(None), "")

    def test_unreachable_service_is_explained_in_words(self) -> None:
        import requests

        cases = (
            (requests.exceptions.ConnectTimeout(), "не ответил вовремя"),
            (requests.exceptions.SSLError(), "защищённое соединение"),
            (requests.exceptions.ConnectionError(), "не удалось соединиться"),
            (RuntimeError("boom"), "сервис недоступен"),
        )
        for error, expected in cases:
            with self.subTest(expected=expected):
                detail = engine._unavailable(engine.SOURCE_SHODAN, error).detail
                self.assertIn(expected, detail)
                self.assertNotIn("Error", detail)

    def test_extra_servers_have_no_resolver_that_refuses_everyone(self) -> None:
        # Mullvad обычным запросом отвечает только про собственные имена; UltraDNS уже есть в списке программы.
        labels = [label for label, _address in engine.EXTRA_SERVERS]
        self.assertNotIn("Mullvad", labels)
        self.assertNotIn("UltraDNS", labels)


class ExternalAnswerTests(unittest.TestCase):
    def test_hackertarget_quota_comes_with_http_200(self) -> None:
        source = engine.parse_hackertarget_answer(200, "API count exceeded - Increase Quota with Membership")

        self.assertEqual(source.status, engine.SOURCE_LIMIT)

    def test_hackertarget_list_and_errors(self) -> None:
        ok = engine.parse_hackertarget_answer(200, "A.example.com\nb.example.com\nb.example.com\n")
        self.assertEqual(ok.names, ("a.example.com", "b.example.com"))
        self.assertEqual(engine.parse_hackertarget_answer(200, "").status, engine.SOURCE_EMPTY)
        self.assertEqual(engine.parse_hackertarget_answer(200, "error check your search parameter").status, engine.SOURCE_ERROR)
        self.assertEqual(engine.parse_hackertarget_answer(503, "oops").status, engine.SOURCE_ERROR)

    def test_thc_answer(self) -> None:
        body = json.dumps({"matching_records": 97, "domains": [{"domain": "a.example"}], "next_page_state": "abc"})
        source, state = engine.parse_thc_answer(200, body)
        self.assertEqual((source.status, source.names, source.total, state), (engine.SOURCE_OK, ("a.example",), 97, "abc"))

        error, _ = engine.parse_thc_answer(200, json.dumps({"status": "error", "error": "invalid ip"}))
        self.assertEqual((error.status, error.detail), (engine.SOURCE_ERROR, "invalid ip"))
        self.assertEqual(engine.parse_thc_answer(200, "<html>")[0].status, engine.SOURCE_ERROR)
        self.assertEqual(engine.parse_thc_answer(429, "")[0].status, engine.SOURCE_LIMIT)

    def test_shodan_answer(self) -> None:
        source = engine.parse_shodan_answer(200, json.dumps({"hostnames": ["a.example"], "ports": [443, 80]}))
        self.assertEqual((source.names, source.extra), (("a.example",), "открытые порты: 443, 80"))
        empty = engine.parse_shodan_answer(404, json.dumps({"detail": "No information available"}))
        self.assertEqual(empty.status, engine.SOURCE_EMPTY)

    def test_ripestat_keeps_every_origin(self) -> None:
        body = json.dumps(
            {"data": {"resource": "5.255.255.0/24", "announced": True, "asns": [{"asn": 1, "holder": "A"}, {"asn": 2, "holder": "B"}]}}
        )
        info = engine.parse_ripestat_answer(200, body)
        self.assertEqual(info.origins, (("1", "A"), ("2", "B")))
        self.assertIsNone(engine.parse_ripestat_answer(200, "not json"))

        merged = engine.merge_network(engine.NetworkInfo(asn="1", owner="Cymru"), info)
        self.assertEqual((merged.asn, merged.owner, merged.prefix, len(merged.origins)), ("1", "Cymru", "5.255.255.0/24", 2))


class RunTests(unittest.TestCase):
    def test_domain_run_collects_everything(self) -> None:
        network = _Network(self)
        stages: list[engine.DomainLookupReport] = []

        report = engine.run_domain_lookup(
            "https://example.com/", servers=[SECURE, GOOD, STUB, DEAD], on_stage=stages.append
        )

        self.assertTrue(report.finished)
        self.assertFalse(report.stopped)
        self.assertEqual(report.primary_ip, "93.184.216.34")
        by_label = {item.server.label: item for item in report.answers}
        self.assertEqual(by_label["Хороший"].ipv6, ("2606:2800:220:1::1",))
        self.assertEqual(by_label["Провайдер"].level, engine.LEVEL_FAIL)
        self.assertEqual(by_label["Молчун"].status, STATUS_TIMEOUT)
        self.assertEqual(by_label["Windows"].ipv4, ("93.184.216.34",))
        self.assertFalse(report.intercepted)
        self.assertEqual((report.ping.sent, report.ping.received, report.ping.ttl), (4, 3, 57))
        self.assertEqual(report.ping6.ip, "2606:2800:220:1::1")
        self.assertEqual(report.tcp.status, "ok")
        self.assertEqual((report.network.asn, report.network.prefix), ("64500", "93.184.216.0/24"))
        self.assertEqual(report.network.owner, "EXAMPLE-NET, US")
        self.assertEqual(report.network.origins, (("64500", "EXAMPLE"),))
        sources = {item.key: item for item in report.sources}
        self.assertEqual(sources[engine.SOURCE_PTR].names, ("host.example.net",))
        self.assertEqual(sources[engine.SOURCE_CERT_NAMED].names, ("cn.example", "cert.example"))
        self.assertEqual(sources[engine.SOURCE_THC].names, ("first.example", "second.example"))
        self.assertEqual(sources[engine.SOURCE_SHODAN].names, ("shodan.example",))
        # THC ответил — запасной HackerTarget с его маленьким лимитом не трогаем.
        self.assertNotIn(engine.SOURCE_HACKERTARGET, sources)
        self.assertFalse(any("hackertarget" in url for url in network.http_calls))
        self.assertGreater(len(stages), 3)
        self.assertFalse(stages[0].finished)

    def test_hackertarget_is_only_a_fallback(self) -> None:
        def http(method, url, **kwargs):
            if "thc.org" in url:
                raise OSError("blocked")
            if "hackertarget" in url:
                return 200, "fallback.example\n"
            return _fake_http(method, url, **kwargs)

        _Network(self, http=http)

        report = engine.run_domain_lookup("93.184.216.34", servers=[GOOD])

        sources = {item.key: item for item in report.sources}
        self.assertEqual(sources[engine.SOURCE_THC].status, engine.SOURCE_ERROR)
        self.assertEqual(sources[engine.SOURCE_HACKERTARGET].names, ("fallback.example",))
        self.assertEqual(report.answers, ())
        self.assertNotIn(engine.SOURCE_CERT_NAMED, sources)

    def test_external_services_are_not_called_when_disabled(self) -> None:
        network = _Network(self)

        report = engine.run_domain_lookup("93.184.216.34", servers=[GOOD], use_external=False)

        self.assertEqual(network.http_calls, [])
        sources = {item.key: item for item in report.sources}
        self.assertEqual(sources[engine.SOURCE_THC].status, engine.SOURCE_SKIPPED)
        # Владелец сети узнаётся DNS-запросом, внешние сайты для этого не нужны.
        self.assertEqual(report.network.asn, "64500")

    def test_private_address_is_never_sent_out(self) -> None:
        network = _Network(self)

        report = engine.run_domain_lookup("192.168.1.1", servers=[GOOD])

        self.assertEqual(network.http_calls, [])
        self.assertIsNone(report.network)
        self.assertEqual({item.key: item.status for item in report.sources}[engine.SOURCE_THC], engine.SOURCE_SKIPPED)

    @staticmethod
    def _route(ip):
        hops = tuple(Hop(ttl, HOP_ROUTER, f"10.0.0.{ttl}", float(ttl)) for ttl in range(1, 7))
        return RouteTrace(target=ip, hops=hops + (Hop(7, HOP_TARGET, ip, 40.0),), reached=True)

    def test_domain_is_also_checked_like_a_site_in_blockcheck_and_an_address_is_not(self) -> None:
        network = _Network(self)
        network.site = {"key": "user:example.com", "label": "example.com", "level": "ok", "headline": "example.com открывается", "targets": []}
        stages = []
        report = engine.run_domain_lookup("example.com", servers=[GOOD], use_external=False, on_stage=stages.append)
        # Домен проверен не только по адресу: в отчёте лежит запись сайта, как в BlockCheck.
        self.assertEqual(network.site_calls, ["example.com"])
        self.assertEqual(report.site, network.site)
        self.assertTrue(any(stage.site == network.site for stage in stages))
        self.assertIn("=== Как открывается сайт ===", plans.build_text_report(report))

        # У голого адреса имени сайта нет — такой проверки не бывает; локальный адрес тоже не трогаем.
        network.site_calls.clear()
        self.assertIsNone(engine.run_domain_lookup("93.184.216.34", servers=[GOOD], use_external=False).site)
        self.assertEqual(network.site_calls, [])

    def test_path_quic_and_filter_place_reach_the_report(self) -> None:
        blocked = QuicVerdict(QUIC_BLOCKED_BY_NAME, "блокируется по имени сайта")
        asked: list = []

        def quic(domain, ip):
            asked.append((domain, ip))
            return blocked, FilterFacts(True, True, 6, 6)

        _Network(self, route=self._route, quic=quic)
        report = engine.run_domain_lookup("example.com", servers=[GOOD], use_external=False)

        self.assertEqual(asked, [("example.com", "93.184.216.34")])
        self.assertTrue(report.route.reached)
        self.assertEqual(report.quic, blocked)
        lines = [line.text for line in plans.build_path_lines(report)]
        self.assertEqual(lines[0], "До сервера 7 узлов, ответили 7.")
        self.assertEqual(lines[1], "QUIC (UDP 443): блокируется по имени сайта.")
        self.assertEqual(lines[2], "Фильтр стоит между узлом 5 (10.0.0.5) и узлом 6 (10.0.0.6).")
        self.assertEqual(plans.build_path_lines(report)[2].tone, plans.TONE_ERROR)
        table = plans.build_path_text(report).splitlines()
        # Отметка стоит между пятым и шестым узлом.
        self.assertEqual(table.index(f"    {plans.FILTER_MARK}"), 5)
        self.assertTrue(table[4].startswith(" 5  10.0.0.5"))
        self.assertTrue(table[6].startswith(" 6  10.0.0.6"))
        text = plans.build_text_report(report)
        self.assertIn("=== Путь до сервера ===", text)
        self.assertIn(plans.FILTER_MARK, text)

    def test_open_site_has_path_and_quic_but_no_filter_line(self) -> None:
        _Network(self, route=self._route, quic=lambda domain, ip: (QuicVerdict(QUIC_OK, "отвечает за 12 мс"), None))
        report = engine.run_domain_lookup("example.com", servers=[GOOD], use_external=False)

        lines = plans.build_path_lines(report)
        self.assertEqual([line.text for line in lines], ["До сервера 7 узлов, ответили 7.", "QUIC (UDP 443): отвечает за 12 мс."])
        self.assertEqual(lines[1].tone, plans.TONE_SUCCESS)
        self.assertNotIn(plans.FILTER_MARK, plans.build_path_text(report))

    def test_plain_address_gets_path_but_no_quic(self) -> None:
        asked: list = []
        _Network(self, route=self._route, quic=lambda domain, ip: asked.append(domain) or (None, None))
        report = engine.run_domain_lookup("93.184.216.34", servers=[GOOD], use_external=False)

        self.assertEqual(asked, [])
        self.assertTrue(report.route.reached)
        self.assertIsNone(report.quic)

    def test_unreached_target_is_explained_without_alarm(self) -> None:
        def route(ip):
            return RouteTrace(target=ip, hops=(Hop(1, HOP_ROUTER, "10.0.0.1", 1.0), Hop(2, HOP_SILENT), Hop(3, HOP_ROUTER, "10.0.0.3", 2.0)))

        _Network(self, route=route)
        report = engine.run_domain_lookup("example.com", servers=[GOOD], use_external=False)

        line = plans.build_path_lines(report)[0]
        self.assertIn("последний ответивший узел — 3-й (10.0.0.3)", line.text)
        self.assertIn("это ещё не блокировка", line.text)
        self.assertEqual(line.tone, plans.TONE_MUTED)
        self.assertIn(" 2  не ответил", plans.build_path_text(report))

    def test_filter_place_is_searched_only_when_quic_is_blocked_by_name(self) -> None:
        from utils.socket_cancel import SocketCancel

        located: list = []

        def locate(ip, name, *, max_ttl, cancel):
            located.append((ip, name, max_ttl))
            return FilterFacts(True, True, 6, 6)

        for verdict, expected in (
            (QuicVerdict(QUIC_OK, "отвечает"), []),
            (None, []),
            (QuicVerdict(QUIC_BLOCKED_BY_NAME, "блокируется"), [("203.0.113.5", "example.com", 20)]),
        ):
            with self.subTest(verdict=verdict):
                located.clear()
                with (
                    patch.object(engine, "collect_quic", lambda *a, **k: object()),
                    patch.object(engine, "judge_quic", lambda _facts: verdict),
                    patch.object(engine, "locate_filter", locate),
                ):
                    result = engine._inspect_quic("example.com", "203.0.113.5", 20, SocketCancel())
                self.assertEqual(located, expected)
                self.assertEqual(result[0], verdict)
                self.assertEqual(result[1] is not None, bool(expected))

    def test_hop_count_is_declined_correctly(self) -> None:
        self.assertEqual([plans._hops_word(n) for n in (1, 2, 4, 5, 11, 12, 21, 22, 25, 111)],
                         ["узел", "узла", "узла", "узлов", "узлов", "узлов", "узел", "узла", "узлов", "узлов"])

    def test_no_path_section_when_tracing_is_unavailable(self) -> None:
        _Network(self)
        report = engine.run_domain_lookup("example.com", servers=[GOOD], use_external=False)

        self.assertEqual(plans.build_path_lines(report), ())
        self.assertEqual(plans.build_path_text(report), "")
        self.assertNotIn("Путь до сервера", plans.build_text_report(report))

    def test_lowest_ttl_of_address_records_is_kept_and_shown(self) -> None:
        def with_ttl(server, name, rtype, **_kwargs):
            if rtype == TYPE_A:
                records = (
                    DnsRecord(name="x", rtype=TYPE_CNAME, ttl=5, value="alias.example"),
                    DnsRecord(name="x", rtype=TYPE_A, ttl=1693, value="93.184.216.34"),
                    DnsRecord(name="x", rtype=TYPE_A, ttl=300, value="93.184.216.35"),
                )
            else:
                records = (DnsRecord(name="x", rtype=TYPE_AAAA, ttl=1332, value="2606:2800:220:1::1"),)
            return DnsQueryResult(status=STATUS_OK, rcode=0, records=records, elapsed_ms=12.0)

        _Network(self)
        with patch.object(engine, "query_server", with_ttl):
            report = engine.run_domain_lookup("example.com", servers=[GOOD, DEAD], use_external=False)

        good = next(item for item in report.answers if item.server == GOOD)
        # Берётся наименьшее время адресных записей; у псевдонима оно своё и не в счёт.
        self.assertEqual(good.ttl, 300)
        row = next(item for item in plans.build_answer_rows(report) if item.server == "Хороший")
        self.assertIn("Ответ считается свежим ещё 300 с (TTL)", row.tooltip)
        self.assertIn("TTL 300 с", plans.build_text_report(report))
        self.assertIn("CNAME: alias.example", plans.build_text_report(report))

    def test_intercepted_dns_is_detected_by_canary(self) -> None:
        _Network(self)
        with patch.object(engine, "canary_answered", lambda _domain: True):
            report = engine.run_domain_lookup("example.com", servers=[GOOD], use_external=False)

        self.assertTrue(report.intercepted)
        self.assertEqual(plans.build_dns_summary(report).tone, plans.TONE_WARNING)

    def test_stop_before_start_returns_quickly(self) -> None:
        _Network(self)

        report = engine.run_domain_lookup("example.com", servers=[GOOD], should_stop=lambda: True)

        self.assertTrue(report.finished)
        self.assertTrue(report.stopped)
        self.assertIsNone(report.ping)
        self.assertIn("Остановлено", plans.build_status(report).text)

    def test_invalid_input_does_not_touch_network(self) -> None:
        with patch.object(engine, "query_server", side_effect=AssertionError):
            report = engine.run_domain_lookup("not a domain")

        self.assertEqual(report.kind, engine.KIND_INVALID)
        self.assertEqual(plans.build_status(report).tone, plans.TONE_ERROR)


class PlansTests(unittest.TestCase):
    def _report(self) -> engine.DomainLookupReport:
        _Network(self)
        return engine.run_domain_lookup("example.com", servers=[SECURE, GOOD, STUB, DEAD])

    def test_rows_and_summary_point_at_the_stub(self) -> None:
        report = self._report()

        rows = {row.server: row for row in plans.build_answer_rows(report)}
        summary = plans.build_dns_summary(report)

        self.assertIn("заглушка (Ростелеком)", rows["Системный DNS · Провайдер"].result)
        self.assertEqual(rows["Системный DNS · Провайдер"].level, engine.LEVEL_FAIL)
        self.assertEqual(rows["Молчун"].result, "сервер не ответил")
        self.assertIn("шифрованный запрос", rows["Шифрованный"].tooltip)
        self.assertEqual(rows["Windows (как видят программы)"].address, "—")
        self.assertEqual(summary.tone, plans.TONE_ERROR)
        self.assertIn("4 из 5", summary.text)

    def test_text_report_carries_transfer_and_path_of_the_main_address(self) -> None:
        steps = [
            {"key": "dns", "title": "DNS", "level": "ok", "text": "ответ DNS совпал с эталоном", "confidence": "high"},
            {"key": "data", "title": "Передача", "level": "warn", "text": "ответ пришёл после паузы", "confidence": "medium"},
            {"key": "quic", "title": "QUIC", "level": "unknown", "text": "не проверялся", "confidence": ""},
        ]
        target = {"host": "youtube.com", "main": True, "text": "открывается", "path": steps,
                  "transfer": {"code": "pause", "level": "warn", "text": "пауза 2,9 с перед первым байтом"}}
        site = {"headline": "открывается", "targets": [target, dict(target, host="other.com", main=False)]}

        text = plans.build_text_report(self._youtube_report(site))

        self.assertIn("   ⚠️ Скорость ответа: пауза 2,9 с перед первым байтом", text)
        self.assertIn("   ✅ DNS: ответ DNS совпал с эталоном (уверенность высокая)", text)
        self.assertIn("   ⚠️ Передача: ответ пришёл после паузы (уверенность средняя)", text)
        self.assertIn("   ❔ QUIC: не проверялся\n", text)
        # Путь печатается один раз — для главного адреса, а не для каждого.
        self.assertEqual(text.count("Путь запроса"), 1)

    def test_text_report_of_an_old_site_record_has_no_new_lines(self) -> None:
        site = {"headline": "открывается", "targets": [{"host": "youtube.com", "main": True, "text": "открывается"}]}

        text = plans.build_text_report(self._youtube_report(site))

        self.assertNotIn("Путь запроса", text)
        self.assertNotIn("Скорость ответа", text)

    def _youtube_report(self, site: dict | None = None) -> engine.DomainLookupReport:
        """Случай 2026-10-13: фильтр отвечает «домена нет» вместо 8.8.8.8, Windows верит и адрес не получает."""
        answers = engine.annotate_answers(
            [
                engine.ResolverAnswer(engine.DnsServer("Windows", "", engine.SERVER_WINDOWS), STATUS_NXDOMAIN, elapsed_ms=8.0),
                engine.ResolverAnswer(
                    engine.DnsServer("Google", "8.8.8.8", engine.SERVER_SYSTEM), STATUS_NXDOMAIN, forged=True, elapsed_ms=6.0
                ),
                engine.ResolverAnswer(engine.DnsServer("Google", "8.8.4.4", engine.SERVER_SYSTEM), STATUS_OK, ipv4=("64.233.165.93",)),
                engine.ResolverAnswer(
                    engine.DnsServer("Google (шифрованный)", "8.8.8.8", engine.SERVER_DOH), STATUS_OK, ipv4=("64.233.165.190",)
                ),
            ]
        )
        return engine.DomainLookupReport(
            target="youtube.com",
            kind=engine.KIND_DOMAIN,
            answers=answers,
            primary_ip=engine.pick_primary_ip(answers),
            site=site,
            finished=True,
            elapsed_s=10.1,
        )

    def test_forged_denial_from_own_dns_is_named_with_the_cure(self) -> None:
        report = self._youtube_report()

        summary = plans.build_dns_summary(report)
        rows = {(row.server, row.address): row for row in plans.build_answer_rows(report)}
        windows, forged = rows[("Windows (как видят программы)", "—")], rows[("Системный DNS · Google", "8.8.8.8")]

        self.assertEqual(report.primary_ip, "64.233.165.93")
        self.assertEqual(summary.tone, plans.TONE_WARNING)
        self.assertIn("подставной «такого домена нет»", summary.text)
        self.assertIn("Системный DNS · Google", summary.text)
        self.assertIn("DNS этого компьютера", summary.text)
        self.assertIn("«Настройка DNS»", summary.text)
        self.assertIn(engine.NOTE_FORGED, forged.result)
        self.assertEqual(windows.level, engine.LEVEL_WARN)
        self.assertIn("браузер и программы сайт не найдут", windows.result)

    def test_status_and_history_name_the_dns_trouble_instead_of_all_is_well(self) -> None:
        site = {"level": "warn", "headline": "youtube.com открывается"}
        report = self._youtube_report(site)

        status = plans.build_status(report)
        entry = plans.build_history_entry(report)

        self.assertEqual(status.tone, plans.TONE_WARNING)
        self.assertIn("Windows адрес сайта не получает", status.text)
        self.assertEqual(entry["level"], "warn")
        self.assertIn("Windows адрес сайта не получает", entry["headline"])

    def test_closed_site_leads_the_status_and_the_history(self) -> None:
        site = {"level": "fail", "headline": "youtube.com не открывается: блокировка по имени"}
        report = self._youtube_report(site)

        status = plans.build_status(report)
        entry = plans.build_history_entry(report)

        self.assertEqual(status.tone, plans.TONE_ERROR)
        self.assertIn("не открывается: блокировка по имени", status.text)
        self.assertEqual(entry["headline"], site["headline"])
        self.assertIn("Windows адрес сайта не получает", entry["problems"][1])

    def test_forged_denial_at_foreign_server_is_not_blamed_on_this_computer(self) -> None:
        answers = engine.annotate_answers(
            [
                engine.ResolverAnswer(engine.DnsServer("Windows", "", engine.SERVER_WINDOWS), STATUS_OK, ipv4=("1.2.3.4",)),
                engine.ResolverAnswer(engine.DnsServer("OpenDNS", "208.67.222.222"), STATUS_NXDOMAIN, tcp_ipv4=("1.2.3.4",)),
            ]
        )
        report = engine.DomainLookupReport(
            target="example.com", kind=engine.KIND_DOMAIN, answers=answers, primary_ip="1.2.3.4", finished=True
        )

        self.assertIn("DNS этого компьютера среди них нет", plans.build_dns_summary(report).text)
        self.assertEqual(plans.own_dns_trouble(report), "")
        self.assertEqual(plans.build_status(report).tone, plans.TONE_SUCCESS)

    def test_system_server_is_not_shown_with_its_address_twice(self) -> None:
        named = engine.ResolverAnswer(engine.DnsServer("Google", "8.8.8.8", engine.SERVER_SYSTEM), STATUS_OK, ipv4=("1.1.1.1",))
        unnamed = engine.ResolverAnswer(engine.DnsServer("", "10.0.0.1", engine.SERVER_SYSTEM), STATUS_OK, ipv4=("1.1.1.1",))
        legacy = engine.ResolverAnswer(engine.DnsServer("10.0.0.2", "10.0.0.2", engine.SERVER_SYSTEM), STATUS_OK, ipv4=("1.1.1.1",))
        report = engine.DomainLookupReport(target="example.com", kind=engine.KIND_DOMAIN, answers=(named, unnamed, legacy))

        self.assertEqual(
            [row.server for row in plans.build_answer_rows(report)], ["Системный DNS · Google", "Системный DNS", "Системный DNS"]
        )

    def test_ping_lines_show_loss_and_times(self) -> None:
        lines = plans.build_ping_lines(self._report())

        self.assertIn("отправлено 4, получено 3, потеряно 25%", lines[0].text)
        self.assertIn("мин 10 мс, средн 20 мс, макс 30 мс, TTL 57", lines[0].text)
        self.assertEqual(lines[0].tone, plans.TONE_WARNING)
        self.assertIn("IPv6", lines[1].text)
        self.assertIn("порту 443", lines[2].text)

    def test_silent_ping_with_open_port_is_explained(self) -> None:
        report = engine.DomainLookupReport(
            target="1.2.3.4",
            kind=engine.KIND_IP,
            primary_ip="1.2.3.4",
            ping=engine.PingReport(ip="1.2.3.4", sent=4, received=0, error_code="TIMEOUT"),
            tcp=engine.TcpReport("1.2.3.4", 443, "ok", 9.0),
        )

        lines = plans.build_ping_lines(report)

        self.assertIn("не отвечает на пинг", lines[0].text)
        self.assertIn("сам работает", lines[-1].text)

    def test_text_report_has_all_sections(self) -> None:
        report = self._report()

        text = plans.build_text_report(report)

        for expected in (
            "Проверка: example.com",
            "=== Адреса с разных DNS ===",
            "=== Пинг ===",
            "=== Сеть адреса 93.184.216.34 ===",
            "ASN: AS64500, подсеть: 93.184.216.0/24, страна: US",
            "=== Кто ещё на адресе 93.184.216.34 ===",
            "    first.example",
            "    открытые порты: 80, 443",
        ):
            self.assertIn(expected, text)
        self.assertEqual(plans.count_neighbors(report), 6)
        self.assertEqual(plans.build_status(report).tone, plans.TONE_SUCCESS)

    def test_long_lists_stay_whole_in_the_report(self) -> None:
        names = tuple(f"host{index}.example" for index in range(305))
        report = engine.DomainLookupReport(
            target="1.2.3.4",
            kind=engine.KIND_IP,
            primary_ip="1.2.3.4",
            sources=(engine.NeighborSource(engine.SOURCE_THC, engine.SOURCE_OK, names=names, total=9000),),
        )

        self.assertIn(names[-1], plans.build_text_report(report))


V6 = "2a00:1450:4010:c08::88"
V4 = "93.184.216.34"


class ProgramRoadTests(unittest.TestCase):
    """Жалоба владельца 2026-10-14: «нифига не открывается», а проверка «пишет что всё ок».

    В его hosts стояли две чужие строки IPv6 для www.youtube.com. Без тумблера YouTube Windows отдавала
    программам только их; с тумблером — ставила их впереди адресов IPv4. Сайт проверялся по IPv4.
    """

    def _run(self, *, first=V6, only_v6=False, v6_tcp="timeout", road=None, site_level="ok", v4_tcp="ok", help=None, hosts_road=None):
        net = _Network(self)
        net.site = {"level": site_level, "headline": "www.youtube.com открывается"}
        infos = [(socket.AF_INET6, 0, 0, "", (V6, 0, 0, 0)), (socket.AF_INET, 0, 0, "", (V4, 0))]
        if first == V4:
            infos.reverse()
        if only_v6:
            infos = infos[:1]

        def tcp(ip, port=443):
            status = v6_tcp if ":" in ip else v4_tcp
            return engine.TcpReport(ip, port, status, 5.0 if status == "ok" else None)

        found = help if help is not None else engine.HostsHelp(service="YouTube")
        patches = [
            patch("utils.net_resolve.resolve_addrinfo", lambda host, **k: infos),
            patch.object(engine, "_tcp_connect", tcp),
            patch.object(engine, "_hosts_help", lambda domain: found),
            patch.object(engine, "_hosts_road", lambda domain, offered, cancel: self.hosts_tried.append(offered) or hosts_road),
        ]
        self.hosts_tried: list[tuple[str, ...]] = []
        if road is not None:
            patches.append(patch.object(engine, "_program_road", lambda domain, ip, cancel: road))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return engine.run_domain_lookup("www.youtube.com", servers=[SECURE, GOOD])

    def test_windows_answer_remembers_which_address_goes_first(self) -> None:
        infos = [(socket.AF_INET6, 0, 0, "", (V6, 0, 0, 0)), (socket.AF_INET, 0, 0, "", (V4, 0))]
        with patch("utils.net_resolve.resolve_addrinfo", return_value=infos):
            answer = engine._ask_windows("www.youtube.com")
        self.assertEqual(answer.first, V6)
        self.assertEqual(engine.program_ipv6([answer]), V6)
        with patch("utils.net_resolve.resolve_addrinfo", return_value=list(reversed(infos))):
            self.assertEqual(engine.program_ipv6([engine._ask_windows("www.youtube.com")]), "")

    def test_only_ipv6_from_windows_and_it_is_dead_is_the_red_headline(self) -> None:
        report = self._run(only_v6=True)

        # Проверен именно тот адрес, на который идут программы, а не только адрес IPv4.
        self.assertEqual((report.road.ip, report.road.tcp.status), (V6, "timeout"))
        self.assertEqual(report.ping6.ip, V6)
        self.assertEqual(engine.road_trouble(report), engine.ROAD_DEAD)
        self.assertTrue(plans.road_is_fatal(report))

        status = plans.build_status(report)
        self.assertEqual(status.tone, plans.TONE_ERROR)
        self.assertIn("Windows отдаёт программам только адреса IPv6, а соединение по IPv6 не устанавливается", status.text)
        first = plans.build_ping_lines(report)[0]
        self.assertEqual(first.tone, plans.TONE_ERROR)
        self.assertIn("Программам некуда идти", first.text)
        self.assertIn(V6, first.text)
        # Вывод сделан по соединению, а не по пингу, — и это сказано.
        self.assertIn("пинг в этом выводе не участвует", first.text)
        # И сказано, что делать: у YouTube есть тумблер, который пишет адреса IPv4.
        self.assertIn("Включите тумблер «YouTube» на странице «Редактор hosts»", first.text)
        entry = plans.build_history_entry(report)
        self.assertEqual(entry["level"], "fail")
        self.assertIn("Windows отдаёт программам только адреса IPv6", entry["headline"])
        self.assertIn("Программам некуда идти", plans.build_text_report(report))

    def test_dead_ipv6_next_to_a_working_ipv4_is_a_warning_not_a_broken_site(self) -> None:
        """Владелец: «хотя он открывается» — браузер сам переходит на IPv4, а проверка писала «не открывается»."""
        from dns.ui import domain_lookup_cards as lookup_cards

        site = {
            "id": "site:user:www.youtube.com", "name": "www.youtube.com", "level": "ok", "headline": "www.youtube.com открывается",
            "targets": [], "problems": [],
        }
        report = engine.replace(self._run(), site=site)

        self.assertEqual(engine.road_trouble(report), engine.ROAD_DEAD)
        self.assertFalse(plans.road_is_fatal(report))
        self.assertEqual(plans.build_status(report).tone, plans.TONE_WARNING)
        first = plans.build_ping_lines(report)[0]
        self.assertEqual(first.tone, plans.TONE_WARNING)
        self.assertIn("Браузер обычно сам переходит на IPv4", first.text)
        self.assertEqual(plans.build_history_entry(report)["level"], "warn")

        cards = {card.key: card for card in lookup_cards.build_lookup_cards(report)}
        ping = cards[lookup_cards.KEY_PING]
        self.assertEqual((ping.level, ping.status), ("warn", "IPv6 не работает, а Windows ставит его первым"))
        # Карточка сайта остаётся про то, что измерено: её уровень не трогается, иначе дорога
        # «Вы → DNS → Сервер → Сайт» показывала «адрес закрыт» у открытого сайта.
        plain = lookup_cards.build_lookup_cards(engine.replace(report, road=None))[0]
        site_card = next(card for card in cards.values() if card.site)
        self.assertEqual((site_card.level, site_card.status, site_card.kind), (plain.level, plain.status, plain.kind))
        self.assertEqual(site_card.lines[0].state, "warn")
        self.assertIn("Windows первым отдаёт программам адрес IPv6", site_card.lines[0].name)

    def test_name_cut_on_the_ipv6_road_is_found_when_ipv4_opens(self) -> None:
        tcp = engine.TcpReport(V6, 443, "ok", 20.0)
        road = engine.ProgramRoad(V6, tcp, lines=(("Как Chrome", "fail", "на приветствие ответа нет"),), opens=False)
        report = self._run(v6_tcp="ok", road=road)

        self.assertEqual(engine.road_trouble(report), engine.ROAD_BLOCKED)
        lines = [line.text for line in plans.build_ping_lines(report)]
        self.assertIn("по нему сайт не открывается", lines[0])
        self.assertIn("По IPv6 · Как Chrome: на приветствие ответа нет.", lines)
        self.assertIn("а по IPv6 сайт не открывается", plans.build_status(report).text)

    def test_no_alarm_without_a_working_ipv4_to_compare_with(self) -> None:
        for name, kwargs in (
            ("Windows ставит первым IPv4", {"first": V4}),
            ("по IPv6 сайт открывается", {"v6_tcp": "ok", "road": engine.ProgramRoad(V6, engine.TcpReport(V6, 443, "ok", 9.0), opens=True)}),
            ("проба по IPv6 ничего не доказала", {"v6_tcp": "ok", "road": engine.ProgramRoad(V6, engine.TcpReport(V6, 443, "ok", 9.0), opens=None)}),
            ("не отвечает и IPv4 — это не про IPv6", {"v4_tcp": "timeout"}),
            (
                "сайт закрыт и по IPv4 — это обычная блокировка",
                {"v6_tcp": "ok", "site_level": "fail", "road": engine.ProgramRoad(V6, engine.TcpReport(V6, 443, "ok", 9.0), opens=False)},
            ),
        ):
            with self.subTest(name):
                report = self._run(**kwargs)
                self.assertEqual(engine.road_trouble(report), "")
                self.assertEqual(plans.road_problem(report), "")
                self.assertNotIn("идут не туда", plans.build_ping_lines(report)[0].text)

    def test_advice_depends_on_who_wrote_the_row(self) -> None:
        by_program = self._run(help=engine.HostsHelp("YouTube", rows=(V6,), managed=(V6,)))
        self.assertIn("Этот адрес записала программа. Выключите и снова включите тумблер «YouTube»", plans.road_cure(by_program))
        # Чужая строка при включённом сервисе: программа уберёт её сама при следующей записи.
        fights = self._run(help=engine.HostsHelp("YouTube", rows=(V4, V6), managed=(V4,)))
        self.assertIn("мешает сервису «YouTube»", plans.road_cure(fights))
        self.assertIn("программа уберёт эту строку сама", plans.road_cure(fights))
        # Чужая строка, а тумблер выключен: включить — и строка уйдёт.
        alone = self._run(help=engine.HostsHelp("YouTube", rows=(V6,)))
        self.assertIn("Включите тумблер «YouTube»", plans.road_cure(alone))
        self.assertIn("уберёт эту строку сама", plans.road_cure(alone))
        # Сервиса для имени нет — убирать строку придётся самому.
        foreign = self._run(help=engine.HostsHelp("", rows=(V6,)))
        self.assertIn("Удалите её сами", plans.road_cure(foreign))
        unknown = self._run(help=engine.HostsHelp())
        self.assertIn("отключить IPv6", plans.road_cure(unknown))

    def test_closed_site_that_opens_by_the_hosts_address_gets_the_advice_to_turn_the_tile_on(self) -> None:
        """Владелец 2026-10-14: «только после hosts открывается, а программа это не проверяет и не
        советует включить обход». По адресу от DNS имя резали, по адресу из «Редактора hosts» — нет."""
        opens = engine.ProgramRoad("64.233.162.198", engine.TcpReport("64.233.162.198", 443, "ok", 20.0), opens=True)
        offered = ("64.233.162.198", "74.125.205.198")
        report = self._run(first=V4, site_level="fail", help=engine.HostsHelp("YouTube", offered=offered), hosts_road=opens)

        self.assertEqual(self.hosts_tried, [offered])
        # Проба идёт после остальных проверок и не выдаёт себя за «проверки не успели».
        self.assertFalse(report.timed_out)
        advice = plans.hosts_advice(report)
        self.assertIn("По адресу из «Редактора hosts» (64.233.162.198) этот сайт открывается", advice)
        self.assertIn("тумблер «YouTube»", advice)
        self.assertIn(advice, plans.build_status(report).text)
        self.assertIn(advice, plans.build_history_entry(report)["problems"])
        self.assertIn(f"Что поможет: {advice}", plans.build_text_report(report))
        self.assertEqual(plans.hosts_try_note(report), "")

    def test_failed_hosts_try_is_shown_instead_of_silence(self) -> None:
        """Снимок владельца на 21.1.7.150: сайт закрыт, совета нет — и не видно, пробовала ли проверка тумблер."""
        from dns.ui import domain_lookup_cards as lookup_cards

        cut = engine.ProgramRoad(
            "64.233.162.198", engine.TcpReport("64.233.162.198", 443, "ok", 20.0), lines=(("Как Chrome", "fail", "соединение сбрасывают"),), opens=False
        )
        report = self._run(first=V4, site_level="fail", help=engine.HostsHelp("YouTube", offered=("64.233.162.198",)), hosts_road=cut)

        self.assertEqual(plans.hosts_advice(report), "")
        note = plans.hosts_try_note(report)
        self.assertIn("Адрес из «Редактора hosts» (64.233.162.198) тоже проверен — приветствие с именем сайта тоже оборвали", note)
        self.assertIn("тумблер «YouTube» в этот раз не помог бы", note)
        text = plans.build_text_report(report)
        self.assertIn(note, text)
        self.assertIn("    Как Chrome: соединение сбрасывают", text)
        site = {"id": "site:user:www.youtube.com", "name": "www.youtube.com", "level": "fail", "headline": "закрыт", "targets": [], "problems": []}
        card = lookup_cards.build_lookup_cards(engine.replace(report, site=site))[0]
        self.assertIn(note, [line.text for line in card.lines])

    def test_hosts_addresses_are_tried_only_for_a_closed_site_with_the_tile_off(self) -> None:
        offered = ("64.233.162.198",)
        for name, kwargs in (
            ("сайт открывается — пробовать незачем", {"site_level": "ok", "help": engine.HostsHelp("YouTube", offered=offered)}),
            ("сервиса для имени нет", {"site_level": "fail", "help": engine.HostsHelp()}),
            ("тумблер уже включён", {"site_level": "fail", "help": engine.HostsHelp("YouTube", offered=offered, rows=offered, managed=offered)}),
        ):
            with self.subTest(name):
                report = self._run(first=V4, **kwargs)
                self.assertEqual(self.hosts_tried, [])
                self.assertIsNone(report.hosts_road)
                self.assertEqual(plans.hosts_try_note(report), "")

    def test_hosts_addresses_are_tried_one_by_one_until_the_site_opens(self) -> None:
        tried: list[str] = []

        def road(domain, ip, cancel):
            tried.append(ip)
            return engine.ProgramRoad(ip, engine.TcpReport(ip, 443, "ok", 5.0), opens=ip == "2.2.2.2")

        with patch.object(engine, "_program_road", road), patch.object(engine, "HOSTS_ROAD_PAUSE_S", 0.0), patch.object(engine, "HOSTS_ROAD_TRIES", 3):
            found = engine._hosts_road("www.youtube.com", ("1.1.1.1", "2.2.2.2", "3.3.3.3"), engine.SocketCancel())
        self.assertEqual(tried, ["1.1.1.1", "2.2.2.2"])
        self.assertEqual((found.ip, found.opens), ("2.2.2.2", True))
        # Первая попытка шла долго — фильтр глушит имя молча, вторая кончилась бы так же: не ждём её.
        tried.clear()
        clock = iter([0.0, 9.0])
        with (
            patch.object(engine, "_program_road", road),
            patch.object(engine, "HOSTS_ROAD_PAUSE_S", 0.0),
            patch.object(engine.time, "monotonic", lambda: next(clock)),
        ):
            engine._hosts_road("www.youtube.com", ("1.1.1.1", "3.3.3.3"), engine.SocketCancel())
        self.assertEqual(tried, ["1.1.1.1"])
        # Проверку сняли — дальше не идём.
        stopped = engine.SocketCancel()
        stopped.cancel()
        with patch.object(engine, "_program_road", road):
            self.assertIsNone(engine._hosts_road("www.youtube.com", ("1.1.1.1",), stopped))

    def test_road_probe_uses_the_real_site_name_and_judges_only_cut_greetings(self) -> None:
        from diagnostics import protocol_probe

        def line(key, state):
            return protocol_probe.ProtocolLine(key, key, state, "", "текст")

        asked: list[tuple[str, str]] = []

        def collect(host, ip, *, submit, cancel):
            asked.append((host, ip))
            return "facts"

        for states, expected in (
            ((protocol_probe.STATE_FAIL, protocol_probe.STATE_OK), True),
            ((protocol_probe.STATE_FAIL, protocol_probe.STATE_FAIL), False),
            ((protocol_probe.STATE_UNKNOWN, protocol_probe.STATE_UNKNOWN), None),
        ):
            # Программы ходят простым приветствием: по нему и судим, строки «Как Chrome» тут нет.
            found = (line(protocol_probe.PROTO_TLS12, states[0]), line(protocol_probe.PROTO_TLS13, states[1]), line(protocol_probe.PROTO_HTTP, "ok"))
            with (
                patch.object(engine, "_tcp_connect", lambda ip, port=443: engine.TcpReport(ip, port, "ok", 5.0)),
                patch.object(protocol_probe, "collect", collect),
                patch.object(protocol_probe, "judge", lambda facts: found),
            ):
                road = engine._program_road("www.youtube.com", V6, engine.SocketCancel())
            self.assertEqual(road.opens, expected)
        self.assertEqual(asked[0], ("www.youtube.com", V6))
        # Без соединения пробы с именем не шлются.
        with patch.object(engine, "_tcp_connect", lambda ip, port=443: engine.TcpReport(ip, port, "timeout")):
            self.assertEqual(engine._program_road("www.youtube.com", V6, engine.SocketCancel()).lines, ())

    def test_hosts_file_reader_returns_both_families_in_file_order(self) -> None:
        import os
        import tempfile

        from utils.windows_dns_query import hosts_file_addresses, hosts_file_ipv4

        handle, path = tempfile.mkstemp(text=True)
        self.addCleanup(os.unlink, path)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write("2001:db8::10 www.youtube.com\n# 1.1.1.1 www.youtube.com\n1.2.3.4 WWW.YouTube.com other.example\nbad www.youtube.com\n")
        self.assertEqual(hosts_file_addresses("www.youtube.com", path=path), ("2001:db8::10", "1.2.3.4"))
        self.assertEqual(hosts_file_ipv4("www.youtube.com", path=path), ("1.2.3.4",))


class WiringTests(unittest.TestCase):
    def test_server_list_is_large_and_has_encrypted_reference(self) -> None:
        with patch("settings.store.get_custom_dns_servers", return_value=[{"id": "1", "name": "Мой", "ipv4": ["10.9.8.7"]}]):
            servers = dns_commands.build_domain_lookup_servers()

        unique = {(server.kind == engine.SERVER_DOH, server.address) for server in servers}
        self.assertGreaterEqual(len(unique), 20)
        self.assertGreaterEqual(sum(1 for server in servers if server.kind == engine.SERVER_DOH), 3)
        self.assertIn(("Мой", "10.9.8.7", engine.SERVER_CUSTOM), [(s.label, s.address, s.kind) for s in servers])
        self.assertEqual(len(engine._unique_servers(servers)), len(unique))
        # Сервер системы подписан именем владельца, а не своим же адресом.
        with patch("utils.windows_dns_query.system_dns_servers", return_value=("8.8.8.8", "10.11.12.13")):
            system = [s for s in dns_commands.build_domain_lookup_servers() if s.kind == engine.SERVER_SYSTEM]
        self.assertEqual([(s.label, s.address) for s in system], [("Google DNS", "8.8.8.8"), ("", "10.11.12.13")])
        # Серверы списка спрашиваются обычным DNS, а Wikimedia и DNS-AI его не принимают.
        labels = {server.label for server in servers}
        self.assertIn("Quad9", labels)
        self.assertFalse({"Wikimedia DNS", "DNS-AI"} & labels)

    def test_worker_receives_action_from_facade(self) -> None:
        feature_source = inspect.getsource(build_dns_feature)
        worker_source = inspect.getsource(domain_lookup_worker)

        self.assertIn("run_domain_lookup=run_domain_lookup", feature_source)
        self.assertNotIn("from dns import commands", worker_source)
        self.assertNotIn("dns.commands", worker_source)
        self.assertNotIn("import dns.public", worker_source)
        self.assertFalse(hasattr(build_dns_feature(), "run_domain_lookup"))

    def test_worker_emits_stages_and_result(self) -> None:
        def fake_run(target, *, use_external, on_stage, should_stop):
            on_stage("partial")
            return (target, use_external, should_stop())

        worker = domain_lookup_worker.DomainLookupWorker(7, target="example.com", use_external=False, run_domain_lookup=fake_run)
        stages, completed = [], []
        worker.stage.connect(lambda request_id, report: stages.append((request_id, report)))
        worker.completed.connect(lambda request_id, report: completed.append((request_id, report)))
        worker.stop()

        worker.run()

        self.assertEqual(stages, [(7, "partial")])
        self.assertEqual(completed, [(7, ("example.com", False, True))])

    def test_worker_reports_failure(self) -> None:
        def broken(*_args, **_kwargs):
            raise OSError("сеть упала")

        worker = domain_lookup_worker.DomainLookupWorker(3, target="x.example", use_external=True, run_domain_lookup=broken)
        failed = []
        worker.failed.connect(lambda request_id, error: failed.append((request_id, error)))

        with patch.object(domain_lookup_worker, "log"):
            worker.run()

        self.assertEqual(failed, [(3, "сеть упала")])


if __name__ == "__main__":
    unittest.main()


class ScreenRowsTests(unittest.TestCase):
    """На экране «Проверки домена» — строки, а не моноширинный текст и таблица."""

    def test_long_neighbour_list_is_cut_on_screen_and_points_to_the_report(self) -> None:
        from types import SimpleNamespace

        from dns import domain_lookup_plans as plans

        source = SimpleNamespace(key=plans.SOURCE_THC, status=plans.SOURCE_OK, names=tuple(f"s{n}.example" for n in range(100)), total=None, extra="", detail="")
        report = SimpleNamespace(primary_ip="1.2.3.4", sources=(source,))
        [group] = plans.build_neighbor_groups(report)

        self.assertEqual(len(group.rows), 1 + plans.NEIGHBOR_ROWS_LIMIT + 1)
        self.assertIn("и ещё 60", group.rows[-1].name)
        self.assertEqual(group.rows[0].name, "Найдено: 100")
        self.assertEqual(plans.build_neighbor_groups(SimpleNamespace(primary_ip="", sources=(source,))), ())
