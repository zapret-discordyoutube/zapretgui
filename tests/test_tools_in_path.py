"""Запущенный VPN мешает проверке, только когда интернет на самом деле идёт через него."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from diagnostics import sections
from utils.bypass_tools import tools_in_path

TOOLS = ("sing-box", "Cloudflare WARP", "GoodbyeDPI")


class ToolsInPathTests(unittest.TestCase):
    def test_routed_vpn_puts_every_tool_in_the_path(self) -> None:
        self.assertEqual(tools_in_path(TOOLS, True), TOOLS)

    def test_idle_vpn_is_not_in_the_path_but_packet_tool_always_is(self) -> None:
        # GoodbyeDPI переделывает пакеты самого компьютера — дорога в интернет тут ни при чём.
        self.assertEqual(tools_in_path(TOOLS, False), ("GoodbyeDPI",))
        self.assertEqual(tools_in_path(("WireGuard",), False), ())

    def test_unknown_route_is_treated_as_disturbing(self) -> None:
        # Не знаем — не выдаём искажённый вывод за чистый.
        self.assertEqual(tools_in_path(TOOLS, None), TOOLS)

    def test_no_tools_nothing_in_path(self) -> None:
        self.assertEqual(tools_in_path((), True), ())


class SectionTests(unittest.TestCase):
    def test_route_reading_failure_gives_unknown(self) -> None:
        with patch.object(sections.system_state, "_read_tunnels_routed", side_effect=OSError("нет доступа")):
            self.assertIsNone(sections.vpn_routed())
        with patch.object(sections.system_state, "_read_tunnels_routed", return_value=("WireGuard",)):
            self.assertTrue(sections.vpn_routed())
        with patch.object(sections.system_state, "_read_tunnels_routed", return_value=()):
            self.assertIs(sections.vpn_routed(), False)

    def test_report_line_tells_idle_tools_from_working_ones(self) -> None:
        working = sections.tools_line(("WireGuard",), ("WireGuard",))
        idle = sections.tools_line(("WireGuard",), ())

        self.assertIn("вместе с ними", working)
        self.assertIn("идёт напрямую", idle)
        self.assertIn("сеть провайдера", idle)
        self.assertEqual(sections.tools_line((), ()), "")


class EngineTests(unittest.TestCase):
    """В отчёте — и все запущенные программы, и отдельно те, что стояли на дороге проверки."""

    def _run(self, routed) -> tuple[dict, str]:
        from test_diagnostics_verdict import _Net

        from diagnostics import engine

        net = _Net()
        net.bypass_tools = ("WireGuard",)
        lines: list[str] = []
        with patch.object(sections, "vpn_routed", return_value=routed):
            report = net.run(engine.run_blockcheck, "full", emit=lines.append)
        return report, "\n".join(lines)

    def test_idle_vpn_is_running_but_not_in_the_path(self) -> None:
        report, text = self._run(False)

        self.assertEqual((report["other_bypass_tools"], report["tools_in_path"]), (["WireGuard"], []))
        self.assertIn("интернет идёт напрямую", text)

    def test_routed_vpn_is_in_the_path(self) -> None:
        report, text = self._run(True)

        self.assertEqual(report["tools_in_path"], ["WireGuard"])
        self.assertIn("вместе с ними", text)


if __name__ == "__main__":
    unittest.main()
