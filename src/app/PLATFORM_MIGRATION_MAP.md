# Multi-platform migration map

Generated from `src/app/windows_api_files.txt` (the fence list, see `app/architecture_checks.py`). Each Windows-touching file is assigned to the interface in `src/platforms/` it will move behind. The assignment is a first guess from the file name and its matching lines; confirm it when the interface is cut. Task file: `/root/.agent-todo/zapretgui/2026-11-15-multi-platform-linux-gui.md`.

Numbers are lines that use Windows-only calls (`windll`, `winreg`, `powershell`, `sc.exe`, `taskkill`, `schtasks`, `win32*`, `wintypes`). Some are only comments or docstrings (for example the updater's PowerShell texts); they count too, because the text is Windows-only.

| Interface | Files | Lines |
|---|---:|---:|
| launch | 3 | 12 |
| privileges | 7 | 34 |
| single_instance | 1 | 1 |
| process | 7 | 129 |
| netprobe | 8 | 78 |
| autostart and services | 7 | 70 |
| theme_and_window | 13 | 87 |
| tray | 3 | 92 |
| associations | 4 | 23 |
| updater_install | 10 | 64 |
| engine | 5 | 24 |
| integrations | 4 | 66 |
| **total** | **72** | **680** |

## launch (first start of the program)

The launch gate, message boxes before Qt exists, UAC relaunch, process start time.

- `src/main/process_start_time.py` — 10
- `src/main/shell.py` — 1
- `src/zapret_update/apply.py` — 1

## privileges (admin / root, elevation)

`IsUserAnAdmin`, relaunch as administrator, `runas`. Linux: root check, polkit, helper service.

- `src/startup/admin_check_debug.py` — 19
- `src/startup/admin_check.py` — 6
- `src/diagnostics/system_state.py` — 5
- `src/program_settings/commands.py` — 1
- `src/winws_runtime/health/process_monitor.py` — 1
- `src/winws_runtime/health/startup_error_diagnosis.py` — 1
- `src/hosts/commands.py` — 1

## single_instance

Mutex so only one copy runs. Linux: lock file or D-Bus name.

- `src/startup/single_instance.py` — 1

## process (probe, kill, start, memory)

Process lists, kill, hidden-console flags, working-set trimming.

- `src/utils/windows_process_probe.py` — 48
- `src/zapret_update/processes.py` — 40
- `src/winws_runtime/runtime/process_probe.py` — 18
- `src/utils/process_killer.py` — 11
- `src/utils/windows_working_set.py` — 9
- `src/utils/subproc.py` — 2
- `src/presets/native_dir_watcher.py` — 1

## netprobe (network measurements)

ICMP ping, HTTP probe, DNS query, TCP info, adapter addresses through Windows DLLs (iphlpapi, dnsapi, winhttp, ws2_32).

- `src/utils/windows_http.py` — 38
- `src/utils/windows_icmp.py` — 28
- `src/windows_features/internet_cleanup_winapi.py` — 4
- `src/diagnostics/tcp_stats.py` — 3
- `src/dns/winapi.py` — 2
- `src/utils/windows_dns_query.py` — 1
- `src/utils/tcp_info.py` — 1
- `src/hosts/ipv6_detection.py` — 1

## autostart and services

Scheduled task, Windows service (NSSM, `sc.exe`), startup shortcut. Linux: systemd unit, XDG autostart.

- `src/autostart/service_api.py` — 37
- `src/autostart/nssm_service.py` — 8
- `src/utils/winapi_service_types.py` — 8
- `src/autostart/scheduled_task_api.py` — 6
- `src/dns/local_proxy_windows.py` — 5
- `src/zapret_update/share_service.py` — 3
- `src/winws_runtime/health/launch_conflicts.py` — 3

## theme_and_window (native window behaviour)

DWM title bar, DPI awareness, system theme, file drop, drag hover, idle time, popup menus, first frame, screen presence.

- `src/ui/windows_file_drop.py` — 20
- `src/ui/windows_screen_presence.py` — 16
- `src/ui/windows_system_theme.py` — 13
- `src/zapret_update/screen.py` — 9
- `src/settings/store.py` — 5
- `src/ui/user_idle.py` — 5
- `src/ui/windows_drag_hover_detector.py` — 5
- `src/config/window_metrics.py` — 4
- `src/ui/window_first_frame.py` — 4
- `src/main/window_native_commands.py` — 2
- `src/support_request_bundle.py` — 2
- `src/ui/popup_menu.py` — 1
- `src/ui/popup_menu_style.py` — 1

## tray

Notification-area icon and its menu (`tray.py` has 90 lines of this). Linux: StatusNotifierItem.

- `src/tray.py` — 90
- `src/ui/tray_menu/popup.py` — 1
- `src/ui/runtime_ui_bridge.py` — 1

## associations (default browser, tg:// link, proxy settings in the registry)

Registry reads that ask the system what to open. Linux: xdg-settings / xdg-mime.

- `src/blockcheck/browser_switch.py` — 16
- `src/blockcheck/default_browser.py` — 3
- `src/config/telegram_links.py` — 2
- `src/telegram_proxy/config/settings.py` — 2

## updater_install (replace the running program)

PowerShell watchdog and splash scripts, hand-off file, recovery. Linux: package manager or AppImage self-replace, with the helper service.

- `src/updater/install/apply_mode.py` — 23
- `src/updater/install/recovery_hook.py` — 9
- `src/updater/install/watchdog.py` — 9
- `src/zapret_update/rescue.py` — 7
- `src/updater/install/splash.py` — 5
- `src/updater/install/splash_script.py` — 4
- `src/updater/install/watchdog_script.py` — 3
- `src/updater/install/handoff.py` — 2
- `src/updater/install/__init__.py` — 1
- `src/updater/ui/restart_splash_spec.py` — 1

## engine (start and watch winws2 / WinDivert)

WinDivert driver, engine process control, exit diagnosis, system dependencies. Linux: nfqws2 + nftables through the root helper.

- `src/winws_runtime/health/windows_system_dependencies.py` — 8
- `src/winws_runtime/engine/winapi.py` — 7
- `src/winws_runtime/health/winws_exit_diagnosis.py` — 6
- `src/winws_runtime/runtime/system_ops.py` — 2
- `src/winws_runtime/health/windivert_auto_fix.py` — 1

## integrations (Windows only, become capability flags)

Defender, MAX blocker, BFE firewall service, antivirus start checks. Hidden on Linux through `caps.has(...)`.

- `src/startup/check_start.py` — 31
- `src/windows_features/max_blocker.py` — 21
- `src/startup/bfe_util.py` — 10
- `src/windows_features/defender_manager.py` — 4

## Progress (hand-maintained)

- 2026-11-15 `launch`, part 1: message boxes (`MessageBoxW`) moved behind `platforms.current().messages` (backends: `platforms/windows`, `platforms/linux` with zenity/kdialog, `platforms/stub`). Done in `main/launch_gate.py`, `main/shell.py`, `main/qt_runtime.py`. Left in the launch group: `process_start_time` (process start time), `shell.py` (admin relaunch, goes with `privileges`), `zapret_update/apply.py` (standalone updater part, goes with `updater_install`).


## Found while probing the first start on Linux (2026-11-15)

Run: the app past its launch gate, under Xvfb (a virtual screen) on CT 201, with `ctypes.windll` replaced by a fake.

1. The program refuses to start from source, on purpose, in two places: the outer gate `main/launch_gate.py` (`require_packaged_application`, install layout `_internal\Zapret.exe`, Windows version, install folder) and an inner guard `require_packaged_application()` in `main/entry.py` that stops a direct call of `main.entry.main()` (pinned by `tests/test_runtime_layout.py`). They are not duplicates. A Linux development run still needs a clear, explicit way past both, to be decided together with the Linux launch description.
2. Next wall: `main/shell.py` `shell_bootstrap` calls `ctypes.windll.shell32.ShellExecuteW` (relaunch as administrator).
3. With those skipped and `windll` faked, the main window is built and shown (`first showEvent`, about 290 ms) under Xvfb. The qfluentwidgets window does not need Windows to appear. The process then crashed with a segmentation fault; the cause is not analysed yet and may come from the fake, not from the app.
4. The source tree has no `presets/` folder (they live in the private repository / build), so the log warns "Пресеты не найдены". A Linux dev run needs them copied in or pointed to.

Probe script (not in the repository): `/tmp/zg-baseline/tools/probe_launch.py`.
