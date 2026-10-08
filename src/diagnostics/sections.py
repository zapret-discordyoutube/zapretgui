"""Проверки отдельных разделов BlockCheck.

Каждая функция отвечает за один раздел отчёта: собирает факты через
``diagnostics.net_access``, отдаёт их чистому судье своего модуля
(``ipv6_check.judge``, ``system_state.judge``, ``speed_check.summarize_speed``…)
и возвращает готовый итог. Порядок разделов и общий отчёт — дело движка
(``diagnostics.engine``); здесь нет ничего про сайты и сервисы.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import replace

from diagnostics import (
    block_kind,
    filter_habits,
    filter_place,
    ipv6_check,
    my_network,
    net_access,
    quic_probe,
    registry,
    report_text,
    speed_check,
    system_state,
    udp_burst,
    upload_probe,
)
from diagnostics import problems as problem_rules
from diagnostics.limits import FILTER_MAX_TTL, FREEZE_READ_TIMEOUT
from diagnostics.run_context import Probe, Run, Stopped
from diagnostics.services import Service
from diagnostics.tls_probe import KIND_RESET, ProbeResult
from diagnostics.verdict import Level
from utils.dns_wire import TYPE_AAAA
from utils.ip_owner import lookup_ip_owner

Emit = Callable[[str], None]


def start_registry() -> Callable[[float], registry.Index]:
    """Запускает обновление списка реестра РКН и возвращает «ждалку» его итога.

    Список качается своим потоком, а не потоком прогона: если сеть медленная,
    проверка его не ждёт и берёт тот, что уже лежит на диске, а скачивание
    доходит до конца само — следующей проверке достанется свежий.
    """
    folder = registry.default_folder()
    done = threading.Event()
    result: list[registry.Index] = []

    def _fetch(url: str, etag: str) -> registry.Fetched | None:
        got = net_access.download_file(url, etag)
        if got is None:
            return None
        body, new_etag = got
        return registry.Fetched(body=body, etag=new_etag, unchanged=body is None)

    def _work() -> None:
        try:
            result.append(registry.refresh(folder, _fetch))
        except Exception:
            pass
        finally:
            done.set()

    threading.Thread(target=_work, name="blockcheck-registry", daemon=True).start()

    def wait(seconds: float) -> registry.Index:
        done.wait(max(0.0, seconds))
        return result[0] if result else registry.load(folder)

    return wait


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


def environment_lines() -> list[str]:
    lines: list[str] = []
    if sys.platform == "win32":
        version = sys.getwindowsversion()
        edition = "Windows 11" if version.build >= 22000 else f"Windows {version.major}"
        lines.append(f"🖥️ {edition} (сборка {version.build})")

    servers = net_access.system_servers()
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


def zapret_status() -> tuple[bool | None, str]:
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


def download(run: Run, host: str, path: str) -> ProbeResult | None:
    """Загрузка файла для проверки обрыва."""
    ip = net_access.known_address(run, host)
    if not ip:
        return None
    from diagnostics.freeze_check import READ_LIMIT

    return net_access.fetch(run, host, ip, path, read_limit=READ_LIMIT, read_timeout=FREEZE_READ_TIMEOUT)


def upload(run: Run, host: str, path: str) -> upload_probe.UploadVerdict | None:
    """Проверка отправки данных на тот же сервер. None — адреса нет или проверку сняли."""
    ip = net_access.known_address(run, host)
    if not ip or run.dns_cancelled():
        return None
    facts = upload_probe.collect(host, ip, path, submit=run.submit, cancel=run.probe_cancel)
    return upload_probe.judge(facts)


def _system_has_ipv6_route() -> bool | None:
    """Считает ли Windows, что по IPv6 есть дорога в интернет. None — не Windows или ошибка."""
    if sys.platform != "win32":
        return None
    from dns.winapi import internet_route

    return bool(internet_route().has_ipv6)


def check_ipv6(run: Run) -> ipv6_check.Ipv6Verdict:
    facts = ipv6_check.collect(
        has_route=_system_has_ipv6_route,
        lookup=lambda host: net_access.doh_lookup(run, host, TYPE_AAAA)[1],
        get=lambda host, ip: net_access.get(run, host, ip, "/"),
        submit=run.submit,
    )
    return ipv6_check.judge(facts)


IPV6_ICON = {
    ipv6_check.IPV6_OK: "✅",
    ipv6_check.IPV6_ABSENT: "ℹ️",
    ipv6_check.IPV6_BROKEN: "⚠️",
    ipv6_check.IPV6_UNKNOWN: "❔",
}


_CLOCK_HOST = "www.google.com"


def clock_skew(run: Run) -> float | None:
    """На сколько секунд часы компьютера впереди времени сервера. None — узнать не удалось."""
    from email.utils import parsedate_to_datetime

    addresses = net_access.doh_lookup(run, _CLOCK_HOST)[1]
    if not addresses:
        return None
    result = net_access.get(run, _CLOCK_HOST, addresses[0], "/generate_204", read_limit=1)
    local = time.time()
    for line in result.body.split(b"\r\n\r\n", 1)[0].split(b"\r\n")[1:]:
        name, _colon, value = line.partition(b":")
        if name.strip().lower() == b"date":
            try:
                return local - parsedate_to_datetime(value.decode("ascii", errors="ignore").strip()).timestamp()
            except (TypeError, ValueError):
                return None
    return None


def check_system(
    run: Run, services: dict[str, Service], zapret_running: bool | None = None
) -> tuple[system_state.SystemItem, ...]:
    hosts = tuple(dict.fromkeys(target.host for service in services.values() for target in service.targets))
    facts = system_state.collect_facts(check_hosts=hosts, clock_skew=lambda: clock_skew(run))
    return system_state.judge(facts, zapret_running=zapret_running)


SYSTEM_ICON = {
    system_state.LEVEL_OK: "✅",
    system_state.LEVEL_INFO: "ℹ️",
    system_state.LEVEL_WARN: "⚠️",
    system_state.LEVEL_FAIL: "❌",
    system_state.LEVEL_UNKNOWN: "❔",
}


DNS_FINDING_LEVEL = {"fail": Level.FAIL, "warn": Level.WARN}
_DNS_FINDING_ICON = {"ok": "✅", "info": "ℹ️", "warn": "⚠️", "fail": "❌"}


def dns_finding_parts(finding: dict) -> dict:
    """Готовые части находки про DNS: заголовок, все серверы парами «сервис, адрес» и пояснение.

    Пусто, если проверка их не дала: тогда экран делит фразу сам.
    """
    title = str(finding.get("title") or "")
    if not title:
        return {}
    servers = [[str(pair[0]), str(pair[1])] for pair in finding.get("servers") or () if len(pair) == 2]
    return {"title": title, "servers": servers, "note": str(finding.get("note") or "")}


def finish_dns_servers(run: Run, future: Future, emit: Emit) -> dict | None:
    """Итог проверки DNS-серверов для полной проверки: печатает раздел и возвращает словарь."""
    try:
        result = run.wait(future)
    except Stopped:
        raise
    except Exception as exc:
        emit(f"❔ DNS-серверы: проверка не выполнилась ({exc})")
        return None
    if not isinstance(result, dict):
        return None
    findings = [
        {"level": str(item.get("level") or "info"), "text": str(item.get("text") or ""), **dns_finding_parts(item)}
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


def find_filter_place(
    run: Run,
    collected: dict[str, list[Probe]],
    emit: Emit,
    *,
    zapret_running: bool | None = None,
    other_tools=(),
    own_asn: str = "",
) -> dict | None:
    """Где стоит фильтр — по нескольким сайтам, которые режут по имени (см. ``diagnostics.filter_place``)."""
    from diagnostics import block_cause, path_trace

    candidates = filter_place.pick_candidates(
        (
            probe.host,
            probe.reach.ip if probe.reach is not None else "",
            probe.quic is not None and probe.quic.code == quic_probe.QUIC_BLOCKED_BY_NAME,
            # По TCP ищется только фильтр, который рвёт соединение сбросом, и только доказанная блокировка по имени.
            probe.cause is not None and probe.cause.code == block_cause.CAUSE_BY_NAME and tcp_reset_seen(probe),
        )
        for probes in collected.values()
        for probe in probes
    )
    if run.dns_cancelled():
        return None
    # Блокировок по имени нет — дорогу всё равно показываем: по первому открывшемуся сайту.
    plain = next(
        (
            probe
            for probes in collected.values()
            for probe in probes
            if probe.reach is not None and probe.reach.ok and probe.reach.ip and ":" not in probe.reach.ip
        ),
        None,
    )
    pairs = {filter_place.METHOD_QUIC: path_trace.send_pair, filter_place.METHOD_TCP: path_trace.tcp_pair}

    distances: dict[str, int | None] = {}

    def _distance(ip: str, traced, cancel) -> int | None:
        # Расстояние у сайта одно на оба способа: второй раз не меряем.
        if ip not in distances:
            around = len(traced.hops) if traced is not None and traced.supported and traced.reached else 0
            distances[ip] = path_trace.tcp_distance(ip, cancel=cancel, around=around)
        return distances[ip]

    def _locate(method: str, host: str, ip: str, traced) -> path_trace.FilterFacts:
        return path_trace.locate_filter(
            ip,
            host,
            max_ttl=FILTER_MAX_TTL,
            cancel=run.probe_cancel,
            pair=pairs[method],
            distance_of=lambda address, cancel: _distance(address, traced, cancel),
        )

    def _trace(ip: str) -> path_trace.RouteTrace:
        return path_trace.trace_route(ip, should_stop=run.dns_cancelled)

    facts = filter_place.collect(candidates, locate=_locate, trace=_trace, submit=run.submit)
    if run.dns_cancelled():
        return None
    judged = [(item, filter_place.judge_site(item)) for item in facts]
    verdicts = [verdict for _item, verdict in judged if verdict is not None]
    # Узлы показываем по сайту, где место найдено; если нигде — по первому.
    shown_facts, shown = next(
        ((item, verdict) for item, verdict in judged if verdict is not None and verdict.hop),
        next(((item, verdict) for item, verdict in judged if verdict is not None), (None, None)),
    )

    def _owner(ip: str) -> tuple[str, str] | None:
        owner = lookup_ip_owner(ip, lambda name, rtype: net_access.doh_ask(run, name, rtype))
        return (owner.asn, owner.owner) if owner is not None and (owner.asn or owner.owner) else None

    route = shown_facts.trace if shown_facts else None
    if route is None and not candidates and plain is not None:
        route = _trace(plain.reach.ip)
    hops = filter_place.describe_hops(route, own_asn=own_asn, owner_of=_owner)
    placement = filter_place.aggregate(verdicts, hops, zapret_running=zapret_running, other_tools=other_tools)
    place = filter_place.report(placement, verdicts, hops, shown)
    place.update(filter_place.mechanisms(verdicts, _dns_place(run, _trace)))
    if shown is None and plain is not None and hops:
        place["host"], place["address"] = plain.host, plain.reach.ip
    for line in filter_place.lines(place):
        emit(line)
    return place


def live_services(services: dict, verdict_of: Callable, publish: Callable[[list], None]) -> Callable:
    """Возвращает приёмник «сайт проверен»: сервис уходит на экран, как только готовы все его адреса.

    ``verdict_of(сервис, пробы)`` даёт итог сервиса, ``publish(список)`` получает
    готовые сервисы в том же виде, что и в итоговом отчёте. После перепроверки
    движок кладёт окончательный список — он заменяет этот предварительный.
    """
    lock = threading.Lock()
    done: dict[str, list[Probe]] = {}

    def on_probe(key: str, probe: Probe) -> None:
        with lock:
            done.setdefault(key, []).append(probe)
            ready = {name: list(done[name]) for name in services if len(done.get(name, ())) >= len(services[name].targets)}
        verdicts = {name: verdict_of(services[name], probes) for name, probes in ready.items()}
        publish(report_text.services_report({name: services[name] for name in ready}, verdicts, ready))

    return on_probe


def dns_problems(dns_servers: dict | None) -> list[dict]:
    """Находки проверки DNS-серверов строками итога."""
    found: list[dict] = []
    for finding in (dns_servers or {}).get("findings") or ():
        level = DNS_FINDING_LEVEL.get(finding["level"])
        if level is not None:
            found.append(
                problem_rules.problem(
                    level, finding["text"], action="dns", kind=block_kind.KIND_DNS, parts=dns_finding_parts(finding)
                )
            )
    return found


def emit_freeze(freeze, emit: Emit) -> None:
    """Раздел «Обрыв на 16–20 КБ» текстового отчёта."""
    if freeze is None:
        return
    marks = {"ok": "✅", "freeze": "❌", "unknown": "❔"}
    rows = [(marks[item.state.value], item.name, item.text) for item in freeze.servers]
    for line in report_text.section_lines("Обрыв на 16–20 КБ", freeze, rows):
        emit(line)


def emit_telegram(telegram, emit: Emit) -> None:
    """Раздел «Telegram: дата-центры» текстового отчёта."""
    if telegram is None:
        return
    marks = {"ok": "✅", "fail": "❌", "unknown": "❔"}
    rows = [
        (marks[report_text.telegram_state(item)], f"{item.center.name} ({item.center.address})", report_text.telegram_text(item))
        for item in telegram.servers
    ]
    for line in report_text.section_lines("Telegram: дата-центры", telegram, rows):
        emit(line)


def emit_system(system, emit: Emit) -> None:
    """Раздел «Состояние системы» текстового отчёта."""
    if not system:
        return
    emit("")
    emit("━━━━━━━━ Состояние системы ━━━━━━━━")
    for item in system:
        emit(f"{SYSTEM_ICON[item.level]} {item.title}: {item.text}")


def emit_network(network, emit: Emit) -> None:
    """Раздел «Ваша сеть» текстового отчёта."""
    if network is None:
        return
    emit("")
    emit("━━━━━━━━ Ваша сеть ━━━━━━━━")
    for item in network["lines"]:
        emit(f"{'⚠️' if item['state'] == 'warn' else 'ℹ️'} {item['name']}: {item['text']}")


def emit_voice(voice, emit: Emit) -> None:
    """Раздел «Голосовые звонки» текстового отчёта."""
    if voice is None:
        return
    rows = [("✅" if item.answered else ("❌" if item.decided else "❔"), item.name, item.text) for item in voice.servers]
    for line in report_text.section_lines("Голосовые звонки (UDP)", voice, rows):
        emit(line)


def _dns_place(run: Run, trace: Callable):
    """Где перехватывают обычные DNS-запросы (см. ``diagnostics.dns_place``). None — проверку сняли."""
    from diagnostics import dns_place

    route = run.submit(trace, dns_place.DNS_SERVER)
    facts = dns_place.collect(
        lambda ttl: dns_place.ask_with_ttl(dns_place.DNS_SERVER, ttl, cancel=run.probe_cancel), submit=run.submit
    )
    try:
        traced = route.result()
    except Exception:
        traced = None
    reached = traced is not None and traced.supported and traced.reached
    return dns_place.judge(replace(facts, distance=len(traced.hops) if reached else 0))


def check_udp_burst(run: Run) -> udp_burst.BurstVerdict | None:
    """Серии UDP-пакетов: не замирает ли поток после первых двух десятков (см. ``diagnostics.udp_burst``)."""
    facts = udp_burst.check_bursts(
        lambda host, port: udp_burst.send_burst(host, port, cancel=run.probe_cancel), submit=run.submit
    )
    return None if run.dns_cancelled() else udp_burst.judge(facts)


HABIT_SITES = 3
# Срок одной пробы дробления: их десятки подряд к одному адресу, а «молчание» фильтра видно и за три секунды.
HABIT_TIMEOUT_S = 3.0
ECH_HOST = "www.cloudflare.com"


def check_filter_habits(run: Run, collected: dict[str, list[Probe]], emit: Emit, *, tools=()) -> dict | None:
    """Как работает фильтр: какое дробление приветствия проходит и режется ли ECH.

    ``tools`` — запущенные Zapret, VPN и другие программы обхода: пробы идут через
    них, поэтому вывод помечается как «сеть вместе с обходом».
    """
    from diagnostics import block_cause, browser_hello

    def _send(ip: str, parts: tuple[bytes, ...], pause: float) -> str:
        return browser_hello.send_parts(
            ip, parts, pause=pause, timeout=HABIT_TIMEOUT_S, cancel=run.probe_cancel
        ).kind

    def _rest(seconds: float) -> None:
        # Передышка между соединениями к одному адресу; «Стоп» её не ждёт.
        if not run.dns_cancelled():
            time.sleep(seconds)

    probes = [probe for items in collected.values() for probe in items]
    # Дробление сравнивается только там, где блокировка по имени уже доказана. С каждого сервиса —
    # один адрес, и сначала из разных сетей: три адреса одного Discord показали бы одно и то же трижды.
    blocked: dict[str, Probe] = {}
    spare: dict[str, Probe] = {}
    networks: set[str] = set()
    for items in collected.values():
        for probe in items:
            ip = probe.reach.ip if probe.reach is not None else ""
            if not ip or ":" in ip or probe.cause is None or probe.cause.code != block_cause.CAUSE_BY_NAME:
                continue
            network = ".".join(ip.split(".")[:2])
            if network in networks:
                spare.setdefault(ip, probe)
            else:
                blocked[ip] = probe
                networks.add(network)
            break
    blocked.update({ip: probe for ip, probe in spare.items() if ip not in blocked})
    cloudflare = next(
        (probe for probe in probes if probe.host == ECH_HOST and probe.reach is not None and probe.reach.ok and ":" not in probe.reach.ip),
        None,
    )
    if run.dns_cancelled() or (not blocked and cloudflare is None):
        return None
    ech_future = run.submit(filter_habits.check_ech, cloudflare.reach.ip, send=_send) if cloudflare is not None else None
    split_futures = [
        run.submit(filter_habits.check_split, probe.host, ip, send=_send, pause=_rest)
        for ip, probe in list(blocked.items())[:HABIT_SITES]
    ]
    splits = [verdict for verdict in (filter_habits.judge_split(run.wait(future)) for future in split_futures) if verdict is not None]
    ech = filter_habits.judge_ech(run.wait(ech_future) if ech_future is not None else None)
    report = filter_habits.summarize(splits, ech, tools=tools)
    if report is None:
        return None
    for line in filter_habits.lines(report):
        emit(line)
    return report


def tcp_reset_seen(probe: Probe) -> bool:
    """Основное соединение с сайтом оборвалось сбросом (а не тишиной)."""
    return probe.reach is not None and probe.reach.kind == KIND_RESET


def check_speed(run: Run, emit: Emit) -> dict | None:
    """Скорость зарубежных серверов против российских. Идёт последней, когда остальная нагрузка спала."""

    def download(server: speed_check.SpeedServer) -> tuple[int, float] | None:
        ip = net_access.known_address(run, server.host)
        if not ip:
            return None
        started = time.monotonic()
        stop_at = started + speed_check.SAMPLE_SECONDS
        result = net_access.fetch(
            run,
            server.host,
            ip,
            server.path,
            read_limit=speed_check.SAMPLE_BYTES,
            # Качаем не дольше отведённого: на медленной линии три мегабайта шли бы минуту.
            body_done=lambda _body: time.monotonic() >= stop_at,
        )
        if not result.ok:
            return None
        return int(result.body_size), time.monotonic() - started

    samples = speed_check.check_speed(download, should_stop=run.dns_cancelled)
    if not samples:
        return None
    report = speed_check.summarize_speed(samples)
    emit("")
    emit("━━━━━━━━ Скорость ━━━━━━━━")
    for item in samples:
        place = "Россия" if item.server.domestic else "за границей"
        emit(f"{'ℹ️' if item.kbps is not None else '❔'} {item.server.name} ({place}): {speed_check.speed_text(item.kbps)}")
    emit(f"{report_text.LEVEL_ICON[report.level]} {report.headline}")
    slow = report.level == Level.WARN
    return {
        "level": report.level.value,
        "headline": report.headline,
        "items": [
            {
                "name": item.server.name,
                "host": item.server.host,
                "domestic": item.server.domestic,
                "kbps": None if item.kbps is None else round(item.kbps, 1),
                "state": "unknown" if item.kbps is None else ("warn" if slow and not item.server.domestic else "ok"),
                "text": speed_check.speed_text(item.kbps),
            }
            for item in samples
        ],
    }


def check_network(run: Run, other_tools) -> dict:
    """«Ваша сеть»: внешний адрес, провайдер и адрес компьютера — готовым словарём для отчёта."""

    def _fetch(server: str) -> bytes | None:
        result = net_access.fetch(run, my_network.TRACE_HOST, server, my_network.TRACE_PATH, read_limit=2048)
        return bytes(result.body) if result.ok and result.body else None

    def _probe(ttl: int) -> tuple[str, float | None] | None:
        if run.dns_cancelled():
            return None
        return net_access.ping_hop(my_network.TRACE_SERVERS[0], ttl, timeout_ms=my_network.PING_TIMEOUT_MS)

    facts = my_network.collect(
        probe=_probe,fetch=_fetch, # Владельца сети спрашиваем шифрованным путём: иначе за него ответил бы перехватчик DNS.
        owner_of=lambda ip: lookup_ip_owner(ip, lambda name, rtype: net_access.doh_ask(run, name, rtype)),)
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
