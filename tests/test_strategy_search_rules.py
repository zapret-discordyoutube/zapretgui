"""Правила подбора, порядок стратегий, профиль пробы и история."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from blockcheck.strategy_search import verdict as rules
from blockcheck.strategy_search.ordering import (
    FAILED_MEMORY_SECONDS,
    Candidate,
    batch_for_mode,
    order_candidates,
)
from blockcheck.strategy_search.probe_profile import build_probe_config_text, build_probe_profile
from blockcheck.strategy_search.verdict import ProbeOutcome, UdpProbeSpec
from diagnostics import tls_probe


def https_result(kind=tls_probe.KIND_OK, *, status=200, body_size=40_000, body_cut=False, stage="read", **extra):
    return tls_probe.ProbeResult(
        ip="1.2.3.4",
        kind=kind,
        stage=stage,
        status=status,
        body_size=body_size,
        body_cut=body_cut,
        elapsed_ms=50.0,
        **extra,
    )


class JudgeHttpsTests(unittest.TestCase):
    def test_full_http_answer_is_ok(self) -> None:
        self.assertTrue(rules.judge_https(https_result()).ok)

    def test_redirect_and_forbidden_from_real_server_are_ok(self) -> None:
        # Сертификат проверен — это ответ настоящего сервера, а не заглушка.
        self.assertTrue(rules.judge_https(https_result(status=302, body_size=300)).ok)
        self.assertTrue(rules.judge_https(https_result(status=403, body_size=300)).ok)

    def test_400_means_fakes_reached_the_server(self) -> None:
        outcome = rules.judge_https(https_result(status=400, body_size=300))
        self.assertEqual(outcome.state, rules.PROBE_BLOCKED)
        self.assertIn("фейковые", outcome.reason)

    def test_answer_cut_at_16kb_is_blocked(self) -> None:
        outcome = rules.judge_https(https_result(body_size=16_384, body_cut=True))
        self.assertEqual(outcome.state, rules.PROBE_BLOCKED)
        self.assertIn("по объёму", outcome.reason)

    def test_answer_cut_early_is_blocked_but_cut_after_limit_is_ok(self) -> None:
        self.assertEqual(rules.judge_https(https_result(body_size=2_000, body_cut=True)).state, rules.PROBE_BLOCKED)
        self.assertTrue(rules.judge_https(https_result(body_size=30_000, body_cut=True)).ok)

    def test_small_complete_page_is_ok(self) -> None:
        self.assertTrue(rules.judge_https(https_result(body_size=5_000, body_cut=False)).ok)

    def test_dpi_failures_are_blocked_and_connect_failure_is_unreachable(self) -> None:
        for kind in (tls_probe.KIND_TLS, tls_probe.KIND_RESET, tls_probe.KIND_TIMEOUT, tls_probe.KIND_ERROR):
            with self.subTest(kind=kind):
                self.assertEqual(rules.judge_https(https_result(kind, status=None)).state, rules.PROBE_BLOCKED)
        cert = rules.judge_https(https_result(tls_probe.KIND_CERT, status=None, cert_problem="самодельный сертификат"))
        self.assertEqual(cert.state, rules.PROBE_BLOCKED)
        self.assertIn("самодельный", cert.reason)
        self.assertEqual(
            rules.judge_https(https_result(tls_probe.KIND_CONNECT, status=None, stage="connect")).state,
            rules.PROBE_UNREACHABLE,
        )


class HttpBodyCompleteTests(unittest.TestCase):
    def test_complete_by_length_chunked_and_empty(self) -> None:
        from blockcheck.strategy_search.probes import http_body_complete

        self.assertTrue(http_body_complete(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"))
        self.assertFalse(http_body_complete(b"HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\nhello"))
        self.assertTrue(http_body_complete(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n"))
        self.assertFalse(http_body_complete(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhel"))
        self.assertTrue(http_body_complete(b"HTTP/1.1 204 No Content\r\n\r\n"))
        self.assertFalse(http_body_complete(b"HTTP/1.1 200 OK\r\nContent-Len"))


class BaselineRuleTests(unittest.TestCase):
    OK = ProbeOutcome(rules.PROBE_OK, "HTTP 200")
    BLOCKED = ProbeOutcome(rules.PROBE_BLOCKED, "соединение сброшено")
    GONE = ProbeOutcome(rules.PROBE_UNREACHABLE, "сервер не отвечает на подключение")

    def test_blocked_addresses_are_pinned_one_per_family(self) -> None:
        decision = rules.decide_baseline(
            [("1.1.1.1", self.BLOCKED), ("1.1.1.2", self.BLOCKED), ("2a00::1", self.BLOCKED), ("1.1.1.3", self.OK)]
        )
        self.assertEqual(decision.state, rules.BASELINE_BLOCKED)
        self.assertEqual(decision.probe_addresses, ("1.1.1.1", "2a00::1"))

    def test_all_open_is_open(self) -> None:
        self.assertEqual(rules.decide_baseline([("1.1.1.1", self.OK)]).state, rules.BASELINE_OPEN)

    def test_unreachable_is_not_dpi(self) -> None:
        decision = rules.decide_baseline([("1.1.1.1", self.GONE), ("2a00::1", self.GONE)])
        self.assertEqual(decision.state, rules.BASELINE_NOT_DPI)
        self.assertEqual(rules.decide_baseline([]).state, rules.BASELINE_NOT_DPI)

    def test_unreachable_ipv6_does_not_hide_blocked_ipv4(self) -> None:
        decision = rules.decide_baseline([("2a00::1", self.GONE), ("1.1.1.1", self.BLOCKED)])
        self.assertEqual(decision.probe_addresses, ("1.1.1.1",))

    def test_attempt_needs_every_address(self) -> None:
        self.assertTrue(rules.attempt_passed([self.OK, self.OK]))
        self.assertFalse(rules.attempt_passed([self.OK, self.BLOCKED]))
        self.assertFalse(rules.attempt_passed([]))

    def test_only_ambiguous_failures_need_network_check(self) -> None:
        self.assertFalse(rules.attempt_needs_network_check([self.BLOCKED]))
        self.assertTrue(rules.attempt_needs_network_check([ProbeOutcome(rules.PROBE_BLOCKED, "нет ответа (таймаут)")]))
        self.assertTrue(rules.attempt_needs_network_check([self.GONE]))

    def test_final_verdict(self) -> None:
        self.assertEqual(rules.final_verdict([True, True, True]), rules.VERDICT_WORKING)
        self.assertEqual(rules.final_verdict([True, False]), rules.VERDICT_UNSTABLE)
        self.assertEqual(rules.final_verdict([False]), rules.VERDICT_FAILED)
        self.assertEqual(rules.final_verdict([]), rules.VERDICT_FAILED)


class UdpRuleTests(unittest.TestCase):
    primary = UdpProbeSpec("Цель", "stun", "stun.example", 3478, "9.9.9.9", primary=True)
    canary = UdpProbeSpec("Rust", "source_a2s", "205.0.0.1", 28015, "205.0.0.1")
    OK = ProbeOutcome(rules.PROBE_OK)
    SILENT = ProbeOutcome(rules.PROBE_BLOCKED, "нет ответа (таймаут)")

    def test_baseline_keeps_only_silent_probes(self) -> None:
        state, chosen, _reason = rules.decide_udp_baseline([(self.primary, self.OK), (self.canary, self.SILENT)])
        self.assertEqual(state, rules.BASELINE_BLOCKED)
        self.assertEqual(chosen, (self.canary,))

    def test_primary_must_open_when_it_was_blocked(self) -> None:
        self.assertFalse(rules.udp_attempt_passed([(self.primary, self.SILENT), (self.canary, self.OK)]))
        self.assertTrue(rules.udp_attempt_passed([(self.primary, self.OK), (self.canary, self.SILENT)]))

    def test_any_blocked_canary_is_enough_without_primary(self) -> None:
        other = UdpProbeSpec("CS", "source_a2s", "46.0.0.1", 27015, "46.0.0.1")
        self.assertTrue(rules.udp_attempt_passed([(self.canary, self.SILENT), (other, self.OK)]))
        self.assertFalse(rules.udp_attempt_passed([]))


class OrderingTests(unittest.TestCase):
    @staticmethod
    def make(strategy_id: str, *functions: str) -> Candidate:
        return Candidate(strategy_id, strategy_id, "\n".join(f"--lua-desync={name}:x=1" for name in functions))

    def test_confirmed_first_then_techniques_interleaved_then_recent_failures(self) -> None:
        candidates = [
            self.make("pass", "pass"),
            self.make("fake1", "fake"),
            self.make("fake2", "fake"),
            self.make("fake3", "fake"),
            self.make("split1", "multisplit"),
            self.make("combo", "fake", "multisplit"),
            self.make("old_fail", "syndata"),
        ]
        ordered = order_candidates(
            candidates,
            confirmed_ids=["fake3"],
            failed_at={"fake2": 1000.0 - 60, "old_fail": 1000.0 - FAILED_MEMORY_SECONDS - 1},
            now=1000.0,
        )
        ids = [candidate.strategy_id for candidate in ordered]
        self.assertNotIn("pass", ids)
        self.assertEqual(ids[0], "fake3")
        # Разные приёмы идут раньше повторов одного приёма.
        self.assertEqual(ids[1:5], ["fake1", "split1", "combo", "old_fail"])
        # Недавно не сработавшая — в самом конце.
        self.assertEqual(ids[-1], "fake2")

    def test_batch_sizes(self) -> None:
        ordered = [self.make(f"s{i}", "fake") for i in range(100)]
        self.assertEqual(len(batch_for_mode(ordered, "quick")), 30)
        self.assertEqual(len(batch_for_mode(ordered, "standard")), 80)
        self.assertEqual(len(batch_for_mode(ordered, "full")), 100)


class ProbeProfileTests(unittest.TestCase):
    def test_tcp_profile_and_config_text(self) -> None:
        profile = build_probe_profile(
            "tcp_https",
            strategy_args="--lua-desync=fake:blob=tls_google\n--lua-desync=multisplit:pos=1",
            match_domain="Discord.com",
        )
        self.assertEqual(
            profile.apply_lines(),
            [
                "--wf-tcp-out=443",
                "--filter-tcp=443",
                "--hostlist-domains=discord.com",
                "--out-range=-d8",
                "--lua-desync=fake:blob=tls_google",
                "--lua-desync=multisplit:pos=1",
            ],
        )
        text = build_probe_config_text(profile, ["--blob=tls_google:@bin/tls_google.bin"])
        lines = text.splitlines()
        lua_init = [line for line in lines if line.startswith("--lua-init=")]
        self.assertEqual(len(lua_init), 8)
        # Фейки объявлены до профиля, как в обычном пресете.
        self.assertLess(lines.index("--blob=tls_google:@bin/tls_google.bin"), lines.index("--filter-tcp=443"))
        self.assertEqual(lines[-1], "--lua-desync=multisplit:pos=1")

    def test_voice_and_games_profiles(self) -> None:
        voice = build_probe_profile("stun_voice", strategy_args="--lua-desync=fake")
        self.assertEqual(voice.preamble_lines, ("--wf-udp-out=443-65535",))
        self.assertIn("--payload=stun,discord_ip_discovery", voice.filter_lines)

        games = build_probe_profile(
            "udp_games",
            strategy_args="--lua-desync=fake",
            games_ipset_paths=["lists/ipset-roblox.txt"],
            games_addresses=["9.9.9.9", "9.9.9.9", "1.1.1.1"],
        )
        self.assertEqual(
            games.filter_lines,
            ("--filter-udp=443,50000-65535", "--ipset=lists/ipset-roblox.txt", "--ipset-ip=9.9.9.9,1.1.1.1"),
        )
        with_probe_ports = build_probe_profile("udp_games", strategy_args="--lua-desync=fake", games_ports=[3478, 443, 60000, 3478])
        self.assertEqual(with_probe_ports.preamble_lines, ("--wf-udp-out=443,50000-65535,3478",))


class HistoryTests(unittest.TestCase):
    def test_record_results_moves_confirmed_to_front_and_forgets_their_failures(self) -> None:
        from blockcheck.strategy_search import history

        section = {"strategy_history": {"tcp_https|discord.com": {"confirmed": ["old"], "failed": {"new": 1.0}}}}

        def fake_update(mutator):
            mutator(section)
            return section

        with patch("settings.store.update_blockcheck_settings", side_effect=fake_update):
            history.record_results("tcp_https|discord.com", confirmed=["new"], failed=["bad"], now=5.0)

        entry = section["strategy_history"]["tcp_https|discord.com"]
        self.assertEqual(entry["confirmed"], ["new", "old"])
        self.assertEqual(entry["failed"], {"bad": 5.0})

    def test_count_recent_failures_ignores_forgotten_ones(self) -> None:
        from blockcheck.strategy_search import history

        now = 10_000_000.0
        section = {
            "strategy_history": {
                "tcp_https|discord.com": {
                    "confirmed": ["good"],
                    "failed": {"fresh": now - 60, "old": now - FAILED_MEMORY_SECONDS - 1},
                }
            }
        }
        with patch("settings.store.get_blockcheck_settings", return_value=section):
            self.assertEqual(history.count_recent_failures("tcp_https|discord.com", now=now), 1)
            self.assertEqual(history.count_recent_failures("tcp_https|youtube.com", now=now), 0)

    def test_settings_normalize_keeps_history_shape(self) -> None:
        from settings.normalize import normalize_blockcheck

        normalized = normalize_blockcheck(
            {"strategy_history": {"TCP_HTTPS|Discord.com": {"confirmed": ["a", "a", ""], "failed": {"b": "7", "": 1, "c": "x"}}}}
        )
        self.assertEqual(
            normalized["strategy_history"],
            {"tcp_https|discord.com": {"confirmed": ["a"], "failed": {"b": 7.0}}},
        )
        self.assertNotIn("scan_resume", normalized)


if __name__ == "__main__":
    unittest.main()
