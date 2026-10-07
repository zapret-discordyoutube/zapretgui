"""Движок BlockCheck и проверки DNS подмены.

BlockCheck отвечает на вопрос «какие сайты открываются и что с остальными».
Сайты собраны в сервисы (Discord — это сайт, чат и CDN картинок; YouTube — сайт,
превью и видеосервер). Режим «Discord и YouTube» проверяет только их, «Все
сайты» — ещё мессенджеры, соцсети, контрольные сайты и домены пользователя.
Голосовые серверы (UDP) и обрыв загрузки на 16–20 КБ проверяются всегда.

Как проверяется один адрес
--------------------------

1. Параллельно спрашиваются DNS системы (без кэша и hosts), эталон по
   DNS-over-HTTPS к 1.1.1.1 и 8.8.8.8 (по IP, чтобы эталон не зависел от
   проверяемого DNS) и файл hosts.
2. **Открывается ли сайт.** HTTPS-запрос идёт через ``diagnostics.tls_probe``
   (OpenSSL, TLS 1.3 — как браузер и как подбор стратегий) к адресу, которым
   воспользовался бы браузер: из hosts, из DNS системы, если он подлинный,
   иначе из эталона. При неудаче — ещё одна попытка на другом адресе и одна
   по IPv6: браузер сам переключается на IPv6, если IPv4 режут сильнее.
3. **Честен ли DNS.** Если адрес из DNS не совпал с эталоном, решает
   сертификат по этому адресу (см. ``diagnostics.verdict``).
4. Результаты печатаются в постоянном порядке, в конце — итог с советами.
   Тот же итог возвращается словарём для экрана BlockCheck.

Все сетевые вызовы ограничены по времени; кнопка «Стоп» снимает их сразу.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from diagnostics import block_cause, ipv6_check, quic_probe, upload_probe
from diagnostics.tls_probe import (
    KIND_CANCELLED,
    KIND_CERT,
    ProbeResult,
    https_get,
)
from diagnostics.verdict import (
    ADVICE_DNS as _ADVICE_DNS,
    ADVICE_VIA_ZAPRET as _ADVICE_VIA_ZAPRET,
    advice_geo_site as _advice_geo_site,
    DnsJudgement,
    DnsState,
    Level,
    ReachState,
    ServiceVerdict,
    TargetOutcome,
    describe_reach,
    judge_dns,
    judge_reach,
    summarize_service,
)
from utils.bypass_tools import running_bypass_tools
from utils.dns_reference import REFERENCE_RESOLVERS, ReferenceResolver
from utils.dns_wire import FAILURE_CANCELLED, TYPE_A, TYPE_AAAA, DnsQueryResult, failure_text, query_doh
from utils.socket_cancel import SocketCancel
from utils.windows_dns_query import (
    DNS_STATUS_NAME_ERROR,
    ERROR_CANCELLED,
    DnsAnswer,
    hosts_file_ipv4,
    query_ipv4,
    system_dns_servers,
)

__all__ = [
    "SCOPE_ALL",
    "SCOPE_MAIN",
    "SERVICES",
    "Service",
    "Target",
    "build_services",
    "run_blockcheck",
    "run_dns_check",
]


Emit = Callable[[str], None]
ShouldStop = Callable[[], bool]

DNS_TIMEOUT = 4.0
# Провайдер подменяет DNS не каждый раз: один запрос давал то «подмена», то
# «всё честно». Три запроса подряд ловят и такую подмену.
DNS_ATTEMPTS = 3
DOH_TIMEOUT = 5.0
HTTPS_TIMEOUT = 5.0
READ_TIMEOUT = 3.0
REACH_ATTEMPTS = 2
DISCOVERY_TIMEOUT = 6.0
# Верхняя граница на всю проверку: дальше недопроверенное помечается как
# «нет ответа», а не подвешивает окно. Для «Всех сайтов» — дольше: там ещё
# голосовые серверы и загрузка файлов для проверки обрыва.
RUN_DEADLINE = 30.0
RUN_DEADLINE_ALL = 45.0
FREEZE_READ_TIMEOUT = 4.0


def _timed_out_line(deadline: float) -> str:
    return (
        f"⚠️ Часть проверок не уложилась в {deadline:.0f} с и была прервана — "
        "их результат неизвестен."
    )

# Сколько тела ответа читать, чтобы заметить обрыв после ~16 КБ (ТСПУ режет
# соединение с зарубежными CDN ровно на этом объёме).
BODY_PROBE_BYTES = 64 * 1024

DNS_TYPE_A = TYPE_A
DNS_TYPE_AAAA = TYPE_AAAA

_WATCH_PAGE = "/watch?v=jNQXAC9IVRw&hl=en"
_WATCH_PAGE_MAX_BYTES = 2_000_000
YOUTUBE_HOST = "www.youtube.com"
GOOGLEVIDEO_FALLBACK_HOST = "redirector.googlevideo.com"

SOURCE_HOSTS = "hosts"
SOURCE_SYSTEM = "system"
SOURCE_REFERENCE = "reference"


@dataclass(frozen=True, slots=True)
class Target:
    host: str
    purpose: str
    path: str = "/"
    read_body: bool = False
    # Главный адрес сервиса: если он не открывается, не открывается сервис.
    main: bool = False
    # Хост видеосервера каждый раз узнаётся у YouTube: он свой у каждого
    # провайдера и меняется со временем.
    discover_googlevideo: bool = False


@dataclass(frozen=True, slots=True)
class Service:
    key: str
    label: str
    targets: tuple[Target, ...]
    # Контрольный сайт: его почти никогда не блокируют. Если не открываются
    # даже контрольные — дело в подключении, а не в блокировках.
    control: bool = False
    # Российский контрольный сайт. Если открываются только такие, а зарубежные
    # контрольные нет — провайдер пропускает лишь разрешённые адреса.
    domestic: bool = False


def _site(key: str, label: str, host: str, *, control: bool = False, domestic: bool = False) -> Service:
    return Service(
        key, label, (Target(host, "сайт", read_body=True, main=True),), control=control, domestic=domestic
    )


SCOPE_MAIN = "main"
SCOPE_ALL = "all"

# «Discord и YouTube» — то, ради чего Zapret ставят чаще всего.
SERVICES: dict[str, Service] = {
    "discord": Service(
        "discord",
        "Discord",
        (
            Target("discord.com", "сайт и вход", read_body=True, main=True),
            Target("gateway.discord.gg", "чат и статусы"),
            Target("cdn.discordapp.com", "картинки и файлы"),
        ),
    ),
    "youtube": Service(
        "youtube",
        "YouTube",
        (
            Target(YOUTUBE_HOST, "сайт", read_body=True, main=True),
            Target("i.ytimg.com", "превью видео", path="/generate_204"),
            Target(
                GOOGLEVIDEO_FALLBACK_HOST,
                "видео",
                path="/generate_204",
                discover_googlevideo=True,
            ),
        ),
    ),
}

# Добавляются в режиме «Все сайты».
EXTRA_SERVICES: tuple[Service, ...] = (
    Service(
        "telegram",
        "Telegram",
        (
            Target("telegram.org", "сайт", read_body=True, main=True),
            Target("web.telegram.org", "веб-версия"),
        ),
    ),
    _site("instagram", "Instagram", "www.instagram.com"),
    _site("facebook", "Facebook", "www.facebook.com"),
    _site("x", "X (Twitter)", "x.com"),
    _site("linkedin", "LinkedIn", "www.linkedin.com"),
    _site("spotify", "Spotify", "www.spotify.com"),
    _site("rutracker", "RuTracker", "rutracker.org"),
    _site("google", "Google", "www.google.com", control=True),
    _site("cloudflare", "Cloudflare", "www.cloudflare.com", control=True),
    _site("yandex", "Яндекс", "ya.ru", control=True, domestic=True),
    _site("vk", "ВКонтакте", "vk.com", control=True, domestic=True),
)


def build_services(scope: str, user_domains=()) -> dict[str, Service]:
    """Сервисы для проверки: основные, при «Все сайты» — остальные и свои домены."""
    services = dict(SERVICES)
    if str(scope or "").strip().lower() != SCOPE_ALL:
        # Свои домены — часть режима «Все сайты»: так написано на экране.
        return services
    for service in EXTRA_SERVICES:
        services[service.key] = service
    known_hosts = {target.host for service in services.values() for target in service.targets}
    for domain in user_domains or ():
        host = str(domain or "").strip().lower().rstrip(".")
        if host and host not in known_hosts:
            known_hosts.add(host)
            services[f"user:{host}"] = _site(f"user:{host}", host, host)
    return services


# Сколько потоков нужно одной цели в худшем случае: сама цель, три запроса к
# DNS системы, два эталона (A и AAAA) по запросу на каждый эталонный сервер и
# несколько HTTPS-запросов, четыре пробы уточнения причины и три пакета QUIC. Задачи ждут друг
# друга внутри одного пула, поэтому
# нехватка потоков — это не «медленнее», а взаимная блокировка.
_WORKERS_PER_TARGET = 15 + 2 * len(REFERENCE_RESOLVERS)


class _Stopped(Exception):
    pass


@dataclass(slots=True)
class _Probe:
    target: Target
    service: str
    host: str
    discovery_note: str = ""
    dns: DnsAnswer = field(default_factory=DnsAnswer)
    # Сколько из DNS_ATTEMPTS запросов получили «такого сайта нет».
    dns_nxdomain: int = 0
    hosts_ips: tuple[str, ...] = ()
    reference_ips: tuple[str, ...] = ()
    reference_ok: bool = False
    reference_ipv6: tuple[str, ...] = ()
    # HTTPS-запрос к адресу из hosts или DNS системы (проверка сертификата).
    local_check: ProbeResult | None = None
    # DNS ответил и адресом из эталона, и другим: запрос к этому другому
    # адресу решает, CDN это или подмена «через раз».
    suspect_check: ProbeResult | None = None
    # Итоговый запрос «открывается ли» и откуда взят его адрес.
    reach: ProbeResult | None = None
    reach_source: str = ""
    attempts: int = 0
    # Была ли запасная попытка по IPv6 и чем она кончилась.
    ipv6_result: ProbeResult | None = None
    judgement: DnsJudgement | None = None
    reach_state: ReachState = ReachState.UNKNOWN
    # Как именно блокируют (по имени сайта, по адресу, страницей провайдера), если удалось выяснить.
    cause: block_cause.Cause | None = None
    # Проходит ли QUIC (UDP 443) к этому сайту. None — не проверяли или проверку сняли.
    quic: quic_probe.QuicVerdict | None = None


class _Run:
    """Общие для одного прогона пул потоков, дедлайн и отмена.

    «Стоп» пользователя и истёкший общий лимит — разные вещи: после «Стопа»
    отчёт не печатается, а по лимиту незавершённые запросы снимаются, и отчёт
    выводится с пометкой, что часть проверок не успела.
    """

    def __init__(self, should_stop: ShouldStop | None, *, workers: int, deadline: float | None = None) -> None:
        self._should_stop = should_stop
        self._user_stopped = False
        self.timed_out = False
        self.probe_cancel = SocketCancel()
        # Сервер проверки обрыва → адрес, по которому к нему ходили.
        self.freeze_addresses: dict[str, str] = {}
        self._reference_lock = threading.Lock()
        # Эталонный сервер → [сколько раз ответил, сколько раз нет, последняя причина].
        self._reference: dict[ReferenceResolver, list] = {}
        self.deadline_seconds = RUN_DEADLINE if deadline is None else float(deadline)
        self.deadline = time.monotonic() + self.deadline_seconds
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="diag")

    def _cancel_all(self) -> None:
        self.probe_cancel.cancel()

    def note_reference(self, resolver: ReferenceResolver, result: DnsQueryResult) -> None:
        # Снятый по «Стопу» или по лимиту времени запрос ничего не говорит о сервере.
        if result.failure == FAILURE_CANCELLED:
            return
        with self._reference_lock:
            entry = self._reference.setdefault(resolver, [0, 0, ""])
            if result.answered:
                entry[0] += 1
            else:
                entry[1] += 1
                entry[2] = failure_text(result)

    def reference_report(self) -> list[dict]:
        """Состояние эталонных серверов за прогон, в постоянном порядке."""
        with self._reference_lock:
            seen = {resolver: tuple(entry) for resolver, entry in self._reference.items()}
        report: list[dict] = []
        for resolver in REFERENCE_RESOLVERS:
            if resolver not in seen:
                continue
            answered, failed, reason = seen[resolver]
            report.append(
                {
                    "label": resolver.label,
                    "address": resolver.address,
                    # Сервер считается недоступным, только если не ответил ни разу.
                    "ok": answered > 0,
                    "answered": answered,
                    "failed": failed,
                    "reason": reason if not answered else "",
                }
            )
        return report

    def stopped(self) -> bool:
        if self._user_stopped:
            return True
        try:
            if self._should_stop is not None and self._should_stop():
                self._user_stopped = True
                self._cancel_all()
        except Exception:
            return False
        return self._user_stopped

    def expired(self) -> bool:
        if time.monotonic() < self.deadline:
            return False
        if not self.timed_out:
            self.timed_out = True
            # Всё, что ещё висит, снимаем: запросы и DNS вернутся сразу.
            self._cancel_all()
        return True

    def dns_cancelled(self) -> bool:
        return self.stopped() or self.expired()

    def submit(self, fn, *args, **kwargs) -> Future:
        return self.pool.submit(fn, *args, **kwargs)

    def wait(self, future: Future):
        """Ждёт результат, не пропуская «Стоп» и общий дедлайн."""
        while True:
            if self.stopped():
                raise _Stopped()
            self.expired()
            try:
                return future.result(timeout=0.1)
            except TimeoutError:
                continue

    def close(self) -> None:
        self._cancel_all()
        self.pool.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# Отдельные проверки
# ---------------------------------------------------------------------------


def _doh_lookup(run: _Run, host: str, record_type: int = DNS_TYPE_A) -> tuple[bool, tuple[str, ...]]:
    """Эталонные адреса по DNS-over-HTTPS. (ответил ли хоть один, адреса).

    Спрашиваются все эталонные серверы сразу, ответы складываются. Кто из них
    не ответил и почему — запоминается в прогоне и попадает в отчёт.
    """

    def _one(resolver: ReferenceResolver) -> DnsQueryResult:
        return query_doh(resolver.address, host, record_type, timeout_s=DOH_TIMEOUT, cancel=run.probe_cancel)

    futures = [(resolver, run.submit(_one, resolver)) for resolver in REFERENCE_RESOLVERS]
    answered = False
    ips: list[str] = []
    for resolver, future in futures:
        result = future.result()
        run.note_reference(resolver, result)
        answered = answered or result.answered
        for ip in result.values(record_type):
            if ip not in ips:
                ips.append(ip)
    return answered, tuple(ips)


def _get(run: _Run, host: str, ip: str, path: str, *, read_limit: int = 0) -> ProbeResult:
    return https_get(
        host,
        ip,
        path,
        timeout=HTTPS_TIMEOUT,
        read_limit=read_limit,
        read_timeout=READ_TIMEOUT,
        cancel=run.probe_cancel,
    )


def _discover_googlevideo(run: _Run) -> tuple[str, str]:
    """Хост видеосервера, который YouTube выдаёт этой сети. (хост, пояснение)."""
    from blockcheck.googlevideo_discovery import extract_googlevideo_hosts

    found: list[str] = []

    def _done(body: bytes) -> bool:
        if b"googlevideo" not in body:
            return False
        hosts = extract_googlevideo_hosts(body.decode("utf-8", errors="ignore"))
        if hosts:
            found.append(hosts[0])
            return True
        return False

    # Адрес YouTube — тот, которым воспользовался бы браузер: hosts, DNS
    # системы, а если DNS его не дал — эталон.
    candidates = list(hosts_file_ipv4(YOUTUBE_HOST))
    if not candidates:
        candidates = list(query_ipv4(YOUTUBE_HOST, timeout=DNS_TIMEOUT, cancelled=run.dns_cancelled).ips)
    if not candidates:
        candidates = list(_doh_lookup(run, YOUTUBE_HOST)[1])

    for ip in candidates[:1]:
        result = https_get(
            YOUTUBE_HOST,
            ip,
            _WATCH_PAGE,
            timeout=DISCOVERY_TIMEOUT,
            read_limit=_WATCH_PAGE_MAX_BYTES,
            read_timeout=READ_TIMEOUT,
            body_done=_done,
            cancel=run.probe_cancel,
        )
        if found:
            return found[0], "адрес видеосервера получен от YouTube"
        if result.ok:
            return GOOGLEVIDEO_FALLBACK_HOST, "YouTube не назвал видеосервер, поэтому проверяем общий адрес"
        if result.kind == KIND_CANCELLED:
            break
    return GOOGLEVIDEO_FALLBACK_HOST, "страница YouTube не открылась, поэтому проверяем общий адрес видеосерверов"


def _reach_order(probe: _Probe, *, local_ok: bool) -> tuple[list[str], str]:
    """Адреса для проверки «открывается ли» и откуда они взяты."""
    if probe.hosts_ips:
        return list(probe.hosts_ips), SOURCE_HOSTS
    reference = set(probe.reference_ips)
    # Сначала адреса, которые подтвердил эталон: если DNS «через раз»
    # подсовывает чужой адрес, открываемость сайта проверяется по настоящему.
    system = sorted(probe.dns.ips, key=lambda ip: ip not in reference)
    matches = bool(set(system) & reference)
    if system and (local_ok or matches or not probe.reference_ips):
        return system, SOURCE_SYSTEM
    return list(probe.reference_ips), SOURCE_REFERENCE


def _check_reach(run: _Run, probe: _Probe, *, read_limit: int) -> None:
    local = probe.local_check
    order, source = _reach_order(probe, local_ok=bool(local and local.ok))
    probe.reach_source = source
    if not order:
        # Адреса нет потому, что проверку прервали (лимит времени или «Стоп»),
        # — это «не успели», а не «не удалось узнать адрес».
        if run.dns_cancelled() or probe.dns.status == ERROR_CANCELLED:
            probe.reach = ProbeResult(ip="", kind=KIND_CANCELLED)
        return

    attempts: list[ProbeResult] = []
    if local is not None and local.ip == order[0]:
        attempts.append(local)
    while len(attempts) < REACH_ATTEMPTS:
        last = attempts[-1] if attempts else None
        if last is not None and (last.ok or last.kind == KIND_CANCELLED):
            break
        if run.dns_cancelled():
            break
        tried = {item.ip for item in attempts}
        untried = [item for item in order if item not in tried]
        # Чужой сертификат на одном адресе — повод попробовать другой, но не
        # тот же самый ещё раз.
        if last is not None and last.kind == KIND_CERT and not untried:
            break
        ip = untried[0] if untried else order[0]
        attempts.append(_get(run, probe.host, ip, probe.target.path, read_limit=read_limit))

    probe.attempts = len(attempts)
    if not attempts:
        probe.reach = ProbeResult(ip="", kind=KIND_CANCELLED)
        return
    probe.reach = next((item for item in attempts if item.ok), attempts[-1])

    # Браузер сам уходит на IPv6, если IPv4 не отвечает: без этой попытки
    # проверка показала бы ❌ там, где сайт у пользователя открывается.
    last = probe.reach
    if (
        last is not None
        and not last.ok
        and last.kind not in (KIND_CANCELLED, KIND_CERT)
        and probe.reference_ipv6
        and source != SOURCE_HOSTS
        and not run.dns_cancelled()
    ):
        probe.ipv6_result = _get(run, probe.host, probe.reference_ipv6[0], probe.target.path, read_limit=read_limit)
        if probe.ipv6_result.ok:
            probe.reach = probe.ipv6_result


def _merge_dns_answers(answers: list[DnsAnswer]) -> tuple[DnsAnswer, int]:
    """Сводит несколько одинаковых DNS-запросов. (ответ, сколько раз «сайта нет»).

    Адреса — все, что пришли хоть раз; если адресов не было ни разу, берётся
    первый ответ с его кодом ошибки.
    """
    nxdomain = sum(1 for answer in answers if not answer.ips and answer.status == DNS_STATUS_NAME_ERROR)
    ips: list[str] = []
    for answer in answers:
        for ip in answer.ips:
            if ip not in ips:
                ips.append(ip)
    if not ips:
        return (answers[0] if answers else DnsAnswer()), nxdomain
    first = next(answer for answer in answers if answer.ips)
    return DnsAnswer(ips=tuple(ips), cnames=first.cnames, status=first.status, elapsed_ms=first.elapsed_ms), nxdomain


def _probe_target(run: _Run, target: Target, service: str, *, full: bool) -> _Probe:
    host = target.host
    discovery_note = ""
    if target.discover_googlevideo:
        host, discovery_note = _discover_googlevideo(run)

    probe = _Probe(target=target, service=service, host=host, discovery_note=discovery_note)
    read_limit = BODY_PROBE_BYTES if (full and target.read_body) else 0

    dns_futures = [
        run.submit(query_ipv4, host, timeout=DNS_TIMEOUT, cancelled=run.dns_cancelled)
        for _attempt in range(DNS_ATTEMPTS)
    ]
    doh_future = run.submit(_doh_lookup, run, host)
    doh6_future = run.submit(_doh_lookup, run, host, DNS_TYPE_AAAA) if full else None
    probe.hosts_ips = hosts_file_ipv4(host)
    probe.dns, probe.dns_nxdomain = _merge_dns_answers([future.result() for future in dns_futures])

    local_ips = probe.hosts_ips or probe.dns.ips
    local_future: Future | None = None
    if full and local_ips:
        # В полной проверке запрос к адресу из DNS нужен всегда — не ждём эталон.
        local_future = run.submit(_get, run, host, local_ips[0], target.path, read_limit=read_limit)

    probe.reference_ok, probe.reference_ips = doh_future.result()
    reference = set(probe.reference_ips)
    matches = bool(set(probe.dns.ips) & reference)
    if local_future is None and local_ips and (probe.hosts_ips or not matches):
        local_future = run.submit(_get, run, host, local_ips[0], target.path)
    # DNS дал и адрес из эталона, и другой. У CDN так бывает, но так же
    # выглядит подмена «через раз»: сертификат по другому адресу решает.
    suspects = [ip for ip in probe.dns.ips if ip not in reference] if (matches and not probe.hosts_ips) else []
    suspect_future: Future | None = None
    if suspects and not (local_ips and local_ips[0] == suspects[0]):
        suspect_future = run.submit(_get, run, host, suspects[0], target.path)
    if local_future is not None:
        probe.local_check = local_future.result()
    if suspect_future is not None:
        probe.suspect_check = suspect_future.result()
    elif suspects:
        probe.suspect_check = probe.local_check

    # При совпадении с эталоном о DNS говорит только проверка «лишнего»
    # адреса: сбой на подтверждённом адресе — дело не DNS.
    dns_check = probe.suspect_check if matches and not probe.hosts_ips else probe.local_check
    probe.judgement = judge_dns(
        system_ips=probe.dns.ips,
        system_status=probe.dns.status,
        reference_ips=probe.reference_ips,
        hosts_ips=probe.hosts_ips,
        check_kind=dns_check.kind if dns_check else "",
        check_cert_problem=dns_check.cert_problem if dns_check else "",
        nxdomain_count=probe.dns_nxdomain,
        attempts=DNS_ATTEMPTS,
    )

    if full:
        if doh6_future is not None:
            probe.reference_ipv6 = doh6_future.result()[1]
        _check_reach(run, probe, read_limit=read_limit)
        probe.reach_state = judge_reach(probe.reach)
        # Пакеты QUIC уходят сразу, а ждём их после уточнения причины: обе
        # проверки идут одновременно.
        quic_future = _start_quic(run, probe)
        _refine_cause(run, probe)
        if quic_future is not None:
            probe.quic = quic_probe.judge(quic_future.result())
    return probe


def _start_quic(run: _Run, probe: _Probe) -> Future | None:
    """QUIC проверяется по тому же адресу, к которому шёл основной запрос."""
    result = probe.reach
    if result is None or not result.ip or run.dns_cancelled():
        return None
    return run.submit(quic_probe.collect, probe.host, result.ip, submit=run.submit, cancel=run.probe_cancel)


def _ping_ok(ip: str) -> bool | None:
    """Отвечает ли адрес на пинг. None — пинг в этой системе недоступен."""
    from utils.windows_icmp import ping_ipv4_host_winapi, ping_ipv6_winapi

    if ":" in ip:
        result = ping_ipv6_winapi(ip, count=2, timeout_ms=1500)
    else:
        result = ping_ipv4_host_winapi(ip, count=2, timeout_ms=1500, resolved_ip=ip)
    if result.error_code == "UNSUPPORTED":
        return None
    return bool(result.ok)


def _refine_cause(run: _Run, probe: _Probe) -> None:
    """Сайт не открылся: выясняем, режут по имени или по адресу."""
    result = probe.reach
    if not block_cause.needs_refining(result) or run.dns_cancelled():
        return
    facts = block_cause.collect(probe.host, result, submit=run.submit, cancel=run.probe_cancel, ping=_ping_ok)
    # Проверку могли снять, пока шли пробы: по обрывкам вывод не делаем.
    if not run.dns_cancelled():
        probe.cause = block_cause.judge(facts)


# ---------------------------------------------------------------------------
# Текст отчёта
# ---------------------------------------------------------------------------

_LEVEL_ICON = {Level.OK: "✅", Level.WARN: "⚠️", Level.FAIL: "❌", Level.UNKNOWN: "❔"}
_DNS_ICON = {DnsState.OK: "✅", DnsState.SPOOFED: "❌", DnsState.LOCAL: "ℹ️", DnsState.UNKNOWN: "❔"}
_SOURCE_NOTE = {
    SOURCE_HOSTS: ", адрес из файла hosts",
    SOURCE_REFERENCE: ", адрес по DNS-over-HTTPS",
}


def _ips_text(ips: tuple[str, ...], limit: int = 3) -> str:
    if not ips:
        return "—"
    shown = ", ".join(ips[:limit])
    return f"{shown} и ещё {len(ips) - limit}" if len(ips) > limit else shown


def _reach_text(probe: _Probe) -> str:
    """Одна строка: открывается ли адрес и почему нет."""
    result = probe.reach
    source = _SOURCE_NOTE.get(probe.reach_source, "")
    if probe.reach_state == ReachState.OK and result is not None:
        tls = f", {result.tls_version.replace('TLSv', 'TLS ')}" if result.tls_version else ""
        if ":" in result.ip:
            return f"открывается по IPv6, по IPv4 — нет ({result.elapsed_ms:.0f} мс{tls}, {result.ip})"
        return f"открывается ({result.elapsed_ms:.0f} мс{tls}, {result.ip}{source})"
    text = describe_reach(result, timeout=HTTPS_TIMEOUT)
    if result is None or not result.ip:
        return text
    tries = f", попыток: {probe.attempts}" if probe.attempts > 1 else ""
    ipv6 = ", по IPv6 тоже не открылся" if probe.ipv6_result is not None else ""
    return f"{text} ({result.ip}{source}{tries}{ipv6})"


def _dns_detail(probe: _Probe) -> str:
    parts: list[str] = []
    if probe.hosts_ips:
        parts.append(f"hosts: {_ips_text(probe.hosts_ips)}")
    if probe.dns.ips:
        flaky = f" (а {probe.dns_nxdomain} из {DNS_ATTEMPTS} раз — «сайта нет»)" if probe.dns_nxdomain else ""
        parts.append(f"DNS системы: {_ips_text(probe.dns.ips)}{flaky}")
    else:
        parts.append(f"DNS системы: нет адреса ({probe.dns.detail})")
    if probe.reference_ips:
        parts.append(f"эталон: {_ips_text(probe.reference_ips)}")
    elif not probe.reference_ok:
        parts.append("эталон: недоступен")
    return " · ".join(parts)


def _dns_lines(probe: _Probe, indent: str) -> list[str]:
    judgement = probe.judgement
    lines: list[str] = []
    if judgement is not None:
        lines.append(f"{indent}{_DNS_ICON[judgement.state]} DNS: {judgement.reason}")
    lines.append(f"{indent}   {_dns_detail(probe)}")
    return lines


# Молчание по QUIC — не поломка: сервер может его не поддерживать.
_QUIC_ICON = {
    quic_probe.QUIC_OK: "✅",
    quic_probe.QUIC_BLOCKED_BY_NAME: "❌",
    quic_probe.QUIC_SILENT: "ℹ️",
}


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _probe_lines(probe: _Probe, *, full: bool) -> list[str]:
    title = f"{probe.host} — {probe.target.purpose}"
    if not full:
        lines = [title]
        if probe.discovery_note:
            lines.append(f"  ℹ️ {probe.discovery_note}")
        lines.extend(_dns_lines(probe, "  "))
        return lines

    icon = "✅" if probe.reach_state == ReachState.OK else "❌"
    if probe.reach_state == ReachState.UNKNOWN:
        icon = "❔"
    lines = [f"{icon} {title}: {_reach_text(probe)}"]
    if probe.cause is not None:
        lines.append(f"   🔎 {_sentence(probe.cause.text)}")
    if probe.quic is not None:
        lines.append(f"   {_QUIC_ICON[probe.quic.code]} QUIC (UDP 443): {probe.quic.text}")
    if probe.discovery_note:
        lines.append(f"   ℹ️ {probe.discovery_note}")
    lines.extend(_dns_lines(probe, "   "))
    return lines


def _dns_provider_name(ip: str) -> tuple[str, str]:
    """(название провайдера из списка программы, его раздел) или пустые строки."""
    try:
        from dns.dns_providers import DNS_PROVIDERS
    except Exception:
        return "", ""
    for category, providers in DNS_PROVIDERS.items():
        for name, info in providers.items():
            if ip in (info.get("ipv4") or ()):
                return str(name), str(category)
    return "", ""


def _environment_lines() -> list[str]:
    lines: list[str] = []
    if sys.platform == "win32":
        version = sys.getwindowsversion()
        edition = "Windows 11" if version.build >= 22000 else f"Windows {version.major}"
        lines.append(f"🖥️ {edition} (сборка {version.build})")

    servers = system_dns_servers()
    if servers:
        described: list[str] = []
        unblock_names: list[str] = []
        for ip in servers:
            name, category = _dns_provider_name(ip)
            described.append(f"{ip} ({name})" if name else ip)
            if category == "Для ИИ" and name not in unblock_names:
                unblock_names.append(name)
        lines.append(f"🌐 DNS-серверы системы: {', '.join(described)}")
        for name in unblock_names:
            lines.append(
                f"ℹ️ {name} сам меняет адреса части сайтов, чтобы обходить блокировки. "
                "Такие адреса проверяются по сертификату и подменой не считаются."
            )
    return lines


def _zapret_status() -> tuple[bool | None, str]:
    try:
        from settings.mode import ALL_WINWS_EXE_NAME_SET, WINWS_EXE_FAMILY_LABEL
        from utils.windows_process_probe import iter_process_records_winapi

        running = [
            f"{name} (PID {pid})"
            for pid, name in iter_process_records_winapi()
            if str(name or "").lower() in ALL_WINWS_EXE_NAME_SET
        ]
    except Exception as exc:
        return None, f"⚠️ Zapret: не удалось проверить процессы ({exc})"
    if running:
        return True, f"✅ Zapret запущен: {', '.join(running)}"
    return False, f"❌ Zapret не запущен ({WINWS_EXE_FAMILY_LABEL} нет среди процессов)"


# ---------------------------------------------------------------------------
# Прогоны
# ---------------------------------------------------------------------------


def _run_probes(
    run: _Run,
    services: dict[str, Service],
    *,
    full: bool,
    emit: Emit,
) -> dict[str, list[_Probe]]:
    """Запускает все цели сразу и печатает их блоки по порядку."""
    planned: list[tuple[str, Target, Future]] = []
    for key, service in services.items():
        for target in service.targets:
            if not full and target.discover_googlevideo:
                target = Target(target.host, target.purpose, target.path, main=target.main)
            planned.append((key, target, run.submit(_probe_target, run, target, key, full=full)))

    collected: dict[str, list[_Probe]] = {key: [] for key in services}
    current_service = ""
    for key, target, future in planned:
        if key != current_service:
            current_service = key
            emit("")
            emit(f"━━━━━━━━ {services[key].label} ━━━━━━━━")
        try:
            probe = run.wait(future)
        except _Stopped:
            raise
        except Exception as exc:
            emit(f"❔ {target.host} — {target.purpose}: проверка не выполнилась ({exc})")
            continue
        collected[key].append(probe)
        for line in _probe_lines(probe, full=full):
            emit(line)
    emit("")
    return collected


def _service_verdict(service: Service, probes: list[_Probe], *, zapret_running: bool | None) -> ServiceVerdict:
    outcomes = [
        TargetOutcome(
            host=probe.host,
            purpose=probe.target.purpose,
            reach=probe.reach_state,
            dns=probe.judgement.state if probe.judgement else DnsState.UNKNOWN,
            main=probe.target.main,
        )
        for probe in probes
    ]
    return summarize_service(service.label, outcomes, zapret_running=zapret_running)


def _short_text(probe: _Probe) -> str:
    if probe.reach_state != ReachState.OK:
        return describe_reach(probe.reach, timeout=HTTPS_TIMEOUT)
    if probe.reach is not None and ":" in probe.reach.ip:
        return "открывается только по IPv6"
    return "открывается"


def _target_report(probe: _Probe) -> dict:
    return {
        "host": probe.host,
        "purpose": probe.target.purpose,
        "main": probe.target.main,
        "state": probe.reach_state.value,
        "ok": probe.reach_state == ReachState.OK,
        "text": _reach_text(probe),
        "short": _short_text(probe),
        "dns_state": probe.judgement.state.value if probe.judgement else "",
        "dns_reason": probe.judgement.reason if probe.judgement else "",
        "cause": probe.cause.code if probe.cause else "",
        "cause_text": _sentence(probe.cause.text) if probe.cause else "",
        "quic": probe.quic.code if probe.quic else "",
        "quic_text": probe.quic.text if probe.quic else "",
        "note": probe.discovery_note,
    }


def _freeze_address(run: _Run, host: str) -> str:
    """Адрес сервера для проверки обрыва: из hosts/DNS системы, иначе эталон. Пусто — не нашли.

    Адрес запоминается на прогон: отправка проверяется на том же адресе, что и загрузка.
    """
    known = run.freeze_addresses.get(host)
    if known is not None:
        return known
    ips = list(hosts_file_ipv4(host)) or list(query_ipv4(host, timeout=DNS_TIMEOUT, cancelled=run.dns_cancelled).ips)
    if not ips:
        ips = list(_doh_lookup(run, host)[1])
    address = ips[0] if ips else ""
    if address:
        run.freeze_addresses[host] = address
    return address


def _download(run: _Run, host: str, path: str) -> ProbeResult | None:
    """Загрузка файла для проверки обрыва."""
    ip = _freeze_address(run, host)
    if not ip:
        return None
    from diagnostics.freeze_check import READ_LIMIT

    return https_get(
        host,
        ip,
        path,
        timeout=HTTPS_TIMEOUT,
        read_limit=READ_LIMIT,
        read_timeout=FREEZE_READ_TIMEOUT,
        cancel=run.probe_cancel,
    )


def _upload(run: _Run, host: str, path: str) -> upload_probe.UploadVerdict | None:
    """Проверка отправки данных на тот же сервер. None — адреса нет или проверку сняли."""
    ip = _freeze_address(run, host)
    if not ip or run.dns_cancelled():
        return None
    facts = upload_probe.collect(host, ip, path, submit=run.submit, cancel=run.probe_cancel)
    return upload_probe.judge(facts)


def _wait_plain(future: Future):
    return future.result()


_LEVEL_ORDER = {Level.FAIL: 0, Level.WARN: 1, Level.UNKNOWN: 2, Level.OK: 3}
# Блокировки, которые обходит стратегия Zapret.
_BYPASSABLE = (ReachState.DPI, ReachState.FREEZE)


def _no_geo_service(_host: str) -> str:
    return ""


def _problem(level: Level, text: str, advice=(), *, action: str = "", target: str = "") -> dict:
    return {"level": level.value, "text": text, "advice": list(advice), "action": action, "target": target}


def _collect_problems(
    services: dict[str, Service],
    verdicts: dict[str, ServiceVerdict],
    collected: dict[str, list[_Probe]],
    *,
    voice,
    freeze,
    zapret_running: bool | None,
    geo_service_for: Callable[[str], str] | None = None,
    reference: list[dict] | None = None,
    ipv6: ipv6_check.Ipv6Verdict | None = None,
) -> tuple[list[dict], list[str], list[str]]:
    """Итог для экрана: проблемы по важности, открывающиеся сервисы, подменённые DNS."""
    problems: list[dict] = []

    controls = [key for key, service in services.items() if service.control]
    foreign = [key for key in controls if not services[key].domestic]
    domestic = [key for key in controls if services[key].domestic]
    # Только настоящий провал контрольных сайтов: «не успели проверить»
    # (лимит времени) — не «нет интернета», иначе такой прогон спрятал бы
    # найденные блокировки остальных сайтов.
    foreign_down = bool(foreign) and all(verdicts[key].level == Level.FAIL for key in foreign)
    domestic_up = bool(domestic) and all(verdicts[key].level == Level.OK for key in domestic)
    whitelisted = foreign_down and domestic_up
    offline = bool(controls) and all(verdicts[key].level == Level.FAIL for key in controls)
    names = ", ".join(services[key].label for key in foreign)
    if whitelisted:
        problems.append(
            _problem(
                Level.FAIL,
                f"Открываются только российские сайты ({', '.join(services[key].label for key in domestic)}), "
                f"а зарубежные контрольные ({names}) — нет. Похоже на режим «белых списков»: провайдер "
                "пропускает только разрешённые адреса",
                (
                    "В таком режиме Zapret не помогает: закрыты сами адреса, а не отдельные сайты. "
                    "Обычно это временное ограничение, чаще в мобильных сетях — проверьте другую сеть.",
                ),
            )
        )
    elif offline:
        problems.append(
            _problem(
                Level.FAIL,
                f"Не открываются даже контрольные сайты ({', '.join(services[key].label for key in controls)}) — "
                "похоже, нет интернета или всё соединение режет антивирус, прокси или VPN",
                ("Проверьте подключение к интернету и повторите проверку.",),
            )
        )
    # В обоих случаях причина общая и уже названа: совет «подберите стратегию»
    # у каждого сайта был бы неправдой и шумом.
    offline = offline or whitelisted

    # Сервисы идут в том же порядке, что и в отчёте: «Открываются: …» не должен
    # начинаться с сайтов, у которых просто подменён DNS. Проблемы по важности
    # сортируются в конце, и внутри одного уровня этот порядок сохраняется.
    working: list[str] = []
    for key, service in services.items():
        if service.control:
            continue
        verdict = verdicts[key]
        broken = [probe for probe in collected.get(key, ()) if probe.reach_state != ReachState.OK]
        if verdict.level in (Level.FAIL, Level.WARN) and broken:
            if offline:
                # Без интернета «Zapret не обходит блокировку» у каждого сайта —
                # неправда и шум: причина одна, она уже написана первой строкой.
                continue
            advice = tuple(item for item in verdict.advice if item != _ADVICE_DNS)
            # Стратегия помогает только от DPI и обрыва. При чужом сертификате,
            # недоступном адресе или без адреса кнопка подбора увела бы не туда.
            bypassable = next((probe for probe in broken if probe.reach_state in _BYPASSABLE), None)
            action = ""
            if bypassable is not None:
                action = "strategy" if zapret_running else "start_zapret"
            target = (bypassable or broken[0]).host
            # Гео-сайт сам ограничивает доступ из России: стратегия его не
            # чинит, и совет «подберите стратегию» увёл бы пользователя не туда.
            geo = next(
                ((probe.host, name) for probe in broken if (name := (geo_service_for or _no_geo_service)(probe.host))),
                None,
            )
            if geo is not None:
                target, geo_service = geo
                advice = (_advice_geo_site(geo_service),) + tuple(
                    item for item in advice if item not in _ADVICE_VIA_ZAPRET
                )
                action = "hosts"
            causes = tuple(
                dict.fromkeys(_sentence(probe.cause.text) + "." for probe in broken if probe.cause is not None)
            )
            problems.append(_problem(verdict.level, verdict.headline, causes + advice, action=action, target=target))
        elif verdict.level == Level.UNKNOWN:
            if not offline:
                problems.append(_problem(Level.UNKNOWN, verdict.headline, verdict.advice))
        else:
            working.append(service.label)

    if freeze is not None and freeze.level in (Level.FAIL, Level.WARN):
        problems.append(_problem(freeze.level, freeze.headline, freeze.advice, action="strategy" if zapret_running else "start_zapret"))
    elif freeze is not None and freeze.level == Level.UNKNOWN and not offline:
        problems.append(_problem(freeze.level, freeze.headline, freeze.advice))
    if voice is not None and voice.level != Level.OK and not offline:
        problems.append(_problem(voice.level, voice.headline, voice.advice, action="strategy_voice"))

    spoofed = [
        probe.host
        for probes in collected.values()
        for probe in probes
        if probe.judgement is not None and probe.judgement.state == DnsState.SPOOFED
    ]
    if spoofed:
        shown = ", ".join(spoofed[:5]) + (f" и ещё {len(spoofed) - 5}" if len(spoofed) > 5 else "")
        problems.append(
            _problem(
                Level.WARN,
                f"DNS подменяет ответы для {shown}. Браузер с защищённым DNS этого не замечает, а программы, "
                "которые спрашивают адрес у Windows, эти сайты не откроют",
                (_ADVICE_DNS,),
                action="dns",
            )
        )
    # QUIC заблокирован, а сам сайт открывается: это не «сайт не работает», но
    # браузер сначала пробует QUIC и переходит на обычное соединение с задержкой.
    quic_blocked = [
        services[key].label
        for key, probes in collected.items()
        if any(
            probe.quic is not None
            and probe.quic.code == quic_probe.QUIC_BLOCKED_BY_NAME
            and probe.reach_state == ReachState.OK
            for probe in probes
        )
    ]
    if quic_blocked and not offline:
        problems.append(
            _problem(
                Level.WARN,
                f"QUIC (UDP 443) блокируется по имени для: {', '.join(quic_blocked)}. Сайты открываются обычным "
                "соединением, но браузер сначала пробует QUIC, поэтому открытие и начало видео могут запаздывать",
                (_ADVICE_QUIC,),
            )
        )
    if ipv6 is not None and ipv6.code == ipv6_check.IPV6_BROKEN and not offline:
        problems.append(_problem(Level.WARN, f"IPv6 {ipv6.text}", (_ADVICE_IPV6,)))
    for item in _blocked_references(reference or []):
        problems.append(_problem(Level.WARN, _reference_text(item), (_ADVICE_BLOCKED_REFERENCE,), action="dns"))
    problems.sort(key=lambda item: _LEVEL_ORDER.get(Level(item["level"]), 9))
    return problems, working, spoofed


def _system_has_ipv6_route() -> bool | None:
    """Считает ли Windows, что по IPv6 есть дорога в интернет. None — не Windows или ошибка."""
    if sys.platform != "win32":
        return None
    from dns.winapi import internet_route

    return bool(internet_route().has_ipv6)


def _check_ipv6(run: _Run) -> ipv6_check.Ipv6Verdict:
    facts = ipv6_check.collect(
        has_route=_system_has_ipv6_route,
        lookup=lambda host: _doh_lookup(run, host, DNS_TYPE_AAAA)[1],
        get=lambda host, ip: _get(run, host, ip, "/"),
        submit=run.submit,
    )
    return ipv6_check.judge(facts)


_IPV6_ICON = {
    ipv6_check.IPV6_OK: "✅",
    ipv6_check.IPV6_ABSENT: "ℹ️",
    ipv6_check.IPV6_BROKEN: "⚠️",
    ipv6_check.IPV6_UNKNOWN: "❔",
}
_ADVICE_IPV6 = (
    "Из-за этого сайты открываются с задержкой: браузер сначала ждёт IPv6. Перезагрузите роутер; "
    "если не поможет — снимите галочку «IP версии 6» в свойствах сетевого адаптера Windows."
)


def _blocked_references(reference: list[dict]) -> list[dict]:
    """Эталонные серверы, которые не ответили ни разу, хотя другие отвечали.

    Если молчат все, дело не в отдельном сервере: это уже «нет интернета» или
    «эталон недоступен», и об этом сказано в другом месте отчёта.
    """
    down = [item for item in reference if not item["ok"]]
    return down if len(down) < len(reference) else []


def _reference_text(item: dict) -> str:
    reason = f": {item['reason']}" if item["reason"] else ""
    return f"Шифрованный DNS {item['label']} ({item['address']}) недоступен{reason}"


_ADVICE_QUIC = (
    "В пресете должен быть profile для UDP 443 (QUIC) с этими сайтами. Проще всего выбрать готовый пресет, "
    "где он есть, или отключить QUIC в браузере (в Chrome: chrome://flags → Experimental QUIC protocol)."
)
_ADVICE_BLOCKED_REFERENCE = (
    "Похоже, этот сервер закрыт у вашего провайдера. Не выбирайте его для защищённого DNS: "
    "Windows молча вернётся к обычным запросам, которые видны и подменяются."
)


def _section_lines(title: str, report, rows) -> list[str]:
    icon = {Level.OK: "✅", Level.WARN: "⚠️", Level.FAIL: "❌", Level.UNKNOWN: "❔"}
    lines = ["", f"━━━━━━━━ {title} ━━━━━━━━"]
    for mark, name, text in rows:
        lines.append(f"{mark} {name}: {text}")
    lines.append(f"{icon[report.level]} {report.headline}")
    return lines


def run_blockcheck(
    scope: str = SCOPE_MAIN,
    *,
    user_domains=(),
    emit: Emit,
    should_stop: ShouldStop | None = None,
    geo_service_for: Callable[[str], str] | None = None,
) -> dict:
    """Проверка BlockCheck. Печатает отчёт через ``emit`` и возвращает итог для экрана.

    ``geo_service_for`` — поиск «адрес → сервис» по гео-сайтам каталога hosts:
    таким сайтам советуется hosts или DNS, а не подбор стратегии.
    """
    from diagnostics.freeze_check import check_freeze, summarize_freeze
    from diagnostics.voice_check import check_voice, summarize_voice

    scope = SCOPE_ALL if str(scope or "").strip().lower() == SCOPE_ALL else SCOPE_MAIN
    services = build_services(scope, user_domains)
    targets_count = sum(len(service.targets) for service in services.values())
    run = _Run(
        should_stop,
        workers=targets_count * _WORKERS_PER_TARGET + 40,
        deadline=RUN_DEADLINE_ALL if scope == SCOPE_ALL else RUN_DEADLINE,
    )
    started = time.monotonic()
    try:
        title = "Все сайты" if scope == SCOPE_ALL else "Discord и YouTube"
        emit(f"🔍 BlockCheck: {title} — {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
        environment = _environment_lines()
        for line in environment:
            emit(line)
        zapret_running, zapret_line = _zapret_status()
        emit(zapret_line)
        other_tools = running_bypass_tools()
        if other_tools:
            emit(
                f"ℹ️ Работают другие программы обхода или VPN: {', '.join(other_tools)}. "
                "Результат показывает сеть вместе с ними, а не «чистую» сеть провайдера."
            )
        emit("⏳ Проверяем так же, как браузер: TLS 1.3, правильные адреса сайтов…")

        # Звонки и обрыв на 16 КБ проверяются всегда: режим меняет только список сайтов.
        voice_future = run.submit(check_voice, run.submit, _wait_plain)
        freeze_future = run.submit(
            check_freeze,
            run.submit,
            _wait_plain,
            lambda host, path: _download(run, host, path),
            lambda host, path: _upload(run, host, path),
        )

        ipv6_future = run.submit(_check_ipv6, run)

        collected = _run_probes(run, services, full=True, emit=emit)

        ipv6 = None
        try:
            ipv6 = run.wait(ipv6_future)
        except _Stopped:
            raise
        except Exception as exc:
            emit(f"❔ IPv6: проверка не выполнилась ({exc})")
        if ipv6 is not None:
            emit("━━━━━━━━ IPv6 ━━━━━━━━")
            emit(f"{_IPV6_ICON[ipv6.code]} IPv6 {ipv6.text}")

        voice = freeze = None
        if voice_future is not None:
            try:
                voice = summarize_voice(run.wait(voice_future))
            except _Stopped:
                raise
            except Exception as exc:
                emit(f"❔ Голосовые серверы: проверка не выполнилась ({exc})")
            if voice is not None:
                for line in _section_lines(
                    "Голосовые звонки (UDP)",
                    voice,
                    [("✅" if item.answered else "❌", item.name, item.text) for item in voice.servers],
                ):
                    emit(line)
        if freeze_future is not None:
            try:
                freeze = summarize_freeze(run.wait(freeze_future), zapret_running=zapret_running)
            except _Stopped:
                raise
            except Exception as exc:
                emit(f"❔ Обрыв на 16–20 КБ: проверка не выполнилась ({exc})")
            if freeze is not None:
                marks = {"ok": "✅", "freeze": "❌", "unknown": "❔"}
                for line in _section_lines(
                    "Обрыв на 16–20 КБ",
                    freeze,
                    [(marks[item.state.value], item.name, item.text) for item in freeze.servers],
                ):
                    emit(line)

        verdicts = {
            key: _service_verdict(service, collected[key], zapret_running=zapret_running)
            for key, service in services.items()
        }
        problems, working, spoofed = _collect_problems(
            services,
            verdicts,
            collected,
            voice=voice,
            freeze=freeze,
            zapret_running=zapret_running,
            geo_service_for=geo_service_for,
            reference=run.reference_report(),
            ipv6=ipv6,
        )

        emit("")
        emit("━━━━━━━━ 📊 Итог ━━━━━━━━")
        if run.timed_out:
            emit(_timed_out_line(run.deadline_seconds))
        icon = {"ok": "✅", "warn": "⚠️", "fail": "❌", "unknown": "❔"}
        for problem in problems:
            emit(f"{icon[problem['level']]} {problem['text']}")
            for advice in problem["advice"]:
                emit(f"   👉 {advice}")
        if working:
            emit(f"✅ Открываются: {', '.join(working)}")
        elapsed = time.monotonic() - started
        emit(f"Проверка заняла {elapsed:.1f} с.")

        return {
            "scope": scope,
            "services": [
                {
                    "key": key,
                    "label": service.label,
                    "control": service.control,
                    "domestic": service.domestic,
                    "level": verdicts[key].level.value,
                    "headline": verdicts[key].headline,
                    "advice": list(verdicts[key].advice),
                    "dns_note": verdicts[key].dns_note,
                    "targets": [_target_report(probe) for probe in collected[key]],
                }
                for key, service in services.items()
            ],
            "voice": _section_report(
                voice, [(item.name, "ok" if item.answered else "fail", item.text) for item in voice.servers]
            ) if voice else None,
            "freeze": _section_report(
                freeze, [(item.name, item.state.value, item.text) for item in freeze.servers]
            ) if freeze else None,
            "problems": problems,
            "working": working,
            "spoofed_hosts": spoofed,
            "reference": run.reference_report(),
            "ipv6": {"state": ipv6.code, "text": ipv6.text} if ipv6 is not None else None,
            "environment": environment,
            "zapret_running": zapret_running,
            "zapret_line": zapret_line,
            "other_bypass_tools": list(other_tools),
            "timed_out": run.timed_out,
            "elapsed": elapsed,
            "dns_poisoning_detected": bool(spoofed),
        }
    except _Stopped:
        return {"stopped": True}
    finally:
        run.close()


def _section_report(report, rows) -> dict:
    return {
        "level": report.level.value,
        "headline": report.headline,
        "advice": list(report.advice),
        # state: ok / fail / freeze / unknown — «не удалось проверить» не должно
        # выглядеть как «не работает».
        "items": [{"name": name, "ok": state == "ok", "state": state, "text": text} for name, state, text in rows],
    }


def run_dns_check(*, emit: Emit, should_stop: ShouldStop | None = None) -> dict:
    """Проверка DNS подмены для вкладки «Проверка DNS подмены»."""
    services = build_services(SCOPE_MAIN)
    targets_count = sum(len(service.targets) for service in services.values())
    run = _Run(should_stop, workers=targets_count * _WORKERS_PER_TARGET)
    started = time.monotonic()
    try:
        emit("🔍 ПРОВЕРКА DNS ПОДМЕНЫ")
        for line in _environment_lines():
            emit(line)
        emit(
            "Как проверяем: адрес от DNS системы сравниваем с эталоном по DNS-over-HTTPS. "
            "Если адреса разные, решает сертификат сервера: подлинный — подмены нет."
        )

        collected = _run_probes(run, services, full=False, emit=emit)

        spoofed = _has_spoofing(collected)
        emit("━━━━━━━━ 📊 Итог ━━━━━━━━")
        if run.timed_out:
            emit(_timed_out_line(run.deadline_seconds))
        if spoofed:
            emit("❌ Обнаружена DNS подмена:")
            for probes in collected.values():
                for probe in probes:
                    if probe.judgement is not None and probe.judgement.state == DnsState.SPOOFED:
                        emit(f"   • {probe.host} — {probe.judgement.reason}")
            emit(
                "Браузер с защищённым DNS этого не замечает, а программы, которые спрашивают адрес "
                "у Windows, получат неверный ответ."
            )
            emit("👉 Откройте «Настройка DNS» и включите DNS с шифрованием (DoH) — его провайдер перехватить не сможет.")
        elif any(
            probe.judgement and probe.judgement.state == DnsState.UNKNOWN
            for probes in collected.values()
            for probe in probes
        ):
            emit("✅ Явной подмены не найдено. Часть адресов (❔) отличается от эталона, а проверить их не удалось — для CDN это обычно нормально.")
        else:
            emit("✅ DNS работает честно. Если сайты не открываются — дело не в DNS, а в блокировке соединения.")
        reference = run.reference_report()
        for item in _blocked_references(reference):
            emit(f"⚠️ {_reference_text(item)}")
            emit(f"   👉 {_ADVICE_BLOCKED_REFERENCE}")
        emit(f"Проверка заняла {time.monotonic() - started:.1f} с.")
        return {
            "summary": {"dns_poisoning_detected": spoofed},
            "reference": reference,
            "domains": {
                probe.host: {
                    "state": probe.judgement.state.value if probe.judgement else "",
                    "reason": probe.judgement.reason if probe.judgement else "",
                    "system_ips": list(probe.dns.ips),
                    "reference_ips": list(probe.reference_ips),
                    "hosts_ips": list(probe.hosts_ips),
                }
                for probes in collected.values()
                for probe in probes
            },
        }
    except _Stopped:
        return {"summary": {"dns_poisoning_detected": False}, "stopped": True}
    finally:
        run.close()


def _has_spoofing(collected: dict[str, list[_Probe]]) -> bool:
    return any(
        probe.judgement is not None and probe.judgement.state == DnsState.SPOOFED
        for probes in collected.values()
        for probe in probes
    )
