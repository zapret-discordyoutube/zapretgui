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
$random = New-Object System.Random

# Изменяемое состояние живёт в таблице: обработчики событий WinForms видят её
# без путаницы областей видимости PowerShell.
$S = @{
    Clock        = [System.Diagnostics.Stopwatch]::StartNew()
    StartedAt    = (Get-Date)
    Stage        = 0
    StageSince   = 0.0
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
    Shown        = $false
    Snapshot     = (-not [string]::IsNullOrWhiteSpace($SnapshotPath))
    TickErrors   = 0
    OldAlive     = $true
    OldCheckedAt = -1.0
}

function Next-Joke {
    if ($jokes.Count -eq 0) { return }
    $choices = @($jokes | Where-Object { [string]$_ -ne $S.Joke })
    if ($choices.Count -eq 0) { $choices = $jokes }
    $S.PrevJoke = $S.Joke
    $S.Joke = [string]$choices[$random.Next($choices.Count)]
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
    $S.Stage = $stage
    $S.StageSince = $S.Clock.Elapsed.TotalSeconds
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

function Poll-State {
    $now = $S.Clock.Elapsed.TotalSeconds
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

function Target-Fill {
    $now = $S.Clock.Elapsed.TotalSeconds
    switch ($S.Stage) {
        0 { return 0.12 }
        1 { return 0.15 + 0.72 * (1 - [Math]::Exp(-($now - $S.StageSince) / 22.0)) }
        default { return 1.0 }
    }
}

function Draw-Frame([System.Drawing.Graphics]$g, [int]$width, [int]$height) {
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::ClearTypeGridFit
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $k = [single]($g.DpiX / 96.0)
    $now = $S.Clock.Elapsed.TotalSeconds

    $g.Clear($C.Background)
    $borderPen = New-Object System.Drawing.Pen($C.Border, [single]1)
    $g.DrawRectangle($borderPen, 0, 0, $width - 1, $height - 1)
    $borderPen.Dispose()

    $pad = [single](36 * $k)
    $contentWidth = [single]([Math]::Min($width - 2 * $pad, 720 * $k))
    $left = [single](($width - $contentWidth) / 2)
    $blockHeight = [single](430 * $k)
    $top = [single]([Math]::Max($pad, ($height - $blockHeight) / 2))

    # Заголовок и подзаголовок.
    $fg = New-Object System.Drawing.SolidBrush($C.Foreground)
    $muted = New-Object System.Drawing.SolidBrush($C.Muted)
    $g.DrawString([string]$spec.texts.title, $F.Title, $fg, $left, $top)
    $y = $top + $F.Title.GetHeight($g) + 4 * $k
    $g.DrawString([string]$spec.texts.subtitle, $F.Sub, $muted, $left, $y)
    $y += $F.Sub.GetHeight($g) + 30 * $k

    # Этапы: готовые — галочка, текущий — пульсирующее кольцо.
    for ($i = 0; $i -lt $stages.Count; $i++) {
        $cx = $left + 11 * $k
        $cy = $y + 11 * $k
        $r = [single](10 * $k)
        if ($i -lt $S.Stage -or ($S.Stage -ge 2 -and $i -eq 2 -and $S.Fill -ge 0.99)) {
            $b = New-Object System.Drawing.SolidBrush($C.Accent)
            $g.FillEllipse($b, $cx - $r, $cy - $r, 2 * $r, 2 * $r)
            $b.Dispose()
            $p = New-Object System.Drawing.Pen($C.OnAccent, [single](2.2 * $k))
            $p.StartCap = 'Round'; $p.EndCap = 'Round'
            $g.DrawLines($p, [System.Drawing.PointF[]]@(
                (New-Object System.Drawing.PointF(($cx - 4.5 * $k), ($cy + 0.2 * $k))),
                (New-Object System.Drawing.PointF(($cx - 1.2 * $k), ($cy + 3.6 * $k))),
                (New-Object System.Drawing.PointF(($cx + 5 * $k), ($cy - 3.8 * $k)))
            ))
            $p.Dispose()
            $font = $F.Stage; $brush = $fg
        } elseif ($i -eq $S.Stage) {
            $pulse = 0.5 + 0.5 * [Math]::Sin($now * 4.2)
            $halo = New-Object System.Drawing.SolidBrush((With-Alpha $C.Accent (0.12 + 0.18 * $pulse)))
            $hr = $r + 4 * $k * $pulse
            $g.FillEllipse($halo, $cx - $hr, $cy - $hr, 2 * $hr, 2 * $hr)
            $halo.Dispose()
            $p = New-Object System.Drawing.Pen($C.Accent, [single](2.4 * $k))
            $g.DrawEllipse($p, $cx - $r, $cy - $r, 2 * $r, 2 * $r)
            $p.Dispose()
            # Бегущая дуга внутри кольца: этап в работе.
            $arc = New-Object System.Drawing.Pen($C.Accent, [single](2.4 * $k))
            $arc.StartCap = 'Round'; $arc.EndCap = 'Round'
            $inner = $r - 4.5 * $k
            $g.DrawArc($arc, $cx - $inner, $cy - $inner, 2 * $inner, 2 * $inner, [single](($now * 300) % 360), [single]110)
            $arc.Dispose()
            $font = $F.StageB; $brush = $fg
        } else {
            $p = New-Object System.Drawing.Pen((With-Alpha $C.Muted 0.6), [single](1.6 * $k))
            $g.DrawEllipse($p, $cx - $r, $cy - $r, 2 * $r, 2 * $r)
            $p.Dispose()
            $font = $F.Stage; $brush = $muted
        }
        $textY = $cy - $font.GetHeight($g) / 2
        $g.DrawString([string]$stages[$i], $font, $brush, ($left + 32 * $k), $textY)
        $y += 36 * $k
    }
    $y += 34 * $k

    # Дорожка: градиент цвета акцента и бегущий блик, по ней бежит логотип.
    $logoSize = [single](38 * $k)
    $trackHeight = [single](10 * $k)
    $trackLeft = $left + $logoSize / 2
    $trackWidth = $contentWidth - $logoSize
    $trackTop = $y + $logoSize * 1.45
    $trackRect = New-Object System.Drawing.RectangleF($trackLeft, $trackTop, $trackWidth, $trackHeight)
    $trackPath = New-RoundedPath $trackRect ($trackHeight / 2)
    $trackBrush = New-Object System.Drawing.SolidBrush($C.Track)
    $g.FillPath($trackBrush, $trackPath)
    $trackBrush.Dispose(); $trackPath.Dispose()

    $fillWidth = [single]([Math]::Max(0.0, $trackWidth * $S.Fill))
    if ($fillWidth -gt 1) {
        $fillRect = New-Object System.Drawing.RectangleF($trackLeft, $trackTop, $fillWidth, $trackHeight)
        $fillPath = New-RoundedPath $fillRect ($trackHeight / 2)
        $dark = [System.Drawing.Color]::FromArgb(255, [int]($C.Accent.R * 0.85), [int]($C.Accent.G * 0.85), [int]($C.Accent.B * 0.85))
        $light = [System.Drawing.Color]::FromArgb(255, [int][Math]::Min(255.0, $C.Accent.R + 60.0), [int][Math]::Min(255.0, $C.Accent.G + 60.0), [int][Math]::Min(255.0, $C.Accent.B + 60.0))
        $gradRect = New-Object System.Drawing.RectangleF(($trackLeft - 1), $trackTop, ($fillWidth + 2), $trackHeight)
        $grad = New-Object System.Drawing.Drawing2D.LinearGradientBrush($gradRect, $dark, $light, [System.Drawing.Drawing2D.LinearGradientMode]::Horizontal)
        $g.FillPath($grad, $fillPath)
        $grad.Dispose()
        if ($S.Fill -lt 0.999) {
            $band = [single]([Math]::Max($fillWidth * 0.35, 40 * $k))
            $phase = ($now % 1.6) / 1.6
            $center = $trackLeft - $band + ($fillWidth + 2 * $band) * $phase
            $shineRect = New-Object System.Drawing.RectangleF(($center - $band), $trackTop, (2 * $band), $trackHeight)
            $shine = New-Object System.Drawing.Drawing2D.LinearGradientBrush($shineRect, [System.Drawing.Color]::FromArgb(0, 255, 255, 255), [System.Drawing.Color]::FromArgb(0, 255, 255, 255), [System.Drawing.Drawing2D.LinearGradientMode]::Horizontal)
            $blend = New-Object System.Drawing.Drawing2D.ColorBlend(3)
            $blend.Colors = [System.Drawing.Color[]]@([System.Drawing.Color]::FromArgb(0, 255, 255, 255), [System.Drawing.Color]::FromArgb(120, 255, 255, 255), [System.Drawing.Color]::FromArgb(0, 255, 255, 255))
            $blend.Positions = [single[]]@(0, 0.5, 1)
            $shine.InterpolationColors = $blend
            $state = $g.Save()
            $g.SetClip($fillPath)
            $g.FillRectangle($shine, $shineRect)
            $g.Restore($state)
            $shine.Dispose()
        }
        $fillPath.Dispose()
    }

    # Логотип бежит по краю закрашенной части: подскок, покачивание, след.
    $runnerX = $trackLeft + $fillWidth
    $hop = 0.0; $angle = 0.0; $squash = 1.0
    if ($S.DoneJumpAt -ge 0) {
        $t = ($now - $S.DoneJumpAt) / 1.1
        if ($t -lt 1) {
            $hop = $logoSize * 0.5 * [Math]::Sin([Math]::PI * $t)
            $angle = 360 * $t
        }
    } else {
        $stride = ($now % 0.52) / 0.52
        $hop = $logoSize * 0.16 * [Math]::Abs([Math]::Sin([Math]::PI * $stride))
        $angle = 9 * [Math]::Sin([Math]::PI * ($now % 1.04) / 0.52) + 6
        $squash = 1 - 0.07 * (1 - [Math]::Abs([Math]::Sin([Math]::PI * $stride)))
        for ($i = 1; $i -le 5; $i++) {
            $dotBrush = New-Object System.Drawing.SolidBrush((With-Alpha $C.Accent ([Math]::Max(0.0, 0.5 - $i * 0.09))))
            $dr = [single]((3.2 - $i * 0.4) * $k)
            $dx = $runnerX - ($logoSize * 0.35 + $i * 7 * $k)
            $dy = $trackTop - 5 * $k + [Math]::Sin($now * 11 + $i) * 1.5 * $k
            $g.FillEllipse($dotBrush, $dx - $dr, $dy - $dr, 2 * $dr, 2 * $dr)
            $dotBrush.Dispose()
        }
    }
    if ($null -ne $logo) {
        $state = $g.Save()
        $g.TranslateTransform([single]$runnerX, [single]($trackTop - 2 * $k - $hop))
        $g.ScaleTransform([single](2 - $squash), [single]$squash)
        $g.TranslateTransform(0, [single](-$logoSize / 2))
        $g.RotateTransform([single]$angle)
        $g.DrawImage($logo, (New-Object System.Drawing.RectangleF((-$logoSize / 2), (-$logoSize / 2), $logoSize, $logoSize)))
        $g.Restore($state)
    }
    $y = $trackTop + $trackHeight + 22 * $k

    # Шутка: старая уплывает вверх и тает, новая выплывает снизу.
    $swap = [Math]::Min(1.0, ($now - $S.JokeAt) / 0.42)
    $shift = 14 * $k
    if ($swap -lt 1 -and $S.PrevJoke) {
        $b = New-Object System.Drawing.SolidBrush((With-Alpha $C.Muted (1 - $swap)))
        $g.DrawString($S.PrevJoke, $F.Joke, $b, $left, ($y - $shift * $swap))
        $b.Dispose()
    }
    if ($S.Joke) {
        $b = New-Object System.Drawing.SolidBrush((With-Alpha $C.Muted $swap))
        $g.DrawString($S.Joke, $F.Joke, $b, $left, ($y + $shift * (1 - $swap)))
        $b.Dispose()
    }
    $y += $F.Joke.GetHeight($g) + 26 * $k
    $g.DrawString([string]$spec.texts.footer, $F.Small, $muted, $left, $y)
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
        $target = Target-Fill
        $S.Fill = $S.Fill + ($target - $S.Fill) * 0.12
        if ($S.Stage -ge 2 -and $S.Fill -ge 0.995 -and $S.DoneJumpAt -lt 0) { $S.Fill = 1.0; $S.DoneJumpAt = $now }
        if ($S.Closing) {
            $fade = if ($S.CloseFast) { 0.15 } else { 0.35 }
            $left = 1 - ($now - $S.ClosingAt) / $fade
            if ($left -le 0) { $timer.Stop(); $form.Close(); return }
            Set-FormOpacity ([Math]::Min($S.Opacity, $left))
        } elseif ($S.Opacity -lt 1) {
            Set-FormOpacity ([Math]::Min(1.0, $S.Opacity + 0.16))
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
