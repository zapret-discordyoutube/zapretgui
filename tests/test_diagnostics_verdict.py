from __future__ import annotations

import socket
import threading
import time
import unittest
from unittest.mock import patch

import diagnostics.engine as engine
from diagnostics.tls_probe import (
    KIND_CERT,
    KIND_CONNECT,
    KIND_OK,
    KIND_RESET,
    KIND_TIMEOUT,
    KIND_TLS,
    ProbeCancel,
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
        for ip in ("195.82.146.214", "127.0.0.1", "0.0.0.0", "10.10.10.10", "192.168.1.1"):
            with self.subTest(ip=ip):
                self.assertEqual(self._judge(system_ips=(ip,)).state, DnsState.SPOOFED)

    def test_vpn_fake_ip_is_local_not_spoofing(self) -> None:
        self.assertEqual(self._judge(system_ips=("198.18.0.12",)).state, DnsState.LOCAL)

    def test_hosts_file_entry_is_reported_as_local(self) -> None:
        judgement = self._judge(hosts_ips=("1.2.3.4",), system_ips=("1.2.3.4",), check_kind=KIND_CERT)

        self.assertEqual(judgement.state, DnsState.LOCAL)
        self.assertIn("hosts", judgement.reason)

    def test_occasional_nxdomain_with_real_addresses_is_spoofing(self) -> None:
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

    def __init__(self, *, system=DISCORD_REAL, reference=DISCORD_REAL, status=0, https=None, reference_v6=(), voice=(), freeze=()):
        self.voice = voice
        self.freeze = freeze
        self.system = system
        self.reference = reference
        self.reference_v6 = reference_v6
        self.status = status
        self.https = https or (lambda host, ip: _ok(ip))
        self.calls: list[tuple[str, str]] = []

    def patches(self):
        def _https_get(host, ip, *_args, **_kwargs):
            self.calls.append((host, ip))
            return self.https(host, ip)

        return (
            patch.object(
                engine,
                "query_ipv4",
                side_effect=lambda host, **_kw: (
                    self.system(host) if callable(self.system) else DnsAnswer(ips=self.system, status=self.status)
                ),
            ),
            patch.object(
                engine,
                "_doh_lookup",
                side_effect=lambda _run, _host, record_type=engine.DNS_TYPE_A: (
                    True,
                    self.reference_v6 if record_type == engine.DNS_TYPE_AAAA else self.reference,
                ),
            ),
            patch.object(engine, "https_get", side_effect=_https_get),
            patch.object(engine, "hosts_file_ipv4", return_value=()),
            patch.object(engine, "system_dns_servers", return_value=("83.220.169.155",)),
            patch.object(engine, "_zapret_status", return_value=(True, "✅ Zapret запущен")),
            patch.object(engine, "_discover_googlevideo", return_value=("rr1---sn-test.googlevideo.com", "")),
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
        with patch.object(engine, "query_ipv4", side_effect=lambda *_a, **_k: next(answers)):
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
        self.assertEqual(sorted(discord_calls), sorted(DISCORD_REAL))
        self.assertFalse(result["dns_poisoning_detected"])
        self.assertEqual(result["services"][0]["level"], "fail")
        self.assertIn("Подбор стратегии", text)
        hosts_order = [line.split(" ")[1] for line in lines if line.startswith("❌ ") and "." in line.split(" ")[1]]
        self.assertEqual(hosts_order[:3], ["discord.com", "gateway.discord.gg", "cdn.discordapp.com"])

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
        def _slow(host, ip):
            time.sleep(0.6)
            return ProbeResult(ip=ip, kind="cancelled")

        lines: list[str] = []
        net = _Net(system=("195.82.146.214",), https=_slow)
        with patch.object(engine, "RUN_DEADLINE", 0.2):
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
        answers = iter([DnsAnswer(ips=(stub,)), DnsAnswer(ips=DISCORD_REAL), DnsAnswer(ips=DISCORD_REAL)] * 20)

        def _system(_host):
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

        cancel = ProbeCancel()
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

    def test_controls_down_means_no_internet_first(self) -> None:
        result = self._run_all(lambda host, ip: ProbeResult(ip=ip, kind=KIND_CONNECT))

        self.assertIn("контрольные сайты", result["problems"][0]["text"])

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
