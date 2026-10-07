from __future__ import annotations

import socket
import threading
import time
import unittest
from unittest.mock import patch

import diagnostics.engine as engine
from diagnostics import net_access, sections
from diagnostics.tls_probe import (
    KIND_CERT,
    KIND_CONNECT,
    KIND_OK,
    KIND_RESET,
    KIND_TIMEOUT,
    KIND_TLS,
    ProbeResult,
    https_get,
)
from diagnostics.verdict import (
    DnsState,
    Level,
    ReachState,
    TargetOutcome,
    judge_dns,
    judge_reach,
    summarize_service,
)
from utils.dns_wire import (
    FAILURE_CANCELLED,
    FAILURE_TIMEOUT,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_TIMEOUT,
    DnsQueryResult,
    DnsRecord,
)
from utils.socket_cancel import SocketCancel
from utils.windows_dns_query import DNS_STATUS_NAME_ERROR, DnsAnswer

DISCORD_REAL = ("162.159.137.232", "162.159.138.232")
# Адрес прокси разблокирующего DNS (Comss/Xbox DNS и т. п.).
UNBLOCK_PROXY = ("83.220.169.155",)


def _ok(ip: str = DISCORD_REAL[0], **extra) -> ProbeResult:
    return ProbeResult(ip=ip, kind=KIND_OK, status=200, tls_version="TLSv1.3", **extra)


class JudgeDnsTests(unittest.TestCase):
    def _judge(self, **overrides):
        values = dict(
            system_ips=DISCORD_REAL,
            system_status=0,
            reference_ips=DISCORD_REAL,
            hosts_ips=(),
            check_kind=KIND_OK,
        )
        values.update(overrides)
        return judge_dns(**values)

    def test_same_addresses_as_reference_are_ok(self) -> None:
        self.assertEqual(self._judge(check_kind="").state, DnsState.OK)

    def test_unblocking_dns_proxy_with_real_certificate_is_not_spoofing(self) -> None:
        judgement = self._judge(system_ips=UNBLOCK_PROXY, check_kind=KIND_OK)

        self.assertEqual(judgement.state, DnsState.OK)
        self.assertIn("не подмена", judgement.reason)

    def test_foreign_certificate_on_system_address_is_spoofing(self) -> None:
        judgement = self._judge(
            system_ips=("5.6.7.8",),
            check_kind=KIND_CERT,
            check_cert_problem="сертификат выдан другому сайту",
        )

        self.assertEqual(judgement.state, DnsState.SPOOFED)
        self.assertIn("другому сайту", judgement.reason)

    def test_dpi_reset_on_different_address_is_not_called_spoofing(self) -> None:
        judgement = self._judge(system_ips=("5.6.7.8",), check_kind=KIND_RESET)

        self.assertEqual(judgement.state, DnsState.UNKNOWN)

    def test_stub_and_private_addresses_are_spoofing(self) -> None:
        for ip in ("195.82.146.214", "127.0.0.1", "0.0.0.0", "10.10.10.10"):
            with self.subTest(ip=ip):
                self.assertEqual(self._judge(system_ips=(ip,)).state, DnsState.SPOOFED)

    def test_home_network_address_is_a_home_filter_not_the_provider(self) -> None:
        """Роутер с родительским контролем, Pi-hole, AdGuard Home отвечают адресом домашней сети."""
        for ip in ("192.168.1.1", "10.0.0.5", "172.16.3.9"):
            with self.subTest(ip=ip):
                judgement = self._judge(system_ips=(ip,))
                self.assertEqual(judgement.state, DnsState.LOCAL)
                self.assertIn("это не провайдер", judgement.reason)

    def test_vpn_fake_ip_is_local_not_spoofing(self) -> None:
        self.assertEqual(self._judge(system_ips=("198.18.0.12",)).state, DnsState.LOCAL)

    def test_hosts_file_entry_is_reported_as_local(self) -> None:
        judgement = self._judge(hosts_ips=("1.2.3.4",), system_ips=("1.2.3.4",), check_kind=KIND_CERT)

        self.assertEqual(judgement.state, DnsState.LOCAL)
        self.assertIn("hosts", judgement.reason)

    def test_occasional_nxdomain_with_real_addresses_is_spoofing(self) -> None:
        # Хватает и одного раза из трёх: исправный DNS про существующий сайт так не отвечает,
        # а у пользователя провайдер подсовывал «сайта нет» именно через раз.
        judgement = self._judge(check_kind="", nxdomain_count=1, attempts=3)

        self.assertEqual(judgement.state, DnsState.SPOOFED)
        self.assertIn("1 из 3", judgement.reason)

    def test_nxdomain_for_existing_site_is_dns_block(self) -> None:
        judgement = self._judge(system_ips=(), system_status=DNS_STATUS_NAME_ERROR, check_kind="")

        self.assertEqual(judgement.state, DnsState.SPOOFED)

    def test_silent_dns_is_unknown(self) -> None:
        judgement = self._judge(system_ips=(), system_status=1460, reference_ips=(), check_kind="")

        self.assertEqual(judgement.state, DnsState.UNKNOWN)


class ReachTests(unittest.TestCase):
    def test_reach_states(self) -> None:
        self.assertEqual(judge_reach(_ok()), ReachState.OK)
        self.assertEqual(judge_reach(None), ReachState.NO_ADDRESS)
        for kind in (KIND_RESET, KIND_TLS, KIND_TIMEOUT):
            with self.subTest(kind=kind):
                self.assertEqual(judge_reach(ProbeResult(ip="1.1.1.1", kind=kind)), ReachState.DPI)
        self.assertEqual(judge_reach(ProbeResult(ip="1.1.1.1", kind=KIND_CONNECT)), ReachState.IP_BLOCK)
        self.assertEqual(judge_reach(ProbeResult(ip="1.1.1.1", kind=KIND_CERT)), ReachState.CERT)

    def test_body_cut_at_16kb_is_freeze_but_short_page_is_ok(self) -> None:
        self.assertEqual(judge_reach(_ok(body_cut=True, body_size=16_500)), ReachState.FREEZE)
        self.assertEqual(judge_reach(_ok(body_cut=True, body_size=3_000)), ReachState.OK)
        self.assertEqual(judge_reach(_ok(body_size=16_500)), ReachState.OK)


class ServiceSummaryTests(unittest.TestCase):
    def _outcomes(self, main: ReachState, *others: ReachState, dns: DnsState = DnsState.OK):
        items = [TargetOutcome("www.youtube.com", "сайт", main, dns, main=True)]
        for index, state in enumerate(others):
            items.append(TargetOutcome(f"host{index}", f"часть {index}", state, DnsState.OK))
        return items

    def test_site_that_opens_is_never_red_even_with_dns_block(self) -> None:
        verdict = summarize_service(
            "YouTube",
            self._outcomes(ReachState.OK, ReachState.OK, dns=DnsState.SPOOFED),
            zapret_running=True,
        )

        self.assertEqual(verdict.level, Level.WARN)
        self.assertEqual(verdict.headline, "YouTube открывается")
        self.assertIn("www.youtube.com", verdict.dns_note)
        self.assertTrue(any("DoH" in item for item in verdict.advice))

    def test_everything_open_is_ok(self) -> None:
        verdict = summarize_service("Discord", self._outcomes(ReachState.OK, ReachState.OK), zapret_running=True)

        self.assertEqual(verdict.level, Level.OK)
        self.assertEqual(verdict.advice, ())

    def test_main_blocked_by_dpi_with_zapret_running_suggests_strategy(self) -> None:
        verdict = summarize_service("Discord", self._outcomes(ReachState.DPI), zapret_running=True)

        self.assertEqual(verdict.level, Level.FAIL)
        self.assertIn("не обходит", verdict.headline)
        self.assertIn("Подбор стратегии", verdict.advice[0])

    def test_main_blocked_without_zapret_suggests_start(self) -> None:
        verdict = summarize_service("Discord", self._outcomes(ReachState.DPI), zapret_running=False)

        self.assertIn("Запустите Zapret", verdict.advice[0])

    def test_broken_secondary_part_is_named(self) -> None:
        verdict = summarize_service(
            "YouTube", self._outcomes(ReachState.OK, ReachState.FREEZE), zapret_running=True
        )

        self.assertEqual(verdict.level, Level.WARN)
        self.assertIn("часть 0", verdict.headline)


class _Done:
    """Уже готовый результат вместо Future: проверка обрыва без пула потоков."""

    def __init__(self, value) -> None:
        self._value = value

    def result(self, timeout=None):
        return self._value


class _Net:
    """Фейковая сеть для движка: DNS, эталон и HTTPS по адресу."""

    def __init__(self, *, system=DISCORD_REAL, reference=DISCORD_REAL, status=0, https=None, reference_v6=(), voice=(), freeze=(), cause_facts=None):
        self.voice = voice
        self.freeze = freeze
        self.system = system
        self.reference = reference
        self.reference_v6 = reference_v6
        self.status = status
        self.https = https or (lambda host, ip: _ok(ip))
        self.calls: list[tuple[str, str]] = []
        # Что «показали» дополнительные пробы: по умолчанию ничего не выяснили.
        self.cause_facts = cause_facts
        self.refined: list[str] = []
        self.bypass_tools: tuple[str, ...] = ()
        # Что «показал» QUIC: по умолчанию проверку будто сняли — вывода нет.
        self.quic_facts = None
        self.quic_asked: list[tuple[str, str]] = []
        # Что «показал» набор объёма по одному соединению: по умолчанию обрыва нет.
        self.volume_facts = None
        self.volume_asked: list[str] = []
        self.network: dict = {"external_ip": "", "provider": "", "lines": []}
        self.telegram: tuple = ()
        self.speed: dict | None = None
        # TLS 1.2 / TLS 1.3 / HTTP по отдельности: по умолчанию проверку будто сняли.
        self.protocol_facts = None
        self.protocols_asked: list[tuple[str, str]] = []
        self.ipv6 = sections.ipv6_check.Ipv6Verdict(sections.ipv6_check.IPV6_ABSENT, "в этой сети его нет")
        # Состояние системы читает реестр и службы: в сценариях движка оно задаётся явно.
        self.system_items: tuple = ()

    def _quic(self, host, ip, **_kwargs):
        self.quic_asked.append((host, ip))
        if self.quic_facts is not None:
            return self.quic_facts(host, ip)
        return engine.quic_probe.QuicFacts(host=host, cancelled=True)

    def _protocols(self, host, ip, **_kwargs):
        self.protocols_asked.append((host, ip))
        if self.protocol_facts is not None:
            return self.protocol_facts(host, ip)
        return engine.protocol_probe.ProtocolFacts(host=host, ip=ip, cancelled=True)

    def _volume(self, host, ip, _path, **_kwargs):
        self.volume_asked.append(host)
        if self.volume_facts is not None:
            return self.volume_facts(host, ip)
        return engine.volume_probe.VolumeFacts(engine.volume_probe.VolumeRun(engine.volume_probe.RUN_PASSED, 33_000, 4))

    def _collect(self, host, result, **_kwargs):
        self.refined.append(host)
        if self.cause_facts is not None:
            return self.cause_facts(host, result)
        return engine.block_cause.CauseFacts(host=host, result=result)

    def patches(self):
        def _https_get(host, ip, *_args, **_kwargs):
            self.calls.append((host, ip))
            return self.https(host, ip)

        return (
            patch.object(
                net_access,
                "query_ipv4",
                side_effect=lambda host, **_kw: (
                    self.system(host) if callable(self.system) else DnsAnswer(ips=self.system, status=self.status)
                ),
            ),
            patch.object(
                net_access,
                "doh_lookup",
                side_effect=lambda _run, _host, record_type=engine.DNS_TYPE_A: (
                    True,
                    self.reference_v6 if record_type == engine.DNS_TYPE_AAAA else self.reference,
                ),
            ),
            patch.object(net_access, "https_get", side_effect=_https_get),
            # Пауза перед повтором нужна настоящей сети; сценариям она только добавляет секунды.
            patch.object(engine, "RETRY_PAUSE_S", 0.0),
            # Уточнение причины ходит в сеть само: в сценариях движка оно подменено.
            patch.object(engine.block_cause, "collect", side_effect=self._collect),
            patch.object(engine.quic_probe, "collect", side_effect=self._quic),
            patch.object(engine.volume_probe, "collect", side_effect=self._volume),
            patch.object(engine.protocol_probe, "collect", side_effect=self._protocols),
            patch.object(sections, "check_ipv6", side_effect=lambda _run: self.ipv6),
            # «Ваша сеть» и дата-центры Telegram ходят в сеть сами: в сценариях движка их нет.
            patch.object(sections, "check_network", side_effect=lambda _run, _tools: self.network),
            patch.object(sections, "check_speed", side_effect=lambda _run, _emit: self.speed),
            patch.object(engine.telegram_check, "check_telegram", side_effect=lambda *_a, **_k: self.telegram),
            patch.object(sections, "check_system", side_effect=lambda _run, _services: self.system_items),
            patch.object(net_access, "hosts_file_ipv4", return_value=()),
            patch.object(net_access, "system_dns_servers", return_value=("83.220.169.155",)),
            patch.object(sections, "zapret_status", return_value=(True, "✅ Zapret запущен")),
            patch.object(engine, "running_bypass_tools", return_value=self.bypass_tools),
            patch.object(engine, "_discover_googlevideo", return_value=(("rr1---sn-test.googlevideo.com",), "")),
            # Звонки и обрыв 16 КБ проверяются в любом режиме — без сети в тестах.
            patch("diagnostics.voice_check.check_voice", return_value=self.voice),
            patch("diagnostics.freeze_check.check_freeze", return_value=self.freeze),
        )

    def run(self, fn, *args, **kwargs):
        from contextlib import ExitStack

        with ExitStack() as stack:
            for item in self.patches():
                stack.enter_context(item)
            return fn(*args, **kwargs)


class ReferenceResolverTests(unittest.TestCase):
    """Эталон собирается с нескольких серверов; закрытый провайдером сервер виден в отчёте."""

    @staticmethod
    def _answer(rtype, *ips) -> DnsQueryResult:
        records = tuple(DnsRecord(name="x", rtype=rtype, ttl=60, value=ip) for ip in ips)
        return DnsQueryResult(status=STATUS_OK, rcode=0, records=records, elapsed_ms=10.0)

    def _run(self, doh, fn, *args, **kwargs):
        from contextlib import ExitStack

        net = _Net()
        with ExitStack() as stack:
            for item in net.patches():
                if getattr(item, "attribute", "") != "doh_lookup":
                    stack.enter_context(item)
            stack.enter_context(patch.object(net_access, "query_doh", doh))
            return fn(*args, **kwargs)

    def _doh(self, *, silent=(), cancelled=()):
        def doh(server, _host, rtype, **_kwargs):
            if server in silent:
                return DnsQueryResult(status=STATUS_TIMEOUT, failure=FAILURE_TIMEOUT)
            if server in cancelled:
                return DnsQueryResult(status=STATUS_ERROR, failure=FAILURE_CANCELLED)
            return self._answer(rtype, *DISCORD_REAL) if rtype == engine.DNS_TYPE_A else self._answer(rtype)

        return doh

    def test_blocked_reference_server_is_named_with_reason_and_dns_button(self) -> None:
        lines: list[str] = []
        result = self._run(self._doh(silent=("8.8.8.8",)), engine.run_blockcheck, "main", emit=lines.append)

        state = {item["address"]: item for item in result["reference"]}
        self.assertFalse(state["8.8.8.8"]["ok"])
        self.assertEqual(state["8.8.8.8"]["reason"], "сервер молчит")
        self.assertTrue(state["1.1.1.1"]["ok"])
        problem = next(item for item in result["problems"] if "8.8.8.8" in item["text"])
        self.assertEqual((problem["level"], problem["action"]), ("warn", "dns"))
        self.assertIn("Шифрованный DNS Google (8.8.8.8) недоступен: сервер молчит", "\n".join(lines))
        # Остальные серверы ответили — эталон есть, сайты проверены как обычно.
        self.assertIn("Discord", result["working"])

    def test_dns_tab_also_names_blocked_reference_server(self) -> None:
        lines: list[str] = []
        result = self._run(self._doh(silent=("8.8.8.8",)), engine.run_dns_check, emit=lines.append)

        self.assertIn("Шифрованный DNS Google (8.8.8.8) недоступен", "\n".join(lines))
        self.assertFalse(result["summary"]["dns_poisoning_detected"])
        self.assertFalse({item["address"]: item["ok"] for item in result["reference"]}["8.8.8.8"])

    def test_when_every_reference_is_silent_no_single_server_is_blamed(self) -> None:
        """Молчат все — это не «провайдер закрыл Google», а нет связи вовсе."""
        everyone = tuple(resolver.address for resolver in engine.REFERENCE_RESOLVERS)
        lines: list[str] = []
        result = self._run(self._doh(silent=everyone), engine.run_blockcheck, "main", emit=lines.append)

        self.assertTrue(all(not item["ok"] for item in result["reference"]))
        self.assertFalse([item for item in result["problems"] if "Шифрованный DNS" in item["text"]])
        self.assertIn("эталон: недоступен", "\n".join(lines))

    def test_query_stopped_by_time_limit_says_nothing_about_the_server(self) -> None:
        result = self._run(self._doh(cancelled=("8.8.8.8",)), engine.run_blockcheck, "main", emit=lambda _line: None)

        self.assertNotIn("8.8.8.8", {item["address"] for item in result["reference"]})
        self.assertFalse([item for item in result["problems"] if "Шифрованный DNS" in item["text"]])

    def test_answers_of_all_reference_servers_are_merged(self) -> None:
        by_server = {"1.1.1.1": ("1.1.1.10",), "8.8.8.8": ("8.8.8.80", "1.1.1.10")}

        def doh(server, _host, rtype, **_kwargs):
            return self._answer(rtype, *by_server.get(server, ()))

        run = engine._Run(None, workers=8)
        try:
            with patch.object(net_access, "query_doh", doh):
                answered, ips = net_access.doh_lookup(run, "example.com")
        finally:
            run.close()

        self.assertTrue(answered)
        self.assertEqual(ips, ("1.1.1.10", "8.8.8.80"))


class BlockCauseInReportTests(unittest.TestCase):
    """Сайт не открылся: в отчёте сказано, режут по имени или по адресу."""

    @staticmethod
    def _reset_on_hello(host, ip):
        return ProbeResult(ip=ip, kind=KIND_RESET, stage="tls")

    def test_block_by_name_is_shown_in_report_and_first_in_advice(self) -> None:
        def facts(host, result):
            return engine.block_cause.CauseFacts(
                host=host,
                result=result,
                neutral=engine.block_cause.HelloResult(engine.block_cause.HELLO_OK),
                nameless=engine.block_cause.HelloResult(engine.block_cause.HELLO_RESET),
            )

        lines: list[str] = []
        net = _Net(https=self._reset_on_hello, cause_facts=facts)
        result = net.run(engine.run_blockcheck, "main", emit=lines.append)

        self.assertIn("   🔎 Блокировка по имени сайта: с именем discord.com соединение обрывается", "\n".join(lines))
        discord = next(item for item in result["services"] if item["key"] == "discord")
        main = next(item for item in discord["targets"] if item["main"])
        self.assertEqual(main["cause"], "by_name")
        self.assertTrue(main["cause_text"].startswith("Блокировка по имени сайта"))
        problem = next(item for item in result["problems"] if item["target"] == "discord.com")
        self.assertTrue(problem["advice"][0].startswith("Блокировка по имени сайта"))
        # От блокировки по имени стратегия помогает: кнопка подбора остаётся.
        self.assertEqual(problem["action"], "strategy")

    def test_working_site_is_not_probed_again(self) -> None:
        net = _Net()
        result = net.run(engine.run_blockcheck, "main", emit=lambda _line: None)

        self.assertEqual(net.refined, [])
        self.assertTrue(all(target["cause"] == "" for item in result["services"] for target in item["targets"]))

    def test_unclear_probes_add_nothing_to_report(self) -> None:
        lines: list[str] = []
        net = _Net(https=self._reset_on_hello)
        result = net.run(engine.run_blockcheck, "main", emit=lines.append)

        self.assertIn("discord.com", net.refined)
        self.assertNotIn("🔎", "\n".join(lines))
        problem = next(item for item in result["problems"] if item["target"] == "discord.com")
        self.assertIn("Подбор стратегии", problem["advice"][0])

    def test_other_bypass_tools_are_named_before_results(self) -> None:
        lines: list[str] = []
        net = _Net()
        net.bypass_tools = ("Xray", "Cloudflare WARP")
        result = net.run(engine.run_blockcheck, "main", emit=lines.append)

        note = next(line for line in lines if "Xray" in line)
        self.assertIn("Работают другие программы обхода или VPN: Xray, Cloudflare WARP", note)
        self.assertLess(lines.index(note), next(i for i, line in enumerate(lines) if line.startswith("━━━━━━━━ Discord")))
        self.assertEqual(result["other_bypass_tools"], ["Xray", "Cloudflare WARP"])


class QuicInReportTests(unittest.TestCase):
    """QUIC проверяется по тому же адресу, что и сайт, и не путается с блокировкой сайта."""

    @staticmethod
    def _facts(blocked=(), silent=()):
        def facts(host, _ip):
            if host in blocked:
                return engine.quic_probe.QuicFacts(host=host, neutral_ms=40.0, nameless_ms=41.0)
            if host in silent:
                return engine.quic_probe.QuicFacts(host=host)
            return engine.quic_probe.QuicFacts(host=host, real_ms=12.0, neutral_ms=13.0, nameless_ms=13.0)

        return facts

    def test_working_quic_is_shown_and_is_not_a_problem(self) -> None:
        lines: list[str] = []
        net = _Net()
        net.quic_facts = self._facts()
        result = net.run(engine.run_blockcheck, "main", emit=lines.append)

        self.assertIn("   ✅ QUIC (UDP 443): отвечает за 12 мс", lines)
        self.assertFalse([item for item in result["problems"] if "QUIC" in item["text"]])
        discord = next(item for item in result["services"] if item["key"] == "discord")
        self.assertEqual({target["quic"] for target in discord["targets"]}, {"ok"})
        # Пакеты идут на тот же адрес, к которому шёл основной запрос.
        self.assertIn(("discord.com", DISCORD_REAL[0]), net.quic_asked)

    def test_quic_blocked_for_open_site_is_one_warning_naming_services(self) -> None:
        lines: list[str] = []
        net = _Net()
        net.quic_facts = self._facts(blocked={"www.youtube.com", "i.ytimg.com", "discord.com"})
        result = net.run(engine.run_blockcheck, "main", emit=lines.append)
        text = "\n".join(lines)

        self.assertIn("❌ QUIC (UDP 443): блокируется по имени сайта: пакет с именем discord.com пропадает", text)
        problems = [item for item in result["problems"] if "QUIC" in item["text"]]
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["level"], "warn")
        self.assertIn("для: Discord, YouTube.", problems[0]["text"])
        self.assertIn("UDP 443", problems[0]["advice"][0])
        # Сами сайты открываются: «не открывается» про них не пишется.
        self.assertEqual(sorted(result["working"]), ["Discord", "YouTube"])

    def test_server_without_quic_is_a_note_not_a_problem(self) -> None:
        lines: list[str] = []
        net = _Net()
        net.quic_facts = self._facts(silent={"discord.com"})
        result = net.run(engine.run_blockcheck, "main", emit=lines.append)

        note = next(line for line in lines if "QUIC" in line and "не поддерживает" in line)
        self.assertTrue(note.startswith("   ℹ️ "))
        self.assertFalse([item for item in result["problems"] if "QUIC" in item["text"]])

    def test_blocked_site_gets_site_problem_not_extra_quic_one(self) -> None:
        net = _Net(https=lambda host, ip: ProbeResult(ip=ip, kind=KIND_RESET, stage="tls"))
        net.quic_facts = self._facts(blocked={"discord.com", "gateway.discord.gg", "cdn.discordapp.com"})
        result = net.run(engine.run_blockcheck, "main", emit=lambda _line: None)

        self.assertFalse([item for item in result["problems"] if item["text"].startswith("QUIC")])
        self.assertTrue([item for item in result["problems"] if item["target"] == "discord.com"])

    def test_dns_tab_does_not_send_quic_packets(self) -> None:
        net = _Net()
        net.quic_facts = self._facts()
        net.run(engine.run_dns_check, emit=lambda _line: None)

        self.assertEqual(net.quic_asked, [])


class Ipv6InReportTests(unittest.TestCase):
    def _run(self, code: str, text: str):
        lines: list[str] = []
        net = _Net()
        net.ipv6 = sections.ipv6_check.Ipv6Verdict(code, text)
        return net.run(engine.run_blockcheck, "main", emit=lines.append), lines

    def test_broken_ipv6_is_a_warning_with_advice(self) -> None:
        result, lines = self._run(sections.ipv6_check.IPV6_BROKEN, "настроен, но не работает")

        self.assertIn("⚠️ IPv6 настроен, но не работает", lines)
        problem = next(item for item in result["problems"] if item["text"].startswith("IPv6"))
        self.assertEqual(problem["level"], "warn")
        self.assertIn("с задержкой", problem["advice"][0])
        self.assertEqual(result["ipv6"], {"state": "broken", "text": "настроен, но не работает"})

    def test_absent_and_working_ipv6_are_only_lines_in_report(self) -> None:
        for code, icon in ((sections.ipv6_check.IPV6_ABSENT, "ℹ️"), (sections.ipv6_check.IPV6_OK, "✅")):
            with self.subTest(code=code):
                result, lines = self._run(code, "текст")
                self.assertIn(f"{icon} IPv6 текст", lines)
                self.assertFalse([item for item in result["problems"] if item["text"].startswith("IPv6")])

    def test_ipv6_section_comes_after_sites_and_before_summary(self) -> None:
        _result, lines = self._run(sections.ipv6_check.IPV6_OK, "работает")
        section = lines.index("━━━━━━━━ IPv6 ━━━━━━━━")

        self.assertGreater(section, next(i for i, line in enumerate(lines) if line.startswith("━━━━━━━━ YouTube")))
        self.assertLess(section, lines.index("━━━━━━━━ 📊 Итог ━━━━━━━━"))


class FreezeUploadWiringTests(unittest.TestCase):
    """Отправка проверяется на том же адресе, по которому шла загрузка."""

    def _run(self):
        run = engine._Run(None, workers=4)
        self.addCleanup(run.close)
        return run

    def test_address_is_found_once_and_reused_for_upload(self) -> None:
        run = self._run()
        asked: list = []

        def collect(host, ip, path, **_kwargs):
            asked.append((host, ip, path))
            return sections.upload_probe.UploadFacts(sections.upload_probe.PostResult(sections.upload_probe.POST_STALLED))

        with (
            patch.object(net_access, "hosts_file_ipv4", return_value=("203.0.113.9",)) as hosts,
            patch.object(net_access, "https_get", return_value=_ok("203.0.113.9")) as get,
            patch.object(sections.upload_probe, "collect", collect),
        ):
            sections.download(run, "cdn.example", "/file.bin")
            verdict = sections.upload(run, "cdn.example", "/file.bin")

        self.assertEqual(hosts.call_count, 1)
        self.assertEqual(get.call_args.args[:2], ("cdn.example", "203.0.113.9"))
        self.assertEqual(asked, [("cdn.example", "203.0.113.9", "/file.bin")])
        self.assertEqual(verdict.code, sections.upload_probe.UPLOAD_UNKNOWN)

    def test_no_address_or_stopped_run_means_no_upload_probe(self) -> None:
        run = self._run()
        with (
            patch.object(net_access, "hosts_file_ipv4", return_value=()),
            patch.object(net_access, "query_ipv4", return_value=DnsAnswer()),
            patch.object(net_access, "doh_lookup", return_value=(False, ())),
            patch.object(sections.upload_probe, "collect") as collect,
        ):
            self.assertIsNone(sections.upload(run, "nowhere.example", "/"))
        collect.assert_not_called()

        stopped = engine._Run(lambda: True, workers=4)
        self.addCleanup(stopped.close)
        with (
            patch.object(net_access, "hosts_file_ipv4", return_value=("203.0.113.9",)),
            patch.object(sections.upload_probe, "collect") as collect,
        ):
            self.assertIsNone(sections.upload(stopped, "cdn.example", "/"))
        collect.assert_not_called()


class SystemStateInReportTests(unittest.TestCase):
    @staticmethod
    def _item(key, level, text, advice=""):
        return engine.system_state.SystemItem(key, f"Заголовок {key}", level, text, advice)

    def _run(self, *items, https=None):
        lines: list[str] = []
        net = _Net(https=https)
        net.system_items = tuple(items)
        return net.run(engine.run_blockcheck, "main", emit=lines.append), lines

    def test_every_item_is_printed_and_returned(self) -> None:
        ok = self._item("admin", "ok", "есть")
        note = self._item("antivirus", "info", "работает Kaspersky", "Добавьте в исключения")
        result, lines = self._run(ok, note)

        self.assertIn("━━━━━━━━ Состояние системы ━━━━━━━━", lines)
        self.assertIn("✅ Заголовок admin: есть", lines)
        self.assertIn("ℹ️ Заголовок antivirus: работает Kaspersky", lines)
        self.assertEqual([item["key"] for item in result["system"]], ["admin", "antivirus"])
        self.assertEqual(result["system"][1]["advice"], "Добавьте в исключения")
        # Пометки и «всё хорошо» в список проблем не попадают.
        self.assertFalse([item for item in result["problems"] if item["text"].startswith("Заголовок")])

    def test_failures_and_warnings_become_problems_with_advice(self) -> None:
        result, _lines = self._run(
            self._item("bfe", "fail", "не работает", "Запустите службу"),
            self._item("proxy", "warn", "включён"),
            self._item("clock", "unknown", "проверить не удалось"),
        )
        problems = {item["text"]: item for item in result["problems"] if item["text"].startswith("Заголовок")}

        self.assertEqual(set(problems), {"Заголовок bfe: не работает", "Заголовок proxy: включён"})
        self.assertEqual(problems["Заголовок bfe: не работает"]["level"], "fail")
        self.assertEqual(problems["Заголовок bfe: не работает"]["advice"], ["Запустите службу"])
        self.assertEqual(problems["Заголовок proxy: включён"]["advice"], [])
        # Неполадка компьютера стоит выше предупреждений о сети.
        self.assertEqual(result["problems"][0]["text"], "Заголовок bfe: не работает")

    def test_system_problem_is_shown_even_when_nothing_opens(self) -> None:
        """Остановленная служба сама может быть причиной «нет интернета» — молчать о ней нельзя."""
        net = _Net(https=lambda host, ip: ProbeResult(ip=ip, kind=KIND_CONNECT))
        net.system_items = (self._item("bfe", "fail", "не работает"),)
        result = net.run(engine.run_blockcheck, "all", emit=lambda _line: None)

        self.assertTrue([item for item in result["problems"] if item["text"] == "Заголовок bfe: не работает"])

    def test_no_section_when_nothing_was_collected(self) -> None:
        result, lines = self._run()

        self.assertNotIn("━━━━━━━━ Состояние системы ━━━━━━━━", lines)
        self.assertEqual(result["system"], [])


class ClockSkewTests(unittest.TestCase):
    def _skew(self, body: bytes | None, addresses=("142.250.1.1",)):
        run = engine._Run(None, workers=4)
        self.addCleanup(run.close)
        result = ProbeResult(ip="142.250.1.1", kind=KIND_OK, status=204, body=body or b"")
        with (
            patch.object(net_access, "doh_lookup", return_value=(bool(addresses), tuple(addresses))),
            patch.object(net_access, "https_get", return_value=result) as get,
            patch.object(sections.time, "time", return_value=1_800_000_000.0),
        ):
            return sections.clock_skew(run), get

    def test_skew_is_local_time_minus_server_date(self) -> None:
        from email.utils import formatdate

        body = b"HTTP/1.1 204 No Content\r\nDate: " + formatdate(1_800_000_000 - 7200, usegmt=True).encode() + b"\r\n\r\n"
        skew, get = self._skew(body)

        self.assertEqual(skew, 7200.0)
        self.assertEqual(get.call_args.args[:3], ("www.google.com", "142.250.1.1", "/generate_204"))

    def test_no_address_no_date_or_bad_date_is_unknown(self) -> None:
        self.assertIsNone(self._skew(b"", addresses=())[0])
        self.assertIsNone(self._skew(b"HTTP/1.1 204 No Content\r\nServer: x\r\n\r\n")[0])
        self.assertIsNone(self._skew(b"HTTP/1.1 204 No Content\r\nDate: yesterday\r\n\r\n")[0])
        # Строка «Date:» в теле ответа — не заголовок.
        self.assertIsNone(self._skew(b"HTTP/1.1 200 OK\r\nServer: x\r\n\r\nDate: Mon, 01 Jan 2024 00:00:00 GMT")[0])


class FullCheckTests(unittest.TestCase):
    """«Полная проверка»: всё из «Всех сайтов» плюс DNS-серверы и место фильтра."""

    DNS_OK = {
        "level": "warn",
        "findings": [
            {"level": "fail", "text": "Обычные DNS-запросы перехватываются по дороге."},
            {"level": "info", "text": "Не отвечают совсем: Xbox DNS (old)."},
            {"level": "ok", "text": "Для защищённого DNS сейчас лучше всего подходит Cloudflare (1.1.1.1)."},
        ],
        "text": "Сервер  Адрес\nCloudflare  1.1.1.1",
    }

    def _run(self, scope="full", *, dns=None, quic_blocked=(), trace=None, filter_facts=None, https=None):
        from diagnostics import path_trace

        lines: list[str] = []
        calls: dict = {"dns": 0, "trace": [], "locate": []}

        def check_dns(*, should_stop=None):
            calls["dns"] += 1
            return dns if dns is not None else self.DNS_OK

        def fake_trace(ip, **_kwargs):
            calls["trace"].append(ip)
            return trace or path_trace.RouteTrace(target=ip, supported=False)

        def fake_locate(ip, name, *, max_ttl, cancel):
            calls["locate"].append((ip, name, max_ttl))
            return filter_facts or path_trace.FilterFacts(True, True, 6, 6)

        net = _Net(https=https)
        net.quic_facts = lambda host, ip: (
            engine.quic_probe.QuicFacts(host=host, neutral_ms=40.0)
            if host in quic_blocked
            else engine.quic_probe.QuicFacts(host=host, real_ms=10.0)
        )
        from contextlib import ExitStack

        with ExitStack() as stack:
            for item in net.patches():
                stack.enter_context(item)
            stack.enter_context(patch.object(path_trace, "trace_route", fake_trace))
            stack.enter_context(patch.object(path_trace, "locate_filter", fake_locate))
            result = engine.run_blockcheck(scope, emit=lines.append, check_dns_servers=check_dns)
        return result, lines, calls

    def test_full_scope_checks_all_sites_and_dns_servers(self) -> None:
        result, lines, calls = self._run()

        self.assertEqual(result["scope"], "full")
        self.assertIn("🔍 BlockCheck: Полная проверка", lines[0])
        self.assertIn("telegram", {item["key"] for item in result["services"]})
        self.assertEqual(calls["dns"], 1)
        self.assertEqual(result["dns_servers"]["level"], "warn")
        self.assertIn("━━━━━━━━ DNS-серверы ━━━━━━━━", lines)
        self.assertIn("❌ Обычные DNS-запросы перехватываются по дороге.", lines)
        self.assertIn("   Cloudflare  1.1.1.1", lines)

    def test_dns_findings_that_matter_become_problems_with_dns_button(self) -> None:
        result, _lines, _calls = self._run()
        dns_problems = [item for item in result["problems"] if "DNS" in item["text"] and item["action"] == "dns"]

        self.assertEqual([item["text"] for item in dns_problems], ["Обычные DNS-запросы перехватываются по дороге."])
        self.assertEqual(dns_problems[0]["level"], "fail")
        # Пометки и «лучший сервер» в список проблем не идут.
        self.assertFalse([item for item in result["problems"] if "Xbox DNS" in item["text"] or "лучше всего" in item["text"]])

    def test_filter_place_is_searched_for_site_with_quic_blocked_by_name(self) -> None:
        from diagnostics import path_trace
        from utils.windows_icmp import HOP_ROUTER

        trace = path_trace.RouteTrace(
            DISCORD_REAL[0], tuple(path_trace.Hop(ttl, HOP_ROUTER, f"10.0.0.{ttl}", 1.0) for ttl in range(1, 8)), reached=True
        )
        result, lines, calls = self._run(quic_blocked={"discord.com"}, trace=trace)

        self.assertEqual(calls["locate"], [(DISCORD_REAL[0], "discord.com", sections.FILTER_MAX_TTL)])
        self.assertEqual(calls["trace"], [DISCORD_REAL[0]])
        place = result["filter"]
        self.assertEqual((place["host"], place["found"], place["hop"]), ("discord.com", True, 6))
        self.assertEqual(place["text"], "Фильтр стоит между узлом 5 (10.0.0.5) и узлом 6 (10.0.0.6)")
        self.assertEqual(len(place["hops"]), 7)
        self.assertIn("📍 По сайту discord.com: фильтр стоит между узлом 5 (10.0.0.5) и узлом 6 (10.0.0.6)", lines)

    def test_no_filter_search_when_quic_is_not_blocked(self) -> None:
        result, lines, calls = self._run()

        self.assertEqual((calls["locate"], calls["trace"]), ([], []))
        self.assertIsNone(result["filter"])
        self.assertNotIn("━━━━━━━━ Где стоит фильтр ━━━━━━━━", lines)

    def test_filter_is_searched_once_even_if_many_sites_are_blocked(self) -> None:
        _result, _lines, calls = self._run(quic_blocked={"discord.com", "www.youtube.com", "telegram.org"})

        self.assertEqual(len(calls["locate"]), 1)

    def test_other_scopes_do_not_run_the_extra_checks(self) -> None:
        for scope in ("all", "main"):
            with self.subTest(scope=scope):
                result, lines, calls = self._run(scope, quic_blocked={"discord.com"})
                self.assertEqual((calls["dns"], calls["locate"]), (0, []))
                self.assertIsNone(result["dns_servers"])
                self.assertIsNone(result["filter"])

    def test_unknown_scope_falls_back_to_main(self) -> None:
        result, _lines, _calls = self._run("что-то")

        self.assertEqual(result["scope"], "main")

    def test_broken_dns_check_does_not_break_the_rest(self) -> None:
        lines: list[str] = []

        def broken(*, should_stop=None):
            raise OSError("нет сети")

        net = _Net()
        result = net.run(engine.run_blockcheck, "full", emit=lines.append, check_dns_servers=broken)

        self.assertIsNone(result["dns_servers"])
        self.assertIn("❔ DNS-серверы: проверка не выполнилась (нет сети)", lines)
        self.assertIn("Discord", result["working"])

    def test_full_check_without_dns_function_still_runs(self) -> None:
        net = _Net()
        result = net.run(engine.run_blockcheck, "full", emit=lambda _line: None)

        self.assertEqual(result["scope"], "full")
        self.assertIsNone(result["dns_servers"])


class EngineScenarioTests(unittest.TestCase):
    """Движок целиком, сеть подменена фейками."""

    def test_unblocking_dns_is_not_reported_as_spoofing(self) -> None:
        lines: list[str] = []
        net = _Net(system=UNBLOCK_PROXY)
        result = net.run(engine.run_dns_check, emit=lines.append)
        text = "\n".join(lines)

        self.assertFalse(result["summary"]["dns_poisoning_detected"])
        self.assertNotIn("Обнаружена DNS подмена", text)
        self.assertIn("Comss DNS", text)
        self.assertIn("сам меняет адреса", text)

    def test_dns_that_answers_nxdomain_only_sometimes_is_still_spoofing(self) -> None:
        """У пользователя вкладка DNS то находила подмену, то нет: провайдер отвечал «сайта нет» не каждый раз."""
        answers = iter([
            DnsAnswer(ips=(), status=DNS_STATUS_NAME_ERROR),
            DnsAnswer(ips=DISCORD_REAL),
            DnsAnswer(ips=DISCORD_REAL),
        ] * 10)
        lines: list[str] = []
        net = _Net()
        with patch.object(net_access, "query_ipv4", side_effect=lambda *_a, **_k: next(answers)):
            patches = [item for item in net.patches() if getattr(item, "attribute", "") != "query_ipv4"]
            from contextlib import ExitStack

            with ExitStack() as stack:
                for item in patches:
                    stack.enter_context(item)
                result = engine.run_dns_check(emit=lines.append)

        self.assertTrue(result["summary"]["dns_poisoning_detected"])
        self.assertIn("то правильно, то «такого сайта нет»", "\n".join(lines))

    def test_dns_tab_skips_https_when_address_matches_reference(self) -> None:
        net = _Net()
        net.run(engine.run_dns_check, emit=lambda _line: None)

        self.assertEqual(net.calls, [])

    def test_foreign_certificate_is_reported_as_spoofing(self) -> None:
        lines: list[str] = []
        net = _Net(
            system=("5.6.7.8",),
            https=lambda host, ip: ProbeResult(ip=ip, kind=KIND_CERT, cert_problem="сертификат выдан другому сайту"),
        )
        result = net.run(engine.run_dns_check, emit=lines.append)

        self.assertTrue(result["summary"]["dns_poisoning_detected"])
        self.assertIn("Обнаружена DNS подмена", "\n".join(lines))

    def test_nxdomain_site_that_opens_by_real_address_is_reported_as_working(self) -> None:
        """Случай из жалобы: DNS говорит «домена нет», браузер с DoH открывает сайт."""
        lines: list[str] = []
        net = _Net(system=(), status=DNS_STATUS_NAME_ERROR, reference=("142.251.157.4",))
        result = net.run(engine.run_blockcheck, "main", emit=lines.append)
        text = "\n".join(lines)

        youtube = next(item for item in result["services"] if item["key"] == "youtube")
        self.assertEqual(youtube["headline"], "YouTube открывается")
        self.assertEqual(youtube["level"], "warn")
        self.assertIn("www.youtube.com", youtube["dns_note"])
        self.assertTrue(result["dns_poisoning_detected"])
        self.assertIn(("www.youtube.com", "142.251.157.4"), net.calls)
        self.assertNotIn("❌ YouTube", text)

    def test_dpi_reset_retries_once_on_another_address_and_reports_strategy(self) -> None:
        lines: list[str] = []
        net = _Net(https=lambda host, ip: ProbeResult(ip=ip, kind=KIND_RESET))
        result = net.run(engine.run_blockcheck, "main", emit=lines.append)
        text = "\n".join(lines)

        discord_calls = [ip for host, ip in net.calls if host == "discord.com"]
        # Оба адреса пробуются в общем залпе и ещё раз — при повторной проверке поодиночке.
        self.assertEqual(sorted(discord_calls), sorted(DISCORD_REAL * 2))
        discord_target = result["services"][0]["targets"][0]
        self.assertEqual(discord_target["rechecked"], "same")
        self.assertIn("повторная проверка поодиночке дала то же", discord_target["text"])
        self.assertFalse(result["dns_poisoning_detected"])
        self.assertEqual(result["services"][0]["level"], "fail")
        self.assertIn("Подбор стратегии", text)
        hosts_order = [line.split(" ")[1] for line in lines if line.startswith("❌ ") and "." in line.split(" ")[1]]
        self.assertEqual(hosts_order[:3], ["discord.com", "gateway.discord.gg", "cdn.discordapp.com"])

    def test_site_that_opens_on_the_calm_second_check_is_not_reported_as_blocked(self) -> None:
        """Первый сбой дала нагрузка самой проверки: при повторе поодиночке сайт открывается."""
        seen: dict[str, int] = {}

        def _https(host, ip):
            seen[host] = seen.get(host, 0) + 1
            # discord.com не соединяется в залпе (оба адреса и повтор после паузы), потом открывается.
            if host == "discord.com" and seen[host] <= 3:
                return ProbeResult(ip=ip, kind=KIND_CONNECT, connect_fail="timeout")
            return _ok(ip)

        net = _Net(https=_https)
        with patch.object(engine, "RETRY_PAUSE_S", 0.0):
            result = net.run(engine.run_blockcheck, "main", emit=lambda _line: None)

        discord = next(item for item in result["services"] if item["key"] == "discord")
        self.assertEqual(discord["level"], "ok")
        self.assertEqual(discord["targets"][0]["rechecked"], "opened")
        self.assertIn("со второй проверки, поодиночке", discord["targets"][0]["text"])
        self.assertFalse(any(item["target"] == "discord.com" for item in result["problems"]))

    def test_dns_tab_does_not_recheck(self) -> None:
        """Вкладка «DNS подмена» проверяет DNS, а не открываемость: повторный проход ей не нужен."""
        calls = []

        def _https(host, ip):
            calls.append(host)
            return ProbeResult(ip=ip, kind=KIND_RESET)

        net = _Net(system=("5.6.7.8",), https=_https)
        net.run(engine.run_dns_check, emit=lambda _line: None)

        self.assertEqual(len(calls), len(set(calls)))

    def test_site_blocked_over_ipv4_but_open_over_ipv6_is_reported_as_working(self) -> None:
        """Браузер сам уходит на IPv6, поэтому и проверка обязана его попробовать."""
        v6 = "2606:4700::6810:1"

        def _https(host, ip):
            return _ok(ip) if ip == v6 else ProbeResult(ip=ip, kind=KIND_RESET)

        net = _Net(https=_https, reference_v6=(v6,))
        result = net.run(engine.run_blockcheck, "main", emit=lambda _line: None)

        discord = next(item for item in result["services"] if item["key"] == "discord")
        self.assertEqual(discord["level"], "ok")
        self.assertIn("IPv6", discord["targets"][0]["short"])
        self.assertIn(("discord.com", v6), net.calls)

    def test_ipv6_is_not_tried_when_ipv4_opens(self) -> None:
        net = _Net(reference_v6=("2606:4700::6810:1",))
        net.run(engine.run_blockcheck, "main", emit=lambda _line: None)

        self.assertFalse(any(":" in ip for _host, ip in net.calls))

    def test_system_address_that_fails_is_rechecked_by_reference_address(self) -> None:
        def _https(host, ip):
            if ip == "5.6.7.8":
                return ProbeResult(ip=ip, kind=KIND_RESET)
            return _ok(ip)

        net = _Net(system=("5.6.7.8",), https=_https)
        result = net.run(engine.run_blockcheck, "main", emit=lambda _line: None)

        self.assertEqual(result["services"][0]["level"], "ok")
        self.assertIn(("discord.com", DISCORD_REAL[0]), net.calls)

    def test_run_deadline_still_prints_summary_and_keeps_spoofing(self) -> None:
        # Запас по времени большой: при загруженной машине ответы DNS не должны
        # опоздать к сроку — иначе тест падает через раз.
        def _slow(host, ip):
            time.sleep(2.0)
            return ProbeResult(ip=ip, kind="cancelled")

        lines: list[str] = []
        net = _Net(system=("195.82.146.214",), https=_slow)
        with patch.object(engine, "RUN_DEADLINE", 1.0):
            result = net.run(engine.run_dns_check, emit=lines.append)

        text = "\n".join(lines)
        self.assertNotIn("stopped", result)
        self.assertTrue(result["summary"]["dns_poisoning_detected"])
        self.assertIn("Итог", text)
        self.assertIn("не уложилась", text)

    def test_stop_returns_quickly_and_marks_stopped(self) -> None:
        release = threading.Event()

        def _slow(host, ip):
            release.wait(5)
            return _ok(ip)

        net = _Net(system=("5.6.7.8",), https=_slow)
        started = time.monotonic()
        result = net.run(engine.run_dns_check, emit=lambda _line: None, should_stop=lambda: True)
        release.set()

        self.assertTrue(result.get("stopped"))
        self.assertLess(time.monotonic() - started, 2.0)


class EngineMixedAnswerTests(unittest.TestCase):
    """Найденные при разборе ошибки: смешанный ответ DNS, прерывание по времени, hosts."""

    def _mixed_net(self, https):
        stub = "95.1.1.1"
        # У каждого сайта своя очередь ответов: с общей очередью потоки делили бы
        # ответы как придётся, и «чужой» адрес доставался бы не тому сайту.
        lock = threading.Lock()
        queues: dict[str, object] = {}

        def _system(host):
            with lock:
                answers = queues.setdefault(
                    host,
                    iter([DnsAnswer(ips=(stub,)), DnsAnswer(ips=DISCORD_REAL), DnsAnswer(ips=DISCORD_REAL)]),
                )
                return next(answers)

        return _Net(system=_system, https=https), stub

    def test_dns_that_sometimes_gives_foreign_server_is_spoofing_but_site_opens(self) -> None:
        def _https(host, ip):
            if ip == "95.1.1.1":
                return ProbeResult(ip=ip, kind=KIND_CERT, cert_problem="сертификат выдан другому сайту")
            return _ok(ip)

        net, _stub = self._mixed_net(_https)
        result = net.run(engine.run_blockcheck, "main", emit=lambda _line: None)

        discord = next(item for item in result["services"] if item["key"] == "discord")
        main = next(item for item in discord["targets"] if item["main"])
        # Сайт открывается по настоящему адресу, а DNS помечен как подмена.
        self.assertTrue(main["ok"])
        self.assertEqual(main["dns_state"], "spoofed")
        self.assertIn("чужого сервера", main["dns_reason"])

    def test_cdn_with_extra_genuine_address_is_not_spoofing(self) -> None:
        net, _stub = self._mixed_net(lambda host, ip: _ok(ip))
        result = net.run(engine.run_blockcheck, "main", emit=lambda _line: None)

        discord = next(item for item in result["services"] if item["key"] == "discord")
        self.assertTrue(all(item["dns_state"] == "ok" for item in discord["targets"]))

    def test_deadline_before_first_request_is_not_no_address(self) -> None:
        probe = engine._Probe(target=engine.Target("discord.com", "сайт"), service="discord", host="discord.com")
        probe.dns = DnsAnswer(ips=DISCORD_REAL)
        probe.reference_ips = DISCORD_REAL
        run = engine._Run(None, workers=1, deadline=0)
        self.addCleanup(run.close)

        engine._check_reach(run, probe, read_limit=0)

        self.assertEqual(judge_reach(probe.reach), ReachState.UNKNOWN)

    def test_cancel_in_the_middle_of_body_is_not_a_freeze(self) -> None:
        """Лимит времени снял медленную загрузку после 16 КБ — это не обрыв ТСПУ."""
        from diagnostics import tls_probe

        cancel = SocketCancel()
        chunks = [b"HTTP/1.1 200 OK\r\n\r\n" + b"x" * 16_000]

        class _Tls:
            def settimeout(self, _value): pass
            def do_handshake(self): pass
            def version(self): return "TLSv1.3"
            def sendall(self, _data): pass
            def shutdown(self, _how): pass
            def close(self): pass

            def recv(self, _size):
                if chunks:
                    return chunks.pop(0)
                cancel.cancel()
                raise socket.timeout()

        class _Context:
            def wrap_socket(self, _sock, **_kwargs):
                return _Tls()

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        with patch.object(tls_probe, "_client_context", return_value=_Context()):
            result = https_get(
                "example.com", "127.0.0.1", timeout=2.0, port=listener.getsockname()[1],
                read_limit=64_000, cancel=cancel,
            )

        self.assertEqual(result.kind, "cancelled")
        self.assertNotEqual(judge_reach(result), ReachState.FREEZE)

    def test_stale_hosts_entry_gets_hosts_advice_not_antivirus(self) -> None:
        verdict = summarize_service(
            "Instagram",
            [TargetOutcome("www.instagram.com", "сайт", ReachState.CERT, DnsState.LOCAL, main=True)],
            zapret_running=True,
        )

        self.assertIn("hosts", verdict.headline)
        self.assertTrue(any("hosts" in item for item in verdict.advice))
        self.assertFalse(any("антивирус" in item for item in verdict.advice))

    def test_user_domains_only_in_all_sites_scope(self) -> None:
        self.assertNotIn("user:example.org", engine.build_services("main", ["example.org"]))
        self.assertIn("user:example.org", engine.build_services("all", ["example.org"]))


class BlockcheckScopeTests(unittest.TestCase):
    """Режимы BlockCheck, голосовые серверы, обрыв 16 КБ и сводка проблем."""

    def test_scopes_and_user_domains(self) -> None:
        main = engine.build_services("main")
        everything = engine.build_services("all", ["Example.org", "discord.com"])

        self.assertEqual(list(main), ["discord", "youtube"])
        self.assertIn("telegram", everything)
        self.assertTrue(everything["google"].control)
        self.assertIn("user:example.org", everything)
        # Домен, который и так проверяется, не дублируется.
        self.assertNotIn("user:discord.com", everything)

    def test_unparsable_stun_answer_means_udp_works(self) -> None:
        from types import SimpleNamespace

        from diagnostics.voice_check import _describe, summarize_voice, VoiceServer

        answered, _text = _describe(SimpleNamespace(status=SimpleNamespace(value="fail"), error_code="PARSE_ERR", detail=""))
        self.assertTrue(answered)
        report = summarize_voice((VoiceServer("A", "a", False, "нет"), VoiceServer("B", "b", False, "нет")))
        self.assertEqual(report.level, Level.FAIL)
        # На win10 молчали только серверы Telegram — это про звонки Telegram, не «UDP вообще».
        report = summarize_voice((
            VoiceServer("Google STUN", "stun.l.google.com", True, "да"),
            VoiceServer("Telegram STUN", "stun.telegram.org", False, "нет"),
        ))
        self.assertEqual(report.level, Level.WARN)
        self.assertIn("Telegram", report.headline)
        report = summarize_voice((
            VoiceServer("Google STUN", "stun.l.google.com", True, "да"),
            VoiceServer("CF STUN", "stun.cloudflare.com", False, "нет"),
        ))
        self.assertEqual(report.level, Level.OK)

    def test_freeze_classification(self) -> None:
        from diagnostics.freeze_check import FreezeState, classify_download

        frozen = classify_download(_ok(body_cut=True, body_size=16_500))
        self.assertEqual(frozen[0], FreezeState.FREEZE)
        self.assertEqual(classify_download(_ok(body_size=32_768))[0], FreezeState.OK)
        # Сброс сразу, без данных — не «доступ есть» и не «обрыв», а «не удалось проверить».
        self.assertEqual(classify_download(ProbeResult(ip="1.1.1.1", kind=KIND_RESET))[0], FreezeState.UNKNOWN)
        self.assertEqual(classify_download(_ok(body_size=2_000))[0], FreezeState.UNKNOWN)

    def test_freeze_takes_spare_address_of_same_provider(self) -> None:
        from diagnostics import freeze_check
        from diagnostics.freeze_check import FreezeState, check_freeze

        targets = (
            {"provider": "Hetzner", "url": "https://dead.example/a.css"},
            {"provider": "Hetzner", "url": "https://alive.example/b.bin"},
            {"provider": "Hetzner", "url": "https://third.example/c.bin"},
            {"provider": "OVH", "url": "https://ovh.example/1Mb.dat"},
        )
        calls: list[str] = []

        def _download(host, _path):
            calls.append(host)
            return None if host == "dead.example" else _ok(body_size=33_000)

        with patch("blockcheck.data_lists.TCP_16_20_TARGETS", targets):
            servers = check_freeze(lambda fn, *a: _Done(fn(*a)), lambda f: f.result(), _download)

        self.assertEqual([item.state for item in servers], [FreezeState.OK, FreezeState.OK])
        self.assertIn("alive.example", servers[0].name)
        # Ответ получен со второго адреса — третий не трогаем.
        self.assertNotIn("third.example", calls)
        # Вышел запас времени — запасные адреса не пробуются.
        calls.clear()
        with patch("blockcheck.data_lists.TCP_16_20_TARGETS", targets):
            servers = check_freeze(lambda fn, *a: _Done(fn(*a)), lambda f: f.result(), _download, budget=0)
        self.assertEqual(servers[0].state, FreezeState.UNKNOWN)
        self.assertNotIn("alive.example", calls)
        self.assertEqual(freeze_check.CANDIDATES_PER_PROVIDER, 3)

    def test_freeze_summary_says_how_many_servers_were_checked(self) -> None:
        from diagnostics.freeze_check import FreezeServer, FreezeState, summarize_freeze

        ok = FreezeServer("A", FreezeState.OK, "32 КБ")
        unknown = FreezeServer("B", FreezeState.UNKNOWN, "сброс")
        full = summarize_freeze((ok, ok), zapret_running=True)
        self.assertEqual((full.level, full.headline), (Level.OK, "Обрыва загрузки на 16–20 КБ нет"))
        half = summarize_freeze((ok, ok, ok, unknown, unknown, unknown), zapret_running=True)
        self.assertEqual(half.level, Level.OK)
        self.assertIn("3 из 6", half.headline)
        mostly_unknown = summarize_freeze((ok, unknown, unknown), zapret_running=True)
        self.assertEqual(mostly_unknown.level, Level.UNKNOWN)
        self.assertIn("1 из 3", mostly_unknown.headline)

    def _run_all(self, https):
        from diagnostics.freeze_check import FreezeServer, FreezeState
        from diagnostics.voice_check import VoiceServer

        net = _Net(
            https=https,
            voice=(VoiceServer("CF", "stun", True, "отвечает"),),
            freeze=(FreezeServer("Akamai", FreezeState.OK, "получено 32 КБ"),),
        )
        return net.run(engine.run_blockcheck, "all", emit=lambda _line: None)

    def _run_with_telegram(self, connected, *, scope="all"):
        from diagnostics.freeze_check import FreezeServer, FreezeState
        from diagnostics.telegram_check import DATA_CENTERS, DcResult
        from diagnostics.voice_check import VoiceServer

        net = _Net(
            https=lambda host, ip: _ok(ip),
            voice=(VoiceServer("CF", "stun", True, "отвечает"),),
            freeze=(FreezeServer("Akamai", FreezeState.OK, "получено 32 КБ"),),
        )
        net.telegram = tuple(
            DcResult(center, ok, 30.0 if ok else None, attempts=1 if ok else 2)
            for center, ok in zip(DATA_CENTERS, connected)
        )
        net.network = {"external_ip": "203.0.113.7", "provider": "ROSTELECOM-AS · AS12389", "lines": []}
        return net.run(engine.run_blockcheck, scope, emit=lambda _line: None)

    def test_silent_telegram_data_centres_become_a_problem_only_when_all_are_silent(self) -> None:
        dead = self._run_with_telegram([False] * 5)
        texts = [item["text"] for item in dead["problems"]]
        self.assertTrue(any("Дата-центры Telegram не принимают соединения" in text for text in texts))
        self.assertEqual(dead["telegram"]["level"], "fail")
        self.assertEqual([item["state"] for item in dead["telegram"]["items"]], ["fail"] * 5)
        self.assertEqual(dead["telegram"]["items"][0]["text"], "не соединился, попыток: 2")

        partial = self._run_with_telegram([True, True, True, True, False])
        self.assertEqual(partial["telegram"]["level"], "warn")
        self.assertFalse(any("Telegram" in item["text"] and "дата-центр" in item["text"].lower() for item in partial["problems"]))

    def test_network_block_reaches_the_report_and_quick_check_skips_telegram(self) -> None:
        full = self._run_with_telegram([True] * 5)
        self.assertEqual(full["network"]["provider"], "ROSTELECOM-AS · AS12389")
        self.assertEqual(full["telegram"]["items"][0]["text"], "соединение за 30 мс")

        quick = self._run_with_telegram([False] * 5, scope="main")
        self.assertIsNone(quick["telegram"])
        self.assertEqual(quick["network"]["external_ip"], "203.0.113.7")

    def test_cut_on_foreign_controls_is_not_whitelist_mode(self) -> None:
        """Google и Cloudflare обрываются после 16 КБ — это лечит Zapret, а не «закрыты все зарубежные адреса»."""
        domestic = ("ya.ru", "vk.com")

        def https(host, ip):
            if host in domestic:
                return _ok(ip)
            return ProbeResult(ip=ip, kind="ok", status=200, body_size=16_500, body_cut=True)

        result = self._run_all(https)

        kinds = [item["kind"] for item in result["problems"]]
        self.assertNotIn("network", kinds)
        self.assertFalse(any("белых списков" in item["text"] for item in result["problems"]))
        # И остальные находки не спрятаны «общей причиной».
        self.assertTrue(any(item["kind"] == "cut16" for item in result["problems"]))

    def test_answering_reference_server_refutes_no_internet_and_whitelist(self) -> None:
        """Эталонный DNS-сервер — зарубежный адрес: раз он ответил, интернет есть и зарубежные адреса открыты."""
        from diagnostics.freeze_check import FreezeServer, FreezeState
        from diagnostics.voice_check import VoiceServer

        net = _Net(
            https=lambda host, ip: ProbeResult(ip=ip, kind=KIND_CONNECT, connect_fail="timeout"),
            voice=(VoiceServer("CF", "stun", True, "отвечает"),),
            freeze=(FreezeServer("Akamai", FreezeState.OK, "получено 32 КБ"),),
        )
        reference = [{"label": "Cloudflare", "address": "1.1.1.1", "ok": True, "answered": 5, "failed": 0, "reason": ""}]
        with patch.object(engine._Run, "reference_report", return_value=reference):
            result = net.run(engine.run_blockcheck, "all", emit=lambda _line: None)

        self.assertFalse(any(item["kind"] == "network" for item in result["problems"]))

    def _run_with_quic(self, code_for):
        from diagnostics.freeze_check import FreezeServer, FreezeState
        from diagnostics.voice_check import VoiceServer

        net = _Net(
            https=lambda host, ip: _ok(ip),
            voice=(VoiceServer("CF", "stun", True, "отвечает"),),
            freeze=(FreezeServer("Akamai", FreezeState.OK, "получено 32 КБ"),),
        )
        quic = engine.quic_probe

        def facts(host, _ip):
            code = code_for(host)
            if code == quic.QUIC_OK:
                return quic.QuicFacts(host=host, real_ms=10.0)
            return quic.QuicFacts(host=host)

        net.quic_facts = facts
        return net.run(engine.run_blockcheck, "all", emit=lambda _line: None)

    def test_silent_quic_on_sites_that_surely_support_it_means_udp_443_is_closed(self) -> None:
        quic = engine.quic_probe
        closed = self._run_with_quic(lambda _host: quic.QUIC_SILENT)

        [problem] = [item for item in closed["problems"] if item["kind"] == "quic"]
        self.assertIn("UDP 443 закрыт целиком", problem["text"])
        self.assertIn("www.google.com", problem["text"])

    def test_one_answering_site_means_udp_443_is_not_closed(self) -> None:
        quic = engine.quic_probe
        result = self._run_with_quic(lambda host: quic.QUIC_OK if host == "www.google.com" else quic.QUIC_SILENT)

        self.assertFalse(any("закрыт целиком" in item["text"] for item in result["problems"]))

    def test_speed_is_measured_only_in_the_full_check(self) -> None:
        from diagnostics.freeze_check import FreezeServer, FreezeState
        from diagnostics.voice_check import VoiceServer

        def run(scope):
            net = _Net(
                https=lambda host, ip: _ok(ip),
                voice=(VoiceServer("CF", "stun", True, "отвечает"),),
                freeze=(FreezeServer("Akamai", FreezeState.OK, "получено 32 КБ"),),
            )
            net.speed = {"level": "ok", "headline": "Заметной разницы в скорости нет", "items": []}
            return net.run(engine.run_blockcheck, scope, emit=lambda _line: None)

        self.assertEqual(run("full")["speed"]["level"], "ok")
        self.assertIsNone(run("all")["speed"])

    def test_controls_down_means_no_internet_first(self) -> None:
        result = self._run_all(lambda host, ip: ProbeResult(ip=ip, kind=KIND_CONNECT))

        self.assertIn("контрольные сайты", result["problems"][0]["text"])

    def test_only_domestic_sites_open_means_whitelist_mode(self) -> None:
        domestic = {"ya.ru", "vk.com"}

        def _https(host, ip):
            return _ok(ip) if host in domestic else ProbeResult(ip=ip, kind=KIND_CONNECT)

        result = self._run_all(_https)
        first = result["problems"][0]

        self.assertEqual(first["level"], "fail")
        self.assertIn("белых списков", first["text"])
        self.assertIn("Яндекс, ВКонтакте", first["text"])
        self.assertIn("Google, Cloudflare", first["text"])
        self.assertIn("Zapret не помогает", first["advice"][0])
        self.assertNotIn("контрольные сайты", first["text"].split("зарубежные")[0])
        # Причина одна и названа: кнопок «Подобрать стратегию» у каждого сайта нет.
        self.assertFalse([item for item in result["problems"] if item["action"] in ("strategy", "start_zapret")])
        yandex = next(item for item in result["services"] if item["key"] == "yandex")
        self.assertTrue(yandex["control"] and yandex["domestic"])

    def test_foreign_controls_open_is_not_whitelist_mode(self) -> None:
        """Один закрытый сайт при живых зарубежных контрольных — обычная блокировка."""
        result = self._run_all(
            lambda host, ip: ProbeResult(ip=ip, kind=KIND_RESET) if host == "x.com" else _ok(ip)
        )

        self.assertFalse(any("белых списков" in item["text"] for item in result["problems"]))
        self.assertTrue(any(item["target"] == "x.com" and item["action"] == "strategy" for item in result["problems"]))

    def test_domestic_site_down_too_is_no_internet_not_whitelist(self) -> None:
        result = self._run_all(lambda host, ip: ProbeResult(ip=ip, kind=KIND_CONNECT))
        first = result["problems"][0]["text"]

        self.assertIn("Не открываются даже контрольные сайты (Google, Cloudflare, Яндекс, ВКонтакте)", first)
        self.assertNotIn("белых списков", first)

    def test_working_sites_keep_service_order_and_offline_hides_site_noise(self) -> None:
        # У YouTube подменён DNS (уровень «предупреждение»), но в «Открываются»
        # он не должен выскакивать раньше Discord.
        def _https(host, ip):
            return _ok(ip)

        from diagnostics.freeze_check import FreezeServer, FreezeState
        from diagnostics.voice_check import VoiceServer

        def _system(host):
            if host == "www.youtube.com":
                return DnsAnswer(ips=(), status=DNS_STATUS_NAME_ERROR)
            return DnsAnswer(ips=DISCORD_REAL)

        net = _Net(
            system=_system,
            https=_https,
            voice=(VoiceServer("CF", "stun", True, "отвечает"),),
            freeze=(FreezeServer("Akamai", FreezeState.OK, "получено 32 КБ"),),
        )
        result = net.run(engine.run_blockcheck, "all", emit=lambda _line: None)
        youtube = next(item for item in result["services"] if item["key"] == "youtube")
        self.assertEqual(youtube["level"], "warn")
        self.assertEqual(result["working"][:2], ["Discord", "YouTube"])

        offline = self._run_all(lambda host, ip: ProbeResult(ip=ip, kind=KIND_CONNECT))
        texts = [problem["text"] for problem in offline["problems"]]
        self.assertIn("контрольные сайты", texts[0])
        # Причина одна — нет интернета; строки «X не открывается» по каждому сайту не нужны.
        self.assertEqual(len(texts), 1)

    def test_controls_cut_by_deadline_do_not_hide_real_blocks(self) -> None:
        controls = {"www.google.com", "www.cloudflare.com"}

        def _https(host, ip):
            if host in controls:
                return ProbeResult(ip=ip, kind="cancelled")
            if host == "x.com":
                return ProbeResult(ip=ip, kind=KIND_RESET)
            return _ok(ip)

        result = self._run_all(_https)
        texts = [problem["text"] for problem in result["problems"]]

        self.assertFalse(any("контрольные сайты" in text for text in texts))
        self.assertTrue(any(problem["target"] == "x.com" for problem in result["problems"]))

    def test_strategy_button_only_for_blocks_a_strategy_can_bypass(self) -> None:
        result = self._run_all(
            lambda host, ip: ProbeResult(ip=ip, kind=KIND_CONNECT) if host == "x.com" else _ok(ip)
        )

        problem = next(item for item in result["problems"] if item["target"] == "x.com")
        self.assertEqual(problem["action"], "")

    def test_blocked_site_gets_strategy_action_with_its_host(self) -> None:
        result = self._run_all(lambda host, ip: ProbeResult(ip=ip, kind=KIND_RESET) if host == "x.com" else _ok(ip))

        problem = result["problems"][0]
        self.assertEqual(problem["action"], "strategy")
        self.assertEqual(problem["target"], "x.com")
        self.assertIn("Discord", result["working"])
        self.assertEqual(result["voice"]["level"], "ok")
        self.assertEqual(result["freeze"]["level"], "ok")


class TlsProbeFailureTests(unittest.TestCase):
    """Классификация сбоев на настоящих сокетах (без TLS-сервера)."""

    def _server(self, behaviour):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def _serve():
            try:
                conn, _ = listener.accept()
                behaviour(conn)
            except OSError:
                pass
            finally:
                listener.close()

        threading.Thread(target=_serve, daemon=True).start()
        return port

    def _get(self, port, timeout=1.0):
        return https_get("example.com", "127.0.0.1", timeout=timeout, port=port)

    def test_reset_during_handshake_is_reset_or_tls(self) -> None:
        def _rst(conn):
            conn.recv(1024)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
            conn.close()

        result = self._get(self._server(_rst))

        self.assertIn(result.kind, (KIND_RESET, KIND_TLS))
        self.assertEqual(judge_reach(result), ReachState.DPI)

    def test_silent_server_after_connect_is_timeout(self) -> None:
        def _silent(conn):
            time.sleep(2)
            conn.close()

        result = self._get(self._server(_silent), timeout=0.5)

        self.assertEqual(result.kind, KIND_TIMEOUT)
        self.assertEqual(result.stage, "tls")

    def test_closed_port_is_connect_failure(self) -> None:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        result = self._get(port)

        self.assertEqual(result.kind, KIND_CONNECT)
        self.assertEqual(judge_reach(result), ReachState.IP_BLOCK)


class BlockKindInReportTests(unittest.TestCase):
    """В отчёте у каждого сайта назван вид блокировки: по адресу, по имени или обрыв после 16 КБ."""

    @staticmethod
    def _stalls(received: int, requests: int):
        vp = engine.volume_probe
        return vp.VolumeRun(vp.RUN_STALLED, received, requests)

    def test_cut_after_16kb_is_found_on_a_site_with_a_small_page(self) -> None:
        """Главная страница короткая и «открывается», но по соединению не проходит больше 14 КБ."""
        vp = engine.volume_probe
        lines: list[str] = []
        net = _Net()
        net.volume_facts = lambda host, _ip: (
            vp.VolumeFacts(self._stalls(14_500, 20), self._stalls(14_100, 40))
            if host == "discord.com"
            else vp.VolumeFacts(vp.VolumeRun(vp.RUN_PASSED, 33_000, 4))
        )
        result = net.run(engine.run_blockcheck, "main", emit=lines.append)

        discord = next(item for item in result["services"] if item["key"] == "discord")
        main = next(item for item in discord["targets"] if item["main"])
        self.assertEqual((main["state"], main["ok"], main["kind"], main["volume"]), ("freeze", False, "cut16", "cut"))
        self.assertEqual(main["kind_title"], "Обрыв после 16 КБ")
        self.assertIn("соединение замирает после 14 КБ", main["short"])
        self.assertEqual((discord["level"], discord["kind"]), ("fail", "cut16"))
        self.assertTrue(discord["headline"].startswith("Discord грузится не до конца: загрузка обрывается после 16 КБ"))
        problem = next(item for item in result["problems"] if item["target"] == "discord.com")
        self.assertEqual((problem["kind"], problem["title"], problem["action"]), ("cut16", "Discord", "strategy"))
        self.assertNotIn("Discord", result["working"])
        self.assertIn("   🏷 Вид блокировки: Обрыв после 16 КБ", lines)

    def test_single_stall_does_not_turn_a_working_site_red(self) -> None:
        vp = engine.volume_probe
        net = _Net()
        net.volume_facts = lambda _host, _ip: vp.VolumeFacts(
            self._stalls(14_500, 20), vp.VolumeRun(vp.RUN_PASSED, 33_000, 70)
        )
        lines: list[str] = []
        result = net.run(engine.run_blockcheck, "main", emit=lines.append)

        self.assertEqual([item["level"] for item in result["services"]], ["ok", "ok"])
        self.assertFalse([item for item in result["problems"] if item["level"] in ("fail", "warn")])
        self.assertIn("Discord", result["working"])
        self.assertIn("случайный сбой", "\n".join(lines))

    def test_big_page_and_broken_site_are_not_probed_for_volume(self) -> None:
        net = _Net(https=lambda host, ip: _ok(ip, body_size=40_000))
        net.run(engine.run_blockcheck, "main", emit=lambda _line: None)
        # Страница больше окна обрыва пришла целиком — повторять незачем.
        self.assertNotIn("discord.com", net.volume_asked)

        net = _Net(https=lambda host, ip: ProbeResult(ip=ip, kind=KIND_RESET, stage="tls"))
        net.run(engine.run_blockcheck, "main", emit=lambda _line: None)
        self.assertEqual(net.volume_asked, [])

    def test_control_sites_are_not_loaded_with_volume_requests(self) -> None:
        net = _Net()
        net.run(engine.run_blockcheck, "all", emit=lambda _line: None)

        self.assertIn("discord.com", net.volume_asked)
        for host in ("www.google.com", "www.cloudflare.com", "ya.ru", "vk.com"):
            self.assertNotIn(host, net.volume_asked)

    def test_block_by_name_goes_to_its_own_group(self) -> None:
        def facts(host, result):
            return engine.block_cause.CauseFacts(
                host=host,
                result=result,
                neutral=engine.block_cause.HelloResult(engine.block_cause.HELLO_OK),
                nameless=engine.block_cause.HelloResult(engine.block_cause.HELLO_RESET),
            )

        net = _Net(https=lambda host, ip: ProbeResult(ip=ip, kind=KIND_RESET, stage="tls"), cause_facts=facts)
        result = net.run(engine.run_blockcheck, "main", emit=lambda _line: None)

        discord = next(item for item in result["services"] if item["key"] == "discord")
        self.assertEqual(discord["kind"], "sni")
        self.assertIn("блокировка по имени сайта (SNI)", discord["headline"])
        problem = next(item for item in result["problems"] if item["target"] == "discord.com")
        self.assertEqual((problem["kind"], problem["title"]), ("sni", "Discord"))
        # Свидетельство стоит первым в советах и отдельно — чтобы экран не ставил перед ним стрелку.
        self.assertEqual(problem["evidence"], problem["advice"][: len(problem["evidence"])])
        self.assertTrue(problem["evidence"][0].startswith("Блокировка по имени сайта"))

    def test_block_by_address_is_not_mixed_with_block_by_name(self) -> None:
        def facts(host, result):
            return engine.block_cause.CauseFacts(
                host=host,
                result=result,
                # Тишина на любое имя, даже на разрешённое: так молчит фильтр, а не сам сервер.
                neutral=engine.block_cause.HelloResult(engine.block_cause.HELLO_TIMEOUT),
                nameless=engine.block_cause.HelloResult(engine.block_cause.HELLO_TIMEOUT),
                allowed=engine.block_cause.HelloResult(engine.block_cause.HELLO_TIMEOUT),
            )

        net = _Net(https=lambda host, ip: ProbeResult(ip=ip, kind=KIND_RESET, stage="tls"), cause_facts=facts)
        result = net.run(engine.run_blockcheck, "main", emit=lambda _line: None)

        problem = next(item for item in result["problems"] if item["target"] == "discord.com")
        self.assertEqual(problem["kind"], "ip")
        self.assertIn("сервер заблокирован по адресу (IP)", problem["text"])
        self.assertIn("Редакторе hosts", problem["advice"][-1])

    def test_problems_about_the_network_have_their_own_kinds(self) -> None:
        net = _Net(system=("0.0.0.0",))
        result = net.run(engine.run_blockcheck, "main", emit=lambda _line: None)

        kinds = {item["kind"] for item in result["problems"]}
        self.assertIn("dns", kinds)
        self.assertTrue(all(item["kind"] for item in result["problems"]))


class IcmpAddressTests(unittest.TestCase):
    def test_icmp_address_keeps_network_byte_order_in_memory(self) -> None:
        import socket
        import struct

        from utils.windows_icmp import _ipv4_to_dword

        for ip in ("142.251.150.4", "162.159.137.232"):
            with self.subTest(ip=ip):
                self.assertEqual(struct.pack("<I", _ipv4_to_dword(ip)), socket.inet_aton(ip))


if __name__ == "__main__":
    unittest.main()
