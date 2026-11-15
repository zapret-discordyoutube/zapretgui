from __future__ import annotations

import os
import shutil
import sys
import time

from config.build_info import APP_VERSION

from log.log import log
from platforms import MessageKind, current as current_platform

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
        current_platform().messages.show("Zapret – версия", APP_VERSION, MessageKind.INFO)
        sys.exit(0)

    if "--update" in args and len(args) > 3:
        handle_update_mode(args)
        sys.exit(0)

    if "--apply-update" in args:
        # Новая версия из пробного дерева ставит себя сама (обновление по
        # файлам). Права администратора у неё уже есть: её запустила
        # работающая программа.
        from updater.install.apply_mode import run_apply_mode

        position = args.index("--apply-update")
        sys.exit(run_apply_mode(args[position + 1] if len(args) > position + 1 else ""))

    start_in_tray = "--tray" in args

    # До запроса прав администратора: иначе человек увидел бы окно UAC от
    # запуска, который тут же закроется.
    if _update_is_being_installed():
        sys.exit(0)

    privileges = current_platform().privileges
    if privileges.gui_needs_elevation and not privileges.is_elevated():
        if not privileges.relaunch_elevated(sys.executable, args[1:]):
            current_platform().messages.show(
                "Zapret",
                "Не удалось запросить права администратора.",
                MessageKind.ERROR,
            )
        sys.exit(0)

    single_instance = current_platform().single_instance
    instance_lock = single_instance.acquire("ZapretSingleInstance")
    if instance_lock.already_running:
        if single_instance.signal_running_instance():
            log("Существующему экземпляру послан сигнал показать окно", "INFO")
        elif start_in_tray:
            log("Второй экземпляр из автозапуска — выходим молча", "INFO")
        else:
            current_platform().messages.show(
                "Zapret",
                "Экземпляр Zapret уже запущен, но не удалось показать окно!",
                MessageKind.INFO,
            )
        sys.exit(0)

    register_exit_step(
        "освобождение мьютекса единственного экземпляра",
        instance_lock.release,
    )
    # Событие создаётся до инициализации Qt: второй экземпляр, запущенный
    # во время загрузки первого, доставит сигнал без потерь.
    single_instance.create_show_signal()
    return bool(start_in_tray)
