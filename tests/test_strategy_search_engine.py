"""Порядок работы подбора стратегии на подставной сети и подставном winws2.

Каждый тест описывает сеть функцией «ответ на проверку при такой-то
запущенной стратегии» и смотрит, что подбор решил и сообщил.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from blockcheck.strategy_search import verdict as rules
from blockcheck.strategy_search.engine import SearchRequest, StrategySearch
from blockcheck.strategy_search.history import TargetHistory
from blockcheck.strategy_search.ordering import Candidate
from blockcheck.strategy_search.verdict import ProbeOutcome
from blockcheck.strategy_search.winws_session import SessionStart

OK = ProbeOutcome(rules.PROBE_OK, "HTTP 200", 120.0)
RESET = ProbeOutcome(rules.PROBE_BLOCKED, "соединение сброшено", 30.0)
TIMEOUT = ProbeOutcome(rules.PROBE_BLOCKED, "нет шифрования (таймаут)", 4000.0)
NO_CONNECT = ProbeOutcome(rules.PROBE_UNREACHABLE, "сервер не отвечает на подключение", 4000.0)


def cand(strategy_id: str, function: str = "fake") -> Candidate:
    return Candidate(strategy_id, strategy_id, f"--lua-desync={function}:id={strategy_id}")


def args_id(args: str | None) -> str | None:
    """Кто сейчас запущен: id стратегии, "pass" или None (winws2 не запущен)."""
    if args is None:
        return None
    if args.strip() == "--lua-desync=pass":
        return "pass"
    return args.split("id=", 1)[1].split()[0] if "id=" in args else args


class FakeSession:
    def __init__(self, env: "FakeEnv", config_text: str) -> None:
        self.env = env
        self.config_text = config_text
        self.strategy = next(
            (line.split("id=", 1)[1] for line in config_text.splitlines() if "id=" in line),
            "pass" if "--lua-desync=pass" in config_text else None,
        )

    def start(self):
        if self.env.start_fails.get(self.strategy, 0) > 0:
            self.env.start_fails[self.strategy] -= 1
            return SessionStart(False, exit_code=1, output_tail="lua error")
        self.env.running = self.strategy
        return SessionStart(True, ready_ms=100.0)

    def alive(self) -> bool:
        if self.env.die_during_probe.get(self.strategy, 0) > 0:
            self.env.die_during_probe[self.strategy] -= 1
            return False
        return True

    def stop(self) -> bool:
        self.env.running = None
        self.env.stops += 1
        return True

    def output_tail(self, lines: int = 6) -> str:
        return "crash"


@dataclass
class FakeEnv:
    network: object = None  # (running_id, call_number) -> ProbeOutcome
    candidates: list | None = None
    addresses: tuple = ("1.1.1.10", "1.1.1.11")
    resolve_error: str = ""
    control: list | None = None  # последовательность ответов control_alive
    port80_open: bool = False
    control_default: bool = True

    def __post_init__(self):
        self.running = None
        self.sessions: list[FakeSession] = []
        self.calls: dict = {}
        self.start_fails: dict = {}
        self.die_during_probe: dict = {}
        self.stops = 0
        self.pre_cleaned = False
        self.post_cleaned = False
        self.recovered = 0
        self.history_saved = None
        self.slept = 0.0
        self.clock = 0.0
        self.control = list(self.control or [])

    # каталог и история
    def load_candidates(self, scan_protocol):
        return list(self.candidates or [])

    def load_history(self, key):
        return TargetHistory()

    def record_history(self, key, *, confirmed, failed):
        self.history_saved = (key, list(confirmed), list(failed))

    def blob_lines(self, strategy_args):
        return []

    def games_ipset_paths(self, scope):
        return ["lists/ipset-roblox.txt"]

    # процессы
    def pre_cleanup(self):
        self.pre_cleaned = True

    def post_cleanup(self):
        self.post_cleaned = True

    def start_session(self, config_text):
        session = FakeSession(self, config_text)
        self.sessions.append(session)
        return session

    def recover_after_crash(self):
        self.recovered += 1

    def strategy_pause_seconds(self):
        return 0.0

    # сеть
    def control_alive(self):
        return self.control.pop(0) if self.control else self.control_default

    def resolve(self, host, port, *, udp):
        if self.resolve_error:
            return [], self.resolve_error
        return list(self.addresses), ""

    def _answer(self):
        key = self.running
        number = self.calls.get(key, 0)
        self.calls[key] = number + 1
        return self.network(key, number)

    def probe_https(self, host, addresses):
        outcome = self._answer()
        return [outcome for _address in addresses]

    def probe_udp(self, specs):
        return [(spec, self.network(self.running, spec)) for spec in specs]

    def tcp_port_open(self, address, port):
        return self.port80_open

    def cancel_probes(self):
        pass

    # время
    def monotonic(self):
        self.clock += 0.01
        return self.clock

    def wall_time(self):
        return 1_000_000.0

    def sleep(self, seconds):
        self.slept += seconds
        self.clock += seconds


class FakeEvents:
    def __init__(self, answer: bool = False) -> None:
        self.answer = answer
        self.questions: list[str] = []
        self.results = []
        self.logs: list[str] = []
        self.cancel_after_results: int | None = None
        self.started_args: list[str] = []
        self.stages: list[tuple[str, str]] = []

    def log(self, message):
        self.logs.append(message)

    def phase(self, text):
        pass

    def strategy_started(self, name, index, total, args=""):
        self.started_args.append(args)

    def stage(self, step, status, text=""):
        self.stages.append((step, status))

    def strategy_result(self, result):
        self.results.append(result)

    def ask_continue(self, reason):
        self.questions.append(reason)
        return self.answer

    def is_cancelled(self):
        return self.cancel_after_results is not None and len(self.results) >= self.cancel_after_results


def run(env: FakeEnv, events: FakeEvents | None = None, **request):
    events = events or FakeEvents()
    params = {"target": "discord.com", "mode": "quick"}
    params.update(request)
    report = StrategySearch(SearchRequest(**params), env=env, events=events).run()
    return report, events


def blocked_unless(*working: str):
    """Цель закрыта (сброс), пока не запущена одна из рабочих стратегий."""

    def network(running, _number):
        return OK if running in working else RESET

    return network


class BaselineTests(unittest.TestCase):
    def test_open_without_bypass_asks_and_stops_when_declined(self) -> None:
        env = FakeEnv(network=lambda running, n: OK, candidates=[cand("a"), cand("b")])
        report, events = run(env, FakeEvents(answer=False))

        self.assertEqual(len(events.questions), 1)
        self.assertIn("без обхода", events.questions[0])
        self.assertTrue(report.baseline_accessible)
        self.assertEqual(report.total_tested, 0)
        self.assertEqual(env.sessions, [])
        self.assertTrue(env.post_cleaned)

    def test_open_without_bypass_continue_tests_all_without_controls(self) -> None:
        env = FakeEnv(network=lambda running, n: OK, candidates=[cand("a"), cand("b")])
        report, events = run(env, FakeEvents(answer=True))

        self.assertTrue(report.baseline_accessible)
        self.assertEqual(report.total_tested, 2)
        # Ни контрольного pass, ни обратной проверки: цель и так открыта.
        self.assertEqual([session.strategy for session in env.sessions], ["a", "b"])
        self.assertTrue(all(result.raw_data.get("forced") for result in events.results))

    def test_connect_refused_everywhere_is_not_a_dpi_block(self) -> None:
        env = FakeEnv(network=lambda running, n: NO_CONNECT, candidates=[cand("a")], port80_open=True)
        report, _events = run(env)

        self.assertIn("порт 80 открыт", report.fatal_error)
        self.assertIn("не снимают", report.fatal_error)
        self.assertEqual(env.sessions, [])
        self.assertTrue(report.cancelled)

    def test_unresolvable_target_stops(self) -> None:
        env = FakeEnv(network=lambda running, n: OK, candidates=[cand("a")], resolve_error="нет такого имени")
        report, _events = run(env)

        self.assertIn("нет такого имени", report.fatal_error)
        self.assertEqual(env.sessions, [])

    def test_dns_stub_address_stops_with_dns_advice(self) -> None:
        env = FakeEnv(network=lambda running, n: RESET, candidates=[cand("a")], addresses=("195.82.146.214",))
        report, _events = run(env)

        self.assertIn("Настройка DNS", report.fatal_error)
        self.assertEqual(env.sessions, [])

    def test_no_internet_stops_before_anything(self) -> None:
        env = FakeEnv(network=lambda running, n: RESET, candidates=[cand("a")], control=[False])
        report, _events = run(env)

        self.assertIn("Нет интернета", report.fatal_error)
        self.assertEqual(env.sessions, [])
        self.assertTrue(env.pre_cleaned)
        self.assertTrue(env.post_cleaned)

    def test_pass_control_runs_first_and_passing_pass_asks_user(self) -> None:
        env = FakeEnv(network=blocked_unless("pass"), candidates=[cand("a")])
        report, events = run(env, FakeEvents(answer=False))

        self.assertEqual([session.strategy for session in env.sessions], ["pass"])
        self.assertEqual(len(events.questions), 1)
        self.assertIn("без приёмов обхода", events.questions[0])
        self.assertEqual(report.total_tested, 0)


class StrategyVerdictTests(unittest.TestCase):
    def test_working_strategy_needs_three_passes_and_carries_apply_lines(self) -> None:
        env = FakeEnv(network=blocked_unless("good"), candidates=[cand("bad"), cand("good", "multisplit")])
        report, events = run(env)

        by_id = {result.strategy_id: result for result in events.results}
        good = by_id["good"]
        self.assertTrue(good.success)
        self.assertEqual(good.verdict, rules.VERDICT_WORKING)
        self.assertEqual((good.attempts_ok, good.attempts_total), (3, 3))
        self.assertEqual(env.calls["good"], 3)
        # «Применить» получит ровно проверенный профиль.
        good_config = next(session.config_text for session in env.sessions if session.strategy == "good")
        for line in good.apply_lines:
            self.assertIn(line, good_config)
        self.assertIn("--hostlist-domains=discord.com", good.apply_lines)
        self.assertFalse(by_id["bad"].success)
        self.assertEqual(by_id["bad"].apply_lines, ())
        self.assertEqual([result.strategy_id for result in report.working_strategies], ["good"])

    def test_strategy_that_fails_on_recheck_is_unstable_not_working(self) -> None:
        def network(running, number):
            if running == "flaky":
                return OK if number == 0 else TIMEOUT
            return RESET

        env = FakeEnv(network=network, candidates=[cand("flaky")])
        report, events = run(env)

        result = events.results[0]
        self.assertEqual(result.verdict, rules.VERDICT_UNSTABLE)
        self.assertFalse(result.success)
        self.assertEqual(report.working_strategies, [])

    def test_success_is_not_counted_when_target_opens_without_bypass_afterwards(self) -> None:
        state = {"flipped": False}

        def network(running, number):
            if running == "lucky":
                state["flipped"] = True
                return OK
            if running is None and state["flipped"]:
                return OK  # сеть «починилась» сама — стратегия тут ни при чём
            return RESET

        env = FakeEnv(network=network, candidates=[cand("lucky")])
        _report, events = run(env)

        self.assertEqual(events.results[0].verdict, rules.VERDICT_NOT_COUNTED)
        self.assertFalse(events.results[0].success)

    def test_winws2_crash_during_probe_is_retried_not_counted_as_failure(self) -> None:
        env = FakeEnv(network=blocked_unless("good"), candidates=[cand("good")])
        env.die_during_probe["good"] = 1
        _report, events = run(env)

        self.assertEqual(events.results[0].verdict, rules.VERDICT_WORKING)
        self.assertEqual(env.recovered, 1)

    def test_strategy_that_never_starts_is_reported_as_crash(self) -> None:
        env = FakeEnv(network=blocked_unless(), candidates=[cand("broken")])
        env.start_fails["broken"] = 99
        report, events = run(env)

        self.assertEqual(events.results[0].verdict, rules.VERDICT_CRASH)
        # Сбой winws2 не записывается в «не сработала».
        self.assertEqual(env.history_saved[2], [])
        self.assertEqual(report.working_strategies, [])

    def test_network_drop_rechecks_strategy_instead_of_failing_it(self) -> None:
        state = {"down": True}

        def network(running, number):
            if running == "good" and not state["down"]:
                return OK
            if running == "good":
                state["down"] = False  # первая проверка попала на пропавший интернет
                return TIMEOUT
            return RESET

        # 1) перед подбором сеть есть; 2) после таймаута — нет; 3) через паузу вернулась.
        env = FakeEnv(network=network, candidates=[cand("good")], control=[True, False, True])
        _report, events = run(env)

        self.assertEqual(len(events.results), 1)
        self.assertEqual(events.results[0].verdict, rules.VERDICT_WORKING)
        self.assertGreater(env.slept, 0)

    def test_network_that_never_returns_stops_the_scan(self) -> None:
        env = FakeEnv(
            network=lambda running, n: TIMEOUT,
            candidates=[cand("a"), cand("b")],
            control=[True],
            control_default=False,
        )
        report, events = run(env)

        self.assertIn("пропал интернет", report.fatal_error)
        self.assertEqual(events.results, [])

    def test_reset_failure_does_not_waste_time_on_network_check(self) -> None:
        env = FakeEnv(network=blocked_unless(), candidates=[cand("a")], control=[True, False])
        _report, events = run(env)

        # Сброс — след работы DPI: сеть жива, второй ответ control не спрашивался.
        self.assertEqual(env.control, [False])
        self.assertEqual(events.results[0].verdict, rules.VERDICT_FAILED)


class StageEventTests(unittest.TestCase):
    def test_successful_scan_walks_all_steps(self) -> None:
        env = FakeEnv(network=blocked_unless("good"), candidates=[cand("good")])
        _report, events = run(env)

        done = [step for step, status in events.stages if status == "done"]
        self.assertEqual(done, ["network", "baseline", "control", "strategies"])
        self.assertIn("--lua-desync=fake:id=good", events.started_args)

    def test_stop_marks_running_step_failed_and_reports_kind(self) -> None:
        env = FakeEnv(network=lambda running, n: NO_CONNECT, candidates=[cand("a")], port80_open=True)
        report, events = run(env)

        self.assertEqual(report.stop_kind, "address_block")
        self.assertIn(("baseline", "failed"), events.stages)

    def test_no_internet_kind(self) -> None:
        env = FakeEnv(network=lambda running, n: RESET, candidates=[cand("a")], control=[False])
        report, events = run(env)

        self.assertEqual(report.stop_kind, "no_internet")
        self.assertIn(("network", "failed"), events.stages)


class BatchTests(unittest.TestCase):
    def test_whole_quick_batch_is_tested_even_after_finding_working(self) -> None:
        candidates = [cand(f"s{i}", f"f{i % 5}") for i in range(40)]
        env = FakeEnv(network=blocked_unless("s0", "s1", "s2", "s3"), candidates=candidates)
        report, events = run(env)

        self.assertEqual(report.total_available, 40)
        self.assertEqual(report.total_tested, 30)
        self.assertEqual(len(events.results), 30)

    def test_history_records_confirmed_and_failed(self) -> None:
        env = FakeEnv(network=blocked_unless("good"), candidates=[cand("good"), cand("bad", "multisplit")])
        run(env)

        key, confirmed, failed = env.history_saved
        self.assertEqual(key, "tcp_https|discord.com")
        self.assertEqual(confirmed, ["good"])
        self.assertEqual(failed, ["bad"])

    def test_recent_failures_go_last_unless_started_over(self) -> None:
        candidates = [cand("a", "fake"), cand("b", "multisplit")]

        class HistoryEnv(FakeEnv):
            def load_history(self, key):
                # «a» не сработала минуту назад — обычный запуск отодвигает её в конец.
                return TargetHistory(failed={"a": 1_000_000.0 - 60})

        _report, events = run(HistoryEnv(network=blocked_unless(), candidates=candidates))
        self.assertEqual([result.strategy_id for result in events.results], ["b", "a"])

        started_over = HistoryEnv(network=blocked_unless(), candidates=candidates)
        _report, events = run(started_over, from_start=True)
        self.assertEqual([result.strategy_id for result in events.results], ["a", "b"])
        self.assertTrue(any("заново" in line for line in events.logs))
        # История не стирается: итоги нового прохода дописываются как обычно.
        self.assertEqual(started_over.history_saved[2], ["a", "b"])

    def test_forced_scan_does_not_pollute_history(self) -> None:
        env = FakeEnv(network=lambda running, n: OK, candidates=[cand("a")])
        run(env, FakeEvents(answer=True))

        self.assertIsNone(env.history_saved)

    def test_pass_strategy_from_catalog_is_never_a_candidate(self) -> None:
        env = FakeEnv(
            network=blocked_unless(),
            candidates=[Candidate("pass", "pass", "--lua-desync=pass"), cand("a")],
        )
        report, events = run(env)

        self.assertEqual([result.strategy_id for result in events.results], ["a"])
        self.assertEqual(report.total_available, 1)

    def test_cancel_stops_and_cleans_up(self) -> None:
        env = FakeEnv(network=blocked_unless(), candidates=[cand(f"s{i}") for i in range(5)])
        events = FakeEvents()
        events.cancel_after_results = 2
        report, events = run(env, events)

        self.assertTrue(report.cancelled)
        self.assertEqual(len(events.results), 2)
        self.assertTrue(env.post_cleaned)
        self.assertIsNone(env.running)

    def test_googlevideo_probe_host_differs_from_profile_domain(self) -> None:
        env = FakeEnv(network=blocked_unless("good"), candidates=[cand("good")])
        seen_hosts = []
        original = env.probe_https

        def probe_https(host, addresses):
            seen_hosts.append(host)
            return original(host, addresses)

        env.probe_https = probe_https
        _report, events = run(env, target="googlevideo.com", probe_host="rr1---sn-abc.googlevideo.com")

        self.assertEqual(set(seen_hosts), {"rr1---sn-abc.googlevideo.com"})
        self.assertIn("--hostlist-domains=googlevideo.com", events.results[0].apply_lines)


class UdpTests(unittest.TestCase):
    def test_games_success_ignores_probes_open_without_bypass(self) -> None:
        # Основная цель (Google STUN) открыта и без обхода, игровой сервер закрыт.
        def network(running, spec):
            if spec.primary:
                return OK
            if spec.name == "Rust A2S" and running == "good":
                return OK
            return TIMEOUT

        env = FakeEnv(
            network=network,
            candidates=[cand("useless"), cand("good", "udplen")],
            addresses=("9.9.9.9",),
        )
        report, events = run(env, target="stun.l.google.com:19302", scan_protocol="udp_games")

        by_id = {result.strategy_id: result for result in events.results}
        self.assertFalse(by_id["useless"].success)
        self.assertTrue(by_id["good"].success)
        profile = by_id["good"].apply_lines
        self.assertIn("--ipset=lists/ipset-roblox.txt", profile)
        self.assertIn("--ipset-ip=9.9.9.9", profile)

    def test_games_profile_intercepts_every_probe_port(self) -> None:
        env = FakeEnv(network=lambda running, spec: OK if running == "good" else TIMEOUT, candidates=[cand("good")])
        _report, events = run(env, target="stun.cloudflare.com:3478", scan_protocol="udp_games")

        lines = events.results[0].apply_lines
        wf = next(line for line in lines if line.startswith("--wf-udp-out="))
        flt = next(line for line in lines if line.startswith("--filter-udp="))
        # Иначе winws2 не видит ни одного проверяемого пакета и стратегия ни при чём.
        for port in (3478, 28015, 27015, 19132):
            self.assertIn(str(port), wf.split("=", 1)[1].split(","))
            self.assertIn(str(port), flt.split("=", 1)[1].split(","))
        self.assertEqual(wf.split("=", 1)[1], flt.split("=", 1)[1])

    def test_voice_requires_the_users_target(self) -> None:
        def network(running, spec):
            return OK if running == "good" else TIMEOUT

        env = FakeEnv(network=network, candidates=[cand("good"), cand("bad", "udplen")], addresses=("8.8.8.8",))
        _report, events = run(env, target="stun.l.google.com:19302", scan_protocol="stun_voice")

        by_id = {result.strategy_id: result for result in events.results}
        self.assertTrue(by_id["good"].success)
        self.assertFalse(by_id["bad"].success)
        self.assertIn("--filter-l7=stun,discord", by_id["good"].apply_lines)


if __name__ == "__main__":
    unittest.main()
