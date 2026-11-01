"""Правила подбора: что значит «открылось», «заблокировано», «сеть пропала».

Здесь нет сети и процессов — только решения по уже полученным ответам.
Поэтому каждое правило проверяется тестами без Windows и без winws2.

Главные принципы (как в оригинальном blockcheck2 из zapret2):

- стратегию проверяют, только если цель действительно закрыта без обхода;
- стратегия «работает», только если помогла на тех же адресах, которые без
  обхода были закрыты, и помогла несколько раз подряд;
- «открылось» значит «прошёл объём», а не только «ответил»: фильтр режет
  соединение где-то в окне ``FREEZE_MIN_BYTES``–``FREEZE_MAX_BYTES``
  (``diagnostics.limits``, «обрыв на 16 КБ»), а короткая страница это окно
  не пересекает. Где оборвалось, решает общее правило ``diagnostics.cut_window``
  (оно же у проверки сайтов): байты считаются как у фильтра, с рукопожатием;
- первая попытка — отбор: её провал сразу «не работает». После первого
  успеха одна случайная тишина стратегию не губит, явный след DPI губит;
- в историю провалов не идут: провал при пропавшей сети, «Стоп», разовое
  падение winws2 и запуск, на котором перехват не стал готов. Идёт падение
  winws2, повторившееся на этой стратегии одинаково.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from diagnostics import cut_window
from diagnostics.limits import FREEZE_MAX_BYTES
from utils.net_failure import (
    FAILURE_CANCELLED,
    FAILURE_CERT,
    FAILURE_CLOSED,
    FAILURE_HTTP,
    FAILURE_MALFORMED,
    FAILURE_RESET,
    FAILURE_TIMEOUT,
    FAILURE_TLS,
)

# --- Исход одной проверки адреса -------------------------------------------

PROBE_OK = "ok"
# Соединение до сервера есть, но ответ сорван: сброс, обрыв, таймаут после
# приветствия TLS, подменённый сертификат. Так выглядит работа DPI.
PROBE_BLOCKED = "blocked"
# До сервера не удалось даже подключиться. Стратегии обхода DPI тут не помогут:
# это блокировка адреса целиком или сервер выключен.
PROBE_UNREACHABLE = "unreachable"
PROBE_CANCELLED = "cancelled"

# Обрыв ответа внутри общего окна ``diagnostics.limits.FREEZE_*`` — блокировка
# «по объёму» (ТСПУ). Подбор и проверка сайтов судят по одному окну: стратегия,
# после которой сайт замирает на 28 КБ, не работает, а не «работает».
# Сколько байт ответа читать: больше верхней границы обрыва, иначе его не видно.
HTTPS_READ_LIMIT = FREEZE_MAX_BYTES + 4 * 1024


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    state: str
    reason: str = ""
    time_ms: float = 0.0
    # Вид сбоя из ``utils.net_failure``; пусто, если адрес ответил. Правила опираются
    # на него, а ``reason`` — только текст для человека.
    failure: str = ""
    # Сколько байт ответ провёз по счёту фильтра (``cut_window``): по нему решается,
    # нужна ли проверка объёма. 0 — не измерено.
    body_size: int = 0
    # Проверка объёма по соединению: "" — не делалась, VOLUME_* — её итог.
    volume: str = ""
    # Как идут ответы по соединению (``diagnostics.transfer_probe``): код вывода и его текст.
    # Пусто — объём не набирали или замеров мало. Пауза или медленный ответ — свойство
    # сервера или канала: открывшуюся стратегию они не опровергают, но подбор тут не поможет.
    transfer: str = ""
    transfer_text: str = ""

    @property
    def ok(self) -> bool:
        return self.state == PROBE_OK


def judge_https(result) -> ProbeOutcome:
    """Итог HTTPS-запроса как у Chrome (``utils.chrome_https.chrome_get``).

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
        return ProbeOutcome(PROBE_CANCELLED, "отменено", elapsed, FAILURE_CANCELLED)
    if kind == tls_probe.KIND_CONNECT:
        return ProbeOutcome(PROBE_UNREACHABLE, "сервер не отвечает на подключение", elapsed, result.connect_fail)
    if kind == tls_probe.KIND_CERT:
        problem = result.cert_problem or "сертификат не прошёл проверку"
        return ProbeOutcome(PROBE_BLOCKED, f"подменён сертификат: {problem}", elapsed, FAILURE_CERT)
    if kind == tls_probe.KIND_TLS:
        return ProbeOutcome(PROBE_BLOCKED, "обрыв при установке шифрования", elapsed, FAILURE_TLS)
    if kind == tls_probe.KIND_RESET:
        return ProbeOutcome(PROBE_BLOCKED, "соединение сброшено", elapsed, FAILURE_RESET)
    if kind == tls_probe.KIND_TIMEOUT:
        stage = "шифрования" if result.stage == tls_probe.STAGE_TLS else "ответа"
        return ProbeOutcome(PROBE_BLOCKED, f"нет {stage} (таймаут)", elapsed, FAILURE_TIMEOUT)
    if kind != tls_probe.KIND_OK:
        return ProbeOutcome(PROBE_BLOCKED, "сервер ответил не по HTTP", elapsed, FAILURE_MALFORMED)

    status = result.status
    if status is None:
        return ProbeOutcome(PROBE_BLOCKED, "нет ответа HTTP", elapsed, FAILURE_CLOSED)
    received = cut_window.result_bytes(result)
    cut = cut_window.result_cut(result)
    if cut in (cut_window.CUT_WINDOW, cut_window.CUT_EARLY):
        size_kb = max(1, round(received / 1024))
        if cut == cut_window.CUT_WINDOW:
            return ProbeOutcome(PROBE_BLOCKED, f"ответ обрывается на ~{size_kb} КБ (блокировка по объёму)", elapsed, FAILURE_RESET)
        # Раньше окна: страница не догрузилась — стратегия сайт не открыла, хотя на обычный
        # обрыв ТСПУ это не похоже.
        return ProbeOutcome(PROBE_BLOCKED, f"ответ оборвался на ~{size_kb} КБ", elapsed, FAILURE_RESET)
    if status == 400:
        return ProbeOutcome(PROBE_BLOCKED, "сервер ответил 400: до него дошли фейковые пакеты", elapsed, FAILURE_HTTP)
    return ProbeOutcome(PROBE_OK, f"HTTP {status}", elapsed, body_size=received)


def judge_http(
    *,
    host: str,
    status: int | None,
    location: str = "",
    body: bytes = b"",
    failure: str = "",
    connected: bool = True,
    elapsed_ms: float = 0.0,
    body_cut: bool = False,
    body_size: int = 0,
) -> ProbeOutcome:
    """Итог обычного HTTP-запроса к порту 80.

    Без шифрования провайдер может подменить ответ, поэтому, кроме обрыва,
    блокировкой считается и страница-заглушка (перенаправление на страницу о
    блокировке, код 451, текст о реестре — ``diagnostics.block_cause.stub_reason``),
    а 400 — как и в HTTPS: до сервера дошли фейковые пакеты стратегии.
    ``body_cut`` — заголовок пришёл, а тело сброшено или замерло: это тоже
    блокировка, хотя статус уже 200. ``body_size`` — байты всего ответа (заголовки
    и тело): так их считает фильтр.
    """
    from diagnostics.block_cause import HttpFacts, stub_reason

    if failure == FAILURE_CANCELLED:
        return ProbeOutcome(PROBE_CANCELLED, "отменено", elapsed_ms, FAILURE_CANCELLED)
    if not connected:
        return ProbeOutcome(PROBE_UNREACHABLE, "сервер не отвечает на подключение", elapsed_ms, failure)
    if status is None:
        reasons = {FAILURE_RESET: "соединение сброшено", FAILURE_TIMEOUT: "нет ответа (таймаут)"}
        return ProbeOutcome(PROBE_BLOCKED, reasons.get(failure, "нет ответа HTTP"), elapsed_ms, failure or FAILURE_CLOSED)
    stub = stub_reason(host, HttpFacts(status=status, location=location, body=body))
    if stub:
        return ProbeOutcome(PROBE_BLOCKED, f"заглушка провайдера: {stub}", elapsed_ms, FAILURE_HTTP)
    if status == 400:
        return ProbeOutcome(PROBE_BLOCKED, "сервер ответил 400: до него дошли фейковые пакеты", elapsed_ms, FAILURE_HTTP)
    if body_cut:
        size_kb = max(1, round(body_size / 1024))
        # Без шифрования фильтр считает только сам ответ: заголовки и тело (``body_size``).
        if cut_window.cut_place(body_size) == cut_window.CUT_WINDOW:
            return ProbeOutcome(
                PROBE_BLOCKED, f"ответ обрывается на ~{size_kb} КБ (блокировка по объёму)", elapsed_ms, failure or FAILURE_RESET
            )
        return ProbeOutcome(PROBE_BLOCKED, f"ответ оборвался на ~{size_kb} КБ", elapsed_ms, failure or FAILURE_RESET)
    return ProbeOutcome(PROBE_OK, f"HTTP {status}", elapsed_ms, body_size=body_size)


# --- Объём: прошло ли соединение порог обрыва ---------------------------------------

VOLUME_OK = "ok"
VOLUME_CUT = "cut"
# Набрать объём не удалось (сервер отвечает мало и закрывает соединение):
# обрыв не проверен, но и не найден.
VOLUME_UNKNOWN = "unknown"


def needs_volume(outcome: ProbeOutcome) -> bool:
    """Адрес ответил так коротко, что обрыв по объёму (окно ``FREEZE_*_BYTES``) не мог проявиться."""
    return outcome.ok and not outcome.volume and not cut_window.passed_window(outcome.body_size)


def judge_volume(answered: ProbeOutcome, verdict, transfer=None) -> ProbeOutcome:
    """Итог ``diagnostics.volume_probe.judge`` поверх ответа того же адреса.

    Замирание в окне обрыва — явный след фильтра: такая стратегия не работает,
    сколько бы раз ни открывалась короткая страница. ``transfer`` — вывод о
    скорости ответов по тем же замерам (``diagnostics.transfer_probe.TransferVerdict``).
    """
    from diagnostics import volume_probe

    transfer_code = str(getattr(transfer, "code", "") or "")
    transfer_text = str(getattr(transfer, "text", "") or "")

    if verdict.code == volume_probe.VOLUME_CUT:
        return ProbeOutcome(
            PROBE_BLOCKED,
            f"соединение замирает после ~{max(1, verdict.cut_kb)} КБ (блокировка по объёму)",
            answered.time_ms,
            FAILURE_RESET,
            answered.body_size,
            VOLUME_CUT,
            transfer_code,
            transfer_text,
        )
    code = VOLUME_OK if verdict.code == volume_probe.VOLUME_OK else VOLUME_UNKNOWN
    return ProbeOutcome(
        answered.state, answered.reason, answered.time_ms, answered.failure, answered.body_size, code,
        transfer_code, transfer_text,
    )


# Выводы о передаче, при которых подбирать стратегию бесполезно: сервер или канал отвечают
# с паузами или медленно, и от обхода это не зависит.
SLOW_TRANSFER_CODES = ("pause", "slow_reply", "slow_body")


def transfer_problem(outcomes: Iterable[ProbeOutcome]) -> str:
    """Текст о паузах или медленной передаче по пробам объёма; пусто — жалоб нет."""
    for outcome in outcomes:
        if outcome.transfer in SLOW_TRANSFER_CODES and outcome.transfer_text:
            return outcome.transfer_text
    return ""


def worst_transfer(outcomes: Iterable[ProbeOutcome]) -> str:
    """Самый тяжёлый вывод о передаче среди проб: пауза, затем медленный ответ, затем медленное тело."""
    codes = {outcome.transfer for outcome in outcomes if outcome.transfer in SLOW_TRANSFER_CODES}
    return next((code for code in SLOW_TRANSFER_CODES if code in codes), "")


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
    # Закрыто обрывом по объёму: цель отвечает, но соединение замирает.
    volume_block: bool = False


def decide_baseline(
    outcomes: Sequence[tuple[str, ProbeOutcome]],
    *,
    max_probe_addresses: int = 3,
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
            tuple(spread_addresses(blocked, max(1, int(max_probe_addresses)))),
            _first_reason(outcomes, PROBE_BLOCKED),
        )
    opened = [address for address, outcome in outcomes if outcome.state == PROBE_OK]
    if opened:
        return BaselineDecision(BASELINE_OPEN, tuple(opened[: max(1, int(max_probe_addresses))]))
    if not outcomes:
        return BaselineDecision(BASELINE_NOT_DPI, (), "адрес цели не найден")
    reason = _first_reason(outcomes, PROBE_UNREACHABLE) or "цель не отвечает"
    return BaselineDecision(BASELINE_NOT_DPI, (), reason)


def _subnet(address: str) -> str:
    if ":" in address:
        return ":".join(address.split(":")[:4])
    return ".".join(address.split(".")[:3])


def spread_addresses(addresses: Iterable[str], limit: int = 3) -> list[str]:
    """Адреса из разных подсетей: два IPv4 и один IPv6.

    Соседние адреса одного хостинга фильтр режет по-разному, поэтому стратегия,
    проверенная на одном сервере, может не открыть другой. Адреса из одной
    подсети почти всегда ведут себя одинаково — их второй раз не берём.
    Адреса проверяются параллельно, так что время попытки почти не растёт.
    """
    items = list(dict.fromkeys(str(address) for address in addresses if address))
    v4 = [address for address in items if ":" not in address]
    v6 = [address for address in items if ":" in address]
    picked: list[str] = []
    subnets: set[str] = set()
    for address in v4:
        if len(picked) >= 2:
            break
        if _subnet(address) not in subnets:
            subnets.add(_subnet(address))
            picked.append(address)
    if v6:
        picked.append(v6[0])
    if not picked:
        picked = items[:1]
    return picked[: max(1, int(limit))]


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
        if outcome.failure in _SILENT_FAILURES:
            return True
    return False


def attempt_silent(outcomes: Sequence[ProbeOutcome]) -> bool:
    """Провал без единого явного следа DPI: только тишина или «не подключиться».

    Такой провал бывает и от потерянного пакета, поэтому стратегии дают ещё
    одну попытку. Сброс, чужой сертификат, ответ 400 или замирание на объёме
    случайностью не объяснить — после них повтор не нужен.
    """
    failed = [outcome for outcome in outcomes if not outcome.ok]
    if not failed:
        return False
    return all(
        outcome.state == PROBE_UNREACHABLE or outcome.failure in _SILENT_FAILURES
        for outcome in failed
    )


# Тишина и закрытое без ответа соединение: так выглядит и DPI, и пропавший интернет.
_SILENT_FAILURES = frozenset({FAILURE_TIMEOUT, FAILURE_CLOSED})


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

# Столько раз стратегия должна открыть цель на свежих соединениях.
CONFIRM_ATTEMPTS = 3
# Столько тихих провалов (молчание, потерянный пакет) прощается при
# подтверждении: без этого одна потеря на линии делала открывшую цель
# стратегию «нестабильной».
SILENT_FAILURES_ALLOWED = 1
MAX_ATTEMPTS = CONFIRM_ATTEMPTS + SILENT_FAILURES_ALLOWED


@dataclass(frozen=True, slots=True)
class AttemptMark:
    passed: bool
    # Провал без явного следа DPI (только для провала).
    silent: bool = False


def _marks(attempts: Sequence[AttemptMark | bool]) -> list[AttemptMark]:
    return [item if isinstance(item, AttemptMark) else AttemptMark(bool(item)) for item in attempts]


def attempts_done(attempts: Sequence[AttemptMark | bool]) -> bool:
    """Хватит ли попыток для вывода.

    Первая попытка — отбор: провал любого вида сразу «не работает», иначе
    каждая нерабочая стратегия на «тихом» фильтре стоила бы два таймаута.
    Дальше принимаем после трёх успехов; отвергаем после явного следа DPI или
    второй тишины. Так случайная потеря у уже открывшей цель стратегии стоит
    одну лишнюю попытку, а не отметку «нестабильно».
    """
    marks = _marks(attempts)
    passed = sum(1 for mark in marks if mark.passed)
    explicit = sum(1 for mark in marks if not mark.passed and not mark.silent)
    silent = sum(1 for mark in marks if not mark.passed and mark.silent)
    return (
        (bool(marks) and not marks[0].passed)
        or passed >= CONFIRM_ATTEMPTS
        or explicit > 0
        or silent > SILENT_FAILURES_ALLOWED
        or len(marks) >= MAX_ATTEMPTS
    )


def final_verdict(attempts: Sequence[AttemptMark | bool]) -> str:
    """Итог по попыткам: ни одного успеха — не работает; три успеха без явного
    провала и не больше одной тишины — работает; иначе — нестабильно."""
    marks = _marks(attempts)
    passed = sum(1 for mark in marks if mark.passed)
    explicit = sum(1 for mark in marks if not mark.passed and not mark.silent)
    silent = sum(1 for mark in marks if not mark.passed and mark.silent)
    if passed == 0:
        return VERDICT_FAILED
    if passed >= CONFIRM_ATTEMPTS and explicit == 0 and silent <= SILENT_FAILURES_ALLOWED:
        return VERDICT_WORKING
    return VERDICT_UNSTABLE
