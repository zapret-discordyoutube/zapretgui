"""Правила подбора: что значит «открылось», «заблокировано», «сеть пропала».

Здесь нет сети и процессов — только решения по уже полученным ответам.
Поэтому каждое правило проверяется тестами без Windows и без winws2.

Главные принципы (как в оригинальном blockcheck2 из zapret2):

- стратегию проверяют, только если цель действительно закрыта без обхода;
- стратегия «работает», только если помогла на тех же адресах, которые без
  обхода были закрыты, и помогла несколько раз подряд;
- сбой сети или падение winws2 не записывается в провал стратегии.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# --- Исход одной проверки адреса -------------------------------------------

PROBE_OK = "ok"
# Соединение до сервера есть, но ответ сорван: сброс, обрыв, таймаут после
# приветствия TLS, подменённый сертификат. Так выглядит работа DPI.
PROBE_BLOCKED = "blocked"
# До сервера не удалось даже подключиться. Стратегии обхода DPI тут не помогут:
# это блокировка адреса целиком или сервер выключен.
PROBE_UNREACHABLE = "unreachable"
PROBE_CANCELLED = "cancelled"

# Обрыв ответа на 14–24 КБ — характерная блокировка «по объёму» (ТСПУ).
FREEZE_MIN_BYTES = 14_000
FREEZE_MAX_BYTES = 24_000
# Сколько байт ответа читать: больше верхней границы обрыва, иначе его не видно.
HTTPS_READ_LIMIT = 32_768


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    state: str
    reason: str = ""
    time_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.state == PROBE_OK


def judge_https(result) -> ProbeOutcome:
    """Итог HTTPS-запроса ``diagnostics.tls_probe.https_get``.

    Сертификат проверяется для имени сайта, поэтому любой ответ после
    успешного TLS пришёл от настоящего сервера. Провайдер не может подсунуть
    туда свою страницу-заглушку, не сломав сертификат. Значит, маркеры
    заглушки в HTTPS не нужны, а проверять надо другое:

    - пришла ли строка статуса HTTP (без неё ответа нет);
    - не 400 ли это: так сервер отвечает, когда до него дошли фейковые пакеты
      стратегии (правило blockcheck2);
    - не оборвался ли ответ посередине (блокировка по объёму).
    """
    from diagnostics import tls_probe

    kind = result.kind
    elapsed = float(result.elapsed_ms or 0.0)
    if kind == tls_probe.KIND_CANCELLED:
        return ProbeOutcome(PROBE_CANCELLED, "отменено", elapsed)
    if kind == tls_probe.KIND_CONNECT:
        return ProbeOutcome(PROBE_UNREACHABLE, "сервер не отвечает на подключение", elapsed)
    if kind == tls_probe.KIND_CERT:
        problem = result.cert_problem or "сертификат не прошёл проверку"
        return ProbeOutcome(PROBE_BLOCKED, f"подменён сертификат: {problem}", elapsed)
    if kind == tls_probe.KIND_TLS:
        return ProbeOutcome(PROBE_BLOCKED, "обрыв при установке шифрования", elapsed)
    if kind == tls_probe.KIND_RESET:
        return ProbeOutcome(PROBE_BLOCKED, "соединение сброшено", elapsed)
    if kind == tls_probe.KIND_TIMEOUT:
        stage = "шифрования" if result.stage == tls_probe.STAGE_TLS else "ответа"
        return ProbeOutcome(PROBE_BLOCKED, f"нет {stage} (таймаут)", elapsed)
    if kind != tls_probe.KIND_OK:
        return ProbeOutcome(PROBE_BLOCKED, "сервер ответил не по HTTP", elapsed)

    status = result.status
    if status is None:
        return ProbeOutcome(PROBE_BLOCKED, "нет ответа HTTP", elapsed)
    if result.body_cut and result.body_size < FREEZE_MAX_BYTES:
        size_kb = max(1, round(result.body_size / 1024))
        if result.body_size >= FREEZE_MIN_BYTES:
            return ProbeOutcome(PROBE_BLOCKED, f"ответ обрывается на ~{size_kb} КБ (блокировка по объёму)", elapsed)
        return ProbeOutcome(PROBE_BLOCKED, f"ответ оборвался на ~{size_kb} КБ", elapsed)
    if status == 400:
        return ProbeOutcome(PROBE_BLOCKED, "сервер ответил 400: до него дошли фейковые пакеты", elapsed)
    return ProbeOutcome(PROBE_OK, f"HTTP {status}", elapsed)


# --- Проверка «до подбора» ----------------------------------------------------

BASELINE_BLOCKED = "blocked"
BASELINE_OPEN = "open"
BASELINE_NOT_DPI = "not_dpi"


@dataclass(frozen=True, slots=True)
class BaselineDecision:
    state: str
    # Адреса, на которых дальше проверяются все стратегии.
    probe_addresses: tuple[str, ...] = ()
    reason: str = ""


def decide_baseline(
    outcomes: Sequence[tuple[str, ProbeOutcome]],
    *,
    max_probe_addresses: int = 2,
) -> BaselineDecision:
    """Что делать дальше по проверке цели без обхода.

    - Хоть один адрес закрыт так, как закрывает DPI, — подбираем, и все
      стратегии проверяем именно на закрытых адресах.
    - Всё открывается — подбирать нечего (решение за пользователем).
    - Ни до одного адреса не достучаться — DPI тут ни при чём, подбор бесполезен.
    """
    blocked = [address for address, outcome in outcomes if outcome.state == PROBE_BLOCKED]
    if blocked:
        return BaselineDecision(
            BASELINE_BLOCKED,
            tuple(_one_per_family(blocked)[: max(1, int(max_probe_addresses))]),
            _first_reason(outcomes, PROBE_BLOCKED),
        )
    opened = [address for address, outcome in outcomes if outcome.state == PROBE_OK]
    if opened:
        return BaselineDecision(BASELINE_OPEN, tuple(opened[: max(1, int(max_probe_addresses))]))
    if not outcomes:
        return BaselineDecision(BASELINE_NOT_DPI, (), "адрес цели не найден")
    reason = _first_reason(outcomes, PROBE_UNREACHABLE) or "цель не отвечает"
    return BaselineDecision(BASELINE_NOT_DPI, (), reason)


def _one_per_family(addresses: Iterable[str]) -> list[str]:
    """По одному адресу IPv4 и IPv6: этого хватает, а проверка вдвое быстрее."""
    picked: list[str] = []
    seen: set[bool] = set()
    for address in addresses:
        is_v6 = ":" in address
        if is_v6 in seen:
            continue
        seen.add(is_v6)
        picked.append(address)
    return picked


def _first_reason(outcomes: Sequence[tuple[str, ProbeOutcome]], state: str) -> str:
    for _address, outcome in outcomes:
        if outcome.state == state and outcome.reason:
            return outcome.reason
    return ""


# --- Одна попытка стратегии -----------------------------------------------------


def attempt_passed(outcomes: Sequence[ProbeOutcome]) -> bool:
    """Попытка засчитана, только если открылись все проверяемые адреса."""
    return bool(outcomes) and all(outcome.ok for outcome in outcomes)


def attempt_reason(outcomes: Sequence[ProbeOutcome]) -> str:
    for outcome in outcomes:
        if not outcome.ok:
            return outcome.reason or outcome.state
    return ""


def attempt_time_ms(outcomes: Sequence[ProbeOutcome]) -> float:
    """Время ответа — самый медленный из проверенных адресов."""
    return max((float(outcome.time_ms) for outcome in outcomes), default=0.0)


def attempt_needs_network_check(outcomes: Sequence[ProbeOutcome]) -> bool:
    """Провал без явного следа DPI мог быть сбоем сети: его надо перепроверить.

    Сброс соединения или подменённый сертификат — это активное вмешательство,
    сеть при этом жива. А таймаут и «не подключиться» одинаково выглядят и при
    блокировке, и при пропавшем интернете.
    """
    for outcome in outcomes:
        if outcome.ok:
            continue
        if outcome.state == PROBE_UNREACHABLE:
            return True
        if "таймаут" in outcome.reason or "нет ответа" in outcome.reason:
            return True
    return False


# --- UDP: голос и игры ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UdpProbeSpec:
    """Одна UDP-проверка: основная цель пользователя или вспомогательный сервер."""

    name: str
    kind: str  # "stun" | "source_a2s" | "bedrock_ping"
    host: str
    port: int
    address: str = ""
    primary: bool = False


def decide_udp_baseline(results: Sequence[tuple[UdpProbeSpec, ProbeOutcome]]) -> tuple[str, tuple[UdpProbeSpec, ...], str]:
    """Проверка «до подбора» для UDP: какие проверки закрыты без обхода.

    UDP не отличает «заблокировано» от «сервер молчит», поэтому закрытыми
    считаются все молчащие проверки. Дальше по ним и судят стратегию.
    """
    reachable = [(spec, outcome) for spec, outcome in results if outcome.state != PROBE_UNREACHABLE]
    if not reachable:
        reason = next((outcome.reason for _spec, outcome in results if outcome.reason), "")
        return BASELINE_NOT_DPI, (), reason or "адрес цели не найден"
    blocked = tuple(spec for spec, outcome in reachable if outcome.state == PROBE_BLOCKED)
    if blocked:
        reason = next((outcome.reason for spec, outcome in reachable if spec in blocked and outcome.reason), "")
        return BASELINE_BLOCKED, blocked, reason
    return BASELINE_OPEN, tuple(spec for spec, _outcome in reachable), ""


def udp_attempt_passed(results: Sequence[tuple[UdpProbeSpec, ProbeOutcome]]) -> bool:
    """Попытка UDP засчитана по проверкам, закрытым без обхода.

    Если основная цель была закрыта — она обязана открыться. Иначе хватает
    любой из закрытых вспомогательных проверок: игровые серверы бывают
    выключены, и требовать ответа от всех значило бы никогда не найти стратегию.
    Проверки, открытые и без обхода, сюда не передаются вовсе — иначе любая
    стратегия «работала» бы за их счёт.
    """
    if not results:
        return False
    primary = [outcome for spec, outcome in results if spec.primary]
    if primary:
        return all(outcome.ok for outcome in primary)
    return any(outcome.ok for _spec, outcome in results)


# --- Итог стратегии -----------------------------------------------------------------

VERDICT_WORKING = "working"
VERDICT_UNSTABLE = "unstable"
VERDICT_FAILED = "failed"
VERDICT_CRASH = "crash"
# Стратегия прошла, но без обхода цель тоже открылась: сеть нестабильна,
# и успех нельзя приписать стратегии.
VERDICT_NOT_COUNTED = "not_counted"
VERDICT_CANCELLED = "cancelled"

CONFIRM_ATTEMPTS = 3


def final_verdict(passed_attempts: Sequence[bool]) -> str:
    """Итог по попыткам: первая — отбор, остальные — перепроверка победителя."""
    if not passed_attempts or not passed_attempts[0]:
        return VERDICT_FAILED
    if len(passed_attempts) >= CONFIRM_ATTEMPTS and all(passed_attempts):
        return VERDICT_WORKING
    return VERDICT_UNSTABLE
