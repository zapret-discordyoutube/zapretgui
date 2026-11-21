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
    assert f"ExecStartPre=-/usr/sbin/{command}" in text and f"ExecStopPost=-/usr/sbin/{command}" in text


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
    for path in (
        "/usr/lib/systemd/system/zapretgui-helper.service",
        "/usr/share/polkit-1/rules.d/50-zapretgui.rules",
        "/usr/share/applications/zapretgui.desktop",
    ):
        assert path in install, path
        assert path in uninstall, path
    # the icon theme tree (every size and the scalable one) goes in with one copy and comes out by name
    assert "/usr/share/icons/hicolor/" in install and "find /usr/share/icons/hicolor -name 'zapretgui.*'" in uninstall
    assert "/opt/zapretgui" in install and "rm -rf /opt/zapretgui" in uninstall
    assert "systemctl enable --now zapretgui-helper.service" in install
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
    for needed in ("libxcb-icccm4", "libxcb-keysyms1", "libxcb-render-util0", "libxcb-cursor0", "libxkbcommon-x11-0"):
        assert needed in _deb_depends()
    assert "/var/log/zapretgui-install.log" in text


def test_install_dependencies_installs_only_what_is_missing(tmp_path) -> None:
    import re
    import stat
    import subprocess

    text = (PACKAGING / "install.sh").read_text(encoding="utf-8")
    function = re.search(r"^install_dependencies\(\) \{.*?^\}", text, re.M | re.S).group(0)
    stubs = tmp_path / "bin"
    stubs.mkdir()
    log = tmp_path / "apt.log"

    def stub(name: str, body: str) -> None:
        (stubs / name).write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        (stubs / name).chmod((stubs / name).stat().st_mode | stat.S_IXUSR)

    stub("dpkg", 'case "$2" in libcap2|zlib1g) exit 0;; *) exit 1;; esac')
    stub("apt-get", f'echo "apt-get $*" >> "{log}"')
    script = 'DEPENDENCIES="zlib1g libxcb-icccm4 libcap2 libxcb-keysyms1"\n' + function + "\ninstall_dependencies\n"
    done = subprocess.run(["bash", "-c", script], env={"PATH": f"{stubs}:/usr/bin:/bin"}, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls == ["apt-get install -y --no-install-recommends libxcb-icccm4 libxcb-keysyms1"]
    # nothing missing: apt-get is not even called
    log.unlink()
    stub("dpkg", "exit 0")
    subprocess.run(["bash", "-c", script], env={"PATH": f"{stubs}:/usr/bin:/bin"}, capture_output=True, text=True, timeout=30)
    assert not log.exists()


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
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "DISPLAY": ":99", "HOME": str(tmp_path), "ZAPRETGUI_PREFIX": str(prefix)}
    done = subprocess.run(["/bin/sh", str(app / "AppRun")], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert done.returncode == 134
    said = log.read_text(encoding="utf-8")
    assert "did not start" in said and "Could not load the Qt platform plugin xcb" in said


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
