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

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from datetime import datetime

from diagnostics import (
    block_cause,
    block_kind,
    net_access,
    protocol_probe,
    quic_probe,
    registry,
    sections,
    system_state,
    telegram_check,
    udp_burst,
    volume_probe,
)
from diagnostics import problems as problem_rules
from diagnostics import report_text
from diagnostics.limits import (
    DISCOVERY_TIMEOUT,
    DNS_ATTEMPTS,
    REACH_ADDRESSES,
    REGISTRY_WAIT_S,
    RECHECK_AT_ONCE,
    RECHECK_NEEDS_S,
    RECHECK_SITES,
    RETRY_PAUSE_S,
    RUN_DEADLINE,
    RUN_DEADLINE_ALL,
    RUN_DEADLINE_FULL,
    SITES_AT_ONCE,
    SOURCE_HOSTS,
    SOURCE_REFERENCE,
    SOURCE_SYSTEM,
    VIDEO_SERVERS,
)
from diagnostics.run_context import RECHECK_OPENED, RECHECK_SAME
from diagnostics.run_context import Probe as _Probe
from diagnostics.run_context import Run as _Run
from diagnostics.run_context import Stopped as _Stopped
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
    CONNECT_TIMEOUT,
    KIND_CONNECT,
    KIND_CANCELLED,
    KIND_CERT,
    ProbeResult,
)
from diagnostics.verdict import (
    FREEZE_MAX_BYTES,
    DnsState,
    Level,
    ReachState,
    ServiceVerdict,
    TargetOutcome,
    judge_dns,
    judge_reach,
    summarize_service,
)
from utils.bypass_tools import running_bypass_tools
from utils.dns_reference import REFERENCE_RESOLVERS
from utils.dns_wire import TYPE_A, TYPE_AAAA
from utils.windows_dns_query import (
    DNS_STATUS_NAME_ERROR,
    ERROR_CANCELLED,
    DnsAnswer,
)

__all__ = [
    "PROGRESS_STEPS",
    "run_blockcheck",
    "run_dns_check",
]


Emit = Callable[[str], None]
ShouldStop = Callable[[], bool]


# Сколько тела ответа читать, чтобы заметить обрыв после ~16 КБ (ТСПУ режет
# соединение с зарубежными CDN ровно на этом объёме).
BODY_PROBE_BYTES = 64 * 1024

DNS_TYPE_A = TYPE_A
DNS_TYPE_AAAA = TYPE_AAAA

_WATCH_PAGE = "/watch?v=jNQXAC9IVRw&hl=en"
_WATCH_PAGE_MAX_BYTES = 2_000_000


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
# несколько HTTPS-запросов, четыре пробы уточнения причины, три пакета QUIC и пять
# потоков на TLS 1.2 / TLS 1.3 / «как Chrome» / HTTP. Задачи ждут друг
# друга внутри одного пула, поэтому
# нехватка потоков — это не «медленнее», а взаимная блокировка.
_WORKERS_PER_TARGET = 28 + 2 * len(REFERENCE_RESOLVERS)


# ---------------------------------------------------------------------------
# Отдельные проверки
# ---------------------------------------------------------------------------


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
    candidates = list(net_access.hosts_ipv4(YOUTUBE_HOST))
    if not candidates:
        candidates = list(net_access.system_ipv4(run, YOUTUBE_HOST).ips)
    if not candidates:
        candidates = list(net_access.doh_lookup(run, YOUTUBE_HOST)[1])

    for ip in candidates[:1]:
        result = net_access.fetch(
            run, YOUTUBE_HOST, ip, _WATCH_PAGE, timeout=DISCOVERY_TIMEOUT, read_limit=_WATCH_PAGE_MAX_BYTES, body_done=_done
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
        return net_access.get(run, probe.host, ip, probe.target.path, read_limit=read_limit)

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
            if all(item.kind == KIND_CONNECT for item in attempts) and not run.dns_cancelled():
                # Все адреса пробовались в одну секунду: короткий сбой сети задел бы их
                # разом. Ещё одна попытка после паузы отделяет сбой от блокировки.
                _pause(run, RETRY_PAUSE_S)
                if not run.dns_cancelled():
                    attempts.append(_one(order[0]))
        elif attempts[0].kind != KIND_CERT:
            _pause(run, RETRY_PAUSE_S)
            if not run.dns_cancelled():
                attempts.append(_one(order[0]))

    probe.attempts = len(attempts)
    probe.tried = tuple((item.ip, item.kind) for item in attempts if item.kind != KIND_CANCELLED)
    probe.tried_silent = bool(probe.tried) and all(
        item.kind == KIND_CONNECT and item.connect_fail == CONNECT_TIMEOUT
        for item in attempts
        if item.kind != KIND_CANCELLED
    )
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
        run.submit(net_access.system_ipv4, run, host)
        for _attempt in range(DNS_ATTEMPTS)
    ]
    doh_future = run.submit(net_access.doh_lookup, run, host)
    doh6_future = run.submit(net_access.doh_lookup, run, host, DNS_TYPE_AAAA) if full else None
    probe.hosts_ips = net_access.hosts_ipv4(host)
    probe.dns, probe.dns_nxdomain = _merge_dns_answers([future.result() for future in dns_futures])

    local_ips = probe.hosts_ips or probe.dns.ips
    local_future: Future | None = None
    if full and local_ips:
        # В полной проверке запрос к адресу из DNS нужен всегда — не ждём эталон.
        local_future = run.submit(net_access.get, run, host, local_ips[0], target.path, read_limit=read_limit)

    probe.reference_ok, probe.reference_ips = doh_future.result()
    reference = set(probe.reference_ips)
    matches = bool(set(probe.dns.ips) & reference)
    if local_future is None and local_ips and (probe.hosts_ips or not matches):
        local_future = run.submit(net_access.get, run, host, local_ips[0], target.path)
    # DNS дал и адрес из эталона, и другой. У CDN так бывает, но так же
    # выглядит подмена «через раз»: сертификат по другому адресу решает.
    suspects = [ip for ip in probe.dns.ips if ip not in reference] if (matches and not probe.hosts_ips) else []
    suspect_future: Future | None = None
    if suspects and not (local_ips and local_ips[0] == suspects[0]):
        suspect_future = run.submit(net_access.get, run, host, suspects[0], target.path)
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


# ---------------------------------------------------------------------------
# Текст отчёта
# ---------------------------------------------------------------------------


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
    lane = run.lane(SITES_AT_ONCE)

    def _probe(*args, **kwargs) -> _Probe:
        try:
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
            planned.append((key, target, lane.submit(_probe, run, target, key, full=full, volume=volume)))

    # Сначала дожидаемся всех, затем перепроверяем упавшие, и только потом печатаем:
    # перепроверка может изменить результат.
    done_probes: list[tuple[str, Target, _Probe | None, str]] = []
    for key, target, future in planned:
        try:
            done_probes.append((key, target, run.wait(future), ""))
        except _Stopped:
            raise
        except Exception as exc:
            done_probes.append((key, target, None, str(exc)))
    if full:
        done_probes = _recheck_failed(run, services, done_probes)

    collected: dict[str, list[_Probe]] = {key: [] for key in services}
    current_service = ""
    for key, target, probe, error in done_probes:
        if key != current_service:
            current_service = key
            emit("")
            emit(f"━━━━━━━━ {services[key].label} ━━━━━━━━")
        if probe is None:
            emit(f"❔ {target.host} — {target.purpose}: проверка не выполнилась ({error})")
            continue
        collected[key].append(probe)
        for line in report_text.probe_lines(probe, full=full):
            emit(line)
    emit("")
    return collected


def _recheck_failed(run: _Run, services: dict[str, Service], done_probes: list) -> list:
    """Сайты, которые не открылись, проверяются ещё раз — поодиночке, когда залп закончился.

    Первая проверка идёт по десяткам сайтов разом. Если сбой дала сама эта
    нагрузка (роутер или провайдер не успели), при спокойной повторной проверке
    сайт откроется. Не открылся снова — сбой настоящий.
    """
    failed = [
        index
        for index, (_key, _target, probe, _error) in enumerate(done_probes)
        if probe is not None and probe.reach_state in (ReachState.IP_BLOCK, ReachState.DPI)
    ][:RECHECK_SITES]
    if not failed or run.dns_cancelled() or run.deadline - time.monotonic() < RECHECK_NEEDS_S:
        return done_probes
    lane = run.lane(RECHECK_AT_ONCE)

    def _again(index: int) -> _Probe | None:
        key, target, first, _error = done_probes[index]
        if run.dns_cancelled():
            return None
        again = _probe_target(run, target, key, full=True, volume=not services[key].control)
        if again.reach is None or again.reach.kind == KIND_CANCELLED:
            # Повтор не успел — остаётся первый результат, без пометки.
            return None
        again.rechecked = RECHECK_OPENED if again.reach_state == ReachState.OK else RECHECK_SAME
        if first.reach_state != again.reach_state and again.reach_state != ReachState.OK:
            # Сбой другого вида: берём свежий, но «подтверждённым» его не считаем.
            again.rechecked = ""
        return again

    futures = [(index, lane.submit(_again, index)) for index in failed]
    result = list(done_probes)
    for index, future in futures:
        try:
            again = run.wait(future)
        except _Stopped:
            raise
        except Exception:
            continue
        if again is not None:
            key, target, _first, error = result[index]
            result[index] = (key, target, again, error)
    return result


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


def _wait_plain(future: Future):
    return future.result()


def _attempt(emit: Emit, title: str, call: Callable[[], object]):
    """Раздел, который идёт прямо сейчас. Его сбой оставляет строку в отчёте и не роняет проверку."""
    try:
        return call()
    except _Stopped:
        raise
    except Exception as exc:
        emit(f"❔ {title}: проверка не выполнилась ({exc})")
        return None


def _settle(run: _Run, future: Future | None, emit: Emit, title: str):
    """Итог раздела, который шёл в фоне. None — раздел не запускали или он сломался.

    Сбой одного раздела не должен ронять всю проверку: о нём остаётся строка
    в отчёте, остальные разделы показываются как обычно.
    """
    if future is None:
        return None
    try:
        return run.wait(future)
    except _Stopped:
        raise
    except Exception as exc:
        emit(f"❔ {title}: проверка не выполнилась ({exc})")
        return None


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
    from diagnostics.freeze_check import EVERY_AT_ONCE, check_freeze, summarize_freeze
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
        environment = sections.environment_lines()
        for line in environment:
            emit(line)
        zapret_running, zapret_line = sections.zapret_status()
        emit(zapret_line)
        other_tools = running_bypass_tools()
        if other_tools:
            emit(
                f"ℹ️ Запущены другие программы обхода или VPN: {', '.join(other_tools)}. "
                "Если они сейчас включены, результат показывает сеть вместе с ними, а не «чистую» сеть провайдера."
            )
        emit("⏳ Проверяем так же, как браузер: TLS 1.3, правильные адреса сайтов…")

        # Звонки и обрыв на 16 КБ проверяются всегда: режим меняет только список сайтов.
        voice_future = run.submit(check_voice, run.submit, _wait_plain)
        burst_future = run.submit(sections.check_udp_burst, run)
        freeze_future = run.submit(
            check_freeze,
            run.lane(EVERY_AT_ONCE).submit,
            _wait_plain,
            lambda host, path: sections.download(run, host, path),
            lambda host, path: sections.upload(run, host, path),
            every=full,
            on_server=lambda _server, done, total: step(STEP_HOSTINGS, done, total),
        )

        network_future = run.submit(sections.check_network, run, other_tools)
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
        ipv6_future = run.submit(sections.check_ipv6, run)
        system_future = run.submit(sections.check_system, run, services, zapret_running)

        collected = _run_probes(
            run, services, full=True, emit=emit, on_done=lambda done, total: step(STEP_SITES, done, total)
        )
        # Тяжёлое идёт по очереди, а не разом с сайтами: проверка DNS-серверов — это сотни
        # своих соединений, сборка списка реестра РКН — секунды счёта. Вместе с сайтами
        # они забирали процессор у окна программы, и оно замирало.
        dns_future = run.submit(check_dns_servers, should_stop=run.dns_cancelled) if full and check_dns_servers else None
        registry_wait = sections.start_registry()

        ipv6 = _settle(run, ipv6_future, emit, "IPv6")
        step(STEP_IPV6)
        if ipv6 is not None:
            emit("━━━━━━━━ IPv6 ━━━━━━━━")
            emit(f"{sections.IPV6_ICON[ipv6.code]} IPv6 {ipv6.text}")

        system: tuple[system_state.SystemItem, ...] = tuple(_settle(run, system_future, emit, "Состояние системы") or ())
        step(STEP_SYSTEM)
        if system:
            emit("")
            emit("━━━━━━━━ Состояние системы ━━━━━━━━")
            for item in system:
                emit(f"{sections.SYSTEM_ICON[item.level]} {item.title}: {item.text}")

        voice_facts = _settle(run, voice_future, emit, "Голосовые серверы")
        voice = summarize_voice(voice_facts) if voice_facts is not None else None
        step(STEP_VOICE)
        if voice is not None:
            for line in report_text.section_lines(
                "Голосовые звонки (UDP)",
                voice,
                [("✅" if item.answered else ("❌" if item.decided else "❔"), item.name, item.text) for item in voice.servers],
            ):
                emit(line)

        burst = _settle(run, burst_future, emit, "Серия пакетов UDP")
        for line in udp_burst.lines(burst):
            emit(line)

        telegram_facts = _settle(run, telegram_future, emit, "Дата-центры Telegram")
        telegram = (
            telegram_check.summarize_telegram(telegram_facts, zapret_running=zapret_running)
            if telegram_facts is not None
            else None
        )
        if telegram is not None:
            marks = {"ok": "✅", "fail": "❌", "unknown": "❔"}
            for line in report_text.section_lines(
                "Telegram: дата-центры",
                telegram,
                [
                    (marks[report_text.telegram_state(item)], f"{item.center.name} ({item.center.address})", report_text.telegram_text(item))
                    for item in telegram.servers
                ],
            ):
                emit(line)

        network = _settle(run, network_future, emit, "Ваша сеть")
        if network is not None:
            emit("")
            emit("━━━━━━━━ Ваша сеть ━━━━━━━━")
            for item in network["lines"]:
                emit(f"{'⚠️' if item['state'] == 'warn' else 'ℹ️'} {item['name']}: {item['text']}")

        freeze_facts = _settle(run, freeze_future, emit, "Обрыв на 16–20 КБ")
        freeze = summarize_freeze(freeze_facts, zapret_running=zapret_running) if freeze_facts is not None else None
        if freeze is not None:
            marks = {"ok": "✅", "freeze": "❌", "unknown": "❔"}
            for line in report_text.section_lines(
                "Обрыв на 16–20 КБ",
                freeze,
                [(marks[item.state.value], item.name, item.text) for item in freeze.servers],
            ):
                emit(line)

        dns_servers = sections.finish_dns_servers(run, dns_future, emit) if dns_future is not None else None
        if full:
            step(STEP_DNS_SERVERS)
        filter_place = (
            sections.find_filter_place(
                run,
                collected,
                emit,
                zapret_running=zapret_running,
                other_tools=other_tools,
                own_asn=str((network or {}).get("asn") or ""),
            )
            if full
            else None
        )
        extra = full and not run.dns_cancelled()
        habits = _attempt(emit, "Как работает фильтр", lambda: sections.check_filter_habits(run, collected, emit)) if extra else None
        if full:
            step(STEP_FILTER)
        speed = _attempt(emit, "Скорость", lambda: sections.check_speed(run, emit)) if full and not run.dns_cancelled() else None

        verdicts = {
            key: _service_verdict(service, collected[key], zapret_running=zapret_running)
            for key, service in services.items()
        }
        problems, working, spoofed = problem_rules.collect_problems(
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
        problems += problem_rules.burst_problems(burst)
        if dns_servers is not None:
            for finding in dns_servers["findings"]:
                level = sections.DNS_FINDING_LEVEL.get(finding["level"])
                if level is not None:
                    problems.append(
                        problem_rules.problem(
                            level,
                            finding["text"],
                            action="dns",
                            kind=block_kind.KIND_DNS,
                            parts=sections.dns_finding_parts(finding),
                        )
                    )
        problems.sort(key=lambda item: problem_rules.LEVEL_ORDER.get(Level(item["level"]), 9))

        services_report = report_text.services_report(services, verdicts, collected)
        registry_index = registry_wait(REGISTRY_WAIT_S)
        registry.annotate(services_report, registry_index)
        for line in registry.lines(services_report, registry_index):
            emit(line)

        elapsed = time.monotonic() - started
        for line in report_text.summary_lines(
            problems, working, timed_out=run.timed_out, deadline=run.deadline_seconds, elapsed=elapsed
        ):
            emit(line)

        return {
            "scope": scope,
            "services": services_report,
            "registry": registry.summary(registry_index),
            "voice": report_text.voice_report(voice, burst),
            "freeze": report_text.freeze_report(freeze),
            "telegram": report_text.telegram_report(telegram),
            "network": network,
            "speed": speed,
            "problems": problems,
            "working": working,
            "spoofed_hosts": spoofed,
            "reference": run.reference_report(),
            "ipv6": {"state": ipv6.code, "text": ipv6.text} if ipv6 is not None else None,
            "dns_servers": dns_servers,
            "filter": filter_place,
            "habits": habits,
            "system": report_text.system_report(system),
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


def run_dns_check(*, emit: Emit, should_stop: ShouldStop | None = None) -> dict:
    """Проверка DNS подмены для вкладки «Проверка DNS подмены»."""
    services = build_services(SCOPE_MAIN)
    targets_count = sum(len(service.targets) for service in services.values())
    run = _Run(should_stop, workers=targets_count * _WORKERS_PER_TARGET, deadline=RUN_DEADLINE)
    started = time.monotonic()
    try:
        emit("🔍 ПРОВЕРКА DNS ПОДМЕНЫ")
        for line in sections.environment_lines():
            emit(line)
        emit(
            "Как проверяем: адрес от DNS системы сравниваем с эталоном по DNS-over-HTTPS. "
            "Если адреса разные, решает сертификат сервера: подлинный — подмены нет."
        )

        collected = _run_probes(run, services, full=False, emit=emit)

        spoofed = _has_spoofing(collected)
        emit("━━━━━━━━ 📊 Итог ━━━━━━━━")
        if run.timed_out:
            emit(report_text.timed_out_line(run.deadline_seconds))
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
        for item in problem_rules.blocked_references(reference):
            emit(f"⚠️ {problem_rules.reference_text(item)}")
            emit(f"   👉 {problem_rules.ADVICE_BLOCKED_REFERENCE}")
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
