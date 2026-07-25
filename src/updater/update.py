"""Небольшие общие функции обновлятора.

Сетевой и файловый конвейер находится в ``updater.update_pipeline``.
Здесь остаются только запуск установщика и сравнение версий, чтобы у
обновления был один рабочий путь без старого универсального worker-а.
"""

from __future__ import annotations

import base64
import os
import subprocess
from collections.abc import Sequence

from packaging import version

from log.log import log

from .github_release import normalize_version


def _quote_powershell_literal(value: str) -> str:
    """Возвращает строку, которую PowerShell прочитает без подстановок."""
    return "'" + value.replace("'", "''") + "'"


def launch_installer_winapi(exe_path: str, arguments: Sequence[str]) -> bool:
    """Запускает Inno Setup через PowerShell с правами администратора.

    Функция вызывается только из отдельной стадии конвейера. Ожидание ответа
    UAC поэтому не может остановить главный поток Qt.
    """
    if not os.path.exists(exe_path):
        log(f"❌ Файл установщика не найден: {exe_path}", "🔁❌ ERROR")
        return False

    file_size = os.path.getsize(exe_path)
    log(f"📦 Размер установщика: {file_size / 1024 / 1024:.1f} MB", "🔁 UPDATE")
    log(f"🚀 Запуск через PowerShell (RunAs): {exe_path}", "🔁 UPDATE")

    argument_list = [str(argument) for argument in arguments]
    argument_line = subprocess.list2cmdline(argument_list)
    log(f"   Параметры: {argument_line}", "🔁 UPDATE")

    try:
        ps_command = (
            "$ErrorActionPreference='Stop';"
            "try {"
            "$process=Start-Process -FilePath "
            f"{_quote_powershell_literal(os.path.abspath(exe_path))} "
            f"-ArgumentList {_quote_powershell_literal(argument_line)} "
            "-Verb RunAs -PassThru;"
            "if($null -eq $process){exit 1};"
            "exit 0"
            "} catch {"
            "[Console]::Error.WriteLine($_.Exception.Message);"
            "exit 1"
            "}"
        )
        encoded_command = base64.b64encode(ps_command.encode("utf-16le")).decode("ascii")
        process = subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded_command,
            ],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            _, stderr_bytes = process.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            log("❌ Не получен ответ на запрос прав администратора", "🔁❌ ERROR")
            return False

        if process.returncode != 0:
            stderr = stderr_bytes.decode("utf-8", errors="ignore").strip()
            log(
                f"❌ PowerShell ошибка (код {process.returncode}): {stderr}",
                "🔁❌ ERROR",
            )
            return False

        log("✅ Установщик запущен успешно", "🔁 UPDATE")
        return True
    except Exception as exc:
        log(f"❌ Ошибка запуска: {exc}", "🔁❌ ERROR")
        return False


def compare_versions(v1: str, v2: str) -> int:
    """Сравнивает две версии: -1, 0 или 1."""
    try:
        ver1 = version.parse(normalize_version(v1))
        ver2 = version.parse(normalize_version(v2))
        return (ver1 > ver2) - (ver1 < ver2)
    except Exception as exc:
        log(f"Ошибка сравнения версий '{v1}' и '{v2}': {exc}", "🔁❌ ERROR")
        return (v1 > v2) - (v1 < v2)


__all__ = ["compare_versions", "launch_installer_winapi"]
