from __future__ import annotations

import threading
import unittest
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import patch

from dns import server_check as sc
from dns.dns_providers import DNS_PROVIDERS
from utils.dns_wire import (
    FAILURE_CANCELLED,
    FAILURE_CERT,
    FAILURE_REFUSED,
    FAILURE_RESET,
    FAILURE_TIMEOUT,
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_NXDOMAIN,
    STATUS_OK,
    STATUS_TIMEOUT,
    TYPE_A,
    DnsQueryResult,
    DnsRecord,
)
from utils.ip_owner import IpOwner
from utils.windows_icmp import WindowsPingResult

GOOGLE = sc.CheckTarget("Google DNS", "8.8.8.8", dot_host="dns.google", doh_host="dns.google")
QUAD9 = sc.CheckTarget("Quad9", "9.9.9.9", dot_host="dns.quad9.net", doh_host="dns.quad9.net")
PLAIN = sc.CheckTarget("Свой", "10.0.0.53")
# Обычный DNS (порт 53) этот сервер не открывает сам.
WIKIMEDIA = sc.CheckTarget(
    "Wikimedia DNS", "185.71.138.138", dot_host="wikimedia-dns.org", doh_host="wikimedia-dns.org", encrypted_only=True
)

OWNERS = {
    "1.1.1.1": IpOwner(asn="13335", owner="CLOUDFLARENET"),
    "2.2.2.2": IpOwner(asn="15169", owner="GOOGLE"),
    "3.3.3.3": IpOwner(asn="42", owner="WOODYNET"),
    "4.4.4.4": IpOwner(asn="8492", owner="NSDI"),
}


def _answer(*ips: str, ms: float = 10.0) -> DnsQueryResult:
    records = tuple(DnsRecord(name="x", rtype=TYPE_A, ttl=60, value=ip) for ip in ips)
    return DnsQueryResult(status=STATUS_OK if ips else STATUS_EMPTY, rcode=0, records=records, elapsed_ms=ms)


TIMEOUT = DnsQueryResult(status=STATUS_TIMEOUT, failure=FAILURE_TIMEOUT)
NXDOMAIN = DnsQueryResult(status=STATUS_NXDOMAIN, rcode=3, elapsed_ms=5.0)


def _ok(ms: float = 10.0) -> sc.Cell:
    return sc.Cell(state=sc.STATE_OK, elapsed_ms=ms)


def _fail(failure: str = FAILURE_TIMEOUT, reason: str = "сервер молчит") -> sc.Cell:
    return sc.Cell(state=sc.STATE_FAIL, failure=failure, reason=reason)


SKIP = sc.Cell(state=sc.STATE_SKIP)


def _row(target=GOOGLE, *, icmp=None, udp=None, tcp=None, dot=None, doh=None, **extra) -> sc.Observation:
    cells = (
        (sc.TRANSPORT_ICMP, icmp or _ok()),
        (sc.TRANSPORT_UDP, udp or _ok()),
        (sc.TRANSPORT_TCP, tcp or _ok()),
        (sc.TRANSPORT_DOT, dot or _ok()),
        (sc.TRANSPORT_DOH, doh or _ok()),
    )
    return sc.Observation(target=target, cells=cells, **extra)


def _judged(row: sc.Observation) -> sc.Observation:
    return sc.Observation(
        target=row.target,
        cells=row.cells,
        doh_by_address=row.doh_by_address,
        udp_egress=row.udp_egress,
        secure_egress=row.secure_egress,
        secure_via=row.secure_via,
        domains=row.domains,
        findings=sc.judge_row(row, OWNERS.get),
    )


def _codes(findings) -> list[str]:
    return [finding.code for finding in findings]


class TargetTests(unittest.TestCase):
    def test_both_addresses_of_a_server_are_checked(self) -> None:
        targets = sc.build_targets(DNS_PROVIDERS)
        addresses = [target.address for target in targets]

        self.assertIn("8.8.8.8", addresses)
        self.assertIn("8.8.4.4", addresses)
        self.assertEqual(len(addresses), len(set(addresses)), "адрес не должен проверяться дважды")
        self.assertFalse([address for address in addresses if ":" in address])

    def test_ipv6_addresses_are_added_only_on_request(self) -> None:
        addresses = [target.address for target in sc.build_targets(DNS_PROVIDERS, ipv6=True)]

        self.assertIn("2001:4860:4860::8888", addresses)

    def test_encrypted_settings_come_from_the_catalog(self) -> None:
        by_address = {target.address: target for target in sc.build_targets(DNS_PROVIDERS)}

        google = by_address["8.8.4.4"]
        self.assertEqual((google.dot_host, google.doh_host, google.doh_port, google.doh_path), ("dns.google", "dns.google", 443, "/dns-query"))
        # DoH у dnsdoh.art — на обычном порту: прежний 444 молчит.
        self.assertEqual(by_address["194.180.189.33"].doh_port, 443)
        # У Xbox DNS порт 853 принимает только второй адрес: DoT у сервера не проверяется.
        xbox = by_address["111.88.96.54"]
        self.assertEqual((xbox.dot_host, xbox.doh_host), ("", "xbox-dns.ru"))
        # У этого сервера ни DoT, ни DoH не отвечают: проверять их нельзя.
        self.assertEqual((by_address["87.228.47.200"].dot_host, by_address["87.228.47.200"].doh_host), ("", ""))

    def test_doh_port_and_path_come_from_the_template(self) -> None:
        providers = {"Свои": {"Пример": {"ipv4": ["192.0.2.1"], "doh": "https://dns.example.com:444/q"}}}

        (target,) = sc.build_targets(providers)

        self.assertEqual((target.doh_host, target.doh_port, target.doh_path), ("dns.example.com", 444, "/q"))

    def test_servers_without_plain_dns_are_marked(self) -> None:
        by_address = {target.address: target for target in sc.build_targets(DNS_PROVIDERS, ipv6=True)}

        for address in ("185.71.138.138", "192.144.59.14", "186.246.49.127", "2a0a:2b41:0:500d::53"):
            with self.subTest(address=address):
                self.assertTrue(by_address[address].encrypted_only)
        self.assertEqual(by_address["185.71.138.138"], replace(WIKIMEDIA, icon="fa5b.wikipedia-w", color="#94a3b8"))
        self.assertFalse(by_address["9.9.9.9"].encrypted_only)

    def test_users_own_server_has_only_plain_transports(self) -> None:
        targets = sc.build_targets({"Свои": {"Мой": {"ipv4": ["10.0.0.53"], "ipv6": [], "custom_id": "1"}}})

        self.assertEqual(targets, (sc.CheckTarget("Мой", "10.0.0.53"),))

    def test_dot_names_in_catalog_are_host_names(self) -> None:
        for group in DNS_PROVIDERS.values():
            for name, data in group.items():
                dot = data.get("dot")
                if dot is not None:
                    with self.subTest(provider=name):
                        self.assertRegex(dot, r"^[a-z0-9.-]+\.[a-z]{2,}$")


class _Net:
    """Фейковая сеть: ответы задаются по способу связи и по имени."""

    def __init__(self, **overrides) -> None:
        self.calls: list[tuple] = []
        self.lock = threading.Lock()
        self.udp = overrides.get("udp", lambda address, name: _answer("5.5.5.5"))
        self.tcp = overrides.get("tcp", lambda address, name: _answer("5.5.5.5"))
        self.dot = overrides.get("dot", lambda address, name, host: _answer("5.5.5.5"))
        self.doh = overrides.get("doh", lambda address, name, host: _answer("5.5.5.5"))
        self.ping = overrides.get("ping", WindowsPingResult(ok=True, average_ms=7.0, sent=2, received=2))
        self.canary = overrides.get("canary", False)

    def _note(self, *call) -> None:
        with self.lock:
            self.calls.append(call)

    def enter(self, stack: ExitStack) -> None:
        def udp(address, name, _rtype, **_kw):
            self._note("udp", address, name)
            return self.udp(address, name)

        def tcp(address, name, _rtype, **_kw):
            self._note("tcp", address, name)
            return self.tcp(address, name)

        def dot(address, name, _rtype, *, tls_host="", **_kw):
            self._note("dot", address, name, tls_host)
            return self.dot(address, name, tls_host)

        def doh(address, name, _rtype, *, tls_host="", **_kw):
            self._note("doh", address, name, tls_host)
            return self.doh(address, name, tls_host)

        for name, fake in (("query_udp", udp), ("query_tcp", tcp), ("query_dot", dot), ("query_doh", doh)):
            stack.enter_context(patch.object(sc, name, fake))
        stack.enter_context(patch.object(sc, "canary_answered", lambda *_a, **_k: self.canary))
        stack.enter_context(patch("utils.windows_icmp.ping_ipv4_host_winapi", lambda *_a, **_k: self.ping))
        stack.enter_context(patch("utils.windows_icmp.ping_ipv6_winapi", lambda *_a, **_k: self.ping))

    def probe(self, target=GOOGLE) -> sc.Observation:
        from utils.socket_cancel import SocketCancel

        with ExitStack() as stack:
            self.enter(stack)
            return sc.probe_address(target, SocketCancel(), lambda: False)

    def kinds(self, name: str) -> list[str]:
        return sorted(call[0] for call in self.calls if call[2] == name)


class ProbeTests(unittest.TestCase):
    def test_every_transport_is_asked_separately(self) -> None:
        net = _Net()
        row = net.probe()

        self.assertEqual([row.cell(name).state for name in sc.TRANSPORTS], [sc.STATE_OK] * 5)
        self.assertEqual(row.cell(sc.TRANSPORT_ICMP).elapsed_ms, 7.0)
        # Каждым способом — серия запросов: блокировка может включиться не с первого.
        self.assertEqual(net.kinds(sc.PROBE_DOMAIN), sorted(["doh", "dot", "tcp", "udp"] * sc.PROBE_ATTEMPTS))
        self.assertEqual(row.cell(sc.TRANSPORT_DOH).trail, (True,) * sc.PROBE_ATTEMPTS)
        self.assertFalse(row.cell(sc.TRANSPORT_DOH).unstable)
        self.assertIn(("dot", "8.8.8.8", sc.PROBE_DOMAIN, "dns.google"), net.calls)

    def test_server_without_encrypted_names_skips_those_transports(self) -> None:
        net = _Net()
        row = net.probe(PLAIN)

        self.assertEqual(row.cell(sc.TRANSPORT_DOT).state, sc.STATE_SKIP)
        self.assertEqual(row.cell(sc.TRANSPORT_DOH).state, sc.STATE_SKIP)
        self.assertEqual(set(net.kinds(sc.PROBE_DOMAIN)), {"tcp", "udp"})

    def test_server_without_plain_dns_is_not_asked_the_plain_way(self) -> None:
        """Порт 53 у такого сервера закрыт им самим: молчание там — не блокировка провайдера."""
        net = _Net(udp=lambda address, name: TIMEOUT, tcp=lambda address, name: TIMEOUT)
        row = net.probe(WIKIMEDIA)

        for transport in (sc.TRANSPORT_UDP, sc.TRANSPORT_TCP):
            self.assertEqual(row.cell(transport).state, sc.STATE_SKIP)
            self.assertEqual(row.cell(transport).reason, "сервер принимает только шифрованные запросы")
        self.assertEqual(row.cell(sc.TRANSPORT_DOT).state, sc.STATE_OK)
        self.assertEqual(row.cell(sc.TRANSPORT_DOH).state, sc.STATE_OK)
        self.assertEqual({call[0] for call in net.calls}, {"dot", "doh"})
        self.assertEqual(row.secure_via, sc.TRANSPORT_DOH)
        self.assertEqual(_judged(row).findings, ())

    def test_silence_is_retried_once_and_refusal_is_not(self) -> None:
        net = _Net(udp=lambda address, name: TIMEOUT, tcp=lambda address, name: DnsQueryResult(status=STATUS_ERROR, failure=FAILURE_REFUSED))
        row = net.probe(PLAIN)

        self.assertEqual(net.kinds(sc.PROBE_DOMAIN), ["tcp", "udp", "udp"])
        self.assertEqual(row.cell(sc.TRANSPORT_UDP).reason, "сервер молчит")
        self.assertEqual(row.cell(sc.TRANSPORT_TCP).reason, "порт закрыт")

    def test_block_that_starts_after_first_request_is_seen(self) -> None:
        reset = DnsQueryResult(status=STATUS_ERROR, failure=FAILURE_RESET)
        asked: list[str] = []

        def doh(address, name, host):
            if name != sc.PROBE_DOMAIN:
                return _answer("5.5.5.5")
            asked.append(name)
            return _answer("5.5.5.5", ms=40.0) if len(asked) == 1 else reset

        row = _Net(doh=doh).probe()
        cell = row.cell(sc.TRANSPORT_DOH)

        # После срыва серия идёт дальше: второй срыв подряд — уже не случайная потеря.
        self.assertEqual((cell.state, cell.trail, cell.elapsed_ms), (sc.STATE_OK, (True, False, False), 40.0))
        self.assertTrue(cell.unstable and cell.fades)
        self.assertEqual(cell.failure, FAILURE_RESET)
        # Запрос без имени нужен, только когда по имени не ответили с самого начала.
        self.assertIsNone(row.doh_by_address)

    def test_time_is_the_middle_one_of_the_series(self) -> None:
        times = iter((90.0, 10.0, 30.0))

        def tcp(address, name):
            return _answer("5.5.5.5", ms=next(times) if name == sc.PROBE_DOMAIN else 1.0)

        row = _Net(tcp=tcp).probe(PLAIN)

        self.assertEqual(row.cell(sc.TRANSPORT_TCP).elapsed_ms, 30.0)

    def test_silent_address_is_not_waited_for_twice(self) -> None:
        net = _Net(doh=lambda address, name, host: TIMEOUT, tcp=lambda address, name: TIMEOUT)
        row = net.probe()

        # В TCP, DoT и DoH молчание не повторяем: потери там исправляет сама система.
        self.assertEqual(sorted(call[3] for call in net.calls if call[0] == "doh"), ["", "dns.google"])
        self.assertEqual(row.cell(sc.TRANSPORT_DOH).trail, (False,))
        self.assertEqual(row.cell(sc.TRANSPORT_TCP).trail, (False,))
        self.assertTrue(row.doh_by_address.failed)

    def test_slow_answer_by_name_starts_the_request_without_name_early(self) -> None:
        release = threading.Event()
        came_early: list[bool] = []

        def doh(address, name, host):
            if host and name == sc.PROBE_DOMAIN and not release.is_set():
                came_early.append(release.wait(2))
                return TIMEOUT
            if not host:
                release.set()
            return _answer("5.5.5.5")

        with patch.object(sc, "DOH_WITHOUT_NAME_AFTER_S", 0.05):
            row = _Net(doh=doh).probe()

        # Запрос без имени пришёл, пока первый по имени ещё висел, а не после него.
        self.assertEqual(came_early, [True])
        self.assertTrue(row.cell(sc.TRANSPORT_DOH).failed)
        self.assertTrue(row.doh_by_address.ok)

    def test_doh_is_tried_without_name_only_when_name_may_be_blocked(self) -> None:
        def blocked_by_name(address, name, host):
            return TIMEOUT if host else _answer("5.5.5.5")

        row = _Net(doh=blocked_by_name).probe()
        self.assertTrue(row.cell(sc.TRANSPORT_DOH).failed)
        self.assertTrue(row.doh_by_address.ok)
        self.assertEqual(row.secure_via, sc.TRANSPORT_DOH)

        wrong_cert = DnsQueryResult(status=STATUS_ERROR, failure=FAILURE_CERT)
        net = _Net(doh=lambda address, name, host: wrong_cert)
        row = net.probe()
        # Чужой сертификат — это уже ответ на вопрос, запрос без имени ничего не добавит.
        self.assertIsNone(row.doh_by_address)
        self.assertEqual(row.secure_via, sc.TRANSPORT_DOT)

    def test_who_answers_and_sensitive_sites_are_asked_both_ways(self) -> None:
        def udp(address, name):
            if name == sc.WHOAMI_DOMAIN:
                return _answer("4.4.4.4")
            return NXDOMAIN if name == "rutor.info" else _answer("5.5.5.5")

        def doh(address, name, host):
            return _answer("2.2.2.2") if name == sc.WHOAMI_DOMAIN else _answer("6.6.6.6")

        row = _Net(udp=udp, doh=doh).probe()

        self.assertEqual((row.udp_egress, row.secure_egress, row.secure_via), ("4.4.4.4", "2.2.2.2", sc.TRANSPORT_DOH))
        facts = {fact.domain: fact for fact in row.domains}
        self.assertEqual(set(facts), set(sc.SENSITIVE_DOMAINS))
        self.assertEqual((facts["rutor.info"].udp_status, facts["rutor.info"].secure_ips), (STATUS_NXDOMAIN, ("6.6.6.6",)))

    def test_nothing_to_compare_without_plain_or_without_other_path(self) -> None:
        net = _Net(udp=lambda address, name: TIMEOUT)
        row = net.probe()
        self.assertEqual((row.udp_egress, row.domains), ("", ()))
        self.assertEqual(net.kinds(sc.WHOAMI_DOMAIN), [])

        dead = lambda *_a: TIMEOUT  # noqa: E731
        net = _Net(tcp=dead, dot=dead, doh=dead)
        row = net.probe()
        self.assertEqual((row.secure_via, row.domains), ("", ()))

    def test_ping_unavailable_in_system_is_skip_not_failure(self) -> None:
        row = _Net(ping=WindowsPingResult(ok=False, sent=2, received=0, error_code="UNSUPPORTED")).probe(PLAIN)

        self.assertEqual(row.cell(sc.TRANSPORT_ICMP).state, sc.STATE_SKIP)

    def test_cancelled_query_is_unknown_not_failure(self) -> None:
        cancelled = DnsQueryResult(status=STATUS_ERROR, failure=FAILURE_CANCELLED)
        row = _Net(tcp=lambda address, name: cancelled).probe(PLAIN)

        self.assertEqual(row.cell(sc.TRANSPORT_TCP).state, sc.STATE_UNKNOWN)


class RowVerdictTests(unittest.TestCase):
    def _findings(self, row: sc.Observation):
        return sc.judge_row(row, OWNERS.get)

    def test_healthy_server_has_no_findings_even_without_ping(self) -> None:
        self.assertEqual(self._findings(_row(icmp=_fail())), ())

    def test_server_silent_everywhere_is_dead_and_ping_is_mentioned(self) -> None:
        dead = _row(udp=_fail(), tcp=_fail(), dot=_fail(), doh=_fail())
        (finding,) = self._findings(dead)
        self.assertEqual((finding.level, finding.code), (sc.LEVEL_FAIL, sc.CODE_DEAD))
        self.assertIn("на пинг при этом отвечает", finding.text)

        no_ping = _row(icmp=_fail(), udp=_fail(), tcp=_fail(), dot=SKIP, doh=SKIP)
        self.assertNotIn("пинг", self._findings(no_ping)[0].text)

    def test_server_contradicting_itself_is_spoofed(self) -> None:
        for udp in ({"udp_status": STATUS_NXDOMAIN}, {"udp_status": STATUS_OK, "udp_ips": ("195.82.146.214",)}):
            with self.subTest(udp=udp):
                fact = sc.DomainFact("rutor.info", secure_status=STATUS_OK, secure_ips=("6.6.6.6",), **udp)
                findings = self._findings(_row(domains=(fact,)))
                self.assertEqual(_codes(findings), [sc.CODE_SPOOFED])
                self.assertEqual(findings[0].level, sc.LEVEL_FAIL)
                self.assertIn("rutor.info", findings[0].text)

    def test_no_spoofing_verdict_without_genuine_answer_to_compare(self) -> None:
        facts = (
            # Сайта и правда может не быть: шифрованным путём сервер тоже ничего не дал.
            sc.DomainFact("rutor.info", udp_status=STATUS_NXDOMAIN, secure_status=STATUS_NXDOMAIN),
            sc.DomainFact("flibusta.is", udp_status=STATUS_NXDOMAIN, secure_status=STATUS_TIMEOUT),
            # Молчание — не подмена: сервер мог долго искать ответ.
            sc.DomainFact("rezka.ag", udp_status=STATUS_TIMEOUT, secure_status=STATUS_OK, secure_ips=("6.6.6.6",)),
            # Разные настоящие адреса — обычное дело у крупных сайтов.
            sc.DomainFact("cdn.example", udp_status=STATUS_OK, udp_ips=("7.7.7.7",), secure_status=STATUS_OK, secure_ips=("6.6.6.6",)),
        )

        self.assertEqual(self._findings(_row(domains=facts)), ())

    def test_closed_transports_are_named_with_reason(self) -> None:
        findings = self._findings(_row(udp=_fail(), dot=_fail(FAILURE_REFUSED, "порт закрыт"), doh=_fail()))

        self.assertEqual(_codes(findings), [sc.CODE_UDP_BLOCKED, sc.CODE_DOT_BLOCKED, sc.CODE_DOH_BLOCKED])
        self.assertIn("порт закрыт", findings[1].text)

    def test_tcp_alone_is_only_a_note(self) -> None:
        (finding,) = self._findings(_row(tcp=_fail()))

        self.assertEqual((finding.level, finding.code), (sc.LEVEL_INFO, sc.CODE_TCP_BLOCKED))

    def test_doh_answering_without_name_means_name_is_blocked(self) -> None:
        (finding,) = self._findings(_row(doh=_fail(), doh_by_address=_ok()))

        self.assertEqual(finding.code, sc.CODE_DOH_NAME_BLOCKED)
        self.assertIn("dns.google", finding.text)

    def test_different_networks_for_plain_and_encrypted_is_a_note_not_alarm(self) -> None:
        (finding,) = self._findings(_row(udp_egress="4.4.4.4", secure_egress="2.2.2.2"))
        self.assertEqual((finding.level, finding.code), (sc.LEVEL_INFO, sc.CODE_FOREIGN_ANSWERS))
        self.assertIn("NSDI", finding.text)

        self.assertEqual(self._findings(_row(udp_egress="2.2.2.2", secure_egress="2.2.2.2")), ())
        # Владелец неизвестен — сравнивать нечего.
        self.assertEqual(self._findings(_row(udp_egress="9.9.9.99", secure_egress="2.2.2.2")), ())


    def test_server_answering_every_other_time_is_noted(self) -> None:
        lost_once = sc.Cell(state=sc.STATE_OK, elapsed_ms=9.0, failure=FAILURE_TIMEOUT, reason="сервер молчит", trail=(True, False, True))
        (finding,) = self._findings(_row(udp=lost_once))
        # Один потерянный запрос — шум сети, а не тревога.
        self.assertEqual((finding.level, finding.code), (sc.LEVEL_INFO, sc.CODE_UNSTABLE))
        self.assertIn("обычный DNS (UDP) — 2 из 3 запросов (сервер молчит)", finding.text)
        self.assertNotIn("Первые запросы проходят", finding.text)

        # Два срыва из трёх — уже не случайность, даже если это просто молчание.
        silent_twice = sc.Cell(state=sc.STATE_OK, elapsed_ms=9.0, failure=FAILURE_TIMEOUT, reason="сервер молчит", trail=(True, False, False))
        self.assertEqual(self._findings(_row(tcp=silent_twice))[0].level, sc.LEVEL_WARN)

        cut = sc.Cell(state=sc.STATE_OK, elapsed_ms=40.0, failure=FAILURE_RESET, reason="соединение оборвано", trail=(True, False, False))
        (finding,) = self._findings(_row(doh=cut))
        self.assertEqual((finding.level, finding.code), (sc.LEVEL_WARN, sc.CODE_UNSTABLE))
        self.assertIn("DoH — 1 из 3 запросов", finding.text)
        self.assertIn("Первые запросы проходят, а следующие уже нет.", finding.text)


    def test_server_refusing_a_site_even_over_encrypted_path_filters_it_itself(self) -> None:
        refused = sc.DomainFact("rutor.info", udp_status=STATUS_NXDOMAIN, secure_status=STATUS_NXDOMAIN)
        stub = sc.DomainFact("rezka.ag", udp_status=STATUS_OK, udp_ips=("0.0.0.0",), secure_status=STATUS_OK, secure_ips=("0.0.0.0",))
        row = _row(domains=(refused, stub))

        # Сайта может и правда не быть: без подтверждения от другого сервера вывода нет.
        self.assertEqual(sc.judge_row(row, OWNERS.get), ())
        self.assertEqual(sc.judge_row(row, OWNERS.get, frozenset({"flibusta.is"})), ())

        (finding,) = sc.judge_row(row, OWNERS.get, frozenset({"rutor.info", "rezka.ag"}))
        # Это не подмена по дороге и не тревога: шифрованный ответ подменить нельзя.
        self.assertEqual((finding.level, finding.code), (sc.LEVEL_INFO, sc.CODE_SELF_FILTER))
        self.assertIn("rutor.info, rezka.ag", finding.text)
        self.assertIn("решение самого сервера", finding.text)

    def test_resolvable_sites_are_those_some_server_really_resolved(self) -> None:
        honest = _row(GOOGLE, domains=(sc.DomainFact("rutor.info", secure_status=STATUS_OK, secure_ips=("6.6.6.6",)),))
        stub = _row(QUAD9, domains=(sc.DomainFact("rezka.ag", secure_status=STATUS_OK, secure_ips=("0.0.0.0",)),))

        self.assertEqual(sc.resolvable_domains((honest, stub)), frozenset({"rutor.info"}))


class ReportVerdictTests(unittest.TestCase):
    def test_single_lost_requests_are_counted_not_listed(self) -> None:
        lost = sc.Cell(state=sc.STATE_OK, elapsed_ms=9.0, failure=FAILURE_TIMEOUT, reason="сервер молчит", trail=(True, False, True))
        cut = sc.Cell(state=sc.STATE_OK, elapsed_ms=5.0, failure=FAILURE_RESET, reason="соединение оборвано", trail=(True, False, False))
        rows = (_judged(_row(GOOGLE, udp=lost)), _judged(_row(QUAD9, doh=cut)), _judged(_row(PLAIN, udp=lost, dot=SKIP, doh=SKIP)))
        shaky = [finding for finding in sc.judge_report(rows, False, OWNERS.get) if finding.code == sc.CODE_UNSTABLE]

        # Настоящий срыв назван по имени; случайные потери — одной строкой с числом, без списка.
        self.assertEqual([finding.level for finding in shaky], [sc.LEVEL_WARN, sc.LEVEL_INFO])
        self.assertIn("Quad9 (9.9.9.9)", shaky[0].text)
        self.assertNotIn("Google", shaky[0].text)
        self.assertIn("Потеряли по одному запросу: 2 из 3 адресов", shaky[1].text)

    def test_self_filtering_servers_are_named_once_and_not_recommended(self) -> None:
        refused = sc.Finding(sc.LEVEL_INFO, sc.CODE_SELF_FILTER, "сам не отдаёт адреса сайтов: rutor.info")
        rows = (
            _row(GOOGLE, doh=_ok(5.0), findings=(refused,)),
            _row(sc.CheckTarget("Google DNS", "8.8.4.4"), doh=_ok(6.0), findings=(refused,)),
            _row(QUAD9, doh=_ok(80.0)),
        )
        findings = sc.judge_report(rows, False, OWNERS.get)

        named = next(finding for finding in findings if finding.code == sc.CODE_SELF_FILTER)
        self.assertEqual(named.level, sc.LEVEL_INFO)
        self.assertEqual(named.text.count("Google DNS"), 1)
        # Быстрый, но фильтрующий сервер лучшим не называется.
        best = next(finding for finding in findings if finding.code == sc.CODE_BEST)
        self.assertIn("Quad9 (9.9.9.9)", best.text)

    def test_shaky_servers_are_named_and_not_recommended(self) -> None:
        cut = sc.Cell(state=sc.STATE_OK, elapsed_ms=5.0, failure=FAILURE_RESET, reason="соединение оборвано", trail=(True, False, False))
        rows = (_judged(_row(GOOGLE, doh=cut)), _judged(_row(QUAD9, doh=_ok(80.0))))
        findings = sc.judge_report(rows, False, OWNERS.get)

        shaky = next(finding for finding in findings if finding.code == sc.CODE_UNSTABLE)
        self.assertEqual(shaky.level, sc.LEVEL_WARN)
        self.assertIn("Google DNS (8.8.8.8)", shaky.text)
        self.assertIn("не с первого запроса", shaky.text)
        # Быстрый, но отвечающий через раз сервер советовать нельзя.
        best = next(finding for finding in findings if finding.code == sc.CODE_BEST)
        self.assertIn("Quad9 (9.9.9.9)", best.text)
        self.assertIn("Без замечаний: 1 из 2", best.text)

    def _report(self, rows, canary=False):
        rows = tuple(_judged(row) for row in rows)
        return sc.judge_report(rows, canary, OWNERS.get)

    def test_clean_network_recommends_fastest_encrypted_server(self) -> None:
        findings = self._report([_row(GOOGLE, doh=_ok(40.0)), _row(QUAD9, doh=_ok(15.0))])

        self.assertEqual(_codes(findings), [sc.CODE_BEST])
        self.assertIn("Quad9 (9.9.9.9)", findings[0].text)
        self.assertIn("15 мс", findings[0].text)
        self.assertIn("2 из 2", findings[0].text)

    def test_canary_answer_is_interception(self) -> None:
        findings = self._report([_row()], canary=True)

        self.assertEqual((findings[0].level, findings[0].code), (sc.LEVEL_FAIL, sc.CODE_INTERCEPTED))
        self.assertIn("где DNS-сервера нет", findings[0].text)

    def test_one_network_answering_for_different_owners_is_interception(self) -> None:
        """Случай из поста: запросы к Google и Quad9 уводят на общий сервер."""
        findings = self._report(
            [
                _row(GOOGLE, udp_egress="4.4.4.4", secure_egress="2.2.2.2"),
                _row(QUAD9, udp_egress="4.4.4.4", secure_egress="3.3.3.3"),
            ]
        )

        self.assertEqual(findings[0].code, sc.CODE_INTERCEPTED)
        self.assertIn("NSDI", findings[0].text)
        self.assertIn("Google DNS, Quad9", findings[0].text)

    def test_two_names_of_one_owner_are_not_interception(self) -> None:
        """Живой случай: Xbox DNS и Xbox DNS v2 сами выполняют обычные запросы через чужую сеть."""
        xbox = sc.CheckTarget("Xbox DNS", "111.88.96.54", doh_host="xbox-dns.ru")
        xbox_v2 = sc.CheckTarget("Xbox DNS v2", "87.228.47.200", doh_host="xbox-dns.ru")
        findings = self._report(
            [
                _row(xbox, udp_egress="4.4.4.4", secure_egress="1.1.1.1"),
                _row(xbox_v2, udp_egress="4.4.4.4", secure_egress="1.1.1.1"),
            ]
        )

        self.assertNotIn(sc.CODE_INTERCEPTED, _codes(findings))

    def test_transport_closed_everywhere_is_said_once(self) -> None:
        third = sc.CheckTarget("OpenDNS", "208.67.222.222", dot_host="dns.opendns.com", doh_host="doh.opendns.com")
        findings = self._report([_row(target, dot=_fail()) for target in (GOOGLE, QUAD9, third)])

        dot = next(finding for finding in findings if "DoT" in finding.text)
        self.assertIn("закрыт целиком", dot.text)
        self.assertIn("ни один из 3", dot.text)
        self.assertNotIn("8.8.8.8", dot.text)

    def test_transport_closed_for_some_servers_names_them(self) -> None:
        findings = self._report([_row(GOOGLE, doh=_fail(), dot=_fail()), _row(QUAD9)])

        texts = " ".join(finding.text for finding in findings)
        self.assertIn("DoT (порт 853) закрыт у части серверов: Google DNS (8.8.8.8)", texts)
        self.assertIn("DoH (порт 443) закрыт у части серверов: Google DNS (8.8.8.8)", texts)
        # Сервер с закрытым шифрованием для защищённого DNS не предлагается.
        best = next(finding for finding in findings if finding.code == sc.CODE_BEST)
        self.assertIn("Quad9", best.text)
        self.assertIn("1 из 2", best.text)

    def test_spoofing_server_is_never_recommended(self) -> None:
        fact = sc.DomainFact("rutor.info", udp_status=STATUS_NXDOMAIN, secure_status=STATUS_OK, secure_ips=("6.6.6.6",))
        findings = self._report([_row(GOOGLE, domains=(fact,))])

        self.assertEqual(_codes(findings), [sc.CODE_SPOOFED])

    def test_dead_servers_are_listed_as_note(self) -> None:
        findings = self._report([_row(GOOGLE, udp=_fail(), tcp=_fail(), dot=_fail(), doh=_fail()), _row(QUAD9)])

        dead = next(finding for finding in findings if finding.code == sc.CODE_DEAD)
        self.assertEqual(dead.level, sc.LEVEL_INFO)
        self.assertIn("Google DNS (8.8.8.8)", dead.text)

    def test_running_bypass_tools_are_named_first_as_a_caveat(self) -> None:
        rows = (_judged(_row(GOOGLE)),)
        findings = sc.judge_report(rows, True, OWNERS.get, ("Zapret", "Xray"))

        self.assertEqual((findings[0].level, findings[0].code), (sc.LEVEL_INFO, sc.CODE_BYPASS_RUNNING))
        self.assertIn("Zapret, Xray", findings[0].text)
        # Оговорка не отменяет остальные выводы.
        self.assertIn(sc.CODE_INTERCEPTED, _codes(findings))

    def test_findings_carry_ready_parts_with_the_whole_server_list(self) -> None:
        spoofed = sc.Finding(sc.LEVEL_FAIL, sc.CODE_SPOOFED, "обычные ответы подменяются")
        targets = [sc.CheckTarget("Cloudflare", f"1.1.1.{index}") for index in range(1, 7)]
        rows = tuple(_row(target, doh=_ok(20.0), findings=(spoofed,)) for target in targets) + (_row(QUAD9, doh=_ok(80.0)),)
        findings = sc.judge_report(rows, False, OWNERS.get)

        named = next(finding for finding in findings if finding.code == sc.CODE_SPOOFED)
        # Фраза для журнала обрезана, а перечень для экрана — полный.
        self.assertEqual(
            named.text,
            "Обычные ответы подменяются у серверов: Cloudflare (1.1.1.1), Cloudflare (1.1.1.2), "
            "Cloudflare (1.1.1.3), Cloudflare (1.1.1.4) и ещё 2.",
        )
        self.assertEqual(named.title, "Обычные ответы подменяются у серверов")
        self.assertEqual(named.servers, tuple(("Cloudflare", f"1.1.1.{index}") for index in range(1, 7)))
        self.assertEqual(named.note, "")
        best = next(finding for finding in findings if finding.code == sc.CODE_BEST)
        self.assertEqual((best.title, best.servers), ("Для защищённого DNS сейчас лучше всего подходит", (("Quad9", "9.9.9.9"),)))
        self.assertEqual(best.note, "Шифрованный запрос проходит за 80 мс. Без замечаний: 1 из 7 адресов.")

    def test_every_listing_finding_has_title_servers_and_note(self) -> None:
        cut = sc.Cell(state=sc.STATE_OK, elapsed_ms=5.0, failure=FAILURE_RESET, reason="соединение оборвано", trail=(True, False, False))
        refused = sc.Finding(sc.LEVEL_INFO, sc.CODE_SELF_FILTER, "сам не отдаёт адреса сайтов: rutor.info")
        second = sc.CheckTarget("Google DNS", "8.8.4.4")
        rows = (
            _judged(_row(GOOGLE, dot=_fail(), doh=cut)),
            _row(second, findings=(refused,)),
            _judged(_row(QUAD9, udp=_fail(), tcp=_fail(), dot=_fail(), doh=_fail())),
            _judged(_row(PLAIN, udp=_fail(), dot=SKIP, doh=SKIP)),
        )
        parts = {finding.title: (finding.servers, finding.note) for finding in sc.judge_report(rows, False, OWNERS.get)}

        self.assertEqual(parts["Шифрованный DNS по DoT (порт 853) закрыт у части серверов"], ((("Google DNS", "8.8.8.8"),), ""))
        self.assertEqual(parts["Обычный DNS (UDP, порт 53) закрыт у части серверов"], (((PLAIN.provider, PLAIN.address),), ""))
        self.assertEqual(
            parts["Отвечают через раз"],
            ((("Google DNS", "8.8.8.8"),), "Так бывает, когда блокировка включается не с первого запроса."),
        )
        # Во фразе фильтрующий сервис назван один раз, а в перечне — его адреса.
        servers, note = parts["Сами не отдают часть сайтов"]
        self.assertEqual(servers, (("Google DNS", "8.8.4.4"),))
        self.assertIn("решение самих серверов", note)
        self.assertEqual(parts["Не отвечают совсем"], ((("Quad9", "9.9.9.9"),), ""))

    def test_findings_without_a_list_keep_the_explanation_as_a_note(self) -> None:
        dead = [_judged(_row(sc.CheckTarget("Cloudflare", f"1.1.1.{index}"), dot=_fail())) for index in range(1, 4)]
        findings = sc.judge_report(tuple(dead), True, OWNERS.get, ("Zapret",))
        parts = {finding.code: finding for finding in findings}

        bypass = parts[sc.CODE_BYPASS_RUNNING]
        self.assertEqual((bypass.title, bypass.servers), ("Во время проверки работали", ()))
        self.assertTrue(bypass.note.startswith("Zapret. Такие программы"))
        caught = parts[sc.CODE_INTERCEPTED]
        self.assertEqual(caught.title, "Обычные DNS-запросы перехватываются по дороге (провайдером или роутером)")
        self.assertTrue(caught.note.startswith("Ответил адрес, где DNS-сервера нет."))
        closed = next(finding for finding in findings if "закрыт целиком" in finding.text)
        self.assertEqual(
            (closed.title, closed.servers, closed.note),
            ("Шифрованный DNS по DoT (порт 853) закрыт целиком", (), "Не ответил ни один из 3 адресов."),
        )
        # Заголовок и пояснение вместе не теряют ничего из фразы.
        for finding in findings:
            self.assertTrue(finding.title, finding.text)
            self.assertTrue(finding.text.startswith(finding.title), finding.text)

    def test_dead_servers_alone_do_not_get_all_clear(self) -> None:
        findings = self._report([_row(PLAIN, udp=_fail(), tcp=_fail(), dot=SKIP, doh=SKIP)])

        self.assertEqual(_codes(findings), [sc.CODE_DEAD])

    def test_servers_without_encryption_and_without_problems_are_fine(self) -> None:
        findings = self._report([_row(PLAIN, dot=SKIP, doh=SKIP)])

        self.assertEqual(_codes(findings), [sc.CODE_ALL_FINE])


class RunTests(unittest.TestCase):
    def _run(self, net: _Net, targets=(GOOGLE, QUAD9), **kwargs):
        owners = lambda ip, _ask: OWNERS.get(ip)  # noqa: E731
        with ExitStack() as stack:
            net.enter(stack)
            stack.enter_context(patch.object(sc, "lookup_ip_owner", owners))
            return sc.run_server_check(targets, **kwargs)

    def test_report_is_complete_and_progress_is_published(self) -> None:
        seen: list[sc.ServerCheckReport] = []
        report = self._run(_Net(), on_progress=seen.append)

        self.assertTrue(report.finished)
        self.assertFalse(report.stopped or report.timed_out)
        self.assertEqual([row.target for row in report.rows], [GOOGLE, QUAD9])
        self.assertEqual((report.total, report.canary), (2, False))
        self.assertEqual(_codes(report.findings), [sc.CODE_BEST])
        self.assertEqual(seen[0].rows, ())
        self.assertFalse(seen[0].finished)
        self.assertIs(seen[-1], report)

    def test_interception_is_found_end_to_end(self) -> None:
        def udp(address, name):
            return _answer("4.4.4.4") if name == sc.WHOAMI_DOMAIN else _answer("5.5.5.5")

        def doh(address, name, host):
            if name != sc.WHOAMI_DOMAIN:
                return _answer("5.5.5.5")
            return _answer("2.2.2.2" if address == "8.8.8.8" else "3.3.3.3")

        report = self._run(_Net(udp=udp, doh=doh, canary=True))

        self.assertEqual(report.findings[0].code, sc.CODE_INTERCEPTED)
        self.assertEqual(report.owner_of("4.4.4.4").owner, "NSDI")
        self.assertIn(sc.CODE_FOREIGN_ANSWERS, _codes(report.rows[0].findings))

    def test_stop_ends_run_without_conclusions(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)

        def hanging(address, name):
            release.wait(5)
            return DnsQueryResult(status=STATUS_ERROR, failure=FAILURE_CANCELLED)

        calls = iter([False, False] + [True] * 1000)
        report = self._run(_Net(udp=hanging), should_stop=lambda: next(calls))

        self.assertTrue(report.finished and report.stopped)
        self.assertEqual(report.findings, ())

    def test_bypass_caveat_reaches_report_and_its_text(self) -> None:
        from dns import server_check_plans as plans

        report = self._run(_Net(), bypass=["Zapret"])

        self.assertEqual(report.bypass, ("Zapret",))
        self.assertEqual(report.findings[0].code, sc.CODE_BYPASS_RUNNING)
        self.assertIn("Работали во время проверки: Zapret", plans.build_text_report(report))

    def test_empty_list_finishes_at_once(self) -> None:
        report = self._run(_Net(), targets=())

        self.assertTrue(report.finished)
        self.assertEqual((report.rows, report.findings), ((), ()))


if __name__ == "__main__":
    unittest.main()
