from __future__ import annotations

import unittest
from unittest.mock import patch

from utils import bypass_tools


class BypassToolsTests(unittest.TestCase):
    def test_tools_are_recognised_by_process_name_in_any_case(self) -> None:
        names = ["explorer.exe", "XRAY.EXE", "sing-box.exe", "chrome.exe"]

        self.assertEqual(bypass_tools.bypass_tools_among(names), ("Xray", "sing-box"))

    def test_two_process_names_of_one_tool_are_named_once(self) -> None:
        self.assertEqual(bypass_tools.bypass_tools_among(["ciadpi.exe", "byedpi.exe"]), ("ByeDPI",))

    def test_ordinary_processes_and_zapret_itself_are_not_listed(self) -> None:
        self.assertEqual(bypass_tools.bypass_tools_among(["winws2.exe", "winws.exe", "svchost.exe", "", None]), ())

    def test_every_name_in_the_list_is_a_lowercase_exe(self) -> None:
        for name, title in bypass_tools.BYPASS_TOOLS.items():
            with self.subTest(name=name):
                self.assertEqual(name, name.lower())
                self.assertTrue(name.endswith(".exe"))
                self.assertTrue(title)

    def test_running_tools_come_from_process_list(self) -> None:
        records = [(4, "System"), (100, "warp-svc.exe"), (200, "notepad.exe")]
        with patch("utils.windows_process_probe.iter_process_records_winapi", return_value=records):
            self.assertEqual(bypass_tools.running_bypass_tools(), ("Cloudflare WARP",))

    def test_failed_process_list_means_nothing_known(self) -> None:
        with patch("utils.windows_process_probe.iter_process_records_winapi", side_effect=OSError("нет доступа")):
            self.assertEqual(bypass_tools.running_bypass_tools(), ())


if __name__ == "__main__":
    unittest.main()
