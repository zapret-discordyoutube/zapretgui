from __future__ import annotations

import inspect
import json
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

        def http_spy(method, url, **kwargs):
            self.http_calls.append(url)
            return http(method, url, **kwargs)

        ping = WindowsPingResult(ok=True, average_ms=20.0, sent=4, received=3, min_ms=10.0, max_ms=30.0, ttl=57)
        patches = [
            patch.object(engine, "query_server", _fake_query),
            patch.object(engine, "query_doh", _fake_query),
            patch.object(engine, "canary_answered", lambda _domain: False),
            patch.object(engine, "trace_route", lambda ip, **_k: self.route(ip)),
            patch.object(engine, "_inspect_quic", lambda domain, ip, max_ttl, cancel: self.quic(domain, ip)),
            patch.object(engine, "_http", http_spy),
            patch.object(engine, "fetch_cert_names", lambda ip, server_name=None: CertNames("ok", ("cert.example",), "cn.example")),
            patch.object(engine, "_tcp_connect", lambda ip, port=443: engine.TcpReport(ip, port, "ok", 5.0)),
            patch("utils.windows_icmp.ping_ipv4_host_winapi", lambda *a, **k: ping),
            patch("utils.windows_icmp.ping_ipv6_winapi", lambda *a, **k: ping),
            patch("utils.net_resolve.resolve_ips", lambda host, **k: (["93.184.216.34"], [])),
        ]
        for item in patches:
            item.start()
            test.addCleanup(item.stop)


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

    def test_long_lists_are_cut_on_screen_but_not_in_report(self) -> None:
        names = tuple(f"host{index}.example" for index in range(plans.NEIGHBORS_SHOWN_LIMIT + 5))
        report = engine.DomainLookupReport(
            target="1.2.3.4",
            kind=engine.KIND_IP,
            primary_ip="1.2.3.4",
            sources=(engine.NeighborSource(engine.SOURCE_THC, engine.SOURCE_OK, names=names, total=9000),),
        )

        screen = plans.build_neighbors_text(report)

        self.assertIn(f"показано {len(names)} из 9000", screen)
        self.assertIn("и ещё 5", screen)
        self.assertNotIn(names[-1], screen)
        self.assertIn(names[-1], plans.build_text_report(report))


class WiringTests(unittest.TestCase):
    def test_server_list_is_large_and_has_encrypted_reference(self) -> None:
        with patch("settings.store.get_custom_dns_servers", return_value=[{"id": "1", "name": "Мой", "ipv4": ["10.9.8.7"]}]):
            servers = dns_commands.build_domain_lookup_servers()

        unique = {(server.kind == engine.SERVER_DOH, server.address) for server in servers}
        self.assertGreaterEqual(len(unique), 20)
        self.assertGreaterEqual(sum(1 for server in servers if server.kind == engine.SERVER_DOH), 3)
        self.assertIn(("Мой", "10.9.8.7", engine.SERVER_CUSTOM), [(s.label, s.address, s.kind) for s in servers])
        self.assertEqual(len(engine._unique_servers(servers)), len(unique))
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
