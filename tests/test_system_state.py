from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from diagnostics import system_state as ss

HEALTHY = ss.SystemFacts(
    is_admin=True,
    windows_build=19045,
    in_temp_folder=False,
    in_onedrive=False,
    free_mb=50_000,
    integrity=(True, (), ()),
    bfe_running=True,
    conflicts=(),
    goodbyedpi_services=(),
    bypass_tools=(),
    antivirus="",
    proxy_server="",
    proxy_script="",
    tunnels=(),
    hosts_readable=True,
    hosts_overrides=(),
    clock_skew_s=2.0,
)


def _item(facts: ss.SystemFacts, key: str) -> ss.SystemItem:
    return next(item for item in ss.judge(facts) if item.key == key)


class JudgeTests(unittest.TestCase):
    def test_healthy_system_is_all_ok_in_fixed_order(self) -> None:
        items = ss.judge(HEALTHY)

        self.assertEqual({item.level for item in items}, {ss.LEVEL_OK})
        self.assertEqual(
            [item.key for item in items],
            ["admin", "windows", "location", "disk", "files", "bfe", "conflicts", "bypass", "antivirus", "proxy", "hosts", "clock"],
        )
        self.assertEqual(ss.worst_level(items), ss.LEVEL_OK)

    def test_unknown_facts_are_unknown_not_ok(self) -> None:
        """Не удалось узнать — это не «всё в порядке»."""
        items = ss.judge(ss.SystemFacts())

        self.assertEqual({item.level for item in items}, {ss.LEVEL_UNKNOWN})
        self.assertEqual(len(items), 12)
        self.assertTrue(all(item.text == "проверить не удалось" for item in items))

    def test_things_that_stop_zapret_are_failures_with_advice(self) -> None:
        cases = {
            "admin": replace(HEALTHY, is_admin=False),
            "windows": replace(HEALTHY, windows_build=10240),
            "bfe": replace(HEALTHY, bfe_running=False),
            "files": replace(HEALTHY, integrity=(True, ("winws2.exe",), ())),
        }
        for key, facts in cases.items():
            with self.subTest(key=key):
                item = _item(facts, key)
                self.assertEqual(item.level, ss.LEVEL_FAIL)
                self.assertTrue(item.advice)

    def test_missing_and_damaged_files_are_named(self) -> None:
        item = _item(replace(HEALTHY, integrity=(True, ("winws2.exe",), ("WinDivert.dll",))), "files")

        self.assertIn("не хватает: winws2.exe", item.text)
        self.assertIn("повреждены: WinDivert.dll", item.text)
        self.assertIn("исключения антивируса", item.advice)

    def test_no_file_list_is_a_note_not_a_failure(self) -> None:
        item = _item(replace(HEALTHY, integrity=(False, (), ())), "files")

        self.assertEqual(item.level, ss.LEVEL_INFO)

    def test_interfering_programs_and_leftover_services_are_warned_together(self) -> None:
        item = _item(replace(HEALTHY, conflicts=("Process Hacker",), goodbyedpi_services=("GoodbyeDPI",)), "conflicts")

        self.assertEqual(item.level, ss.LEVEL_WARN)
        self.assertIn("Process Hacker", item.text)
        self.assertIn("служба GoodbyeDPI (GoodbyeDPI)", item.text)

    def test_other_bypass_tools_vpn_and_antivirus_are_notes_not_alarms(self) -> None:
        bypass = _item(replace(HEALTHY, bypass_tools=("Xray",), tunnels=("WireGuard Tunnel",)), "bypass")
        self.assertEqual(bypass.level, ss.LEVEL_INFO)
        self.assertIn("запущены: Xray", bypass.text)
        self.assertIn("активны VPN-подключения: WireGuard Tunnel", bypass.text)

        antivirus = _item(replace(HEALTHY, antivirus="Kaspersky"), "antivirus")
        self.assertEqual(antivirus.level, ss.LEVEL_INFO)
        self.assertIn("Kaspersky", antivirus.text)

    def test_manual_proxy_and_setup_script_are_both_warned(self) -> None:
        manual = _item(replace(HEALTHY, proxy_server="127.0.0.1:8080"), "proxy")
        self.assertEqual(manual.level, ss.LEVEL_WARN)
        self.assertIn("127.0.0.1:8080", manual.text)

        script = _item(replace(HEALTHY, proxy_script="http://wpad/wpad.dat"), "proxy")
        self.assertEqual(script.level, ss.LEVEL_WARN)
        self.assertIn("сценарий автонастройки", script.text)

    def test_location_and_disk_warnings(self) -> None:
        self.assertIn("временной папки", _item(replace(HEALTHY, in_temp_folder=True), "location").text)
        self.assertIn("OneDrive", _item(replace(HEALTHY, in_onedrive=True), "location").text)
        low = _item(replace(HEALTHY, free_mb=120), "disk")
        self.assertEqual(low.level, ss.LEVEL_WARN)
        self.assertIn("120 МБ", low.text)
        self.assertEqual(_item(replace(HEALTHY, free_mb=900), "disk").text, "свободно 900 МБ")
        self.assertEqual(_item(HEALTHY, "disk").text, "свободно 48 ГБ")

    def test_hosts_entries_are_a_note_and_unreadable_file_is_a_warning(self) -> None:
        note = _item(replace(HEALTHY, hosts_overrides=(("discord.com", "1.2.3.4"),)), "hosts")
        self.assertEqual(note.level, ss.LEVEL_INFO)
        self.assertIn("discord.com → 1.2.3.4", note.text)

        self.assertEqual(_item(replace(HEALTHY, hosts_readable=False), "hosts").level, ss.LEVEL_WARN)

    def test_clock_far_off_is_warned_with_direction_and_size(self) -> None:
        ahead = _item(replace(HEALTHY, clock_skew_s=3 * 3600), "clock")
        self.assertEqual(ahead.level, ss.LEVEL_WARN)
        self.assertIn("спешат на 3 ч", ahead.text)

        behind = _item(replace(HEALTHY, clock_skew_s=-90 * 60), "clock")
        self.assertIn("отстают на", behind.text)
        self.assertIn("могут показывать", behind.text)
        self.assertIn("отстают на 5 дн", _item(replace(HEALTHY, clock_skew_s=-5 * 86400), "clock").text)
        # Минуты расхождения сертификаты не ломают: предупреждать не о чем.
        for seconds in (-120, 301, -20 * 60):
            self.assertEqual(_item(replace(HEALTHY, clock_skew_s=seconds), "clock").level, ss.LEVEL_OK)

    def test_worst_level_puts_failure_first_and_note_below_unknown(self) -> None:
        def items(*levels):
            return [ss.SystemItem("k", "t", level, "x") for level in levels]

        self.assertEqual(ss.worst_level(items(ss.LEVEL_OK, ss.LEVEL_WARN, ss.LEVEL_FAIL)), ss.LEVEL_FAIL)
        self.assertEqual(ss.worst_level(items(ss.LEVEL_OK, ss.LEVEL_INFO, ss.LEVEL_UNKNOWN)), ss.LEVEL_UNKNOWN)
        self.assertEqual(ss.worst_level(items(ss.LEVEL_OK, ss.LEVEL_INFO)), ss.LEVEL_INFO)
        self.assertEqual(ss.worst_level([]), ss.LEVEL_OK)


class TunnelAdapterTests(unittest.TestCase):
    @staticmethod
    def _adapter(name, description, *, if_type=6, connected=True):
        return SimpleNamespace(name=name, description=description, if_type=if_type, connected=connected)

    def test_vpn_adapters_are_recognised_by_description_or_type(self) -> None:
        adapters = [
            self._adapter("Ethernet", "Intel I219-V"),
            self._adapter("wg0", "WireGuard Tunnel", if_type=53),
            self._adapter("Подключение", "WAN Miniport (PPPOE)", if_type=23),
            self._adapter("OpenVPN TAP", "TAP-Windows Adapter V9"),
        ]

        self.assertEqual(ss.tunnel_adapters(adapters), ("wg0", "Подключение", "OpenVPN TAP"))

    def test_disconnected_vpn_and_virtual_machine_adapters_are_not_vpn(self) -> None:
        adapters = [
            self._adapter("wg0", "WireGuard Tunnel", connected=False),
            self._adapter("vEthernet (WSL)", "Hyper-V Virtual Ethernet Adapter"),
            self._adapter("VMware Network Adapter VMnet8", "VMware Virtual Ethernet Adapter"),
            # Служебные туннели самой Windows.
            self._adapter("Teredo Tunneling Pseudo-Interface", "Microsoft Teredo Tunneling Adapter", if_type=131),
        ]

        self.assertEqual(ss.tunnel_adapters(adapters), ())


class CollectTests(unittest.TestCase):
    def test_failed_reader_leaves_fact_unknown_and_does_not_break_others(self) -> None:
        def broken():
            raise OSError("нет доступа")

        with (
            patch.object(ss.sys, "platform", "win32"),
            patch.object(ss, "_read_is_admin", lambda: True),
            patch.object(ss, "_read_windows_build", broken),
            patch.object(ss, "_read_in_temp", lambda: False),
            patch.object(ss, "_read_in_onedrive", lambda: False),
            patch.object(ss, "_read_free_mb", lambda: 1000),
            patch.object(ss, "_read_integrity", broken),
            patch.object(ss, "_read_bfe", lambda: True),
            patch.object(ss, "_read_conflicts", lambda: ("Process Hacker",)),
            patch.object(ss, "_read_goodbyedpi_services", lambda: ()),
            patch.object(ss, "_read_bypass_tools", lambda: ("Xray",)),
            patch.object(ss, "_read_antivirus", lambda: ""),
            patch.object(ss, "_read_proxy", broken),
            patch.object(ss, "_read_tunnels", lambda: ()),
            patch.object(ss, "_read_hosts_readable", lambda: True),
            patch.object(ss, "_read_hosts_overrides", lambda hosts: tuple((host, "1.2.3.4") for host in hosts)),
        ):
            facts = ss.collect_facts(check_hosts=("discord.com",), clock_skew=lambda: 12.0)

        self.assertTrue(facts.is_admin)
        self.assertIsNone(facts.windows_build)
        self.assertIsNone(facts.integrity)
        self.assertIsNone(facts.proxy_server)
        self.assertIsNone(facts.proxy_script)
        self.assertEqual(facts.conflicts, ("Process Hacker",))
        self.assertEqual(facts.hosts_overrides, (("discord.com", "1.2.3.4"),))
        self.assertEqual(facts.clock_skew_s, 12.0)

    def test_other_system_reads_nothing_but_the_clock(self) -> None:
        with patch.object(ss.sys, "platform", "linux"):
            facts = ss.collect_facts(clock_skew=lambda: -3.0)

        self.assertEqual(facts, ss.SystemFacts(clock_skew_s=-3.0))

    def test_broken_clock_check_is_unknown(self) -> None:
        def broken():
            raise OSError("нет сети")

        with patch.object(ss.sys, "platform", "linux"):
            self.assertIsNone(ss.collect_facts(clock_skew=broken).clock_skew_s)

    def test_module_only_reads(self) -> None:
        """Проверка не должна ничего запускать, останавливать и править."""
        import inspect

        source = inspect.getsource(ss)
        for forbidden in (
            "ensure_bfe_running",
            "start_service",
            "check_goodbyedpi(",
            "_stop_and_delete_service",
            "_disable_proxy",
            "try_kill_conflicting_processes",
            "restore_hosts_permissions",
            "SetValueEx",
            "safe_write_hosts_file",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
