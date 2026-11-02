from __future__ import annotations

import threading

from hosts.state import HostsApplyResult, HostsCommandResult, HostsFileText


def read_hosts_file():
    from hosts.hosts import safe_read_hosts_file

    return safe_read_hosts_file()


def write_hosts_file(content):
    from hosts.hosts import safe_write_hosts_file

    return safe_write_hosts_file(content)


def restore_hosts_permissions() -> HostsCommandResult:
    from hosts.hosts import restore_hosts_permissions as _restore_hosts_permissions

    success, message = _restore_hosts_permissions()
    return HostsCommandResult(success=bool(success), message=str(message or ""))


def create_hosts_manager(status_callback=None):
    from hosts.hosts import HostsManager

    return HostsManager(status_callback=status_callback)


_OFF_PROFILES = ("", "off", "откл", "откл.", "0", "false")
# Сколько секунд верить ответу живого адреса; «мёртв» и «не узнали» перепроверяются всегда.
_ALIVE_CACHE_S = 600.0
_PROBE_CACHE: dict[tuple[str, str, str], tuple[float, object]] = {}
# Проверки не идут одновременно: фоновая, начатая со старым выбором, иначе затёрла бы
# набор, который только что собрала проверка по нажатию тумблера.
_CHECK_LOCK = threading.Lock()
# Запись блока и сохранение выбора — одно целое: сверка, начатая фоновой проверкой, иначе могла
# бы прочитать прежний выбор и записать его поверх только что нажатого тумблера.
_WRITE_LOCK = threading.Lock()


def hosts_write_lock() -> threading.Lock:
    """Замок записи hosts: тот, под которым страница Hosts пишет свой блок.

    Остальным, кто читает hosts, правит текст и пишет обратно (Telegram Proxy,
    блокировки MAX и госСМИ), нужно держать тот же замок на всё чтение и запись:
    иначе фоновая сверка блока Hosts успевала бы вписаться между их чтением и
    записью, и запись возвращала бы файл к тексту до сверки.
    """
    return _WRITE_LOCK
# Набор адресов до последней фоновой проверки. Страница узнаёт включённый сервис по тому,
# что его адреса записаны в hosts; если новые адреса записать не удалось, набор возвращается
# к прежнему — иначе включённые сервисы показались бы выключенными.
_OVERLAY_BEFORE_REFRESH: dict | None = None


def _fresh_addresses(host: str, want_ipv6: bool) -> list[str]:
    """Адреса имени по шифрованному DNS: обычному ответу для таких имён в России верить нельзя."""
    from utils.dns_reference import reference_ask
    from utils.dns_wire import TYPE_A, TYPE_AAAA
    from utils.socket_cancel import SocketCancel

    ask = reference_ask(SocketCancel(), timeout_s=3.0)
    found: list[str] = []
    for rtype in (TYPE_A, TYPE_AAAA) if want_ipv6 else (TYPE_A,):
        answer = ask(host, rtype)
        if answer is not None:
            found.extend(answer.values(rtype))
    return found


def check_addresses(selection: dict[str, str] | None = None, *, why, recheck=None):
    """Умный движок: проверяет адреса включённых сервисов и запоминает рабочие (hosts.address_engine).

    ``selection`` — выбор «сервис → профиль»; None — сохранённый выбор. ``why`` — повод
    (``utils.probe_gate.Why``): ``USER`` — человек только что переключил сервис, мёртвый адрес
    убирается сразу; ``SCHEDULE`` — плановая проверка, только первые адреса, поиск лишь при беде.
    ``recheck`` — названия сервисов, которые проверить сейчас (None — все включённые).
    Сам файл hosts здесь не меняется: его переписывает refresh_applied_selection, если
    проверенные адреса отличаются от записанных.
    """
    with _CHECK_LOCK:
        return _check_addresses(selection, why=why, recheck=recheck)


def _check_addresses(selection: dict[str, str] | None, *, why, recheck):
    global _OVERLAY_BEFORE_REFRESH
    import time

    from hosts import address_engine, profile_dns, proxy_domains
    from hosts.address_probe import ALIVE, check_address, sweep
    from hosts.ipv6_detection import is_ipv6_available
    from hosts.relay_check import internet_reachable
    from log.log import log

    chosen = load_user_selection() if selection is None else dict(selection or {})
    active = {
        name: profile.strip()
        for name, profile in chosen.items()
        if isinstance(name, str) and isinstance(profile, str) and profile.strip().lower() not in _OFF_PROFILES
    }
    def _probe(address: str, host: str, front: str):
        # Адрес, который ответил несколько минут назад, заново не проверяется: иначе каждое
        # переключение любого тумблера перепроверяло бы все включённые сервисы.
        key = (address, host, front)
        cached = _PROBE_CACHE.get(key)
        if cached is not None and time.monotonic() - cached[0] < _ALIVE_CACHE_S:
            return cached[1]
        result = check_address(address, host, front=front)
        if result.verdict == ALIVE:
            _PROBE_CACHE[key] = (time.monotonic(), result)
        else:
            _PROBE_CACHE.pop(key, None)
        return result

    previous = proxy_domains.get_engine_overlay()
    catalog = proxy_domains.load_shipped_catalog()
    overlay, report = address_engine.check_selection(
        active,
        catalog=catalog,
        previous=previous,
        relays=proxy_domains.relay_addresses_of(catalog),
        probe=_probe,
        resolve=_fresh_addresses,
        # Свежие посредники профиля — у DNS-сервера самого профиля (hosts.profile_dns).
        profile_resolve=profile_dns.ask_profile,
        network_ok=internet_reachable,
        ipv6=bool(is_ipv6_available()),
        why=why,
        recheck=recheck,
        sweep=sweep,
    )
    if overlay is not None:
        if selection is None and report.changed:
            _OVERLAY_BEFORE_REFRESH = previous
        proxy_domains.set_engine_overlay(overlay)
    text = describe_address_check(report)
    log(f"Hosts, проверка адресов: проверено {report.checked}, изменилось: {report.changed}. {text}".strip(), "INFO")
    return report


def describe_address_check(report) -> str:
    """Что нашла проверка адресов — одной-двумя фразами для человека. Пусто — сказать нечего."""
    if report is None or not report.network:
        return ""
    parts: list[str] = []
    if report.added:
        parts.append("Взяты свежие адреса: " + ", ".join(_short(name) for name in report.added) + ".")
    if report.dropped:
        parts.append("Неработающие адреса убраны: " + ", ".join(_short(name) for name in report.dropped) + ".")
    if report.silent:
        parts.append(
            "Ни один адрес не ответил: " + ", ".join(_short(name) for name in report.silent) + " — оставлены прежние адреса."
        )
    return " ".join(parts)


def _short(service_name: str) -> str:
    """Название сервиса без пояснения в скобках."""
    return str(service_name).split(" (", 1)[0]


def refresh_applied_selection(hosts_manager=None) -> HostsCommandResult:
    """При запуске переписывает уже применённый блок hosts, если каталог сменил адреса."""
    from hosts.proxy_domains import get_all_services, has_saved_user_hosts_selection
    from log.log import log

    if not get_all_services():
        # Каталог не загрузился (база отклонена, нет файла): выбор прочитался бы пустым,
        # и блок переписался бы пустым — молча стёрлись бы все записи человека. Ждём каталога.
        return HostsCommandResult(success=False, message="каталог hosts не загрузился — блок не трогается")
    manager = hosts_manager or create_hosts_manager(
        status_callback=lambda message: log(f"Hosts при запуске: {message}", "DEBUG")
    )
    with _WRITE_LOCK:
        changed, reason = manager.refresh_applied_service_selection(
            load_user_selection(),
            has_saved_selection=has_saved_user_hosts_selection(),
        )
    _settle_overlay_after_refresh(write_failed=bool(getattr(manager, "refresh_write_failed", False)))
    return HostsCommandResult(success=True, message=reason, changed=bool(changed))


def _settle_overlay_after_refresh(*, write_failed: bool) -> None:
    """После сверки hosts: если новые адреса записать не удалось, вернуть набор, который записан."""
    global _OVERLAY_BEFORE_REFRESH
    from hosts import proxy_domains
    from log.log import log

    with _CHECK_LOCK:
        before, _OVERLAY_BEFORE_REFRESH = _OVERLAY_BEFORE_REFRESH, None
        if write_failed and before is not None:
            proxy_domains.set_engine_overlay(before)
            log("Hosts: проверенные адреса записать не удалось — оставлены те, что уже в файле", "WARNING")


def load_page_snapshot():
    """Снимок страницы Hosts: каталог, текст hosts и доступ. Только чтение."""
    from hosts.page_snapshot import load_page_snapshot as _load_page_snapshot

    return _load_page_snapshot()


def apply_hosts_draft(selection: dict[str, str], adobe: bool | None = None) -> HostsApplyResult:
    """Записывает черновик страницы одной операцией и возвращает свежий снимок.

    selection — полный выбор «сервис → профиль»; adobe — None, если блок
    Adobe не меняли.

    Запись идёт сразу, с теми адресами, которые известны сейчас: нажатие тумблера
    не ждёт сеть. Проверку адресов только что включённых сервисов страница
    запускает следом (``recheck_applied``); найдёт лучший порядок — блок будет
    переписан ещё раз, уже без участия человека.
    """
    from log.log import log
    from utils.probe_gate import Why

    manager = create_hosts_manager(
        status_callback=lambda message: log(f"Hosts: {message}", "DEBUG")
    )
    selection = dict(selection or {})
    with _WRITE_LOCK:
        saved = load_user_selection()
        touched = tuple(
            name
            for name, profile in selection.items()
            if isinstance(name, str)
            and isinstance(profile, str)
            and profile.strip().lower() not in _OFF_PROFILES
            and saved.get(name) != profile
        )
        success = bool(manager.apply_service_dns_selections(selection))
        message = str(manager.last_status or "")
        stranded = list(getattr(manager, "stranded_services", ()) or ())
        if not success and stranded:
            # Ни один посредник профиля не ответил: возможно, DNS-сервер профиля уже перевёл
            # сервис на другие. Свежие посредники спрашиваются у него самого, и запись — ещё раз.
            log(f"Hosts: посредники не отвечают ({', '.join(stranded)}) — спрашиваю свежие у DNS профиля", "INFO")
            try:
                check_addresses(selection, why=Why.USER, recheck=set(stranded))
            except Exception as exc:
                log(f"Hosts: свежие посредники не получены: {exc}", "WARNING")
            else:
                success = bool(manager.apply_service_dns_selections(selection))
                message = str(manager.last_status or message)
        if success:
            save_user_selection(selection)
        if success and adobe is not None:
            success = bool(manager.add_adobe_domains() if adobe else manager.remove_adobe_domains())
            message = str(manager.last_status or message)

    snapshot = None
    try:
        snapshot = load_page_snapshot()
    except Exception as exc:
        log(f"Hosts: не удалось перечитать состояние после записи: {exc}", "WARNING")
    return HostsApplyResult(success=success, message=message, snapshot=snapshot, touched=touched if success else ())


def recheck_applied(names) -> HostsCommandResult:
    """Сразу после записи: проверить адреса только что включённых сервисов.

    Если проверка нашла порядок лучше записанного (первый адрес молчит, есть живой) —
    блок в hosts переписывается. ``message`` — что сказать человеку (пусто — нечего),
    ``changed`` — переписан ли файл.
    """
    from utils.probe_gate import Why

    report = check_addresses(None, why=Why.USER, recheck=set(names or ()))
    changed = bool(report.changed) and bool(refresh_applied_selection().changed)
    _check_in_zapret_dns(names)
    note = describe_address_check(report) if changed or report.silent else ""
    return HostsCommandResult(success=True, message=note, changed=changed)


def _check_in_zapret_dns(names) -> None:
    """Человек только что включил профиль «Zapret DNS»: сразу отметиться у посредника сервера."""
    from config import zapret_dns
    from log.log import log

    selection = load_user_selection()
    if not any(selection.get(name) == zapret_dns.CATALOG_PROFILE_ID for name in names or ()):
        return
    try:
        from dns.zapret_dns_server import check_in

        check_in()
    except Exception as exc:
        log(f"Zapret DNS: отметка для посредника не отправлена: {exc}", "WARNING")


def load_hosts_text() -> HostsFileText:
    """Весь текст hosts для редактора. Только чтение: файл не создаётся."""
    from hosts.hosts import HOSTS_PATH, is_file_readonly, safe_read_hosts_file

    exists = HOSTS_PATH.exists()
    text = safe_read_hosts_file()
    return HostsFileText(
        text=text or "",
        path=str(HOSTS_PATH),
        exists=exists,
        readable=text is not None,
        read_only=bool(exists and is_file_readonly(HOSTS_PATH)),
    )


def save_hosts_text(text: str) -> HostsCommandResult:
    """Записывает весь текст hosts из редактора как есть.

    Защиту «только чтение» не снимает: это делает только кнопка
    «Восстановить права».
    """
    from hosts.hosts import HOSTS_PATH, is_file_readonly, safe_write_hosts_file

    if HOSTS_PATH.exists() and is_file_readonly(HOSTS_PATH):
        return HostsCommandResult(
            False,
            "Файл hosts защищён от записи (стоит «только чтение»). Снимите защиту в меню страницы Hosts.",
        )
    content = str(text or "")
    if content and not content.endswith("\n"):
        content += "\n"
    if not safe_write_hosts_file(content):
        return HostsCommandResult(False, "Не удалось записать файл hosts: нет прав или файл занят.")
    return HostsCommandResult(True, str(HOSTS_PATH), changed=True)


def load_user_selection() -> dict[str, str]:
    from hosts.proxy_domains import load_user_hosts_selection

    try:
        return dict(load_user_hosts_selection() or {})
    except Exception:
        return {}


def save_user_selection(selection: dict[str, str]) -> bool:
    from hosts.proxy_domains import save_user_hosts_selection

    try:
        return bool(save_user_hosts_selection(dict(selection)))
    except Exception:
        return False


def get_hosts_path_str() -> str:
    import os

    from utils.subproc import get_system32_path

    try:
        if os.name == "nt":
            sys_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
            if sys_root:
                return os.path.join(sys_root, "System32", "drivers", "etc", "hosts")
        return os.path.join(get_system32_path(), "drivers", "etc", "hosts")
    except Exception:
        return os.path.join(get_system32_path(), "drivers", "etc", "hosts")


def open_hosts_file() -> HostsCommandResult:
    import ctypes
    import os

    hosts_path = get_hosts_path_str()
    if not os.path.exists(hosts_path):
        return HostsCommandResult(False, f"Файл не найден: {hosts_path}")

    try:
        ctypes.windll.shell32.ShellExecuteW(None, "runas", "notepad.exe", hosts_path, None, 1)
        return HostsCommandResult(True, hosts_path)
    except Exception as exc:
        return HostsCommandResult(False, str(exc))
