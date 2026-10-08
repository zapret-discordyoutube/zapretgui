"""Сброс сети Windows: то, что лежит за плиткой «Сбросить сеть Windows».

Всё, для чего у Windows есть готовые функции, делается прямыми вызовами
WinAPI (windows_features.internet_cleanup_winapi):

- кэш DNS и кэш адресов с маршрутами очищаются всегда;
- прокси WinHTTP сбрасывается, только если он задан;
- системный прокси отключается, только если он смотрит на этот же компьютер
  и там никто не отвечает — так бывает после аварийно закрытого VPN или
  прокси-клиента. Работающий прокси не трогается;
- из каталога Winsock удаляются надстройки посторонних программ (LSP).

У сброса TCP/IP и у диапазона динамических портов открытых функций нет,
поэтому для них запускается netsh — три команды по десятой доле секунды.
Сброс TCP/IP вступает в силу после перезагрузки Windows.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log
from utils.subproc import get_system_exe, run_hidden
from windows_features import internet_cleanup_winapi as winapi

NETSH_TIMEOUT_SECONDS = 30
# netsh запускается без консоли и в этом случае пишет в кодировке Windows (ANSI),
# а не в консольной (OEM): для русской Windows это 1251, а не 866.
NETSH_ENCODING = "mbcs" if os.name == "nt" else "utf-8"
DYNAMIC_TCP_PORT_START = 10000
DYNAMIC_TCP_PORT_COUNT = 30000

# Столько ждём ответа от прокси на этом же компьютере. Работающая программа
# отвечает мгновенно, а на закрытый порт Windows стучится около двух секунд.
PROXY_PROBE_TIMEOUT_SECONDS = 0.5
MAX_LISTED_ITEMS = 3
# Сколько итог висит на экране: в нём несколько строк, а ошибку ещё нужно успеть понять.
RESULT_DURATION_MS = 8000
PROBLEM_DURATION_MS = 15000


@dataclass(frozen=True, slots=True)
class CleanupStep:
    """Один шаг сброса.

    `run` возвращает фразу о том, что сделано, или пустую строку, если менять
    было нечего. Ошибку Windows он отдаёт исключением. `needs_reboot` — шаг
    подействует только после перезагрузки Windows.
    """

    label: str
    run: Callable[[], str]
    needs_reboot: bool = False


@dataclass(slots=True)
class InternetCleanupActionResult:
    level: str
    title: str
    content: str
    revert_checked: bool | None
    final_status: str
    duration_ms: int = RESULT_DURATION_MS


# ── шаги через netsh ──────────────────────────────────────────────────────


def _run_netsh(*args: str) -> tuple[int, str]:
    """Скрыто запускает netsh и возвращает его код завершения и текст."""
    try:
        completed = run_hidden(
            (get_system_exe("netsh.exe"), *args),
            capture_output=True,
            timeout=NETSH_TIMEOUT_SECONDS,
            encoding=NETSH_ENCODING,
        )
    except subprocess.TimeoutExpired:
        raise OSError(f"netsh не ответил за {NETSH_TIMEOUT_SECONDS} секунд") from None
    return int(completed.returncode), f"{completed.stdout or ''}\n{completed.stderr or ''}"


def _netsh_problem(code: int, text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    detail = next((line for line in lines if not line.endswith("OK!")), "")
    if len(detail) > 160:
        detail = detail[:157] + "..."
    return f"netsh вернул код {code}" + (f": {detail}" if detail else "")


def _count_reset_items(text: str) -> tuple[int, int]:
    """Сколько пунктов netsh сбросил и в скольких ему отказано.

    Каждый пункт — строка вида «Сброс Маршрут - OK!» либо «Сброс  - сбой.»
    (в английской Windows «Resetting Route, OK!»). Слова зависят от языка
    Windows, поэтому опираемся только на «OK!» и общее первое слово.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    done = [line for line in lines if line.endswith("OK!")]
    if not done:
        return 0, 0
    first_word = done[0].split()[0]
    items = [line for line in lines if line.split()[0] == first_word]
    return len(done), len(items) - len(done)


def _reset_tcpip(family: str) -> str:
    code, text = _run_netsh("interface", family.lower(), "reset")
    done, refused = _count_reset_items(text)
    log(f"Сброс сети: TCP/IP {family} — код {code}, сброшено пунктов: {done}, отказано: {refused}", "INFO")
    # На исправной Windows netsh всегда возвращает код 1: один защищённый системный
    # пункт сбросить нельзя («Отказано в доступе»), остальные три десятка сброшены.
    if code != 0 and done <= refused:
        raise OSError(_netsh_problem(code, text))
    return f"TCP/IP {family} сброшен."


def _reset_tcpip_v4() -> str:
    return _reset_tcpip("IPv4")


def _reset_tcpip_v6() -> str:
    return _reset_tcpip("IPv6")


def _set_dynamic_tcp_ports() -> str:
    code, text = _run_netsh(
        "interface",
        "ipv4",
        "set",
        "dynamicport",
        "tcp",
        f"start={DYNAMIC_TCP_PORT_START}",
        f"num={DYNAMIC_TCP_PORT_COUNT}",
    )
    if code != 0:
        raise OSError(_netsh_problem(code, text))
    last_port = DYNAMIC_TCP_PORT_START + DYNAMIC_TCP_PORT_COUNT - 1
    return f"Динамические TCP-порты: {DYNAMIC_TCP_PORT_START}–{last_port}."


# ── шаги через WinAPI ─────────────────────────────────────────────────────


def _flush_dns_cache() -> str:
    from dns.winapi import flush_resolver_cache

    if not flush_resolver_cache():
        raise OSError("Windows не смогла очистить кэш DNS")
    return "Кэш DNS очищен."


def _flush_address_caches() -> str:
    winapi.flush_neighbor_and_path_caches()
    return "Кэш адресов и маршрутов очищен."


def _reset_winhttp_proxy() -> str:
    proxy = winapi.read_winhttp_proxy()
    if not proxy:
        return ""
    winapi.reset_winhttp_proxy()
    return f"Прокси WinHTTP ({proxy}) сброшен."


def _proxy_endpoints(server: str) -> list[tuple[str, int]] | None:
    """Разбирает адрес системного прокси на пары «узел, порт».

    Windows хранит либо один адрес («127.0.0.1:10809»), либо список по
    протоколам («http=127.0.0.1:10809;socks=127.0.0.1:10808»). None — запись
    не удалось понять; такой прокси не трогаем.
    """
    endpoints: list[tuple[str, int]] = []
    for part in re.split(r"[;\s]+", str(server or "").strip()):
        if not part:
            continue
        address = part.rsplit("=", 1)[-1].split("://", 1)[-1].strip("/")
        match = re.fullmatch(r"\[(?P<v6>[^\]]+)\]:(?P<v6port>\d+)|(?P<host>[^:\[\]]+):(?P<port>\d+)", address)
        if match is None:
            return None
        host = match.group("v6") or match.group("host")
        port = int(match.group("v6port") or match.group("port"))
        if not 0 < port < 65536:
            return None
        if (host, port) not in endpoints:
            endpoints.append((host, port))
    return endpoints or None


def _is_this_computer(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _accepts_connections(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=PROXY_PROBE_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def _disable_dead_system_proxy() -> str:
    proxy = winapi.read_system_proxy()
    if not proxy.enabled or not proxy.server:
        return ""
    endpoints = _proxy_endpoints(proxy.server)
    if endpoints is None or not all(_is_this_computer(host) for host, _port in endpoints):
        # Прокси в сети (рабочий, провайдерский) — не наше дело.
        return ""
    if any(_accepts_connections(host, port) for host, port in endpoints):
        return ""
    winapi.disable_system_proxy()
    return f"Системный прокси {proxy.server} отключён: программа по этому адресу не отвечает."


def _remove_winsock_addons() -> str:
    providers = winapi.list_layered_winsock_providers()
    if not providers:
        return ""
    for provider in providers:
        log(f"Сброс сети: удаляется надстройка Winsock «{provider.name}»", "INFO")
        winapi.remove_winsock_provider(provider)
    names = list(dict.fromkeys(provider.name for provider in providers))
    listed = ", ".join(f"«{name}»" for name in names[:MAX_LISTED_ITEMS])
    if len(names) > MAX_LISTED_ITEMS:
        listed += f" и ещё {len(names) - MAX_LISTED_ITEMS}"
    return (
        f"Удалены надстройки Winsock: {listed}. "
        "Перезапустите браузер и другие программы, чтобы они перестали ими пользоваться."
    )


def default_cleanup_steps() -> tuple[CleanupStep, ...]:
    return (
        CleanupStep("сброс TCP/IP IPv4", _reset_tcpip_v4, needs_reboot=True),
        CleanupStep("сброс TCP/IP IPv6", _reset_tcpip_v6, needs_reboot=True),
        # Порты — после сброса TCP/IP, как и в прежнем порядке команд.
        CleanupStep("динамические TCP-порты", _set_dynamic_tcp_ports),
        CleanupStep("кэш DNS", _flush_dns_cache),
        CleanupStep("кэш адресов и маршрутов", _flush_address_caches),
        CleanupStep("прокси WinHTTP", _reset_winhttp_proxy),
        CleanupStep("системный прокси", _disable_dead_system_proxy),
        CleanupStep("Winsock", _remove_winsock_addons),
    )


# ── выполнение ────────────────────────────────────────────────────────────


def build_internet_cleanup_error_result(error: str) -> InternetCleanupActionResult:
    return InternetCleanupActionResult(
        level="error",
        title="Сброс сети не выполнен",
        content=str(error or "Не удалось выполнить сброс сети Windows."),
        revert_checked=None,
        final_status="",
        duration_ms=PROBLEM_DURATION_MS,
    )


def run_internet_cleanup(steps: Sequence[CleanupStep] | None = None) -> InternetCleanupActionResult:
    """Выполняет все шаги по порядку; ошибка одного шага не мешает остальным."""
    done: list[str] = []
    untouched: list[str] = []
    failed: list[str] = []
    needs_reboot = False
    for step in default_cleanup_steps() if steps is None else steps:
        try:
            note = str(step.run() or "")
        except Exception as exc:
            log(f"Сброс сети: {step.label} — ошибка: {exc}", "WARNING")
            failed.append(f"{step.label} — {exc}")
            continue
        log(f"Сброс сети: {step.label} — {note or 'менять нечего'}", "INFO")
        (done if note else untouched).append(note or step.label)
        needs_reboot = needs_reboot or (bool(note) and step.needs_reboot)

    # Сделанное — одним абзацем: сообщение само переносит длинные строки.
    lines = [" ".join(done)] if done else []
    if untouched:
        lines.append(f"Менять не пришлось: {', '.join(untouched)}.")
    if needs_reboot:
        lines.append("Перезагрузите Windows, чтобы сброс TCP/IP подействовал.")
    if not failed:
        return InternetCleanupActionResult(
            level="success",
            title="Сеть Windows сброшена",
            content="\n".join(lines),
            revert_checked=None,
            final_status="",
        )

    problems = "Не получилось: " + "; ".join(failed) + "."
    if not lines:
        return build_internet_cleanup_error_result(problems)
    return InternetCleanupActionResult(
        level="warning",
        title="Сеть сброшена частично",
        content="\n".join([*lines, problems]),
        revert_checked=None,
        final_status="",
        duration_ms=PROBLEM_DURATION_MS,
    )


class InternetCleanupWorker(QThread):
    loaded = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(self, request_id: int, *, parent=None):
        super().__init__(parent)
        self._request_id = int(request_id)

    def run(self) -> None:
        try:
            result = run_internet_cleanup()
        except Exception as exc:
            log(f"InternetCleanupWorker: не удалось выполнить сброс сети: {exc}", "WARNING")
            self.failed.emit(self._request_id, str(exc))
            return
        self.loaded.emit(self._request_id, result)


__all__ = [
    "CleanupStep",
    "InternetCleanupActionResult",
    "InternetCleanupWorker",
    "build_internet_cleanup_error_result",
    "default_cleanup_steps",
    "run_internet_cleanup",
]
