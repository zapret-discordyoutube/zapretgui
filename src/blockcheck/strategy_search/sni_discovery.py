"""Какие имена сайтов (SNI) фильтр пропускает к цели — для подставных пакетов подбора.

Стратегия с подставным пакетом (fake, hostfakesplit) называет в нём чужое имя:
фильтр видит его и пропускает соединение. В обычной сети годится любое имя,
кроме заблокированного. В сети с «белым списком имён» пропускают только
разрешённые — подставной пакет с ``www.google.com`` там бесполезен, и подбор
перебирал бы сотни стратегий впустую.

Порядок (по образцу «поиска белых SNI» из Runnin4ik/dpi-detector, MIT):

1. до запуска winws2 к адресу цели — настоящее имя, ``example.com`` и без имени;
2. постороннее имя или «без имени» проходит — фильтр режет только цель
   («чёрный» список), перебор не нужен: имена каталога и так годятся;
2а. не проходит ничего, а с окном приёма в 1 байт ответ сервера приходит — это не
   список имён: фильтр режет любой TLS с адреса по ответу сервера
   (``block_cause.CAUSE_REPLY_WINDOW``). Имена тут не помогут, перебор не нужен;
3. не проходит ничего — «белый» список: имена перебираются по одному, с
   шагом и паузами между пачками, до трёх найденных; найденное подтверждается
   повтором, а в конце ещё раз пробуется настоящее имя: если и оно вдруг
   прошло, фильтр непостоянен и находки не в счёт;
4. три подряд «нет соединения» — адрес придержали за частые запросы:
   перебор останавливается, ничего не утверждается.

Найденные имена записываются прямо в текст стратегии (``with_allowed_name``):
пресет — точка истины, при запуске ничего не подставляется.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import dataclass, replace

from blockcheck.strategy_search.ordering import Candidate

__all__ = [
    "CANDIDATE_NAMES",
    "FILTER_BLACK",
    "FILTER_REPLY_WINDOW",
    "FILTER_UNKNOWN",
    "FILTER_WHITE",
    "NEUTRAL_NAME",
    "NameSearch",
    "base_of",
    "discover",
    "variants",
    "with_allowed_name",
]

NEUTRAL_NAME = "example.com"
# Кого пробовать в белом списке: разрешённые чаще всего — госсервисы, банки,
# операторы, маркетплейсы. По одному имени на семейство: поддомены разрешённого
# домена проходят вместе с ним.
CANDIDATE_NAMES: tuple[str, ...] = (
    "max.ru",
    "ya.ru",
    "vk.com",
    "gosuslugi.ru",
    "sberbank.ru",
    "ozon.ru",
    "wildberries.ru",
    "avito.ru",
    "mail.ru",
    "2gis.ru",
    "rzd.ru",
    "mts.ru",
    "megafon.ru",
    "beeline.ru",
    "tbank.ru",
    "vtb.ru",
    "alfabank.ru",
    "rutube.ru",
    "dzen.ru",
    "kinopoisk.ru",
    "hh.ru",
    "mos.ru",
    "nalog.gov.ru",
    "sferum.ru",
)
MAX_TRIED = 24
WANTED = 3
BATCH = 4
# Паузы между пробами — случайные в этих пределах: ровный ритм «имя каждые 0,25 с» к одному
# адресу сам выдаёт перебор (``utils.probe_gate``). Поиск идёт только по сигналу беды —
# когда цель закрыта и без обхода, — поэтому лишние секунды здесь дешевле узора.
STEP_S = (0.4, 1.2)
BATCH_PAUSE_S = (1.5, 3.0)
CONFIRM_PAUSE_S = (0.8, 1.6)
FROZEN_AFTER = 3
# Сколько вариантов с найденным именем добавлять в подбор на одно имя.
VARIANTS_PER_NAME = 6

FILTER_BLACK = "black"
FILTER_WHITE = "white"
FILTER_UNKNOWN = "unknown"
# Любой TLS с адреса режут по ответу сервера, а с окном приёма в 1 байт ответ проходит.
FILTER_REPLY_WINDOW = "reply_window"

# Ответ пробы: True — шифрование началось (дорога открыта), False — соединение
# оборвали или заглушили, None — не удалось соединиться вовсе.
Hello = Callable[[str | None], bool | None]
# Проба малого окна к тому же адресу: True — начало ответа сервера пришло.
Window = Callable[[], bool | None]


@dataclass(frozen=True, slots=True)
class NameSearch:
    kind: str
    names: tuple[str, ...] = ()
    tried: int = 0
    # Адрес перестал отвечать на частые запросы — перебор остановлен.
    frozen: bool = False
    # Настоящее имя прошло в конце: фильтр непостоянен, находки отброшены.
    unstable: bool = False


def discover(
    hello: Hello,
    target_host: str,
    *,
    sleep: Callable[[float], None],
    cancelled: Callable[[], bool],
    names: tuple[str, ...] = CANDIDATE_NAMES,
    pick: Callable[[float, float], float] = random.uniform,
    window: Window | None = None,
) -> NameSearch:
    """Какие имена пропускает фильтр к адресу цели. Сеть — только через ``hello`` и ``window``.

    ``pick(от, до)`` — длина паузы (в тестах подменяется); ``window`` — проба малого окна."""
    if hello(target_host):
        # Настоящее имя прошло без обхода: сравнивать не с чем.
        return NameSearch(FILTER_UNKNOWN)
    neutral = hello(NEUTRAL_NAME)
    if neutral is None:
        return NameSearch(FILTER_UNKNOWN, frozen=True)
    if neutral or hello(None):
        return NameSearch(FILTER_BLACK)
    if window is not None and window():
        return NameSearch(FILTER_REPLY_WINDOW)

    found: list[str] = []
    tried = 0
    silent_in_row = 0
    for name in names[:MAX_TRIED]:
        if cancelled() or len(found) >= WANTED:
            break
        if tried:
            sleep(pick(*(BATCH_PAUSE_S if tried % BATCH == 0 else STEP_S)))
        tried += 1
        answer = hello(name)
        if answer is None:
            silent_in_row += 1
            if silent_in_row >= FROZEN_AFTER:
                return NameSearch(FILTER_WHITE, tuple(found), tried, frozen=True)
            continue
        silent_in_row = 0
        if answer:
            sleep(pick(*CONFIRM_PAUSE_S))
            if hello(name):
                found.append(name)
    if found and hello(target_host):
        return NameSearch(FILTER_WHITE, (), tried, unstable=True)
    return NameSearch(FILTER_WHITE, tuple(found), tried)


_SNI = re.compile(r"(tls_mod=[^:\s]*?)\bsni=[^,:\s]+")
_RNDSNI = re.compile(r"(tls_mod=[^:\s]*?)\brndsni\b")
_TLS_MOD = re.compile(r"(tls_mod=[^:\s]+)")
_HOST = re.compile(r"\b(host=)[^:\s]+")
_HOSTS = re.compile(r"\b(hosts=)[^:\s]+")
_SUFFIX = "@sni="


def with_allowed_name(candidate: Candidate, name: str) -> Candidate | None:
    """Та же стратегия, но подставной пакет называет ``name``. None — у стратегии нет имени в подделке."""
    args = candidate.args
    if _HOST.search(args) or _HOSTS.search(args):
        changed = _HOSTS.sub(lambda match: match.group(1) + name, _HOST.sub(lambda match: match.group(1) + name, args))
    elif _SNI.search(args):
        changed = _SNI.sub(lambda match: f"{match.group(1)}sni={name}", args)
    elif _RNDSNI.search(args):
        changed = _RNDSNI.sub(lambda match: f"{match.group(1)}sni={name}", args)
    elif _TLS_MOD.search(args):
        changed = _TLS_MOD.sub(lambda match: f"{match.group(1)},sni={name}", args)
    else:
        return None
    if changed == args:
        return None
    return replace(
        candidate,
        strategy_id=f"{candidate.strategy_id}{_SUFFIX}{name}",
        name=f"{candidate.name} · имя {name}",
        args=changed,
    )


def base_of(strategy_id: str) -> tuple[str, str]:
    """(id стратегии каталога, имя) для варианта ``id@sni=имя``; у обычной стратегии имя пустое."""
    base, _sep, name = str(strategy_id).partition(_SUFFIX)
    return base, name


def variants(ordered: list[Candidate], names: tuple[str, ...], *, per_name: int = VARIANTS_PER_NAME) -> list[Candidate]:
    """Варианты лучших по порядку стратегий с каждым найденным именем."""
    result: list[Candidate] = []
    for name in names:
        made = 0
        for candidate in ordered:
            if made >= per_name:
                break
            if base_of(candidate.strategy_id)[1]:
                continue
            variant = with_allowed_name(candidate, name)
            if variant is not None:
                result.append(variant)
                made += 1
    return result
