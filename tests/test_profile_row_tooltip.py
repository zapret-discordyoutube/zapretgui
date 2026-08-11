from __future__ import annotations

import unittest
from types import SimpleNamespace


def _item(
    *,
    lines: tuple[str, ...],
    list_type: str = "",
    strategy_name: str = "Стратегия не выбрана",
    in_preset: bool = True,
    enabled: bool = True,
):
    return SimpleNamespace(
        key="k",
        persistent_key="sig:k",
        display_name="youtube.com (интерфейс)",
        strategy_id="none" if strategy_name == "Стратегия не выбрана" else "s1",
        strategy_name=strategy_name,
        match_lines=lines,
        list_type=list_type,
        rating="",
        favorite=False,
        in_preset=in_preset,
        enabled=enabled,
        group="youtube",
        group_name="YouTube",
        order=0,
    )


class ProfileRowTooltipTests(unittest.TestCase):
    def test_tcp_hostlist_tooltip_keeps_summary_and_adds_explanations(self) -> None:
        from profile.list_view_state import match_summary, profile_row_tooltip

        item = _item(
            lines=("--filter-tcp=80,443",),
            list_type="hostlist",
            strategy_name="multisplit seqovl",
        )
        tooltip = profile_row_tooltip(item)
        tooltip_lines = tooltip.split("\n")

        # Краткая техническая строка остаётся первой — её просили не убирать.
        self.assertEqual(tooltip_lines[0], match_summary(item))
        self.assertEqual(tooltip_lines[0], "TCP • TCP 80,443 • hostlist")
        self.assertIn("TCP — обычные соединения с сайтами и сервисами (TLS/HTTP).", tooltip_lines)
        self.assertTrue(any(line.startswith("Hostlist — обход действует только для доменов") for line in tooltip_lines))
        self.assertIn("Стратегия обхода: multisplit seqovl.", tooltip_lines)

    def test_udp_ipset_tooltip_explains_udp_and_ipset(self) -> None:
        from profile.list_view_state import profile_row_tooltip

        tooltip = profile_row_tooltip(_item(lines=("--filter-udp=443",), list_type="ipset"))

        self.assertIn("UDP — трафик без установления соединения", tooltip)
        self.assertIn("IPset — обход действует по IP-адресам из списка", tooltip)

    def test_voice_tooltip_explains_voice_traffic(self) -> None:
        from profile.list_view_state import profile_row_tooltip

        tooltip = profile_row_tooltip(_item(lines=("--filter-l7=stun",)))

        self.assertIn("Voice — голосовой трафик", tooltip)

    def test_unknown_list_type_adds_no_false_claims(self) -> None:
        from profile.list_view_state import profile_row_tooltip

        tooltip = profile_row_tooltip(_item(lines=("--filter-tcp=443",), list_type=""))

        self.assertNotIn("Hostlist", tooltip)
        self.assertNotIn("IPset", tooltip)

    def test_placeholder_strategy_name_is_not_shown(self) -> None:
        from profile.list_view_state import profile_row_tooltip

        tooltip = profile_row_tooltip(_item(lines=("--filter-tcp=443",)))

        self.assertNotIn("Стратегия обхода", tooltip)
        self.assertNotIn("Стратегия не выбрана", tooltip)

    def test_row_for_profile_keeps_status_lines_after_explanations(self) -> None:
        from profile.list_view_state import row_for_profile

        row = row_for_profile(_item(lines=("--filter-tcp=443",), list_type="hostlist", in_preset=False))

        self.assertIn("Hostlist — обход действует только", row["tooltip"])
        self.assertIn("Профиля ещё нет в пресете", row["tooltip"])

        row = row_for_profile(_item(lines=("--filter-tcp=443",), enabled=False))
        self.assertIn("сейчас выключен", row["tooltip"])


if __name__ == "__main__":
    unittest.main()
