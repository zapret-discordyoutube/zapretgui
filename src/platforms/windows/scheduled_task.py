"""
Автозапуск GUI через Планировщик заданий Windows и штатный schtasks.exe.

Zapret.exe собран с manifest requireAdministrator, поэтому ярлык в папке
автозагрузки Windows молча игнорирует на системах с включённым UAC. Задача
планировщика с RunLevel=HighestAvailable запускается с правами администратора
без нового UAC-запроса при входе пользователя.

Задача всегда работает в интерактивной сессии текущего пользователя, а не от
SYSTEM. Иначе окно и значок в трее попадут в изолированную session 0.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from html import escape as _escape_xml_text, unescape as _unescape_xml_text
import locale
import ntpath
import os
import re
import subprocess
import tempfile
from pathlib import Path

from config.runtime_layout import RUNTIME_DIR_NAME, RUNTIME_EXE_NAME
from log.log import log


AUTOSTART_TASK_NAME = "ZapretGUI Autostart"
AUTOSTART_TASK_ARGS = "--tray"
_TASK_XML_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
_NAME_SAM_COMPATIBLE = 2


def _current_user_id() -> str:
    """Возвращает DOMAIN\\User через штатный Windows API."""
    try:
        size = wintypes.ULONG(256)
        buffer = ctypes.create_unicode_buffer(size.value)
        get_user_name = ctypes.windll.secur32.GetUserNameExW
        if get_user_name(_NAME_SAM_COMPATIBLE, buffer, ctypes.byref(size)):
            value = buffer.value.strip()
            if value:
                return value
        if size.value > len(buffer):
            buffer = ctypes.create_unicode_buffer(size.value)
            if get_user_name(_NAME_SAM_COMPATIBLE, buffer, ctypes.byref(size)):
                value = buffer.value.strip()
                if value:
                    return value
    except Exception:
        pass

    # Безопасный запасной вариант для редких окружений, где Secur32 недоступен.
    user = os.environ.get("USERNAME", "").strip()
    domain = os.environ.get("USERDOMAIN", "").strip()
    if not user:
        raise OSError("Windows не вернул имя текущего пользователя")
    return f"{domain}\\{user}" if domain else user


def _schtasks_executable() -> str:
    windows_root = os.environ.get("SystemRoot", r"C:\Windows").strip()
    return ntpath.join(windows_root, "System32", "schtasks.exe")


def _run_schtasks(arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [_schtasks_executable(), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _decode_process_output(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    if not data:
        return ""

    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        candidates = ("utf-16",)
    elif data.startswith(b"<\x00") or data.count(b"\x00") > len(data) // 4:
        candidates = ("utf-16-le", "utf-16")
    else:
        candidates = (
            "utf-8-sig",
            locale.getpreferredencoding(False),
            "mbcs",
            "cp866",
        )

    for encoding in candidates:
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _canonical_app_working_directory(exe_path: str) -> str:
    """Возвращает корень установки только для канонического runtime exe."""
    normalized = ntpath.normpath(str(exe_path or "").strip())
    runtime_dir = ntpath.dirname(normalized)
    app_root = ntpath.dirname(runtime_dir)
    if (
        ntpath.basename(normalized).casefold() != RUNTIME_EXE_NAME.casefold()
        or ntpath.basename(runtime_dir).casefold() != RUNTIME_DIR_NAME.casefold()
        or not app_root
    ):
        raise ValueError(
            "Автозапуск разрешён только для установленного "
            f"{RUNTIME_DIR_NAME}\\{RUNTIME_EXE_NAME}: {exe_path}"
        )
    return app_root


def _build_autostart_task_xml(exe_path: str, user_id: str) -> bytes:
    working_directory = _canonical_app_working_directory(exe_path)
    user_id = str(user_id or "").strip()
    if not user_id:
        raise ValueError("Для задачи автозапуска не определён текущий пользователь")

    escaped_user = _escape_xml_text(user_id, quote=False)
    escaped_exe = _escape_xml_text(exe_path, quote=False)
    escaped_working_directory = _escape_xml_text(working_directory, quote=False)
    task_xml = f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="{_TASK_XML_NAMESPACE}">
  <RegistrationInfo>
    <Author>ZapretGUI</Author>
    <Description>Автозапуск ZapretGUI в трее при входе в Windows</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escaped_user}</UserId>
      <Delay>PT3S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escaped_user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escaped_exe}</Command>
      <Arguments>{AUTOSTART_TASK_ARGS}</Arguments>
      <WorkingDirectory>{escaped_working_directory}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'''
    return task_xml.encode("utf-16")


def _format_schtasks_error(result: subprocess.CompletedProcess[bytes]) -> str:
    detail = _decode_process_output(result.stderr).strip()
    if not detail:
        detail = _decode_process_output(result.stdout).strip()
    return detail or f"schtasks.exe завершился с кодом {result.returncode}"


def create_or_update_autostart_task(
    exe_path: str,
    *,
    task_name: str = AUTOSTART_TASK_NAME,
) -> bool:
    """Регистрирует или обновляет задачу автозапуска текущего пользователя."""
    exe_path = str(exe_path or "").strip()
    if not exe_path:
        log("Autostart task create failed: empty exe path", "ERROR")
        return False

    temporary_path: Path | None = None
    try:
        task_xml = _build_autostart_task_xml(exe_path, _current_user_id())
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".xml",
            prefix="zapretgui-autostart-",
            delete=False,
        ) as temporary:
            temporary.write(task_xml)
            temporary_path = Path(temporary.name)

        result = _run_schtasks(
            ["/Create", "/TN", task_name, "/XML", str(temporary_path), "/F"]
        )
        if result.returncode == 0:
            return True
        log(f"Autostart task create failed: {_format_schtasks_error(result)}", "WARNING")
        return False
    except Exception as exc:
        log(f"Autostart task create failed: {exc}", "WARNING")
        return False
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _first_task_action(xml_text: str) -> tuple[str, str] | None:
    prefix = r"(?:[A-Za-z_][\w.-]*:)?"
    execute = re.search(
        rf"<{prefix}Exec\b[^>]*>(.*?)</{prefix}Exec\s*>",
        xml_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if execute is None:
        return None

    def _value(name: str) -> str:
        match = re.search(
            rf"<{prefix}{name}\b[^>]*>(.*?)</{prefix}{name}\s*>",
            execute.group(1),
            flags=re.IGNORECASE | re.DOTALL,
        )
        return _unescape_xml_text(match.group(1)).strip() if match else ""

    command = _value("Command")
    return (command, _value("Arguments")) if command else None


def get_autostart_task_action(
    *,
    task_name: str = AUTOSTART_TASK_NAME,
) -> tuple[str, str] | None:
    """Возвращает путь и аргументы действия задачи или None, если её нет."""
    try:
        result = _run_schtasks(["/Query", "/TN", task_name, "/XML", "ONE"])
        if result.returncode != 0:
            return None
        return _first_task_action(_decode_process_output(result.stdout))
    except Exception:
        return None


def autostart_task_exists(*, task_name: str = AUTOSTART_TASK_NAME) -> bool:
    return get_autostart_task_action(task_name=task_name) is not None


def delete_autostart_task(*, task_name: str = AUTOSTART_TASK_NAME) -> bool:
    """Удаляет задачу. False означает, что задача отсутствовала или возникла ошибка."""
    try:
        result = _run_schtasks(["/Delete", "/TN", task_name, "/F"])
        if result.returncode == 0:
            return True
        log(f"Autostart task delete skipped: {_format_schtasks_error(result)}", "DEBUG")
        return False
    except Exception as exc:
        log(f"Autostart task delete skipped: {exc}", "DEBUG")
        return False
