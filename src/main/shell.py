from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time

from config.build_info import APP_VERSION

from log.log import log

from startup.admin_check import is_admin
from utils.exit_steps import register_exit_step
from utils.subproc import run_hidden


def handle_update_mode(argv: list[str] | None = None) -> None:
    args = list(argv or sys.argv)
    if len(args) < 4:
        log("--update: недостаточно аргументов", "❌ ERROR")
        return

    old_exe, new_exe = args[2], args[3]

    for _ in range(10):
        if not os.path.exists(old_exe) or os.access(old_exe, os.W_OK):
            break
        time.sleep(0.5)

    try:
        shutil.copy2(new_exe, old_exe)
        run_hidden([old_exe])
        log("Файл обновления применён", "INFO")
    except Exception as exc:
        log(f"Ошибка в режиме --update: {exc}", "❌ ERROR")
    finally:
        try:
            os.remove(new_exe)
        except FileNotFoundError:
            pass


def _update_is_being_installed() -> bool:
    """Прямо сейчас установщик заменяет программу на более новую версию.

    Этот запуск ему помешал бы: старая версия начала бы второе обновление и
    заняла место новой. Поэтому программа тихо выходит — новую версию откроет
    сам установщик. Любая ошибка чтения записи значит «не мешаем запуску»:
    программа обязана открываться, даже если каталог обновления недоступен.
    """
    try:
        from updater.install.interrupted import installation_in_progress

        record = installation_in_progress()
    except Exception:
        return False
    if record is None:
        return False
    log(
        f"Идёт установка обновления v{record.version}: этот запуск отменён, "
        "новую версию откроет установщик",
        "🔁 UPDATE",
    )
    return True


def shell_bootstrap(argv: list[str] | None = None) -> bool:
    args = list(argv or sys.argv)

    if "--version" in args:
        ctypes.windll.user32.MessageBoxW(None, APP_VERSION, "Zapret – версия", 0x40)
        sys.exit(0)

    if "--update" in args and len(args) > 3:
        handle_update_mode(args)
        sys.exit(0)

    start_in_tray = "--tray" in args

    # До запроса прав администратора: иначе человек увидел бы окно UAC от
    # запуска, который тут же закроется.
    if _update_is_being_installed():
        sys.exit(0)

    if not is_admin():
        params = subprocess.list2cmdline(list(args[1:]))
        shell_exec_result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            sys.executable,
            params,
            None,
            1,
        )
        if int(shell_exec_result) <= 32:
            ctypes.windll.user32.MessageBoxW(
                None,
                "Не удалось запросить права администратора.",
                "Zapret",
                0x10,
            )
        sys.exit(0)

    from startup.single_instance import (
        create_mutex,
        create_show_event,
        release_mutex,
        signal_show_event,
    )

    mutex_handle, already_running = create_mutex("ZapretSingleInstance")
    if already_running:
        if signal_show_event():
            log("Существующему экземпляру послан сигнал показать окно", "INFO")
        elif start_in_tray:
            log("Второй экземпляр из автозапуска — выходим молча", "INFO")
        else:
            ctypes.windll.user32.MessageBoxW(
                None,
                "Экземпляр Zapret уже запущен, но не удалось показать окно!",
                "Zapret",
                0x40,
            )
        sys.exit(0)

    register_exit_step(
        "освобождение мьютекса единственного экземпляра",
        lambda: release_mutex(mutex_handle),
    )
    # Событие создаётся до инициализации Qt: второй экземпляр, запущенный
    # во время загрузки первого, доставит сигнал без потерь.
    create_show_event()
    return bool(start_in_tray)
