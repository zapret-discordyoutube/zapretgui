"""The unit file and the polkit rule must name the same things the code does."""

from __future__ import annotations

import configparser
import re
import subprocess

import pytest
from pathlib import Path

from platforms.linux import capture_rules
from platforms.linux.helper import policy, protocol

PACKAGING = Path(__file__).resolve().parents[1] / "packaging" / "linux"


def _unit() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment]
    parser.read_string((PACKAGING / "systemd" / "zapretgui-helper.service").read_text(encoding="utf-8"))
    return parser


def test_the_unit_removes_the_same_table_the_code_creates() -> None:
    text = (PACKAGING / "systemd" / "zapretgui-helper.service").read_text(encoding="utf-8")
    command = f"nft delete table inet {capture_rules.TABLE_NAME}"
    # "-" = a failure is ignored; 2>/dev/null = the harmless "no such table" is not printed into the journal as an error
    assert f"ExecStartPre=-/bin/sh -c '/usr/sbin/{command} 2>/dev/null'" in text
    assert f"ExecStopPost=-/bin/sh -c '/usr/sbin/{command} 2>/dev/null'" in text


def test_the_unit_starts_the_helper_under_the_runtime_folder_of_the_socket() -> None:
    service = _unit()["Service"]
    assert service["ExecStart"] == "/opt/zapretgui/bin/zapretgui-helper"
    assert service["RuntimeDirectory"] == Path(protocol.SOCKET_PATH).parent.name
    assert service["Restart"] == "on-failure"
    assert "multi-user.target" in _unit()["Install"]["WantedBy"]


def test_the_unit_gives_the_helper_only_what_firewall_work_needs() -> None:
    caps = _unit()["Service"]["CapabilityBoundingSet"].split()
    assert {"CAP_NET_ADMIN", "CAP_NET_RAW", "CAP_SETUID", "CAP_SETGID"} <= set(caps)
    # The engine runs as the person who asked: stopping it means signalling another user's process (found on a real
    # desktop: without CAP_KILL "stop" removed the rules but left nfqws2 running).
    assert "CAP_KILL" in caps
    assert "CAP_SYS_MODULE" not in caps and "CAP_SYS_PTRACE" not in caps
    assert _unit()["Service"]["ProtectSystem"] == "strict"


def test_the_polkit_rule_names_one_unit_and_the_helper_group() -> None:
    text = (PACKAGING / "polkit" / "50-zapretgui.rules").read_text(encoding="utf-8")
    assert 'action.lookup("unit") == "zapretgui-helper.service"' in text
    assert f'subject.isInGroup("{protocol.SOCKET_GROUP}")' in text
    assert "org.freedesktop.systemd1.manage-units" in text and text.count("polkit.Result.YES") == 1


def test_the_engine_path_in_the_code_is_where_the_package_puts_the_binary() -> None:
    assert str(policy.ENGINE_PATH) == "/opt/zapretgui/bin/nfqws2"
    assert str(policy.PROGRAM_ROOT) == "/opt/zapretgui"


def test_the_desktop_entry_is_valid_and_points_at_the_installed_program() -> None:
    entry = configparser.ConfigParser(strict=False, interpolation=None)
    entry.optionxform = str  # type: ignore[assignment]
    entry.read_string((PACKAGING / "zapretgui.desktop").read_text(encoding="utf-8"))
    main = entry["Desktop Entry"]
    assert main["Type"] == "Application" and main["Terminal"] == "false"
    assert main["Exec"].split()[0] == "/opt/zapretgui/bin/zapretgui"
    assert main["Icon"] == "zapretgui" and "Network" in main["Categories"]
    # presets are plain .txt/.zip files and no file type is claimed: a MIME type here would hijack text files
    assert "MimeType" not in main and "%U" not in main["Exec"]
    assert "Name[ru]" not in main and "Comment[ru]" in main


def test_install_and_uninstall_put_and_remove_the_same_system_files() -> None:
    install = (PACKAGING / "install.sh").read_text(encoding="utf-8")
    uninstall = (PACKAGING / "uninstall.sh").read_text(encoding="utf-8")
    initsys = (PACKAGING / "initsys.sh").read_text(encoding="utf-8")
    for path in (
        "/usr/lib/systemd/system/zapretgui-helper.service",
        "/usr/share/polkit-1/rules.d/50-zapretgui.rules",
        "/usr/share/applications/zapretgui.desktop",
    ):
        assert path in install or path in initsys, path  # the service files are put by initsys.sh, found by the init system
        assert path in uninstall, path
    assert "zg_service_install" in install and "zg_service_start" in install and "zg_service_remove" in uninstall
    # the icon theme tree (every size and the scalable one) goes in with one copy and comes out by name
    assert "/usr/share/icons/hicolor/" in install and "find /usr/share/icons/hicolor -name 'zapretgui.*'" in uninstall
    assert "/opt/zapretgui" in install and "rm -rf /opt/zapretgui" in uninstall
    assert "systemctl enable --now zapretgui-helper.service" in initsys
    assert f"nft delete table inet {capture_rules.TABLE_NAME}" in uninstall
    assert "groupadd --system zapretgui" in install and "groupdel zapretgui" in uninstall


def test_the_scripts_are_executable_and_valid_shell() -> None:
    import os
    import subprocess

    for name in ("install.sh", "uninstall.sh"):
        path = PACKAGING / name
        assert os.access(path, os.X_OK), name
        done = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr


def test_uninstall_keeps_the_users_data_unless_asked() -> None:
    uninstall = (PACKAGING / "uninstall.sh").read_text(encoding="utf-8")
    assert "--purge-user-data" in uninstall
    purge = uninstall.split("--purge-user-data", 1)[1]
    for folder in (".config/zapretgui", ".local/share/zapretgui", ".local/state/zapretgui", ".cache/zapretgui", ".config/autostart/zapretgui.desktop"):
        assert folder in purge
    before_the_switch = uninstall.split('if [ "${1:-}" = "--purge-user-data" ]')[0]
    before_the_switch = "\n".join(line for line in before_the_switch.splitlines() if not line.lstrip().startswith("#"))
    assert "$home" not in before_the_switch and ".config" not in before_the_switch and ".local" not in before_the_switch


def test_the_start_wrappers_run_the_bundled_program_in_utf8_mode() -> None:
    import os
    import subprocess

    window = PACKAGING / "bin" / "zapretgui"
    helper = PACKAGING / "bin" / "zapretgui-helper"
    desktop = (PACKAGING / "zapretgui.desktop").read_text(encoding="utf-8")
    assert f"Exec={'/opt/zapretgui/bin/' + window.name}\n" in desktop
    for path in (window, helper):
        assert os.access(path, os.X_OK), path.name
        text = path.read_text(encoding="utf-8")
        assert "PYTHONUTF8=1" in text and "LC_CTYPE=C.UTF-8" in text and "exec /opt/zapretgui/_internal/zapretgui" in text
        assert subprocess.run(["sh", "-n", str(path)], capture_output=True, text=True).returncode == 0
    assert "--zapret-helper" in helper.read_text(encoding="utf-8")
    assert "--zapret-helper" not in window.read_text(encoding="utf-8")


def test_main_switches_into_the_helper_role_before_any_window_code(monkeypatch) -> None:
    import runpy
    import sys

    import pytest

    from platforms.linux.helper import server

    monkeypatch.setattr(server, "main", lambda: 7)
    monkeypatch.setattr(sys, "argv", ["zapretgui", "--zapret-helper"])
    main_py = Path(__file__).resolve().parents[1] / "src" / "main.py"
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(main_py), run_name="__main__")
    assert stopped.value.code == 7


def test_the_deb_files_are_valid_and_match_the_installer() -> None:
    import os
    import subprocess

    deb = PACKAGING / "deb"
    control = (deb / "control.in").read_text(encoding="utf-8")
    assert "Package: zapretgui" in control and "@VERSION@" in control and "@SIZE@" in control
    # the engine is linked against these libraries: build_nfqws2.sh prints the same list
    for library in ("nftables", "libluajit-5.1-2", "libnetfilter-queue1", "libnfnetlink0", "libmnl0", "zlib1g"):
        assert library in control
    for name in ("postinst", "prerm", "postrm"):
        assert os.access(deb / name, os.X_OK), name
        assert subprocess.run(["sh", "-n", str(deb / name)], capture_output=True, text=True).returncode == 0
    postinst = (deb / "postinst").read_text(encoding="utf-8")
    prerm = (deb / "prerm").read_text(encoding="utf-8")
    postrm = (deb / "postrm").read_text(encoding="utf-8")
    assert "groupadd --system zapretgui" in postinst and "enable zapretgui-helper.service" in postinst
    assert f"nft delete table inet {capture_rules.TABLE_NAME}" in prerm
    assert "groupdel zapretgui" in postrm and "purge" in postrm
    # the people's own data is never deleted by a package script
    code = "\n".join(line for line in postrm.splitlines() if not line.lstrip().startswith("#"))
    assert ".local/share" not in code and ".config" not in code


def test_install_leaves_the_uninstaller_with_the_program_and_knows_pkexec() -> None:
    install = (PACKAGING / "install.sh").read_text(encoding="utf-8")
    assert '"$TARGET/uninstall.sh"' in install
    assert "PKEXEC_UID" in install and "VERSION" in install.split("for item in", 1)[1].split(";", 1)[0]


def test_the_appimage_launcher_installs_the_system_part_and_starts_the_installed_program() -> None:
    import os
    import subprocess

    apprun = PACKAGING / "appimage" / "AppRun"
    text = apprun.read_text(encoding="utf-8")
    assert os.access(apprun, os.X_OK)
    assert subprocess.run(["sh", "-n", str(apprun)], capture_output=True, text=True).returncode == 0
    # root cannot read the AppImage's own mount: files are copied out first, and the copy is removed explicitly
    assert "mktemp -d /var/tmp/zapretgui-install" in text and 'rm -rf "$tmp"' in text and "trap" not in text.split("\n", 1)[1].replace("# not a trap", "")
    assert "pkexec" in text and "sudo" in text and "--install-system-part" in text
    assert text.rstrip().endswith('start_window "$PREFIX/bin/zapretgui" "$@"')
    # a fresh install adds the person to the group; the window then starts inside it at once (no re-login needed)
    assert 'start_window sg zapretgui -c "exec $PREFIX/bin/zapretgui"' in text and 'Log out and in once' in text
    # the version the launcher compares is the one the bundle script writes
    assert "VERSION" in text


def test_the_unit_lets_the_helper_write_exactly_the_hosts_file_and_its_backup() -> None:
    service = _unit()["Service"]
    assert service["ReadWritePaths"] == "-/etc/hosts"
    assert service["StateDirectory"] == "zapretgui"
    from platforms.linux.helper import system_ops

    assert system_ops.HOSTS_PATH == "/etc/hosts"
    assert system_ops.HOSTS_BACKUP.startswith("/var/lib/zapretgui/")


def _hosts_backup_block(script: Path, tmp_path: Path) -> str:
    text = script.read_text(encoding="utf-8")
    start = text.index("if [ -f /var/lib/zapretgui/hosts.original ]")
    end = re.compile(r"^\s*fi$", re.MULTILINE).search(text, start).end()  # the first "fi" after the start closes our if
    block = text[start:end]
    return block.replace("/var/lib/zapretgui", str(tmp_path / "var")).replace("/etc/hosts", str(tmp_path / "hosts"))


@pytest.mark.parametrize("script", [PACKAGING / "deb" / "postrm", PACKAGING / "uninstall.sh"], ids=["postrm", "uninstall"])
def test_the_hosts_backup_goes_with_the_program_unless_hosts_still_differs_from_it(script, tmp_path) -> None:
    block = _hosts_backup_block(script, tmp_path)
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "hosts.original").write_text("127.0.0.1 localhost\n")
    (tmp_path / "hosts").write_text("127.0.0.1 localhost\n")
    assert subprocess.run(["sh", "-c", block]).returncode == 0
    assert not (tmp_path / "var").exists()
    # an edited /etc/hosts: the backup is the only way back, so it stays
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "hosts.original").write_text("127.0.0.1 localhost\n")
    (tmp_path / "hosts").write_text("127.0.0.1 localhost\n1.2.3.4 x.example\n")
    assert subprocess.run(["sh", "-c", block]).returncode == 0
    assert (tmp_path / "var" / "hosts.original").exists()


def test_the_window_wrapper_puts_a_desktop_shortcut_once_per_user(tmp_path) -> None:
    import os
    import shutil
    import subprocess

    wrapper = (PACKAGING / "bin" / "zapretgui").read_text(encoding="utf-8")
    entry = PACKAGING / "zapretgui.desktop"
    script = tmp_path / "wrapper.sh"
    # only the shortcut function runs: the real program is not started, the menu entry is the one of the repository
    script.write_text(wrapper.replace("/usr/share/applications/zapretgui.desktop", str(entry)).replace("exec /opt/zapretgui/_internal/zapretgui", "true"), encoding="utf-8")
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    env = {**os.environ, "HOME": str(home), "DISPLAY": ":0", "XDG_STATE_HOME": str(home / "state")}
    env.pop("WAYLAND_DISPLAY", None)
    shortcut = home / "Desktop" / "zapretgui.desktop"
    subprocess.run(["sh", str(script)], env=env, check=True)
    assert shortcut.is_file() and os.access(shortcut, os.X_OK)
    # the same entry; only its icon line may differ (it names the picture by path when the theme icon is installed)
    strip_icon = lambda text: [line for line in text.splitlines() if not line.startswith("Icon=")]  # noqa: E731
    assert strip_icon(shortcut.read_text(encoding="utf-8")) == strip_icon(entry.read_text(encoding="utf-8"))
    shortcut.unlink()
    subprocess.run(["sh", str(script)], env=env, check=True)
    assert not shortcut.exists(), "a shortcut the user deleted must not come back"
    # no graphical session: nothing is created and nothing is remembered
    shutil.rmtree(home / "state")
    env.pop("DISPLAY")
    subprocess.run(["sh", str(script)], env=env, check=True)
    assert not shortcut.exists() and not (home / "state").exists()


def test_the_deb_recommends_the_package_that_has_pkexec() -> None:
    """Debian 13 split polkit: ``polkitd`` is the daemon, ``pkexec`` the binary. The AppImage installer and the in-program
    update both run ``pkexec``; a desktop with only ``polkitd`` (the KDE test machine) answered "pkexec is not installed"."""
    control = (PACKAGING / "deb" / "control.in").read_text(encoding="utf-8")
    recommends = next(line for line in control.splitlines() if line.startswith("Recommends:"))
    assert "pkexec" in recommends and "polkitd" in recommends
    # an old system without the split still resolves through the transitional name
    assert recommends.count("policykit-1") == 2


SCRIPTS = ("install.sh", "uninstall.sh", "appimage/AppRun", "bin/zapretgui", "bin/zapretgui-helper", "deb/postinst", "deb/postrm", "deb/prerm")


def test_the_packaging_scripts_are_executable_in_git() -> None:
    """A release is built from ``git archive`` of the commit, not from a working tree. Dev 296 was built that way with the
    scripts stored as 100644: the AppImage carried a non-executable install.sh, and on every desktop its first start died
    (``sudo: ...install.sh: command not found``, pkexec the same)."""
    listing = subprocess.run(["git", "ls-files", "-s", "--", "packaging/linux"], cwd=PACKAGING.parents[1], capture_output=True, text=True)
    if listing.returncode != 0:
        pytest.skip("not a git checkout")
    modes = {line.split("\t")[1].removeprefix("packaging/linux/"): line.split()[0] for line in listing.stdout.splitlines()}
    assert [name for name in SCRIPTS if modes.get(name) != "100755"] == []


def test_the_appimage_start_makes_its_installer_copy_executable_whatever_the_build_did() -> None:
    text = (PACKAGING / "appimage" / "AppRun").read_text(encoding="utf-8")
    copy = text.index('cp -a "$ROOT"/. "$tmp"/')
    assert 'chmod 0755 "$tmp/install.sh"' in text[copy : copy + 400]


def test_a_message_of_the_appimage_start_reaches_a_desktop_without_dialog_tools() -> None:
    """Double-clicked, the AppImage has no terminal: a message printed only to stderr is "nothing happens" for the person."""
    text = (PACKAGING / "appimage" / "AppRun").read_text(encoding="utf-8")
    say = text[text.index("say() {") : text.index("system_part_ready()")]
    order = [say.index(tool) for tool in ("zenity", "kdialog", "notify-send", "xmessage", "x-terminal-emulator")]
    assert order == sorted(order)
    assert subprocess.run(["sh", "-n", str(PACKAGING / "appimage" / "AppRun")]).returncode == 0


def _run_appimage_start(tmp_path, *, pkexec_exit: int | None, terminal: bool):
    """Runs the real AppRun as a non-root person on a "desktop" (DISPLAY set, no terminal) with stub system tools."""
    import os
    import shutil
    import stat
    import subprocess

    app = tmp_path / "app"
    root = app / "zapretgui-root"
    root.mkdir(parents=True)
    shutil.copy2(PACKAGING / "appimage" / "AppRun", app / "AppRun")
    (root / "VERSION").write_text("21.1.7.999\n", encoding="utf-8")
    log = tmp_path / "calls.log"
    (root / "install.sh").write_text(f'#!/bin/sh\necho "install $*" >> "{log}"\n', encoding="utf-8")
    stubs = tmp_path / "stubs"
    stubs.mkdir()

    def stub(name: str, body: str) -> None:
        path = stubs / name
        path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    for quiet in ("zenity", "kdialog", "notify-send", "xmessage"):
        stub(quiet, f'echo "{quiet} $*" >> "{log}"')
    stub("id", 'case "$1" in -u) echo 1000;; -un) echo tester;; -nG) echo tester;; *) exec /usr/bin/id "$@";; esac')
    stub("sudo", f'echo "sudo" >> "{log}"; exec "$@"')
    if pkexec_exit is not None:
        stub("pkexec", f'echo "pkexec" >> "{log}"; if [ {pkexec_exit} = 0 ]; then exec "$@"; fi; exit {pkexec_exit}')
    if terminal:
        stub("x-terminal-emulator", f'echo "terminal" >> "{log}"; shift; exec "$@" < /dev/null')
    else:
        # a terminal program that is there but cannot open a window (no display server behind DISPLAY)
        for name in ("x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal", "mate-terminal", "lxterminal", "xterm"):
            stub(name, "exit 1")
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "DISPLAY": ":99", "HOME": str(tmp_path)}
    done = subprocess.run(["/bin/sh", str(app / "AppRun")], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done.returncode, calls


def test_the_appimage_asks_for_the_password_in_a_terminal_when_pkexec_is_missing(tmp_path) -> None:
    """Debian 13 ships pkexec as its own package; a double-clicked AppImage has no terminal for a plain sudo."""
    code, calls = _run_appimage_start(tmp_path, pkexec_exit=None, terminal=True)
    assert "terminal" in calls and "sudo" in calls and any(call.startswith("install --user tester") for call in calls)


def test_the_appimage_falls_back_to_the_terminal_when_pkexec_has_no_password_agent(tmp_path) -> None:
    code, calls = _run_appimage_start(tmp_path, pkexec_exit=127, terminal=True)
    assert calls.index("pkexec") < calls.index("terminal") and any(call.startswith("install --user tester") for call in calls)


def test_a_dismissed_password_dialog_is_final_and_a_working_pkexec_needs_no_terminal(tmp_path) -> None:
    code, calls = _run_appimage_start(tmp_path / "no", pkexec_exit=126, terminal=True)
    assert code == 1 and "terminal" not in calls and not any(call.startswith("install") for call in calls)
    code, calls = _run_appimage_start(tmp_path / "yes", pkexec_exit=0, terminal=True)
    assert "pkexec" in calls and "terminal" not in calls and sum(call.startswith("install") for call in calls) == 1


def test_without_any_way_to_ask_the_appimage_says_what_to_run(tmp_path) -> None:
    code, calls = _run_appimage_start(tmp_path, pkexec_exit=None, terminal=False)
    said = "\n".join(calls)
    assert code == 1 and "zenity" in said and "not installed" in said and "sudo" in said and "--install-system-part" in said


def test_the_appimage_start_leaves_a_log_file_the_person_can_send(tmp_path) -> None:
    """Report 2026-11-21 (Dev 300): nothing visible happened; without a terminal there was no way to see why."""
    _run_appimage_start(tmp_path / "ok", pkexec_exit=0, terminal=True)
    text = (tmp_path / "ok" / ".cache" / "zapretgui" / "appimage-start.log").read_text(encoding="utf-8")
    assert "=== AppRun start" in text and "tools found:" in text and "desktop: DISPLAY=:99" in text
    assert "running the installer through pkexec" in text and "pkexec finished with code 0" in text
    _run_appimage_start(tmp_path / "no", pkexec_exit=126, terminal=True)
    refused = (tmp_path / "no" / ".cache" / "zapretgui" / "appimage-start.log").read_text(encoding="utf-8")
    assert "pkexec finished with code 126" in refused and "message to the person: The system part was not installed" in refused
    assert "appimage-start.log" in refused  # the message to the person names the file


def test_the_window_wrapper_starts_inside_the_new_group_without_a_new_login(tmp_path) -> None:
    """Report 2026-11-22: after the first install the window opened from the menu, but every engine start said
    "You are not in the zapretgui group" until the next login. The wrapper re-runs the window through sg, arguments intact."""
    import subprocess

    wrapper = tmp_path / "zapretgui"
    program = tmp_path / "program"
    wrapper.write_text(
        (PACKAGING / "bin" / "zapretgui").read_text(encoding="utf-8").replace("/opt/zapretgui/_internal/zapretgui", str(program)),
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    program.write_text('#!/bin/sh\necho "group=${ZAPRETGUI_IN_GROUP:-no}"; for a in "$@"; do echo "arg=[$a]"; done\n', encoding="utf-8")
    program.chmod(0o755)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "sg").write_text('#!/bin/sh\necho "via sg $1"; shift 2; exec /bin/sh -c "$1"\n', encoding="utf-8")
    (stubs / "id").write_text('#!/bin/sh\ncase "$1" in -un) echo tester;; -nG) echo "tester";; *) exec /usr/bin/id "$@";; esac\n', encoding="utf-8")
    (stubs / "getent").write_text('#!/bin/sh\necho "zapretgui:x:983:other,tester"\n', encoding="utf-8")
    for stub in stubs.iterdir():
        stub.chmod(0o755)
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path), "XDG_STATE_HOME": str(tmp_path / "state")}
    done = subprocess.run([str(wrapper), "it's a file.json", "two words"], env=env, capture_output=True, text=True, timeout=30)
    lines = done.stdout.splitlines()
    assert lines == ["via sg zapretgui", "group=1", "arg=[it's a file.json]", "arg=[two words]"], done
    # already in the group (this session has it): no sg, no loop
    (stubs / "id").write_text('#!/bin/sh\ncase "$1" in -un) echo tester;; -nG) echo "tester zapretgui";; *) exec /usr/bin/id "$@";; esac\n', encoding="utf-8")
    done = subprocess.run([str(wrapper), "x"], env=env, capture_output=True, text=True, timeout=30)
    assert done.stdout.splitlines() == ["group=no", "arg=[x]"], done


def test_a_window_without_a_graphical_session_is_explained_as_such(tmp_path) -> None:
    import subprocess

    text = (PACKAGING / "appimage" / "AppRun").read_text(encoding="utf-8")
    assert "There is no graphical session in this terminal" in text


def _deb_depends() -> list[str]:
    control = (PACKAGING / "deb" / "control.in").read_text(encoding="utf-8")
    line = next(row for row in control.splitlines() if row.startswith("Depends:"))
    return [item.strip() for item in line.removeprefix("Depends:").split(",") if item.strip()]


def test_the_appimage_installer_brings_the_same_libraries_the_deb_depends_on() -> None:
    """The AppImage has no package manager behind it: install.sh must install what the .deb would pull in."""
    import re

    text = (PACKAGING / "install.sh").read_text(encoding="utf-8")
    listed = re.search(r'^DEPENDENCIES="([^"]+)"', text, re.M)
    assert listed is not None
    assert sorted(listed.group(1).split()) == sorted(name for name in _deb_depends() if name != "systemd" and "(" not in name)
    # the window-system libraries whose absence on a lean desktop made "nothing happens"
    for needed in ("libxcb-icccm4", "libxcb-keysyms1", "libxcb-render-util0", "libxcb-cursor0", "libxkbcommon-x11-0", "libglib2.0-0t64", "libwayland-cursor0"):
        assert needed in _deb_depends()
    assert "/var/log/zapretgui-install.log" in text


def _install_functions() -> str:
    import re

    text = (PACKAGING / "install.sh").read_text(encoding="utf-8")
    parts = [re.search(rf"^{name}\(\) \{{.*?^\}}", text, re.M | re.S).group(0) for name in ("package_manager", "install_dependencies")]
    return "\n".join(parts) + "\n"


def _run_install_dependencies(tmp_path, manager: str, stubs: dict, header: str):
    import stat
    import subprocess

    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in stubs.items():
        (bindir / name).write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        (bindir / name).chmod((bindir / name).stat().st_mode | stat.S_IXUSR)
    script = header + "\n" + _install_functions() + "install_dependencies\n"
    return subprocess.run(
        ["bash", "-c", script],
        env={"PATH": f"{bindir}:/usr/bin:/bin", "ZG_PKG": manager},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_install_dependencies_installs_only_what_is_missing(tmp_path) -> None:
    log = tmp_path / "apt.log"
    stubs = {
        "dpkg": 'case "$2" in libcap2|zlib1g) exit 0;; *) exit 1;; esac',
        "apt-get": f'echo "apt-get $*" >> "{log}"',
    }
    header = 'DEPENDENCIES="zlib1g libxcb-icccm4 libcap2 libxcb-keysyms1"'
    done = _run_install_dependencies(tmp_path, "apt", stubs, header)
    assert done.returncode == 0, done.stderr
    assert log.read_text(encoding="utf-8").splitlines() == ["apt-get install -y --no-install-recommends libxcb-icccm4 libxcb-keysyms1"]
    # nothing missing: apt-get is not even called
    log.unlink()
    stubs["dpkg"] = "exit 0"
    _run_install_dependencies(tmp_path, "apt", stubs, header)
    assert not log.exists()


def test_install_dependencies_uses_pacman_on_arch(tmp_path) -> None:
    log = tmp_path / "pacman.log"
    stubs = {
        "pacman": f'case "$1" in -Qq) case "$2" in glibc|zlib) exit 0;; *) exit 1;; esac;; *) echo "pacman $*" >> "{log}";; esac',
        "apt-get": f'echo "WRONG apt-get $*" >> "{log}"',
    }
    done = _run_install_dependencies(tmp_path, "pacman", stubs, 'ARCH_PACKAGES="glibc zlib libcap nftables"')
    assert done.returncode == 0, done.stderr
    # only the missing names; --needed and no -y/-u (a partial upgrade is how Arch breaks)
    assert log.read_text(encoding="utf-8").splitlines() == ["pacman -S --noconfirm --needed libcap nftables"]


def test_install_dependencies_uses_dnf_on_fedora(tmp_path) -> None:
    log = tmp_path / "dnf.log"
    stubs = {
        "rpm": 'case "$2" in glibc) exit 0;; *) exit 1;; esac',
        "dnf": f'echo "dnf $*" >> "{log}"',
    }
    done = _run_install_dependencies(tmp_path, "dnf", stubs, 'FEDORA_PACKAGES="glibc libcap nftables"')
    assert done.returncode == 0, done.stderr
    assert log.read_text(encoding="utf-8").splitlines() == ["dnf install -y --setopt=strict=0 libcap nftables"]


def test_install_dependencies_uses_xbps_on_void(tmp_path) -> None:
    log = tmp_path / "xbps.log"
    stubs = {
        "xbps-query": 'case "$1" in zlib|libcap) exit 0;; *) exit 1;; esac',
        "xbps-install": f'echo "xbps-install $*" >> "{log}"',
    }
    done = _run_install_dependencies(tmp_path, "xbps", stubs, 'XBPS_PACKAGES="zlib LuaJIT libcap nftables"')
    assert done.returncode == 0, done.stderr
    assert log.read_text(encoding="utf-8").splitlines() == ["xbps-install -y LuaJIT nftables"]


def test_package_lists_of_arch_and_fedora_are_not_empty() -> None:
    """Three lists describe the same needs (deb, Arch, Fedora): a library added to one is checked against the others."""
    import re

    text = (PACKAGING / "install.sh").read_text(encoding="utf-8")

    def words(name: str) -> list:
        return re.search(rf'^{name}="([^"]+)"', text, re.M | re.S).group(1).split()

    for name in ("ARCH_PACKAGES", "FEDORA_PACKAGES", "XBPS_PACKAGES"):
        assert len(words(name)) >= 15, name
        assert "nftables" in words(name) and "luajit" in {word.lower() for word in words(name)}, name


def test_arch_and_fedora_packages_repackage_the_deb() -> None:
    pkgbuild = (PACKAGING / "arch" / "PKGBUILD").read_text(encoding="utf-8")
    assert "pkgname=zapretgui-bin" in pkgbuild
    assert "sysusers" in pkgbuild  # the group zapretgui is created by the package
    install_script = (PACKAGING / "arch" / "zapretgui.install").read_text(encoding="utf-8")
    assert "post_install" in install_script and "pre_remove" in install_script
    spec = (PACKAGING / "rpm" / "zapretgui.spec").read_text(encoding="utf-8")
    assert "groupadd" in spec and "%pre" in spec
    assert "ar x" in spec  # the .deb is unpacked, so the two formats carry the same files


def test_a_window_that_dies_at_start_is_explained_instead_of_nothing_happening(tmp_path) -> None:
    """The report: password window, then nothing. A missing Qt library made the window exit at once, unseen without a terminal."""
    import os
    import stat
    import subprocess

    app = tmp_path / "app"
    (app / "zapretgui-root").mkdir(parents=True)
    import shutil

    shutil.copy2(PACKAGING / "appimage" / "AppRun", app / "AppRun")
    (app / "zapretgui-root" / "VERSION").write_text("1\n", encoding="utf-8")
    prefix = tmp_path / "opt"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "VERSION").write_text("1\n", encoding="utf-8")
    program = prefix / "bin" / "zapretgui"
    program.write_text('#!/bin/sh\necho "qt.qpa.plugin: Could not load the Qt platform plugin xcb" >&2\nexit 134\n', encoding="utf-8")
    program.chmod(program.stat().st_mode | stat.S_IXUSR)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "calls.log"
    for name, body in {
        "zenity": f'echo "zenity $*" >> "{log}"',
        "getent": 'echo "zapretgui:x:999:tester"',
        "systemctl": "exit 0",
        "id": 'case "$1" in -u) echo 1000;; -un) echo tester;; -nG) echo "tester zapretgui";; *) exec /usr/bin/id "$@";; esac',
    }.items():
        (stubs / name).write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        (stubs / name).chmod(0o755)
    import socket

    helper_socket = tmp_path / "helper.sock"  # "the helper is running" is its socket, under any init system
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(helper_socket))
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "DISPLAY": ":99", "HOME": str(tmp_path), "ZAPRETGUI_PREFIX": str(prefix), "ZAPRETGUI_SOCKET": str(helper_socket)}
    done = subprocess.run(["/bin/sh", str(app / "AppRun")], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    listener.close()
    assert done.returncode == 134
    said = log.read_text(encoding="utf-8")
    assert "did not start" in said and "Could not load the Qt platform plugin xcb" in said
    journal = (tmp_path / ".cache" / "zapretgui" / "appimage-start.log").read_text(encoding="utf-8")
    assert "exited after" in journal and "code 134" in journal and "Could not load the Qt platform plugin xcb" in journal


def _run_appimage_with_glibc(tmp_path, glibc: str):
    import shutil
    import stat

    app = tmp_path / "app"
    (app / "zapretgui-root").mkdir(parents=True)
    shutil.copy2(PACKAGING / "appimage" / "AppRun", app / "AppRun")
    (app / "zapretgui-root" / "VERSION").write_text("21.1.7.999\n", encoding="utf-8")
    log = tmp_path / "calls.log"
    (app / "zapretgui-root" / "install.sh").write_text(f'#!/bin/sh\necho install >> "{log}"\n', encoding="utf-8")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("getconf", f"echo glibc {glibc}"), ("zenity", f'echo "zenity $*" >> "{log}"'), ("pkexec", f'echo pkexec >> "{log}"')):
        path = stubs / name
        path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "DISPLAY": ":99", "HOME": str(tmp_path)}
    done = subprocess.run(["/bin/sh", str(app / "AppRun")], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    return done, (log.read_text(encoding="utf-8").splitlines() if log.exists() else [])


def test_the_deb_refuses_a_system_with_an_older_c_library_than_the_program_needs() -> None:
    """Debian 12 (glibc 2.36) accepted the .deb, installed it, and the program then died with "GLIBC_2.38 not found"."""
    assert "libc6 (>= 2.38)" in _deb_depends()


def test_the_appimage_says_at_once_that_the_system_is_too_old_and_installs_nothing(tmp_path) -> None:
    done, calls = _run_appimage_with_glibc(tmp_path, "2.36")
    assert done.returncode == 1 and "2.36" in done.stderr and "2.38" in done.stderr
    assert any(call.startswith("zenity") for call in calls) and "install" not in calls and "pkexec" not in calls
    # a new enough C library (Ubuntu 24.04 has 2.39) goes on to the normal first start (root runs install.sh, a person pkexec)
    done, calls = _run_appimage_with_glibc(tmp_path / "ok", "2.39")
    assert "needs a newer system" not in done.stderr and ("install" in calls or "pkexec" in calls)


def test_the_glibc_minimum_of_the_appimage_is_the_one_the_deb_depends_on() -> None:
    minimum = re.search(r"^MIN_GLIBC=([0-9.]+)", (PACKAGING / "appimage" / "AppRun").read_text(encoding="utf-8"), re.M)
    assert minimum is not None and f"libc6 (>= {minimum.group(1)})" in _deb_depends()


# --- the init system is found by itself (systemd, OpenRC, sysvinit) ------------------------------------------------------

def _initsys(tmp_path, script: str, *, init: str | None, stubs: dict[str, str] | None = None):
    """Runs a piece of shell with initsys.sh loaded, the init scripts and the init folder redirected into tmp_path."""
    import os
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    calls = tmp_path / "calls.log"
    for name, body in {
        "systemctl": 'echo "systemctl $*" >> "$CALLS"',
        "rc-service": 'echo "rc-service $*" >> "$CALLS"',
        "rc-update": 'echo "rc-update $*" >> "$CALLS"',
        "update-rc.d": 'echo "update-rc.d $*" >> "$CALLS"',
        "sv": 'echo "sv $*" >> "$CALLS"',
        **(stubs or {}),
    }.items():
        (bin_dir / name).write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin", "CALLS": str(calls), "ZG_SRC": str(PACKAGING),
        "ZG_INIT_DIR": str(tmp_path / "init.d"), "ZG_UNIT_DIR": str(tmp_path / "units"), "ZG_PREFIX": str(tmp_path / "no-program"),
        "ZG_SV_DIR": str(tmp_path / "sv"), "ZG_SERVICE_DIR": str(tmp_path / "service"), "ZG_WAIT_SECONDS": "0",
    }
    (tmp_path / "service").mkdir(parents=True, exist_ok=True)
    if init:
        env["ZG_INIT"] = init
    done = subprocess.run(["sh", "-c", f'. "{PACKAGING}/initsys.sh"; {script}'], env=env, capture_output=True, text=True, timeout=60)
    return done, (calls.read_text(encoding="utf-8").splitlines() if calls.exists() else [])


def test_each_init_system_gets_its_own_service_and_commands(tmp_path) -> None:
    # systemd: the unit is copied, enabled and started by systemctl
    done, calls = _initsys(tmp_path / "a", "zg_service_install; zg_service_start", init="systemd")
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "a" / "units" / "zapretgui-helper.service").is_file()
    assert "systemctl enable --now zapretgui-helper.service" in calls
    # OpenRC: an openrc-run script in /etc/init.d, added to the default runlevel, started by rc-service
    done, calls = _initsys(tmp_path / "b", "zg_service_install; zg_service_start", init="openrc")
    script = tmp_path / "b" / "init.d" / "zapretgui-helper"
    assert done.returncode == 0, done.stderr
    assert script.read_text(encoding="utf-8").startswith("#!/sbin/openrc-run") and script.stat().st_mode & 0o111
    assert "rc-update add zapretgui-helper default" in calls and "rc-service zapretgui-helper restart" in calls
    assert not (tmp_path / "b" / "units").exists()
    # sysvinit: an LSB script, registered by update-rc.d
    done, calls = _initsys(tmp_path / "c", "zg_service_install; zg_service_start", init="sysv", stubs={})
    script = tmp_path / "c" / "init.d" / "zapretgui-helper"
    assert "### BEGIN INIT INFO" in script.read_text(encoding="utf-8") and script.stat().st_mode & 0o111
    assert "update-rc.d zapretgui-helper defaults" in calls
    # nothing known: no file is made, the person is told what to run
    done, calls = _initsys(tmp_path / "d", "zg_service_install; zg_service_start; zg_start_hint", init="none")
    assert done.returncode == 0 and "start the helper by hand" in done.stdout and not (tmp_path / "d" / "init.d").exists()
    # removing takes an init script away and leaves it out of the runlevels
    done, calls = _initsys(tmp_path / "e", "zg_service_install; zg_service_remove", init="openrc")
    assert not (tmp_path / "e" / "init.d" / "zapretgui-helper").exists() and "rc-update del zapretgui-helper default" in calls


def test_runit_gets_a_service_folder_linked_into_the_folder_runsvdir_watches(tmp_path) -> None:
    """Void Linux: /etc/sv/zapretgui-helper (run + finish); the link in /var/service is what enables and starts it."""
    done, calls = _initsys(tmp_path, "zg_service_install; zg_service_start", init="runit")
    assert done.returncode == 0, done.stderr
    service = tmp_path / "sv" / "zapretgui-helper"
    assert service.joinpath("run").read_text(encoding="utf-8").startswith("#!/bin/sh") and service.joinpath("run").stat().st_mode & 0o111
    assert service.joinpath("finish").stat().st_mode & 0o111
    link = tmp_path / "service" / "zapretgui-helper"
    assert link.is_symlink() and link.resolve() == service.resolve()
    assert not (tmp_path / "init.d").exists() and not (tmp_path / "units").exists()
    # a second install (an update) restarts the running service instead of linking again
    done, calls = _initsys(tmp_path, "zg_service_install; zg_service_start", init="runit")
    assert done.returncode == 0 and any(call.startswith("sv restart") and call.endswith("zapretgui-helper") for call in calls)
    # stopping with "disable" takes the link away, so the next boot does not start it; removing drops the folder too
    done, calls = _initsys(tmp_path, "zg_service_stop disable", init="runit")
    assert not link.exists() and not link.is_symlink() and any(call.startswith("sv stop") for call in calls)
    done, _ = _initsys(tmp_path, "zg_service_remove", init="runit")
    assert not service.exists()
    done, _ = _initsys(tmp_path / "hint", "zg_start_hint", init="runit")
    assert "sv start zapretgui-helper" in done.stdout


def test_a_runit_machine_is_recognised_by_the_name_of_process_1_in_both_languages(monkeypatch) -> None:
    import sys

    sys.path.insert(0, str(PACKAGING.parents[1] / "src"))
    from platforms.linux import init_system

    text = (PACKAGING / "initsys.sh").read_text(encoding="utf-8")
    assert "runit|runit-init) echo runit" in text  # Void's process 1 is called "runit"
    monkeypatch.delenv("ZG_INIT", raising=False)
    monkeypatch.setattr(init_system.os.path, "isdir", lambda path: False)
    monkeypatch.setattr(init_system, "_read", lambda path: "runit" if path == "/proc/1/comm" else "")
    assert init_system.detect() == "runit"
    assert init_system.start_command() == "sudo sv start zapretgui-helper"
    assert init_system.status_command() == "sudo sv status zapretgui-helper"
    monkeypatch.setattr(init_system, "_read", lambda path: "s6-svscan" if path == "/proc/1/comm" else "")
    assert init_system.detect() == "none"


def test_the_runit_scripts_are_shell_that_parses_and_ship_with_the_program() -> None:
    import subprocess

    for name in ("zapretgui-helper.runit", "zapretgui-helper.runit-finish"):
        path = PACKAGING / "init" / name
        assert subprocess.run(["sh", "-n", str(path)], capture_output=True).returncode == 0, name
        assert path.read_text(encoding="utf-8").startswith("#!/bin/sh")
    run = (PACKAGING / "init" / "zapretgui-helper.runit").read_text(encoding="utf-8")
    assert "exec \"$PREFIX/bin/zapretgui-helper\"" in run  # runsv supervises the helper itself, not a wrapper that forks


def test_the_init_system_is_chosen_without_being_told(tmp_path) -> None:
    """Detection reads the machine: /run/systemd/system, the name of process 1, OpenRC's state, then start-stop-daemon."""
    text = (PACKAGING / "initsys.sh").read_text(encoding="utf-8")
    assert "/run/systemd/system" in text and "/proc/1/comm" in text and "/run/openrc/softlevel" in text and "start-stop-daemon" in text
    done, _ = _initsys(tmp_path, "zg_init_kind", init=None)
    assert done.stdout.strip() in {"systemd", "openrc", "sysv", "runit", "none"}
    for kind, hint in (("systemd", "systemctl start"), ("openrc", "rc-service zapretgui-helper start"), ("sysv", "service zapretgui-helper start"), ("none", "zapretgui-helper")):
        done, _ = _initsys(tmp_path / kind, "zg_start_hint", init=kind)
        assert hint in done.stdout


def test_the_python_side_names_the_same_commands_as_the_shell_side(monkeypatch) -> None:
    import sys

    sys.path.insert(0, str(PACKAGING.parents[1] / "src"))
    from platforms.linux import init_system

    for kind, start in (("systemd", "systemctl start zapretgui-helper"), ("openrc", "rc-service zapretgui-helper start"), ("sysv", "service zapretgui-helper start")):
        monkeypatch.setenv("ZG_INIT", kind)
        assert init_system.detect() == kind and init_system.start_command().endswith(start)
    monkeypatch.setenv("ZG_INIT", "none")
    assert init_system.start_command() == "sudo /opt/zapretgui/bin/zapretgui-helper" and init_system.enable_command() == ""
    from platforms.linux.helper import client

    monkeypatch.setenv("ZG_INIT", "openrc")
    assert "rc-service zapretgui-helper start" in client.unavailable_hint() and "rc-update add" in client.unavailable_hint()


def test_the_deb_no_longer_demands_systemd_and_carries_the_init_scripts() -> None:
    control = (PACKAGING / "deb" / "control.in").read_text(encoding="utf-8")
    depends = next(row for row in control.splitlines() if row.startswith("Depends:"))
    assert "systemd" not in depends.replace("libsystemd", "")  # Devuan has no systemd package installed
    postinst = (PACKAGING / "deb" / "postinst").read_text(encoding="utf-8")
    prerm = (PACKAGING / "deb" / "prerm").read_text(encoding="utf-8")
    assert "initsys.sh" in postinst and "zg_service_start" in postinst and "zg_service_remove" in prerm
    for name in ("zapretgui-helper.sysv", "zapretgui-helper.openrc"):
        assert (PACKAGING / "init" / name).is_file()


def test_the_sysvinit_script_really_starts_reports_and_stops_a_daemon(tmp_path) -> None:
    """Runs the LSB script with the real start-stop-daemon and a stand-in helper that makes its socket, as the real one does."""
    import shutil
    import subprocess

    if not shutil.which("start-stop-daemon"):
        import pytest

        pytest.skip("start-stop-daemon is not installed here")
    prefix = tmp_path / "opt"
    (prefix / "bin").mkdir(parents=True)
    socket_path = tmp_path / "run" / "helper.sock"
    daemon = prefix / "bin" / "zapretgui-helper"
    daemon.write_text(
        "#!/bin/sh\necho started-by-test\n"
        f'exec python3 -c "import socket,time,os;s=socket.socket(socket.AF_UNIX);s.bind(\\"{socket_path}\\");time.sleep(300)"\n',
        encoding="utf-8",
    )
    daemon.chmod(0o755)
    env = {
        "PATH": "/usr/sbin:/sbin:/usr/bin:/bin", "ZAPRETGUI_PREFIX": str(prefix), "ZAPRETGUI_PIDFILE": str(tmp_path / "h.pid"),
        "ZAPRETGUI_LOGFILE": str(tmp_path / "h.log"), "ZAPRETGUI_SOCKET": str(socket_path),
    }
    script = str(PACKAGING / "init" / "zapretgui-helper.sysv")

    def run(action: str):
        return subprocess.run(["sh", script, action], env=env, capture_output=True, text=True, timeout=60)

    try:
        assert run("status").returncode == 3
        started = run("start")
        assert started.returncode == 0, started.stderr
        assert socket_path.exists() and run("status").returncode == 0
        assert "started-by-test" in (tmp_path / "h.log").read_text(encoding="utf-8")  # its output goes to the log, there is no journal
        assert run("start").returncode == 0  # a second start does not start a second helper
        assert run("restart").returncode == 0 and socket_path.exists()
        assert run("stop").returncode == 0
        assert run("status").returncode == 3 and not socket_path.exists() and not (tmp_path / "h.pid").exists()
    finally:
        run("stop")


def test_install_sh_ignores_the_umask_of_the_person_who_ran_sudo() -> None:
    """The report: `umask 077` in .bashrc made /opt/zapretgui rwx------ and the engine (it runs as the person) died with code 127."""
    text = (PACKAGING / "install.sh").read_text(encoding="utf-8")
    assert "\numask 022\n" in text
    assert text.index("\numask 022\n") < text.index('mkdir "$TARGET.new"')  # before the first folder is made
    assert 'chmod -R go+rX,go-w "$TARGET"' in text  # and the result is open whatever the source files were


def test_install_sh_really_leaves_an_open_folder_under_umask_077(tmp_path) -> None:
    import subprocess

    target = tmp_path / "opt"
    script = (
        "umask 077\numask 022\n"  # the same two lines as install.sh: the person's umask, then ours
        f'mkdir -p "{target}.new/bin" && : > "{target}.new/bin/x" && mv "{target}.new" "{target}"\n'
        f'chmod -R go+rX,go-w "{target}"; chmod 755 "{target}"\n'
        f'stat -c %a "{target}" "{target}/bin" "{target}/bin/x"'
    )
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=20)
    assert done.stdout.split() == ["755", "755", "644"], done.stdout


def test_install_sh_makes_root_the_owner_of_the_program_folder() -> None:
    """The report (ls of /opt/zapretgui): the folders were owned by the person, copied from the AppImage with cp -a.
    The helper runs that program as root, so the folder must belong to root before anything is started."""
    text = (PACKAGING / "install.sh").read_text(encoding="utf-8")
    assert 'chown -R root:root "$TARGET"' in text
    assert text.index('chown -R root:root "$TARGET"') > text.index('mv "$TARGET.new" "$TARGET"')  # after the copy, before the service starts
    assert text.index('chown -R root:root "$TARGET"') < text.index("zg_service_install")


def test_missing_icon_folder_does_not_stop_the_install_before_the_service() -> None:
    """Found in a Void chroot: /usr/share/icons/hicolor did not exist, cp failed, set -e ended the script before the helper service."""
    text = (PACKAGING / "install.sh").read_text(encoding="utf-8")
    icons = text.index('cp -a "$TARGET/system/hicolor/."')
    assert "install -d /usr/share/icons/hicolor" in text[icons - 120 : icons]
    assert "|| echo" in text[icons : icons + 160]  # not fatal
    assert icons < text.index("zg_service_install")
