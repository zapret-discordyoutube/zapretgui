"""The unit file and the polkit rule must name the same things the code does."""

from __future__ import annotations

import configparser
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
    assert main["MimeType"].rstrip(";") == "application/x-zapret-preset"
    assert "Name[ru]" not in main and "Comment[ru]" in main


def test_the_mime_type_of_the_desktop_entry_is_defined_by_the_package() -> None:
    import xml.etree.ElementTree as ET

    root = ET.parse(PACKAGING / "zapretgui-preset.xml").getroot()
    types = [element.get("type") for element in root]
    assert types == ["application/x-zapret-preset"]


def test_install_and_uninstall_put_and_remove_the_same_system_files() -> None:
    install = (PACKAGING / "install.sh").read_text(encoding="utf-8")
    uninstall = (PACKAGING / "uninstall.sh").read_text(encoding="utf-8")
    for path in (
        "/usr/lib/systemd/system/zapretgui-helper.service",
        "/usr/share/polkit-1/rules.d/50-zapretgui.rules",
        "/usr/share/applications/zapretgui.desktop",
        "/usr/share/mime/packages/zapretgui-preset.xml",
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
    assert "$home" not in before_the_switch and ".config" not in before_the_switch and ".local" not in before_the_switch
