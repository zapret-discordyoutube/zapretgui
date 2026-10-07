"""Как работает фильтр: какое дробление приветствия он пропускает и режет ли он ECH.

Зачем. Мало знать, что сайт режут по имени, — от того, КАК фильтр читает
начало соединения, зависит, какая стратегия обхода сработает. Здесь это
выясняется напрямую, без подбора.

Дробление
---------
Имя сайта лежит в приветствии (ClientHello). Одно и то же приветствие
отправляется четырьмя способами:

1. **целиком** — одним пакетом, как обычно;
2. **разрез TCP** — двумя пакетами, граница посреди имени;
3. **две записи TLS** — одним пакетом, но приветствие разложено на две записи
   шифрования, граница тоже посреди имени;
4. **оба приёма** — две записи, каждая своим пакетом.

Если «целиком» режется, а способ 2 проходит — фильтр не склеивает пакеты, и
стратегии с дроблением сработают. Проходит только 4 — пакеты он склеивает, а
записи нет. Не проходит ничего — он собирает всё, дробление бесполезно, нужны
стратегии с подделками.

Контроль. Те же четыре способа с посторонним именем на том же адресе: если
сервер сам не принимает раздробленное приветствие, способ не оценивается.
Каждая проба с настоящим именем делается дважды, засчитывается только
совпавший исход.

ECH
---
ECH прячет имя сайта от фильтра; браузеры включают его для сайтов за
Cloudflare. Фильтр умеет резать такие соединения по внешнему имени
``cloudflare-ech.com``. Сравниваются три приветствия к одному адресу
Cloudflare: с ECH и этим внешним именем; с тем же именем без ECH; с ECH и
другим внешним именем. Режется только первое — режут именно ECH.

Здесь нет сети: отправка передаётся снаружи (``send``). Сбор фактов отделён от
выводов.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from diagnostics import browser_hello
from diagnostics.block_cause import (
    HELLO_ALERT,
    HELLO_CANCELLED,
    HELLO_OK,
    HELLO_RESET,
    HELLO_TIMEOUT,
)

__all__ = [
    "ECH_BLOCKED",
    "ECH_FINE",
    "ECH_UNKNOWN",
    "HABIT_ALL",
    "HABIT_NONE",
    "HABIT_NOT_BY_NAME",
    "HABIT_SOME",
    "HABIT_UNKNOWN",
    "WAY_BOTH",
    "WAY_RECORDS",
    "WAY_TCP",
    "WAY_WHOLE",
    "EchFacts",
    "SplitFacts",
    "check_ech",
    "check_split",
    "judge_ech",
    "judge_split",
    "lines",
    "summarize",
]

WAY_WHOLE = "whole"
WAY_TCP = "tcp"
WAY_RECORDS = "records"
WAY_BOTH = "both"
WAYS = (WAY_WHOLE, WAY_TCP, WAY_RECORDS, WAY_BOTH)
WAY_TITLES = {
    WAY_WHOLE: "Целиком",
    WAY_TCP: "Разрез TCP посреди имени",
    WAY_RECORDS: "Две записи TLS",
    WAY_BOTH: "Две записи двумя пакетами",
}

NEUTRAL_NAME = "example.com"
ECH_OUTER = "cloudflare-ech.com"
ECH_OTHER_OUTER = "example.com"
# Пауза между кусками: фильтр должен увидеть их отдельными пакетами.
PART_PAUSE_S = 0.04
REPEATS = 2

PASSED = "passed"
CUT = "cut"
# Два повтора кончились по-разному.
UNSTABLE = "unstable"
# Сервер сам не принял такое приветствие (видно по контролю) — способ не оценить.
NO_CONTROL = "no_control"

# Что получилось по сайту.
HABIT_NOT_BY_NAME = "not_by_name"  # целиком проходит: по имени здесь не режут
HABIT_SOME = "some"  # какое-то дробление проходит
HABIT_NONE = "none"  # не проходит ничего
HABIT_ALL = "all"  # проходит любое дробление
HABIT_UNKNOWN = "unknown"

ECH_BLOCKED = "blocked"
ECH_FINE = "fine"
ECH_UNKNOWN = "unknown"

Send = Callable[[str, tuple[bytes, ...], float], str]


def _parts(host: str, way: str) -> tuple[tuple[bytes, ...], float]:
    """Куски приветствия для способа ``way`` и пауза между ними."""
    record = browser_hello.build_hello(host, post_quantum=False)
    at = browser_hello.name_offset(record, host) + max(1, len(host) // 2)
    if way == WAY_TCP:
        return (record[:at], record[at:]), PART_PAUSE_S
    first, second = browser_hello.split_record(record, at)
    if way == WAY_RECORDS:
        return (first + second,), 0.0
    if way == WAY_BOTH:
        return (first, second), PART_PAUSE_S
    return (record,), 0.0


@dataclass(frozen=True, slots=True)
class SplitFacts:
    host: str
    ip: str
    # Способ → исходы повторов с настоящим именем (коды ``HELLO_*``).
    real: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # Способ → исход с посторонним именем.
    control: tuple[tuple[str, str], ...] = ()
    cancelled: bool = False


def check_split(host: str, ip: str, *, send: Send, submit: Callable) -> SplitFacts:
    """``send(адрес, куски, пауза)`` → код ответа. Способы идут одновременно, повторы одного способа — по очереди."""

    def one(way: str) -> tuple[tuple[str, ...], str]:
        control_parts, pause = _parts(NEUTRAL_NAME, way)
        control = send(ip, control_parts, pause)
        outcomes = []
        for _attempt in range(REPEATS):
            real_parts, pause = _parts(host, way)
            outcomes.append(send(ip, real_parts, pause))
        return tuple(outcomes), control

    futures = [(way, submit(one, way)) for way in WAYS]
    real, control = [], []
    for way, future in futures:
        outcomes, control_kind = future.result()
        real.append((way, outcomes))
        control.append((way, control_kind))
    cancelled = any(HELLO_CANCELLED in outcomes for _way, outcomes in real) or any(
        kind == HELLO_CANCELLED for _way, kind in control
    )
    return SplitFacts(host, ip, tuple(real), tuple(control), cancelled)


def _answered(kind: str) -> bool:
    # Отказ сервера — тоже ответ сервера: приветствие до него дошло.
    return kind in (HELLO_OK, HELLO_ALERT)


def _cut(kind: str) -> bool:
    return kind in (HELLO_RESET, HELLO_TIMEOUT)


@dataclass(frozen=True, slots=True)
class SplitVerdict:
    host: str
    ip: str
    code: str
    # Способ → ``PASSED`` / ``CUT`` / ``UNSTABLE`` / ``NO_CONTROL``.
    ways: tuple[tuple[str, str], ...]
    text: str
    # Что из этого следует для выбора стратегии. Пусто — сказать нечего.
    advice: str = ""


def _way_state(outcomes: tuple[str, ...], control: str) -> str:
    if not _answered(control):
        return NO_CONTROL
    if outcomes and all(_answered(kind) for kind in outcomes):
        return PASSED
    if outcomes and all(_cut(kind) for kind in outcomes):
        return CUT
    return UNSTABLE


def judge_split(facts: SplitFacts) -> SplitVerdict | None:
    """Вывод по одному сайту. None — проверку сняли."""
    if facts.cancelled:
        return None
    control = dict(facts.control)
    states = tuple((way, _way_state(outcomes, control.get(way, ""))) for way, outcomes in facts.real)
    by_way = dict(states)
    whole = by_way.get(WAY_WHOLE, UNSTABLE)
    if whole == PASSED:
        return SplitVerdict(
            facts.host,
            facts.ip,
            HABIT_NOT_BY_NAME,
            states,
            "приветствие целиком проходит — по имени сайта его здесь не режут (или уже работает обход)",
        )
    if whole != CUT:
        return SplitVerdict(
            facts.host, facts.ip, HABIT_UNKNOWN, states, "приветствие целиком режется через раз — сравнивать способы не с чем"
        )
    tried = [way for way in (WAY_TCP, WAY_RECORDS, WAY_BOTH) if by_way.get(way) in (PASSED, CUT)]
    passing = [way for way in tried if by_way[way] == PASSED]
    if not tried:
        return SplitVerdict(
            facts.host,
            facts.ip,
            HABIT_UNKNOWN,
            states,
            "сервер сам не принимает раздробленное приветствие или ответы нестабильны — оценить дробление не на чем",
        )
    if not passing:
        return SplitVerdict(
            facts.host,
            facts.ip,
            HABIT_NONE,
            states,
            "фильтр собирает приветствие обратно: ни разрез по пакетам, ни по записям шифрования не проходит",
            "Одно дробление здесь не поможет: выбирайте стратегии с подделками (fake) или со сменой порядка пакетов.",
        )
    if WAY_TCP in passing:
        return SplitVerdict(
            facts.host,
            facts.ip,
            HABIT_ALL if len(passing) == len(tried) else HABIT_SOME,
            states,
            "фильтр не склеивает пакеты: приветствие, разрезанное посреди имени, проходит",
            "Здесь должны работать самые простые стратегии — с дроблением пакета (split, multisplit, disorder).",
        )
    if WAY_RECORDS in passing:
        return SplitVerdict(
            facts.host,
            facts.ip,
            HABIT_SOME,
            states,
            "пакеты фильтр склеивает, а записи шифрования — нет: приветствие двумя записями проходит",
            "Ищите стратегии с дроблением записи TLS (в названии обычно tlsrec).",
        )
    return SplitVerdict(
        facts.host,
        facts.ip,
        HABIT_SOME,
        states,
        "по отдельности разрез пакета и разрез записи не проходят, а вместе — проходят",
        "Нужны стратегии, которые делают оба приёма сразу: дробление записи TLS вместе с дроблением пакета.",
    )


@dataclass(frozen=True, slots=True)
class EchFacts:
    ip: str
    # С ECH и внешним именем Cloudflare (повторы).
    with_ech: tuple[str, ...] = ()
    # То же имя без ECH.
    without_ech: str = ""
    # ECH с другим внешним именем.
    other_outer: str = ""


def check_ech(ip: str, *, send: Send) -> EchFacts:
    def hello(name: str, ech: bool) -> tuple[bytes, ...]:
        return (browser_hello.build_hello(name, post_quantum=False, ech=ech),)

    without = send(ip, hello(ECH_OUTER, False), 0.0)
    other = send(ip, hello(ECH_OTHER_OUTER, True), 0.0)
    with_ech = tuple(send(ip, hello(ECH_OUTER, True), 0.0) for _attempt in range(REPEATS))
    return EchFacts(ip, with_ech, without, other)


def judge_ech(facts: EchFacts | None) -> tuple[str, str]:
    """(код, фраза). Вывод «режут» — только если оба контроля на том же адресе проходят."""
    if facts is None or HELLO_CANCELLED in (*facts.with_ech, facts.without_ech, facts.other_outer):
        return ECH_UNKNOWN, ""
    if not _answered(facts.without_ech) or not _answered(facts.other_outer):
        return ECH_UNKNOWN, "проверить не удалось: сервер Cloudflare не ответил на контрольное приветствие"
    if facts.with_ech and all(_cut(kind) for kind in facts.with_ech):
        return (
            ECH_BLOCKED,
            "соединения с шифрованным именем сайта (ECH) к Cloudflare режутся, а такие же без ECH проходят. "
            "Сайты за Cloudflare могут не открываться в браузере, пока в нём включён ECH",
        )
    if facts.with_ech and all(_answered(kind) for kind in facts.with_ech):
        return ECH_FINE, "соединения с шифрованным именем сайта (ECH) к Cloudflare проходят"
    return ECH_UNKNOWN, "соединения с ECH проходят через раз — вывода нет"


ADVICE_ECH = (
    "Если сайты за Cloudflare не открываются: в Chrome — chrome://flags → Encrypted ClientHello → Disabled; "
    "в Firefox — about:config → network.dns.echconfig.enabled = false."
)


def summarize(splits, ech: tuple[str, str]) -> dict | None:
    """Раздел отчёта «Как работает фильтр». None — показывать нечего."""
    sites = [
        {
            "host": item.host,
            "address": item.ip,
            "code": item.code,
            "text": item.text,
            "advice": item.advice,
            "ways": [{"way": way, "title": WAY_TITLES[way], "state": state} for way, state in item.ways],
        }
        for item in splits
    ]
    ech_code, ech_text = ech
    if not sites and not ech_text:
        return None
    # Общий вывод — по сайтам, где сравнение состоялось; совпал у всех — говорим уверенно.
    decided = [item for item in splits if item.code in (HABIT_SOME, HABIT_ALL, HABIT_NONE)]
    texts = {item.text for item in decided}
    if not decided:
        headline = sites[0]["text"] if sites else ech_text
        advice = ""
    elif len(texts) == 1:
        headline = decided[0].text
        advice = decided[0].advice
    else:
        headline = "на разных сайтах фильтр ведёт себя по-разному — смотрите по каждому отдельно"
        advice = ""
    return {
        "headline": headline[:1].upper() + headline[1:],
        "advice": advice,
        "sites": sites,
        "ech": {"state": ech_code, "text": ech_text, "advice": ADVICE_ECH if ech_code == ECH_BLOCKED else ""},
    }


_STATE_WORDS = {PASSED: "проходит", CUT: "режется", UNSTABLE: "через раз", NO_CONTROL: "сервер такое не принимает"}
_STATE_ICONS = {PASSED: "✅", CUT: "❌", UNSTABLE: "❔", NO_CONTROL: "❔"}


def lines(report: dict) -> list[str]:
    """Раздел для текстового отчёта."""
    out = ["", "━━━━━━━━ Как работает фильтр ━━━━━━━━", f"ℹ️ {report.get('headline', '')}"]
    if report.get("advice"):
        out.append(f"   👉 {report['advice']}")
    for site in report.get("sites") or ():
        out.append(f"   {site.get('host', '')} ({site.get('address', '')}): {site.get('text', '')}")
        for way in site.get("ways") or ():
            out.append(f"      {_STATE_ICONS.get(way['state'], '❔')} {way['title']}: {_STATE_WORDS.get(way['state'], '')}")
    ech = report.get("ech") or {}
    if ech.get("text"):
        icon = {ECH_BLOCKED: "⚠️", ECH_FINE: "✅"}.get(ech.get("state"), "❔")
        out.append(f"{icon} ECH: {ech['text']}")
        if ech.get("advice"):
            out.append(f"   👉 {ech['advice']}")
    return out
