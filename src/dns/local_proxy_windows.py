"""Обращения к Windows для встроенного dnscrypt-proxy (см. dns.local_proxy).

Здесь только «руки»: служба Windows, порт 53, запуск движка для проверки
настроек и пробный DNS-запрос. Что и в каком порядке делать, решает
dns.local_proxy; в тестах этот модуль подменяется.

Служба создаётся командой sc.exe: ей нужны учётная запись с малыми правами
(LocalService — движку хватает сети и своей папки) и перезапуск после сбоя,
а общий autostart.service_api создаёт службы только от имени системы.
"""

from __future__ import annotations

import socket
import time
from pathlib import Path

from log.log import log

_SERVICE_RUNNING = 4
_SERVICE_STOPPED = 1


def work_dir() -> Path:
    """Папка движка: его копия, настройки и журнал. Установщик её не трогает."""
    from config.runtime_layout import APPLICATION_PATHS

    return APPLICATION_PATHS.user_dir / "dnscrypt"


def shipped_exe() -> Path:
    """Движок из установки; обновляется вместе с программой."""
    from config.runtime_layout import APPLICATION_PATHS

    return APPLICATION_PATHS.exe_dir / "dnscrypt-proxy.exe"


def _sc(*args: str, timeout: int = 20) -> tuple[int, str]:
    from utils.subproc import get_system_exe, run_hidden

    try:
        done = run_hidden([get_system_exe("sc.exe"), *args], wait=True, capture_output=True, timeout=timeout)
    except Exception as exc:
        return -1, str(exc)
    output = done.stdout if isinstance(done.stdout, str) else (done.stdout or b"").decode("cp866", "replace")
    return int(done.returncode), output.strip()


def service_state(name: str) -> str:
    """"" — службы нет, "running", "stopped" или "other" (запускается, останавливается)."""
    from autostart import service_api

    if not service_api.service_exists(name):
        return ""
    state = service_api.get_service_state(name)
    if state == _SERVICE_RUNNING:
        return "running"
    if state == _SERVICE_STOPPED:
        return "stopped"
    return "other"


def service_image_path(name: str) -> str:
    """Команда запуска службы из её записи в Windows."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SYSTEM\CurrentControlSet\Services\{name}") as key:
            return str(winreg.QueryValueEx(key, "ImagePath")[0])
    except OSError:
        return ""


def create_service(name: str, command: str, *, display_name: str, description: str) -> tuple[bool, str]:
    code, output = _sc(
        "create", name,
        "binPath=", command,
        "start=", "auto",
        "obj=", r"NT AUTHORITY\LocalService",
        "DisplayName=", display_name,
    )
    if code != 0:
        return False, output or f"sc create: код {code}"
    _sc("description", name, description)
    # Движок упал или его сняли — Windows поднимает его снова: иначе пропал бы весь DNS.
    _sc("failure", name, "reset=", "86400", "actions=", "restart/1000/restart/1000/restart/5000")
    return True, ""


def start_service(name: str) -> tuple[bool, str]:
    code, output = _sc("start", name)
    if code not in (0, 1056):  # 1056 — уже запущена
        reason = " ".join((output or f"код {code}").split())
        log(f"DNS: служба {name} не запустилась: {reason}", "WARNING")
        return False, reason
    return True, ""


def grant_service_access(folder: Path) -> None:
    """Даёт учётной записи службы (LocalService) читать движок и писать журнал в его папке.

    В обычной папке установки эти права уже есть, но программу могут поставить
    и туда, где служебным учётным записям доступ закрыт.
    """
    from utils.subproc import get_system_exe, run_hidden

    try:
        run_hidden(
            [get_system_exe("icacls.exe"), str(folder), "/grant", "*S-1-5-19:(OI)(CI)M", "/T", "/C", "/Q"],
            wait=True, capture_output=True, timeout=20,
        )
    except Exception as exc:
        log(f"DNS: не удалось выдать службе права на папку {folder}: {exc}", "WARNING")


def stop_service(name: str) -> bool:
    from autostart import service_api

    return bool(service_api.stop_service(name, wait_timeout_ms=10000))


def delete_service(name: str) -> bool:
    from autostart import service_api

    return bool(service_api.delete_service(name))


def _can_bind(family: int, address: str, kind: int) -> bool:
    try:
        with socket.socket(family, kind) as probe:
            probe.bind((address, 53))
        return True
    except OSError:
        return False


def ipv6_loopback_available() -> bool:
    """Есть ли на компьютере адрес ::1 (IPv6 бывает выключен целиком)."""
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as probe:
            probe.bind(("::1", 0))
        return True
    except OSError:
        return False


def port_53_free(*, ipv6: bool) -> bool:
    """Свободен ли порт 53 на 127.0.0.1 (и на ::1): его может держать другая программа."""
    free = _can_bind(socket.AF_INET, "127.0.0.1", socket.SOCK_DGRAM) and _can_bind(
        socket.AF_INET, "127.0.0.1", socket.SOCK_STREAM
    )
    if free and ipv6:
        free = _can_bind(socket.AF_INET6, "::1", socket.SOCK_DGRAM)
    return free


def check_config(exe: Path, config: Path) -> tuple[bool, str]:
    """Просит сам движок проверить файл настроек, не запуская его."""
    from utils.subproc import run_hidden

    try:
        done = run_hidden(
            [str(exe), "-config", str(config), "-check"],
            wait=True, capture_output=True, timeout=30, cwd=str(exe.parent),
        )
    except Exception as exc:
        return False, str(exc)
    output = done.stdout if isinstance(done.stdout, str) else (done.stdout or b"").decode("utf-8", "replace")
    errors = done.stderr if isinstance(done.stderr, str) else (done.stderr or b"").decode("utf-8", "replace")
    return done.returncode == 0, (output + "\n" + errors).strip()


def answers(address: str) -> bool:
    """Отдаёт ли DNS на этом адресе настоящий ответ с адресом сайта."""
    from utils.dns_wire import STATUS_OK, TYPE_A, query_udp

    return query_udp(address, "example.com", TYPE_A, timeout_s=1.5).status == STATUS_OK


def sleep(seconds: float) -> None:
    time.sleep(seconds)


__all__ = [
    "answers",
    "check_config",
    "create_service",
    "delete_service",
    "grant_service_access",
    "ipv6_loopback_available",
    "port_53_free",
    "service_image_path",
    "service_state",
    "shipped_exe",
    "sleep",
    "start_service",
    "stop_service",
    "work_dir",
]
