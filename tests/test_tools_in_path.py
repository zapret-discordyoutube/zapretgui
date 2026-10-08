"""VPN-адаптер, прокси и программа, меняющая пакеты, мешают проверке по-разному."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from diagnostics import sections
from utils.bypass_tools import BYPASS_TOOLS, tool_kind, tools_in_path

TOOLS = ("sing-box", "Cloudflare WARP", "WireGuard", "GoodbyeDPI")


class KindTests(unittest.TestCase):
    def test_every_known_tool_has_a_kind(self) -> None:
        kinds = {title: tool_kind(title) for title in BYPASS_TOOLS.values()}

        self.assertEqual(kinds["GoodbyeDPI"], "packet")
        self.assertEqual((kinds["sing-box"], kinds["Xray"], kinds["v2rayN"], kinds["ByeDPI"]), ("proxy",) * 4)
        self.assertEqual((kinds["WireGuard"], kinds["Cloudflare WARP"], kinds["OpenVPN"], kinds["tun2socks"]), ("vpn",) * 4)


class ToolsInPathTests(unittest.TestCase):
    def test_direct_internet_leaves_only_packet_tools_in_the_path(self) -> None:
        # Живой случай: sing-box, WARP и WireGuard запущены, а интернет идёт напрямую через провайдера.
        self.assertEqual(tools_in_path(TOOLS, ()), ("GoodbyeDPI",))
        self.assertEqual(tools_in_path(("sing-box", "WireGuard"), ()), ())

    def test_routed_adapter_is_named_itself_not_every_running_program(self) -> None:
        # Через какой адаптер идёт интернет — известно точно; какие из программ при этом ни при чём — тоже.
        found = tools_in_path(TOOLS, ("wg0",))

        self.assertEqual(found, ("GoodbyeDPI", "VPN-подключение «wg0»"))

    def test_proxy_is_never_in_the_path_by_itself(self) -> None:
        # Прокси обслуживает только программы, которым назначен; проверка ходит напрямую.
        self.assertEqual(tools_in_path(("sing-box", "Xray"), ()), ())
        self.assertEqual(tools_in_path(("sing-box", "Xray"), None), ())

    def test_proxy_in_tun_mode_shows_up_as_its_adapter(self) -> None:
        self.assertEqual(tools_in_path(("sing-box",), ("sing-box-tun",)), ("VPN-подключение «sing-box-tun»",))

    def test_unknown_adapters_keep_vpn_under_suspicion(self) -> None:
        # Не знаем — не выдаём искажённый вывод за чистый.
        self.assertEqual(tools_in_path(TOOLS, None), ("GoodbyeDPI", "Cloudflare WARP", "WireGuard"))

    def test_nothing_running_nothing_in_path(self) -> None:
        self.assertEqual(tools_in_path((), ()), ())
        self.assertEqual(tools_in_path((), None), ())


class SectionTests(unittest.TestCase):
    def test_adapters_reading_failure_gives_unknown(self) -> None:
        with patch.object(sections.system_state, "_read_tunnels_routed", side_effect=OSError("нет доступа")):
            self.assertIsNone(sections.routed_adapters())
        with patch.object(sections.system_state, "_read_tunnels_routed", return_value=("wg0",)):
            self.assertEqual(sections.routed_adapters(), ("wg0",))
        with patch.object(sections.system_state, "_read_tunnels_routed", return_value=()):
            self.assertEqual(sections.routed_adapters(), ())

    def test_report_line_speaks_of_vpn_and_proxy_separately(self) -> None:
        line = sections.tools_line(("sing-box", "WireGuard"), ())

        self.assertIn("VPN запущен, но не подключён: WireGuard", line)
        self.assertIn("Работает прокси: sing-box", line)
        self.assertIn("мимо прокси", line)
        self.assertNotIn("На дороге проверки", line)

    def test_report_line_names_what_stands_in_the_path(self) -> None:
        line = sections.tools_line(("sing-box", "WireGuard"), ("VPN-подключение «wg0»",))

        self.assertIn("На дороге проверки стоит: VPN-подключение «wg0»", line)
        # Адаптер подключён — фраза «VPN не подключён» была бы неправдой.
        self.assertNotIn("не подключён", line)
        self.assertIn("Работает прокси: sing-box", line)

    def test_no_tools_no_line(self) -> None:
        self.assertEqual(sections.tools_line((), ()), "")


class EngineTests(unittest.TestCase):
    """В отчёте — и всё запущенное, и отдельно то, что стояло на дороге проверки."""

    def _run(self, adapters) -> tuple[dict, str]:
        from test_diagnostics_verdict import _Net

        from diagnostics import engine

        net = _Net()
        net.bypass_tools = ("sing-box", "WireGuard")
        lines: list[str] = []
        with patch.object(sections, "routed_adapters", return_value=adapters):
            report = net.run(engine.run_blockcheck, "full", emit=lines.append)
        return report, "\n".join(lines)

    def test_idle_vpn_and_proxy_are_running_but_not_in_the_path(self) -> None:
        report, text = self._run(())

        self.assertEqual((report["other_bypass_tools"], report["tools_in_path"]), (["sing-box", "WireGuard"], []))
        self.assertIn("VPN запущен, но не подключён", text)

    def test_routed_adapter_is_in_the_path(self) -> None:
        report, text = self._run(("wg0",))

        self.assertEqual(report["tools_in_path"], ["VPN-подключение «wg0»"])
        self.assertIn("На дороге проверки стоит", text)

    def test_adapter_without_any_known_program_is_still_reported(self) -> None:
        from test_diagnostics_verdict import _Net

        from diagnostics import engine

        net = _Net()
        lines: list[str] = []
        with patch.object(sections, "routed_adapters", return_value=("Неизвестный VPN",)):
            report = net.run(engine.run_blockcheck, "full", emit=lines.append)

        self.assertEqual(report["tools_in_path"], ["VPN-подключение «Неизвестный VPN»"])
        self.assertIn("На дороге проверки стоит", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
