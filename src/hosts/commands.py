from __future__ import annotations

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


def refresh_applied_selection(hosts_manager=None) -> HostsCommandResult:
    """При запуске переписывает уже применённый блок hosts, если каталог сменил адреса."""
    from hosts.proxy_domains import has_saved_user_hosts_selection
    from log.log import log

    manager = hosts_manager or create_hosts_manager(
        status_callback=lambda message: log(f"Hosts при запуске: {message}", "DEBUG")
    )
    changed, reason = manager.refresh_applied_service_selection(
        load_user_selection(),
        has_saved_selection=has_saved_user_hosts_selection(),
    )
    return HostsCommandResult(success=True, message=reason, changed=bool(changed))


def load_page_snapshot():
    """Снимок страницы Hosts: каталог, текст hosts и доступ. Только чтение."""
    from hosts.page_snapshot import load_page_snapshot as _load_page_snapshot

    return _load_page_snapshot()


def apply_hosts_draft(selection: dict[str, str], adobe: bool | None = None) -> HostsApplyResult:
    """Записывает черновик страницы одной операцией и возвращает свежий снимок.

    selection — полный выбор «сервис → профиль»; adobe — None, если блок
    Adobe не меняли.
    """
    from log.log import log

    manager = create_hosts_manager(
        status_callback=lambda message: log(f"Hosts: {message}", "DEBUG")
    )
    selection = dict(selection or {})
    success = bool(manager.apply_service_dns_selections(selection))
    message = str(manager.last_status or "")
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
    return HostsApplyResult(success=success, message=message, snapshot=snapshot)


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
