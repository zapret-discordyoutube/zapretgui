"""Путь запроса одним списком: шаги от DNS до передачи данных, у каждого — уровень, слова и уверенность.

Проверка сайта собирает много отдельных фактов: ответ DNS, соединение, шифрование,
причину сбоя, QUIC, объём и скорость ответов. Каждый из них показан своей строкой, но
вопрос человека один: **на каком шаге пути ломается и насколько мы в этом уверены**.
Разбор сбоя YouTube у абонентов ТТК (октябрь 2026) показал, зачем нужен именно путь:
соединение и шифрование проходили, ломалась только передача, а зелёные первые шаги
выглядели как «всё хорошо».

Здесь новых проверок нет. Список строится по уже собранным фактам одного адреса
(``diagnostics.run_context.Probe``), чистой функцией без сети, поэтому вывод проверяется
тестами. Шаги идут всегда в одном порядке:

1. DNS — честен ли ответ справочной;
2. TCP — устанавливается ли соединение;
3. шифрование (TLS) — проходит ли рукопожатие и не рвут ли его по имени сайта;
4. QUIC — открывается ли быстрый протокол поверх UDP;
5. передача — идут ли данные дальше первых килобайт: обрыв на объёме, паузы и скорость.

У шага три исхода (``ok``, ``warn``, ``fail``) и ``unknown`` — «не проверялся или проверить
не удалось». Уверенность (``low`` / ``medium`` / ``high``) говорит, сколько проверок
подтверждает вывод: один замер и повторённый тремя вывод — не одно и то же.
"""

from __future__ import annotations

from dataclasses import dataclass

from diagnostics import cut_window, quic_probe, transfer_probe, volume_probe
from diagnostics.levels import FAIL, OK, UNKNOWN, WARN
from diagnostics.limits import DNS_ATTEMPTS
from diagnostics.tls_probe import KIND_CANCELLED, KIND_CONNECT
from diagnostics.transfer_probe import CONFIDENCE_HIGH, CONFIDENCE_LOW, CONFIDENCE_MEDIUM
from diagnostics.verdict import DnsState, ReachState, describe_reach

__all__ = [
    "STEP_DATA",
    "STEP_DNS",
    "STEP_QUIC",
    "STEP_TCP",
    "STEP_TLS",
    "STEPS",
    "PathStep",
    "breaking_step",
    "build",
    "chain_text",
]

STEP_DNS = "dns"
STEP_TCP = "tcp"
STEP_TLS = "tls"
STEP_QUIC = "quic"
STEP_DATA = "data"

# Порядок и названия шагов — одни на всю программу.
STEPS: dict[str, str] = {
    STEP_DNS: "DNS",
    STEP_TCP: "Соединение (TCP)",
    STEP_TLS: "Шифрование (TLS)",
    STEP_QUIC: "QUIC",
    STEP_DATA: "Передача данных",
}

# Значки цепочки для текстового отчёта.
_ICONS = {OK: "✅", WARN: "⚠️", FAIL: "❌", UNKNOWN: "❔"}


@dataclass(frozen=True, slots=True)
class PathStep:
    key: str
    level: str
    text: str
    # Сколько проверок подтверждает вывод: пусто — вывода нет (шаг не проверялся).
    confidence: str = ""

    @property
    def title(self) -> str:
        return STEPS.get(self.key, self.key)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "level": self.level,
            "text": self.text,
            "confidence": self.confidence,
        }


def _dns_step(probe) -> PathStep:
    judgement = probe.judgement
    if judgement is None:
        return PathStep(STEP_DNS, UNKNOWN, "ответ DNS не сверялся")
    # Три запроса и эталон по шифрованному каналу — сильная опора; без эталона вывод слабее.
    confidence = CONFIDENCE_HIGH if probe.reference_ok and DNS_ATTEMPTS >= 3 else CONFIDENCE_MEDIUM if probe.reference_ok else CONFIDENCE_LOW
    if judgement.state == DnsState.SPOOFED:
        return PathStep(STEP_DNS, WARN, judgement.reason or "ответ DNS подменён", confidence)
    if judgement.state == DnsState.UNKNOWN:
        return PathStep(STEP_DNS, UNKNOWN, judgement.reason or "сверить ответ DNS не удалось")
    return PathStep(STEP_DNS, OK, judgement.reason or "ответ DNS совпал с эталоном", confidence)


def _tcp_step(probe) -> PathStep:
    result = probe.reach
    if result is None:
        return PathStep(STEP_TCP, UNKNOWN, "адрес сервера не найден")
    if result.kind == KIND_CANCELLED:
        return PathStep(STEP_TCP, UNKNOWN, "проверку прервали")
    # Повторы на разных адресах подтверждают вывод: один сбой на одном адресе — слабая опора.
    confidence = CONFIDENCE_HIGH if len(probe.tried) >= 2 else CONFIDENCE_MEDIUM
    if result.kind == KIND_CONNECT:
        text = "соединиться с сервером не удалось"
        if len(probe.tried) >= 2:
            text += f" ни по одному из {len(probe.tried)} адресов"
        return PathStep(STEP_TCP, FAIL, text, confidence)
    return PathStep(STEP_TCP, OK, f"соединение с {result.ip} установлено", confidence)


def _tls_step(probe) -> PathStep:
    result = probe.reach
    state = probe.reach_state
    if result is None or state == ReachState.NO_ADDRESS or result.kind in (KIND_CONNECT, KIND_CANCELLED):
        return PathStep(STEP_TLS, UNKNOWN, "до шифрования не дошли")
    if probe.fingerprint:
        return PathStep(
            STEP_TLS, FAIL,
            "соединение «как Chrome» рвут, а простое приветствие к тому же серверу проходит: фильтр узнаёт браузер по почерку",
            CONFIDENCE_HIGH,
        )
    if state in (ReachState.OK, ReachState.FREEZE):
        return PathStep(STEP_TLS, OK, "шифрование установлено", CONFIDENCE_MEDIUM if probe.attempts <= 1 else CONFIDENCE_HIGH)
    if state == ReachState.CERT:
        return PathStep(STEP_TLS, FAIL, describe_reach(result), CONFIDENCE_HIGH)
    if state == ReachState.DPI:
        cause = probe.cause
        text = describe_reach(result)
        if cause is not None and cause.text:
            text = f"{text}. {cause.text[:1].upper()}{cause.text[1:]}"
        # Уверенный вывод о причине опирается на прямое противоречие, а не на молчание.
        confidence = CONFIDENCE_HIGH if cause is not None and cause.confident else CONFIDENCE_MEDIUM
        return PathStep(STEP_TLS, FAIL, text, confidence)
    return PathStep(STEP_TLS, UNKNOWN, describe_reach(result))


def _quic_step(probe) -> PathStep:
    quic = probe.quic
    if quic is None:
        return PathStep(STEP_QUIC, UNKNOWN, "QUIC не проверялся")
    if quic.code == quic_probe.QUIC_OK:
        return PathStep(STEP_QUIC, OK, quic.text, CONFIDENCE_MEDIUM)
    if quic.code == quic_probe.QUIC_BLOCKED_BY_NAME:
        return PathStep(STEP_QUIC, WARN, quic.text, CONFIDENCE_MEDIUM)
    # Молчание UDP не отличить от сервера без QUIC: это не поломка и не вывод.
    return PathStep(STEP_QUIC, UNKNOWN, quic.text)


def _data_step(probe) -> PathStep:
    volume, transfer = probe.volume, probe.transfer
    if volume is not None and volume.code == volume_probe.VOLUME_CUT:
        return PathStep(STEP_DATA, FAIL, volume.text, CONFIDENCE_HIGH)
    if probe.reach_state == ReachState.FREEZE:
        return PathStep(STEP_DATA, FAIL, describe_reach(probe.reach), CONFIDENCE_MEDIUM)
    if transfer is not None and transfer.code != transfer_probe.TRANSFER_UNKNOWN:
        return PathStep(STEP_DATA, transfer.level, transfer.text, transfer.confidence)
    if volume is not None and volume.code == volume_probe.VOLUME_OK:
        return PathStep(STEP_DATA, OK, volume.text, CONFIDENCE_LOW)
    if volume is not None:
        return PathStep(STEP_DATA, UNKNOWN, volume.text)
    result = probe.reach
    if cut_window.whole_past_window(result):
        # Отдельная проба объёма не нужна: сама страница больше окна обрыва и пришла целиком.
        return PathStep(STEP_DATA, OK, f"страница в {result.body_size // 1024} КБ пришла целиком — обрыва после 16 КБ нет", CONFIDENCE_LOW)
    return PathStep(STEP_DATA, UNKNOWN, "объём и скорость ответов не проверялись")


def build(probe) -> tuple[PathStep, ...]:
    """Путь запроса по фактам одного адреса: пять шагов, всегда в одном порядке."""
    return (_dns_step(probe), _tcp_step(probe), _tls_step(probe), _quic_step(probe), _data_step(probe))


def breaking_step(steps: tuple[PathStep, ...] | list[PathStep]) -> PathStep | None:
    """Первый шаг, на котором путь ломается или шатается: сначала явный сбой, затем оговорка.

    Шаги без вывода (``unknown``) не считаются: «не проверялось» — не поломка. ``None`` — путь чист.
    """
    for level in (FAIL, WARN):
        found = next((step for step in steps if step.level == level), None)
        if found is not None:
            return found
    return None


def chain_text(steps: tuple[PathStep, ...] | list[PathStep]) -> str:
    """Путь в одну строку для текстового отчёта: «DNS ✅ → TCP ✅ → … → Передача ⚠️»."""
    short = {STEP_DNS: "DNS", STEP_TCP: "TCP", STEP_TLS: "TLS", STEP_QUIC: "QUIC", STEP_DATA: "передача"}
    return " → ".join(f"{short.get(step.key, step.key)} {_ICONS.get(step.level, '❔')}" for step in steps)

