"""Состояние системы: что на самом компьютере мешает Zapret или искажает проверки.

Сетевые проверки отвечают на вопрос «что блокирует провайдер». Но сайт может
не открываться и по причинам на самом компьютере: остановлена служба, без
которой драйвер не запускается; антивирус унёс файлы программы; включён
прокси; сбиты часы. Здесь всё это собирается в одном месте.

Правило модуля — **только чтение**. Ничего не запускается, не останавливается
и не правится: проверка, которая молча меняет систему, скрывает от человека
то, что он должен был узнать.

Сбор (``collect_facts``) отделён от выводов (``judge``): выводы — чистая
функция над собранными фактами. Факт, который узнать не удалось, остаётся
``None`` и превращается в «проверить не удалось», а не в «всё в порядке».
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass

__all__ = [
    "LEVEL_FAIL",
    "LEVEL_INFO",
    "LEVEL_OK",
    "LEVEL_UNKNOWN",
    "LEVEL_WARN",
    "MIN_WINDOWS_BUILD",
    "SystemFacts",
    "SystemItem",
    "collect_facts",
    "judge",
    "tunnel_adapters",
    "worst_level",
]

LEVEL_OK = "ok"
LEVEL_INFO = "info"
LEVEL_WARN = "warn"
LEVEL_FAIL = "fail"
LEVEL_UNKNOWN = "unknown"

# Windows 10 версии 1809: раньше драйвер перехвата не работает.
MIN_WINDOWS_BUILD = 17763
LOW_DISK_MB = 200
# Сертификаты сайтов перестают проходить проверку при заметном расхождении часов.
CLOCK_SKEW_LIMIT_S = 300

_LEVEL_ORDER = {LEVEL_FAIL: 0, LEVEL_WARN: 1, LEVEL_UNKNOWN: 2, LEVEL_INFO: 3, LEVEL_OK: 4}

# Признаки VPN-подключения в описании сетевого адаптера.
_TUNNEL_WORDS = (
    "wireguard",
    "wintun",
    "tap-windows",
    "openvpn",
    "tailscale",
    "zerotier",
    "amnezia",
    "cloudflare warp",
    "sing-box",
    "vpn",
)
_IF_TYPE_PPP = 23


@dataclass(frozen=True, slots=True)
class SystemFacts:
    """Сырые факты. ``None`` везде значит «узнать не удалось»."""

    is_admin: bool | None = None
    windows_build: int | None = None
    in_temp_folder: bool | None = None
    in_onedrive: bool | None = None
    free_mb: int | None = None
    # (проверка состоялась, каких файлов нет, какие повреждены).
    integrity: tuple[bool, tuple[str, ...], tuple[str, ...]] | None = None
    bfe_running: bool | None = None
    # Программы, которые мешают драйверу перехвата.
    conflicts: tuple[str, ...] | None = None
    # Службы GoodbyeDPI, оставшиеся в системе.
    goodbyedpi_services: tuple[str, ...] | None = None
    # Другие программы обхода и VPN, запущенные сейчас.
    bypass_tools: tuple[str, ...] | None = None
    # Название антивируса; пустая строка — не найден.
    antivirus: str | None = None
    # Адрес ручного прокси и адрес сценария автонастройки; пустая строка — выключено.
    proxy_server: str | None = None
    proxy_script: str | None = None
    # Названия активных VPN-подключений.
    tunnels: tuple[str, ...] | None = None
    hosts_readable: bool | None = None
    # Проверяемые сайты, адрес которых задан в файле hosts: (сайт, адрес).
    hosts_overrides: tuple[tuple[str, str], ...] | None = None
    # На сколько секунд часы компьютера впереди настоящего времени (минус — отстают).
    clock_skew_s: float | None = None


@dataclass(frozen=True, slots=True)
class SystemItem:
    key: str
    title: str
    level: str
    text: str
    advice: str = ""


def worst_level(items) -> str:
    levels = [item.level for item in items]
    return min(levels, key=lambda level: _LEVEL_ORDER[level]) if levels else LEVEL_OK


# ---------------------------------------------------------------------------
# Сбор фактов
# ---------------------------------------------------------------------------


def tunnel_adapters(interfaces) -> tuple[str, ...]:
    """Названия подключённых адаптеров, похожих на VPN."""
    found: list[str] = []
    for interface in interfaces:
        if not getattr(interface, "connected", False):
            continue
        description = str(getattr(interface, "description", "") or "")
        name = str(getattr(interface, "name", "") or "")
        text = f"{description} {name}".lower()
        if getattr(interface, "if_type", 0) == _IF_TYPE_PPP or any(word in text for word in _TUNNEL_WORDS):
            label = name or description
            if label and label not in found:
                found.append(label)
    return tuple(found)


def _safe(read: Callable[[], object]):
    """Факт или None: сбой одного чтения не должен ронять остальные."""
    try:
        return read()
    except Exception:
        return None


def _read_is_admin() -> bool:
    import ctypes

    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def _read_windows_build() -> int:
    return int(sys.getwindowsversion().build)


def _read_in_temp() -> bool:
    from startup.check_start import check_if_application_root_is_temporary

    return bool(check_if_application_root_is_temporary())


def _read_in_onedrive() -> bool:
    from startup.check_start import check_path_for_onedrive

    return bool(check_path_for_onedrive()[0])


def _read_free_mb() -> int:
    import shutil

    from config.runtime_layout import APPLICATION_PATHS

    return int(shutil.disk_usage(str(APPLICATION_PATHS.root)).free // (1024 * 1024))


def _read_integrity() -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    from install_integrity import verify_fast

    report = verify_fast()
    return bool(report.checked), tuple(report.missing), tuple(report.corrupted)


def _read_bfe() -> bool:
    from startup.bfe_util import is_service_running

    return bool(is_service_running("BFE"))


def _read_conflicts() -> tuple[str, ...]:
    from startup.check_start import check_mitmproxy
    from winws_runtime.health.launch_conflicts import check_conflicting_processes

    names = [str(item.get("name") or item.get("exe") or "") for item in check_conflicting_processes()]
    if check_mitmproxy()[0]:
        names.append("mitmproxy")
    return tuple(dict.fromkeys(name for name in names if name))


def _read_goodbyedpi_services() -> tuple[str, ...]:
    from startup.check_start import _service_exists_reg

    return tuple(name for name in ("GoodbyeDPI", "GoodbyeDPI_x64", "GoodbyeDPIService") if _service_exists_reg(name))


def _read_bypass_tools() -> tuple[str, ...]:
    from utils.bypass_tools import bypass_tools_among
    from utils.windows_process_probe import iter_process_records_winapi_strict

    return bypass_tools_among(name for _pid, name in iter_process_records_winapi_strict())


def _read_antivirus() -> str:
    from winws_runtime.health.antivirus_detection import _detect_active_antivirus

    return str(_detect_active_antivirus() or "")


def _read_proxy() -> tuple[str, str]:
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    ) as key:
        def value(name: str):
            try:
                return winreg.QueryValueEx(key, name)[0]
            except FileNotFoundError:
                return ""

        server = str(value("ProxyServer") or "") if value("ProxyEnable") else ""
        return server, str(value("AutoConfigURL") or "")


def _read_tunnels() -> tuple[str, ...]:
    from dns.winapi import list_interfaces

    return tunnel_adapters(list_interfaces())


def _read_hosts_readable() -> bool:
    from hosts.hosts import safe_read_hosts_file

    return safe_read_hosts_file() is not None


def _read_hosts_overrides(hosts: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    from utils.windows_dns_query import hosts_file_ipv4

    found: list[tuple[str, str]] = []
    for host in hosts:
        addresses = hosts_file_ipv4(host)
        if addresses:
            found.append((host, addresses[0]))
    return tuple(found)


def collect_facts(
    *,
    check_hosts: tuple[str, ...] = (),
    clock_skew: Callable[[], float | None] | None = None,
) -> SystemFacts:
    """Читает всё, что можно узнать о системе, ничего в ней не меняя.

    ``check_hosts`` — сайты, для которых ищутся записи в файле hosts.
    ``clock_skew`` — как узнать расхождение часов (для этого нужна сеть).
    """
    if sys.platform != "win32":
        # На другой системе читать нечего: все факты остаются неизвестными.
        return SystemFacts(clock_skew_s=_safe(clock_skew) if clock_skew is not None else None)
    proxy = _safe(_read_proxy)
    return SystemFacts(
        is_admin=_safe(_read_is_admin),
        windows_build=_safe(_read_windows_build),
        in_temp_folder=_safe(_read_in_temp),
        in_onedrive=_safe(_read_in_onedrive),
        free_mb=_safe(_read_free_mb),
        integrity=_safe(_read_integrity),
        bfe_running=_safe(_read_bfe),
        conflicts=_safe(_read_conflicts),
        goodbyedpi_services=_safe(_read_goodbyedpi_services),
        bypass_tools=_safe(_read_bypass_tools),
        antivirus=_safe(_read_antivirus),
        proxy_server=proxy[0] if proxy is not None else None,
        proxy_script=proxy[1] if proxy is not None else None,
        tunnels=_safe(_read_tunnels),
        hosts_readable=_safe(_read_hosts_readable),
        hosts_overrides=_safe(lambda: _read_hosts_overrides(tuple(check_hosts))),
        clock_skew_s=_safe(clock_skew) if clock_skew is not None else None,
    )


# ---------------------------------------------------------------------------
# Выводы
# ---------------------------------------------------------------------------


def _unknown(key: str, title: str) -> SystemItem:
    return SystemItem(key, title, LEVEL_UNKNOWN, "проверить не удалось")


def _short(names, limit: int = 4) -> str:
    names = list(names)
    shown = ", ".join(names[:limit])
    return f"{shown} и ещё {len(names) - limit}" if len(names) > limit else shown


def _minutes(seconds: float) -> str:
    minutes = abs(seconds) / 60
    if minutes < 90:
        return f"{round(minutes)} мин"
    hours = minutes / 60
    return f"{round(hours)} ч" if hours < 48 else f"{round(hours / 24)} дн"


def judge(facts: SystemFacts) -> tuple[SystemItem, ...]:
    """Строки отчёта о системе, в постоянном порядке."""
    items: list[SystemItem] = []

    def add(key: str, title: str, level: str, text: str, advice: str = "") -> None:
        items.append(SystemItem(key, title, level, text, advice))

    title = "Права администратора"
    if facts.is_admin is None:
        items.append(_unknown("admin", title))
    elif facts.is_admin:
        add("admin", title, LEVEL_OK, "есть")
    else:
        add(
            "admin",
            title,
            LEVEL_FAIL,
            "программа запущена без них — драйвер перехвата не запустится",
            "Закройте программу и запустите её снова, согласившись на запрос Windows.",
        )

    title = "Версия Windows"
    if facts.windows_build is None:
        items.append(_unknown("windows", title))
    elif facts.windows_build >= MIN_WINDOWS_BUILD:
        add("windows", title, LEVEL_OK, f"сборка {facts.windows_build} подходит")
    else:
        add(
            "windows",
            title,
            LEVEL_FAIL,
            f"сборка {facts.windows_build} слишком старая: нужна Windows 10 версии 1809 (сборка {MIN_WINDOWS_BUILD}) или новее",
            "Обновите Windows.",
        )

    title = "Папка программы"
    if facts.in_temp_folder is None and facts.in_onedrive is None:
        items.append(_unknown("location", title))
    elif facts.in_temp_folder:
        add(
            "location",
            title,
            LEVEL_WARN,
            "программа запущена из временной папки — Windows может удалить её файлы",
            "Установите программу обычным установщиком.",
        )
    elif facts.in_onedrive:
        add(
            "location",
            title,
            LEVEL_WARN,
            "программа лежит в папке OneDrive — синхронизация мешает её файлам",
            "Переустановите программу в папку вне OneDrive.",
        )
    else:
        add("location", title, LEVEL_OK, "расположение подходит")

    title = "Место на диске"
    if facts.free_mb is None:
        items.append(_unknown("disk", title))
    elif facts.free_mb < LOW_DISK_MB:
        add(
            "disk",
            title,
            LEVEL_WARN,
            f"свободно всего {facts.free_mb} МБ — может не хватить на журналы и обновление",
            "Освободите место на диске с программой.",
        )
    else:
        add("disk", title, LEVEL_OK, f"свободно {facts.free_mb // 1024} ГБ" if facts.free_mb >= 1024 else f"свободно {facts.free_mb} МБ")

    title = "Файлы программы"
    if facts.integrity is None:
        items.append(_unknown("files", title))
    else:
        checked, missing, corrupted = facts.integrity
        if not checked:
            add("files", title, LEVEL_INFO, "сверить не с чем: в установке нет списка файлов")
        elif missing or corrupted:
            parts = []
            if missing:
                parts.append(f"не хватает: {_short(missing)}")
            if corrupted:
                parts.append(f"повреждены: {_short(corrupted)}")
            add(
                "files",
                title,
                LEVEL_FAIL,
                "; ".join(parts) + ". Чаще всего файлы забирает в карантин антивирус",
                "Добавьте папку программы в исключения антивируса и восстановите программу через страницу обновлений.",
            )
        else:
            add("files", title, LEVEL_OK, "все на месте")

    title = "Служба фильтрации Windows (BFE)"
    if facts.bfe_running is None:
        items.append(_unknown("bfe", title))
    elif facts.bfe_running:
        add("bfe", title, LEVEL_OK, "работает")
    else:
        add(
            "bfe",
            title,
            LEVEL_FAIL,
            "не работает — без неё драйвер перехвата не запускается",
            "Откройте «Службы» Windows, найдите «Служба базовой фильтрации» и запустите её.",
        )

    title = "Мешающие программы"
    if facts.conflicts is None and facts.goodbyedpi_services is None:
        items.append(_unknown("conflicts", title))
    else:
        found = list(facts.conflicts or ())
        if facts.goodbyedpi_services:
            found.append(f"служба GoodbyeDPI ({_short(facts.goodbyedpi_services)})")
        if found:
            add(
                "conflicts",
                title,
                LEVEL_WARN,
                f"найдены: {_short(found)}. Они используют тот же драйвер или перехватывают его работу",
                "Закройте эти программы перед запуском Zapret.",
            )
        else:
            add("conflicts", title, LEVEL_OK, "не найдены")

    title = "Другие программы обхода и VPN"
    if facts.bypass_tools is None and facts.tunnels is None:
        items.append(_unknown("bypass", title))
    else:
        parts = []
        if facts.bypass_tools:
            parts.append(f"запущены: {_short(facts.bypass_tools)}")
        if facts.tunnels:
            parts.append(f"активны VPN-подключения: {_short(facts.tunnels)}")
        if parts:
            add(
                "bypass",
                title,
                LEVEL_INFO,
                "; ".join(parts) + ". Проверки при них показывают сеть вместе с ними, а не сеть провайдера",
            )
        else:
            add("bypass", title, LEVEL_OK, "не найдены")

    title = "Антивирус"
    if facts.antivirus is None:
        items.append(_unknown("antivirus", title))
    elif facts.antivirus:
        add(
            "antivirus",
            title,
            LEVEL_INFO,
            f"работает {facts.antivirus}. Если Zapret не запускается или у программы пропадают файлы — причина может быть в нём",
            "Добавьте папку программы в исключения антивируса.",
        )
    else:
        add("antivirus", title, LEVEL_OK, "сторонний не найден")

    title = "Системный прокси"
    if facts.proxy_server is None and facts.proxy_script is None:
        items.append(_unknown("proxy", title))
    elif facts.proxy_server:
        add(
            "proxy",
            title,
            LEVEL_WARN,
            f"включён ({facts.proxy_server}) — браузеры идут через него, а не напрямую",
            "Если прокси вы не включали, отключите его: Параметры Windows → Сеть и Интернет → Прокси-сервер.",
        )
    elif facts.proxy_script:
        add(
            "proxy",
            title,
            LEVEL_WARN,
            f"задан сценарий автонастройки ({facts.proxy_script}) — он решает, какие сайты пускать через прокси",
            "Если сценарий вы не задавали, уберите его: Параметры Windows → Сеть и Интернет → Прокси-сервер.",
        )
    else:
        add("proxy", title, LEVEL_OK, "выключен")

    title = "Файл hosts"
    if facts.hosts_readable is None and facts.hosts_overrides is None:
        items.append(_unknown("hosts", title))
    elif facts.hosts_readable is False:
        add(
            "hosts",
            title,
            LEVEL_WARN,
            "не читается — программа не сможет ни проверить, ни изменить его",
            "Откройте страницу «Редактор hosts» и нажмите «Восстановить права».",
        )
    elif facts.hosts_overrides:
        shown = _short(f"{host} → {address}" for host, address in facts.hosts_overrides)
        add(
            "hosts",
            title,
            LEVEL_INFO,
            f"в нём заданы адреса проверяемых сайтов: {shown}. Программы берут адрес оттуда, а не у DNS",
        )
    else:
        add("hosts", title, LEVEL_OK, "записей для проверяемых сайтов нет")

    title = "Часы компьютера"
    if facts.clock_skew_s is None:
        items.append(_unknown("clock", title))
    elif abs(facts.clock_skew_s) > CLOCK_SKEW_LIMIT_S:
        direction = "спешат" if facts.clock_skew_s > 0 else "отстают"
        add(
            "clock",
            title,
            LEVEL_WARN,
            f"{direction} на {_minutes(facts.clock_skew_s)} — из-за этого сайты показывают ошибки сертификатов",
            "Включите автоматическую установку времени: Параметры Windows → Время и язык → Дата и время.",
        )
    else:
        add("clock", title, LEVEL_OK, "идут верно")

    return tuple(items)
