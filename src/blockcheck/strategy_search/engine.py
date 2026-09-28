"""Порядок работы подбора стратегии.

Цепочка одного подбора:

1. Подготовка: стратегии в «умном» порядке (``ordering``), остановка Zapret
   и чистка WinDivert, чтобы ничего постороннего не мешало замерам.
2. Интернет жив? Если не открывается ни один обычный сайт, подбор бессмыслен.
3. Проверка «до подбора» (как в blockcheck2): цель проверяется без обхода.
   - закрыта так, как закрывает DPI, — подбираем на этих же адресах;
   - открыта — спрашиваем пользователя, есть ли смысл продолжать;
   - закрыта раньше шифрования (сервер не принимает подключение) —
     стратегии обхода DPI это не чинят, останавливаемся с объяснением.
4. Контроль: запуск winws2 с пустой стратегией ``pass``. Если цель
   открывается уже от него — любая стратегия «сработает», и замер ненадёжен.
5. Каждая стратегия: запуск winws2 → одна проверка. Прошла — ещё две на
   свежих соединениях (итог 3/3), затем цель проверяется снова без обхода:
   если открылась и так, успех стратегии не засчитывается.
6. Провал похож на пропавшую сеть (таймаут) — проверяем обычные сайты. Сеть
   пропала — ждём и перепроверяем стратегию, а не пишем её в провалы.
7. Конец: чистка WinDivert, история подбора в настройки.

Сеть, процессы и настройки приходят через ``SearchEnvironment``, поэтому
весь порядок работы проверяется тестами с подставными проверками.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from blockcheck.scan_models import StrategyProbeResult, StrategyScanReport
from blockcheck.strategy_search import verdict as rules
from blockcheck.strategy_search.ordering import Candidate, batch_for_mode, order_candidates
from blockcheck.strategy_search.probe_profile import (
    PROTOCOL_STUN_VOICE,
    PROTOCOL_TCP_HTTPS,
    PROTOCOL_UDP_GAMES,
    ProbeProfile,
    build_probe_config_text,
    build_probe_profile,
)
from blockcheck.strategy_search.verdict import ProbeOutcome, UdpProbeSpec

logger = logging.getLogger(__name__)

PASS_STRATEGY_ARGS = "--lua-desync=pass"
# Сколько раз перезапускать стратегию, если winws2 упал (гонка драйвера).
CRASH_RETRIES = 2
# Сколько ждать возвращения интернета, прежде чем остановить подбор.
NETWORK_WAIT_SECONDS = 30.0
NETWORK_POLL_SECONDS = 3.0
# Сколько адресов цели проверять «до подбора».
BASELINE_MAX_ADDRESSES = 4

# Вспомогательные игровые серверы: их ответ тоже показывает, что UDP игр
# проходит. Для голоса проверяется только цель пользователя.
UDP_GAMES_CANARIES: tuple[tuple[str, str, str, int], ...] = (
    ("Rust A2S", "source_a2s", "205.178.168.170", 28015),
    ("CS A2S", "source_a2s", "46.174.55.234", 27015),
    ("Bedrock CubeCraft", "bedrock_ping", "play.cubecraft.net", 19132),
)


class ScanFatal(Exception):
    """Подбор дальше не имеет смысла; текст — объяснение для пользователя."""


@dataclass(frozen=True, slots=True)
class SearchRequest:
    # Что ввёл пользователь: домен для сайтов, host:port для STUN/игр.
    target: str
    scan_protocol: str = PROTOCOL_TCP_HTTPS
    mode: str = "quick"
    udp_games_scope: str = "all"
    # Какой хост проверять, если он отличается от цели (googlevideo.com →
    # конкретный видеосервер rr*.googlevideo.com). Профиль при этом пишется
    # на цель пользователя, чтобы подошёл любой видеосервер.
    probe_host: str = ""


class SearchEvents(Protocol):
    def log(self, message: str) -> None: ...
    def phase(self, text: str) -> None: ...
    def strategy_started(self, name: str, index: int, total: int) -> None: ...
    def strategy_result(self, result: StrategyProbeResult) -> None: ...
    def ask_continue(self, reason: str) -> bool: ...
    def is_cancelled(self) -> bool: ...


class Session(Protocol):
    def start(self): ...
    def alive(self) -> bool: ...
    def stop(self) -> bool: ...
    def output_tail(self, lines: int = 6) -> str: ...


class SearchEnvironment(Protocol):
    """Всё внешнее, что нужно подбору. Реальная версия — ``environment``."""

    def load_candidates(self, scan_protocol: str) -> list[Candidate]: ...
    def load_history(self, key: str): ...
    def record_history(self, key: str, *, confirmed: list[str], failed: list[str]) -> None: ...
    def blob_lines(self, strategy_args: str) -> list[str]: ...
    def games_ipset_paths(self, udp_games_scope: str) -> list[str]: ...

    def pre_cleanup(self) -> None: ...
    def post_cleanup(self) -> None: ...
    def start_session(self, config_text: str) -> Session: ...
    def recover_after_crash(self) -> None: ...
    def strategy_pause_seconds(self) -> float: ...

    def control_alive(self) -> bool: ...
    def resolve(self, host: str, port: int, *, udp: bool) -> tuple[list[str], str]: ...
    def probe_https(self, host: str, addresses: Sequence[str]) -> list[ProbeOutcome]: ...
    def probe_udp(self, specs: Sequence[UdpProbeSpec]) -> list[tuple[UdpProbeSpec, ProbeOutcome]]: ...
    def tcp_port_open(self, address: str, port: int) -> bool: ...
    def cancel_probes(self) -> None: ...

    def monotonic(self) -> float: ...
    def wall_time(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


@dataclass
class _Target:
    """Что именно проверяется у каждой стратегии."""

    host: str = ""
    addresses: tuple[str, ...] = ()
    udp_specs: tuple[UdpProbeSpec, ...] = ()
    games_addresses: tuple[str, ...] = ()
    games_ports: tuple[int, ...] = ()
    # Цель открывалась без обхода, а пользователь решил проверять всё равно.
    forced: bool = False


@dataclass
class _Attempt:
    passed: bool
    reason: str = ""
    time_ms: float = 0.0
    network_suspect: bool = False


@dataclass
class _StrategyRun:
    verdict: str
    attempts: list[_Attempt] = field(default_factory=list)
    reason: str = ""


class StrategySearch:
    def __init__(self, request: SearchRequest, *, env: SearchEnvironment, events: SearchEvents) -> None:
        self._request = request
        self._env = env
        self._events = events
        self._cancelled = False
        self._protocol = request.scan_protocol
        self._target = _Target()

    # --- Управление -----------------------------------------------------------

    @property
    def cancelled(self) -> bool:
        return self._cancelled or bool(self._events.is_cancelled())

    def cancel(self) -> None:
        """Из любого потока: только флаг и обрыв текущих сетевых проверок.

        Процесс winws2 останавливает сам поток подбора, поэтому окно
        программы не ждёт его завершения.
        """
        self._cancelled = True
        try:
            self._env.cancel_probes()
        except Exception:
            logger.debug("cancel_probes failed", exc_info=True)

    # --- Подбор ---------------------------------------------------------------

    def run(self) -> StrategyScanReport:
        started = self._env.monotonic()
        report = StrategyScanReport(target=self._request.target, total_tested=0, scan_protocol=self._protocol)
        working: list[StrategyProbeResult] = []
        failed: list[StrategyProbeResult] = []
        confirmed_ids: list[str] = []
        failed_ids: list[str] = []
        key = self._history_key()
        try:
            batch, total = self._select_batch(key)
            report.total_available = total
            self._events.log(
                f"Подбор: {self._protocol_label()}, цель {self._request.target}, "
                f"проверим {len(batch)} из {total} стратегий"
            )
            self._events.phase("Подготовка")
            self._env.pre_cleanup()
            if self.cancelled:
                return self._finish(report, working, failed, started, cancelled=True)

            self._events.phase("Проверка сети")
            if not self._env.control_alive():
                raise ScanFatal(
                    "Нет интернета: не открывается ни один обычный сайт. "
                    "Проверьте подключение и запустите подбор снова."
                )

            self._events.phase("Проверка без обхода")
            if not self._baseline(report):
                return self._finish(report, working, failed, started, cancelled=self.cancelled)
            if not self._target.forced and not self._pass_control(report):
                return self._finish(report, working, failed, started, cancelled=self.cancelled)

            queue: deque[tuple[Candidate, bool]] = deque((candidate, False) for candidate in batch)
            index = 0
            while queue and not self.cancelled:
                candidate, rechecked = queue.popleft()
                self._events.strategy_started(candidate.name, index, len(batch))
                self._events.phase(f"[{index + 1}/{len(batch)}] {candidate.name}")
                run = self._test_strategy(candidate)
                if self.cancelled and run.verdict == rules.VERDICT_CANCELLED:
                    break
                if run.verdict == rules.VERDICT_FAILED and run.attempts and run.attempts[-1].network_suspect:
                    if not self._env.control_alive():
                        self._events.log("  Похоже, пропал интернет — жду и перепроверю эту стратегию")
                        if not self._wait_for_network():
                            raise ScanFatal("Во время подбора пропал интернет. Подбор остановлен.")
                        if not rechecked:
                            queue.appendleft((candidate, True))
                            continue
                result = self._make_result(candidate, run)
                self._events.strategy_result(result)
                self._log_result(index, len(batch), candidate, run)
                if result.success:
                    working.append(result)
                    confirmed_ids.append(candidate.strategy_id)
                else:
                    failed.append(result)
                    if run.verdict == rules.VERDICT_FAILED:
                        failed_ids.append(candidate.strategy_id)
                index += 1
                pause = self._env.strategy_pause_seconds()
                if pause > 0 and queue:
                    self._env.sleep(pause)
            return self._finish(report, working, failed, started, cancelled=self.cancelled)
        except ScanFatal as fatal:
            report.fatal_error = str(fatal)
            self._events.log(f"СТОП: {fatal}")
            return self._finish(report, working, failed, started, cancelled=True)
        finally:
            try:
                self._env.post_cleanup()
            except Exception:
                logger.exception("strategy search post-cleanup failed")
            try:
                # Если цель открывалась без обхода, «работают» все стратегии —
                # такой подбор ничего не говорит о них и в историю не идёт.
                if not self._target.forced:
                    self._env.record_history(key, confirmed=confirmed_ids, failed=failed_ids)
            except Exception:
                logger.exception("strategy search history save failed")

    def _finish(self, report, working, failed, started, *, cancelled: bool) -> StrategyScanReport:
        report.working_strategies = list(working)
        report.failed_strategies = list(failed)
        report.total_tested = len(working) + len(failed)
        report.cancelled = bool(cancelled)
        report.elapsed_seconds = self._env.monotonic() - started
        if report.fatal_error:
            self._events.phase("Остановлено")
        elif cancelled:
            self._events.phase("Отменено")
        else:
            self._events.phase("Завершено")
            self._events.log(
                f"Готово: проверено {report.total_tested}, надёжно работают {len(working)}, "
                f"{report.elapsed_seconds:.0f} с"
            )
        return report

    # --- Выбор стратегий --------------------------------------------------------

    def _history_key(self) -> str:
        from blockcheck.strategy_search.history import history_key

        return history_key(self._protocol, self._request.target, self._request.udp_games_scope)

    def _select_batch(self, key: str) -> tuple[list[Candidate], int]:
        candidates = self._env.load_candidates(self._protocol)
        history = self._env.load_history(key)
        ordered = order_candidates(
            candidates,
            confirmed_ids=getattr(history, "confirmed", ()) or (),
            failed_at=getattr(history, "failed", {}) or {},
            now=self._env.wall_time(),
        )
        if not ordered:
            raise ScanFatal("Каталог стратегий пуст: переустановите программу.")
        return batch_for_mode(ordered, self._request.mode), len(ordered)

    # --- Проверка «до подбора» ----------------------------------------------------

    def _baseline(self, report: StrategyScanReport) -> bool:
        if self._protocol == PROTOCOL_TCP_HTTPS:
            return self._baseline_https(report)
        return self._baseline_udp(report)

    def _probe_host(self) -> str:
        return (self._request.probe_host or self._request.target).strip().lower()

    def _baseline_https(self, report: StrategyScanReport) -> bool:
        from blockcheck.strategy_search.probes import stub_addresses

        host = self._probe_host()
        addresses, error = self._env.resolve(host, 443, udp=False)
        if error:
            raise ScanFatal(f"Не удалось узнать адрес {host}: {error}")
        stubs = stub_addresses(addresses)
        if stubs and len(stubs) == len(addresses):
            raise ScanFatal(
                f"Провайдер подменяет адрес {host}: DNS отдаёт заглушку {stubs[0]}. "
                "Стратегии обхода тут не помогут — сначала настройте DNS на странице «Настройка DNS»."
            )
        addresses = [address for address in addresses if address not in stubs][:BASELINE_MAX_ADDRESSES]
        outcomes = self._env.probe_https(host, addresses)
        for address, outcome in zip(addresses, outcomes):
            self._events.log(f"  без обхода {address}: {self._describe(outcome)}")
        decision = rules.decide_baseline(list(zip(addresses, outcomes)))

        if decision.state == rules.BASELINE_NOT_DPI:
            raise ScanFatal(self._not_dpi_reason(host, addresses, decision.reason))
        if decision.state == rules.BASELINE_OPEN:
            report.baseline_accessible = True
            if not self._ask_continue(
                f"{host} открывается и без обхода — подбирать нечего: любая стратегия покажется рабочей."
            ):
                return False
            self._target = _Target(host=host, addresses=decision.probe_addresses, forced=True)
            return True
        self._events.log(f"  Без обхода закрыт ({decision.reason}). Проверяем на {', '.join(decision.probe_addresses)}")
        self._target = _Target(host=host, addresses=decision.probe_addresses)
        return True

    def _not_dpi_reason(self, host: str, addresses: Sequence[str], reason: str) -> str:
        """Объяснение, почему стратегии не помогут, с проверкой порта 80."""
        address = addresses[0] if addresses else ""
        if address and self._env.tcp_port_open(address, 80):
            return (
                f"Сервер {host} ({address}) не принимает подключение к порту 443, а порт 80 открыт. "
                "Провайдер блокирует адрес целиком, ещё до шифрованного приветствия, где zapret "
                "прячет имя сайта. Стратегии обхода DPI такую блокировку не снимают: нужен "
                "прокси, VPN или другой адрес сервера."
            )
        return (
            f"Сервер {host} не отвечает ({reason}) ни на порт 443, ни на порт 80. "
            "Адрес недоступен целиком — стратегии обхода DPI тут не помогут."
        )

    def _udp_pool(self) -> list[UdpProbeSpec]:
        from blockcheck.strategy_scan_targeting import stun_target_parts

        host, port = stun_target_parts(self._request.target)
        specs = [UdpProbeSpec("Цель", "stun", host, int(port), primary=True)]
        if self._protocol == PROTOCOL_UDP_GAMES:
            specs.extend(UdpProbeSpec(name, kind, h, p) for name, kind, h, p in UDP_GAMES_CANARIES)
        resolved: list[UdpProbeSpec] = []
        for spec in specs:
            addresses, _error = self._env.resolve(spec.host, spec.port, udp=True)
            v4 = [address for address in addresses if ":" not in address]
            address = (v4 or addresses or [""])[0]
            resolved.append(UdpProbeSpec(spec.name, spec.kind, spec.host, spec.port, address, spec.primary))
        return resolved

    def _baseline_udp(self, report: StrategyScanReport) -> bool:
        specs = self._udp_pool()
        results = self._env.probe_udp(specs)
        for spec, outcome in results:
            where = spec.address or spec.host
            self._events.log(f"  без обхода {spec.name} {where}:{spec.port}: {self._describe(outcome)}")
        state, chosen, reason = rules.decide_udp_baseline(results)
        games_addresses = tuple(spec.address for spec in specs if spec.address)
        games_ports = tuple(spec.port for spec in specs)
        if state == rules.BASELINE_NOT_DPI:
            raise ScanFatal(f"Не удалось проверить {self._request.target}: {reason}")
        if state == rules.BASELINE_OPEN:
            report.baseline_accessible = True
            if not self._ask_continue(
                f"{self._request.target} отвечает и без обхода — подбирать нечего: "
                "любая стратегия покажется рабочей."
            ):
                return False
            primary = tuple(spec for spec in chosen if spec.primary) or chosen
            self._target = _Target(
                udp_specs=primary, games_addresses=games_addresses, games_ports=games_ports, forced=True
            )
            return True
        self._events.log(f"  Без обхода молчат: {', '.join(spec.name for spec in chosen)}")
        self._target = _Target(udp_specs=chosen, games_addresses=games_addresses, games_ports=games_ports)
        return True

    def _ask_continue(self, reason: str) -> bool:
        self._events.log(reason)
        if self.cancelled:
            return False
        answer = bool(self._events.ask_continue(reason))
        if answer and not self.cancelled:
            self._events.log("Пользователь решил проверять всё равно: результаты — только для сведения")
            return True
        return False

    def _pass_control(self, report: StrategyScanReport) -> bool:
        """Запуск winws2 без приёмов обхода: цель должна остаться закрытой."""
        self._events.phase("Контрольный запуск winws2")
        run = self._test_strategy(Candidate("pass", "pass", PASS_STRATEGY_ARGS), confirm=False)
        if run.verdict == rules.VERDICT_CANCELLED:
            return False
        if run.verdict == rules.VERDICT_CRASH:
            raise ScanFatal(f"winws2 не запускается даже с пустой стратегией: {run.reason}")
        if run.attempts and run.attempts[0].passed:
            report.baseline_accessible = True
            if not self._ask_continue(
                "Цель открывается от одного запуска winws2, даже без приёмов обхода. "
                "Так любая стратегия покажется рабочей."
            ):
                return False
            self._target.forced = True
            return True
        self._events.log("  Контроль пройден: без приёмов обхода цель закрыта")
        return True

    # --- Одна стратегия -------------------------------------------------------------

    def _profile(self, strategy_args: str) -> ProbeProfile:
        games_paths: list[str] = []
        if self._protocol == PROTOCOL_UDP_GAMES:
            games_paths = self._env.games_ipset_paths(self._request.udp_games_scope)
        return build_probe_profile(
            self._protocol,
            strategy_args=strategy_args,
            match_domain=self._request.target,
            games_ipset_paths=games_paths,
            games_addresses=self._target.games_addresses,
            games_ports=self._target.games_ports,
        )

    def _probe_once(self) -> _Attempt:
        if self._protocol == PROTOCOL_TCP_HTTPS:
            outcomes = self._env.probe_https(self._target.host, self._target.addresses)
            return _Attempt(
                rules.attempt_passed(outcomes),
                rules.attempt_reason(outcomes),
                rules.attempt_time_ms(outcomes),
                rules.attempt_needs_network_check(outcomes),
            )
        results = self._env.probe_udp(self._target.udp_specs)
        outcomes = [outcome for _spec, outcome in results]
        passed = rules.udp_attempt_passed(results)
        ok_times = [outcome.time_ms for outcome in outcomes if outcome.ok]
        return _Attempt(
            passed,
            "" if passed else rules.attempt_reason(outcomes),
            min(ok_times) if ok_times else rules.attempt_time_ms(outcomes),
            # UDP-молчание не отличить от пропавшей сети: провал всегда
            # сверяется с обычными сайтами.
            not passed,
        )

    def _test_strategy(self, candidate: Candidate, *, confirm: bool = True) -> _StrategyRun:
        profile = self._profile(candidate.args)
        config_text = build_probe_config_text(profile, self._env.blob_lines(candidate.args))
        crash_reason = ""
        for crash_try in range(1 + CRASH_RETRIES):
            if self.cancelled:
                return _StrategyRun(rules.VERDICT_CANCELLED)
            session = self._env.start_session(config_text)
            start = session.start()
            if not getattr(start, "ok", False):
                crash_reason = self._crash_text(start)
                self._events.log(f"  winws2 не запустился: {crash_reason}")
                session.stop()
                self._env.recover_after_crash()
                continue
            attempts: list[_Attempt] = []
            crashed = False
            try:
                total = rules.CONFIRM_ATTEMPTS if confirm else 1
                for _number in range(total):
                    if self.cancelled:
                        break
                    attempt = self._probe_once()
                    if not session.alive():
                        crashed = True
                        crash_reason = session.output_tail(3) or "процесс завершился во время проверки"
                        break
                    attempts.append(attempt)
                    if not attempt.passed:
                        break
            finally:
                session.stop()
            if self.cancelled:
                return _StrategyRun(rules.VERDICT_CANCELLED, attempts)
            if crashed:
                self._events.log(f"  winws2 упал во время проверки, перезапуск ({crash_try + 1})")
                self._env.recover_after_crash()
                continue
            verdict = rules.final_verdict([attempt.passed for attempt in attempts])
            if confirm and verdict == rules.VERDICT_WORKING and not self._target.forced:
                recheck = self._probe_once()
                if recheck.passed:
                    return _StrategyRun(
                        rules.VERDICT_NOT_COUNTED,
                        attempts,
                        "без обхода цель тоже открылась — сеть нестабильна, успех не засчитан",
                    )
            reason = next((attempt.reason for attempt in attempts if not attempt.passed), "")
            return _StrategyRun(verdict, attempts, reason)
        return _StrategyRun(rules.VERDICT_CRASH, [], crash_reason or "winws2 падает при запуске")

    @staticmethod
    def _crash_text(start) -> str:
        tail = str(getattr(start, "output_tail", "") or "").strip()
        code = getattr(start, "exit_code", None)
        if tail:
            return tail.splitlines()[-1]
        return f"код выхода {code}" if code is not None else "не удалось запустить"

    def _wait_for_network(self) -> bool:
        deadline = self._env.monotonic() + NETWORK_WAIT_SECONDS
        while self._env.monotonic() < deadline and not self.cancelled:
            self._env.sleep(NETWORK_POLL_SECONDS)
            if self._env.control_alive():
                self._events.log("  Интернет вернулся")
                return True
        return False

    # --- Результат ----------------------------------------------------------------------

    def _make_result(self, candidate: Candidate, run: _StrategyRun) -> StrategyProbeResult:
        passed = [attempt for attempt in run.attempts if attempt.passed]
        success = run.verdict == rules.VERDICT_WORKING
        return StrategyProbeResult(
            strategy_name=candidate.name,
            strategy_id=candidate.strategy_id,
            strategy_args=candidate.args,
            target=self._request.target,
            success=success,
            time_ms=passed[0].time_ms if passed else 0.0,
            error="" if success else (run.reason or self._verdict_text(run.verdict)),
            scan_protocol=self._protocol,
            verdict=run.verdict,
            attempts_ok=len(passed),
            attempts_total=rules.CONFIRM_ATTEMPTS if run.attempts else 0,
            apply_lines=tuple(self._profile(candidate.args).apply_lines()) if success else (),
            raw_data={"forced": self._target.forced},
        )

    def _log_result(self, index: int, total: int, candidate: Candidate, run: _StrategyRun) -> None:
        passed = sum(1 for attempt in run.attempts if attempt.passed)
        head = f"[{index + 1}/{total}] {candidate.name}: {self._verdict_text(run.verdict)}"
        if run.verdict in (rules.VERDICT_WORKING, rules.VERDICT_UNSTABLE, rules.VERDICT_NOT_COUNTED):
            head += f" {passed}/{rules.CONFIRM_ATTEMPTS}"
        if run.reason:
            head += f" — {run.reason}"
        self._events.log(head)

    @staticmethod
    def _verdict_text(verdict: str) -> str:
        return {
            rules.VERDICT_WORKING: "работает",
            rules.VERDICT_UNSTABLE: "нестабильно",
            rules.VERDICT_FAILED: "не работает",
            rules.VERDICT_CRASH: "сбой winws2",
            rules.VERDICT_NOT_COUNTED: "не засчитано",
            rules.VERDICT_CANCELLED: "отменено",
        }.get(verdict, verdict)

    @staticmethod
    def _describe(outcome: ProbeOutcome) -> str:
        if outcome.ok:
            return f"открыт ({outcome.reason}, {outcome.time_ms:.0f} мс)"
        return outcome.reason or outcome.state

    def _protocol_label(self) -> str:
        return {
            PROTOCOL_TCP_HTTPS: "сайты (TCP/HTTPS)",
            PROTOCOL_STUN_VOICE: "голосовые звонки (STUN)",
            PROTOCOL_UDP_GAMES: "игры (UDP)",
        }.get(self._protocol, self._protocol)


def run_strategy_search(
    request: SearchRequest,
    *,
    env: SearchEnvironment,
    events: SearchEvents,
    on_created: Callable[[StrategySearch], None] | None = None,
) -> StrategyScanReport:
    search = StrategySearch(request, env=env, events=events)
    if on_created is not None:
        on_created(search)
    return search.run()
