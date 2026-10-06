from __future__ import annotations

import unittest

from profile.list_interpreter import build_profile_list_sources
from profile.parser import parse_preset_text


class RenamedStockProfileNameTests(unittest.TestCase):
    """Пресет пользователя, сохранённый до переименования стоковых профилей.

    В его тексте остаётся прежнее имя (`--name=googlevideo.com (CDN сервера)`):
    менять чужой текст программа не вправе. В интерфейсе такой профиль
    показывается под нынешним именем.
    """

    _OLD_PRESET = """
--name=googlevideo.com (CDN сервера)
--filter-tcp=443
--hostlist=lists/googlevideo.txt
--lua-desync=fake

--new

--name=Мой собственный профиль
--filter-tcp=443
--hostlist=lists/other.txt
--lua-desync=fake
"""

    def _templates(self, text: str) -> dict:
        parsed = parse_preset_text(text, engine="winws2")
        return {f"t{position}": profile for position, profile in enumerate(parsed.profiles)}

    def test_old_stock_name_is_shown_as_the_current_one(self) -> None:
        preset = parse_preset_text(self._OLD_PRESET, engine="winws2")

        sources = build_profile_list_sources(tuple(preset.profiles), {})

        self.assertEqual(
            [source.resolved_display_name for source in sources],
            ["YouTube · видео (googlevideo.com)", "Мой собственный профиль"],
        )
        # Текст пресета не тронут: в самом профиле имя прежнее.
        self.assertEqual(sources[0].profile.name, "googlevideo.com (CDN сервера)")

    def test_old_named_profile_is_the_same_row_as_the_renamed_template(self) -> None:
        # Списки в профиле пользователя уже другие, поэтому совпасть с шаблоном
        # он может только по имени.
        preset = parse_preset_text(
            """
--name=googlevideo.com (CDN сервера)
--filter-tcp=80,443,8443
--hostlist=lists/my-googlevideo.txt
--lua-desync=fake
""",
            engine="winws2",
        )
        templates = self._templates(
            """
--name=YouTube · видео (googlevideo.com)
--filter-tcp=443
--hostlist=lists/googlevideo.txt
"""
        )

        sources = build_profile_list_sources(tuple(preset.profiles), templates)

        self.assertEqual(len(sources), 1)
        self.assertTrue(sources[0].in_preset)
        self.assertEqual(sources[0].resolved_display_name, "YouTube · видео (googlevideo.com)")

    def test_every_old_name_maps_to_a_service_role_name(self) -> None:
        from profile.stock_names import RENAMED_STOCK_PROFILE_NAMES, current_stock_profile_name

        self.assertEqual(len(RENAMED_STOCK_PROFILE_NAMES), 21)
        for old, new in RENAMED_STOCK_PROFILE_NAMES.items():
            with self.subTest(old=old):
                self.assertIn(" · ", new)
                self.assertEqual(current_stock_profile_name(old), new)
                self.assertEqual(current_stock_profile_name(old.upper()), new)
                # Нынешнее имя само ни во что не превращается.
                self.assertEqual(current_stock_profile_name(new), new)
        self.assertEqual(current_stock_profile_name("Telegram"), "Telegram")
        self.assertEqual(current_stock_profile_name(""), "")

    def test_row_hint_says_how_the_profile_is_written_in_the_preset(self) -> None:
        from profile.list_view_state import row_for_profile
        from profile.state import ProfileListItem

        item = ProfileListItem(
            key="p0",
            persistent_key="p0",
            profile_index=0,
            display_name="YouTube · видео (googlevideo.com)",
            enabled=True,
            in_preset=True,
            strategy_id="pass",
            strategy_name="pass",
            match_lines=("--filter-tcp=443", "--hostlist=lists/googlevideo.txt"),
            list_type="hostlist",
            rating="",
            favorite=False,
            group="youtube",
            group_name="YouTube",
            order=0,
            profile_name="googlevideo.com (CDN сервера)",
        )

        row = row_for_profile(item)

        self.assertEqual(row["tile_name"], "Видео (googlevideo.com)")
        self.assertIn("В тексте пресета профиль записан как «googlevideo.com (CDN сервера)».", row["tooltip"])


class ProfileListInterpreterTests(unittest.TestCase):
    def test_preset_profiles_with_same_match_are_kept_as_separate_rows(self) -> None:
        preset = parse_preset_text(
            """
--name=tr.rbxcdn.com
--filter-tcp=443-65535
--hostlist=lists/tr-rbxcdn-com.txt
--lua-desync=hostfakesplit

--new

--name=tr.rbxcdn.com копия 2
--filter-tcp=443-65535
--hostlist=lists/tr-rbxcdn-com.txt
--lua-desync=hostfakesplit

--new

--name=tr.rbxcdn.com копия
--filter-tcp=443-65535
--hostlist=lists/tr-rbxcdn-com.txt
--lua-desync=hostfakesplit
""",
            engine="winws2",
        )

        sources = build_profile_list_sources(tuple(preset.profiles), {})

        self.assertEqual(
            [source.profile.name for source in sources],
            ["tr.rbxcdn.com", "tr.rbxcdn.com копия 2", "tr.rbxcdn.com копия"],
        )


if __name__ == "__main__":
    unittest.main()
