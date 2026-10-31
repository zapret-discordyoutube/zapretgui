# One-time setup on the Windows VM for running Zapret from the sources.
# See start.py for how a launch works. Run as administrator (an ssh session
# to win10 already is one):
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
#
# Creates:
#   task ZapretFromSource       - runs run_app.py as administrator, no UAC prompt
#   task ZapretFromSourceStart  - runs start.py as the normal user (lets the
#                                 Linux host start it: ssh win10 schtasks /run /tn ZapretFromSourceStart)
#   desktop shortcut "Zapret (sources)" - runs start.py
# Running it again replaces them. Remove with: install.ps1 -Remove

param([switch] $Remove)

$ErrorActionPreference = "Stop"

$python = "C:\Users\privacy\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"
$startScript = "G:\public_zapretgui\scripts\dev_from_source\start.py"
$runScript = Join-Path $env:LOCALAPPDATA "ZapretGUI\dev_from_source\run_app.py"
$shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Zapret (sources).lnk"
# Exact account name: in an ssh session $env:USERDOMAIN does not match it.
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name

foreach ($name in @("ZapretFromSource", "ZapretFromSourceStart")) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
}
if (Test-Path -LiteralPath $shortcut) { Remove-Item -LiteralPath $shortcut }
if ($Remove) { "Removed."; exit 0 }

if (-not (Test-Path -LiteralPath $python)) { throw "Python not found: $python" }

# No time limit (the default stops a task after 72 hours), works on battery,
# a second start while running is ignored.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew

$admin = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$action = New-ScheduledTaskAction -Execute $python -Argument "-X utf8 `"$runScript`""
Register-ScheduledTask -TaskName "ZapretFromSource" -Action $action -Principal $admin `
    -Settings $settings -Description "Zapret from sources: run_app.py (see G:\public_zapretgui\scripts\dev_from_source)" | Out-Null

$normal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$action = New-ScheduledTaskAction -Execute $python -Argument "-X utf8 `"$startScript`""
Register-ScheduledTask -TaskName "ZapretFromSourceStart" -Action $action -Principal $normal `
    -Settings $settings -Description "Zapret from sources: start.py (see G:\public_zapretgui\scripts\dev_from_source)" | Out-Null

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcut)
$link.TargetPath = $python
$link.Arguments = "-X utf8 `"$startScript`""
$link.WorkingDirectory = "G:\public_zapretgui"
$link.IconLocation = "C:\Zapret\Dev\_internal\Zapret.exe,0"
$link.Description = "Start Zapret from the current sources on G:"
$link.Save()

"Installed: tasks ZapretFromSource, ZapretFromSourceStart; shortcut $shortcut"
