"""Движок диагностики соединения и проверки DNS подмены.

Как он устроен
--------------
1. Для каждого адреса из списка сервиса запускаются параллельно:
   DNS-запрос к серверам системы (без кэша и hosts), эталонный запрос через
   DNS-over-HTTPS к 1.1.1.1 и 8.8.8.8 (по IP, чтобы эталон не зависел от
   проверяемого DNS), HTTPS-запрос к самому сайту, TCP-подключение к порту
   443, при необходимости ping и проверка TLS 1.2 / 1.3.
2. Все сетевые вызовы идут через Windows API (dnsapi, WinHTTP, ICMP) и
   ограничены по времени; кнопка «Стоп» снимает их сразу.
3. Результаты печатаются в постоянном порядке: блок адреса выводится, как
   только готовы его проверки и все блоки перед ним.
4. Выводы делает ``diagnostics.verdict`` — здесь только сбор фактов и текст.
"""

from __future__ import annotations

import json
import socket
import sys
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote

from diagnostics.verdict import (
    ChannelState,
    DnsJudgement,
    DnsState,
    describe_channel,
    judge_channel,
    judge_dns,
)
from utils.windows_dns_query import (
    DnsAnswer,
    hosts_file_ipv4,
    query_ipv4,
    system_dns_servers,
)
from utils.windows_http import (
    KIND_OK,
    TLS_1_2,
    TLS_1_3,
    HttpCancel,
    HttpsResult,
    https_request,
    is_tls13_supported,
)

__all__ = [
    "SERVICES",
    "Target",
    "run_connection_test",
    "run_dns_check",
]


Emit = Callable[[str], None]
ShouldStop = Callable[[], bool]

DNS_TIMEOUT = 4.0
DOH_TIMEOUT = 5.0
HTTPS_TIMEOUT = 6.0
TCP_TIMEOUT = 4.0
TCP_MAX_ADDRESSES = 6
PING_TIMEOUT_MS = 1500
DISCOVERY_TIMEOUT = 6.0
# Верхняя граница на всю проверку: дальше недопроверенное помечается как
# «нет ответа», а не подвешивает окно.
RUN_DEADLINE = 25.0
_TIMED_OUT_LINE = (
    f"⚠️ Часть проверок не уложилась в {RUN_DEADLINE:.0f} с и была прервана — "
    "их результат неизвестен."
)

# Сколько тела ответа читать, чтобы заметить обрыв после ~16 КБ (ТСПУ режет
# соединение с зарубежными CDN ровно на этом объёме).
BODY_PROBE_BYTES = 64 * 1024
_FREEZE_MIN_BYTES = 14_000
_FREEZE_MAX_BYTES = 24_000

_DOH_ENDPOINTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("1.1.1.1", "/dns-query?name={name}&type=A", ("Accept: application/dns-json",)),
    ("8.8.8.8", "/resolve?name={name}&type=A", ()),
)

_BROWSER_HEADERS = (
    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Accept-Language: en-US,en;q=0.8",
)
_WATCH_PAGE = "/watch?v=jNQXAC9IVRw&hl=en"
_WATCH_PAGE_MAX_BYTES = 2_000_000
GOOGLEVIDEO_FALLBACK_HOST = "redirector.googlevideo.com"


@dataclass(frozen=True, slots=True)
class Target:
    host: str
    purpose: str
    path: str = "/"
    read_body: bool = False
    tls_versions: bool = False
    ping: bool = False
    # Хост видеосервера каждый раз узнаётся у YouTube: он свой у каждого
    # провайдера и меняется со временем.
    discover_googlevideo: bool = False


SERVICES: dict[str, tuple[str, tuple[Target, ...]]] = {
    "discord": (
        "Discord",
        (
            Target("discord.com", "сайт и вход", read_body=True, tls_versions=True, ping=True),
            Target("gateway.discord.gg", "чат и статусы"),
            Target("cdn.discordapp.com", "картинки и файлы"),
        ),
    ),
    "youtube": (
        "YouTube",
        (
            Target("www.youtube.com", "сайт", read_body=True, tls_versions=True, ping=True),
            Target("i.ytimg.com", "превью видео", path="/generate_204"),
            Target(
                GOOGLEVIDEO_FALLBACK_HOST,
                "видеосервер",
                path="/generate_204",
                tls_versions=True,
                discover_googlevideo=True,
            ),
        ),
    ),
}


class _Stopped(Exception):
    pass


@dataclass(slots=True)
class _TcpResult:
    ok: bool
    ip: str
    elapsed_ms: float = 0.0


@dataclass(slots=True)
class _Probe:
    target: Target
    service: str
    host: str
    discovery_note: str = ""
    dns: DnsAnswer = field(default_factory=DnsAnswer)
    hosts_ips: tuple[str, ...] = ()
    reference_ips: tuple[str, ...] = ()
    reference_ok: bool = False
    https: HttpsResult = field(default_factory=HttpsResult)
    tcp: tuple[_TcpResult, ...] = ()
    tls12: HttpsResult | None = None
    tls13: HttpsResult | None = None
    ping: object | None = None
    judgement: DnsJudgement | None = None
    channel: ChannelState = ChannelState.UNKNOWN


class _Run:
    """Общие для одного прогона пул потоков, дедлайн и отмена.

    «Стоп» пользователя и истёкший общий лимит — разные вещи: после «Стопа»
    отчёт не печатается, а по лимиту незавершённые запросы снимаются, и отчёт
    выводится с пометкой, что часть проверок не успела.
    """

    def __init__(self, should_stop: ShouldStop | None, *, workers: int) -> None:
        self._should_stop = should_stop
        self._user_stopped = False
        self.timed_out = False
        self.cancel = HttpCancel()
        self.deadline = time.monotonic() + RUN_DEADLINE
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="diag")

    def stopped(self) -> bool:
        if self._user_stopped:
            return True
        try:
            if self._should_stop is not None and self._should_stop():
                self._user_stopped = True
                self.cancel.cancel()
        except Exception:
            return False
        return self._user_stopped

    def expired(self) -> bool:
        if time.monotonic() < self.deadline:
            return False
        if not self.timed_out:
            self.timed_out = True
            # Всё, что ещё висит, снимаем: WinHTTP и DNS вернутся сразу.
            self.cancel.cancel()
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
        self.cancel.cancel()
        self.pool.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# Отдельные проверки
# ---------------------------------------------------------------------------


def _doh_lookup(run: _Run, host: str) -> tuple[bool, tuple[str, ...]]:
    """Эталонные адреса по DNS-over-HTTPS. (ответил ли хоть один, адреса)."""

    def _one(endpoint: tuple[str, str, tuple[str, ...]]) -> tuple[bool, list[str]]:
        server, path, headers = endpoint
        result = https_request(
            server,
            path.format(name=quote(host)),
            headers=headers,
            timeout=DOH_TIMEOUT,
            max_body=64 * 1024,
            cancel=run.cancel,
        )
        if result.status != 200 or not result.body:
            return False, []
        try:
            data = json.loads(result.body.decode("utf-8", errors="ignore"))
        except ValueError:
            return False, []
        answers = [
            str(item.get("data") or "")
            for item in data.get("Answer") or []
            if isinstance(item, dict) and item.get("type") == 1
        ]
        return data.get("Status") == 0 or bool(answers), answers

    futures = [run.submit(_one, endpoint) for endpoint in _DOH_ENDPOINTS]
    answered = False
    ips: list[str] = []
    for future in futures:
        ok, answers = future.result()
        answered = answered or ok
        for ip in answers:
            if ip and ip not in ips:
                ips.append(ip)
    return answered, tuple(ips)


def _tcp_connect(ip: str, timeout: float) -> _TcpResult:
    started = time.perf_counter()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        code = sock.connect_ex((ip, 443))
    except OSError:
        code = -1
    finally:
        sock.close()
    return _TcpResult(code == 0, ip, (time.perf_counter() - started) * 1000)


def _ping(host: str, ip: str):
    from utils.windows_icmp import ping_ipv4_host_winapi

    return ping_ipv4_host_winapi(host, count=2, timeout_ms=PING_TIMEOUT_MS, resolved_ip=ip)


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

    result = https_request(
        "www.youtube.com",
        _WATCH_PAGE,
        headers=_BROWSER_HEADERS,
        timeout=DISCOVERY_TIMEOUT,
        max_body=_WATCH_PAGE_MAX_BYTES,
        body_done=_done,
        cancel=run.cancel,
    )
    if found:
        return found[0], "адрес видеосервера получен от YouTube"
    if result.kind != KIND_OK:
        return GOOGLEVIDEO_FALLBACK_HOST, "YouTube не открылся, поэтому проверяем общий адрес видеосерверов"
    return GOOGLEVIDEO_FALLBACK_HOST, "YouTube не назвал видеосервер, поэтому проверяем общий адрес"


def _probe_target(run: _Run, target: Target, service: str, *, full: bool) -> _Probe:
    host = target.host
    discovery_note = ""
    if target.discover_googlevideo:
        host, discovery_note = _discover_googlevideo(run)

    probe = _Probe(target=target, service=service, host=host, discovery_note=discovery_note)

    dns_future = run.submit(query_ipv4, host, timeout=DNS_TIMEOUT, cancelled=run.dns_cancelled)
    doh_future = run.submit(_doh_lookup, run, host)
    https_future = run.submit(
        https_request,
        host,
        target.path,
        timeout=HTTPS_TIMEOUT,
        max_body=BODY_PROBE_BYTES if (full and target.read_body) else 0,
        cancel=run.cancel,
    )
    tls_futures: dict[str, Future] = {}
    if full and target.tls_versions:
        tls_futures["tls12"] = run.submit(
            https_request, host, target.path, timeout=HTTPS_TIMEOUT, tls_protocols=TLS_1_2, cancel=run.cancel
        )
        if is_tls13_supported():
            tls_futures["tls13"] = run.submit(
                https_request, host, target.path, timeout=HTTPS_TIMEOUT, tls_protocols=TLS_1_3, cancel=run.cancel
            )

    probe.hosts_ips = hosts_file_ipv4(host)
    probe.dns = dns_future.result()

    # TCP — ко всем адресам, которыми реально пользуются программы: провайдер
    # нередко блокирует только часть адресов сайта.
    connect_ips = (probe.hosts_ips or probe.dns.ips)[:TCP_MAX_ADDRESSES]
    tcp_futures = [run.submit(_tcp_connect, ip, TCP_TIMEOUT) for ip in connect_ips] if full else []
    ping_future = run.submit(_ping, host, connect_ips[0]) if (full and target.ping and connect_ips) else None

    probe.reference_ok, probe.reference_ips = doh_future.result()
    probe.https = https_future.result()
    probe.tls12 = tls_futures["tls12"].result() if "tls12" in tls_futures else None
    probe.tls13 = tls_futures["tls13"].result() if "tls13" in tls_futures else None
    probe.tcp = tuple(future.result() for future in tcp_futures)
    probe.ping = ping_future.result() if ping_future else None

    probe.judgement = judge_dns(
        system_ips=probe.dns.ips,
        system_status=probe.dns.status,
        reference_ips=probe.reference_ips,
        hosts_ips=probe.hosts_ips,
        https_kind=probe.https.kind,
        https_cert_problem=probe.https.cert_problem,
        https_remote_ip=probe.https.remote_ip,
    )
    probe.channel = judge_channel(
        https_kind=probe.https.kind,
        tcp_ok=any(item.ok for item in probe.tcp) if probe.tcp else None,
    )
    if probe.channel == ChannelState.OK and _is_body_freeze(probe.https):
        probe.channel = ChannelState.DPI
    return probe


def _is_body_freeze(result: HttpsResult) -> bool:
    """Ответ пришёл, но данные оборвались на 14–24 КБ — почерк ТСПУ."""
    return bool(result.read_error) and _FREEZE_MIN_BYTES <= len(result.body) <= _FREEZE_MAX_BYTES


# ---------------------------------------------------------------------------
# Текст отчёта
# ---------------------------------------------------------------------------


def _ips_text(ips: tuple[str, ...], limit: int = 3) -> str:
    if not ips:
        return "—"
    shown = ", ".join(ips[:limit])
    return f"{shown} и ещё {len(ips) - limit}" if len(ips) > limit else shown


def _dns_lines(probe: _Probe) -> list[str]:
    judgement = probe.judgement
    lines: list[str] = []
    if probe.hosts_ips:
        lines.append(f"  Файл hosts: {_ips_text(probe.hosts_ips)}")
    if probe.dns.ips:
        lines.append(f"  DNS системы: {_ips_text(probe.dns.ips)}")
    else:
        lines.append(f"  DNS системы: нет адреса ({probe.dns.detail})")
    if probe.reference_ips:
        lines.append(f"  Эталон (DNS-over-HTTPS): {_ips_text(probe.reference_ips)}")
    elif not probe.reference_ok:
        lines.append("  Эталон (DNS-over-HTTPS): недоступен")
    if judgement is not None:
        icon = {
            DnsState.OK: "✅",
            DnsState.SPOOFED: "❌",
            DnsState.LOCAL: "ℹ️",
            DnsState.UNKNOWN: "⚠️",
        }[judgement.state]
        lines.append(f"  {icon} DNS: {judgement.reason}")
    return lines


def _https_line(probe: _Probe) -> str:
    result = probe.https
    if result.kind == KIND_OK:
        where = f", сервер {result.remote_ip}" if result.remote_ip else ""
        if _is_body_freeze(result):
            return (
                f"  ❌ HTTPS: ответ начал приходить, но оборвался на {len(result.body) // 1024} КБ — "
                "так ТСПУ режет соединения с зарубежными серверами"
            )
        return f"  ✅ HTTPS: сервер ответил (код {result.status}, {result.elapsed_ms:.0f} мс{where})"
    return f"  ❌ HTTPS: {describe_channel(result.kind, cert_problem=result.cert_problem, timeout=HTTPS_TIMEOUT)}"


def _tls_line(probe: _Probe) -> str | None:
    if probe.tls12 is None:
        return None

    def _one(result: HttpsResult | None) -> str:
        if result is None:
            return "не поддерживается этой версией Windows"
        if result.kind == KIND_OK:
            return "✓ работает"
        return f"✗ {describe_channel(result.kind, cert_problem=result.cert_problem, timeout=HTTPS_TIMEOUT)}"

    return f"  🔐 TLS 1.2: {_one(probe.tls12)} · TLS 1.3: {_one(probe.tls13)}"


def _tcp_line(probe: _Probe) -> str | None:
    if not probe.tcp:
        return None
    alive = [item for item in probe.tcp if item.ok]
    dead = [item.ip for item in probe.tcp if not item.ok]
    total = len(probe.tcp)
    if not alive:
        return f"  ❌ TCP 443: ни один адрес не отвечает ({total}) — адрес заблокирован или нет сети"
    fastest = min(item.elapsed_ms for item in alive)
    if not dead:
        if total == 1:
            return f"  ✅ TCP 443: адрес {alive[0].ip} отвечает за {fastest:.0f} мс"
        return f"  ✅ TCP 443: отвечают все адреса ({total}), быстрейший за {fastest:.0f} мс"
    return (
        f"  ⚠️ TCP 443: отвечают {len(alive)} из {total} адресов, не отвечает {_ips_text(tuple(dead))} — "
        "часть серверов недоступна, программы обычно переключаются на рабочий"
    )


def _ping_line(probe: _Probe) -> str | None:
    result = probe.ping
    if result is None:
        return None
    received = int(getattr(result, "received", 0) or 0)
    sent = int(getattr(result, "sent", 0) or 0)
    ip = getattr(result, "resolved_ip", "") or ""
    if received:
        average = getattr(result, "average_ms", None)
        delay = f", {average:.0f} мс" if average is not None else ""
        return f"  📶 Ping {ip}: ответил на {received} из {sent}{delay}"
    if getattr(result, "error_code", "") == "TIMEOUT":
        return f"  📶 Ping {ip}: не отвечает (многие серверы игнорируют ping, на работу это не влияет)"
    return f"  📶 Ping {ip}: не удалось выполнить ({getattr(result, 'detail', '') or 'ошибка ICMP'})"


def _probe_lines(probe: _Probe, *, full: bool) -> list[str]:
    lines = [f"{probe.host} — {probe.target.purpose}"]
    if probe.discovery_note:
        lines.append(f"  ℹ️ {probe.discovery_note}")
    lines.extend(_dns_lines(probe))
    if full:
        for line in (_tcp_line(probe), _https_line(probe), _tls_line(probe), _ping_line(probe)):
            if line:
                lines.append(line)
    elif probe.https.kind != KIND_OK and probe.judgement and probe.judgement.state == DnsState.UNKNOWN:
        lines.append(f"  ℹ️ Проверка сертификата: {describe_channel(probe.https.kind, cert_problem=probe.https.cert_problem, timeout=HTTPS_TIMEOUT)}")
    return lines


def _service_summary(label: str, probes: list[_Probe], *, zapret_running: bool | None) -> str:
    states = [probe.judgement.state for probe in probes if probe.judgement]
    channels = [probe.channel for probe in probes]
    if DnsState.SPOOFED in states:
        return (
            f"❌ {label}: DNS подменяет адреса. Откройте «Настройка DNS» и включите DNS "
            "с шифрованием (DoH) — провайдер не сможет его перехватить."
        )
    if ChannelState.CERT in channels:
        return (
            f"❌ {label}: вместо настоящего сервера отвечает чужой — трафик перехватывает "
            "антивирус, прокси или провайдер."
        )
    if ChannelState.DPI in channels:
        if zapret_running:
            return (
                f"❌ {label}: соединение режет DPI провайдера. Zapret запущен, но текущая стратегия "
                "не помогает — подберите другую."
            )
        return f"❌ {label}: соединение режет DPI провайдера. Запустите Zapret."
    if ChannelState.IP_BLOCK in channels:
        return (
            f"❌ {label}: серверы недоступны по адресу — блокировка по IP или нет интернета. "
            "Zapret помогает в таком случае не всегда."
        )
    if all(channel == ChannelState.OK for channel in channels):
        return f"✅ {label}: всё работает."
    return f"⚠️ {label}: часть проверок не дала ответа — повторите проверку."


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


def _selected_services(test_type: str) -> list[str]:
    normalized = str(test_type or "").strip().lower()
    if normalized in SERVICES:
        return [normalized]
    return list(SERVICES)


def _run_probes(
    run: _Run,
    services: list[str],
    *,
    full: bool,
    emit: Emit,
) -> dict[str, list[_Probe]]:
    """Запускает все цели сразу и печатает их блоки по порядку."""
    planned: list[tuple[str, Target, Future]] = []
    for service in services:
        _label, targets = SERVICES[service]
        for target in targets:
            if not full and target.discover_googlevideo:
                target = Target(target.host, target.purpose, target.path)
            planned.append((service, target, run.submit(_probe_target, run, target, service, full=full)))

    collected: dict[str, list[_Probe]] = {service: [] for service in services}
    current_service = ""
    for service, target, future in planned:
        if service != current_service:
            current_service = service
            emit("")
            emit(f"━━━━━━━━ {SERVICES[service][0]} ━━━━━━━━")
        try:
            probe = run.wait(future)
        except _Stopped:
            raise
        except Exception as exc:
            emit(f"{target.host}: ❌ проверка не выполнилась ({exc})")
            continue
        collected[service].append(probe)
        for line in _probe_lines(probe, full=full):
            emit(line)
        emit("")
    return collected


def run_connection_test(test_type: str, *, emit: Emit, should_stop: ShouldStop | None = None) -> dict:
    """Полная диагностика вкладки «Диагностика». Возвращает краткую сводку."""
    services = _selected_services(test_type)
    targets_count = sum(len(SERVICES[service][1]) for service in services)
    run = _Run(should_stop, workers=max(8, targets_count * 8))
    started = time.monotonic()
    try:
        emit(f"🔍 Диагностика соединения — {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
        for line in _environment_lines():
            emit(line)
        zapret_running, zapret_line = _zapret_status()
        emit(zapret_line)
        emit(f"⏳ Проверяем {targets_count} адресов одновременно, это займёт несколько секунд…")

        collected = _run_probes(run, services, full=True, emit=emit)

        emit("━━━━━━━━ 📊 Итог ━━━━━━━━")
        if run.timed_out:
            emit(_TIMED_OUT_LINE)
        summary: dict[str, str] = {}
        for service in services:
            line = _service_summary(SERVICES[service][0], collected[service], zapret_running=zapret_running)
            summary[service] = line
            emit(line)
        emit(f"Проверка заняла {time.monotonic() - started:.1f} с.")
        return {
            "summary": summary,
            "dns_poisoning_detected": _has_spoofing(collected),
        }
    except _Stopped:
        return {"stopped": True}
    finally:
        run.close()


def run_dns_check(*, emit: Emit, should_stop: ShouldStop | None = None) -> dict:
    """Проверка DNS подмены для вкладки «Проверка DNS подмены»."""
    services = list(SERVICES)
    targets_count = sum(len(SERVICES[service][1]) for service in services)
    run = _Run(should_stop, workers=max(8, targets_count * 4))
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
            emit(_TIMED_OUT_LINE)
        if spoofed:
            emit("❌ Обнаружена DNS подмена: провайдер отдаёт неверные адреса.")
            emit("Откройте «Настройка DNS» и включите DNS с шифрованием (DoH) — его провайдер перехватить не сможет.")
        elif any(
            probe.judgement and probe.judgement.state == DnsState.UNKNOWN
            for probes in collected.values()
            for probe in probes
        ):
            emit("⚠️ Явной подмены не найдено, но часть адресов проверить не удалось.")
        else:
            emit("✅ DNS работает честно. Если сайты не открываются — дело не в DNS, а в блокировке соединения.")
        emit(f"Проверка заняла {time.monotonic() - started:.1f} с.")
        return {
            "summary": {"dns_poisoning_detected": spoofed},
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
