# Multi-platform migration map

Generated from `src/app/windows_api_files.txt` (the fence list, see `app/architecture_checks.py`). Each Windows-touching file is assigned to the interface in `src/platforms/` it will move behind. The assignment is a first guess from the file name and its matching lines; confirm it when the interface is cut. Task file: `/root/.agent-todo/zapretgui/2026-11-15-multi-platform-linux-gui.md`.

Numbers are lines that use Windows-only calls (`windll`, `winreg`, `powershell`, `sc.exe`, `taskkill`, `schtasks`, `win32*`, `wintypes`). Some are only comments or docstrings (for example the updater's PowerShell texts); they count too, because the text is Windows-only.

| Interface | Files | Lines |
|---|---:|---:|
| launch | 2 | 11 |
| privileges | 3 | 24 |
| single_instance | 0 | 0 |
| process | 7 | 129 |
| netprobe | 8 | 78 |
| autostart and services | 7 | 69 |
| theme_and_window | 13 | 87 |
| tray | 3 | 92 |
| associations | 4 | 23 |
| updater_install | 10 | 64 |
| engine | 5 | 23 |
| integrations | 4 | 65 |
| **total** | **66** | **665** |

## launch (first start of the program)

The launch gate, message boxes before Qt exists, UAC relaunch, process start time.

- `src/main/process_start_time.py` — 10
- `src/zapret_update/apply.py` — 1

## privileges (admin / root, elevation)

`IsUserAnAdmin`, relaunch as administrator, `runas`. Linux: root check, polkit, helper service.

- `src/startup/admin_check_debug.py` — 19
- `src/diagnostics/system_state.py` — 4
- `src/hosts/commands.py` — 1

## single_instance

Mutex so only one copy runs. Linux: lock file or D-Bus name.


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
- `src/utils/winapi_service_types.py` — 8
- `src/autostart/nssm_service.py` — 7
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
- `src/winws_runtime/health/winws_exit_diagnosis.py` — 5
- `src/winws_runtime/runtime/system_ops.py` — 2
- `src/winws_runtime/health/windivert_auto_fix.py` — 1

## integrations (Windows only, become capability flags)

Defender, MAX blocker, BFE firewall service, antivirus start checks. Hidden on Linux through `caps.has(...)`.

- `src/startup/check_start.py` — 31
- `src/windows_features/max_blocker.py` — 21
- `src/startup/bfe_util.py` — 10
- `src/windows_features/defender_manager.py` — 3

## Progress (hand-maintained)

- 2026-11-15 `launch`, part 1: message boxes (`MessageBoxW`) moved behind `platforms.current().messages` (backends: `platforms/windows`, `platforms/linux` with zenity/kdialog, `platforms/stub`). Done in `main/launch_gate.py`, `main/shell.py`, `main/qt_runtime.py`. Left in the launch group: `process_start_time` (process start time), `shell.py` (admin relaunch, goes with `privileges`), `zapret_update/apply.py` (standalone updater part, goes with `updater_install`).
- 2026-11-15 `privileges` and `single_instance`: `platforms.current().privileges` (`is_elevated`, `relaunch_elevated`, `gui_needs_elevation`: true on Windows, false on Linux because a root helper service does the privileged work) and `platforms.current().single_instance` (Windows: mutex + event; Linux: flock lock file + Unix socket for "show the window"). `startup/admin_check.py` (its two restart helpers had no callers) and `startup/single_instance.py` are deleted. `IsUserAnAdmin` call sites in `diagnostics/system_state`, `program_settings/commands`, `winws_runtime/health/*`, `windows_features/defender_manager` and `autostart/nssm_service` now ask the platform. Left in the privileges group: `startup/admin_check_debug.py` (a Windows-only diagnostic, goes with `diagnostics`) and `hosts/commands.py` (`runas notepad` to edit the hosts file; goes with the Linux hosts-editor work in phase 4).


## Found while probing the first start on Linux (2026-11-15)

Run: the app past its launch gate, under Xvfb (a virtual screen) on CT 201, with `ctypes.windll` replaced by a fake.

1. The program refuses to start from source. Two separate checks do it: `main/launch_gate.py` (`require_packaged_application`, install layout `_internal\\Zapret.exe`, Windows version, install folder) and a second `require_packaged_application()` in `main/entry.py`. A platform layer needs one launch description, not two copies.
2. Next wall: `main/shell.py` `shell_bootstrap` calls `ctypes.windll.shell32.ShellExecuteW` (relaunch as administrator).
3. With those skipped and `windll` faked, the main window is built and shown (`first showEvent`, about 290 ms) under Xvfb. The qfluentwidgets window does not need Windows to appear. The process then crashed with a segmentation fault; the cause is not analysed yet and may come from the fake, not from the app.
4. The source tree has no `presets/` folder (they live in the private repository / build), so the log warns "Пресеты не найдены". A Linux dev run needs them copied in or pointed to.

Probe script (not in the repository): `/tmp/zg-baseline/tools/probe_launch.py`.

- 2026-11-15 `services` and capabilities: `platforms.current().services` (`is_running`, `start`; Windows: pywin32 loaded lazily; Linux: systemd) and `Platform.has(Cap.X)`. Capabilities so far: `BFE`, `SELF_UPDATE`, `WINDOWS_STARTUP_CHECKS` (all Windows only). `startup/bfe_util.py` has no Windows calls left. The startup update check and the Windows startup advisories are not installed on systems without the capability.

- 2026-11-15 `ZAPRET_DEV_ROOT` (the "paths" step, part 1): outside Windows, and only when the variable names an existing folder, the program may start from source and treats that folder as its install root (`config/runtime_layout.py`: `resolve_dev_root`, `is_dev_root_run`). Without the variable the source start is refused exactly as before. `scripts/prepare_dev_root.py` builds such a folder from the repositories (strategy catalogs, built-in presets, icons, managed lists, and from `private_zapretgui`: Lua, engine blobs, templates, catalog databases). The real install layout for Linux (`/opt/zapretgui` + XDG folders) is still to do.

  Run from source on Linux:
  ```
  python3 scripts/prepare_dev_root.py /tmp/zapret-dev-root
  ZAPRET_DEV_ROOT=/tmp/zapret-dev-root PYTHONPATH=src python3 src/main.py
  ```
  (`QT_QPA_PLATFORM=offscreen` for no screen; the virtual X screen `xvfb-run` still ends in a Qt segfault, see below.)

## Second probe, after `launch` / `privileges` / `single_instance` (2026-11-15)

Run: skip the two source-start checks, start with Qt `offscreen`, grab the main window after 9 s, no fake `windll`. Probe script (not in the repository): `/tmp/zg-baseline/tools/probe_shot.py`. Screenshot of the result: `/root/.agent-todo/zapretgui/2026-11-15-linux-gui-first-screenshot.png`.

- The app now starts on Linux with nothing faked: `shell_bootstrap` passes through the platform (no elevation asked, Linux lock file taken, the "show window" socket created and its watcher thread running).
- The real main window (`LupiDPIApp`, 1280x720) is built: sidebar, pages, version badge "Zapret2 v21.1.7.231 FREE".
- Under a virtual X screen (Xvfb, `xcb` platform) the process still ends with a segmentation fault inside Qt's event loop; under `offscreen` it stays alive. Not analysed yet (needs a native backtrace, or a real desktop); the fault is not caused by the old fake `windll`.
- The window shows these error toasts. Each is a concrete Phase 2 task and belongs to a group above:
  1. "Пресеты не найдены" (x4): the source tree has no `presets/winws2` folder. The built layout provides it. A Linux layout needs its own place for resources (`paths` interface).
  2. `module 'ctypes' has no attribute 'windll'` from `updater/install/watchdog.py:199` ("не удалось запросить права для наблюдателя"): the update watcher asks Windows for rights. Group `updater_install`.
  3. "Обновление не установлено: Не удалось запустить установщик": the update flow (`updater/download/flow.py:251`, `updater/install/repair.py:195`) assumes a Windows installer. Group `updater_install`.
  4. `No module named 'win32service'` from `startup/bfe_util.py:5`: unconditional `import win32service` (pywin32 does not exist on Linux). Group `integrations`.

## Third probe, after `services` and the capability gates (2026-11-15)

Only the four "Пресеты не найдены" toasts are left (same cause: the dev tree has no `presets/winws2*` folders; they come from the build layout). The main page also still offers Windows-only things on Linux, for example the quick action "Сбросить сеть Windows"; hiding them is capability work for Phase 2. Diagnostics still shows a BFE row ("не удалось определить") on Linux; it should be hidden, not unknown.

## Fourth probe, with `ZAPRET_DEV_ROOT` and the real launch gate (2026-11-15)

The program starts through the real gate (no patches) and shows the first-run welcome tour. The four "Пресеты не найдены" toasts are gone. Two toasts remain: "ФАЙЛ ИЛИ ПАПКА НЕ НАЙДЕНЫ" ("Ошибка запуска Zapret"), expected because there is no `winws2` engine binary on Linux yet (phase 3). Screenshot: `/root/.agent-todo/zapretgui/2026-11-15-linux-gui-with-dev-root.png`.
