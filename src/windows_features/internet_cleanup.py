"""Сброс сети Windows: то, что лежит за плиткой «Сбросить сеть Windows».

Чинит только то, что ломается на самом деле, и только вызовами WinAPI
(windows_features.internet_cleanup_winapi) — без запуска netsh и разбора его
текста. Всё выполняется за доли секунды и не требует перезагрузки:

- кэш DNS и кэш адресов с маршрутами очищаются всегда: Windows сразу
  узнаёт всё заново;
- прокси WinHTTP сбрасывается, только если он задан;
- системный прокси отключается, только если он смотрит на этот же компьютер
  и там никто не отвечает — так бывает после аварийно закрытого VPN или
  прокси-клиента. Работающий прокси не трогается;
- из каталога Winsock удаляются надстройки посторонних программ (LSP).

Адреса, DNS-серверы и прочие настройки сетевых адаптеров не меняются.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log
from windows_features import internet_cleanup_winapi as winapi

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
    было нечего. Ошибку Windows он отдаёт исключением.
    """

    label: str
    run: Callable[[], str]


@dataclass(slots=True)
class InternetCleanupActionResult:
    level: str
    title: str
    content: str
    revert_checked: bool | None
    final_status: str
    duration_ms: int = RESULT_DURATION_MS


# ── шаги ──────────────────────────────────────────────────────────────────


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
    for step in default_cleanup_steps() if steps is None else steps:
        try:
            note = str(step.run() or "")
        except Exception as exc:
            log(f"Сброс сети: {step.label} — ошибка: {exc}", "WARNING")
            failed.append(f"{step.label} — {exc}")
            continue
        log(f"Сброс сети: {step.label} — {note or 'менять нечего'}", "INFO")
        (done if note else untouched).append(note or step.label)

    lines = list(done)
    if untouched:
        lines.append(f"Менять не пришлось: {', '.join(untouched)}.")
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
