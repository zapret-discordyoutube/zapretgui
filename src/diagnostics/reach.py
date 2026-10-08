"""Открывается ли сайт: какие его адреса пробовать, в каком порядке и когда верить сбою.

Часть проверки одного сайта (``diagnostics.engine``): сюда приходит проба с уже
известными адресами (hosts, DNS системы, эталон), отсюда уходит итог основного
запроса ``probe.reach`` и список всех попыток.

Одному сбою здесь не верят: молчащий адрес перепроверяется другими адресами
сайта, повтором после паузы и попыткой по IPv6 — как это сделал бы браузер.
"""

from __future__ import annotations

import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout

from diagnostics import net_access
from diagnostics.limits import (
    HEAD_START_S,
    REACH_ADDRESSES,
    RETRY_PAUSE_S,
    SOURCE_HOSTS,
    SOURCE_REFERENCE,
    SOURCE_SYSTEM,
)
from diagnostics.run_context import Probe, Run
from diagnostics.tls_probe import CONNECT_TIMEOUT, KIND_CANCELLED, KIND_CERT, KIND_CONNECT, ProbeResult
from utils.windows_dns_query import ERROR_CANCELLED

__all__ = ["check_reach", "pause", "same_network"]


def _reach_order(probe: Probe, *, local_ok: bool) -> tuple[list[str], str]:
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


def _reach_candidates(probe: Probe, order: list[str]) -> list[str]:
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


def same_network(first: str, second: str) -> bool:
    """Адреса из одной сети (грубо: совпадают первые два числа)."""
    return _network_of(first) == _network_of(second)


def _network_of(ip: str) -> str:
    """Сеть адреса для грубого сравнения: первые два числа IPv4."""
    return ".".join(ip.split(".")[:2]) if "." in ip else ip.split(":")[0]


def pause(run: Run, seconds: float) -> None:
    """Пауза, которую снимает «Стоп» и общий лимит времени."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not run.dns_cancelled():
        time.sleep(0.05)


def check_reach(run: Run, probe: Probe, *, read_limit: int) -> None:
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

    others = [ip for ip in _reach_candidates(probe, order) if ip != order[0]][: REACH_ADDRESSES - 1]
    attempts: list[ProbeResult] = []
    early: list[Future] = []
    if local is not None and local.ip == order[0]:
        attempts.append(local)
    elif run.dns_cancelled():
        # Проверку прервали до первого запроса: это «не успели», а не «не открывается».
        probe.reach = ProbeResult(ip="", kind=KIND_CANCELLED)
        return
    else:
        first = run.submit(_one, order[0])
        try:
            attempts.append(first.result(timeout=HEAD_START_S))
        except FutureTimeout:
            # Живой сайт отвечает за доли секунды. Раз первый адрес молчит, остальные пробуются
            # сразу, не дожидаясь его полного срока: так же поступает браузер.
            early = [run.submit(_one, ip) for ip in others]
            attempts.append(first.result())

    # Чужой сертификат на адресе — повод попробовать другой адрес, но не тот же ещё раз.
    if not _settled(attempts) and not run.dns_cancelled():
        if others:
            # Остальные адреса пробуются разом: ждать их по очереди — это десятки секунд.
            futures = early or [run.submit(_one, ip) for ip in others]
            attempts.extend(future.result() for future in futures)
            if all(item.kind == KIND_CONNECT for item in attempts) and not run.dns_cancelled():
                # Все адреса пробовались в одну секунду: короткий сбой сети задел бы их
                # разом. Ещё одна попытка после паузы отделяет сбой от блокировки.
                pause(run, RETRY_PAUSE_S)
                if not run.dns_cancelled():
                    attempts.append(_one(order[0]))
        elif attempts[0].kind != KIND_CERT:
            pause(run, RETRY_PAUSE_S)
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
