"""Порядок работы подбора стратегии.

Цепочка одного подбора:

1. Подготовка: стратегии в «умном» порядке (``ordering``), остановка Zapret
   и чистка WinDivert, чтобы ничего постороннего не мешало замерам.
2. Интернет жив? Если не открывается ни один обычный сайт, подбор бессмыслен.
3. Проверка «до подбора» (как в blockcheck2): цель проверяется без обхода.
   - закрыта так, как закрывает DPI, — подбираем на этих же адресах
     (два IPv4 из разных подсетей и один IPv6);
   - ответила, но коротко — набираем объём по одному соединению: замерла
     в окне обрыва (``diagnostics.limits.FREEZE_*_BYTES``) — это обрыв по
     объёму, и каждую стратегию судим объёмом;
   - открыта — проверяем ещё раз и спрашиваем пользователя, есть ли смысл;
   - закрыта раньше шифрования (сервер не принимает подключение) —
     стратегии обхода DPI это не чинят, останавливаемся с объяснением.
3а. Какие имена пропускает фильтр (``sni_discovery``). Не проходит ни одно, а с
   окном приёма в 1 байт ответ сервера приходит — TLS с адреса режут по ответу
   сервера. Тогда (и для таких же адресов остальных имён сервиса) каждая
   стратегия проверяется с профилем малого окна по этим адресам перед основным
   (``probe_profile.WINDOW_STRATEGY_LINE``), первой — малое окно без приёмов.
4. Контроль: запуск winws2 с пустой стратегией ``pass`` (две пробы). Если
   цель открывается уже от него — любая стратегия «сработает», замер ненадёжен.
5. Каждая стратегия: запуск winws2 → одна проверка (отбор). Короткий ответ
   добирается объёмом. Прошла — подтверждение на свежих соединениях до трёх
   успехов, одна тишина прощается (``verdict.attempts_done``). Затем цель
   дважды проверяется без обхода: открылась и так — успех не засчитывается.
6. Провал похож на пропавшую сеть (таймаут) — проверяем обычные сайты. Сеть
   пропала — ждём и перепроверяем стратегию, а не пишем её в провалы.
7. Конец: чистка WinDivert, история подбора в настройки.

Сеть, процессы и настройки приходят через ``SearchEnvironment``, поэтому
весь порядок работы проверяется тестами с подставными проверками.
"""

from __future__ import annotations

import logging
import statistics
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from blockcheck.scan_models import StrategyProbeResult, StrategyScanReport
from blockcheck.strategy_search import verdict as rules
from blockcheck.strategy_search.ordering import (
    Candidate,
    batch_for_mode,
    continuation,
    family_siblings,
    habit_note,
    order_candidates,
    stop_after_for_mode,
)
from blockcheck.strategy_search.probe_profile import (
    WINDOW_STRATEGY_LINE,
    PROTOCOL_HTTP80,
    PROTOCOL_QUIC,
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
# Малое окно без других приёмов: первая проверка, когда TLS с адреса режут по ответу сервера.
WINDOW_ONLY_ID = "wssize_window_only"
WINDOW_ONLY_NAME = "Только малое окно (wssize 1:1) по адресам"
# Сколько раз перезапускать стратегию, если winws2 упал (гонка драйвера).
# Падение с тем же выводом второй раз подряд — не гонка, а сама стратегия.
CRASH_RETRIES = 2
# Сколько соседей открывшей цель стратегии проверить сразу следом.
SIBLINGS_AFTER_SUCCESS = 3
# История пишется по ходу, каждые столько итогов: упадёт программа посреди
# долгого подбора — проверенное не пропадёт.
HISTORY_FLUSH_EVERY = 10
# Сколько ждать возвращения интернета, прежде чем остановить подбор.
NETWORK_WAIT_SECONDS = 30.0
NETWORK_POLL_SECONDS = 3.0
# Сколько адресов цели проверять «до подбора».
BASELINE_MAX_ADDRESSES = 4
# Сколько раз пробовать цель под пустым winws2 и после успеха без обхода.
# Одной пробы мало: фильтр, который пропускает «через раз», выдал бы любую
# стратегию за рабочую.
CONTROL_PROBES = 2

# Вспомогательные игровые серверы: их ответ тоже показывает, что UDP игр
# проходит. Для голоса проверяется только цель пользователя.
UDP_GAMES_CANARIES: tuple[tuple[str, str, str, int], ...] = (
    ("Rust A2S", "source_a2s", "205.178.168.170", 28015),
    ("CS A2S", "source_a2s", "46.174.55.234", 27015),
    ("Bedrock CubeCraft", "bedrock_ping", "play.cubecraft.net", 19132),
)


# Почему подбор остановился (для панели итога: у каждой причины свой вид).
STOP_NO_INTERNET = "no_internet"
STOP_ADDRESS_BLOCK = "address_block"
STOP_DNS_STUB = "dns_stub"
STOP_UNRESOLVED = "unresolved"
STOP_WINWS = "winws"
STOP_NETWORK_LOST = "network_lost"
STOP_OTHER = "other"

# Шаги для панели хода подбора.
STEP_NETWORK = "network"
STEP_BASELINE = "baseline"
STEP_NAMES = "names"
STEP_CONTROL = "control"
STEP_STRATEGIES = "strategies"


def _remembered_variants(candidates: list[Candidate], confirmed: Sequence[str]) -> list[Candidate]:
    """Подтверждённые варианты «стратегия · имя» из истории: в каталоге их нет, без этого их забыл бы порядок."""
    from blockcheck.strategy_search.sni_discovery import base_of, with_allowed_name

    by_id = {candidate.strategy_id: candidate for candidate in candidates}
    result: list[Candidate] = []
    for strategy_id in confirmed:
        base, name = base_of(strategy_id)
        if name and base in by_id:
            variant = with_allowed_name(by_id[base], name)
            if variant is not None and variant.strategy_id == strategy_id:
                result.append(variant)
    return result


class ScanFatal(Exception):
    """Подбор дальше не имеет смысла; текст — объяснение для пользователя."""

    def __init__(self, message: str, kind: str = STOP_OTHER) -> None:
        super().__init__(message)
        self.kind = kind


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
    # Остальные имена сервиса (YouTube: видеосервер, превью): у каждого — (имя для профиля, хост для пробы).
    # Стратегия считается рабочей, только когда открываются и они, а профиль пишется на все имена сразу.
    companions: tuple[tuple[str, str], ...] = ()
    # Начать заново: не отодвигать в конец стратегии, которые недавно не
    # сработали, а проверять в том же порядке, что и в первый раз. История
    # при этом не стирается — новые итоги допишутся в неё как обычно.
    from_start: bool = False


class SearchEvents(Protocol):
    def log(self, message: str) -> None: ...
    def phase(self, text: str) -> None: ...
    def strategy_started(self, name: str, index: int, total: int, args: str = "") -> None: ...
    def stage(self, step: str, status: str, text: str = "") -> None: ...
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
    def probe_volume(self, host: str, address: str, answered: ProbeOutcome) -> ProbeOutcome: ...
    def probe_http(self, host: str, addresses: Sequence[str]) -> list[ProbeOutcome]: ...
    def probe_quic(self, host: str, addresses: Sequence[str]) -> list[ProbeOutcome]: ...
    def quic_blocked_by_name(self, host: str, address: str) -> bool: ...
    def probe_hello(self, address: str, name: str | None) -> bool | None: ...
    def probe_window(self, address: str) -> bool | None: ...
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
    # Остальные имена сервиса с их адресами: хост и адреса, которые проверяются вместе с целью.
    companions: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # Без обхода цель отвечает, но соединение замирает на объёме: каждая
    # проверка обязана набрать объём, иначе «откроется» любая стратегия.
    volume_block: bool = False
    # Адреса цели и остальных имён, где TLS режут по ответу сервера: им нужен профиль малого окна.
    window_addresses: tuple[str, ...] = ()


@dataclass
class _Attempt:
    passed: bool
    reason: str = ""
    time_ms: float = 0.0
    network_suspect: bool = False
    # Провал без явного следа DPI (``verdict.attempt_silent``).
    silent: bool = False
    # Итог проверки объёма в этой попытке (``verdict.VOLUME_*``) или "".
    volume: str = ""
    # Пауза или медленная передача, замеченные в этой попытке (``verdict.SLOW_TRANSFER_CODES``) или "".
    transfer: str = ""

    @property
    def mark(self) -> rules.AttemptMark:
        return rules.AttemptMark(self.passed, self.silent)


@dataclass
class _StrategyRun:
    verdict: str
    attempts: list[_Attempt] = field(default_factory=list)
    reason: str = ""
    # Во время проверки пропал интернет: стратегию надо перепроверить.
    network_lost: bool = False
    # winws2 дважды подряд упал одинаково: дело в стратегии, а не в драйвере.
    repeatable_crash: bool = False

    @property
    def transfer(self) -> str:
        """Пауза или медленная передача у открывших цель попыток: самый тяжёлый вывод или ""."""
        codes = {attempt.transfer for attempt in self.attempts if attempt.passed and attempt.transfer}
        return next((code for code in rules.SLOW_TRANSFER_CODES if code in codes), "")

    @property
    def volume(self) -> str:
        """Как стратегию проверили на объём: ok, unknown или "" (не проверяли)."""
        codes = {attempt.volume for attempt in self.attempts if attempt.volume}
        if rules.VOLUME_OK in codes:
            return rules.VOLUME_OK
        return rules.VOLUME_UNKNOWN if codes else ""


class StrategySearch:
    def __init__(self, request: SearchRequest, *, env: SearchEnvironment, events: SearchEvents) -> None:
        self._request = request
        self._env = env
        self._events = events
        self._cancelled = False
        self._protocol = request.scan_protocol
        self._target = _Target()
        self._running_step = ""
        self._strategies_started = 0.0
        # Что сказала проба объёма о скорости ответов цели, открытой без обхода ("" — жалоб нет).
        self._baseline_slow = ""

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

    def _stage(self, step: str, status: str, text: str = "") -> None:
        self._running_step = step if status == "running" else ""
        handler = getattr(self._events, "stage", None)
        if handler is not None:
            handler(step, status, text)

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
            batch, total, ordered = self._select_batch(key)
            from_catalog = 0
            report.total_available = total
            stop_after = stop_after_for_mode(self._request.mode)
            self._events.log(
                f"Подбор: {self._protocol_label()}, цель {self._request.target}, "
                f"проверим {len(batch)} из {total} стратегий"
                + (" (заново, без учёта прошлых проверок)" if self._request.from_start else "")
            )
            self._events.phase("Подготовка")
            self._env.pre_cleanup()
            if self.cancelled:
                return self._finish(report, working, failed, started, cancelled=True)

            self._events.phase("Проверка сети")
            self._stage(STEP_NETWORK, "running", "Проверяем, есть ли интернет")
            if not self._env.control_alive():
                raise ScanFatal(
                    "Нет интернета: не открывается ни один обычный сайт. "
                    "Проверьте подключение и запустите подбор снова.",
                    STOP_NO_INTERNET,
                )
            self._stage(STEP_NETWORK, "done", "Интернет есть")

            self._events.phase("Проверка без обхода")
            self._stage(STEP_BASELINE, "running", "Пробуем открыть без обхода")
            protocol_before = self._protocol
            if not self._baseline(report):
                return self._finish(report, working, failed, started, cancelled=self.cancelled)
            if self._protocol != protocol_before:
                # Сайт без HTTPS: стратегии и история — для обычного HTTP.
                key = self._history_key()
                batch, total, ordered = self._select_batch(key)
                report.total_available = total
                report.scan_protocol = self._protocol
                self._events.log(f"Подбор переходит на {self._protocol_label()}: проверим {len(batch)} из {total} стратегий")
            if self._target.forced:
                self._stage(STEP_BASELINE, "skipped", "Открывается и без обхода — проверяем для сведения")
                self._stage(STEP_NAMES, "skipped", "Не нужно")
                self._stage(STEP_CONTROL, "skipped", "Контрольный запуск не нужен")
            else:
                self._stage(STEP_BASELINE, "done", "Без обхода закрыто — есть что подбирать")
                extra = self._find_allowed_names(ordered, batch)
                if extra:
                    batch = [*extra, *batch]
                    from_catalog -= len(extra)
                if not self._pass_control(report):
                    return self._finish(report, working, failed, started, cancelled=self.cancelled)

            queue: deque[tuple[Candidate, bool]] = deque((candidate, False) for candidate in batch)
            # Сколько стратегий из общего списка уже в пачке: варианты с именами не в счёт.
            from_catalog += len(batch)
            seen = {candidate.strategy_id for candidate in batch}
            index = 0
            self._strategies_started = self._env.monotonic()
            while not self.cancelled:
                if not queue:
                    # Пачка кончилась, а надёжной нет — берём следующую, пока есть куда.
                    raw = (
                        []
                        if working or self._target.forced
                        else continuation(ordered, self._request.mode, from_catalog)
                    )
                    from_catalog += len(raw)
                    # Соседи удачных стратегий могли уже прийти из следующей пачки.
                    more = [candidate for candidate in raw if candidate.strategy_id not in seen]
                    seen.update(candidate.strategy_id for candidate in more)
                    if not more:
                        if raw:
                            continue  # вся пачка уже проверена как соседи — следующая
                        break
                    self._events.log(
                        f"Из {len(batch)} проверенных ни одна не подошла — проверяем следующие {len(more)}"
                    )
                    batch = [*batch, *more]
                    queue.extend((candidate, False) for candidate in more)
                candidate, rechecked = queue.popleft()
                self._events.strategy_started(candidate.name, index, len(batch), candidate.args)
                self._events.phase(f"[{index + 1}/{len(batch)}] {candidate.name}")
                self._stage(
                    STEP_STRATEGIES,
                    "running",
                    f"Проверяем {index + 1} из {len(batch)}: {candidate.name}{self._time_left(index, len(batch))}",
                )
                run = self._test_strategy(candidate)
                if self.cancelled and run.verdict == rules.VERDICT_CANCELLED:
                    break
                if run.network_lost:
                    self._events.log("  Похоже, пропал интернет — жду и перепроверю эту стратегию")
                    if not self._wait_for_network():
                        raise ScanFatal("Во время подбора пропал интернет. Подбор остановлен.", STOP_NETWORK_LOST)
                    if not rechecked:
                        queue.appendleft((candidate, True))
                        continue
                result = self._make_result(candidate, run)
                self._events.strategy_result(result)
                self._log_result(index, len(batch), candidate, run)
                if result.success:
                    working.append(result)
                    confirmed_ids.append(candidate.strategy_id)
                    if stop_after and len(working) >= stop_after and not self._target.forced:
                        # Нашлась надёжная — человеку нужна рабочая стратегия, а не все рабочие.
                        index += 1
                        self._events.log(f"Найдено надёжных: {len(working)} — подбор закончен, остальные не нужны")
                        break
                else:
                    failed.append(result)
                    # Провал при пропавшей сети ничего не говорит о стратегии:
                    # в историю на 14 дней он не идёт.
                    if (run.verdict == rules.VERDICT_FAILED and not run.network_lost) or run.repeatable_crash:
                        failed_ids.append(candidate.strategy_id)
                if run.verdict in (rules.VERDICT_WORKING, rules.VERDICT_UNSTABLE) and not self._target.forced:
                    batch = self._bring_siblings(candidate, ordered, batch, queue, seen)
                if len(confirmed_ids) + len(failed_ids) >= HISTORY_FLUSH_EVERY:
                    self._save_history(key, confirmed_ids, failed_ids)
                index += 1
                pause = self._env.strategy_pause_seconds()
                if pause > 0 and queue:
                    self._env.sleep(pause)
            if not self.cancelled:
                self._stage(STEP_STRATEGIES, "done", f"Проверено стратегий: {index}")
            return self._finish(report, working, failed, started, cancelled=self.cancelled)
        except ScanFatal as fatal:
            if self.cancelled:
                # «Стоп» оборвал пробу на полпути: её «не отвечает» — это отмена,
                # а не «нет интернета» или «адрес закрыт целиком».
                return self._finish(report, working, failed, started, cancelled=True)
            report.fatal_error = str(fatal)
            report.stop_kind = fatal.kind
            if self._running_step:
                self._stage(self._running_step, "failed", "")
            self._events.log(f"СТОП: {fatal}")
            return self._finish(report, working, failed, started, cancelled=True)
        finally:
            try:
                self._env.post_cleanup()
            except Exception:
                logger.exception("strategy search post-cleanup failed")
            self._save_history(key, confirmed_ids, failed_ids)

    def _time_left(self, index: int, total: int) -> str:
        """« · осталось ≈ N мин» по среднему времени уже проверенных стратегий."""
        if index < 3 or total <= index:
            return ""
        spent = self._env.monotonic() - self._strategies_started
        left = spent / index * (total - index)
        if left < 60:
            return " · осталось меньше минуты"
        return f" · осталось ≈ {round(left / 60)} мин"

    def _save_history(self, key: str, confirmed_ids: list[str], failed_ids: list[str]) -> None:
        """Дописать накопленное в историю и начать копить заново."""
        # Если цель открывалась без обхода, «работают» все стратегии —
        # такой подбор ничего не говорит о них и в историю не идёт.
        if self._target.forced or not (confirmed_ids or failed_ids):
            return
        try:
            self._env.record_history(key, confirmed=list(confirmed_ids), failed=list(failed_ids))
        except Exception:
            logger.exception("strategy search history save failed")
        confirmed_ids.clear()
        failed_ids.clear()

    def _bring_siblings(self, winner, ordered, batch, queue, seen) -> list[Candidate]:
        """Соседей открывшей цель стратегии — в начало очереди."""
        waiting = {item.strategy_id for item, _rechecked in queue}
        tested = seen - waiting
        siblings = [
            item
            for item in family_siblings(ordered, winner, limit=len(ordered))
            if item.strategy_id not in tested
        ][:SIBLINGS_AFTER_SUCCESS]
        if not siblings:
            return batch
        ids = {item.strategy_id for item in siblings}
        rest = [entry for entry in queue if entry[0].strategy_id not in ids]
        queue.clear()
        queue.extend([*((item, False) for item in siblings), *rest])
        new = [item for item in siblings if item.strategy_id not in seen]
        seen.update(ids)
        self._events.log(f"  Дальше — соседи этой стратегии ({len(siblings)}): тот же приём с другими параметрами")
        return [*batch, *new]

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

    def _select_batch(self, key: str) -> tuple[list[Candidate], int, list[Candidate]]:
        candidates = self._env.load_candidates(self._protocol)
        history = self._env.load_history(key)
        candidates = [*candidates, *_remembered_variants(candidates, getattr(history, "confirmed", ()) or ())]
        # Привычки фильтра BlockCheck выясняет на TLS поверх TCP — к звонкам и играм они не относятся.
        habit = str(getattr(history, "habit", "") or "") if self._protocol == PROTOCOL_TCP_HTTPS else ""
        ordered = order_candidates(
            candidates,
            confirmed_ids=getattr(history, "confirmed", ()) or (),
            failed_at={} if self._request.from_start else (getattr(history, "failed", {}) or {}),
            now=self._env.wall_time(),
            habit=habit,
        )
        if habit_note(habit):
            self._events.log(f"По последней проверке BlockCheck: {habit_note(habit)}.")
        if not ordered:
            raise ScanFatal("Каталог стратегий пуст: переустановите программу.", STOP_OTHER)
        return batch_for_mode(ordered, self._request.mode), len(ordered), ordered

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
            raise ScanFatal(f"Не удалось узнать адрес {host}: {error}", STOP_UNRESOLVED)
        stubs = stub_addresses(addresses)
        if stubs and len(stubs) == len(addresses):
            raise ScanFatal(
                f"Провайдер подменяет адрес {host}: DNS отдаёт заглушку {stubs[0]}. "
                "Стратегии обхода тут не помогут — сначала настройте DNS на странице «Настройка DNS».",
                STOP_DNS_STUB,
            )
        addresses = [address for address in addresses if address not in stubs][:BASELINE_MAX_ADDRESSES]
        outcomes = self._env.probe_https(host, addresses)
        for address, outcome in zip(addresses, outcomes):
            self._events.log(f"  без обхода {address}: {self._describe(outcome)}")
        decision = rules.decide_baseline(list(zip(addresses, outcomes)))
        companions = self._resolve_companions()

        if decision.state == rules.BASELINE_NOT_DPI:
            http = self._baseline_http(report, host, addresses)
            if http is not None:
                return http
            raise ScanFatal(self._not_dpi_reason(host, addresses, decision.reason), STOP_ADDRESS_BLOCK)
        if decision.state == rules.BASELINE_OPEN:
            decision = self._recheck_open_baseline(host, addresses, outcomes, decision)
        if decision.state == rules.BASELINE_OPEN and self._baseline_quic(host, decision.probe_addresses):
            return True
        if decision.state == rules.BASELINE_OPEN and self._companion_closed(companions):
            # Страница открывается, а другое имя сервиса (видеосервер, превью) нет: подбирать есть что.
            self._target = _Target(host=host, addresses=decision.probe_addresses, companions=companions)
            return True
        if decision.state == rules.BASELINE_OPEN:
            report.baseline_accessible = True
            self._stage(STEP_BASELINE, "skipped", "Открывается и без обхода")
            question = f"{host} открывается и без обхода — подбирать нечего: любая стратегия покажется рабочей."
            if self._baseline_slow:
                self._events.log(f"  без обхода открывается, но {self._baseline_slow}")
                question += (
                    f" Но он открывается медленно: {self._baseline_slow}. Это свойство сервера или канала, "
                    "стратегия обхода тут не поможет."
                )
            if not self._ask_continue(question):
                return False
            self._target = _Target(host=host, addresses=decision.probe_addresses, forced=True, companions=companions)
            return True
        self._events.log(f"  Без обхода закрыт ({decision.reason}). Проверяем на {', '.join(decision.probe_addresses)}")
        self._target = _Target(
            host=host,
            addresses=decision.probe_addresses,
            volume_block=decision.volume_block,
            companions=companions,
        )
        return True

    def _resolve_companions(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Остальные имена сервиса с адресами. Имя, которое не нашлось, пропускается: цель это не отменяет."""
        from blockcheck.strategy_search.probes import stub_addresses

        found: list[tuple[str, tuple[str, ...]]] = []
        for _domain, host in self._request.companions:
            addresses, error = self._env.resolve(host, 443, udp=False)
            addresses = [address for address in addresses if address not in stub_addresses(addresses)][:2]
            if error or not addresses:
                self._events.log(f"  {host}: адрес не найден, это имя сервиса не проверяется")
                continue
            found.append((host, tuple(addresses)))
        return tuple(found)

    def _companion_closed(self, companions: Sequence[tuple[str, Sequence[str]]]) -> bool:
        """Не открывается ли без обхода другое имя сервиса. True — хотя бы одно закрыто."""
        for host, addresses in companions:
            outcomes = self._env.probe_https(host, addresses)
            if not rules.attempt_passed(outcomes):
                self._events.log(f"  без обхода {host}: {rules.attempt_reason(outcomes)}")
                return True
        return False

    def _recheck_open_baseline(
        self,
        host: str,
        addresses: Sequence[str],
        outcomes: Sequence[ProbeOutcome],
        decision: rules.BaselineDecision,
    ) -> rules.BaselineDecision:
        """«Открыто без обхода» проверяется объёмом и второй пробой.

        Короткая главная страница проходит и там, где фильтр режет соединение
        в окне ``FREEZE_*_BYTES``, — видео и файлы при этом не грузятся. А фильтр, который
        пропускает через раз, мог случайно пропустить первую пробу.
        """
        checked = [
            (address, self._env.probe_volume(host, address, outcome) if rules.needs_volume(outcome) else outcome)
            for address, outcome in zip(addresses, outcomes)
            if outcome.ok
        ]
        cut = [(address, outcome) for address, outcome in checked if outcome.volume == rules.VOLUME_CUT]
        self._baseline_slow = rules.transfer_problem(outcome for _address, outcome in checked)
        if cut:
            for address, outcome in cut:
                self._events.log(f"  без обхода {address}: {outcome.reason}")
            return rules.BaselineDecision(
                rules.BASELINE_BLOCKED,
                tuple(rules.spread_addresses([address for address, _outcome in cut])),
                cut[0][1].reason,
                volume_block=True,
            )
        again = self._env.probe_https(host, list(decision.probe_addresses))
        flaky = [
            (address, outcome)
            for address, outcome in zip(decision.probe_addresses, again)
            if outcome.state == rules.PROBE_BLOCKED
        ]
        if flaky:
            self._events.log(f"  без обхода {flaky[0][0]} со второго раза: {self._describe(flaky[0][1])} — открывается через раз")
            return rules.BaselineDecision(
                rules.BASELINE_BLOCKED,
                tuple(rules.spread_addresses([address for address, _outcome in flaky])),
                flaky[0][1].reason,
            )
        return decision

    def _baseline_quic(self, host: str, addresses: Sequence[str]) -> bool:
        """Сайт по TCP открыт — а QUIC? True — его режут по имени, подбор идёт для QUIC.

        Браузер открывает YouTube и многие сайты быстрым QUIC (UDP 443) и,
        если он молчит, переходит на обычное соединение — с задержкой при
        открытии и в начале видео. Подбирать для QUIC есть смысл, только если
        фильтр режет именно имя сайта: с посторонним именем тот же сервер
        отвечает. Если сервер QUIC не знает вовсе — подбирать нечего.
        """
        address = next((item for item in addresses if ":" not in item), addresses[0] if addresses else "")
        if not address or self.cancelled:
            return False
        self._events.log(f"  По TCP открыт — проверяем быстрый протокол QUIC (UDP 443) на {address}")
        if not self._env.quic_blocked_by_name(host, address):
            self._events.log("  QUIC не режут по имени — подбирать нечего")
            return False
        self._protocol = PROTOCOL_QUIC
        self._events.log(
            f"  QUIC режут по имени: с именем {host} пакет пропадает, с другим именем сервер отвечает — "
            "подбираем стратегию для QUIC"
        )
        self._target = _Target(host=host, addresses=(address,))
        return True

    def _baseline_http(self, report: StrategyScanReport, host: str, addresses: Sequence[str]) -> bool | None:
        """Порт 443 молчит — может, это сайт без HTTPS.

        None — это не сайт без HTTPS (дальше прежнее объяснение); True — подбор
        идёт по HTTP; False — подбирать нечего, и пользователь отказался.

        Порт 80 открыт и ответ на нём — заглушка или обрыв: фильтр режет обычный
        HTTP, и для него есть свои стратегии. Открыт и отвечает честно — сайт
        работает без шифрования и без обхода.
        """
        if not addresses or not self._env.tcp_port_open(addresses[0], 80):
            return None
        outcomes = self._env.probe_http(host, list(addresses))
        for address, outcome in zip(addresses, outcomes):
            self._events.log(f"  без обхода http://{host} ({address}): {self._describe(outcome)}")
        decision = rules.decide_baseline(list(zip(addresses, outcomes)))
        if decision.state == rules.BASELINE_NOT_DPI:
            return None
        self._protocol = PROTOCOL_HTTP80
        if decision.state == rules.BASELINE_OPEN:
            report.baseline_accessible = True
            self._stage(STEP_BASELINE, "skipped", "Сайт без HTTPS открывается и без обхода")
            if not self._ask_continue(
                f"{host} работает без HTTPS (только порт 80) и открывается без обхода — подбирать нечего."
            ):
                return False
            self._target = _Target(host=host, addresses=decision.probe_addresses, forced=True)
            return True
        self._events.log(
            f"  {host} работает без HTTPS, а обычный HTTP закрыт ({decision.reason}) — подбираем стратегии для HTTP"
        )
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
            raise ScanFatal(f"Не удалось проверить {self._request.target}: {reason}", STOP_UNRESOLVED)
        if state == rules.BASELINE_OPEN:
            report.baseline_accessible = True
            self._stage(STEP_BASELINE, "skipped", "Отвечает и без обхода")
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

    def _find_allowed_names(self, ordered: list[Candidate], batch: list[Candidate]) -> list[Candidate]:
        """Какие имена пропускает фильтр; в белом списке — варианты стратегий с этими именами в начало."""
        from blockcheck.strategy_search import sni_discovery

        if self._protocol != PROTOCOL_TCP_HTTPS or not self._target.addresses:
            self._stage(STEP_NAMES, "skipped", "Только для сайтов")
            return []
        self._events.phase("Какие имена пропускает фильтр")
        self._stage(STEP_NAMES, "running", "Пробуем чужие имена к тому же адресу")
        address = self._target.addresses[0]
        started = self._env.monotonic()
        search = sni_discovery.discover(
            lambda name: self._env.probe_hello(address, name),
            self._target.host,
            sleep=self._env.sleep,
            cancelled=lambda: self.cancelled,
            window=lambda: self._env.probe_window(address),
        )
        seconds = self._env.monotonic() - started
        extra = self._names_extra(ordered, batch, search, address, seconds)
        windows = self._reply_window_addresses(search.kind == sni_discovery.FILTER_REPLY_WINDOW)
        if windows:
            extra = [*self._start_window_mode(windows), *extra]
        return extra

    def _names_extra(self, ordered: list[Candidate], batch: list[Candidate], search, address: str, seconds: float) -> list[Candidate]:
        """Что дал поиск имён: в белом списке — варианты стратегий с найденными именами."""
        from blockcheck.strategy_search import sni_discovery

        if search.kind == sni_discovery.FILTER_REPLY_WINDOW:
            return []
        if search.kind == sni_discovery.FILTER_BLACK:
            self._events.log(
                f"  Имя цели рвётся, постороннее {sni_discovery.NEUTRAL_NAME} проходит — режут только это имя"
            )
            self._stage(STEP_NAMES, "done", "Режут только имя цели — подходят обычные стратегии")
            return []
        if search.kind != sni_discovery.FILTER_WHITE:
            self._stage(STEP_NAMES, "skipped", "Сравнить не с чем")
            return []
        if search.frozen:
            self._events.log("  Адрес перестал отвечать на частые запросы — поиск имён остановлен")
        if search.unstable:
            self._events.log("  Имя цели то рвётся, то проходит — найденные имена не в счёт")
        if not search.names:
            self._events.log(f"  Белый список имён: ни одно из {search.tried} проверенных не прошло ({seconds:.0f} с)")
            self._stage(STEP_NAMES, "done", "Белый список: разрешённых имён не нашли")
            return []
        names = ", ".join(search.names)
        self._events.log(
            f"  Фильтр пропускает к {address} только разрешённые имена: {names} "
            f"(проверено {search.tried}, {seconds:.0f} с)"
        )
        known = {candidate.strategy_id for candidate in batch}
        extra = [item for item in sni_discovery.variants(ordered, search.names) if item.strategy_id not in known]
        self._stage(STEP_NAMES, "done", f"Белый список: проходят {names}")
        if extra:
            self._events.log(f"  Сначала проверим {len(extra)} стратегий с этими именами в подставном пакете")
        return extra

    def _reply_window_addresses(self, target_found: bool) -> tuple[str, ...]:
        """Адреса, где TLS режут по ответу сервера: у цели (первый уже проверен) и у остальных имён сервиса.

        Адрес попадает сюда, только если постороннее имя к нему оборвалось, а малое окно прошло:
        проба малого окна сама повторяет ответ дважды (``environment.probe_window``).
        """
        from blockcheck.strategy_search.sni_discovery import NEUTRAL_NAME

        found: list[str] = []
        first = self._target.addresses[0] if self._target.addresses else ""
        if target_found and first:
            found.append(first)
        others = [(self._target.host, address) for address in self._target.addresses[1:]] if target_found else []
        others += [(host, addresses[0]) for host, addresses in self._target.companions if addresses]
        for host, address in others:
            if self.cancelled:
                break
            if self._env.probe_hello(address, host) is not False or self._env.probe_hello(address, NEUTRAL_NAME) is not False:
                continue
            if self._env.probe_window(address):
                if host != self._target.host:
                    self._events.log(
                        f"  {host} ({address}): шифрование не проходит ни с каким именем, а с окном приёма в 1 байт "
                        "ответ сервера приходит — этот адрес тоже режут по ответу сервера"
                    )
                found.append(address)
        return tuple(dict.fromkeys(found))

    def _start_window_mode(self, addresses: tuple[str, ...]) -> list[Candidate]:
        """Стратегии дальше проверяются с профилем малого окна по ``addresses``; первым — малое окно без приёмов."""
        from blockcheck.strategy_search.probe_profile import window_groups

        self._target.window_addresses = addresses
        groups = ", ".join(window_groups(addresses))
        self._events.log(
            "  Шифрование с адреса режут по ответу сервера: с обычным окном приёма ответа нет ни с каким именем, "
            "а с окном в 1 байт он приходит. Имена тут не помогут — каждую стратегию проверим вместе с малым окном "
            f"(wssize 1:1) в отдельном профиле по адресам {groups}. Скорость через такой профиль ниже в десятки раз."
        )
        self._stage(STEP_NAMES, "done", f"TLS режут по ответу сервера — добавляем малое окно для {groups}")
        # Первым — одно малое окно: если хватит его, приёмы против фильтра имён не нужны,
        # а «Применить» запишет только профиль по адресам.
        return [Candidate(WINDOW_ONLY_ID, WINDOW_ONLY_NAME, WINDOW_STRATEGY_LINE)]

    def _pass_control(self, report: StrategyScanReport) -> bool:
        """Запуск winws2 без приёмов обхода: цель должна остаться закрытой."""
        self._events.phase("Контрольный запуск winws2")
        self._stage(STEP_CONTROL, "running", "Запускаем winws2 вхолостую")
        run = self._test_strategy(Candidate("pass", "pass", PASS_STRATEGY_ARGS), confirm=False)
        if run.verdict == rules.VERDICT_CANCELLED:
            return False
        if run.verdict == rules.VERDICT_CRASH:
            raise ScanFatal(f"winws2 не запускается даже с пустой стратегией: {run.reason}", STOP_WINWS)
        if any(attempt.passed for attempt in run.attempts):
            report.baseline_accessible = True
            self._stage(STEP_CONTROL, "failed", "Открывается даже от пустого запуска winws2")
            if not self._ask_continue(
                "Цель открывается от одного запуска winws2, даже без приёмов обхода. "
                "Так любая стратегия покажется рабочей."
            ):
                return False
            self._target.forced = True
            return True
        self._events.log("  Контроль пройден: без приёмов обхода цель закрыта")
        self._stage(STEP_CONTROL, "done", "Контрольный запуск: без приёмов обхода закрыто")
        return True

    # --- Одна стратегия -------------------------------------------------------------

    def _profile(self, strategy_args: str, *, window: bool = True) -> ProbeProfile:
        """Профиль пробы. ``window=False`` — без профиля малого окна (контрольный запуск: «без приёмов»)."""
        games_paths: list[str] = []
        if self._protocol == PROTOCOL_UDP_GAMES:
            games_paths = self._env.games_ipset_paths(self._request.udp_games_scope)

        site = self._protocol in (PROTOCOL_TCP_HTTPS, PROTOCOL_HTTP80, PROTOCOL_QUIC)
        return build_probe_profile(
            self._protocol,
            strategy_args=strategy_args,
            match_domain=self._match_domains() if site else self._request.target,
            games_ipset_paths=games_paths,
            games_addresses=self._target.games_addresses,
            games_ports=self._target.games_ports,
            window_addresses=self._target.window_addresses if window and self._protocol == PROTOCOL_TCP_HTTPS else (),
        )

    def _match_domains(self) -> str:
        """Имена профиля: цель и остальные имена сервиса через запятую (``--hostlist-domains`` покрывает и поддомены)."""
        from blockcheck.strategy_scan_targeting import profile_domain

        names = [profile_domain(self._request.target), *(profile_domain(domain) for domain, _host in self._request.companions)]
        return ",".join(dict.fromkeys(name for name in names if name))

    def _probe_once(self, *, volume: bool = False) -> _Attempt:
        """Одна попытка. ``volume`` — короткие ответы добрать объёмом по соединению."""
        if self._protocol == PROTOCOL_QUIC:
            outcomes = self._env.probe_quic(self._target.host, self._target.addresses)
            passed = rules.attempt_passed(outcomes)
            # Молчание UDP не отличить от пропавшей сети: провал сверяется с
            # обычными сайтами; повтор уже был внутри пробы. У QUIC провал —
            # всегда тишина: одна потеря не считается следом DPI.
            return _Attempt(
                passed,
                rules.attempt_reason(outcomes),
                rules.attempt_time_ms(outcomes),
                not passed,
                silent=not passed,
            )
        if self._protocol == PROTOCOL_HTTP80:
            outcomes = self._env.probe_http(self._target.host, self._target.addresses)
            passed = rules.attempt_passed(outcomes)
            return _Attempt(
                passed,
                rules.attempt_reason(outcomes),
                rules.attempt_time_ms(outcomes),
                rules.attempt_needs_network_check(outcomes),
                silent=not passed and rules.attempt_silent(outcomes),
            )
        if self._protocol == PROTOCOL_TCP_HTTPS:
            host = self._target.host
            outcomes = self._env.probe_https(host, self._target.addresses)
            if volume and rules.attempt_passed(outcomes):
                outcomes = [
                    self._env.probe_volume(host, address, outcome) if rules.needs_volume(outcome) else outcome
                    for address, outcome in zip(self._target.addresses, outcomes)
                ]
            passed = rules.attempt_passed(outcomes)
            volumes = {outcome.volume for outcome in outcomes if outcome.volume}
            reason = rules.attempt_reason(outcomes)
            # Сервис открыт, только когда открыты все его имена: страница без видеосервера — не победа.
            for companion, addresses in self._target.companions if passed else ():
                extra = self._env.probe_https(companion, addresses)
                if not rules.attempt_passed(extra):
                    passed, reason, outcomes = False, f"{companion}: {rules.attempt_reason(extra)}", extra
                    break
            return _Attempt(
                passed,
                reason,
                rules.attempt_time_ms(outcomes),
                rules.attempt_needs_network_check(outcomes),
                silent=not passed and rules.attempt_silent(outcomes),
                volume=(
                    rules.VOLUME_CUT if rules.VOLUME_CUT in volumes
                    else rules.VOLUME_UNKNOWN if rules.VOLUME_UNKNOWN in volumes
                    else rules.VOLUME_OK if volumes else ""
                ),
                transfer=rules.worst_transfer(outcomes),
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
            # сверяется с обычными сайтами и считается тишиной, а не следом DPI.
            not passed,
            silent=not passed,
        )

    def _test_strategy(self, candidate: Candidate, *, confirm: bool = True) -> _StrategyRun:
        """Проверка одной стратегии. ``confirm=False`` — контрольный запуск: до
        ``CONTROL_PROBES`` проб, до первой открывшей цель."""
        # Контрольный запуск — winws2 без приёмов обхода, малое окно тоже приём.
        profile = self._profile(candidate.args, window=confirm)
        config_text = build_probe_config_text(profile, self._env.blob_lines(candidate.args))
        crash_reason = ""
        previous_crash = ""
        for crash_try in range(1 + CRASH_RETRIES):
            if self.cancelled:
                return _StrategyRun(rules.VERDICT_CANCELLED)
            session = None
            try:
                session = self._env.start_session(config_text)
                start = session.start()
            except OSError as exc:
                # Антивирус занял файл пробы или не дал запустить winws2: это сбой
                # одной стратегии, а не всего подбора.
                if session is not None:
                    try:
                        session.stop()
                    except Exception:
                        pass
                crash_reason = f"не удалось запустить: {exc}"
                self._events.log(f"  winws2 не запустился: {crash_reason}")
                if crash_reason == previous_crash:
                    return _StrategyRun(rules.VERDICT_CRASH, [], crash_reason, repeatable_crash=True)
                previous_crash = crash_reason
                self._env.recover_after_crash()
                continue
            if self.cancelled:
                session.stop()
                return _StrategyRun(rules.VERDICT_CANCELLED)
            if not getattr(start, "ok", False):
                crash_reason = self._crash_text(start)
                self._events.log(f"  winws2 не запустился: {crash_reason}")
                session.stop()
                if crash_reason and crash_reason == previous_crash and not getattr(start, "not_ready", False):
                    # Второй раз то же самое: перезапуски не помогут, стратегию запомним.
                    return _StrategyRun(rules.VERDICT_CRASH, [], crash_reason, repeatable_crash=True)
                previous_crash = crash_reason
                self._env.recover_after_crash()
                continue
            attempts: list[_Attempt] = []
            crashed = False
            network_lost = False
            try:
                while not self.cancelled:
                    if confirm and rules.attempts_done([attempt.mark for attempt in attempts]):
                        break
                    if not confirm and (len(attempts) >= CONTROL_PROBES or any(a.passed for a in attempts)):
                        break
                    # Объём добирается один раз — при первой удачной попытке; если
                    # фильтр режет по объёму, то в каждой.
                    volume = self._target.volume_block or not any(attempt.volume for attempt in attempts)
                    attempt = self._probe_once(volume=volume)
                    if not session.alive():
                        crashed = True
                        crash_reason = session.output_tail(3) or "процесс завершился во время проверки"
                        break
                    attempts.append(attempt)
                    if confirm and not attempt.passed and attempt.network_suspect and not self._env.control_alive():
                        network_lost = True
                        break
            finally:
                session.stop()
            if self.cancelled:
                return _StrategyRun(rules.VERDICT_CANCELLED, attempts)
            if crashed:
                self._events.log(f"  winws2 упал во время проверки, перезапуск ({crash_try + 1})")
                self._env.recover_after_crash()
                continue
            verdict = rules.final_verdict([attempt.mark for attempt in attempts])
            reason = next((attempt.reason for attempt in attempts if not attempt.passed), "")
            if network_lost:
                return _StrategyRun(rules.VERDICT_FAILED, attempts, reason, network_lost=True)
            if confirm and verdict == rules.VERDICT_WORKING and not self._target.forced:
                for _number in range(CONTROL_PROBES):
                    if self.cancelled:
                        return _StrategyRun(rules.VERDICT_CANCELLED, attempts)
                    if self._probe_once(volume=self._target.volume_block).passed:
                        return _StrategyRun(
                            rules.VERDICT_NOT_COUNTED,
                            attempts,
                            "без обхода цель тоже открылась — сеть нестабильна, успех не засчитан",
                        )
                # Stop в последней пробе: она провалилась из-за отмены, а не потому, что цель закрыта.
                if self.cancelled:
                    return _StrategyRun(rules.VERDICT_CANCELLED, attempts)
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
            strategy_name=candidate.name + (
                " + малое окно по адресам" if self._target.window_addresses and candidate.strategy_id != WINDOW_ONLY_ID else ""
            ),
            strategy_id=candidate.strategy_id,
            strategy_args=candidate.args,
            target=self._request.target,
            success=success,
            # Медиана удачных попыток: время одной первой пробы — случайность, по ней «лучшую» не выбрать.
            time_ms=statistics.median(attempt.time_ms for attempt in passed) if passed else 0.0,
            error="" if success else (run.reason or self._verdict_text(run.verdict)),
            scan_protocol=self._protocol,
            verdict=run.verdict,
            attempts_ok=len(passed),
            attempts_total=len(run.attempts),
            apply_lines=tuple(self._profile(candidate.args).apply_lines()) if success else (),
            raw_data={
                "forced": self._target.forced,
                "volume": run.volume,
                # Пауза или медленная передача при открывшейся цели: от стратегии не зависит ("" — не замечено).
                "transfer": run.transfer,
                # Какой хост открывали: у googlevideo.com это видеосервер rr…, а не сама цель.
                "probe_host": self._target.host,
                # Остальные имена сервиса (хост для пробы): после «Применить» открываться должны и они.
                "companions": [host for host, _addresses in self._target.companions],
                # Адреса, где TLS режут по ответу сервера: стратегия проверена с профилем малого окна по ним.
                "window_addresses": list(self._target.window_addresses),
            },
        )

    def _log_result(self, index: int, total: int, candidate: Candidate, run: _StrategyRun) -> None:
        passed = sum(1 for attempt in run.attempts if attempt.passed)
        head = f"[{index + 1}/{total}] {candidate.name}: {self._verdict_text(run.verdict)}"
        if run.verdict in (rules.VERDICT_WORKING, rules.VERDICT_UNSTABLE, rules.VERDICT_NOT_COUNTED):
            head += f" {passed}/{len(run.attempts)}"
        if run.verdict == rules.VERDICT_WORKING and run.volume == rules.VOLUME_UNKNOWN:
            head += " (объём не проверен: сайт отвечает коротко)"
        if run.verdict == rules.VERDICT_WORKING and run.transfer:
            head += " (сервер отвечает медленно или с паузами — от стратегии это не зависит)"
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
            PROTOCOL_HTTP80: "сайт без HTTPS (HTTP, порт 80)",
            PROTOCOL_QUIC: "быстрый протокол QUIC (UDP 443)",
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
