"""Как работает фильтр: что он делает с началом соединения и режет ли он ECH.

Зачем. Мало знать, что сайт режут по имени, — от того, КАК фильтр читает
начало соединения, зависит, какая стратегия обхода сработает. Здесь это
выясняется напрямую, без подбора: одно и то же приветствие (ClientHello) с
именем сайта отправляется по-разному, и видно, какие способы фильтр пропускает.

Способы
-------
- **целиком** — одним пакетом, как обычно; это точка отсчёта;
- **разрезы пакета (TCP)** — после первого байта, перед именем, посреди имени,
  тремя кусками. Куски уходят подряд, без паузы — так же режет сам Zapret;
- **разрез посреди имени с паузой в секунду** — отличает фильтр, который
  склеивает только пришедшее подряд, от того, который ждёт остаток долго;
- **записи TLS** — приветствие двумя записями шифрования: граница перед
  именем, посреди имени, и то же двумя пакетами.

Как делается вывод
------------------
Каждый способ — это сравнение на одном адресе: то же самое дробление, но с
безобидным именем (контроль), и с именем сайта. Способ оценивается, только
если контроль прошёл: иначе неясно, фильтр это или сервер не терпит такое
дробление. Единичный сбой перепроверяется: контроль — второй раз, имя сайта —
третьей попыткой, когда первые две разошлись. Все исходы сохраняются как
есть (сброс, молчание, чужой ответ, нет соединения) и показываются человеку.

Способы идут по очереди и с паузой: пачка соединений к одному адресу сама
бывает поводом для фильтра придержать их все. В конце контроль повторяется —
если он перестал проходить, адрес попал под временный запрет, и вывода нет.

Чего здесь не видно. Поддельные пакеты, перестановку пакетов и наложение
кусков обычным соединением не сделать — эти приёмы Zapret проверка не
испытывает и честно говорит об этом.

Если на компьютере работает Zapret или VPN, пробы идут через них: результат
описывает сеть вместе с обходом, а не фильтр провайдера. Он показывается с
этой пометкой и без советов по стратегиям.

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

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from diagnostics import browser_hello
from diagnostics.block_cause import (
    HELLO_ALERT,
    HELLO_CANCELLED,
    HELLO_CONNECT,
    HELLO_GARBAGE,
    HELLO_OK,
    HELLO_RESET,
    HELLO_TIMEOUT,
)

__all__ = [
    "ECH_BLOCKED",
    "ECH_FINE",
    "ECH_UNKNOWN",
    "HABIT_NONE",
    "HABIT_NOT_BY_NAME",
    "HABIT_RECORDS",
    "HABIT_TCP",
    "HABIT_TIMER",
    "HABIT_UNKNOWN",
    "WAYS",
    "EchFacts",
    "SplitFacts",
    "WayFacts",
    "check_ech",
    "check_split",
    "judge_ech",
    "judge_split",
    "lines",
    "summarize",
]

WAY_WHOLE = "whole"
WAY_TCP_FIRST = "tcp_first"
WAY_TCP_BEFORE = "tcp_before"
WAY_TCP_NAME = "tcp_name"
WAY_TCP_SLOW = "tcp_slow"
WAY_TCP_THREE = "tcp_three"
WAY_REC_BEFORE = "rec_before"
WAY_REC_NAME = "rec_name"
WAY_REC_TCP = "rec_tcp"
WAYS = (
    WAY_WHOLE,
    WAY_TCP_FIRST,
    WAY_TCP_BEFORE,
    WAY_TCP_NAME,
    WAY_TCP_SLOW,
    WAY_TCP_THREE,
    WAY_REC_BEFORE,
    WAY_REC_NAME,
    WAY_REC_TCP,
)
WAY_TITLES = {
    WAY_WHOLE: "Целиком, одним пакетом",
    WAY_TCP_FIRST: "Разрез пакета после первого байта",
    WAY_TCP_BEFORE: "Разрез пакета перед именем сайта",
    WAY_TCP_NAME: "Разрез пакета посреди имени",
    WAY_TCP_SLOW: "Разрез посреди имени с паузой в секунду",
    WAY_TCP_THREE: "Три куска: первый байт и разрез посреди имени",
    WAY_REC_BEFORE: "Две записи TLS, граница перед именем",
    WAY_REC_NAME: "Две записи TLS, граница посреди имени",
    WAY_REC_TCP: "Две записи TLS двумя пакетами",
}
# Разрезы пакета, которые делает и сам Zapret (куски подряд), и разрезы по записям шифрования.
_TCP_WAYS = (WAY_TCP_FIRST, WAY_TCP_BEFORE, WAY_TCP_NAME, WAY_TCP_THREE)
_RECORD_WAYS = (WAY_REC_BEFORE, WAY_REC_NAME, WAY_REC_TCP)

NEUTRAL_NAME = "example.com"
ECH_OUTER = "cloudflare-ech.com"
ECH_OTHER_OUTER = "example.com"
# Пауза «с паузой»: заметно дольше, чем фильтр обычно ждёт остаток пакета.
SLOW_PAUSE_S = 1.0
# Пауза между соединениями к одному адресу.
BETWEEN_S = 0.3
REPEATS = 2

PASSED = "passed"
CUT = "cut"
# Попытки кончились по-разному, и третья не дала перевеса.
UNSTABLE = "unstable"
# С безобидным именем такое дробление тоже не проходит — способ не оценить.
NO_CONTROL = "no_control"
# До способа не дошли: вывод уже ясен или проверку сняли.
NOT_RUN = "not_run"

HABIT_NOT_BY_NAME = "not_by_name"  # целиком проходит: по имени здесь не режут
HABIT_TCP = "tcp"  # не склеивает пакеты
HABIT_TIMER = "timer"  # склеивает только пришедшее подряд
HABIT_RECORDS = "records"  # пакеты склеивает, записи шифрования — нет
HABIT_NONE = "none"  # собирает всё
HABIT_UNKNOWN = "unknown"

ECH_BLOCKED = "blocked"
ECH_FINE = "fine"
ECH_UNKNOWN = "unknown"

Send = Callable[[str, tuple[bytes, ...], float], str]


def _parts(host: str, way: str) -> tuple[tuple[bytes, ...], float]:
    """Куски приветствия для способа ``way`` и пауза между ними."""
    record = browser_hello.build_hello(host, post_quantum=False)
    name_at = browser_hello.name_offset(record, host)
    middle = name_at + max(1, len(host) // 2)
    if way == WAY_TCP_FIRST:
        return (record[:1], record[1:]), 0.0
    if way == WAY_TCP_BEFORE:
        return (record[:name_at], record[name_at:]), 0.0
    if way == WAY_TCP_NAME:
        return (record[:middle], record[middle:]), 0.0
    if way == WAY_TCP_SLOW:
        return (record[:middle], record[middle:]), SLOW_PAUSE_S
    if way == WAY_TCP_THREE:
        return (record[:1], record[1:middle], record[middle:]), 0.0
    if way == WAY_REC_BEFORE:
        return (b"".join(browser_hello.split_record(record, name_at)),), 0.0
    if way == WAY_REC_NAME:
        return (b"".join(browser_hello.split_record(record, middle)),), 0.0
    if way == WAY_REC_TCP:
        return browser_hello.split_record(record, middle), 0.0
    return (record,), 0.0


@dataclass(frozen=True, slots=True)
class WayFacts:
    way: str
    # Исходы с безобидным именем и с именем сайта (коды ``HELLO_*``), по порядку попыток.
    control: tuple[str, ...] = ()
    real: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SplitFacts:
    host: str
    ip: str
    ways: tuple[WayFacts, ...] = ()
    # Контроль «целиком с безобидным именем» ещё раз, в самом конце. Пусто — не делали.
    final_control: str = ""
    cancelled: bool = False


def _answered(kind: str) -> bool:
    # Отказ сервера — тоже ответ сервера: приветствие до него дошло.
    return kind in (HELLO_OK, HELLO_ALERT)


def _cut(kind: str) -> bool:
    # Чужой ответ вместо шифрования — тоже работа фильтра: сервер так не отвечает.
    return kind in (HELLO_RESET, HELLO_TIMEOUT, HELLO_GARBAGE)


def _settled(outcomes: Iterable[str]) -> str:
    """``PASSED`` / ``CUT`` по большинству попыток; пусто — перевеса нет."""
    kinds = list(outcomes)
    passed = sum(1 for kind in kinds if _answered(kind))
    cut = sum(1 for kind in kinds if _cut(kind))
    if passed >= 2 and passed > cut:
        return PASSED
    if cut >= 2 and cut > passed:
        return CUT
    return ""


def check_split(
    host: str, ip: str, *, send: Send, pause: Callable[[float], None] = lambda _seconds: None
) -> SplitFacts:
    """Все способы по очереди. ``send(адрес, куски, пауза)`` → код ответа; ``pause(секунды)`` — передышка между соединениями.

    Если приветствие целиком проходит или его не с чем сравнить, остальные
    способы не делаются: вывода из них всё равно не будет.
    """
    done: list[WayFacts] = []

    def ask(name: str, way: str) -> str:
        parts, gap = _parts(name, way)
        kind = send(ip, parts, gap)
        pause(BETWEEN_S)
        return kind

    def finish(final: str = "", cancelled: bool = False) -> SplitFacts:
        return SplitFacts(host, ip, tuple(done), final, cancelled)

    for way in WAYS:
        control = [ask(NEUTRAL_NAME, way)]
        if not _answered(control[0]) and control[0] != HELLO_CANCELLED:
            # Один несостоявшийся контроль — ещё не «сервер не терпит дробление».
            control.append(ask(NEUTRAL_NAME, way))
        if HELLO_CANCELLED in control:
            return finish(cancelled=True)
        real: list[str] = []
        if any(_answered(kind) for kind in control):
            real = [ask(host, way) for _attempt in range(REPEATS)]
            if not _settled(real) and HELLO_CANCELLED not in real:
                real.append(ask(host, way))
        done.append(WayFacts(way, tuple(control), tuple(real)))
        if HELLO_CANCELLED in real:
            return finish(cancelled=True)
        if way == WAY_WHOLE and _settled(real) != CUT:
            return finish()
    final = ask(NEUTRAL_NAME, WAY_WHOLE)
    return finish(final, cancelled=final == HELLO_CANCELLED)


_OUTCOME_WORDS = {
    HELLO_OK: "прошло",
    HELLO_ALERT: "дошло до сервера",
    HELLO_RESET: "сброс",
    HELLO_TIMEOUT: "молчание",
    HELLO_GARBAGE: "чужой ответ",
    HELLO_CONNECT: "нет соединения",
}


def outcomes_text(outcomes: Iterable[str]) -> str:
    """Исходы попыток словами: «сброс ×2», «прошло, сброс, сброс». Пусто — попыток не было."""
    counted = Counter(_OUTCOME_WORDS.get(kind, "ошибка") for kind in outcomes)
    return ", ".join(word if count == 1 else f"{word} ×{count}" for word, count in counted.items())


def _way_state(facts: WayFacts) -> str:
    if not facts.control:
        return NOT_RUN
    if not any(_answered(kind) for kind in facts.control):
        return NO_CONTROL
    return _settled(facts.real) or UNSTABLE


_WAY_MEANING = {
    PASSED: "фильтр такое приветствие пропускает",
    CUT: "фильтр его режет",
    UNSTABLE: "попытки кончились по-разному — вывода нет",
    NO_CONTROL: "так не проходит и безобидное имя — фильтр это или сервер, не различить",
    NOT_RUN: "не проверялось",
}


@dataclass(frozen=True, slots=True)
class WayVerdict:
    way: str
    state: str
    # Что получилось с именем сайта и с безобидным именем — словами.
    real: str
    control: str
    meaning: str


@dataclass(frozen=True, slots=True)
class SplitVerdict:
    host: str
    ip: str
    code: str
    ways: tuple[WayVerdict, ...]
    text: str
    # Что из этого следует для выбора стратегии. Пусто — сказать нечего.
    advice: str = ""


ADVICE_TCP = (
    "Должны работать самые простые стратегии — с дроблением пакета (multisplit, multidisorder). "
    "Место разреза берите из строк ниже, где способ проходит."
)
ADVICE_TIMER = (
    "Простое дробление вряд ли поможет: Zapret шлёт куски подряд, а подряд фильтр их склеивает. "
    "Пробуйте стратегии со сменой порядка кусков (multidisorder) и с подделками (fake)."
)
ADVICE_RECORDS = (
    "Дробление пакета не поможет, а дробление записи TLS — должно: ищите стратегии с tlsrec в названии."
)
ADVICE_NONE = (
    "Одно дробление здесь не поможет: фильтр собирает приветствие целиком. Пробуйте стратегии с подделками "
    "(fake, fakedsplit, hostfakesplit) и с наложением кусков (seqovl) — их эта проверка испытать не может."
)
_POSITION_HINTS = {
    WAY_TCP_FIRST: "после первого байта (pos=1)",
    WAY_TCP_BEFORE: "перед именем сайта (pos=host)",
    WAY_TCP_NAME: "посреди имени (pos=midsld)",
    WAY_TCP_THREE: "тремя кусками (pos=1,midsld)",
}


def judge_split(facts: SplitFacts) -> SplitVerdict | None:
    """Вывод по одному сайту. None — проверку сняли."""
    if facts.cancelled:
        return None
    found = {item.way: item for item in facts.ways}
    states = {way: _way_state(found[way]) if way in found else NOT_RUN for way in WAYS}
    ways = tuple(
        WayVerdict(
            way,
            states[way],
            outcomes_text(found[way].real) if way in found else "",
            outcomes_text(found[way].control) if way in found else "",
            _WAY_MEANING[states[way]],
        )
        for way in WAYS
    )

    def verdict(code: str, text: str, advice: str = "") -> SplitVerdict:
        return SplitVerdict(facts.host, facts.ip, code, ways, text, advice)

    whole = states[WAY_WHOLE]
    if whole == PASSED:
        return verdict(HABIT_NOT_BY_NAME, "приветствие целиком проходит — по имени сайта его здесь не режут")
    if whole == NO_CONTROL:
        return verdict(
            HABIT_UNKNOWN,
            "на этом адресе не проходит даже безобидное имя — сравнивать не с чем, вывода о фильтре нет",
        )
    if whole != CUT:
        return verdict(HABIT_UNKNOWN, "приветствие целиком режется не каждый раз — сравнивать способы не с чем")
    if facts.final_control and not _answered(facts.final_control):
        return verdict(
            HABIT_UNKNOWN,
            "к концу проверки на этом адресе перестало проходить и безобидное имя: похоже, фильтр на время "
            "закрыл адрес целиком — результатам по способам верить нельзя",
        )
    passing_tcp = [way for way in _TCP_WAYS if states[way] == PASSED]
    if passing_tcp:
        places = ", ".join(_POSITION_HINTS[way] for way in passing_tcp)
        return verdict(
            HABIT_TCP,
            f"фильтр не склеивает пакеты: приветствие, разрезанное {places}, проходит",
            ADVICE_TCP,
        )
    if states[WAY_TCP_NAME] == CUT and states[WAY_TCP_SLOW] == PASSED:
        return verdict(
            HABIT_TIMER,
            "фильтр склеивает куски, пришедшие подряд, но остаток долго не ждёт: с паузой в секунду разрез проходит",
            ADVICE_TIMER,
        )
    if any(states[way] == PASSED for way in _RECORD_WAYS):
        return verdict(
            HABIT_RECORDS,
            "пакеты фильтр склеивает, а записи шифрования — нет: приветствие двумя записями TLS проходит",
            ADVICE_RECORDS,
        )
    # «Собирает всё» — только если разрез пакета посреди имени действительно проверен.
    if states[WAY_TCP_NAME] == CUT and all(states[way] in (CUT, NO_CONTROL) for way in (*_TCP_WAYS, *_RECORD_WAYS)):
        return verdict(
            HABIT_NONE,
            "фильтр собирает приветствие обратно: ни один разрез пакета и записи шифрования не проходит",
            ADVICE_NONE,
        )
    return verdict(
        HABIT_UNKNOWN,
        "по способам дробления ответы разошлись или их не с чем сравнить — вывода о фильтре нет",
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


LIMITS = (
    "Проверяется только дробление приветствия. Поддельные пакеты, смену порядка кусков и их наложение "
    "обычным соединением не сделать — эти приёмы Zapret здесь не испытываются."
)
_DECIDED = (HABIT_TCP, HABIT_TIMER, HABIT_RECORDS, HABIT_NONE)


def summarize(splits, ech: tuple[str, str], *, tools: Iterable[str] = ()) -> dict | None:
    """Раздел отчёта «Как работает фильтр». None — показывать нечего.

    ``tools`` — запущенные Zapret, VPN и другие программы обхода: при них пробы
    идут через них, и вывод описывает сеть вместе с обходом, а не фильтр.
    """
    tools = [str(name) for name in tools]
    sites = [
        {
            "host": item.host,
            "address": item.ip,
            "code": item.code,
            "text": item.text,
            # При работающем обходе совет по стратегиям был бы выведен из искажённых проб.
            "advice": "" if tools else item.advice,
            "ways": [
                {
                    "way": way.way,
                    "title": WAY_TITLES[way.way],
                    "state": way.state,
                    "real": way.real,
                    "control": way.control,
                    "meaning": way.meaning,
                }
                for way in item.ways
            ],
        }
        for item in splits
    ]
    ech_code, ech_text = ech
    if not sites and not ech_text:
        return None
    # Общий вывод — только по сайтам, где сравнение состоялось, и только если он у них один.
    decided = [item for item in splits if item.code in _DECIDED]
    codes = {item.code for item in decided}
    advice = ""
    if tools:
        headline = (
            f"проверка шла вместе с {', '.join(tools)}: пробные соединения проходят через них, поэтому ниже — "
            "поведение сети вместе с обходом, а не фильтра провайдера"
        )
        advice = "Остановите обход и VPN и повторите проверку — тогда будет видно, как работает сам фильтр."
    elif not decided:
        headline = sites[0]["text"] if len(sites) == 1 else (
            "ни по одному сайту сравнение не состоялось — вывода о фильтре нет" if sites else ech_text
        )
    elif len(codes) == 1:
        headline = decided[0].text
        advice = decided[0].advice
    else:
        headline = "на разных сайтах фильтр ведёт себя по-разному — смотрите по каждому отдельно"
    return {
        "headline": headline[:1].upper() + headline[1:],
        # Общий вывод кодом — для подбора стратегий; пусто, когда вывода нет или пробы шли через обход.
        "code": decided[0].code if (not tools and len(codes) == 1) else "",
        "advice": advice,
        "disturbed": tools,
        "limits": LIMITS,
        "sites": sites,
        "ech": {"state": ech_code, "text": ech_text, "advice": ADVICE_ECH if ech_code == ECH_BLOCKED else ""},
    }


_STATE_WORDS = {PASSED: "проходит", CUT: "режется", UNSTABLE: "через раз", NO_CONTROL: "не оценить", NOT_RUN: "не проверялось"}
_STATE_ICONS = {PASSED: "✅", CUT: "❌"}


def way_text(way: dict) -> str:
    """Строка способа: что вышло с именем сайта, что с безобидным и что это значит."""
    if way.get("state") == NOT_RUN:
        return "не проверялось"
    parts = []
    if way.get("real"):
        parts.append(f"с именем сайта: {way['real']}")
    if way.get("control"):
        parts.append(f"с безобидным именем: {way['control']}")
    return " · ".join(parts) + (f" — {way['meaning']}" if way.get("meaning") else "")


def lines(report: dict) -> list[str]:
    """Раздел для текстового отчёта."""
    out = ["", "━━━━━━━━ Как работает фильтр ━━━━━━━━"]
    out.append(f"{'⚠️' if report.get('disturbed') else 'ℹ️'} {report.get('headline', '')}")
    if report.get("advice"):
        out.append(f"   👉 {report['advice']}")
    for site in report.get("sites") or ():
        out.append(f"   {site.get('host', '')} ({site.get('address', '')}): {site.get('text', '')}")
        if site.get("advice"):
            out.append(f"      👉 {site['advice']}")
        for way in site.get("ways") or ():
            if way.get("state") == NOT_RUN:
                continue
            icon = _STATE_ICONS.get(way["state"], "❔")
            out.append(f"      {icon} {way['title']} — {_STATE_WORDS.get(way['state'], '')}: {way_text(way)}")
    if report.get("sites") and report.get("limits"):
        out.append(f"   ℹ️ {report['limits']}")
    ech = report.get("ech") or {}
    if ech.get("text"):
        icon = {ECH_BLOCKED: "⚠️", ECH_FINE: "✅"}.get(ech.get("state"), "❔")
        out.append(f"{icon} ECH: {ech['text']}")
        if ech.get("advice"):
            out.append(f"   👉 {ech['advice']}")
    return out
