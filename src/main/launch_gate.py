"""Проверка запуска Zapret: всё, без чего программа стартовать не может.

Выполняется самой первой, до любых импортов интерфейса и логгера:

1. Zapret запущен как собранная программа, а не из исходников.
2. Zapret.exe лежит в папке ``_internal`` внутри папки установки.
3. Windows подходит по версии.
4. Папка установки стала рабочей: от неё winws2 ищет ``@bin/``, ``@lua/``
   и ``=lists/`` из пресетов.

Если что-то не так, пользователь видит одно понятное окно, и программа
закрывается. Так же поступает ``run_guarded``, если программа неожиданно
упала ещё до появления своего окна.
"""

from __future__ import annotations

import datetime
import os
import sys
import traceback
from typing import Callable

from config.runtime_layout import (
    APPLICATION_PATHS,
    InvalidInstallLayout,
    RUNTIME_DIR_NAME,
    SourceApplicationLaunchForbidden,
    require_packaged_application,
    require_valid_install_layout,
)
from startup.windows_version_guard import WINDOWS_VERSION_ERROR_TITLE, current_windows_support


SOURCE_LAUNCH_TITLE = "Zapret — запуск запрещён"
INSTALL_LAYOUT_TITLE = "Zapret — неверная папка запуска"
INSTALL_ROOT_TITLE = "Zapret — нет доступа к папке"
STARTUP_CRASH_TITLE = "Zapret — не удалось запустить"
STARTUP_CRASH_FILE_NAME = "startup_crash.log"


class LaunchBlocked(Exception):
    """Запуск невозможен; ``message`` показывается пользователю как есть."""

    def __init__(self, title: str, message: str) -> None:
        super().__init__(message)
        self.title = title
        self.message = message


def show_launch_error(title: str, message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
    except Exception:
        print(f"{title}\n\n{message}", file=sys.stderr)


def _check_launch() -> None:
    try:
        require_packaged_application()
    except SourceApplicationLaunchForbidden as exc:
        raise LaunchBlocked(SOURCE_LAUNCH_TITLE, str(exc)) from exc

    try:
        require_valid_install_layout()
    except InvalidInstallLayout as exc:
        raise LaunchBlocked(
            INSTALL_LAYOUT_TITLE,
            "Zapret.exe запущен не из своей папки:\n"
            f"{sys.executable}\n\n"
            f"Программа должна лежать в папке {RUNTIME_DIR_NAME} внутри папки установки. "
            "Запустите Zapret через ярлык на рабочем столе или в меню «Пуск». "
            "Если ярлык не помогает, переустановите Zapret.",
        ) from exc

    windows = current_windows_support()
    if not windows.supported:
        raise LaunchBlocked(WINDOWS_VERSION_ERROR_TITLE, windows.message)

    app_dir = APPLICATION_PATHS.root
    try:
        os.chdir(app_dir)
    except OSError as exc:
        raise LaunchBlocked(
            INSTALL_ROOT_TITLE,
            "Zapret не может открыть свою папку установки:\n"
            f"{app_dir}\n\n"
            f"Причина: {exc}\n\n"
            "Проверьте, что у вашей учётной записи есть доступ к этой папке. "
            "Если путь очень длинный, переустановите Zapret в короткую папку, "
            "например C:\\Zapret.",
        ) from exc


def pass_launch_gate() -> None:
    """Пропускает запуск дальше или показывает окно и закрывает программу."""
    try:
        _check_launch()
    except LaunchBlocked as blocked:
        show_launch_error(blocked.title, blocked.message)
        raise SystemExit(1) from None


def _write_startup_crash_report(exc: BaseException) -> str:
    path = APPLICATION_PATHS.crash_logs_dir / STARTUP_CRASH_FILE_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n=== Падение при запуске ===\n")
            handle.write(f"Время: {datetime.datetime.now().isoformat(timespec='seconds')}\n")
            handle.write(f"Программа: {sys.executable}\n\n")
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=handle)
    except OSError:
        return ""
    return str(path)


def run_guarded(run: Callable[[], None]) -> None:
    """Запускает программу; падение до её окна превращает в понятное окно."""
    try:
        run()
    except Exception as exc:
        report_path = _write_startup_crash_report(exc)
        message = (
            "Zapret не смог запуститься из-за внутренней ошибки.\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
        )
        if report_path:
            message += f"Подробный отчёт сохранён в файл:\n{report_path}\n\n"
        message += "Попробуйте переустановить Zapret. Если не поможет, пришлите этот отчёт в поддержку."
        show_launch_error(STARTUP_CRASH_TITLE, message)
        raise SystemExit(1) from None


__all__ = ["LaunchBlocked", "pass_launch_gate", "run_guarded", "show_launch_error"]
