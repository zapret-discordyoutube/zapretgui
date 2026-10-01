# Сквозная проверка через окно установленной программы.
#
# Скрипт нажимает в окне программы кнопку состояния Zapret в заголовке
# (она запускает и останавливает обход) так же, как это делает человек,
# и после каждого шага сверяет с системой:
#   - после запуска жив winws из папки программы и загружен драйвер;
#   - после остановки своих winws нет и служба драйвера исчезла.
#
# Запускать в сеансе рабочего стола (не через SSH: оттуда окна не видны),
# от администратора, при открытом окне программы:
#
#   powershell -STA -ExecutionPolicy Bypass -File run_gui_e2e.ps1 [-Cycles 3] [-Out result.txt]
#
# Код возврата 0 — все циклы прошли.

param(
    [int]$Cycles = 3,
    [string]$Out = "$env:TEMP\zapret_gui_e2e.txt",
    [string]$Service = "Monkey",
    [int]$TimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

$script:Lines = New-Object System.Collections.Generic.List[string]
function Say([string]$text) {
    $script:Lines.Add($text)
    $script:Lines | Set-Content -Path $Out -Encoding UTF8
}

function Get-ZapretWindow {
    $process = Get-Process Zapret -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
    if (-not $process) { throw "Окно программы не найдено: запустите Zapret и откройте окно" }
    $script:InstallRoot = Split-Path (Split-Path $process.Path -Parent) -Parent
    $condition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $process.Id)
    $window = [System.Windows.Automation.AutomationElement]::RootElement.FindFirst(
        [System.Windows.Automation.TreeScope]::Children, $condition)
    if (-not $window) { throw "Окно программы недоступно для автоматизации" }
    return $window
}

function Find-Elements($window, [string]$typeName, [string]$namePattern) {
    $all = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition)
    $found = @()
    foreach ($element in $all) {
        try {
            if ($element.Current.ControlType.ProgrammaticName -eq "ControlType.$typeName" -and
                $element.Current.Name -match $namePattern) { $found += $element }
        } catch { }
    }
    return $found
}

function Get-StatusText($window) {
    $element = Find-Elements $window "Text" "^Статус Zapret:" | Select-Object -First 1
    if ($element) { return $element.Current.Name }
    return ""
}

function Click-Button($window, [string]$namePattern) {
    $button = Find-Elements $window "Button" $namePattern | Where-Object { $_.Current.IsEnabled } | Select-Object -First 1
    if (-not $button) { return $false }
    $button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    return $true
}

# Кнопка состояния в заголовке окна переключает обход. Старые версии
# программы имели отдельные кнопки на странице — они оставлены запасными.
function Click-Start($window) {
    return (Click-Button $window "^Состояние Zapret:") -or (Click-Button $window "^Запустить Zapret")
}

function Click-Stop($window) {
    return (Click-Button $window "^Состояние Zapret:") -or (Click-Button $window "^Остановить")
}

function Wait-Until([scriptblock]$condition, [int]$seconds) {
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    while ($watch.Elapsed.TotalSeconds -lt $seconds) {
        if (& $condition) { return $watch.Elapsed.TotalSeconds }
        Start-Sleep -Milliseconds 100
    }
    return -1
}

function Get-OwnEngines {
    $exeDir = Join-Path $script:InstallRoot "exe"
    return @(Get-Process winws, winws2 -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and ($_.Path -like "$exeDir\*")
    })
}

function Get-DriverState {
    $output = (sc.exe query $Service) -join " "
    if ($LASTEXITCODE -eq 1060 -or $output -match "1060") { return "absent" }
    # Слово «STATE» в выводе sc.exe переводится, названия состояний — нет.
    if ($output -match ":\s*\d+\s+(RUNNING|STOPPED|STOP_PENDING|START_PENDING)") { return $Matches[1] }
    return "unknown"
}

$failures = 0
try {
    $window = Get-ZapretWindow
    Say "Окно: $($window.Current.Name); установка: $script:InstallRoot"

    # Окно с предложением обновиться перекрывает кнопки — откладываем.
    if (Click-Button $window "^Отложить установку обновления") { Say "Предложение обновиться отложено"; Start-Sleep -Milliseconds 500 }

    # Исходное состояние — обход остановлен.
    if ((Get-StatusText $window) -notmatch "остановлен") {
        Click-Stop $window | Out-Null
        if ((Wait-Until { (Get-StatusText $window) -match "остановлен" } $TimeoutSeconds) -lt 0) {
            throw "Не удалось привести обход в остановленное состояние: $(Get-StatusText $window)"
        }
    }
    Say "Исходное состояние: $(Get-StatusText $window); драйвер: $(Get-DriverState)"

    for ($cycle = 1; $cycle -le $Cycles; $cycle++) {
        $problems = @()

        if (-not (Click-Start $window)) { throw "Кнопка запуска Zapret не найдена" }
        $startSeconds = Wait-Until { (Get-OwnEngines).Count -gt 0 -and (Get-StatusText $window) -notmatch "остановлен|Запуск|запуск" } $TimeoutSeconds
        $statusRunning = Get-StatusText $window
        $engines = Get-OwnEngines
        $driverRunning = Get-DriverState
        if ($startSeconds -lt 0) { $problems += "запуск не подтверждён за $TimeoutSeconds с" }
        if ($engines.Count -ne 1) { $problems += "своих winws: $($engines.Count), ожидался 1" }
        if ($driverRunning -ne "RUNNING") { $problems += "драйвер после запуска: $driverRunning" }

        # Движок должен прожить дольше стартового окна.
        Start-Sleep -Milliseconds 1500
        if ((Get-OwnEngines).Count -ne 1) { $problems += "winws умер вскоре после запуска" }

        if (-not (Click-Stop $window)) { $problems += "кнопка остановки не найдена" }
        $stopSeconds = Wait-Until { (Get-OwnEngines).Count -eq 0 -and (Get-StatusText $window) -match "остановлен" } $TimeoutSeconds
        $driverGone = Wait-Until { (Get-DriverState) -eq "absent" } 5
        $statusStopped = Get-StatusText $window
        if ($stopSeconds -lt 0) { $problems += "остановка не подтверждена за $TimeoutSeconds с" }
        if ($driverGone -lt 0) { $problems += "служба драйвера осталась после остановки: $(Get-DriverState)" }

        $verdict = if ($problems.Count) { $failures++; "FAIL" } else { "PASS" }
        Say ("{0}  цикл {1}: запуск {2:N2} с [{3}], остановка {4:N2} с [{5}], драйвер после остановки: {6}" -f `
            $verdict, $cycle, $startSeconds, $statusRunning, $stopSeconds, $statusStopped, (Get-DriverState))
        foreach ($problem in $problems) { Say "      $problem" }
    }
} catch {
    $failures++
    Say "FAIL  $($_.Exception.Message)"
}

Say ""
Say ("{0}/{1} циклов прошло" -f ($Cycles - [Math]::Min($failures, $Cycles)), $Cycles)
Say "DONE failures=$failures"
exit ([int]($failures -gt 0))
