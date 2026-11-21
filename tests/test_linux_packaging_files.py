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
        "/usr/share/icons/hicolor/256x256/apps/zapretgui.png",
    ):
        assert path in install, path
        assert path in uninstall, path
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
    assert text.rstrip().endswith('exec /opt/zapretgui/bin/zapretgui "$@"')
    # a fresh install adds the person to the group; the window then starts inside it at once (no re-login needed)
    assert 'exec sg zapretgui -c "exec /opt/zapretgui/bin/zapretgui"' in text and 'Log out and in once' in text
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
    assert shortcut.read_text(encoding="utf-8") == entry.read_text(encoding="utf-8")
    shortcut.unlink()
    subprocess.run(["sh", str(script)], env=env, check=True)
    assert not shortcut.exists(), "a shortcut the user deleted must not come back"
    # no graphical session: nothing is created and nothing is remembered
    shutil.rmtree(home / "state")
    env.pop("DISPLAY")
    subprocess.run(["sh", str(script)], env=env, check=True)
    assert not shortcut.exists() and not (home / "state").exists()
