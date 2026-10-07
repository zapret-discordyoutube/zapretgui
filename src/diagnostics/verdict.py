"""Выводы диагностики: открывается ли сервис и честен ли DNS.

Здесь нет сети — только правила. Движок (``diagnostics.engine``) собирает
факты, а этот модуль решает, что они значат.

Два вопроса разделены:

* **Открывается ли сервис.** Движок подключается к правильному адресу
  (из hosts, из DNS системы, если он подлинный, или из эталона
  DNS-over-HTTPS) так же, как браузер. Итог по сервису строится только
  отсюда: если сайт открывается, красного «❌» быть не должно.
* **Честен ли DNS.** Адрес, не похожий на эталон, сам по себе не подмена:
  Anycast и CDN отдают разным резолверам разные адреса, а DNS для обхода
  блокировок (Comss, Xbox DNS и т. п.) специально возвращает адрес своего
  прокси. Решает сертификат по адресу из DNS: настоящий сервер (или честный
  прокси) предъявит подлинный сертификат сайта, заглушка провайдера — нет.
  Подмена DNS — отдельное предупреждение: браузер с защищённым DNS её
  не замечает, а программы Windows — да.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from diagnostics.block_kind import KIND_CUT, KIND_IP, KIND_NO_CONNECT, kind_info, site_kind
from diagnostics.tls_probe import (
    KIND_CANCELLED,
    KIND_CERT,
    KIND_CONNECT,
    KIND_OK,
    KIND_RESET,
    KIND_TIMEOUT,
    KIND_TLS,
    ProbeResult,
)
from utils.address_kinds import AddressKind, address_kind, is_stub_address
from utils.windows_dns_query import DNS_STATUS_NAME_ERROR, DNS_STATUS_NO_RECORDS

__all__ = [
    "DnsJudgement",
    "DnsState",
    "FREEZE_MAX_BYTES",
    "FREEZE_MIN_BYTES",
    "Level",
    "ReachState",
    "ServiceVerdict",
    "TargetOutcome",
    "describe_reach",
    "judge_dns",
    "judge_reach",
    "summarize_service",
]


class DnsState(Enum):
    OK = "ok"
    SPOOFED = "spoofed"
    LOCAL = "local"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DnsJudgement:
    state: DnsState
    reason: str


def judge_dns(
    *,
    system_ips: tuple[str, ...],
    system_status: int,
    reference_ips: tuple[str, ...],
    hosts_ips: tuple[str, ...],
    check_kind: str = "",
    check_cert_problem: str = "",
    nxdomain_count: int = 0,
    attempts: int = 1,
) -> DnsJudgement:
    """Вердикт по одному домену.

    ``system_ips`` — ответ DNS-серверов системы (без кэша и hosts),
    ``reference_ips`` — эталон по DNS-over-HTTPS, ``check_kind`` — исход
    HTTPS-запроса к адресу из hosts или DNS системы (пусто, если запрос
    не делался: адрес совпал с эталоном).
    """
    if hosts_ips:
        note = f"адрес задан в файле hosts ({', '.join(hosts_ips)}), программы берут его оттуда, а не у DNS"
        if check_kind == KIND_CERT:
            return DnsJudgement(
                DnsState.LOCAL,
                f"{note}; сервер по этому адресу предъявил чужой сертификат — запись в hosts устарела или неверна",
            )
        return DnsJudgement(DnsState.LOCAL, note)

    # Провайдер подсовывает «сайта нет» наперегонки с настоящим ответом: иногда
    # первым приходит настоящий. Хоть один такой ответ при существующем сайте —
    # подмена, даже если в остальных попытках адрес пришёл правильный.
    # Хватает и одного раза: исправный DNS-сервер при сбое молчит или отвечает
    # ошибкой сервера, а «такого сайта нет» про существующий сайт сам не скажет.
    if system_ips and nxdomain_count and reference_ips:
        return DnsJudgement(
            DnsState.SPOOFED,
            f"DNS отвечает то правильно, то «такого сайта нет» ({nxdomain_count} из {attempts} раз) — "
            "провайдер перехватывает часть запросов",
        )

    if not system_ips:
        if system_status in (DNS_STATUS_NAME_ERROR, DNS_STATUS_NO_RECORDS) and reference_ips:
            return DnsJudgement(
                DnsState.SPOOFED,
                "DNS отвечает, что такого сайта нет, хотя он существует — сайт заблокирован через DNS",
            )
        return DnsJudgement(DnsState.UNKNOWN, "DNS-сервер не дал адрес, сравнить не с чем")

    fake_ips = [ip for ip in system_ips if address_kind(ip) == AddressKind.FAKE_IP]
    if fake_ips:
        return DnsJudgement(DnsState.LOCAL, f"адрес {fake_ips[0]} выдал VPN-клиент (режим fake-ip) — это не провайдер")

    # Адрес домашней сети возвращают домашние фильтры: роутер с родительским контролем,
    # Pi-hole, AdGuard Home. Это настройка сети человека, а не провайдер. Адрес «никуда»
    # (0.0.0.0, 127.0.0.1) сюда не входит: им отвечают и DNS-серверы провайдеров.
    home = [ip for ip in system_ips if address_kind(ip) == AddressKind.LOCAL]
    if home and not any(address_kind(ip) == AddressKind.BLOCK_STUB for ip in system_ips):
        return DnsJudgement(
            DnsState.LOCAL,
            f"DNS вернул адрес домашней сети {home[0]} — так отвечают фильтры на роутере или компьютере "
            "(родительский контроль, Pi-hole, AdGuard Home), это не провайдер",
        )

    stubs = [ip for ip in system_ips if is_stub_address(ip)]
    if stubs:
        return DnsJudgement(DnsState.SPOOFED, f"DNS вернул адрес-заглушку {stubs[0]} вместо настоящего сервера")

    if reference_ips and set(system_ips) & set(reference_ips):
        # Кроме настоящего адреса DNS дал и другой. ``check_kind`` здесь —
        # проверка именно этого другого адреса: чужой сертификат на нём значит,
        # что часть ответов подменена.
        if check_kind == KIND_CERT:
            problem = check_cert_problem or "сертификат не прошёл проверку"
            return DnsJudgement(
                DnsState.SPOOFED,
                f"DNS отвечает то правильным адресом, то адресом чужого сервера ({problem}) — "
                "провайдер перехватывает часть запросов",
            )
        return DnsJudgement(DnsState.OK, "адрес совпадает с эталоном")

    if check_kind == KIND_OK:
        if reference_ips:
            return DnsJudgement(
                DnsState.OK,
                "адрес отличается от эталона, но сервер предъявил подлинный сертификат сайта — "
                "так работают CDN и DNS для обхода блокировок, это не подмена",
            )
        return DnsJudgement(DnsState.OK, "эталон недоступен, но сервер предъявил подлинный сертификат сайта")

    if check_kind == KIND_CERT:
        problem = check_cert_problem or "сертификат не прошёл проверку"
        return DnsJudgement(DnsState.SPOOFED, f"по адресу из DNS отвечает чужой сервер: {problem}")

    return DnsJudgement(
        DnsState.UNKNOWN,
        "адрес отличается от эталона (у CDN и видеосерверов так бывает), а сертификат проверить "
        "не удалось — соединение не установилось",
    )


# ---------------------------------------------------------------------------
# Открывается ли адрес
# ---------------------------------------------------------------------------

# ТСПУ режет соединения с зарубежными серверами после ~16 КБ данных. Считает он
# пакеты (около 25 в обе стороны), поэтому в байтах граница плавает: по замерам
# разных проверок — от 12 до 36 КБ.
FREEZE_MIN_BYTES = 12_000
FREEZE_MAX_BYTES = 36_000


class ReachState(Enum):
    OK = "ok"
    DPI = "dpi"
    FREEZE = "freeze"
    IP_BLOCK = "ip_block"
    CERT = "cert"
    NO_ADDRESS = "no_address"
    UNKNOWN = "unknown"


def judge_reach(result: ProbeResult | None) -> ReachState:
    if result is None:
        return ReachState.NO_ADDRESS
    if result.kind == KIND_OK:
        if result.body_cut and FREEZE_MIN_BYTES <= result.body_size <= FREEZE_MAX_BYTES:
            return ReachState.FREEZE
        return ReachState.OK
    if result.kind == KIND_CERT:
        return ReachState.CERT
    if result.kind == KIND_CONNECT:
        return ReachState.IP_BLOCK
    if result.kind in (KIND_TLS, KIND_RESET, KIND_TIMEOUT):
        return ReachState.DPI
    return ReachState.UNKNOWN


def describe_reach(result: ProbeResult | None, *, timeout: float = 0.0) -> str:
    """Короткое объяснение, почему адрес не открылся."""
    state = judge_reach(result)
    if result is None or state == ReachState.NO_ADDRESS:
        return "не удалось узнать адрес сервера"
    if state == ReachState.FREEZE:
        return (
            f"ответ оборвался на {result.body_size // 1024} КБ — так ТСПУ режет соединения "
            "с зарубежными серверами"
        )
    if state == ReachState.CERT:
        return f"чужой сертификат ({result.cert_problem or 'не прошёл проверку'}) — трафик перехватывает антивирус, прокси или провайдер"
    if state == ReachState.IP_BLOCK:
        return "не удалось соединиться с сервером"
    if result.kind == KIND_TLS:
        return "соединение рвётся при установке шифрования — так обычно режет фильтр провайдера"
    if result.kind == KIND_RESET:
        return "соединение сброшено — так обычно режет фильтр провайдера"
    if result.kind == KIND_TIMEOUT:
        return f"подключение есть, но сервер не ответил за {timeout:.0f} с — похоже на блокировку провайдером"
    if result.kind == KIND_CANCELLED:
        return "проверка прервана — не хватило времени или нажата «Стоп»"
    return result.detail or "не удалось выполнить запрос"


# ---------------------------------------------------------------------------
# Итог по сервису
# ---------------------------------------------------------------------------


class Level(Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TargetOutcome:
    host: str
    purpose: str
    reach: ReachState
    dns: DnsState
    main: bool = False
    # Вид блокировки (``diagnostics.block_kind``), если его уточнили пробами.
    # Пусто — берётся тот, что следует из одного исхода ``reach``.
    kind: str = ""


@dataclass(frozen=True, slots=True)
class ServiceVerdict:
    level: Level
    headline: str
    # Что сделать, по пунктам. Первый пункт — главный.
    advice: tuple[str, ...] = field(default_factory=tuple)
    dns_note: str = ""
    # Вид блокировки сервиса: по нему экран собирает проблемы в группы.
    kind: str = ""


_BROKEN = (ReachState.DPI, ReachState.FREEZE, ReachState.IP_BLOCK, ReachState.CERT)

_ADVICE_STRATEGY = "Подберите другую стратегию: вкладка «Подбор стратегии» найдёт рабочую для вашего провайдера."
_ADVICE_START = "Запустите Zapret на странице «Управление Zapret 2»."
# Адрес принимает соединение, но шифрование не проходит ни с каким именем:
# иногда стратегия справляется, но обещать этого нельзя.
_ADVICE_STRATEGY_OR_ADDRESS = (
    "Попробуйте подобрать стратегию во вкладке «Подбор стратегии». Если ни одна не подойдёт — закрыт сам адрес: "
    "поможет DNS-профиль в «Редакторе hosts», другой DNS или VPN."
)
# Советы «почини через Zapret»: движок убирает их у гео-сайтов, которым Zapret не помогает.
ADVICE_VIA_ZAPRET = (_ADVICE_STRATEGY, _ADVICE_START, _ADVICE_STRATEGY_OR_ADDRESS)


def advice_geo_site(service: str) -> str:
    """Совет для сервиса, который сам ограничивает доступ из России (каталог hosts)."""
    return (
        f"{service} сам ограничивает доступ из России, поэтому стратегия Zapret его не чинит. "
        "Включите для него DNS-профиль на странице «Редактор hosts» или смените DNS в разделе «Настройка DNS»."
    )


# Общий текст совета: движок убирает его из строк сайтов и оставляет одну
# общую строку про DNS, сравнивая именно с этой константой.
ADVICE_DNS = (
    "Включите DNS с шифрованием (DoH) в разделе «Настройка DNS» — провайдер не сможет подменять ответы."
)
_ADVICE_DNS = ADVICE_DNS
_ADVICE_CERT = (
    "Проверьте антивирус (проверку HTTPS-трафика), прокси и VPN: кто-то подменяет сертификаты сайтов."
)
_ADVICE_HOSTS = (
    "Удалите или исправьте запись этого сайта в файле hosts (страница «Редактор hosts») — "
    "адрес в ней устарел."
)
_ADVICE_IP = "Серверы недоступны по адресу. Zapret в таком случае помогает не всегда — попробуйте другой DNS или VPN."
_ADVICE_NO_CONNECT = (
    "Повторите проверку: соединиться не удалось, но подтверждения, что закрыт сам адрес, нет. "
    "Если повторяется — посмотрите записи этого сайта в «Редакторе hosts» и попробуйте другой DNS."
)


def _cert_cause(items: list[TargetOutcome]) -> DnsState | None:
    """Откуда чужой сервер: из файла hosts, из подменённого DNS или «по пути»."""
    causes = {item.dns for item in items if item.reach == ReachState.CERT}
    for cause in (DnsState.LOCAL, DnsState.SPOOFED):
        if cause in causes:
            return cause
    return None


def _kind_of(item: TargetOutcome) -> str:
    return item.kind or site_kind(item.reach.value)


def _leading_kind(items: list[TargetOutcome]) -> str:
    """Вид блокировки сервиса: у первого сломанного адреса (главный идёт первым)."""
    return next((kind for kind in map(_kind_of, items) if kind), "")


def _reason_for(items: list[TargetOutcome], cert_cause: DnsState | None = None) -> str:
    """Чем мешают: «блокировка по имени сайта», «сервер заблокирован по адресу», «обрыв после 16 КБ».

    Причина — у первого сломанного адреса (главный идёт первым): чужой сертификат
    у побочного адреса не должен подменять собой настоящую причину у главного.
    """
    if items and items[0].reach == ReachState.CERT:
        if cert_cause == DnsState.LOCAL:
            return "запись в файле hosts ведёт на чужой сервер"
        if cert_cause == DnsState.SPOOFED:
            return "DNS подсовывает адрес чужого сервера"
        return "вместо настоящего сервера отвечает чужой"
    kind = _leading_kind(items)
    return kind_info(kind).reason if kind else "проверка не дала ответа"


def _advice_for(
    states: list[ReachState],
    *,
    zapret_running: bool | None,
    cert_cause: DnsState | None = None,
    kind: str = "",
) -> tuple[str, ...]:
    cert_advice = (
        _ADVICE_HOSTS if cert_cause == DnsState.LOCAL else _ADVICE_DNS if cert_cause == DnsState.SPOOFED else _ADVICE_CERT
    )
    if states and states[0] == ReachState.CERT:
        return (cert_advice,)
    if ReachState.CERT in states:
        # Чужой сертификат только у побочного адреса: сначала совет по главной причине, потом про него.
        rest = [state for state in states if state != ReachState.CERT]
        return _advice_for(rest, zapret_running=zapret_running, kind=kind) + (cert_advice,)
    if ReachState.DPI in states or ReachState.FREEZE in states:
        # Вид блокировки важнее: закрытому адресу «запустите Zapret» обещал бы лишнее.
        if kind == KIND_IP:
            return (_ADVICE_STRATEGY_OR_ADDRESS,)
        # «Не удалось узнать, запущен ли Zapret» — не «не запущен»: запускать не советуем.
        if zapret_running is False:
            return (_ADVICE_START,)
        return (_ADVICE_STRATEGY,)
    if ReachState.IP_BLOCK in states:
        return (_ADVICE_IP,) if kind == KIND_IP else (_ADVICE_NO_CONNECT,)
    return ("Повторите проверку через минуту.",)


def summarize_service(
    label: str,
    targets: list[TargetOutcome],
    *,
    zapret_running: bool | None,
) -> ServiceVerdict:
    """Итог по сервису: открывается ли он и что делать, если нет."""
    main = next((item for item in targets if item.main), targets[0] if targets else None)
    if main is None:
        return ServiceVerdict(Level.UNKNOWN, f"{label}: проверка не выполнилась", ("Повторите проверку.",))

    spoofed = [item.host for item in targets if item.dns == DnsState.SPOOFED]
    dns_note = ""
    if spoofed:
        dns_note = (
            f"DNS подменяет адрес {', '.join(spoofed)} — провайдер перехватывает обычные DNS-запросы. "
            "Браузер с защищённым DNS этого не замечает, а программы, которые спрашивают адрес у Windows, "
            "сайт не откроют."
        )

    secondary_broken = [item for item in targets if item is not main and item.reach in _BROKEN]
    if main.reach == ReachState.OK:
        advice: tuple[str, ...] = ()
        if not secondary_broken:
            level = Level.WARN if spoofed else Level.OK
            headline = f"{label} открывается"
        else:
            level = Level.WARN
            parts = ", ".join(item.purpose for item in secondary_broken)
            states = [item.reach for item in secondary_broken]
            cause = _cert_cause(secondary_broken)
            headline = f"{label} открывается, но не работают {parts}: {_reason_for(secondary_broken, cause)}"
            advice = _advice_for(
                states, zapret_running=zapret_running, cert_cause=cause, kind=_leading_kind(secondary_broken)
            )
        if spoofed and _ADVICE_DNS not in advice:
            advice = advice + (_ADVICE_DNS,)
        return ServiceVerdict(level, headline, advice, dns_note, _leading_kind(secondary_broken))

    if main.reach in _BROKEN:
        broken = [main, *secondary_broken]
        states = [item.reach for item in broken]
        cause = _cert_cause(broken)
        kind = _leading_kind(broken)
        # При обрыве сайт «открывается» на первые килобайты: честнее сказать, что он не догружается.
        opens = "грузится не до конца" if kind == KIND_CUT else "не открывается"
        headline = f"{label} {opens}: {_reason_for(broken, cause)}"
        if main.reach in (ReachState.DPI, ReachState.FREEZE) and zapret_running:
            headline += " — Zapret запущен, но эту блокировку не обходит"
        advice = _advice_for(states, zapret_running=zapret_running, cert_cause=cause, kind=kind)
        if spoofed and _ADVICE_DNS not in advice:
            advice = advice + (_ADVICE_DNS,)
        # «Причина не ясна» — не установленная блокировка: это замечание, а не тревога.
        level = Level.WARN if kind == KIND_NO_CONNECT else Level.FAIL
        return ServiceVerdict(level, headline, advice, dns_note, kind)

    if main.reach == ReachState.NO_ADDRESS:
        return ServiceVerdict(
            Level.FAIL,
            f"{label} не открывается: не удалось узнать адрес сервера",
            ("Проверьте подключение к интернету.", _ADVICE_DNS),
            dns_note,
        )
    return ServiceVerdict(Level.UNKNOWN, f"{label}: проверка не дала ответа", ("Повторите проверку через минуту.",), dns_note)
