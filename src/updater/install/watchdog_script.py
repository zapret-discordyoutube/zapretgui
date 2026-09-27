from __future__ import annotations

"""Текст PowerShell-наблюдателя обновления.

Скрипт хранится строкой, а не отдельным файлом: так он гарантированно едет
внутри собранного exe и не зависит от правил упаковки ресурсов. Правила,
общие с Python-частью (путь страховки, число попыток), подставляются при
раскладке скрипта на диск.

Три правила, на которых здесь уже обжигались:

* ``Start-Process -Wait`` в Windows PowerShell 5.1 ждёт не только сам
  процесс, но и всех его потомков. Установщик в конце автообновления сам
  запускает новую версию программы, поэтому такое ожидание длилось бы до её
  закрытия. Ждём только установщик: ``WaitForExit()``.
* ``-ArgumentList`` с массивом склеивает элементы пробелом без кавычек, и
  ``/DIR=D:\\Program Files\\Zapret`` распадался на два аргумента. Строка
  аргументов собирается здесь явно, с кавычками вокруг значений с пробелами.
* ``Set-Content -Encoding UTF8`` пишет BOM. Запись состояния пишется через
  .NET без BOM, чтобы её одинаково читали все участники.
"""

from .recovery_hook import RUNONCE_KEY, RUNONCE_VALUE_NAME


# Одна повторная попытка: она лечит разовую помеху вроде занятого файла, а
# бесконечный цикл переустановок при системной причине только вредит.
INSTALL_ATTEMPTS = 2
GUI_EXIT_TIMEOUT_SECONDS = 60
# Сообщение о неудаче не должно держать наблюдателя вечно: у пользователя
# может не быть возможности нажать «ОК» прямо сейчас.
MESSAGE_TIMEOUT_SECONDS = 600

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
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Write-Line([string]$message) {
    $stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    try {
        [System.IO.File]::AppendAllText($logPath, "$stamp  $message`r`n", $utf8NoBom)
    } catch { }
}

function Read-State {
    if (-not (Test-Path -LiteralPath $StatePath)) { return $null }
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
        $temporary = "$StatePath.$PID.tmp"
        [System.IO.File]::WriteAllText($temporary, ($state | ConvertTo-Json -Depth 5), $utf8NoBom)
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

# Установлена ли ожидаемая версия или более новая. Более новая означает, что
# пользователь уже обновился другим путём и старый установщик ставить нельзя.
function Test-VersionInstalled([string]$root, [string]$expected) {
    $installed = (Get-InstalledVersion $root).Trim()
    if ([string]::IsNullOrWhiteSpace($installed)) { return $false }
    if ([string]::IsNullOrWhiteSpace($expected)) { return $true }
    try {
        return ([version]$installed -ge [version]$expected.Trim())
    } catch {
        return ($installed -eq $expected.Trim())
    }
}

function Test-InstallerHash([string]$path, [string]$expectedSha) {
    if ([string]::IsNullOrWhiteSpace($expectedSha)) { return $true }
    try {
        $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    } catch {
        return $false
    }
    return ($actual.ToLowerInvariant() -eq $expectedSha.Trim().ToLowerInvariant())
}

function Join-InstallerArguments($items) {
    $parts = @()
    foreach ($item in $items) {
        $text = [string]$item
        if ($text -match '\s') { $parts += ('"' + $text + '"') } else { $parts += $text }
    }
    return ($parts -join ' ')
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
if ($null -eq $state) {
    Write-Line 'Состояния обновления нет: обновление отменено'
    exit 0
}

$targetRoot = [string]$state.target_root
$installer = [string]$state.installer_path
$installerSha = [string]$state.installer_sha256
$expectedVersion = [string]$state.version
$preparedPid = [int]$state.gui_pid
$argumentLine = ''
if ($state.arguments) {
    $argumentLine = Join-InstallerArguments @($state.arguments)
}

if ($Recovery) {
    Write-Line 'Режим восстановления после перезагрузки'
    # Доводим только установку, которую уже начали и не закончили. Итог
    # succeeded/failed уже известен, а prepared значит, что установщик так и
    # не запускался: ставить его молча при входе в систему нельзя.
    if ([string]$state.state -ne 'launched') {
        Write-Line "Восстановление не требуется: состояние $($state.state)"
        Remove-RecoveryHook
        exit 0
    }
    if (Test-VersionInstalled $targetRoot $expectedVersion) {
        Write-Line 'Восстановление не требуется: нужная версия на месте'
        Save-State $state 'succeeded' $state.installer_exit_code (Get-InstalledVersion $targetRoot) ''
        Remove-RecoveryHook
        exit 0
    }
} else {
    Wait-ForProcessExit $preparedPid $guiExitTimeoutSeconds
    # Приложение могло отменить обновление, пока наблюдатель запускался:
    # тогда оно удаляет запись или заменяет её новой.
    try {
        $current = Read-State
    } catch {
        $current = $null
    }
    if (($null -eq $current) -or ([string]$current.state -ne 'prepared') -or
        ([int]$current.gui_pid -ne $preparedPid) -or ([string]$current.version -ne $expectedVersion)) {
        Write-Line 'Обновление отменено приложением, установщик не запускается'
        exit 0
    }
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
    if (-not (Test-InstallerHash $installer $installerSha)) {
        $failureReason = 'файл установщика изменён после проверки, запуск отменён'
        Write-Line $failureReason
        break
    }

    Write-Line "Попытка $attempt из $maxAttempts : $installer $argumentLine"
    try {
        if ($argumentLine) {
            $process = Start-Process -FilePath $installer -ArgumentList $argumentLine -PassThru
        } else {
            $process = Start-Process -FilePath $installer -PassThru
        }
        # Без обращения к Handle PowerShell 5.1 теряет код возврата.
        $null = $process.Handle
        $process.WaitForExit()
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
Remove-RecoveryHook
Write-Line "Обновление не подтверждено: $failureReason"

if ([string]::IsNullOrWhiteSpace($installedVersion)) {
    # Приложения на диске нет. Открываем установщик обычным мастером: так
    # пользователь видит ход установки и может довести её до конца сам.
    if ((Test-Path -LiteralPath $installer) -and (-not $Unattended) -and
        (Test-InstallerHash $installer $installerSha)) {
        Write-Line 'Открываем установщик в обычном режиме'
        try {
            Start-Process -FilePath $installer -ArgumentList (Join-InstallerArguments @("/DIR=$targetRoot")) | Out-Null
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
    """Подставляет в скрипт правила, которые обязаны совпадать с Python-частью."""
    return (
        WATCHDOG_SCRIPT_TEMPLATE.replace("@RUNONCE_KEY@", RUNONCE_KEY)
        .replace("@RUNONCE_VALUE_NAME@", RUNONCE_VALUE_NAME)
        .replace("@INSTALL_ATTEMPTS@", str(INSTALL_ATTEMPTS))
        .replace("@GUI_EXIT_TIMEOUT_SECONDS@", str(GUI_EXIT_TIMEOUT_SECONDS))
        .replace("@MESSAGE_TIMEOUT_SECONDS@", str(MESSAGE_TIMEOUT_SECONDS))
    )


__all__ = [
    "GUI_EXIT_TIMEOUT_SECONDS",
    "INSTALL_ATTEMPTS",
    "MESSAGE_TIMEOUT_SECONDS",
    "WATCHDOG_SCRIPT_TEMPLATE",
    "render_watchdog_script",
]
