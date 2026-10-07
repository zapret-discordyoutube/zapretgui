from __future__ import annotations

"""Текст PowerShell-окна, которое держит обновление на экране до конца.

Окно обновления живёт внутри ``Zapret.exe``, а этот файл установщик как раз
заменяет. Поэтому между закрытием старой версии и открытием новой экран был
пустым. Окно-продолжение — отдельный процесс PowerShell (WinForms) из
каталога состояния обновления: оно встаёт на место окна обновления, пока
старая программа закрывается, и гаснет, когда новая сообщит, что открылась.

Правила, на которых держится скрипт:

* Никаких файлов из каталога установки: логотип, тексты и цвета лежат в
  ``restart_splash.json`` и PNG рядом с состоянием обновления.
* Пока старая программа на экране, окно стоит поверх всех: иначе защита
  Windows от кражи фокуса могла поставить его позади Zapret, и после её
  закрытия наверх вышло бы чужое окно. Как только старая программа
  закрылась, «поверх всех» снимается — сообщение установщика должно быть видно.
* Этап берётся из ``handoff.json`` наблюдателя (prepared → launched →
  succeeded); ``failed`` или пропавшая запись — окно сразу уходит, сообщение
  покажет наблюдатель.
* Гаснет по метке ``app_ready.json`` новее своего запуска, по видимому окну
  новой ``Zapret.exe`` или по страховочным срокам — висеть вечно не может.
* ``-SnapshotPath`` — рисует один кадр в PNG и выходит: так внешний вид
  проверяется без рабочего стола.
"""

READY_TIMEOUT_SECONDS = 90
TOTAL_TIMEOUT_SECONDS = 15 * 60

SPLASH_SCRIPT_TEMPLATE = r"""
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SpecPath,
    [Parameter(Mandatory = $true)][string]$StatePath,
    [string]$SnapshotPath = '',
    [int]$SnapshotAfterMs = 1500,
    [string]$SnapshotState = ''
)

$ErrorActionPreference = 'Stop'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$readyTimeoutSeconds = @READY_TIMEOUT_SECONDS@
$totalTimeoutSeconds = @TOTAL_TIMEOUT_SECONDS@

try {
    $spec = Get-Content -LiteralPath $SpecPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    exit 2
}
$logPath = [string]$spec.log_path

function Write-Line([string]$message) {
    if ([string]::IsNullOrWhiteSpace($logPath)) { return }
    $stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    try {
        [System.IO.File]::AppendAllText($logPath, "$stamp  $message`r`n", $utf8NoBom)
    } catch { }
}

Write-Line '--- окно-продолжение запущено ---'

try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
} catch {
    Write-Line "WinForms недоступен: $($_.Exception.Message)"
    exit 2
}

# Чёткий текст на экранах с масштабом и скруглённые углы Windows 11.
$native = $null
try {
    Add-Type -Namespace ZapretRestartSplash -Name Native -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
[DllImport("dwmapi.dll")] public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
'@
    $native = [ZapretRestartSplash.Native]
    [void]$native::SetProcessDPIAware()
} catch {
    Write-Line "Без чёткого масштаба: $($_.Exception.Message)"
}

function Get-Color([string]$value, [string]$fallback) {
    try {
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return [System.Drawing.ColorTranslator]::FromHtml($value)
        }
    } catch { }
    return [System.Drawing.ColorTranslator]::FromHtml($fallback)
}

# Внимание: [Math]::Min/Max с целым литералом (1, 0, 255) PowerShell вызывает
# в целочисленном варианте и молча округляет дробное: Min(1, 0.16) = 0.
# Именно так окно оставалось полностью прозрачным. Только 1.0, 0.0, 255.0.
function With-Alpha([System.Drawing.Color]$color, [double]$alpha) {
    $a = [int][Math]::Max(0.0, [Math]::Min(255.0, [Math]::Round(255.0 * $alpha)))
    return [System.Drawing.Color]::FromArgb($a, $color.R, $color.G, $color.B)
}

$C = @{
    Background = Get-Color ([string]$spec.colors.background) '#272727'
    Card       = Get-Color ([string]$spec.colors.card) '#323232'
    Border     = Get-Color ([string]$spec.colors.border) '#3a3a3a'
    Foreground = Get-Color ([string]$spec.colors.foreground) '#ffffff'
    Muted      = Get-Color ([string]$spec.colors.muted) '#9aa0a6'
    Accent     = Get-Color ([string]$spec.colors.accent) '#60cdff'
    Track      = Get-Color ([string]$spec.colors.track) '#3a3a3a'
    OnAccent   = Get-Color ([string]$spec.colors.on_accent) '#000000'
}

# Надписи занимают одну строку: длинная обрезается многоточием, а не лезет на соседей.
$oneLine = New-Object System.Drawing.StringFormat
$oneLine.FormatFlags = [System.Drawing.StringFormatFlags]::NoWrap
$oneLine.Trimming = [System.Drawing.StringTrimming]::EllipsisCharacter
$centered = New-Object System.Drawing.StringFormat
$centered.FormatFlags = [System.Drawing.StringFormatFlags]::NoWrap
$centered.Alignment = [System.Drawing.StringAlignment]::Center
$centered.LineAlignment = [System.Drawing.StringAlignment]::Center

$logo = $null
try {
    if (-not [string]::IsNullOrWhiteSpace([string]$spec.logo_path)) {
        # Копия в памяти: файл не остаётся занятым.
        $bytes = [System.IO.File]::ReadAllBytes([string]$spec.logo_path)
        $stream = New-Object System.IO.MemoryStream(, $bytes)
        $logo = [System.Drawing.Image]::FromStream($stream)
    }
} catch {
    Write-Line "Логотип не загружен: $($_.Exception.Message)"
}
# Белый силуэт логотипа (та же прозрачность, все точки белые): блик рисуется
# им, поэтому свет идёт строго по форме значка и не задевает фон вокруг.
$logoShine = $null
if ($null -ne $logo) {
    try {
        $logoShine = New-Object System.Drawing.Bitmap($logo.Width, $logo.Height, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $whiten = New-Object System.Drawing.Imaging.ColorMatrix
        $whiten.Matrix00 = 0; $whiten.Matrix11 = 0; $whiten.Matrix22 = 0
        $whiten.Matrix40 = 1; $whiten.Matrix41 = 1; $whiten.Matrix42 = 1
        $attributes = New-Object System.Drawing.Imaging.ImageAttributes
        $attributes.SetColorMatrix($whiten)
        $painter = [System.Drawing.Graphics]::FromImage($logoShine)
        $painter.DrawImage($logo, (New-Object System.Drawing.Rectangle(0, 0, $logo.Width, $logo.Height)), 0, 0, $logo.Width, $logo.Height, [System.Drawing.GraphicsUnit]::Pixel, $attributes)
        $painter.Dispose(); $attributes.Dispose()
    } catch {
        $logoShine = $null
        Write-Line "Блик логотипа недоступен: $($_.Exception.Message)"
    }
}

$stages = @($spec.texts.stages)
$jokes = @($spec.jokes | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })

# Изменяемое состояние живёт в таблице: обработчики событий WinForms видят её
# без путаницы областей видимости PowerShell.
$S = @{
    Clock        = [System.Diagnostics.Stopwatch]::StartNew()
    StartedAt    = (Get-Date)
    Stage        = 0
    StageSince   = 0.0
    # Когда этап стал готовым (-1 — ещё нет): от этого момента идёт анимация галочки.
    DoneAt       = @(-1.0, -1.0, -1.0)
    # Когда этап начался: по разнице видно, сколько он занял.
    StartAt      = @(0.0, -1.0, -1.0)
    # Файл, который установщик копирует сейчас (из его журнала).
    CurrentFile  = ''
    LastTick     = 0.0
    RevealState  = $null
    Base         = $null
    Frames       = 0
    Fill         = 0.0
    SucceededAt  = -1.0
    DoneJumpAt   = -1.0
    # Когда закончился последний этап: от этого момента идёт вспышка по полосе и кольцу.
    FlashAt      = -1.0
    # Промежуточное звено сглаживания: полоса идёт за ним, а оно — за целью.
    Lead         = 0.0
    # Вертушка: угол логотипа в градусах и сглаженная скорость полосы (доля в
    # секунду), от которой зависит, как быстро он крутится.
    Spin         = 0.0
    Speed        = 0.0
    Closing      = $false
    ClosingAt    = 0.0
    CloseFast    = $false
    LastPoll     = -1.0
    LastWindowCheck = -1.0
    Joke         = ''
    PrevJoke     = ''
    JokeAt       = -10.0
    JokeIndex    = 0
    Shown        = $false
    Snapshot     = (-not [string]::IsNullOrWhiteSpace($SnapshotPath))
    TickErrors   = 0
    OldAlive     = $true
    OldCheckedAt = -1.0
    # Ход установки — из журнала установщика (см. Read-SetupLog).
    Expected     = [int]$spec.expected_files
    LogIsNew     = $false
    LogOffset    = [long]0
    LogTail      = ''
    FilesDone    = 0
    InstallStarted   = $false
    InstallSucceeded = $false
    LogClosed    = $false
}

function Next-Joke {
    # Шутки уже перемешаны программой: идём по кругу, без повторов подряд.
    if ($jokes.Count -eq 0) { return }
    $S.PrevJoke = $S.Joke
    $S.Joke = [string]$jokes[$S.JokeIndex % $jokes.Count]
    $S.JokeIndex++
    $S.JokeAt = $S.Clock.Elapsed.TotalSeconds
}

function Start-Closing([bool]$fast, [string]$reason) {
    if ($S.Closing) { return }
    $S.Closing = $true
    $S.CloseFast = $fast
    $S.ClosingAt = $S.Clock.Elapsed.TotalSeconds
    Write-Line "Окно-продолжение закрывается: $reason"
}

function Set-Stage([int]$stage) {
    if ($stage -le $S.Stage) { return }
    $now = $S.Clock.Elapsed.TotalSeconds
    for ($i = $S.Stage; $i -lt $stage -and $i -lt $S.DoneAt.Count; $i++) { $S.DoneAt[$i] = $now }
    if ($now -gt 0.5) { $S.FlashAt = $now }
    for ($i = $S.Stage + 1; $i -le $stage -and $i -lt $S.StartAt.Count; $i++) { $S.StartAt[$i] = $now }
    $S.Stage = $stage
    $S.StageSince = $now
    if ($stage -ge 2 -and $S.SucceededAt -lt 0) { $S.SucceededAt = $S.StageSince }
    Next-Joke
}

function Read-HandoffState {
    if ($S.Snapshot -and $SnapshotState) { return $SnapshotState }
    if (-not (Test-Path -LiteralPath $StatePath)) { return '' }
    try {
        $raw = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8
        return [string](($raw | ConvertFrom-Json).state)
    } catch {
        # Наблюдатель как раз подменяет файл: прочитаем в следующий раз.
        return '?'
    }
}

function Test-AppReady {
    $readyPath = [string]$spec.ready_path
    if ($readyPath -and (Test-Path -LiteralPath $readyPath)) {
        try {
            if ((Get-Item -LiteralPath $readyPath).LastWriteTime -gt $S.StartedAt) { return $true }
        } catch { }
    }
    return $false
}

function Test-NewAppWindow {
    $name = [string]$spec.app_process_name
    if ([string]::IsNullOrWhiteSpace($name)) { return $false }
    try {
        foreach ($process in @(Get-Process -Name $name -ErrorAction SilentlyContinue)) {
            if ($process.Id -eq [int]$spec.old_pid) { continue }
            if ($process.MainWindowHandle -ne [IntPtr]::Zero -and $process.StartTime -gt $S.StartedAt) {
                return $true
            }
        }
    } catch { }
    return $false
}

function Bring-ToFront([string]$why) {
    $ok = $false
    try {
        $form.Activate()
        $form.BringToFront()
        if ($null -ne $native) { $ok = [bool]$native::SetForegroundWindow($form.Handle) }
    } catch { }
    Write-Line "На передний план ($why): $ok"
}

function Watch-OldApp {
    # Старая программа закрылась — «поверх всех» больше не нужно: установщик
    # может показать сообщение об ошибке, и оно должно быть видно.
    $now = $S.Clock.Elapsed.TotalSeconds
    if (-not $S.OldAlive -or ($now - $S.OldCheckedAt) -lt 0.25) { return }
    $S.OldCheckedAt = $now
    $alive = $false
    try { $alive = [bool](Get-Process -Id ([int]$spec.old_pid) -ErrorAction SilentlyContinue) } catch { }
    if ($alive -and $now -lt 60) { return }
    $S.OldAlive = $false
    $form.TopMost = $false
    Write-Line "Старая версия закрылась ($([int]($now * 1000)) мс), окно больше не поверх всех"
    Bring-ToFront 'старая версия закрылась'
}

function Read-SetupLog {
    # Установщик пишет журнал по ходу работы: каждая строка «-- File entry --»
    # — один скопированный файл. Читаем только новое, файл не блокируем.
    $path = [string]$spec.setup_log_path
    if ([string]::IsNullOrWhiteSpace($path) -or -not (Test-Path -LiteralPath $path)) { return }
    if (-not $S.LogIsNew) {
        # Журнал прошлой установки не в счёт: ждём свежий.
        try { $written = (Get-Item -LiteralPath $path).LastWriteTime } catch { return }
        if (-not $S.Snapshot -and $written -le $S.StartedAt) { return }
        $S.LogIsNew = $true
        $S.LogOffset = [long]0
    }
    $chunk = ''
    $fs = $null
    try {
        $fs = [System.IO.File]::Open($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]'ReadWrite, Delete')
        if ($fs.Length -lt $S.LogOffset) {
            # Файл начали заново — считаем заново.
            $S.LogOffset = [long]0; $S.FilesDone = 0; $S.LogTail = ''
        }
        if ($fs.Length -eq $S.LogOffset) { return }
        [void]$fs.Seek($S.LogOffset, [System.IO.SeekOrigin]::Begin)
        $reader = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8, $false, 65536, $true)
        $chunk = $reader.ReadToEnd()
        $S.LogOffset = $fs.Position
        $reader.Dispose()
    } catch {
        return
    } finally {
        if ($null -ne $fs) { $fs.Dispose() }
    }
    $text = $S.LogTail + $chunk
    $cut = $text.LastIndexOf("`n")
    if ($cut -lt 0) { $S.LogTail = $text; return }
    $S.LogTail = $text.Substring($cut + 1)
    $complete = $text.Substring(0, $cut)
    $S.FilesDone += ([regex]::Matches($complete, [regex]::Escape('-- File entry --'))).Count
    $named = [regex]::Matches($complete, 'Dest filename: ([^\r\n]+)')
    if ($named.Count -gt 0) {
        try { $S.CurrentFile = [System.IO.Path]::GetFileName($named[$named.Count - 1].Groups[1].Value.Trim()) } catch { }
    }
    if ($complete.Contains('Starting the installation process.')) {
        if (-not $S.InstallStarted) { Write-Line "Установщик начал копировать файлы (ожидается $($S.Expected))" }
        $S.InstallStarted = $true
    }
    if ($complete.Contains('Installation process succeeded.')) {
        $S.InstallStarted = $true
        if (-not $S.InstallSucceeded) { Write-Line "Установщик скопировал файлы: $($S.FilesDone)" }
        $S.InstallSucceeded = $true
    }
    if ($complete.Contains('Log closed.')) { $S.LogClosed = $true }
}

function Poll-State {
    $now = $S.Clock.Elapsed.TotalSeconds
    Read-SetupLog
    $state = Read-HandoffState
    switch ($state) {
        'prepared'  { Set-Stage 0 }
        'launched'  { Set-Stage 1 }
        'succeeded' { Set-Stage 2 }
        'failed'    { Start-Closing $true 'установка не удалась' }
        ''          {
            # Запись исчезла: обновление отменили — показывать нечего.
            if ($now -gt 3) { Start-Closing $true 'обновление отменено' }
        }
    }
    if ($S.Snapshot) { return }
    Watch-OldApp
    if (Test-AppReady) { Start-Closing $false 'новая версия открылась' ; return }
    if ($now - $S.LastWindowCheck -ge 1.0) {
        $S.LastWindowCheck = $now
        if ($S.Stage -ge 1 -and (Test-NewAppWindow)) { Start-Closing $false 'видно окно новой версии'; return }
    }
    if ($S.SucceededAt -ge 0 -and ($now - $S.SucceededAt) -gt $readyTimeoutSeconds) {
        Start-Closing $false 'новая версия не отозвалась вовремя'
    }
    if ($now -gt $totalTimeoutSeconds) { Start-Closing $false 'истёк общий срок' }
}

function New-RoundedPath([System.Drawing.RectangleF]$rect, [single]$radius) {
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $d = [single]([Math]::Min($radius * 2, [Math]::Min($rect.Width, $rect.Height)))
    if ($d -le 0.5) { $path.AddRectangle($rect); return $path }
    $path.AddArc($rect.X, $rect.Y, $d, $d, 180, 90)
    $path.AddArc($rect.Right - $d, $rect.Y, $d, $d, 270, 90)
    $path.AddArc($rect.Right - $d, $rect.Bottom - $d, $d, $d, 0, 90)
    $path.AddArc($rect.X, $rect.Bottom - $d, $d, $d, 90, 90)
    $path.CloseFigure()
    return $path
}

# Полоса идёт только вперёд: назад и туда-обратно она не ходит никогда.
#   закрываем старую версию        0–5 %
#   установщик готовится           5–10 % — хода не видно, полоса тихо подползает
#   копирование файлов             10–90 % — по числу скопированных файлов
#   установщик завершает           92–94 %
#   открываем новую версию         95–99 %, «почти конец»; 100 % — она открылась
function Target-Fill {
    $now = $S.Clock.Elapsed.TotalSeconds
    if ($S.Closing -and -not $S.CloseFast) { return 1.0 }
    if ($S.Stage -ge 2) { return 0.95 + 0.04 * (1 - [Math]::Exp(-($now - $S.StageSince) / 1.5)) }
    if ($S.InstallSucceeded) {
        if ($S.LogClosed) { return 0.94 }
        return 0.92
    }
    if ($S.InstallStarted) {
        if ($S.Expected -gt 0) {
            $share = [Math]::Min(1.0, $S.FilesDone / [double]$S.Expected)
        } else {
            # Сколько файлов всего — неизвестно: растём по счёту, но до конца не доходим.
            $share = 1 - [Math]::Exp(-$S.FilesDone / 300.0)
        }
        return 0.10 + 0.80 * $share
    }
    if ($S.Stage -ge 1) { return 0.05 + 0.05 * (1 - [Math]::Exp(-($now - $S.StageSince) / 4.0)) }
    return 0.05 * (1 - [Math]::Exp(-$now / 1.2))
}

# Плавный выход к цели: быстро в начале, мягко в конце.
function Ease-Out([double]$t) {
    $t = [Math]::Max(0.0, [Math]::Min(1.0, $t))
    return 1 - [Math]::Pow(1 - $t, 3)
}

# Плавный разгон и плавное торможение: так движется блик по полосе и кольцу.
function Ease-InOut([double]$t) {
    $t = [Math]::Max(0.0, [Math]::Min(1.0, $t))
    if ($t -lt 0.5) { return 4 * $t * $t * $t }
    return 1 - [Math]::Pow(2 - 2 * $t, 3) / 2
}

# То же, но с небольшим перелётом за цель и возвратом — «пружина».
function Ease-Back([double]$t) {
    $t = [Math]::Max(0.0, [Math]::Min(1.0, $t))
    $u = $t - 1
    return 1 + 2.4 * $u * $u * $u + 1.4 * $u * $u
}

# Появление по очереди: часть окна выезжает снизу и проявляется. Проявление —
# заслонка цвета фона поверх уже нарисованного: так не нужно делать
# полупрозрачной каждую кисть. Возвращает, насколько часть уже проявилась.
function Begin-Reveal([System.Drawing.Graphics]$g, [double]$delay, [double]$k) {
    $shown = Ease-Out (($S.Clock.Elapsed.TotalSeconds - $delay) / 0.45)
    $S.RevealState = $g.Save()
    if ($shown -lt 1) { $g.TranslateTransform(0, [single](12 * $k * (1 - $shown))) }
    return $shown
}

function End-Reveal([System.Drawing.Graphics]$g, [double]$shown, [single]$x, [single]$y, [single]$w, [single]$h) {
    if ($shown -lt 1) {
        $veil = New-Object System.Drawing.SolidBrush((With-Alpha $S.Base (1 - $shown)))
        $g.FillRectangle($veil, $x, $y, $w, $h)
        $veil.Dispose()
    }
    $g.Restore($S.RevealState)
}

function Mix-Color([System.Drawing.Color]$from, [System.Drawing.Color]$to, [double]$t) {
    $t = [Math]::Max(0.0, [Math]::Min(1.0, $t))
    return [System.Drawing.Color]::FromArgb(
        255,
        [int]($from.R + ($to.R - $from.R) * $t),
        [int]($from.G + ($to.G - $from.G) * $t),
        [int]($from.B + ($to.B - $from.B) * $t)
    )
}

# Галочка рисуется росчерком: $q — какая её часть уже проведена.
function Draw-Check([System.Drawing.Graphics]$g, [double]$cx, [double]$cy, [double]$k, [double]$q) {
    if ($q -le 0) { return }
    $ax = $cx - 4.5 * $k; $ay = $cy + 0.3 * $k
    $bx = $cx - 1.3 * $k; $by = $cy + 3.5 * $k
    $ex = $cx + 4.8 * $k; $ey = $cy - 3.6 * $k
    $first = [Math]::Sqrt(($bx - $ax) * ($bx - $ax) + ($by - $ay) * ($by - $ay))
    $second = [Math]::Sqrt(($ex - $bx) * ($ex - $bx) + ($ey - $by) * ($ey - $by))
    $drawn = [Math]::Min(1.0, $q) * ($first + $second)
    $p = New-Object System.Drawing.Pen($C.OnAccent, [single](2.1 * $k))
    $p.StartCap = 'Round'; $p.EndCap = 'Round'; $p.LineJoin = 'Round'
    if ($drawn -le $first) {
        $t = $drawn / $first
        $g.DrawLine($p, [single]$ax, [single]$ay, [single]($ax + ($bx - $ax) * $t), [single]($ay + ($by - $ay) * $t))
    } else {
        $t = ($drawn - $first) / $second
        $g.DrawLines($p, [System.Drawing.PointF[]]@(
            (New-Object System.Drawing.PointF($ax, $ay)),
            (New-Object System.Drawing.PointF($bx, $by)),
            (New-Object System.Drawing.PointF(($bx + ($ex - $bx) * $t), ($by + ($ey - $by) * $t)))
        ))
    }
    $p.Dispose()
}

function Format-Duration([double]$seconds) {
    $whole = [int][Math]::Floor([Math]::Max(0.0, $seconds))
    return '{0}:{1:00}' -f [int][Math]::Floor($whole / 60), ($whole % 60)
}

function Draw-Frame([System.Drawing.Graphics]$g, [int]$width, [int]$height) {
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::ClearTypeGridFit
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    # Все размеры — в долях от окна: $M растёт вместе с ним (см. ниже, где
    # создаются шрифты), поэтому картинка занимает окно целиком при любом размере.
    $k = [single]($g.DpiX / 96.0 * $M)
    $now = $S.Clock.Elapsed.TotalSeconds
    $S.Frames += 1

    # Вид — как у страниц самой программы (Windows 11): слева карточка с
    # кольцом хода и логотипом, справа заголовок и этапы карточками, внизу полоса.
    $isDark = ($C.Background.GetBrightness() -lt 0.5)
    $white = [System.Drawing.Color]::White
    $black = [System.Drawing.Color]::Black
    # Цвета — главного окна программы: плоский фон страницы и карточки на нём,
    # без обводок и цветного налёта. Текущая карточка — на ступень светлее,
    # будущая — на полступени ближе к фону.
    $base = $C.Background
    $cardColor = $C.Card
    $cardDimColor = Mix-Color $base $cardColor 0.55
    if ($isDark) {
        $cardLitColor = Mix-Color $cardColor $white 0.065
        $ringTrack = Mix-Color $cardColor $white 0.1
    } else {
        $cardLitColor = $cardColor
        $ringTrack = Mix-Color $base $black 0.1
    }
    # В светлой теме белая карточка на почти белом фоне держится на тонкой кромке.
    $cardEdge = if ($isDark) { $cardColor } else { Mix-Color $base $black 0.075 }
    $S.Base = $base
    $g.Clear($base)
    $borderPen = New-Object System.Drawing.Pen((Mix-Color $base $C.Foreground 0.1), [single]1)
    $g.DrawRectangle($borderPen, 0, 0, $width - 1, $height - 1)
    $borderPen.Dispose()

    $fg = New-Object System.Drawing.SolidBrush($C.Foreground)
    $muted = New-Object System.Drawing.SolidBrush($C.Muted)
    $pad = [single](48 * $k)
    $titleHeight = $F.Title.GetHeight($g)
    $subHeight = $F.Sub.GetHeight($g)
    $smallHeight = $F.Small.GetHeight($g)
    $bigHeight = $F.Big.GetHeight($g)
    $share = [Math]::Max(0.0, [Math]::Min(1.0, $S.Fill))
    $copying = ($S.Stage -eq 1 -and $S.InstallStarted -and -not $S.InstallSucceeded)

    $captionTop = [single]($height - 40 * $k - $subHeight)
    $barHeight = [single](4 * $k)
    $barTop = [single]($captionTop - 16 * $k - $barHeight)

    # Левая карточка с кольцом и правая колонка (заголовок + этапы) одной
    # высоты и стоят вровень; вместе они — по центру места над полосой.
    $cardHeight = [single](100 * $k)
    $cardGap = [single](8 * $k)
    $headHeight = $titleHeight + 6 * $k + $subHeight + 26 * $k
    $blockHeight = $headHeight + $stages.Count * $cardHeight + ($stages.Count - 1) * $cardGap
    $blockTop = [single]([Math]::Max(24.0 * $k, ($barTop - 14 * $k - $blockHeight) / 2))
    $sideWidth = [single](300 * $k)

    # --- Слева: карточка с кольцом хода, крупным логотипом, процентами и счётом файлов. ---
    $shown = Begin-Reveal $g 0.0 $k
    $sidePath = New-RoundedPath (New-Object System.Drawing.RectangleF($pad, $blockTop, $sideWidth, $blockHeight)) ([single](8 * $k))
    $sideBrush = New-Object System.Drawing.SolidBrush($cardColor)
    $g.FillPath($sideBrush, $sidePath)
    $sideBrush.Dispose()
    $edgePen = New-Object System.Drawing.Pen($cardEdge, [single]1)
    $g.DrawPath($edgePen, $sidePath)
    $edgePen.Dispose(); $sidePath.Dispose()
    $underRing = $bigHeight + $subHeight + 22 * $k
    $ringSize = [single]([Math]::Min($sideWidth - 76 * $k, $blockHeight - $underRing - 64 * $k))
    $ringRadius = $ringSize / 2
    $ringX = [single]($pad + $sideWidth / 2)
    $ringY = [single]($blockTop + ($blockHeight - $underRing) / 2)
    $ringStroke = [single](10 * $k)
    # Этап закончился — кольцо на мгновение становится толще.
    $flash = if ($S.FlashAt -ge 0) { ($now - $S.FlashAt) / 0.7 } else { 2.0 }
    $arcStroke = $ringStroke
    if ($flash -lt 1) { $arcStroke = [single]($ringStroke * (1 + 0.22 * [Math]::Sin([Math]::PI * $flash))) }
    # Блик идёт 1,7 с с разгоном и торможением, затем 0,7 с пауза; на концах
    # пути он проявляется и гаснет, а не возникает из ниоткуда.
    $glint = [Math]::Min(1.0, ($now % 2.4) / 1.7)
    $glintAt = Ease-InOut $glint
    $glintGlow = [Math]::Sin([Math]::PI * $glint)
    # «Разведчик» — огонёк, который бежит по ещё не пройденной части кольца и
    # полосы в противофазе с бликом: работа видна, даже когда проценты стоят.
    $scout = [Math]::Min(1.0, (($now + 1.2) % 2.4) / 1.7)
    $scoutAt = Ease-InOut $scout
    $scoutGlow = [Math]::Sin([Math]::PI * $scout)
    $bead = Mix-Color $C.Accent ([System.Drawing.Color]::White) 0.8
    $ringRect = New-Object System.Drawing.RectangleF(($ringX - $ringRadius), ($ringY - $ringRadius), $ringSize, $ringSize)
    $trackPen = New-Object System.Drawing.Pen($ringTrack, $ringStroke)
    $g.DrawEllipse($trackPen, $ringRect)
    $trackPen.Dispose()
    $sweep = [single](360.0 * $share)
    if ($sweep -gt 0.5) {
        $arcPen = New-Object System.Drawing.Pen($C.Accent, $arcStroke)
        $arcPen.StartCap = 'Round'; $arcPen.EndCap = 'Round'
        $g.DrawArc($arcPen, $ringRect, [single]-90, $sweep)
        $arcPen.Dispose()
        if ($S.Fill -lt 0.999) {
            # Блик на дуге — три слоя разной длины: яркая середина, мягкие края.
            $at = $sweep * $glintAt
            foreach ($layer in @(@(18.0, 26), @(11.0, 34), @(5.0, 44))) {
                $from = [Math]::Max(0.0, $at - $layer[0])
                $to = [Math]::Min([double]$sweep, $at + $layer[0])
                if ($to - $from -gt 1) {
                    $shinePen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb([int]($layer[1] * $glintGlow), 255, 255, 255), [single]($ringStroke * 0.55))
                    $shinePen.StartCap = 'Round'; $shinePen.EndCap = 'Round'
                    $g.DrawArc($shinePen, $ringRect, [single](-90 + $from), [single]($to - $from))
                    $shinePen.Dispose()
                }
            }
            # Светлая «бусина» на переднем крае дуги.
            if ($sweep -gt 4) {
                $angle = (-90.0 + $sweep) * [Math]::PI / 180.0
                $beadRadius = [single]($ringStroke * 0.24)
                $beadBrush = New-Object System.Drawing.SolidBrush((With-Alpha $bead (0.72 + 0.2 * [Math]::Sin($now * 3.0))))
                $g.FillEllipse($beadBrush, [single]($ringX + $ringRadius * [Math]::Cos($angle) - $beadRadius), [single]($ringY + $ringRadius * [Math]::Sin($angle) - $beadRadius), (2 * $beadRadius), (2 * $beadRadius))
                $beadBrush.Dispose()
            }
        }
    }
    if ($S.Fill -lt 0.999 -and (360.0 - $sweep) -gt 30) {
        $scoutPen = New-Object System.Drawing.Pen((With-Alpha $C.Accent (0.42 * $scoutGlow)), [single]($ringStroke * 0.45))
        $scoutPen.StartCap = 'Round'; $scoutPen.EndCap = 'Round'
        $g.DrawArc($scoutPen, $ringRect, [single](-90 + $sweep + 10 + (340.0 - $sweep - 10) * $scoutAt), [single]9)
        $scoutPen.Dispose()
    }
    if ($null -ne $logo) {
        # Логотип появляется с увеличением и крутится как вертушка: угол копит
        # таймер (см. $S.Spin), скорость зависит от хода установки.
        # Этап закончился — короткий «вдох», установщик закончил — «вдох» сильнее.
        $scale = (0.84 + 0.16 * (Ease-Back ($now / 0.6))) * (1 + 0.014 * [Math]::Sin($now * 1.8))
        if ($flash -lt 1) { $scale += 0.06 * [Math]::Sin([Math]::PI * $flash) }
        if ($S.DoneJumpAt -ge 0) {
            $t = ($now - $S.DoneJumpAt) / 0.6
            if ($t -lt 1) { $scale += 0.1 * [Math]::Sin([Math]::PI * $t) }
        }
        $size = [single]($ringSize * 0.52 * $scale)
        $logoRect = New-Object System.Drawing.RectangleF((-$size / 2), (-$size / 2), $size, $size)
        $state = $g.Save()
        $g.TranslateTransform($ringX, $ringY)
        $g.RotateTransform([single]$S.Spin)
        $g.DrawImage($logo, $logoRect)
        $g.Restore($state)
        # Раз в несколько секунд по значку наискось проходит световой блик.
        # Полоса света стоит на месте относительно окна, а силуэт под ней
        # крутится вместе со значком.
        $pass = ($now % 4.4) / 0.95
        if ($null -ne $logoShine -and $pass -lt 1 -and $now -gt 1.0) {
            $logoLeft = [single]($ringX - $size / 2)
            $logoTop = [single]($ringY - $size / 2)
            $middle = $logoLeft - $size * 0.35 + $size * 1.7 * (Ease-InOut $pass)
            $slant = $size * 0.16
            # Три вложенные полосы разной ширины: яркая середина, мягкие края.
            foreach ($layer in @(@(0.2, 0.1), @(0.12, 0.13), @(0.05, 0.16))) {
                $half = $size * $layer[0]
                $band = New-Object System.Drawing.Drawing2D.GraphicsPath
                $band.AddPolygon([System.Drawing.PointF[]]@(
                    (New-Object System.Drawing.PointF(($middle - $half + $slant), $logoTop)),
                    (New-Object System.Drawing.PointF(($middle + $half + $slant), $logoTop)),
                    (New-Object System.Drawing.PointF(($middle + $half - $slant), ($logoTop + $size))),
                    (New-Object System.Drawing.PointF(($middle - $half - $slant), ($logoTop + $size)))
                ))
                $fade = New-Object System.Drawing.Imaging.ColorMatrix
                $fade.Matrix33 = [single]($layer[1] * [Math]::Sin([Math]::PI * $pass))
                $attributes = New-Object System.Drawing.Imaging.ImageAttributes
                $attributes.SetColorMatrix($fade)
                $state = $g.Save()
                $g.SetClip($band, [System.Drawing.Drawing2D.CombineMode]::Intersect)
                $g.TranslateTransform($ringX, $ringY)
                $g.RotateTransform([single]$S.Spin)
                # Силуэт полупрозрачный и размытый по смыслу: дорогое сглаживание ему не нужно.
                $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::Bilinear
                $g.DrawImage($logoShine, [System.Drawing.PointF[]]@(
                    (New-Object System.Drawing.PointF((-$size / 2), (-$size / 2))),
                    (New-Object System.Drawing.PointF(($size / 2), (-$size / 2))),
                    (New-Object System.Drawing.PointF((-$size / 2), ($size / 2)))
                ), (New-Object System.Drawing.RectangleF(0, 0, $logoShine.Width, $logoShine.Height)), [System.Drawing.GraphicsUnit]::Pixel, $attributes)
                $g.Restore($state)
                $attributes.Dispose(); $band.Dispose()
            }
        }
    }
    $leftColumn = New-Object System.Drawing.RectangleF($pad, ($ringY + $ringRadius + 14 * $k), $sideWidth, ($bigHeight + 2 * $k))
    $g.DrawString(('{0} %' -f [int][Math]::Round(100.0 * $share)), $F.Big, $fg, $leftColumn, $centered)
    if ($copying -and $S.Expected -gt 0) {
        # Настоящий счёт копирования: «312 из 682 файлов».
        $done = [Math]::Min($S.FilesDone, $S.Expected)
        $counter = ([string]$spec.texts.files_template).Replace('{done}', [string]$done).Replace('{total}', [string]$S.Expected)
        $counterRect = New-Object System.Drawing.RectangleF($leftColumn.X, ($leftColumn.Bottom + 2 * $k), $leftColumn.Width, ($subHeight + 2 * $k))
        $g.DrawString($counter, $F.Sub, $muted, $counterRect, $centered)
    }
    End-Reveal $g $shown ($pad - 4 * $k) ($blockTop - 4 * $k) ($sideWidth + 8 * $k) ($blockHeight + 8 * $k)

    # --- Справа: заголовок и этапы карточками. ---
    $columnLeft = [single]($pad + $sideWidth + 28 * $k)
    $columnWidth = [single]($width - $pad - $columnLeft)
    $y = [single]($blockTop + 2 * $k)

    $shown = Begin-Reveal $g 0.06 $k
    $g.DrawString([string]$spec.texts.title, $F.Title, $fg, (New-Object System.Drawing.RectangleF($columnLeft, $y, $columnWidth, ($titleHeight + 2 * $k))), $oneLine)
    $g.DrawString([string]$spec.texts.subtitle, $F.Sub, $muted, (New-Object System.Drawing.RectangleF($columnLeft, ($y + $titleHeight + 6 * $k), $columnWidth, ($subHeight + 2 * $k))), $oneLine)
    End-Reveal $g $shown ($columnLeft - 6 * $k) ($y - 4 * $k) ($columnWidth + 12 * $k) ($titleHeight + $subHeight + 18 * $k)
    $y = [single]($blockTop + $headHeight)

    $r = [single](13 * $k)
    $statuses = @($spec.texts.statuses)
    for ($i = 0; $i -lt $stages.Count; $i++) {
        $shown = Begin-Reveal $g (0.14 + 0.07 * $i) $k
        $cy = $y + $cardHeight / 2
        $cx = $columnLeft + 38 * $k
        $doneAt = [double]$S.DoneAt[$i]
        $isDone = ($doneAt -ge 0)
        $doneFor = $now - $doneAt
        $isActive = (-not $isDone -and $i -eq $S.Stage)
        # Текущая карточка светлее, с полоской акцента слева — как выбранная
        # строка в Windows 11. Переход плавный: загорается и гаснет.
        $lit = 0.0
        if ($isActive) { $lit = Ease-Out (($now - $S.StageSince) / 0.35) }
        elseif ($isDone) { $lit = 1 - (Ease-Out ($doneFor / 0.45)) }
        $cardRect = New-Object System.Drawing.RectangleF($columnLeft, $y, $columnWidth, $cardHeight)
        $cardPath = New-RoundedPath $cardRect ([single](8 * $k))
        $restColor = if ($isDone -or $isActive) { $cardColor } else { $cardDimColor }
        $cardBrush = New-Object System.Drawing.SolidBrush((Mix-Color $restColor $cardLitColor $lit))
        $g.FillPath($cardBrush, $cardPath)
        $cardBrush.Dispose()
        $edgePen = New-Object System.Drawing.Pen($cardEdge, [single]1)
        $g.DrawPath($edgePen, $cardPath)
        $edgePen.Dispose(); $cardPath.Dispose()
        if ($lit -gt 0.01) {
            $pillHeight = [single](($cardHeight - 56 * $k) * $lit)
            $pillPath = New-RoundedPath (New-Object System.Drawing.RectangleF($columnLeft, ($cy - $pillHeight / 2), ([single](3 * $k)), $pillHeight)) ([single](1.5 * $k))
            $pillBrush = New-Object System.Drawing.SolidBrush((With-Alpha $C.Accent $lit))
            $g.FillPath($pillBrush, $pillPath)
            $pillBrush.Dispose(); $pillPath.Dispose()
        }

        $markRect = New-Object System.Drawing.RectangleF(($cx - $r), ($cy - $r), (2 * $r), (2 * $r))
        $status = ''
        $spent = ''
        if ($isDone) {
            # Кружок заливается от центра с «пружиной», затем росчерком появляется галочка.
            $grow = [single]($r * (0.35 + 0.65 * (Ease-Back ($doneFor / 0.38))))
            $b = New-Object System.Drawing.SolidBrush($C.Accent)
            $g.FillEllipse($b, $cx - $grow, $cy - $grow, 2 * $grow, 2 * $grow)
            $b.Dispose()
            if ($doneFor -lt 0.6) {
                # От галочки расходится тонкое кольцо: этап только что закончился.
                $wave = Ease-Out ($doneFor / 0.6)
                $waveRadius = [single]($r + 8 * $k * $wave)
                $wavePen = New-Object System.Drawing.Pen((With-Alpha $C.Accent (0.4 * (1 - $wave))), [single](1.5 * $k))
                $g.DrawEllipse($wavePen, $cx - $waveRadius, $cy - $waveRadius, 2 * $waveRadius, 2 * $waveRadius)
                $wavePen.Dispose()
            }
            Draw-Check $g $cx $cy ($k * 1.2) (Ease-Out (($doneFor - 0.12) / 0.3))
            $titleFont = $F.Stage; $titleColor = $C.Foreground
            if ($statuses.Count -gt 0) { $status = [string]$statuses[0] }
            if ($S.StartAt[$i] -ge 0) { $spent = Format-Duration ($doneAt - $S.StartAt[$i]) }
        } elseif ($isActive) {
            # Кольцо ожидания Windows: бледная дорожка и дуга, которая вращается, вытягиваясь и сжимаясь.
            # Номер этапа растворяется, кольцо проявляется на его месте.
            if ($lit -lt 0.98) {
                $numberBrush = New-Object System.Drawing.SolidBrush((With-Alpha $C.Muted (1 - $lit)))
                $g.DrawString([string]($i + 1), $F.Small, $numberBrush, $markRect, $centered)
                $numberBrush.Dispose()
            }
            $ring = New-Object System.Drawing.Pen((With-Alpha $C.Accent (0.22 * $lit)), [single](3 * $k))
            $g.DrawEllipse($ring, $markRect)
            $ring.Dispose()
            $arc = New-Object System.Drawing.Pen((With-Alpha $C.Accent $lit), [single](3 * $k))
            $arc.StartCap = 'Round'; $arc.EndCap = 'Round'
            $turn = $now / 1.3
            $beat = $turn - [Math]::Floor($turn)
            $head = Ease-InOut ($beat * 2)
            $tail = Ease-InOut ($beat * 2 - 1)
            $spin = ($now * 130 + [Math]::Floor($turn) * 270 + $tail * 270) % 360
            $g.DrawArc($arc, $markRect, [single]$spin, [single](18 + 270 * ($head - $tail)))
            $arc.Dispose()
            $titleFont = $F.StageB
            $titleColor = Mix-Color $C.Muted $C.Foreground $lit
            if ($statuses.Count -gt 1) { $status = [string]$statuses[1] }
            # Пока идёт копирование — имя файла, который ставится прямо сейчас.
            if ($i -eq 1 -and $copying -and $S.CurrentFile) { $status = $S.CurrentFile }
            if ($S.StartAt[$i] -ge 0) { $spent = Format-Duration ($now - $S.StartAt[$i]) }
        } else {
            $p = New-Object System.Drawing.Pen((With-Alpha $C.Muted 0.5), [single](1.6 * $k))
            $g.DrawEllipse($p, $markRect)
            $p.Dispose()
            $g.DrawString([string]($i + 1), $F.Small, $muted, $markRect, $centered)
            $titleFont = $F.Stage; $titleColor = $C.Muted
            if ($statuses.Count -gt 2) { $status = [string]$statuses[2] }
        }

        # Справа — сколько этап занял (у текущего время идёт).
        $spentWidth = 0.0
        if ($spent) {
            $spentWidth = $g.MeasureString($spent, $F.Sub).Width
            $g.DrawString($spent, $F.Sub, $muted, [single]($columnLeft + $columnWidth - 20 * $k - $spentWidth), [single]($cy - $subHeight / 2))
        }
        $textLeft = [single]($columnLeft + 70 * $k)
        $textWidth = [single]($columnLeft + $columnWidth - 28 * $k - $spentWidth - $textLeft)
        $fontHeight = $titleFont.GetHeight($g)
        $textTop = [single]($cy - ($fontHeight + $smallHeight + 1 * $k) / 2)
        $brush = New-Object System.Drawing.SolidBrush($titleColor)
        $g.DrawString([string]$stages[$i], $titleFont, $brush, (New-Object System.Drawing.RectangleF($textLeft, $textTop, $textWidth, ($fontHeight + 2 * $k))), $oneLine)
        $brush.Dispose()
        if ($status) {
            $statusBrush = New-Object System.Drawing.SolidBrush((With-Alpha $C.Muted 0.9))
            $g.DrawString($status, $F.Small, $statusBrush, (New-Object System.Drawing.RectangleF($textLeft, ($textTop + $fontHeight + 1 * $k), $textWidth, ($smallHeight + 2 * $k))), $oneLine)
            $statusBrush.Dispose()
        }
        if ($lit -gt 0.01) {
            # Полоска хода самого этапа: у копирования — настоящая, по файлам;
            # у остальных хода не видно, поэтому бежит отрезок, как в Windows 11.
            # Закончив, этап заполняет её целиком, и она гаснет вместе с подсветкой.
            $miniWidth = [single]($columnLeft + $columnWidth - 20 * $k - $textLeft)
            $miniHeight = [single](3 * $k)
            $miniTop = [single]($y + $cardHeight - 17 * $k)
            $miniPath = New-RoundedPath (New-Object System.Drawing.RectangleF($textLeft, $miniTop, $miniWidth, $miniHeight)) ($miniHeight / 2)
            $miniBrush = New-Object System.Drawing.SolidBrush((With-Alpha $ringTrack $lit))
            $g.FillPath($miniBrush, $miniPath)
            $miniBrush.Dispose()
            $part = -1.0
            if ($isDone) { $part = 1.0 }
            elseif ($i -eq 1 -and $S.InstallStarted) { $part = [Math]::Max(0.0, [Math]::Min(1.0, ($S.Fill - 0.10) / 0.80)) }
            if ($part -ge 0) {
                $runLeft = $textLeft
                $runWidth = [single]($miniWidth * $part)
            } else {
                $runWidth = [single]($miniWidth * 0.36)
                $runLeft = [single]($textLeft - $runWidth + ($miniWidth + $runWidth) * (Ease-InOut (($now % 1.5) / 1.5)))
            }
            if ($runWidth -gt $miniHeight) {
                $state = $g.Save()
                $g.SetClip($miniPath, [System.Drawing.Drawing2D.CombineMode]::Intersect)
                $runPath = New-RoundedPath (New-Object System.Drawing.RectangleF($runLeft, $miniTop, $runWidth, $miniHeight)) ($miniHeight / 2)
                $runBrush = New-Object System.Drawing.SolidBrush((With-Alpha $C.Accent $lit))
                $g.FillPath($runBrush, $runPath)
                $runBrush.Dispose(); $runPath.Dispose()
                $g.Restore($state)
            }
            $miniPath.Dispose()
        }
        End-Reveal $g $shown ($columnLeft - 8 * $k) ($y - 3 * $k) ($columnWidth + 16 * $k) ($cardHeight + 6 * $k)
        $y += $cardHeight + $cardGap
    }

    # --- Внизу: полоса хода Windows 11 (тонкая дорожка, поверх — заливка), под ней шутка и подпись. ---
    $shown = Begin-Reveal $g 0.36 $k
    $barWidth = [single]($width - 2 * $pad)
    $rail = New-Object System.Drawing.Pen($ringTrack, [single]([Math]::Max(1.0, 1.5 * $k)))
    $g.DrawLine($rail, $pad, ($barTop + $barHeight / 2), ($pad + $barWidth), ($barTop + $barHeight / 2))
    $rail.Dispose()
    $fillWidth = [single]($barWidth * $share)
    if ($fillWidth -gt $barHeight) {
        $fillPath = New-RoundedPath (New-Object System.Drawing.RectangleF($pad, $barTop, $fillWidth, $barHeight)) ($barHeight / 2)
        # Заливка светлеет к переднему краю: видно, куда идёт ход.
        $fillRect = New-Object System.Drawing.RectangleF(($pad - 1), $barTop, ($fillWidth + 2), $barHeight)
        $fillBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush($fillRect, (Mix-Color $C.Accent $S.Base 0.22), $C.Accent, [System.Drawing.Drawing2D.LinearGradientMode]::Horizontal)
        $g.FillPath($fillBrush, $fillPath)
        $fillBrush.Dispose()
        $clear = [System.Drawing.Color]::FromArgb(0, 255, 255, 255)
        $state = $g.Save()
        $g.SetClip($fillPath)
        if ($S.Fill -lt 0.999) {
            # Блик: тот же ход, что и на кольце, — они идут в такт.
            $band = [single]([Math]::Max($fillWidth * 0.22, 36 * $k))
            $center = $pad - $band + ($fillWidth + 2 * $band) * $glintAt
            $shineRect = New-Object System.Drawing.RectangleF(($center - $band), $barTop, (2 * $band), $barHeight)
            $shine = New-Object System.Drawing.Drawing2D.LinearGradientBrush($shineRect, $clear, $clear, [System.Drawing.Drawing2D.LinearGradientMode]::Horizontal)
            $blend = New-Object System.Drawing.Drawing2D.ColorBlend(3)
            $blend.Colors = [System.Drawing.Color[]]@($clear, [System.Drawing.Color]::FromArgb([int](105 * $glintGlow), 255, 255, 255), $clear)
            $blend.Positions = [single[]]@(0, 0.5, 1)
            $shine.InterpolationColors = $blend
            $g.FillRectangle($shine, $shineRect)
            $shine.Dispose()
        }
        if ($flash -lt 1) {
            # Этап закончился — по полосе один раз пробегает яркая вспышка и гаснет.
            $band = [single]([Math]::Max($fillWidth * 0.35, 60 * $k))
            $center = $pad + ($fillWidth + $band) * (Ease-Out $flash)
            $flashRect = New-Object System.Drawing.RectangleF(($center - $band), $barTop, (2 * $band), $barHeight)
            $flashBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush($flashRect, $clear, $clear, [System.Drawing.Drawing2D.LinearGradientMode]::Horizontal)
            $blend = New-Object System.Drawing.Drawing2D.ColorBlend(3)
            $blend.Colors = [System.Drawing.Color[]]@($clear, [System.Drawing.Color]::FromArgb([int](170 * (1 - $flash)), 255, 255, 255), $clear)
            $blend.Positions = [single[]]@(0, 0.5, 1)
            $flashBrush.InterpolationColors = $blend
            $g.FillRectangle($flashBrush, $flashRect)
            $flashBrush.Dispose()
        }
        $g.Restore($state)
        $fillPath.Dispose()
    }

    if ($S.Fill -lt 0.999) {
        $middleY = [single]($barTop + $barHeight / 2)
        $ahead = $barWidth - $fillWidth
        if ($ahead -gt 60 * $k) {
            $scoutX = [single]($pad + $fillWidth + 14 * $k + ($ahead - 42 * $k) * $scoutAt)
            $scoutPen = New-Object System.Drawing.Pen((With-Alpha $C.Accent (0.5 * $scoutGlow)), [single]($barHeight * 0.6))
            $scoutPen.StartCap = 'Round'; $scoutPen.EndCap = 'Round'
            $g.DrawLine($scoutPen, $scoutX, $middleY, [single]($scoutX + 24 * $k), $middleY)
            $scoutPen.Dispose()
        }
        if ($fillWidth -gt 2 * $barHeight) {
            $beadRadius = [single]($barHeight * 0.34)
            $beadBrush = New-Object System.Drawing.SolidBrush((With-Alpha $bead (0.72 + 0.2 * [Math]::Sin($now * 3.0))))
            $g.FillEllipse($beadBrush, [single]($pad + $fillWidth - $barHeight / 2 - $beadRadius), [single]($middleY - $beadRadius), (2 * $beadRadius), (2 * $beadRadius))
            $beadBrush.Dispose()
        }
    }

    $footerText = [string]$spec.texts.footer
    $footerWidth = $g.MeasureString($footerText, $F.Small).Width
    $footerBrush = New-Object System.Drawing.SolidBrush((With-Alpha $C.Muted 0.8))
    $g.DrawString($footerText, $F.Small, $footerBrush, [single]($pad + $barWidth - $footerWidth), [single]($captionTop + ($subHeight - $smallHeight) / 2))
    $footerBrush.Dispose()

    # Шутка: старая уплывает вверх и тает, новая выплывает снизу.
    $swap = Ease-Out (($now - $S.JokeAt) / 0.45)
    $shift = 8 * $k
    $jokeWidth = [single]([Math]::Max(60.0 * $k, $barWidth - $footerWidth - 28 * $k))
    if ($swap -lt 1 -and $S.PrevJoke) {
        $b = New-Object System.Drawing.SolidBrush((With-Alpha $C.Muted (1 - $swap)))
        $g.DrawString($S.PrevJoke, $F.Sub, $b, (New-Object System.Drawing.RectangleF($pad, ($captionTop - $shift * $swap), $jokeWidth, ($subHeight + 2 * $k))), $oneLine)
        $b.Dispose()
    }
    if ($S.Joke) {
        $b = New-Object System.Drawing.SolidBrush((With-Alpha $C.Muted $swap))
        $g.DrawString($S.Joke, $F.Sub, $b, (New-Object System.Drawing.RectangleF($pad, ($captionTop + $shift * (1 - $swap)), $jokeWidth, ($subHeight + 2 * $k))), $oneLine)
        $b.Dispose()
    }
    End-Reveal $g $shown ($pad - 6 * $k) ($barTop - 6 * $k) ($barWidth + 12 * $k) ([single]($height - $barTop))
    $fg.Dispose(); $muted.Dispose()
}

$form = New-Object System.Windows.Forms.Form
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
$form.ShowInTaskbar = $true
# Поверх всех — только пока старая программа на экране (см. Watch-OldApp).
$form.TopMost = (-not $S.Snapshot)
$form.Text = [string]$spec.texts.window_title
$form.BackColor = $C.Background
# Прозрачность — только украшение: где её нет (удалённый сеанс, служба),
# окно просто появляется сразу, а закрытие работает как обычно.
function Set-FormOpacity([double]$value) {
    $S.Opacity = $value
    if (-not $S.OpacityWorks) { return }
    try {
        $form.Opacity = $value
    } catch {
        $S.OpacityWorks = $false
        Write-Line "Прозрачность недоступна: $($_.Exception.Message)"
        try { $form.Opacity = 1 } catch { }
    }
}
$S.Opacity = 0.0
$S.OpacityWorks = $true
Set-FormOpacity 0
$bounds = New-Object System.Drawing.Rectangle([int]$spec.x, [int]$spec.y, [int]$spec.width, [int]$spec.height)
$visible = $false
foreach ($screen in [System.Windows.Forms.Screen]::AllScreens) {
    if ($screen.WorkingArea.IntersectsWith($bounds)) { $visible = $true }
}
if (-not $visible -or $bounds.Width -lt 200 -or $bounds.Height -lt 150) {
    # Место окна обновления за пределами экранов: по центру основного.
    $area = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
    $w = [int][Math]::Min([Math]::Max([double]$bounds.Width, 720.0), $area.Width - 40.0)
    $h = [int][Math]::Min([Math]::Max([double]$bounds.Height, 460.0), $area.Height - 40.0)
    $bounds = New-Object System.Drawing.Rectangle(($area.X + ($area.Width - $w) / 2), ($area.Y + ($area.Height - $h) / 2), $w, $h)
}
$form.Bounds = $bounds

# Общий масштаб картинки: макет рассчитан на окно 1040×660 и растягивается
# или сжимается вместе с настоящим окном — пустых полей не остаётся.
$screenScale = 1.0
try {
    $probe = [System.Drawing.Graphics]::FromHwnd([IntPtr]::Zero)
    $screenScale = $probe.DpiX / 96.0
    $probe.Dispose()
} catch { }
$M = [Math]::Max(0.6, [Math]::Min(1.4, [Math]::Min($bounds.Width / (1040.0 * $screenScale), $bounds.Height / (660.0 * $screenScale))))
$fontFamily = [string]$spec.font_family
if ([string]::IsNullOrWhiteSpace($fontFamily)) { $fontFamily = 'Segoe UI' }
# Полужирное начертание Windows 11 — отдельное семейство; где его нет, берётся жирное.
$boldFamily = $fontFamily
$boldStyle = [System.Drawing.FontStyle]::Bold
try {
    $probe = New-Object System.Drawing.Font("$fontFamily Semibold", 10)
    if ($probe.Name -eq "$fontFamily Semibold") {
        $boldFamily = "$fontFamily Semibold"
        $boldStyle = [System.Drawing.FontStyle]::Regular
    }
    $probe.Dispose()
} catch { }
$F = @{
    Title  = New-Object System.Drawing.Font($boldFamily, [single](24 * $M), $boldStyle)
    Big    = New-Object System.Drawing.Font($boldFamily, [single](32 * $M), $boldStyle)
    Sub    = New-Object System.Drawing.Font($fontFamily, [single](10 * $M))
    Stage  = New-Object System.Drawing.Font($fontFamily, [single](12 * $M))
    StageB = New-Object System.Drawing.Font($boldFamily, [single](12 * $M), $boldStyle)
    Small  = New-Object System.Drawing.Font($fontFamily, [single](9 * $M))
}
try {
    $buffered = [System.Windows.Forms.Control].GetProperty('DoubleBuffered', [System.Reflection.BindingFlags]'NonPublic,Instance')
    $buffered.SetValue($form, $true, $null)
} catch { }
if ($null -ne $logo) {
    try { $form.Icon = [System.Drawing.Icon]::FromHandle(([System.Drawing.Bitmap]$logo).GetHicon()) } catch { }
}

$form.Add_Paint({
    param($sender, $e)
    try {
        Draw-Frame $e.Graphics $form.ClientSize.Width $form.ClientSize.Height
    } catch {
        $S.TickErrors += 1
        if ($S.TickErrors -le 5) { Write-Line "Ошибка отрисовки: $($_.Exception.Message)" }
    }
})

$form.Add_Shown({
    if ($null -ne $native) {
        try { $round = 2; [void]$native::DwmSetWindowAttribute($form.Handle, 33, [ref]$round, 4) } catch { }
    }
    Write-Line "Место окна: $($form.Bounds)"
    Bring-ToFront 'показ'
    if (-not $S.Snapshot) {
        $shownPath = [string]$spec.shown_path
        if ($shownPath) {
            try { [System.IO.File]::WriteAllText($shownPath, (Get-Date).ToString('o'), $utf8NoBom) } catch { Write-Line "Метка показа не записана: $($_.Exception.Message)" }
        }
    }
    Write-Line 'Окно-продолжение показано'
})

Next-Joke
Poll-State

$timer = New-Object System.Windows.Forms.Timer
# 30 кадров в секунду: кадр стоит около 9 мс, чаще — отнимать процессор у установщика.
$timer.Interval = 33
$timer.Add_Tick({
    # Страховка вне общего try: что бы ни ломалось ниже, окно уйдёт.
    if ($S.Clock.Elapsed.TotalSeconds -gt ($totalTimeoutSeconds + 15)) {
        $timer.Stop()
        [System.Windows.Forms.Application]::Exit()
        return
    }
    try {
        $now = $S.Clock.Elapsed.TotalSeconds
        if ($now - $S.LastPoll -ge 0.25) { $S.LastPoll = $now; Poll-State }
        if ($now - $S.JokeAt -ge 2.8) { Next-Joke }
        $dt = [Math]::Max(0.0, [Math]::Min(0.1, $now - $S.LastTick))
        $S.LastTick = $now
        # Сглаживание по времени, а не по кадрам, и только вперёд: полоса
        # одинаково плавная при любой частоте кадров и никогда не идёт назад.
        # Звеньев два: полоса не дёргается с места и не встаёт как вкопанная,
        # а разгоняется и тормозит — у движения появляется вес.
        $target = Target-Fill
        $S.Lead = [Math]::Max($S.Lead, $S.Lead + ($target - $S.Lead) * (1 - [Math]::Exp(-$dt / 0.14)))
        $before = $S.Fill
        $S.Fill = [Math]::Max($S.Fill, $S.Fill + ($S.Lead - $S.Fill) * (1 - [Math]::Exp(-$dt / 0.16)))
        # Логотип-вертушка: спокойно крутится всегда, быстрее — пока полоса
        # растёт, и получает порыв, когда этап закончился. Скорость меняется
        # плавно, поэтому вертушка разгоняется и замедляется, а не дёргается.
        if ($dt -gt 0) { $S.Speed += (($S.Fill - $before) / $dt - $S.Speed) * (1 - [Math]::Exp(-$dt / 0.5)) }
        $rate = 80.0 + [Math]::Min(420.0, 1600.0 * $S.Speed)
        if ($S.FlashAt -ge 0 -and ($now - $S.FlashAt) -lt 1.2) { $rate += 300.0 * (1 - ($now - $S.FlashAt) / 1.2) }
        $S.Spin = ($S.Spin + $rate * $dt) % 360.0
        # Установка закончена — логотип в кольце делает один «вдох».
        if ($S.InstallSucceeded -and $S.Fill -ge 0.9 -and $S.DoneJumpAt -lt 0) { $S.DoneJumpAt = $now }
        if ($S.Stage -ge 2 -and $S.Fill -ge 0.99 -and $S.DoneAt[2] -lt 0) { $S.DoneAt[2] = $now; $S.FlashAt = $now }
        if ($S.Closing) {
            # Обычное закрытие: полоса доходит до конца, ставится последняя
            # галочка, и только потом окно тает. При неудаче — уходит сразу.
            $hold = if ($S.CloseFast) { 0.0 } else { 0.4 }
            $fade = if ($S.CloseFast) { 0.15 } else { 0.3 }
            $left = 1 - ($now - $S.ClosingAt - $hold) / $fade
            if ($left -le 0) { $timer.Stop(); $form.Close(); return }
            Set-FormOpacity ([Math]::Min($S.Opacity, $left))
        } elseif ($S.Opacity -lt 1) {
            Set-FormOpacity ([Math]::Min(1.0, $S.Opacity + $dt / 0.2))
            if ($S.Opacity -lt 1 -and $now -gt 1.5) {
                # Появление застряло: окно должно быть видно, а не красиво.
                Set-FormOpacity 1.0
                Write-Line 'Появление не завершилось само — окно показано сразу'
            }
            if ($S.Opacity -ge 1 -and $S.OpacityWorks) {
                try { Write-Line ("Окно проявилось: Opacity={0}" -f $form.Opacity) } catch { }
            }
        }
        if ($S.Snapshot -and $now * 1000 -ge $SnapshotAfterMs) {
            $timer.Stop()
            $bitmap = New-Object System.Drawing.Bitmap($form.ClientSize.Width, $form.ClientSize.Height)
            $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
            Draw-Frame $graphics $bitmap.Width $bitmap.Height
            $graphics.Dispose()
            $bitmap.Save($SnapshotPath, [System.Drawing.Imaging.ImageFormat]::Png)
            $bitmap.Dispose()
            $form.Close()
            return
        }
        $form.Invalidate()
    } catch {
        $S.TickErrors += 1
        if ($S.TickErrors -le 5) { Write-Line "Ошибка таймера: $($_.Exception.Message)" }
        if ($S.Closing -and $S.TickErrors -gt 30) { $timer.Stop(); $form.Close() }
    }
})
$timer.Start()

try {
    [System.Windows.Forms.Application]::Run($form)
} catch {
    Write-Line "Окно-продолжение упало: $($_.Exception.Message)"
    exit 1
}
if (-not $S.Snapshot) {
    # Окно больше не ждёт: обычные запуски программы метку не пишут.
    try { Remove-Item -LiteralPath $SpecPath -Force -ErrorAction SilentlyContinue } catch { }
}
Write-Line ("Кадров: {0} за {1:0.0} с" -f $S.Frames, $S.Clock.Elapsed.TotalSeconds)
Write-Line '--- окно-продолжение закрыто ---'
exit 0
""".lstrip()


def render_splash_script() -> str:
    """Подставляет в скрипт сроки, которые обязаны совпадать с Python-частью."""
    return SPLASH_SCRIPT_TEMPLATE.replace("@READY_TIMEOUT_SECONDS@", str(READY_TIMEOUT_SECONDS)).replace(
        "@TOTAL_TIMEOUT_SECONDS@", str(TOTAL_TIMEOUT_SECONDS)
    )


__all__ = [
    "READY_TIMEOUT_SECONDS",
    "SPLASH_SCRIPT_TEMPLATE",
    "TOTAL_TIMEOUT_SECONDS",
    "render_splash_script",
]
