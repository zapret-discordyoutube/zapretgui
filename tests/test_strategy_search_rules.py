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
        self.assertTrue(rules.judge_https(https_result(body_size=38_000, body_cut=True)).ok)

    def test_strategy_uses_the_same_cut_window_as_site_check(self) -> None:
        # Сайт, замерший на 30 КБ, проверка сайтов называет обрывом по объёму —
        # значит, и стратегия, после которой так бывает, не работает.
        from diagnostics.limits import FREEZE_MAX_BYTES, FREEZE_MIN_BYTES

        outcome = rules.judge_https(https_result(body_size=30_000, body_cut=True))
        self.assertEqual(outcome.state, rules.PROBE_BLOCKED)
        self.assertIn("по объёму", outcome.reason)
        self.assertIn("по объёму", rules.judge_https(https_result(body_size=FREEZE_MIN_BYTES, body_cut=True)).reason)
        # Читаем больше верхней границы окна, иначе обрыв у её края не увидеть.
        self.assertGreater(rules.HTTPS_READ_LIMIT, FREEZE_MAX_BYTES)

    def test_http_body_cut_after_status_is_blocked(self) -> None:
        # Без шифрования заголовок 200 приходит, а тело фильтр рвёт на полпути.
        cut = rules.judge_http(host="example.org", status=200, failure="reset", body_cut=True, body_size=20_000)
        self.assertEqual(cut.state, rules.PROBE_BLOCKED)
        self.assertIn("по объёму", cut.reason)
        early = rules.judge_http(host="example.org", status=200, failure="reset", body_cut=True, body_size=3_000)
        self.assertEqual(early.state, rules.PROBE_BLOCKED)
        self.assertIn("оборвался", early.reason)
        self.assertTrue(rules.judge_http(host="example.org", status=200).ok)

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
        self.assertTrue(rules.attempt_needs_network_check([ProbeOutcome(rules.PROBE_BLOCKED, "нет ответа (таймаут)", failure="timeout")]))
        self.assertTrue(rules.attempt_needs_network_check([self.GONE]))

    def test_network_check_follows_the_failure_kind_not_the_wording(self) -> None:
        # Правило не ищет слово «таймаут» в тексте: формулировку можно менять, вид сбоя — нет.
        self.assertFalse(rules.attempt_needs_network_check([ProbeOutcome(rules.PROBE_BLOCKED, "нет ответа (таймаут)", failure="reset")]))
        self.assertTrue(rules.attempt_needs_network_check([ProbeOutcome(rules.PROBE_BLOCKED, "сервер замолчал", failure="timeout")]))

    def test_https_result_carries_the_failure_kind(self) -> None:
        timeout = rules.judge_https(tls_probe.ProbeResult(ip="1.1.1.1", kind=tls_probe.KIND_TIMEOUT, stage=tls_probe.STAGE_TLS))
        no_status = rules.judge_https(tls_probe.ProbeResult(ip="1.1.1.1", kind=tls_probe.KIND_OK))
        silent_connect = rules.judge_https(
            tls_probe.ProbeResult(ip="1.1.1.1", kind=tls_probe.KIND_CONNECT, connect_fail=tls_probe.CONNECT_TIMEOUT)
        )
        reset = rules.judge_https(tls_probe.ProbeResult(ip="1.1.1.1", kind=tls_probe.KIND_RESET))

        self.assertEqual((timeout.failure, no_status.failure, silent_connect.failure), ("timeout", "closed", "timeout"))
        self.assertTrue(rules.attempt_needs_network_check([timeout]))
        self.assertTrue(rules.attempt_needs_network_check([no_status]))
        self.assertFalse(rules.attempt_needs_network_check([reset]))

    def test_final_verdict(self) -> None:
        self.assertEqual(rules.final_verdict([True, True, True]), rules.VERDICT_WORKING)
        self.assertEqual(rules.final_verdict([True, False]), rules.VERDICT_UNSTABLE)
        self.assertEqual(rules.final_verdict([False]), rules.VERDICT_FAILED)
        self.assertEqual(rules.final_verdict([]), rules.VERDICT_FAILED)

    def test_one_silence_is_forgiven_only_after_a_success(self) -> None:
        ok, silent, explicit = rules.AttemptMark(True), rules.AttemptMark(False, True), rules.AttemptMark(False)

        self.assertTrue(rules.attempts_done([silent]))
        self.assertEqual(rules.final_verdict([silent]), rules.VERDICT_FAILED)
        self.assertFalse(rules.attempts_done([ok, silent]))
        self.assertTrue(rules.attempts_done([ok, silent, ok, ok]))
        self.assertEqual(rules.final_verdict([ok, silent, ok, ok]), rules.VERDICT_WORKING)
        self.assertTrue(rules.attempts_done([ok, silent, silent]))
        self.assertEqual(rules.final_verdict([ok, silent, silent]), rules.VERDICT_UNSTABLE)
        self.assertTrue(rules.attempts_done([ok, explicit]))
        self.assertEqual(rules.final_verdict([ok, explicit]), rules.VERDICT_UNSTABLE)
        self.assertTrue(rules.attempts_done([ok, ok, ok]))

    def test_silent_attempt_has_no_explicit_dpi_trace(self) -> None:
        timeout = ProbeOutcome(rules.PROBE_BLOCKED, "нет ответа (таймаут)", failure="timeout")
        reset = ProbeOutcome(rules.PROBE_BLOCKED, "соединение сброшено", failure="reset")

        self.assertTrue(rules.attempt_silent([timeout, self.OK]))
        self.assertTrue(rules.attempt_silent([self.GONE]))
        self.assertFalse(rules.attempt_silent([timeout, reset]))
        self.assertFalse(rules.attempt_silent([self.OK]))

    def test_addresses_are_spread_over_subnets(self) -> None:
        picked = rules.spread_addresses(["1.1.1.1", "1.1.1.2", "1.1.2.1", "1.1.3.1", "2a00::1", "2a00::2"])
        self.assertEqual(picked, ["1.1.1.1", "1.1.2.1", "2a00::1"])
        self.assertEqual(rules.spread_addresses(["2a00::1"]), ["2a00::1"])

    def test_short_answer_needs_volume_and_cut_is_blocked(self) -> None:
        from diagnostics import volume_probe

        short = ProbeOutcome(rules.PROBE_OK, "HTTP 200", 50.0, body_size=900)
        long = ProbeOutcome(rules.PROBE_OK, "HTTP 200", 50.0, body_size=60_000)
        self.assertTrue(rules.needs_volume(short))
        self.assertFalse(rules.needs_volume(long))

        cut = rules.judge_volume(short, volume_probe.VolumeVerdict(volume_probe.VOLUME_CUT, "", cut_kb=16))
        self.assertEqual((cut.state, cut.volume), (rules.PROBE_BLOCKED, rules.VOLUME_CUT))
        self.assertIn("16 КБ", cut.reason)
        self.assertFalse(rules.attempt_silent([cut]))
        unknown = rules.judge_volume(short, volume_probe.VolumeVerdict(volume_probe.VOLUME_UNKNOWN, ""))
        self.assertTrue(unknown.ok)
        self.assertFalse(rules.needs_volume(unknown))


class UdpRuleTests(unittest.TestCase):
    primary = UdpProbeSpec("Цель", "stun", "stun.example", 3478, "9.9.9.9", primary=True)
    canary = UdpProbeSpec("Rust", "source_a2s", "205.0.0.1", 28015, "205.0.0.1")
    OK = ProbeOutcome(rules.PROBE_OK)
    SILENT = ProbeOutcome(rules.PROBE_BLOCKED, "нет ответа (таймаут)", failure="timeout")

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

    def _ids(self, habit: str, **extra) -> list[str]:
        candidates = [
            self.make("split1", "multisplit"),
            self.make("split2", "multisplit"),
            Candidate("overlap", "overlap", "--lua-desync=multisplit:pos=1:seqovl=5"),
            self.make("fake1", "fake"),
            self.make("disorder", "multidisorder"),
            self.make("records", "tlsrec"),
            self.make("other", "udplen"),
        ]
        return [candidate.strategy_id for candidate in order_candidates(candidates, habit=habit, **extra)]

    def test_without_filter_habit_order_is_untouched(self) -> None:
        self.assertEqual(self._ids(""), self._ids("непонятный код"))
        self.assertEqual(self._ids("")[:2], ["split1", "fake1"])

    def test_filter_that_reassembles_everything_puts_plain_split_last(self) -> None:
        ids = self._ids("none")

        # Подделки и наложение кусков — вперёд, простое дробление — в конец; ничего не выброшено.
        self.assertEqual(set(ids[:2]), {"overlap", "fake1"})
        self.assertEqual(ids[-2:], ["split1", "split2"])
        self.assertEqual(len(ids), 7)

    def test_filter_that_does_not_reassemble_packets_puts_split_first(self) -> None:
        ids = self._ids("tcp")

        self.assertEqual(set(ids[:4]), {"split1", "split2", "overlap", "disorder"})

    def test_record_split_goes_first_when_only_records_pass(self) -> None:
        ids = self._ids("records")

        self.assertLess(ids.index("records"), ids.index("disorder"))
        self.assertEqual(ids[-2:], ["split1", "split2"])

    def test_confirmed_strategy_stays_first_whatever_the_habit(self) -> None:
        self.assertEqual(self._ids("none", confirmed_ids=["split2"])[0], "split2")

    def test_habit_note_exists_only_for_known_habits(self) -> None:
        from blockcheck.strategy_search.ordering import habit_note

        self.assertIn("целиком", habit_note("none"))
        self.assertEqual(habit_note(""), "")
        self.assertEqual(habit_note("not_by_name"), "")

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
    def test_fresh_filter_habit_is_taken_from_check_history(self) -> None:
        from datetime import datetime

        from blockcheck.strategy_search import history

        now = datetime(2026, 10, 8, 12, 0).timestamp()
        runs = [
            {"time": "2026-10-07T10:00:00", "habit": "tcp"},
            {"time": "2026-10-08T10:00:00", "habit": "none"},
            # Проверка с Zapret вывода о фильтре не даёт — она не отменяет прошлый вывод.
            {"time": "2026-10-08T11:00:00", "habit": ""},
        ]

        self.assertEqual(history.recent_filter_habit(runs, now=now), "none")
        self.assertEqual(history.recent_filter_habit(runs, now=now + FAILED_MEMORY_SECONDS), "")
        self.assertEqual(history.recent_filter_habit([], now=now), "")
        self.assertEqual(history.recent_filter_habit([{"time": "когда-то", "habit": "tcp"}], now=now), "")

    def test_target_history_carries_the_habit(self) -> None:
        from datetime import datetime

        from blockcheck.strategy_search import history

        section = {"strategy_history": {}, "check_history": [{"time": "2026-10-08T10:00:00", "habit": "timer"}]}
        now = datetime(2026, 10, 8, 12, 0).timestamp()
        with patch("settings.store.get_blockcheck_settings", return_value=section):
            self.assertEqual(history.load_target_history("tcp_https|x.com", now=now).habit, "timer")
            self.assertEqual(history.load_target_history("tcp_https|x.com").habit, "")

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


class ControlAliveTests(unittest.TestCase):
    def test_first_open_site_answers_without_waiting_for_slow_ones(self) -> None:
        import time

        from blockcheck.strategy_search import probes

        def fake_get(host, ip, path, **_kwargs):
            if host != probes.CONTROL_HOSTS[0]:
                time.sleep(2.0)
            return tls_probe.ProbeResult(ip=ip, kind=tls_probe.KIND_OK, status=200)

        started = time.monotonic()
        with patch.object(probes, "resolve_target_addresses", return_value=(["192.0.2.1"], "")), patch(
            "utils.chrome_https.chrome_get", side_effect=fake_get
        ):
            self.assertTrue(probes.control_alive())
        self.assertLess(time.monotonic() - started, 1.0)


class BestStrategyTests(unittest.TestCase):
    def test_best_is_the_fastest_reliable_one(self) -> None:
        from blockcheck.strategy_scan_page_plans import best_working_index

        rows = [{"success": False, "time_ms": 5}, {"success": True, "time_ms": 300}, {"success": True, "time_ms": 120}]
        self.assertEqual(best_working_index(rows), 2)
        self.assertEqual(best_working_index([{"success": False}]), -1)


class HttpRuleTests(unittest.TestCase):
    def test_stub_and_400_are_blocks_honest_answer_is_ok(self) -> None:
        stub = rules.judge_http(host="site.ru", status=302, location="http://warning.rt.ru/?id=1")
        self.assertEqual(stub.state, rules.PROBE_BLOCKED)
        self.assertIn("заглушка", stub.reason)
        self.assertEqual(rules.judge_http(host="site.ru", status=451).state, rules.PROBE_BLOCKED)
        self.assertEqual(rules.judge_http(host="site.ru", status=400).state, rules.PROBE_BLOCKED)
        self.assertTrue(rules.judge_http(host="site.ru", status=301, location="https://site.ru/").ok)
        self.assertTrue(rules.judge_http(host="site.ru", status=200, body=b"<html>hello</html>").ok)

    def test_silence_and_reset_are_blocks_no_connect_is_unreachable(self) -> None:
        self.assertEqual(rules.judge_http(host="s.ru", status=None, failure="reset").state, rules.PROBE_BLOCKED)
        self.assertTrue(rules.attempt_silent([rules.judge_http(host="s.ru", status=None, failure="timeout")]))
        self.assertEqual(
            rules.judge_http(host="s.ru", status=None, failure="timeout", connected=False).state, rules.PROBE_UNREACHABLE
        )
