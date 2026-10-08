"""Заморозка от нескольких соединений сразу: сайт открывается одним соединением и висит в браузере.

Проверка открывает сайт одним соединением. Браузер открывает сразу шесть. По
описанию одного автора (другими измерениями не подтверждено) фильтр
«замораживает» сайт, когда видит больше трёх одновременных шифрованных
соединений с одним именем: соединение устанавливается, приветствие уходит, а
ответа нет — и так около двух минут. Тогда «открывается» у нас означает
«висит» у человека.

Как проверяется. На одном сайте, который открылся:

1. одно приветствие «как Chrome» — должно пройти, иначе сравнивать не с чем;
2. четыре таких приветствия одновременно;
3. сразу после — снова одно.

Если после пачки сайт замолчал, а до неё отвечал, остаются два вопроса, и на
оба есть проба: молчит только этот сайт или вся линия (контрольный сайт), и
вернётся ли он сам (опрос раз в несколько секунд, пока не ответит).

ВАЖНО. Проба сама может вызвать эту заморозку: сайт перестанет открываться
у человека на пару минут. Поэтому она идёт самой последней — когда всё
остальное уже проверено, — делается на одном сайте, не делается при
работающем обходе, и её итог прямо говорит, если остановку вызвала она.

Чего отсюда не узнать: действует ли заморозка на адрес, подсеть или имя;
каков настоящий порог; так ли фильтр реагирует на наш «почерк», как на
настоящий Chrome.

Здесь нет сети: отправка приветствия передаётся снаружи (``hello``). Сбор
(``collect``) отделён от вывода (``judge``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from diagnostics.block_cause import HELLO_ALERT, HELLO_CANCELLED, HELLO_OK, HELLO_RESET, HELLO_TIMEOUT

__all__ = [
    "CROWD",
    "CROWD_FREEZE",
    "CROWD_LIMIT",
    "CROWD_NONE",
    "CROWD_STUCK",
    "CROWD_UNKNOWN",
    "CrowdFacts",
    "CrowdVerdict",
    "collect",
    "judge",
]

# Сколько соединений открыть разом: на одно больше порога из описания.
CROWD = 4
# Как долго ждать, пока замолчавший сайт вернётся, и как часто спрашивать.
RECOVERY_WAIT_S = 150.0
RECOVERY_STEP_S = 10.0

CROWD_NONE = "none"  # пачка проходит: такой заморозки нет
CROWD_FREEZE = "freeze"  # после пачки сайт замолчал и сам вернулся
CROWD_STUCK = "stuck"  # замолчал и за время ожидания не вернулся
CROWD_LIMIT = "limit"  # часть пачки получила отказ: так ограничивает сам сайт
CROWD_UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CrowdFacts:
    host: str
    ip: str
    # Одно приветствие до пачки.
    before: str
    # Исходы пачки, по одному на соединение. Пусто — до пачки не дошли.
    crowd: tuple[str, ...] = ()
    # Одно приветствие сразу после пачки.
    after: str = ""
    # Контрольный сайт после пачки: жива ли линия. Пусто — не спрашивали.
    control: str = ""
    # Через сколько секунд сайт снова ответил. None — не ждали или не дождались.
    recovered_s: float | None = None
    # Сколько секунд ждали возвращения.
    waited_s: float = 0.0


@dataclass(frozen=True, slots=True)
class CrowdVerdict:
    code: str
    text: str
    advice: str = ""


def _answered(kind: str) -> bool:
    # Отказ сервера — тоже ответ: приветствие до него дошло.
    return kind in (HELLO_OK, HELLO_ALERT)


def collect(
    host: str,
    ip: str,
    *,
    hello: Callable[[str, str], str],
    together: Callable[[Sequence[Callable[[], str]]], Sequence[str]],
    control: Callable[[], str],
    wait: Callable[[float], bool],
    clock: Callable[[], float],
    max_wait: float = RECOVERY_WAIT_S,
) -> CrowdFacts:
    """Одно приветствие, пачка, снова одно; если сайт замолчал — контроль и ожидание.

    ``hello(адрес, имя)`` → код ответа; ``together(вызовы)`` выполняет их
    одновременно; ``control()`` — приветствие контрольному сайту;
    ``wait(секунды)`` — пауза, False — проверку сняли; ``clock()`` — время в секундах;
    ``max_wait`` — сколько можно ждать возвращения (у проверки есть общий срок).
    """
    before = hello(ip, host)
    if not _answered(before):
        return CrowdFacts(host, ip, before)
    crowd = tuple(together([lambda: hello(ip, host)] * CROWD))
    after = hello(ip, host)
    if _answered(after) or HELLO_CANCELLED in (*crowd, after):
        return CrowdFacts(host, ip, before, crowd, after)
    # Сайт отвечал и замолчал. Жива ли линия — и вернётся ли он сам?
    line = control()
    started = clock()
    recovered: float | None = None
    while _answered(line) and clock() - started < min(RECOVERY_WAIT_S, max_wait):
        if not wait(RECOVERY_STEP_S):
            break
        if _answered(hello(ip, host)):
            recovered = clock() - started
            break
    return CrowdFacts(host, ip, before, crowd, after, line, recovered, clock() - started)


ADVICE_FREEZE = (
    "Так сайт ведёт себя и в браузере: он открывает несколько соединений сразу и натыкается на ту же остановку. "
    "Подберите стратегию Zapret для этого сайта. Остановку сейчас вызвала сама проверка — сайт уже вернулся."
)
ADVICE_STUCK = (
    "Остановку, скорее всего, вызвала сама проверка. Подождите несколько минут, не обновляя страницу сайта, "
    "и он должен вернуться. Если такое повторяется в браузере — подберите стратегию Zapret для этого сайта."
)


def judge(facts: CrowdFacts | None) -> CrowdVerdict | None:
    """Вывод. None — проверку сняли или её не было."""
    if facts is None or HELLO_CANCELLED in (facts.before, facts.after, *facts.crowd):
        return None
    site = f"{facts.host} ({facts.ip})"
    if not _answered(facts.before):
        return CrowdVerdict(CROWD_UNKNOWN, f"{site}: одиночное соединение на этот раз не прошло — сравнивать не с чем")
    passed = sum(1 for kind in facts.crowd if _answered(kind))
    if _answered(facts.after):
        if passed == len(facts.crowd):
            return CrowdVerdict(
                CROWD_NONE,
                f"{site}: {len(facts.crowd)} соединения сразу проходят, и после них сайт отвечает — "
                "от числа соединений он не замирает",
            )
        refused = sum(1 for kind in facts.crowd if kind == HELLO_RESET)
        how = "получили сброс" if refused else "остались без ответа"
        return CrowdVerdict(
            CROWD_LIMIT,
            f"{site}: из {len(facts.crowd)} соединений сразу {len(facts.crowd) - passed} {how}, но сайт после этого "
            "отвечает — похоже на ограничение самого сайта, а не на заморозку",
        )
    if facts.after != HELLO_TIMEOUT:
        return CrowdVerdict(
            CROWD_UNKNOWN,
            f"{site}: после {len(facts.crowd)} соединений сразу сайт ответил сбросом — так отказывает сервер, заморозка выглядит иначе",
        )
    if not _answered(facts.control):
        return CrowdVerdict(
            CROWD_UNKNOWN,
            f"{site}: после пачки соединений сайт замолчал, но не отвечает и контрольный сайт — похоже, пропала сама связь",
        )
    if facts.recovered_s is not None:
        return CrowdVerdict(
            CROWD_FREEZE,
            f"{site}: одно соединение проходит, а после {len(facts.crowd)} сразу сайт замолчал и снова ответил через "
            f"{facts.recovered_s:.0f} с — его замораживают, когда соединений несколько",
            ADVICE_FREEZE,
        )
    return CrowdVerdict(
        CROWD_STUCK,
        f"{site}: одно соединение проходило, а после {len(facts.crowd)} сразу сайт замолчал и за {facts.waited_s:.0f} с "
        "не вернулся; остальной интернет при этом работает",
        ADVICE_STUCK,
    )
