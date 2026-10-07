"""Шифрованный DNS через встроенный dnscrypt-proxy: запуск, остановка, починка.

Windows сама не умеет DNSCrypt и ODoH, поэтому при выборе такого режима
программа ставит на компьютер службу Windows «ZapretDnsCrypt» с движком
dnscrypt-proxy. Движок слушает обычные DNS-запросы на 127.0.0.1 (и ::1) и
шифрует их по дороге, а в сетевой адаптер прописывается адрес 127.0.0.1.

Почему служба, а не процесс программы: DNS должен работать и после закрытия
окна, и после перезагрузки. Упал движок — Windows сама поднимает его снова.

Порядок запуска (start) защищает интернет пользователя: адаптеры получают
127.0.0.1 только после того, как движок реально ответил на DNS-запрос. Не
ответил — служба убирается (или возвращается прежний режим), адаптеры не
тронуты, причина уходит на страницу.

Движок копируется из установки (exe\\dnscrypt-proxy.exe) в свою папку
user\\dnscrypt. Обновление программы заменяет файлы в exe\\ и снимает все её
процессы; копию оно не трогает, и Windows тут же перезапускает службу —
интернет не пропадает на время установки. Новую версию движка копия
получает при следующем запуске программы (repair).

Режим нигде отдельно не хранится: он записан в первой строке файла настроек
движка (dns.local_proxy_config), поэтому страница показывает то, что
запущено на самом деле.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from dns import local_proxy_catalog as catalog
from dns.local_proxy_config import LISTEN_IPV4, LISTEN_IPV6, build_config, read_mode
from log.log import log

SERVICE_NAME = "ZapretDnsCrypt"
SERVICE_DISPLAY_NAME = "Zapret: шифрованный DNS (dnscrypt-proxy)"
SERVICE_DESCRIPTION = (
    "Шифрует DNS-запросы этого компьютера (DNSCrypt, ODoH). Ставится и убирается в ZapretGUI: «Настройка DNS»."
)
EXE_NAME = "dnscrypt-proxy.exe"
CONFIG_NAME = "dnscrypt-proxy.toml"
LOG_NAME = "dnscrypt-proxy.log"
LOCAL_ADDRESSES = frozenset({LISTEN_IPV4, LISTEN_IPV6})
# Движок сначала опрашивает серверы; на медленной линии это занимает несколько секунд.
START_TIMEOUT_S = 25.0
_POLL_S = 0.5


@dataclass(frozen=True, slots=True)
class LocalProxyResult:
    success: bool
    message: str = ""
    # Слушает ли движок ещё и ::1 — тогда его можно прописать адаптеру как IPv6 DNS.
    listen_ipv6: bool = False


def _system(system):
    if system is not None:
        return system
    from dns import local_proxy_windows

    return local_proxy_windows


def _config_path(system) -> Path:
    return system.work_dir() / CONFIG_NAME


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _same_file(first: Path, second: Path) -> bool:
    try:
        if first.stat().st_size != second.stat().st_size:
            return False
        return hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()
    except OSError:
        return False


def _command(exe: Path, config: Path) -> str:
    return f'"{exe}" -config "{config}"'


def _log_tail(system, lines: int = 3) -> str:
    text = _read(system.work_dir() / LOG_NAME).strip().splitlines()
    return " | ".join(text[-lines:])


def active_mode(system=None) -> str:
    """Режим работающего шифрованного DNS или пустая строка.

    Режим есть, только когда есть и служба, и её файл настроек: без службы
    на 127.0.0.1 никто не отвечает, что бы ни было записано в файле.
    """
    system = _system(system)
    if not system.service_state(SERVICE_NAME):
        return ""
    return read_mode(_read(_config_path(system)))


def uses_local_proxy(addresses: Iterable[str]) -> bool:
    """Есть ли среди адресов DNS адаптеров адрес встроенного движка."""
    return any(str(item or "").strip() in LOCAL_ADDRESSES for item in addresses)


def _wait_answer(system) -> bool:
    waited = 0.0
    while waited < START_TIMEOUT_S:
        if system.answers(LISTEN_IPV4):
            return True
        system.sleep(_POLL_S)
        waited += _POLL_S
    return False


def _remove_service(system) -> None:
    if system.service_state(SERVICE_NAME):
        system.stop_service(SERVICE_NAME)
        system.delete_service(SERVICE_NAME)


def _ensure_service(system, exe: Path, config: Path) -> tuple[bool, str]:
    """Служба с нужной командой запуска; чужая или устаревшая запись пересоздаётся."""
    command = _command(exe, config)
    if system.service_state(SERVICE_NAME):
        if system.service_image_path(SERVICE_NAME).strip().casefold() == command.casefold():
            return True, ""
        system.stop_service(SERVICE_NAME)
        system.delete_service(SERVICE_NAME)
    return system.create_service(
        SERVICE_NAME,
        command,
        display_name=SERVICE_DISPLAY_NAME,
        description=SERVICE_DESCRIPTION,
    )


def _roll_back(system, exe: Path, config: Path, previous_config: str) -> None:
    """Запуск не удался: вернуть прежний режим, а если его не было — убрать службу."""
    if not previous_config:
        _remove_service(system)
        try:
            config.unlink(missing_ok=True)
        except OSError:
            pass
        return
    try:
        config.write_text(previous_config, encoding="utf-8")
    except OSError as exc:
        log(f"DNS: не удалось вернуть прежние настройки шифрованного DNS: {exc}", "WARNING")
        return
    ok, _error = _ensure_service(system, exe, config)
    if ok:
        system.start_service(SERVICE_NAME)


def start(mode: str, *, system=None) -> LocalProxyResult:
    """Запускает шифрованный DNS в режиме mode и ждёт, пока он ответит на запрос."""
    system = _system(system)
    if mode not in catalog.MODES:
        return LocalProxyResult(False, f"Неизвестный режим шифрованного DNS: {mode}")
    shipped = system.shipped_exe()
    if not shipped.is_file():
        return LocalProxyResult(
            False,
            "В установке нет файла exe\\dnscrypt-proxy.exe. Переустановите программу или обновите её.",
        )
    work = system.work_dir()
    exe, config = work / EXE_NAME, work / CONFIG_NAME
    previous_config = _read(config) if system.service_state(SERVICE_NAME) else ""

    # Свою службу останавливаем до проверки порта: иначе порт занят ею самой.
    if system.service_state(SERVICE_NAME):
        system.stop_service(SERVICE_NAME)
    listen_ipv6 = bool(system.ipv6_loopback_available())
    if not system.port_53_free(ipv6=listen_ipv6):
        _roll_back(system, exe, config, previous_config)
        return LocalProxyResult(
            False,
            "Порт 53 на этом компьютере занят другой программой. Обычно это служба Windows "
            "«Общий доступ к подключению к Интернету» (раздача интернета, Hyper-V, WSL) "
            "или другой DNS-посредник. Закройте её и выберите шифрованный DNS снова.",
        )

    try:
        work.mkdir(parents=True, exist_ok=True)
        if not _same_file(shipped, exe):
            shutil.copy2(shipped, exe)
        config.write_text(build_config(mode, log_path=str(work / LOG_NAME), listen_ipv6=listen_ipv6), encoding="utf-8")
        system.grant_service_access(work)
    except OSError as exc:
        _roll_back(system, exe, config, previous_config)
        return LocalProxyResult(False, f"Не удалось подготовить папку шифрованного DNS: {exc}")

    ok, output = system.check_config(exe, config)
    if not ok:
        _roll_back(system, exe, config, previous_config)
        return LocalProxyResult(False, f"dnscrypt-proxy не принял настройки: {output[-300:]}")

    ok, error = _ensure_service(system, exe, config)
    if not ok:
        _roll_back(system, exe, config, previous_config)
        return LocalProxyResult(False, f"Не удалось создать службу Windows {SERVICE_NAME}: {error}")
    started, reason = system.start_service(SERVICE_NAME)
    if not started:
        _roll_back(system, exe, config, previous_config)
        return LocalProxyResult(False, f"Служба Windows {SERVICE_NAME} не запустилась: {reason}")
    if not _wait_answer(system):
        tail = _log_tail(system)
        _roll_back(system, exe, config, previous_config)
        return LocalProxyResult(
            False,
            "Шифрованный DNS не ответил: серверы этого режима недоступны на вашей линии. "
            "DNS на адаптерах не менялся." + (f" Последние строки журнала: {tail}" if tail else ""),
        )
    log(f"DNS: шифрованный DNS запущен, режим {mode}", "INFO")
    return LocalProxyResult(True, listen_ipv6=listen_ipv6)


def stop(*, system=None) -> None:
    """Убирает службу шифрованного DNS и её файл настроек."""
    system = _system(system)
    had_service = bool(system.service_state(SERVICE_NAME))
    _remove_service(system)
    try:
        _config_path(system).unlink(missing_ok=True)
    except OSError:
        pass
    if had_service:
        log("DNS: шифрованный DNS остановлен", "INFO")


def stop_if_unused(adapter_addresses: Iterable[str], *, system=None) -> bool:
    """Останавливает движок, если ни один адаптер больше не смотрит на 127.0.0.1."""
    system = _system(system)
    if uses_local_proxy(adapter_addresses) or not system.service_state(SERVICE_NAME):
        return False
    stop(system=system)
    return True


def repair(adapter_addresses: Iterable[str], *, system=None) -> str:
    """Проверка при запуске программы: что сделано — строкой для журнала, иначе пусто.

    - адаптеры смотрят на 127.0.0.1, режим известен, а служба не работает
      или движок в ней старый — служба поднимается заново;
    - служба есть, а ни один адаптер ею не пользуется — она убирается.
    """
    system = _system(system)
    state = system.service_state(SERVICE_NAME)
    work = system.work_dir()
    exe, config = work / EXE_NAME, work / CONFIG_NAME
    mode = read_mode(_read(config))
    if not uses_local_proxy(adapter_addresses):
        if state:
            stop(system=system)
            return "шифрованный DNS не используется ни одним адаптером — служба убрана"
        return ""
    if not mode:
        return ""
    shipped = system.shipped_exe()
    outdated = shipped.is_file() and not _same_file(shipped, exe)
    wrong_command = bool(state) and system.service_image_path(SERVICE_NAME).strip().casefold() != _command(
        exe, config
    ).casefold()
    if state == "running" and not outdated and not wrong_command:
        return ""
    result = start(mode, system=system)
    if result.success:
        return f"шифрованный DNS ({mode}) поднят заново"
    return f"шифрованный DNS ({mode}) не удалось поднять: {result.message}"


__all__ = [
    "LOCAL_ADDRESSES",
    "LocalProxyResult",
    "SERVICE_NAME",
    "active_mode",
    "repair",
    "start",
    "stop",
    "stop_if_unused",
    "uses_local_proxy",
]
