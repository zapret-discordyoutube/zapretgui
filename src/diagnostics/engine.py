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
4. **Чем именно мешают.** Если сайт не открылся, дополнительные пробы
   (``diagnostics.block_cause``) выясняют, режут по имени сайта или закрыт
   сам адрес. Если открылся — по одному соединению набирается объём
   (``diagnostics.volume_probe``): так виден обрыв после 16 КБ у сайта с
   короткой главной страницей. Итог — вид блокировки
   (``diagnostics.block_kind``): по нему экран собирает проблемы в группы.
5. Результаты печатаются в постоянном порядке, в конце — итог с советами.
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

from diagnostics import (
    block_cause,
    block_kind,
    ipv6_check,
    my_network,
    protocol_probe,
    quic_probe,
    system_state,
    telegram_check,
    upload_probe,
    volume_probe,
)
from diagnostics.services import (
    GOOGLEVIDEO_FALLBACK_HOST,
    SCOPE_ALL,
    SCOPE_FULL,
    SCOPE_MAIN,
    SCOPE_TITLES as _SCOPE_TITLES,
    YOUTUBE_HOST,
    Service,
    Target,
    build_services,
)
from diagnostics.tls_probe import (
    KIND_CONNECT,
    KIND_CANCELLED,
    KIND_CERT,
    ProbeResult,
    https_get,
)
from diagnostics.verdict import (
    ADVICE_DNS as _ADVICE_DNS,
    ADVICE_VIA_ZAPRET as _ADVICE_VIA_ZAPRET,
    FREEZE_MAX_BYTES,
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
from utils.ip_owner import lookup_ip_owner
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
    "PROGRESS_STEPS",
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
# Сколько разных адресов сайта пробовать, прежде чем сказать «не открывается».
# Браузер перебирает все адреса из ответа DNS; у крупных сайтов их несколько,
# и закрытым бывает только один.
REACH_ADDRESSES = 4
# Пауза перед повтором на единственном адресе: потерянный пакет не должен
# выглядеть блокировкой.
RETRY_PAUSE_S = 1.0
# Сколько видеосерверов YouTube пробовать: плеер тоже переключается на запасные.
VIDEO_SERVERS = 3
DISCOVERY_TIMEOUT = 6.0
# Верхняя граница на всю проверку: дальше недопроверенное помечается как
# «нет ответа», а не подвешивает окно. Для «Всех сайтов» — дольше: там ещё
# голосовые серверы и загрузка файлов для проверки обрыва.
RUN_DEADLINE = 30.0
# Сколько сайтов проверять одновременно.
SITES_AT_ONCE = 14
RUN_DEADLINE_ALL = 45.0
# Полная проверка ждёт ещё DNS-серверы и поиск места фильтра.
RUN_DEADLINE_FULL = 180.0
FILTER_MAX_TTL = 20
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

SOURCE_HOSTS = "hosts"
SOURCE_SYSTEM = "system"
SOURCE_REFERENCE = "reference"


# Шаги хода проверки: что и в каком порядке показывает экран, пока она идёт.
STEP_SITES = "sites"
STEP_HOSTINGS = "hostings"
STEP_VOICE = "voice"
STEP_IPV6 = "ipv6"
STEP_SYSTEM = "system"
STEP_DNS_SERVERS = "dns_servers"
STEP_FILTER = "filter"
PROGRESS_STEPS = (STEP_SITES, STEP_HOSTINGS, STEP_VOICE, STEP_IPV6, STEP_SYSTEM, STEP_DNS_SERVERS, STEP_FILTER)
# Сколько потоков нужно одной цели в худшем случае: сама цель, три запроса к
# DNS системы, два эталона (A и AAAA) по запросу на каждый эталонный сервер и
# несколько HTTPS-запросов, четыре пробы уточнения причины, три пакета QUIC и четыре
# потока на TLS 1.2 / TLS 1.3 / HTTP. Задачи ждут друг
# друга внутри одного пула, поэтому
# нехватка потоков — это не «медленнее», а взаимная блокировка.
_WORKERS_PER_TARGET = 19 + 2 * len(REFERENCE_RESOLVERS)


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
    # Проходит ли по одному соединению больше 16 КБ. None — не проверяли:
    # сайт не открылся или главная страница и так больше.
    volume: volume_probe.VolumeVerdict | None = None
    # Тот же адрес по TLS 1.2, TLS 1.3 и HTTP отдельно. Пусто — не проверяли.
    protocols: tuple[protocol_probe.ProtocolLine, ...] = ()
    # Все попытки основного запроса по порядку: (адрес, исход).
    tried: tuple[tuple[str, str], ...] = ()
    # Адрес из файла hosts не ответил, а настоящий адрес сайта открылся.
    hosts_stale: bool = False

    @property
    def address_confirmed(self) -> bool:
        """Несоединение перепроверено: молчат все адреса сайта, и другим путём он не открылся.

        Только тогда оно называется «баном по адресу». Одно неудачное соединение
        бывает из-за устаревшей записи в hosts, потерянного пакета или антивируса.
        """
        if not self.tried or any(kind != KIND_CONNECT for _ip, kind in self.tried):
            return False
        # Нужна перепроверка: второй адрес или повтор на том же после паузы.
        if len(self.tried) < 2:
            return False
        # Адреса только из hosts — это проверка записи в hosts, а не сайта.
        if self.hosts_ips and {ip for ip, _kind in self.tried} <= set(self.hosts_ips):
            return False
        # QUIC к тому же адресу отвечает — дорога до адреса открыта, браузер сайт откроет.
        if self.quic is not None and self.quic.code == quic_probe.QUIC_OK:
            return False
        return True

    @property
    def kind(self) -> str:
        """Вид блокировки: по адресу, по имени сайта, обрыв после 16 КБ… Пусто — сайт открывается."""
        return block_kind.site_kind(
            self.reach_state.value,
            self.cause.code if self.cause else "",
            address_confirmed=self.address_confirmed,
        )


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


def _discover_googlevideo(run: _Run) -> tuple[tuple[str, ...], str]:
    """Видеосерверы, которые YouTube выдаёт этой сети. (хосты, пояснение)."""
    from blockcheck.googlevideo_discovery import extract_googlevideo_hosts

    found: list[str] = []

    def _done(body: bytes) -> bool:
        if b"googlevideo" not in body:
            return False
        hosts = extract_googlevideo_hosts(body.decode("utf-8", errors="ignore"))
        if hosts:
            found.extend(hosts[:VIDEO_SERVERS])
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
            return tuple(found), "адрес видеосервера получен от YouTube"
        if result.ok:
            return (GOOGLEVIDEO_FALLBACK_HOST,), "YouTube не назвал видеосервер, поэтому проверяем общий адрес"
        if result.kind == KIND_CANCELLED:
            break
    return (GOOGLEVIDEO_FALLBACK_HOST,), "страница YouTube не открылась, поэтому проверяем общий адрес видеосерверов"


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


def _reach_candidates(probe: _Probe, order: list[str]) -> list[str]:
    """Все известные адреса сайта: сначала те, что выбрал ``_reach_order``, затем остальные.

    Запись в hosts или ответ DNS могут вести на неотвечающий адрес — тогда
    сайт перепроверяется по остальным, как это сделал бы браузер.
    """
    seen: list[str] = []
    for ip in (*order, *probe.dns.ips, *probe.reference_ips):
        if ip and ip not in seen:
            seen.append(ip)
    if not seen:
        return seen
    # После первого адреса — сначала адреса из других сетей: соседние адреса
    # одной сети обычно закрыты или открыты все разом, и четыре попытки в
    # одну сеть ничего не перепроверили бы.
    first, rest = seen[0], seen[1:]
    by_network: dict[str, list[str]] = {}
    for ip in rest:
        by_network.setdefault(_network_of(ip), []).append(ip)
    groups = sorted(by_network.items(), key=lambda item: item[0] == _network_of(first))
    spread: list[str] = []
    while any(items for _network, items in groups):
        for _network, items in groups:
            if items:
                spread.append(items.pop(0))
    return [first, *spread]


def _network_of(ip: str) -> str:
    """Сеть адреса для грубого сравнения: первые два числа IPv4."""
    return ".".join(ip.split(".")[:2]) if "." in ip else ip.split(":")[0]


def _pause(run: _Run, seconds: float) -> None:
    """Пауза, которую снимает «Стоп» и общий лимит времени."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not run.dns_cancelled():
        time.sleep(0.05)


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

    def _one(ip: str) -> ProbeResult:
        return _get(run, probe.host, ip, probe.target.path, read_limit=read_limit)

    def _settled(items: list[ProbeResult]) -> bool:
        return any(item.ok or item.kind == KIND_CANCELLED for item in items)

    attempts: list[ProbeResult] = []
    if local is not None and local.ip == order[0]:
        attempts.append(local)
    elif run.dns_cancelled():
        # Проверку прервали до первого запроса: это «не успели», а не «не открывается».
        probe.reach = ProbeResult(ip="", kind=KIND_CANCELLED)
        return
    else:
        attempts.append(_one(order[0]))

    candidates = _reach_candidates(probe, order)
    others = [ip for ip in candidates if ip != attempts[0].ip][: REACH_ADDRESSES - 1]
    # Чужой сертификат на адресе — повод попробовать другой адрес, но не тот же ещё раз.
    if not _settled(attempts) and not run.dns_cancelled():
        if others:
            # Остальные адреса пробуются разом: ждать их по очереди — это десятки секунд.
            futures = [run.submit(_one, ip) for ip in others]
            attempts.extend(future.result() for future in futures)
        elif attempts[0].kind != KIND_CERT:
            _pause(run, RETRY_PAUSE_S)
            if not run.dns_cancelled():
                attempts.append(_one(order[0]))

    probe.attempts = len(attempts)
    probe.tried = tuple((item.ip, item.kind) for item in attempts if item.kind != KIND_CANCELLED)
    opened = next((item for item in attempts if item.ok), None)
    probe.reach = opened or attempts[0]
    if opened is None and run.dns_cancelled() and len(probe.tried) < 2:
        # Время вышло раньше перепроверки: один сбой — это «не успели», а не «не открывается».
        probe.reach = ProbeResult(ip=attempts[0].ip, kind=KIND_CANCELLED)
        return
    if opened is not None and opened.ip not in order:
        # Открылся адрес не из того источника, с которого начинали.
        probe.reach_source = SOURCE_SYSTEM if opened.ip in probe.dns.ips else SOURCE_REFERENCE
        probe.hosts_stale = source == SOURCE_HOSTS

    # Браузер сам уходит на IPv6, если IPv4 не отвечает: без этой попытки
    # проверка показала бы ❌ там, где сайт у пользователя открывается.
    last = probe.reach
    if (
        last is not None
        and not last.ok
        and last.kind not in (KIND_CANCELLED, KIND_CERT)
        and probe.reference_ipv6
        and not run.dns_cancelled()
    ):
        probe.ipv6_result = _one(probe.reference_ipv6[0])
        if probe.ipv6_result.ok:
            probe.reach = probe.ipv6_result
            probe.hosts_stale = source == SOURCE_HOSTS


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


def _probe_target(run: _Run, target: Target, service: str, *, full: bool, volume: bool = False) -> _Probe:
    if not target.discover_googlevideo:
        return _probe_host(run, target, service, target.host, "", full=full, volume=volume)
    hosts, note = _discover_googlevideo(run)
    first = probe = _probe_host(run, target, service, hosts[0], note, full=full, volume=volume)
    # Один видеосервер не ответил — плеер взял бы следующий. Проверяем так же.
    for host in hosts[1:]:
        if probe.reach_state == ReachState.OK or run.dns_cancelled():
            break
        probe = _probe_host(
            run, target, service, host, f"{note}; первый видеосервер не ответил, проверен запасной", full=full, volume=volume
        )
    return probe if probe.reach_state == ReachState.OK else first


def _probe_host(
    run: _Run, target: Target, service: str, host: str, discovery_note: str, *, full: bool, volume: bool = False
) -> _Probe:
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
        protocols_future = _start_protocols(run, probe)
        _refine_cause(run, probe)
        if volume:
            _check_volume(run, probe)
        if quic_future is not None:
            probe.quic = quic_probe.judge(quic_future.result())
        if protocols_future is not None:
            probe.protocols = protocol_probe.judge(protocols_future.result())
    return probe


def _start_protocols(run: _Run, probe: _Probe) -> Future | None:
    """TLS 1.2, TLS 1.3 и HTTP — по тому же адресу, к которому шёл основной запрос."""
    result = probe.reach
    if result is None or not result.ip or run.dns_cancelled():
        return None
    return run.submit(protocol_probe.collect, probe.host, result.ip, submit=run.submit, cancel=run.probe_cancel)


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


def _check_volume(run: _Run, probe: _Probe) -> None:
    """Сайт открылся: проверяем, проходит ли по одному соединению больше 16 КБ."""
    result = probe.reach
    if probe.reach_state != ReachState.OK or result is None or not result.ip or run.dns_cancelled():
        return
    if result.body_size >= FREEZE_MAX_BYTES and not result.body_cut:
        # Главная страница сама больше окна обрыва и пришла целиком.
        return
    facts = volume_probe.collect(probe.host, result.ip, probe.target.path, cancel=run.probe_cancel)
    if run.dns_cancelled():
        return
    probe.volume = volume_probe.judge(facts)
    if probe.volume.code == volume_probe.VOLUME_CUT:
        probe.reach_state = ReachState.FREEZE


def _fail_text(probe: _Probe) -> str:
    """Почему адрес не открылся — одной фразой."""
    if probe.volume is not None and probe.volume.code == volume_probe.VOLUME_CUT:
        return f"{probe.volume.text} — так провайдер обрывает загрузку"
    return describe_reach(probe.reach, timeout=HTTPS_TIMEOUT)


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
        stale = " — адрес из файла hosts не ответил, запись в нём устарела" if probe.hosts_stale else ""
        return f"открывается ({result.elapsed_ms:.0f} мс{tls}, {result.ip}{source}){stale}"
    text = _fail_text(probe)
    if result is None or not result.ip:
        return text
    addresses = len({ip for ip, _kind in probe.tried})
    if addresses > 1:
        tries = f", не ответил ни один из {addresses} адресов"
    else:
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
    if probe.kind:
        lines.append(f"   🏷 Вид блокировки: {block_kind.kind_info(probe.kind).title}")
    if probe.cause is not None:
        lines.append(f"   🔎 {_sentence(probe.cause.text)}")
    if probe.volume is not None and probe.volume.code != volume_probe.VOLUME_CUT:
        icon = "✅" if probe.volume.code == volume_probe.VOLUME_OK else "ℹ️"
        lines.append(f"   {icon} Обрыв после 16 КБ: {probe.volume.text}")
    if probe.quic is not None:
        lines.append(f"   {_QUIC_ICON[probe.quic.code]} QUIC (UDP 443): {probe.quic.text}")
    if probe.discovery_note:
        lines.append(f"   ℹ️ {probe.discovery_note}")
    lines.extend(_dns_lines(probe, "   "))
    return lines


def _dns_provider(ip: str) -> tuple[str, str, str]:
    """(название сервера из списка программы, его раздел, пометка состояния) или пустые строки."""
    try:
        from dns.dns_providers import find_provider_by_address
    except Exception:
        return "", "", ""
    if ip in ("127.0.0.1", "::1"):
        # Адрес этого компьютера: отвечает встроенный шифрованный DNS, если он запущен.
        try:
            from dns.local_proxy import active_mode

            mode = active_mode()
        except Exception:
            mode = ""
        return (f"шифрованный DNS программы, режим {mode}" if mode else "локальный DNS на этом компьютере"), "", ""
    found = find_provider_by_address(ip)
    if found is None:
        return "", "", ""
    category, name, info = found
    return str(name), str(category), str(info.get("status", ""))


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
        blocked_names: list[str] = []
        for ip in servers:
            name, category, status = _dns_provider(ip)
            described.append(f"{ip} ({name})" if name else ip)
            if category == "Для ИИ" and name not in unblock_names:
                unblock_names.append(name)
            if status == "blocked" and name not in blocked_names:
                blocked_names.append(name)
        lines.append(f"🌐 DNS-серверы системы: {', '.join(described)}")
        for name in unblock_names:
            lines.append(
                f"ℹ️ {name} сам меняет адреса части сайтов, чтобы обходить блокировки. "
                "Такие адреса проверяются по сертификату и подменой не считаются."
            )
        for name in blocked_names:
            lines.append(
                f"⚠️ {name} в России блокируется: обычные запросы к нему могут не доходить "
                "или подменяться по дороге. Что отвечает на вашей линии, покажет вкладка «DNS-серверы»."
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
    on_done: Callable[[int, int], None] | None = None,
) -> dict[str, list[_Probe]]:
    """Запускает все цели сразу и печатает их блоки по порядку.

    ``on_done(готово, всего)`` зовётся после каждой проверенной цели.
    """
    total = sum(len(service.targets) for service in services.values())
    lock = threading.Lock()
    done = [0]
    # Десятки сайтов разом — это сотни запросов к эталонным DNS-серверам в одну
    # секунду: они начинают отказывать, и это выглядело бы как блокировка.
    gate = threading.BoundedSemaphore(SITES_AT_ONCE)

    def _probe(*args, **kwargs) -> _Probe:
        try:
            with gate:
                return _probe_target(*args, **kwargs)
        finally:
            if on_done is not None:
                with lock:
                    done[0] += 1
                    ready = done[0]
                on_done(ready, total)

    planned: list[tuple[str, Target, Future]] = []
    for key, service in services.items():
        for target in service.targets:
            if not full and target.discover_googlevideo:
                target = Target(target.host, target.purpose, target.path, main=target.main)
            # Объём по одному соединению — это десятки запросов подряд: контрольные
            # сайты ими не нагружаем, их всё равно не блокируют.
            volume = full and not service.control
            planned.append((key, target, run.submit(_probe, run, target, key, full=full, volume=volume)))

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
            kind=probe.kind,
        )
        for probe in probes
    ]
    return summarize_service(service.label, outcomes, zapret_running=zapret_running)


def _short_text(probe: _Probe) -> str:
    if probe.reach_state != ReachState.OK:
        return _fail_text(probe)
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
        # Вид блокировки одним словом (ip / sni / cut16 / …) и его название.
        "kind": probe.kind,
        "kind_title": block_kind.kind_info(probe.kind).title if probe.kind else "",
        "volume": probe.volume.code if probe.volume else "",
        "volume_text": probe.volume.text if probe.volume else "",
        "cause": probe.cause.code if probe.cause else "",
        "cause_text": _sentence(probe.cause.text) if probe.cause else "",
        "quic": probe.quic.code if probe.quic else "",
        "quic_text": probe.quic.text if probe.quic else "",
        # Тот же адрес по TLS 1.2, TLS 1.3 и HTTP отдельно.
        "protocols": [
            {"key": line.key, "title": line.title, "state": line.state, "word": line.word, "text": line.text,
             "ms": None if line.ms is None else round(line.ms, 1)}
            for line in probe.protocols
        ],
        "address": probe.reach.ip if probe.reach is not None else "",
        # Какие адреса сайта пробовали и чем кончилось: видно, на чём держится вывод.
        "tried": [{"address": ip, "result": kind} for ip, kind in probe.tried],
        "address_confirmed": probe.address_confirmed,
        "hosts_stale": probe.hosts_stale,
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


def _problem(
    level: Level,
    text: str,
    advice=(),
    *,
    action: str = "",
    target: str = "",
    kind: str = block_kind.KIND_OTHER,
    title: str = "",
    evidence=(),
) -> dict:
    """Строка итога. ``kind`` — вид блокировки: по нему экран собирает строки в группы.

    ``title`` — короткое название для строки внутри группы (вид блокировки там
    уже назван в заголовке). ``evidence`` — на чём основан вывод; эти же фразы
    стоят первыми в ``advice``.
    """
    return {
        "level": level.value,
        "text": text,
        "advice": list(advice),
        "action": action,
        "target": target,
        "kind": kind,
        "title": title,
        "evidence": list(evidence),
    }


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
    system: tuple[system_state.SystemItem, ...] = (),
    telegram: telegram_check.TelegramReport | None = None,
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
                kind=block_kind.KIND_NETWORK,
            )
        )
    elif offline:
        problems.append(
            _problem(
                Level.FAIL,
                f"Не открываются даже контрольные сайты ({', '.join(services[key].label for key in controls)}) — "
                "похоже, нет интернета или всё соединение режет антивирус, прокси или VPN",
                ("Проверьте подключение к интернету и повторите проверку.",),
                kind=block_kind.KIND_NETWORK,
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
            problems.append(
                _problem(
                    verdict.level,
                    verdict.headline,
                    causes + advice,
                    action=action,
                    target=target,
                    kind=verdict.kind or block_kind.KIND_OTHER,
                    # Сервис не открывается целиком — в группе хватит названия;
                    # «открывается, но не работают картинки» нужно сказать полностью.
                    title=service.label if verdict.level == Level.FAIL else "",
                    evidence=causes,
                )
            )
        elif verdict.level == Level.UNKNOWN:
            if not offline:
                problems.append(_problem(Level.UNKNOWN, verdict.headline, verdict.advice))
        else:
            working.append(service.label)

    if freeze is not None and freeze.level in (Level.FAIL, Level.WARN):
        problems.append(
            _problem(
                freeze.level,
                freeze.headline,
                freeze.advice,
                action="strategy" if zapret_running else "start_zapret",
                kind=block_kind.KIND_CUT,
            )
        )
    elif freeze is not None and freeze.level == Level.UNKNOWN and not offline:
        problems.append(_problem(freeze.level, freeze.headline, freeze.advice))
    if voice is not None and voice.level != Level.OK and not offline:
        problems.append(
            _problem(voice.level, voice.headline, voice.advice, action="strategy_voice", kind=block_kind.KIND_VOICE)
        )
    # Один молчащий дата-центр — не проблема для человека: приложение возьмёт другой.
    if telegram is not None and telegram.level == Level.FAIL and not offline:
        problems.append(_problem(telegram.level, telegram.headline, telegram.advice, title="Telegram"))

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
                kind=block_kind.KIND_DNS,
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
                kind=block_kind.KIND_QUIC,
            )
        )
    # Неполадки самого компьютера показываются всегда: они объясняют и «нет интернета».
    for item in system:
        level = _SYSTEM_PROBLEM_LEVEL.get(item.level)
        if level is not None:
            problems.append(
                _problem(
                    level,
                    f"{item.title}: {item.text}",
                    (item.advice,) if item.advice else (),
                    kind=block_kind.KIND_SYSTEM,
                )
            )
    if ipv6 is not None and ipv6.code == ipv6_check.IPV6_BROKEN and not offline:
        problems.append(_problem(Level.WARN, f"IPv6 {ipv6.text}", (_ADVICE_IPV6,), kind=block_kind.KIND_NETWORK))
    for item in _blocked_references(reference or []):
        problems.append(
            _problem(
                Level.WARN,
                _reference_text(item),
                (_ADVICE_BLOCKED_REFERENCE,),
                action="dns",
                kind=block_kind.KIND_DNS,
            )
        )
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


_CLOCK_HOST = "www.google.com"


def _clock_skew(run: _Run) -> float | None:
    """На сколько секунд часы компьютера впереди времени сервера. None — узнать не удалось."""
    from email.utils import parsedate_to_datetime

    addresses = _doh_lookup(run, _CLOCK_HOST)[1]
    if not addresses:
        return None
    result = _get(run, _CLOCK_HOST, addresses[0], "/generate_204", read_limit=1)
    local = time.time()
    for line in result.body.split(b"\r\n\r\n", 1)[0].split(b"\r\n")[1:]:
        name, _colon, value = line.partition(b":")
        if name.strip().lower() == b"date":
            try:
                return local - parsedate_to_datetime(value.decode("ascii", errors="ignore").strip()).timestamp()
            except (TypeError, ValueError):
                return None
    return None


def _check_system(run: _Run, services: dict[str, Service]) -> tuple[system_state.SystemItem, ...]:
    hosts = tuple(dict.fromkeys(target.host for service in services.values() for target in service.targets))
    facts = system_state.collect_facts(check_hosts=hosts, clock_skew=lambda: _clock_skew(run))
    return system_state.judge(facts)


_SYSTEM_ICON = {
    system_state.LEVEL_OK: "✅",
    system_state.LEVEL_INFO: "ℹ️",
    system_state.LEVEL_WARN: "⚠️",
    system_state.LEVEL_FAIL: "❌",
    system_state.LEVEL_UNKNOWN: "❔",
}
_SYSTEM_PROBLEM_LEVEL = {system_state.LEVEL_FAIL: Level.FAIL, system_state.LEVEL_WARN: Level.WARN}


_DNS_FINDING_LEVEL = {"fail": Level.FAIL, "warn": Level.WARN}
_DNS_FINDING_ICON = {"ok": "✅", "info": "ℹ️", "warn": "⚠️", "fail": "❌"}


def _finish_dns_servers(run: _Run, future: Future, emit: Emit) -> dict | None:
    """Итог проверки DNS-серверов для полной проверки: печатает раздел и возвращает словарь."""
    try:
        result = run.wait(future)
    except _Stopped:
        raise
    except Exception as exc:
        emit(f"❔ DNS-серверы: проверка не выполнилась ({exc})")
        return None
    if not isinstance(result, dict):
        return None
    findings = [
        {"level": str(item.get("level") or "info"), "text": str(item.get("text") or "")}
        for item in result.get("findings") or ()
        if item.get("text")
    ]
    emit("")
    emit("━━━━━━━━ DNS-серверы ━━━━━━━━")
    for finding in findings:
        emit(f"{_DNS_FINDING_ICON.get(finding['level'], 'ℹ️')} {finding['text']}")
    text = str(result.get("text") or "")
    for line in text.splitlines():
        emit(f"   {line}")
    return {"level": str(result.get("level") or "unknown"), "findings": findings, "text": text}


def _find_filter_place(run: _Run, services: dict[str, Service], collected: dict[str, list[_Probe]], emit: Emit) -> dict | None:
    """Где стоит фильтр — по первому сайту, QUIC к которому блокируют по имени."""
    from diagnostics import path_trace

    blocked = [
        probe
        for probes in collected.values()
        for probe in probes
        if probe.quic is not None
        and probe.quic.code == quic_probe.QUIC_BLOCKED_BY_NAME
        and probe.reach is not None
        and probe.reach.ip
        and ":" not in probe.reach.ip
    ]
    # Фильтр один на всю сеть: достаточно найти его по одному сайту.
    if not blocked or run.dns_cancelled():
        return None
    probe = blocked[0]
    ip = probe.reach.ip
    trace = path_trace.trace_route(ip, should_stop=run.dns_cancelled)
    facts = path_trace.locate_filter(ip, probe.host, max_ttl=FILTER_MAX_TTL, cancel=run.probe_cancel)
    verdict = path_trace.judge_filter(facts, trace if trace.supported else None)
    if verdict is None:
        return None
    emit("")
    emit("━━━━━━━━ Где стоит фильтр ━━━━━━━━")
    found = verdict.code == path_trace.FILTER_FOUND
    emit(f"{'📍' if found else 'ℹ️'} По сайту {probe.host}: {verdict.text}")
    return {
        "host": probe.host,
        "address": ip,
        "found": found,
        "hop": verdict.hop,
        "text": _sentence(verdict.text),
        "hops": [
            {"ttl": hop.ttl, "address": hop.address, "rtt_ms": hop.rtt_ms}
            for hop in (trace.hops if trace.supported else ())
        ],
    }


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


def _check_network(run: _Run, other_tools) -> dict:
    """«Ваша сеть»: внешний адрес, провайдер и адрес компьютера — готовым словарём для отчёта."""

    def _fetch(server: str) -> bytes | None:
        result = https_get(
            my_network.TRACE_HOST,
            server,
            my_network.TRACE_PATH,
            timeout=HTTPS_TIMEOUT,
            read_limit=2048,
            read_timeout=READ_TIMEOUT,
            cancel=run.probe_cancel,
        )
        return bytes(result.body) if result.ok and result.body else None

    def _ask(name: str, rtype: int) -> DnsQueryResult | None:
        # Владельца сети спрашиваем шифрованным путём: иначе за него ответил бы перехватчик DNS.
        for resolver in REFERENCE_RESOLVERS:
            result = query_doh(resolver.address, name, rtype, cancel=run.probe_cancel)
            if result.answered:
                return result
        return None

    facts = my_network.collect(fetch=_fetch, owner_of=lambda ip: lookup_ip_owner(ip, _ask))
    lines = my_network.judge(facts, bypass_tools=other_tools)
    owner = facts.owner
    provider = " · ".join(part for part in ((owner.owner if owner else ""), (f"AS{owner.asn}" if owner and owner.asn else "")) if part)
    return {
        "external_ip": facts.external_ip,
        "country": facts.country,
        "provider": provider,
        "asn": owner.asn if owner else "",
        "prefix": owner.prefix if owner else "",
        "local_ip": facts.local_ip,
        "lines": [{"state": line.state, "name": line.name, "text": line.text} for line in lines],
    }


def _telegram_text(item: telegram_check.DcResult) -> str:
    if item.connected is None:
        return "проверку прервали"
    if item.connected:
        took = "меньше чем за 1 мс" if (item.ms or 0) < 1 else f"за {round(item.ms or 0)} мс"
        return f"соединение {took}" + (" (со второй попытки)" if item.attempts > 1 else "")
    return f"не соединился, попыток: {item.attempts}"


def _telegram_state(item: telegram_check.DcResult) -> str:
    return "unknown" if item.connected is None else ("ok" if item.connected else "fail")


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
    check_dns_servers: Callable[..., dict] | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict:
    """Проверка BlockCheck. Печатает отчёт через ``emit`` и возвращает итог для экрана.

    ``progress(шаг, готово, всего)`` — ход проверки для экрана; шаги перечислены
    в ``PROGRESS_STEPS``. Зовётся из рабочих потоков.

    ``geo_service_for`` — поиск «адрес → сервис» по гео-сайтам каталога hosts:
    таким сайтам советуется hosts или DNS, а не подбор стратегии.
    ``check_dns_servers`` — проверка DNS-серверов для полной проверки: получает
    ``should_stop`` и возвращает ``{"level", "findings": [{"level", "text"}], "text"}``.
    """
    from diagnostics.freeze_check import check_freeze, summarize_freeze
    from diagnostics.voice_check import check_voice, summarize_voice

    scope = str(scope or "").strip().lower()
    if scope not in _SCOPE_TITLES:
        scope = SCOPE_MAIN
    full = scope == SCOPE_FULL
    services = build_services(scope, user_domains)

    def step(name: str, done: int = 1, total: int = 1) -> None:
        if progress is None:
            return
        try:
            progress(name, done, total)
        except Exception:
            pass

    targets_count = sum(len(service.targets) for service in services.values())
    run = _Run(
        should_stop,
        # Полная проверка держит по потоку на каждый хостинг из списка.
        workers=targets_count * _WORKERS_PER_TARGET + (120 if full else 40),
        deadline={SCOPE_ALL: RUN_DEADLINE_ALL, SCOPE_FULL: RUN_DEADLINE_FULL}.get(scope, RUN_DEADLINE),
    )
    started = time.monotonic()
    try:
        title = _SCOPE_TITLES[scope]
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
            every=full,
            on_server=lambda _server, done, total: step(STEP_HOSTINGS, done, total),
        )

        network_future = run.submit(_check_network, run, other_tools)
        # Дата-центры Telegram — часть списка «все сайты»: в коротком режиме их не трогаем.
        telegram_future = (
            run.submit(
                telegram_check.check_telegram,
                run.submit,
                _wait_plain,
                connect=lambda address, port: telegram_check.connect_once(address, port, cancel=run.probe_cancel),
                pause=lambda seconds: _pause(run, seconds),
            )
            if scope != SCOPE_MAIN
            else None
        )
        ipv6_future = run.submit(_check_ipv6, run)
        system_future = run.submit(_check_system, run, services)
        dns_future = run.submit(check_dns_servers, should_stop=run.dns_cancelled) if full and check_dns_servers else None

        collected = _run_probes(
            run, services, full=True, emit=emit, on_done=lambda done, total: step(STEP_SITES, done, total)
        )

        ipv6 = None
        try:
            ipv6 = run.wait(ipv6_future)
        except _Stopped:
            raise
        except Exception as exc:
            emit(f"❔ IPv6: проверка не выполнилась ({exc})")
        step(STEP_IPV6)
        if ipv6 is not None:
            emit("━━━━━━━━ IPv6 ━━━━━━━━")
            emit(f"{_IPV6_ICON[ipv6.code]} IPv6 {ipv6.text}")

        system: tuple[system_state.SystemItem, ...] = ()
        try:
            system = tuple(run.wait(system_future))
        except _Stopped:
            raise
        except Exception as exc:
            emit(f"❔ Состояние системы: проверка не выполнилась ({exc})")
        step(STEP_SYSTEM)
        if system:
            emit("")
            emit("━━━━━━━━ Состояние системы ━━━━━━━━")
            for item in system:
                emit(f"{_SYSTEM_ICON[item.level]} {item.title}: {item.text}")

        voice = freeze = None
        if voice_future is not None:
            try:
                voice = summarize_voice(run.wait(voice_future))
            except _Stopped:
                raise
            except Exception as exc:
                emit(f"❔ Голосовые серверы: проверка не выполнилась ({exc})")
            step(STEP_VOICE)
            if voice is not None:
                for line in _section_lines(
                    "Голосовые звонки (UDP)",
                    voice,
                    [("✅" if item.answered else "❌", item.name, item.text) for item in voice.servers],
                ):
                    emit(line)
        telegram = None
        if telegram_future is not None:
            try:
                telegram = telegram_check.summarize_telegram(run.wait(telegram_future), zapret_running=zapret_running)
            except _Stopped:
                raise
            except Exception as exc:
                emit(f"❔ Дата-центры Telegram: проверка не выполнилась ({exc})")
            if telegram is not None:
                marks = {"ok": "✅", "fail": "❌", "unknown": "❔"}
                for line in _section_lines(
                    "Telegram: дата-центры",
                    telegram,
                    [
                        (marks[_telegram_state(item)], f"{item.center.name} ({item.center.address})", _telegram_text(item))
                        for item in telegram.servers
                    ],
                ):
                    emit(line)
        network = None
        try:
            network = run.wait(network_future)
        except _Stopped:
            raise
        except Exception as exc:
            emit(f"❔ Ваша сеть: проверка не выполнилась ({exc})")
        if network is not None:
            emit("")
            emit("━━━━━━━━ Ваша сеть ━━━━━━━━")
            for item in network["lines"]:
                emit(f"{'⚠️' if item['state'] == 'warn' else 'ℹ️'} {item['name']}: {item['text']}")
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

        dns_servers = _finish_dns_servers(run, dns_future, emit) if dns_future is not None else None
        if full:
            step(STEP_DNS_SERVERS)
        filter_place = _find_filter_place(run, services, collected, emit) if full else None
        if full:
            step(STEP_FILTER)

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
            system=system,
            telegram=telegram,
        )
        if dns_servers is not None:
            for finding in dns_servers["findings"]:
                level = _DNS_FINDING_LEVEL.get(finding["level"])
                if level is not None:
                    problems.append(_problem(level, finding["text"], action="dns", kind=block_kind.KIND_DNS))
            problems.sort(key=lambda item: _LEVEL_ORDER.get(Level(item["level"]), 9))

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
                    "kind": verdicts[key].kind,
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
            ) | {
                # По серверу: провайдер, метка, в какую сторону оборвалось и за сколько проверили.
                "servers": [
                    {
                        "provider": item.provider,
                        "host": item.host,
                        "id": item.ident,
                        "state": item.state.value,
                        "text": item.text,
                        "direction": item.direction,
                        "seconds": round(item.seconds, 1),
                    }
                    for item in freeze.servers
                ]
            } if freeze else None,
            "telegram": {
                "level": telegram.level.value,
                "headline": telegram.headline,
                "advice": list(telegram.advice),
                "items": [
                    {
                        "name": item.center.name,
                        "address": item.center.address,
                        "state": _telegram_state(item),
                        "text": _telegram_text(item),
                    }
                    for item in telegram.servers
                ],
            } if telegram else None,
            "network": network,
            "problems": problems,
            "working": working,
            "spoofed_hosts": spoofed,
            "reference": run.reference_report(),
            "ipv6": {"state": ipv6.code, "text": ipv6.text} if ipv6 is not None else None,
            "dns_servers": dns_servers,
            "filter": filter_place,
            "system": [
                {"key": item.key, "title": item.title, "level": item.level, "text": item.text, "advice": item.advice}
                for item in system
            ],
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
