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
    Background = Get-Color ([string]$spec.colors.background) '#202020'
    Border     = Get-Color ([string]$spec.colors.border) '#3a3a3a'
    Foreground = Get-Color ([string]$spec.colors.foreground) '#ffffff'
    Muted      = Get-Color ([string]$spec.colors.muted) '#9aa0a6'
    Accent     = Get-Color ([string]$spec.colors.accent) '#60cdff'
    Track      = Get-Color ([string]$spec.colors.track) '#3a3a3a'
    OnAccent   = Get-Color ([string]$spec.colors.on_accent) '#000000'
}

$fontFamily = [string]$spec.font_family
if ([string]::IsNullOrWhiteSpace($fontFamily)) { $fontFamily = 'Segoe UI' }
$F = @{
    Title  = New-Object System.Drawing.Font($fontFamily, 17, [System.Drawing.FontStyle]::Bold)
    Sub    = New-Object System.Drawing.Font($fontFamily, 9.5)
    Stage  = New-Object System.Drawing.Font($fontFamily, 11)
    StageB = New-Object System.Drawing.Font($fontFamily, 11, [System.Drawing.FontStyle]::Bold)
    Joke   = New-Object System.Drawing.Font($fontFamily, 10, [System.Drawing.FontStyle]::Italic)
    Small  = New-Object System.Drawing.Font($fontFamily, 9)
}
# Шутка занимает одну строку: длинная обрезается многоточием, а не лезет на проценты.
$oneLine = New-Object System.Drawing.StringFormat
$oneLine.FormatFlags = [System.Drawing.StringFormatFlags]::NoWrap
$oneLine.Trimming = [System.Drawing.StringTrimming]::EllipsisCharacter

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
    LastTick     = 0.0
    # Сглаженная скорость полосы (доля в секунду): по ней наклоняется логотип.
    Speed        = 0.0
    RevealState  = $null
    Frames       = 0
    Fill         = 0.0
    SucceededAt  = -1.0
    DoneJumpAt   = -1.0
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
        $veil = New-Object System.Drawing.SolidBrush((With-Alpha $C.Background (1 - $shown)))
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

function Draw-Frame([System.Drawing.Graphics]$g, [int]$width, [int]$height) {
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::ClearTypeGridFit
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $k = [single]($g.DpiX / 96.0)
    $now = $S.Clock.Elapsed.TotalSeconds
    $S.Frames += 1

    $g.Clear($C.Background)
    $borderPen = New-Object System.Drawing.Pen($C.Border, [single]1)
    $g.DrawRectangle($borderPen, 0, 0, $width - 1, $height - 1)
    $borderPen.Dispose()

    $pad = [single](36 * $k)
    $contentWidth = [single]([Math]::Min($width - 2 * $pad, 640 * $k))
    $left = [single](($width - $contentWidth) / 2)
    $rowHeight = [single](44 * $k)
    $logoSize = [single](34 * $k)
    $trackHeight = [single](6 * $k)
    $titleHeight = $F.Title.GetHeight($g)
    $subHeight = $F.Sub.GetHeight($g)
    $jokeHeight = $F.Joke.GetHeight($g)
    $blockHeight = $titleHeight + 4 * $k + $subHeight + 28 * $k + $stages.Count * $rowHeight + 18 * $k +
        $logoSize + 10 * $k + $trackHeight + 14 * $k + $jokeHeight + 20 * $k + $F.Small.GetHeight($g)
    $top = [single]([Math]::Max($pad, ($height - $blockHeight) / 2))

    # Заслонка появления чуть шире содержимого: сглаженные края букв и кружков.
    $veilLeft = [single]($left - 8 * $k)
    $veilWidth = [single]($contentWidth + 16 * $k)

    # Заголовок и подзаголовок.
    $fg = New-Object System.Drawing.SolidBrush($C.Foreground)
    $muted = New-Object System.Drawing.SolidBrush($C.Muted)
    $shown = Begin-Reveal $g 0.0 $k
    $g.DrawString([string]$spec.texts.title, $F.Title, $fg, $left, $top)
    $y = $top + $titleHeight + 4 * $k
    $g.DrawString([string]$spec.texts.subtitle, $F.Sub, $muted, $left, $y)
    End-Reveal $g $shown $veilLeft ($top - 4 * $k) $veilWidth ($titleHeight + $subHeight + 16 * $k)
    $y += $subHeight + 28 * $k

    # Этапы — столбик с соединительной линией: готовый этап закрашивает кружок
    # и линию до следующего, текущий крутит дугу, будущие приглушены.
    $r = [single](10 * $k)
    $cx = $left + $r + 1 * $k
    for ($i = 0; $i -lt $stages.Count; $i++) {
        $cy = $y + $rowHeight / 2
        $shown = Begin-Reveal $g (0.08 + 0.07 * $i) $k
        $doneAt = [double]$S.DoneAt[$i]
        $isDone = ($doneAt -ge 0)
        $doneFor = $now - $doneAt
        if ($i -lt $stages.Count - 1) {
            $lineTop = $cy + $r + 5 * $k
            $lineHeight = $rowHeight - 2 * $r - 10 * $k
            $linePen = New-Object System.Drawing.Pen($C.Track, [single](2 * $k))
            $linePen.StartCap = 'Round'; $linePen.EndCap = 'Round'
            $g.DrawLine($linePen, $cx, $lineTop, $cx, ($lineTop + $lineHeight))
            $linePen.Dispose()
            if ($isDone) {
                $grown = Ease-Out (($doneFor - 0.15) / 0.4)
                if ($grown -gt 0) {
                    $linePen = New-Object System.Drawing.Pen($C.Accent, [single](2 * $k))
                    $linePen.StartCap = 'Round'; $linePen.EndCap = 'Round'
                    $g.DrawLine($linePen, $cx, $lineTop, $cx, [single]($lineTop + $lineHeight * $grown))
                    $linePen.Dispose()
                }
            }
        }
        if ($isDone) {
            # Кружок заливается от центра, затем росчерком появляется галочка.
            $ring = New-Object System.Drawing.Pen((With-Alpha $C.Accent 0.3), [single](2 * $k))
            $g.DrawEllipse($ring, $cx - $r, $cy - $r, 2 * $r, 2 * $r)
            $ring.Dispose()
            $grow = [single]($r * (0.35 + 0.65 * (Ease-Back ($doneFor / 0.38))))
            $b = New-Object System.Drawing.SolidBrush($C.Accent)
            $g.FillEllipse($b, $cx - $grow, $cy - $grow, 2 * $grow, 2 * $grow)
            $b.Dispose()
            if ($doneFor -lt 0.55) {
                # Расходящееся кольцо: этап только что закончился.
                $wave = Ease-Out ($doneFor / 0.55)
                $waveRadius = [single]($r + 7 * $k * $wave)
                $wavePen = New-Object System.Drawing.Pen((With-Alpha $C.Accent (0.45 * (1 - $wave))), [single](1.6 * $k))
                $g.DrawEllipse($wavePen, $cx - $waveRadius, $cy - $waveRadius, 2 * $waveRadius, 2 * $waveRadius)
                $wavePen.Dispose()
            }
            Draw-Check $g $cx $cy $k (Ease-Out (($doneFor - 0.12) / 0.3))
            $font = $F.Stage; $color = $C.Foreground
        } elseif ($i -eq $S.Stage) {
            # Бледное кольцо и дуга, которая бежит по нему и «дышит» длиной.
            $ring = New-Object System.Drawing.Pen((With-Alpha $C.Accent 0.25), [single](2.2 * $k))
            $g.DrawEllipse($ring, $cx - $r, $cy - $r, 2 * $r, 2 * $r)
            $ring.Dispose()
            $arc = New-Object System.Drawing.Pen($C.Accent, [single](2.2 * $k))
            $arc.StartCap = 'Round'; $arc.EndCap = 'Round'
            $sweep = 95 + 45 * [Math]::Sin($now * 2.1)
            $g.DrawArc($arc, $cx - $r, $cy - $r, 2 * $r, 2 * $r, [single](($now * 250) % 360), [single]$sweep)
            $arc.Dispose()
            $font = $F.StageB
            $color = Mix-Color $C.Muted $C.Foreground (Ease-Out (($now - $S.StageSince) / 0.3))
        } else {
            $p = New-Object System.Drawing.Pen((With-Alpha $C.Muted 0.45), [single](1.6 * $k))
            $g.DrawEllipse($p, $cx - $r, $cy - $r, 2 * $r, 2 * $r)
            $p.Dispose()
            $font = $F.Stage; $color = $C.Muted
        }
        $label = [string]$stages[$i]
        $textLeft = $left + 34 * $k
        $brush = New-Object System.Drawing.SolidBrush($color)
        $g.DrawString($label, $font, $brush, $textLeft, ($cy - $font.GetHeight($g) / 2))
        $brush.Dispose()
        if ($i -eq 1 -and $S.Stage -eq 1 -and $S.InstallStarted -and $S.Expected -gt 0) {
            # Настоящий ход копирования рядом с этапом: «312 из 682 файлов».
            $done = [Math]::Min($S.FilesDone, $S.Expected)
            $counter = ([string]$spec.texts.files_template).Replace('{done}', [string]$done).Replace('{total}', [string]$S.Expected)
            $labelWidth = $g.MeasureString($label, $font).Width
            $g.DrawString("·  $counter", $F.Sub, $muted, ($textLeft + $labelWidth + 6 * $k), ($cy - $subHeight / 2))
        }
        End-Reveal $g $shown $veilLeft $y $veilWidth $rowHeight
        $y += $rowHeight
    }
    $y += 18 * $k

    # Полоса во всю ширину: тонкая дорожка, заливка цвета акцента с мягким
    # бликом и свечением на переднем крае.
    $trackLeft = $left
    $trackWidth = $contentWidth
    $trackTop = $y + $logoSize + 10 * $k
    $shown = Begin-Reveal $g 0.32 $k
    $trackRect = New-Object System.Drawing.RectangleF($trackLeft, $trackTop, $trackWidth, $trackHeight)
    $trackPath = New-RoundedPath $trackRect ($trackHeight / 2)
    $trackBrush = New-Object System.Drawing.SolidBrush($C.Track)
    $g.FillPath($trackBrush, $trackPath)
    $trackBrush.Dispose(); $trackPath.Dispose()

    $fillWidth = [single]([Math]::Max(0.0, $trackWidth * $S.Fill))
    $headX = $trackLeft + $fillWidth
    if ($fillWidth -gt $trackHeight) {
        $fillRect = New-Object System.Drawing.RectangleF($trackLeft, $trackTop, $fillWidth, $trackHeight)
        $fillPath = New-RoundedPath $fillRect ($trackHeight / 2)
        $dark = [System.Drawing.Color]::FromArgb(255, [int]($C.Accent.R * 0.88), [int]($C.Accent.G * 0.88), [int]($C.Accent.B * 0.88))
        $light = [System.Drawing.Color]::FromArgb(255, [int][Math]::Min(255.0, $C.Accent.R + 36.0), [int][Math]::Min(255.0, $C.Accent.G + 36.0), [int][Math]::Min(255.0, $C.Accent.B + 36.0))
        $gradRect = New-Object System.Drawing.RectangleF(($trackLeft - 1), $trackTop, ($fillWidth + 2), $trackHeight)
        $grad = New-Object System.Drawing.Drawing2D.LinearGradientBrush($gradRect, $dark, $light, [System.Drawing.Drawing2D.LinearGradientMode]::Horizontal)
        $g.FillPath($grad, $fillPath)
        $grad.Dispose()
        if ($S.Fill -lt 0.999) {
            # Блик проходит по заливке раз в две с небольшим секунды: видно, что работа идёт.
            $band = [single]([Math]::Max($fillWidth * 0.3, 36 * $k))
            $phase = ($now % 2.2) / 2.2
            $center = $trackLeft - $band + ($fillWidth + 2 * $band) * $phase
            $shineRect = New-Object System.Drawing.RectangleF(($center - $band), $trackTop, (2 * $band), $trackHeight)
            $clear = [System.Drawing.Color]::FromArgb(0, 255, 255, 255)
            $shine = New-Object System.Drawing.Drawing2D.LinearGradientBrush($shineRect, $clear, $clear, [System.Drawing.Drawing2D.LinearGradientMode]::Horizontal)
            $blend = New-Object System.Drawing.Drawing2D.ColorBlend(3)
            $blend.Colors = [System.Drawing.Color[]]@($clear, [System.Drawing.Color]::FromArgb(95, 255, 255, 255), $clear)
            $blend.Positions = [single[]]@(0, 0.5, 1)
            $shine.InterpolationColors = $blend
            $state = $g.Save()
            $g.SetClip($fillPath)
            $g.FillRectangle($shine, $shineRect)
            $g.Restore($state)
            $shine.Dispose()

            $glowRadius = [single](9 * $k)
            $glowPath = New-Object System.Drawing.Drawing2D.GraphicsPath
            $glowPath.AddEllipse(($headX - $glowRadius - $trackHeight / 2), ($trackTop + $trackHeight / 2 - $glowRadius), (2 * $glowRadius), (2 * $glowRadius))
            $glow = New-Object System.Drawing.Drawing2D.PathGradientBrush -ArgumentList $glowPath
            $glow.CenterColor = With-Alpha $C.Accent (0.4 + 0.15 * [Math]::Sin($now * 2.6))
            $glow.SurroundColors = [System.Drawing.Color[]]@((With-Alpha $C.Accent 0.0))
            $g.FillPath($glow, $glowPath)
            $glow.Dispose(); $glowPath.Dispose()
        }
        $fillPath.Dispose()
    }

    # Логотип едет над передним краем заливки и слегка покачивается; когда
    # установщик закончил — один мягкий «вдох» вместо кувырка.
    if ($null -ne $logo) {
        $runnerX = [Math]::Max($left + $logoSize / 2, [Math]::Min($left + $contentWidth - $logoSize / 2, [double]$headX))
        $bob = 1.6 * $k * [Math]::Sin($now * 2.4)
        # Наклон вперёд по скорости полосы: чем быстрее идёт установка, тем сильнее.
        $lean = [Math]::Min(11.0, $S.Speed * 130.0)
        $scale = 1.0
        if ($S.DoneJumpAt -ge 0) {
            $t = ($now - $S.DoneJumpAt) / 0.6
            if ($t -lt 1) { $scale = 1 + 0.16 * [Math]::Sin([Math]::PI * $t) }
        }
        $size = [single]($logoSize * $scale)
        $state = $g.Save()
        $g.TranslateTransform([single]$runnerX, [single]($trackTop - 10 * $k - $logoSize / 2 + $bob))
        $g.RotateTransform([single]$lean)
        $g.DrawImage($logo, (New-Object System.Drawing.RectangleF((-$size / 2), (-$size / 2), $size, $size)))
        $g.Restore($state)
    }
    End-Reveal $g $shown $veilLeft $y $veilWidth ($logoSize + 10 * $k + $trackHeight + 14 * $k)
    $y = $trackTop + $trackHeight + 14 * $k
    $shown = Begin-Reveal $g 0.42 $k

    # Под полосой: слева шутка, справа проценты.
    $percent = '{0} %' -f [int][Math]::Round(100.0 * [Math]::Min(1.0, $S.Fill))
    $percentWidth = $g.MeasureString($percent, $F.Sub).Width
    $g.DrawString($percent, $F.Sub, $muted, ($left + $contentWidth - $percentWidth), ($y + ($jokeHeight - $subHeight) / 2))

    # Шутка: старая уплывает вверх и тает, новая выплывает снизу.
    $swap = Ease-Out (($now - $S.JokeAt) / 0.45)
    $shift = 10 * $k
    $jokeWidth = [single]($contentWidth - $percentWidth - 16 * $k)
    if ($swap -lt 1 -and $S.PrevJoke) {
        $b = New-Object System.Drawing.SolidBrush((With-Alpha $C.Muted (1 - $swap)))
        $g.DrawString($S.PrevJoke, $F.Joke, $b, (New-Object System.Drawing.RectangleF($left, ($y - $shift * $swap), $jokeWidth, ($jokeHeight + 2 * $k))), $oneLine)
        $b.Dispose()
    }
    if ($S.Joke) {
        $b = New-Object System.Drawing.SolidBrush((With-Alpha $C.Muted $swap))
        $g.DrawString($S.Joke, $F.Joke, $b, (New-Object System.Drawing.RectangleF($left, ($y + $shift * (1 - $swap)), $jokeWidth, ($jokeHeight + 2 * $k))), $oneLine)
        $b.Dispose()
    }
    $y += $jokeHeight + 20 * $k
    $footer = New-Object System.Drawing.SolidBrush((With-Alpha $C.Muted 0.75))
    $g.DrawString([string]$spec.texts.footer, $F.Small, $footer, $left, $y)
    $footer.Dispose()
    $revealTop = $trackTop + $trackHeight + 14 * $k - $shift
    End-Reveal $g $shown $veilLeft $revealTop $veilWidth ($y + $F.Small.GetHeight($g) + 8 * $k - $revealTop)
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
        $target = Target-Fill
        $before = $S.Fill
        $S.Fill = [Math]::Max($S.Fill, $S.Fill + ($target - $S.Fill) * (1 - [Math]::Exp(-$dt / 0.22)))
        if ($dt -gt 0) { $S.Speed += (($S.Fill - $before) / $dt - $S.Speed) * (1 - [Math]::Exp(-$dt / 0.25)) }
        # Установка закончена — логотип делает один мягкий «вдох».
        if ($S.InstallSucceeded -and $S.Fill -ge 0.9 -and $S.DoneJumpAt -lt 0) { $S.DoneJumpAt = $now }
        if ($S.Stage -ge 2 -and $S.Fill -ge 0.99 -and $S.DoneAt[2] -lt 0) { $S.DoneAt[2] = $now }
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
