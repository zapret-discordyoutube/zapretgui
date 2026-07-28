from __future__ import annotations

"""Наблюдатель обновления, переживающий закрытие приложения.

Приложение закрывается сразу после передачи управления установщику, поэтому
проверить исход установки изнутри невозможно: процесса уже нет. Наблюдатель —
отдельный PowerShell-процесс из каталога состояния обновления. Он живёт вне
каталога установки (установщик завершает все процессы внутри ``{app}``),
дожидается кода возврата установщика, сверяет версию на диске и записывает
итог в ``handoff.json``.

Приложение работает с правами администратора, поэтому наблюдатель обычно
запускается без второго запроса UAC и передаёт свои права установщику.
"""

import os
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from log.log import log

from . import update_paths
from .handoff_state import HandoffState, UpdateHandoffRecord, write_record
from .recovery_hook import RUNONCE_KEY, RUNONCE_VALUE_NAME


WATCHDOG_LOG_LEVEL = "🔁 UPDATE"
WATCHDOG_START_TIMEOUT_SECONDS = 15.0
WATCHDOG_START_POLL_SECONDS = 0.2
PREVIOUS_WATCHDOG_LOG_NAME = "watchdog.prev.log"

# Одна повторная попытка: она лечит разовую помеху вроде занятого файла, а
# бесконечный цикл переустановок при системной причине только вредит.
INSTALL_ATTEMPTS = 2
GUI_EXIT_TIMEOUT_SECONDS = 60
# Сообщение о неудаче не должно держать наблюдателя вечно: у пользователя
# может не быть возможности нажать «ОК» прямо сейчас.
MESSAGE_TIMEOUT_SECONDS = 600

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_SHELL_EXECUTE_MIN_SUCCESS = 32

WATCHDOG_SCRIPT_TEMPLATE = r"""
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$StatePath,
    [switch]$Recovery,
    # Режим без вмешательства в сеанс: ни окон, ни запуска приложений. Нужен
    # там, где показывать модальное сообщение некому.
    [switch]$Unattended
)

$ErrorActionPreference = 'Stop'
$stateDir = Split-Path -Parent $StatePath
$logPath = Join-Path $stateDir 'watchdog.log'
$runOnceKey = 'HKLM:\@RUNONCE_KEY@'
$runOnceName = '@RUNONCE_VALUE_NAME@'
$maxAttempts = @INSTALL_ATTEMPTS@
$guiExitTimeoutSeconds = @GUI_EXIT_TIMEOUT_SECONDS@

function Write-Line([string]$message) {
    $stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    try {
        Add-Content -LiteralPath $logPath -Value "$stamp  $message" -Encoding UTF8
    } catch { }
}

function Read-State {
    $raw = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8
    return $raw | ConvertFrom-Json
}

function Save-State($state, [string]$newState, $exitCode, [string]$installedVersion, [string]$errorText) {
    try {
        $state.state = $newState
        $state.installer_exit_code = $exitCode
        $state.installed_version = $installedVersion
        $state.error = $errorText
        $state.updated_at = [double]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()) / 1000.0
        $temporary = "$StatePath.new"
        ($state | ConvertTo-Json -Depth 5) | Set-Content -LiteralPath $temporary -Encoding UTF8
        Move-Item -LiteralPath $temporary -Destination $StatePath -Force
    } catch {
        Write-Line "Не удалось сохранить состояние: $($_.Exception.Message)"
    }
}

function Get-InstalledVersion([string]$root) {
    if ([string]::IsNullOrWhiteSpace($root)) { return '' }
    $exe = Join-Path $root '_internal\Zapret.exe'
    if (-not (Test-Path -LiteralPath $exe)) { return '' }
    try {
        return [string](Get-Item -LiteralPath $exe).VersionInfo.ProductVersion
    } catch {
        return ''
    }
}

function Test-VersionInstalled([string]$root, [string]$expected) {
    $installed = Get-InstalledVersion $root
    if ([string]::IsNullOrWhiteSpace($installed)) { return $false }
    if ([string]::IsNullOrWhiteSpace($expected)) { return $true }
    return ($installed.Trim() -eq $expected.Trim())
}

function Wait-ForProcessExit([int]$processId, [int]$timeoutSeconds) {
    if ($processId -le 0) { return }
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $running = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if (-not $running) { return }
        Start-Sleep -Milliseconds 250
    }
    Write-Line "Приложение не закрылось за $timeoutSeconds с, продолжаем"
}

function Remove-RecoveryHook {
    try {
        Remove-ItemProperty -Path $runOnceKey -Name $runOnceName -ErrorAction SilentlyContinue
    } catch { }
}

function Show-Message([string]$text) {
    if ($Unattended) {
        Write-Line "Сообщение не показано (режим без вмешательства): $text"
        return
    }
    try {
        $shell = New-Object -ComObject WScript.Shell
        [void]$shell.Popup($text, @MESSAGE_TIMEOUT_SECONDS@, 'Zapret: обновление не завершено', 16)
    } catch {
        Write-Line "Не удалось показать сообщение: $($_.Exception.Message)"
    }
}

function Start-InstalledApplication([string]$root) {
    if ($Unattended) { return }
    $exe = Join-Path $root '_internal\Zapret.exe'
    if (-not (Test-Path -LiteralPath $exe)) { return }
    try {
        Start-Process -FilePath $exe -WorkingDirectory $root | Out-Null
    } catch {
        Write-Line "Не удалось запустить приложение: $($_.Exception.Message)"
    }
}

Write-Line '--- наблюдатель обновления запущен ---'

try {
    $state = Read-State
} catch {
    Write-Line "Состояние обновления недоступно: $($_.Exception.Message)"
    exit 2
}

$targetRoot = [string]$state.target_root
$installer = [string]$state.installer_path
$expectedVersion = [string]$state.version
$arguments = @()
if ($state.arguments) {
    $arguments = @($state.arguments | ForEach-Object { [string]$_ })
}

if ($Recovery) {
    Write-Line 'Режим восстановления после перезагрузки'
    if (Test-VersionInstalled $targetRoot $expectedVersion) {
        Write-Line 'Восстановление не требуется: нужная версия на месте'
        Save-State $state 'succeeded' $state.installer_exit_code (Get-InstalledVersion $targetRoot) ''
        Remove-RecoveryHook
        exit 0
    }
} else {
    Wait-ForProcessExit ([int]$state.gui_pid) $guiExitTimeoutSeconds
}

Save-State $state 'launched' $null (Get-InstalledVersion $targetRoot) ''

$succeeded = $false
$lastExitCode = $null
$failureReason = ''

for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    if (-not (Test-Path -LiteralPath $installer)) {
        $failureReason = "установщик не найден: $installer"
        Write-Line $failureReason
        break
    }

    Write-Line "Попытка $attempt из $maxAttempts : $installer $($arguments -join ' ')"
    try {
        if ($arguments.Count -gt 0) {
            $process = Start-Process -FilePath $installer -ArgumentList $arguments -PassThru -Wait
        } else {
            $process = Start-Process -FilePath $installer -PassThru -Wait
        }
        $lastExitCode = $process.ExitCode
    } catch {
        $lastExitCode = $null
        $failureReason = "установщик не запустился: $($_.Exception.Message)"
        Write-Line $failureReason
        Start-Sleep -Seconds 2
        continue
    }

    Write-Line "Установщик завершился с кодом $lastExitCode"
    if ($lastExitCode -ne 0) {
        $failureReason = "установщик вернул код $lastExitCode"
        Start-Sleep -Seconds 2
        continue
    }

    if (Test-VersionInstalled $targetRoot $expectedVersion) {
        $succeeded = $true
        break
    }

    $failureReason = 'установщик отчитался об успехе, но версия на диске не изменилась'
    Write-Line $failureReason
    Start-Sleep -Seconds 2
}

$installedVersion = Get-InstalledVersion $targetRoot

if ($succeeded) {
    Save-State $state 'succeeded' $lastExitCode $installedVersion ''
    Remove-RecoveryHook
    Write-Line "Обновление подтверждено: установлена версия $installedVersion"
    exit 0
}

if ([string]::IsNullOrWhiteSpace($failureReason)) {
    $failureReason = 'установка не подтверждена'
}
Save-State $state 'failed' $lastExitCode $installedVersion $failureReason
Write-Line "Обновление не подтверждено: $failureReason"

if ([string]::IsNullOrWhiteSpace($installedVersion)) {
    # Приложения на диске нет. Открываем установщик обычным мастером: так
    # пользователь видит ход установки и может довести её до конца сам.
    if ((Test-Path -LiteralPath $installer) -and (-not $Unattended)) {
        Write-Line 'Открываем установщик в обычном режиме'
        try {
            Start-Process -FilePath $installer -ArgumentList @("/DIR=$targetRoot") | Out-Null
        } catch {
            Write-Line "Не удалось открыть установщик: $($_.Exception.Message)"
        }
    }
    Show-Message (
        "Обновление Zapret не завершилось: $failureReason.`r`n`r`n" +
        "Программа сейчас не установлена. Запущен установщик — завершите установку вручную.`r`n`r`n" +
        "Установщик: $installer`r`nЖурнал: $logPath"
    )
} else {
    Start-InstalledApplication $targetRoot
    Show-Message (
        "Обновление Zapret не завершилось: $failureReason.`r`n`r`n" +
        "Продолжает работать установленная версия $installedVersion.`r`n`r`n" +
        "Установщик сохранён: $installer`r`nЖурнал: $logPath"
    )
}

exit 1
""".lstrip()


def render_watchdog_script() -> str:
    """Подставляет в скрипт правила, которые обязаны совпадать с Python-частью.

    Путь страховки и число попыток описаны один раз: расхождение между
    наблюдателем и приложением означало бы, что снимает страховку не тот, кто
    её ставил.
    """
    return (
        WATCHDOG_SCRIPT_TEMPLATE.replace("@RUNONCE_KEY@", RUNONCE_KEY)
        .replace("@RUNONCE_VALUE_NAME@", RUNONCE_VALUE_NAME)
        .replace("@INSTALL_ATTEMPTS@", str(INSTALL_ATTEMPTS))
        .replace("@GUI_EXIT_TIMEOUT_SECONDS@", str(GUI_EXIT_TIMEOUT_SECONDS))
        .replace("@MESSAGE_TIMEOUT_SECONDS@", str(MESSAGE_TIMEOUT_SECONDS))
    )


def install_watchdog_script(path: str | Path | None = None) -> Path:
    """Раскладывает скрипт наблюдателя рядом с состоянием обновления.

    Записывается с BOM: Windows PowerShell 5.1 читает файл без BOM в
    системной кодировке и портит русский текст сообщений.
    """
    script_path = Path(path) if path is not None else update_paths.watchdog_script_path()
    script_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = script_path.with_suffix(".ps1.new")
    temporary_path.write_text(render_watchdog_script(), encoding="utf-8-sig")
    os.replace(temporary_path, script_path)
    return script_path


def build_watchdog_command(
    *,
    script_path: str | Path,
    state_path: str | Path,
    recovery: bool = False,
) -> tuple[str, ...]:
    command = (
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-StatePath",
        str(state_path),
    )
    if recovery:
        return command + ("-Recovery",)
    return command


def _rotate_watchdog_log(log_path: Path) -> None:
    """Отодвигает прошлый журнал: появление нового = наблюдатель ожил."""
    try:
        if log_path.exists():
            os.replace(log_path, log_path.with_name(PREVIOUS_WATCHDOG_LOG_NAME))
    except OSError as exc:
        log(f"Не удалось отодвинуть журнал наблюдателя: {exc}", "WARNING")


def _spawn_detached(command: Sequence[str]) -> bool:
    """Запуск от уже полученных прав администратора, без запроса UAC."""
    try:
        subprocess.Popen(
            list(command),
            creationflags=(
                _DETACHED_PROCESS
                | _CREATE_NEW_PROCESS_GROUP
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            ),
            close_fds=True,
        )
        return True
    except Exception as exc:
        log(f"❌ Не удалось запустить наблюдателя обновления: {exc}", "🔁❌ ERROR")
        return False


def _spawn_elevated(command: Sequence[str]) -> bool:
    """Запасной путь: приложение почему-то работает без прав администратора."""
    import ctypes

    arguments = subprocess.list2cmdline(list(command[1:]))
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            command[0],
            arguments,
            None,
            0,
        )
    except Exception as exc:
        log(f"❌ Не удалось запросить права для наблюдателя: {exc}", "🔁❌ ERROR")
        return False

    if int(result) <= _SHELL_EXECUTE_MIN_SUCCESS:
        log(f"❌ Наблюдатель не запущен, код ShellExecute: {result}", "🔁❌ ERROR")
        return False
    return True


def _is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _wait_for_watchdog_start(
    log_path: Path,
    *,
    timeout_seconds: float,
    poll_seconds: float,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> bool:
    deadline = monotonic() + float(timeout_seconds)
    while monotonic() < deadline:
        if log_path.exists():
            return True
        sleep(poll_seconds)
    return log_path.exists()


def launch_update_watchdog(
    record: UpdateHandoffRecord,
    *,
    gui_pid: int | None = None,
    state_path: str | Path | None = None,
    script_path: str | Path | None = None,
    log_path: str | Path | None = None,
    is_admin: Callable[[], bool] = _is_admin,
    spawn_detached: Callable[[Sequence[str]], bool] = _spawn_detached,
    spawn_elevated: Callable[[Sequence[str]], bool] = _spawn_elevated,
    timeout_seconds: float = WATCHDOG_START_TIMEOUT_SECONDS,
    poll_seconds: float = WATCHDOG_START_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> bool:
    """Готовит состояние обновления и поднимает наблюдателя.

    Возвращает True, только если наблюдатель подтвердил запуск своим журналом:
    иначе закрывать приложение нельзя — установку никто не доведёт.
    """
    resolved_state_path = (
        Path(state_path) if state_path is not None else update_paths.handoff_state_path()
    )
    resolved_log_path = (
        Path(log_path) if log_path is not None else update_paths.watchdog_log_path()
    )

    prepared = UpdateHandoffRecord(
        state=HandoffState.PREPARED,
        version=record.version,
        target_root=record.target_root,
        installer_path=record.installer_path,
        arguments=tuple(record.arguments),
        gui_pid=int(gui_pid if gui_pid is not None else os.getpid()),
    )
    if not write_record(prepared, resolved_state_path):
        return False

    try:
        resolved_script_path = install_watchdog_script(script_path)
    except OSError as exc:
        log(f"❌ Не удалось разложить наблюдателя обновления: {exc}", "🔁❌ ERROR")
        return False

    _rotate_watchdog_log(resolved_log_path)
    command = build_watchdog_command(
        script_path=resolved_script_path,
        state_path=resolved_state_path,
    )

    spawned = spawn_detached(command) if is_admin() else spawn_elevated(command)
    if not spawned:
        return False

    if not _wait_for_watchdog_start(
        resolved_log_path,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        sleep=sleep,
        monotonic=monotonic,
    ):
        log("❌ Наблюдатель обновления не подал признаков жизни", "🔁❌ ERROR")
        return False

    log(f"✅ Наблюдатель обновления следит за установкой {record.version}", WATCHDOG_LOG_LEVEL)
    return True


__all__ = [
    "GUI_EXIT_TIMEOUT_SECONDS",
    "INSTALL_ATTEMPTS",
    "WATCHDOG_SCRIPT_TEMPLATE",
    "WATCHDOG_START_TIMEOUT_SECONDS",
    "build_watchdog_command",
    "install_watchdog_script",
    "launch_update_watchdog",
    "render_watchdog_script",
]
