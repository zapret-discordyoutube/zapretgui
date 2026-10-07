from __future__ import annotations

import threading
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from dns import server_check as sc
from dns.dns_providers import DNS_PROVIDERS
from utils.dns_wire import (
    FAILURE_CANCELLED,
    FAILURE_CERT,
    FAILURE_REFUSED,
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
        self.assertEqual(by_address["194.180.189.33"].doh_port, 444)
        # У этого сервера DoT не подтверждён: проверять его нельзя.
        self.assertEqual(by_address["87.228.47.200"].dot_host, "")

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
        self.assertEqual(net.kinds(sc.PROBE_DOMAIN), ["doh", "dot", "tcp", "udp"])
        self.assertIn(("dot", "8.8.8.8", sc.PROBE_DOMAIN, "dns.google"), net.calls)

    def test_server_without_encrypted_names_skips_those_transports(self) -> None:
        net = _Net()
        row = net.probe(PLAIN)

        self.assertEqual(row.cell(sc.TRANSPORT_DOT).state, sc.STATE_SKIP)
        self.assertEqual(row.cell(sc.TRANSPORT_DOH).state, sc.STATE_SKIP)
        self.assertEqual(net.kinds(sc.PROBE_DOMAIN), ["tcp", "udp"])

    def test_silence_is_retried_once_and_refusal_is_not(self) -> None:
        net = _Net(udp=lambda address, name: TIMEOUT, tcp=lambda address, name: DnsQueryResult(status=STATUS_ERROR, failure=FAILURE_REFUSED))
        row = net.probe(PLAIN)

        self.assertEqual(net.kinds(sc.PROBE_DOMAIN), ["tcp", "udp", "udp"])
        self.assertEqual(row.cell(sc.TRANSPORT_UDP).reason, "сервер молчит")
        self.assertEqual(row.cell(sc.TRANSPORT_TCP).reason, "порт закрыт")

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


class ReportVerdictTests(unittest.TestCase):
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
        self.assertIn("DoT (порт 853) закрыт у: Google DNS (8.8.8.8)", texts)
        self.assertIn("DoH (порт 443) закрыт у: Google DNS (8.8.8.8)", texts)
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
