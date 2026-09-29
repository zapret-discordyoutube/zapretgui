from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log
from utils.subproc import get_system_exe, run_hidden


@dataclass(frozen=True, slots=True)
class InternetCleanupCommand:
    label: str
    args: tuple[str, ...]
    timeout_seconds: int = 45


@dataclass(slots=True)
class InternetCleanupActionResult:
    level: str
    title: str
    content: str
    revert_checked: bool | None
    final_status: str


def build_internet_cleanup_commands(
    *,
    resolve_system_exe: Callable[[str], str] = get_system_exe,
) -> tuple[InternetCleanupCommand, ...]:
    netsh = resolve_system_exe("netsh.exe")
    return (
        InternetCleanupCommand("Сброс TCP/IP", (netsh, "int", "ip", "reset")),
        InternetCleanupCommand("Сброс WinHTTP proxy", (netsh, "winhttp", "reset", "proxy")),
        InternetCleanupCommand("Сброс Winsock", (netsh, "winsock", "reset")),
        InternetCleanupCommand("Сброс IPv4", (netsh, "interface", "ipv4", "reset")),
        InternetCleanupCommand("Сброс IPv6", (netsh, "interface", "ipv6", "reset")),
        InternetCleanupCommand(
            "Настройка динамических TCP-портов",
            (
                netsh,
                "int",
                "ipv4",
                "set",
                "dynamicport",
                "tcp",
                "start=10000",
                "num=30000",
            ),
        ),
    )


def _flush_dns_cache_native() -> bool:
    try:
        from dns.winapi import flush_resolver_cache

        return bool(flush_resolver_cache())
    except Exception as exc:
        log(f"Не удалось очистить DNS-кэш через WinAPI: {exc}", "DEBUG")
        return False


def _default_command_runner(args: Sequence[str], *, timeout: int):
    return run_hidden(
        tuple(args),
        wait=True,
        capture_output=True,
        text=False,
        timeout=timeout,
        shell=False,
    )


_MOJIBAKE_MARKERS = (
    "\ufffd",
    "╨",
    "╤",
    "Ð",
    "Ñ",
    "Рџ",
    "Р°",
    "Р±",
    "Рµ",
    "Рѕ",
    "С‚",
    "СЂ",
    "СЃ",
)
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def _decoded_text_score(text: str) -> int:
    cyrillic = len(_CYRILLIC_RE.findall(text))
    mojibake = sum(text.count(marker) for marker in _MOJIBAKE_MARKERS)
    replacement = text.count("\ufffd")
    controls = sum(1 for char in text if ord(char) < 32 and char not in "\r\n\t")
    return cyrillic * 3 - mojibake * 8 - replacement * 20 - controls * 5


def _repair_predecoded_mojibake(text: str) -> str:
    candidates = [text]
    for source_encoding, target_encoding in (("cp866", "cp1251"), ("cp1251", "cp866")):
        try:
            candidates.append(text.encode(source_encoding).decode(target_encoding))
        except UnicodeError:
            continue
    return max(candidates, key=_decoded_text_score)


def _decode_command_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _repair_predecoded_mojibake(value)
    if isinstance(value, bytes):
        candidates = [
            value.decode(encoding, errors="replace")
            for encoding in ("utf-8-sig", "cp866", "cp1251")
        ]
        return max(candidates, key=_decoded_text_score)
    return str(value)


def _looks_like_success_line(line: str) -> bool:
    normalized = line.strip().upper()
    return normalized in {"OK", "OK."} or normalized.endswith(" OK!") or normalized.endswith("- OK!")


def _first_relevant_error_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if not _looks_like_success_line(line):
            return line
    return lines[0] if lines else ""


def _short_error_from_completed(completed: Any) -> str:
    stderr = _decode_command_output(getattr(completed, "stderr", "")).strip()
    stdout = _decode_command_output(getattr(completed, "stdout", "")).strip()
    text = stderr or stdout
    first_line = _first_relevant_error_line(text)
    if len(first_line) <= 180:
        return first_line
    return first_line[:177] + "..."


def build_internet_cleanup_error_result(error: str) -> InternetCleanupActionResult:
    return InternetCleanupActionResult(
        level="error",
        title="Сброс сети не выполнен",
        content=str(error or "Не удалось выполнить очистку сети Windows."),
        revert_checked=None,
        final_status="",
    )


def run_internet_cleanup(
    *,
    command_runner: Callable[..., Any] | None = None,
    flush_dns_cache: Callable[[], bool] | None = None,
    status_callback: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    resolve_system_exe: Callable[[str], str] = get_system_exe,
) -> InternetCleanupActionResult:
    runner = command_runner or _default_command_runner
    flush_dns = flush_dns_cache or _flush_dns_cache_native
    commands = build_internet_cleanup_commands(resolve_system_exe=resolve_system_exe)

    failed: list[str] = []
    completed_count = 0
    for command in commands:
        if should_stop is not None and should_stop():
            return build_internet_cleanup_error_result("Сброс сети Windows остановлен.")
        if status_callback is not None:
            status_callback(f"{command.label}...")
        try:
            completed = runner(command.args, timeout=command.timeout_seconds)
        except Exception as exc:
            failed.append(f"{command.label}: {exc}")
            continue

        return_code = int(getattr(completed, "returncode", 1) or 0)
        if return_code == 0:
            completed_count += 1
            continue
        detail = _short_error_from_completed(completed)
        failed.append(f"{command.label}: код {return_code}" + (f", {detail}" if detail else ""))

    if should_stop is not None and should_stop():
        return build_internet_cleanup_error_result("Сброс сети Windows остановлен.")
    if status_callback is not None:
        status_callback("Очистка DNS-кэша...")
    dns_ok = bool(flush_dns())
    if dns_ok:
        completed_count += 1
    else:
        failed.append("Очистка DNS-кэша: не выполнена")

    if not failed:
        return InternetCleanupActionResult(
            level="success",
            title="Сеть Windows сброшена",
            content=(
                "Очистка выполнена. Если интернет не восстановится сразу, "
                "перезагрузите Windows."
            ),
            revert_checked=None,
            final_status="Готово",
        )

    if completed_count > 0:
        return InternetCleanupActionResult(
            level="warning",
            title="Сеть сброшена частично",
            content=(
                "Часть действий выполнена, но есть ошибки:\n"
                + "\n".join(failed[:4])
                + "\n\nПосле этого всё равно может понадобиться перезагрузка Windows."
            ),
            revert_checked=None,
            final_status="Готово",
        )

    return build_internet_cleanup_error_result("\n".join(failed[:4]))


class InternetCleanupWorker(QThread):
    loaded = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)
    status = pyqtSignal(int, str)

    def __init__(self, request_id: int, *, parent=None):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            result = run_internet_cleanup(
                status_callback=lambda message: self.status.emit(self._request_id, message),
                should_stop=lambda: bool(self._stop_requested),
            )
        except Exception as exc:
            log(f"InternetCleanupWorker: не удалось выполнить сброс сети: {exc}", "WARNING")
            self.failed.emit(self._request_id, str(exc))
            return
        self.loaded.emit(self._request_id, result)


__all__ = [
    "InternetCleanupActionResult",
    "InternetCleanupCommand",
    "InternetCleanupWorker",
    "build_internet_cleanup_commands",
    "build_internet_cleanup_error_result",
    "run_internet_cleanup",
]
