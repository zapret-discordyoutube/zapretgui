"""Имена профилей записаны как «Сервис · роль», а плитка прячет повтор сервиса.

В пресете профиль называется полностью: «YouTube · видео (googlevideo.com)».
Под шапкой группы «YouTube» строка показывает только роль — «Видео
(googlevideo.com)»: что это YouTube, уже сказала шапка.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from folders.defaults import build_default_profile_folders, classify_profile_folder
from profile.list_view_state import build_profile_list_view_state, profile_name_inside_group
from profile.state import ProfileListItem

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALL_PROFILES_PATH = PROJECT_ROOT / "private_zapretgui" / "resources" / "profile" / "templates" / "all_profiles.txt"
BUILTIN_DIR = Path(__file__).resolve().parents[1] / "src" / "presets" / "builtin"

# Прежние имена: во встроенных пресетах и шаблонах их больше быть не должно.
OLD_NAMES = (
    "googlevideo.com (CDN сервера)",
    "youtube.com (интерфейс)",
    "i.ytimg.com (превью роликов)",
    "youtube.com (QUIC)",
    "youtube.com (RTMPS Россия)",
    "discord.com",
    "updates.discord.com",
    "discord.media (voice RTC)",
    "discord (images)",
    "Discord UDP (обычно не нужно)",
    "GitHub",
    "githubusercontent.com",
    "WhatsApp",
    "static.whatsapp.net",
    "WhatsApp UDP wide",
    "Roblox TCP",
    "Roblox UDP",
    "js.rbxcdn.com",
    "css.rbxcdn.com",
    "tr.rbxcdn.com",
    "Twitter Images (twimg.com)",
)


def _item(name: str, *, key: str, group: str, group_name: str, match: str) -> ProfileListItem:
    return ProfileListItem(
        key=key,
        persistent_key=key,
        profile_index=0,
        display_name=name,
        enabled=True,
        in_preset=True,
        strategy_id="pass",
        strategy_name="pass",
        match_lines=("--filter-tcp=443", match),
        list_type="hostlist",
        rating="",
        favorite=False,
        group=group,
        group_name=group_name,
        order=0,
        profile_name=name,
    )


class ProfileNameInsideGroupTests(unittest.TestCase):
    def test_group_title_is_not_repeated_in_the_row(self) -> None:
        self.assertEqual(
            profile_name_inside_group("YouTube · видео (googlevideo.com)", "YouTube"),
            "Видео (googlevideo.com)",
        )
        self.assertEqual(profile_name_inside_group("Discord · UDP (обычно не нужно)", "Discord"), "UDP (обычно не нужно)")
        self.assertEqual(profile_name_inside_group("youtube · сайт и приложение", "YouTube"), "Сайт и приложение")

    def test_name_stays_whole_outside_its_own_group(self) -> None:
        # Профиль перенесли в другую папку или папку переименовали.
        self.assertEqual(
            profile_name_inside_group("Twitter/X · картинки (twimg.com)", "Соцсети"),
            "Twitter/X · картинки (twimg.com)",
        )
        self.assertEqual(profile_name_inside_group("YouTube · видео", "Мои сайты"), "YouTube · видео")
        self.assertEqual(profile_name_inside_group("YouTube · видео", ""), "YouTube · видео")

    def test_plain_names_are_not_cut(self) -> None:
        self.assertEqual(profile_name_inside_group("YouTube", "YouTube"), "YouTube")
        self.assertEqual(profile_name_inside_group("YouTube Music", "YouTube"), "YouTube Music")
        self.assertEqual(profile_name_inside_group("YouTube · ", "YouTube"), "YouTube ·")
        self.assertEqual(profile_name_inside_group("", "YouTube"), "")


class ProfileTileRowNameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _state(self):
        items = (
            _item(
                "YouTube · видео (googlevideo.com)",
                key="yt-0",
                group="youtube",
                group_name="YouTube",
                match="--hostlist=lists/googlevideo.txt",
            ),
            _item(
                "Twitter/X · картинки (twimg.com)",
                key="tw-0",
                group="social",
                group_name="Соцсети",
                match="--hostlist=lists/twimg.txt",
            ),
        )
        return build_profile_list_view_state(
            items, active_profile_types={"all"}, search_query="", group_expanded={}
        )

    def test_row_keeps_the_full_name_and_gets_a_short_one_for_the_tile(self) -> None:
        rows = {row["key"]: row for row in self._state().rows if row.get("kind") == "profile"}

        self.assertEqual(rows["yt-0"]["display_name"], "YouTube · видео (googlevideo.com)")
        self.assertEqual(rows["yt-0"]["tile_name"], "Видео (googlevideo.com)")
        # Полное имя видно в подсказке строки.
        self.assertTrue(rows["yt-0"]["tooltip"].startswith("YouTube · видео (googlevideo.com)\n"))
        self.assertEqual(rows["tw-0"]["tile_name"], "Twitter/X · картинки (twimg.com)")
        self.assertFalse(rows["tw-0"]["tooltip"].startswith("Twitter/X"))

    def test_search_still_finds_the_profile_by_its_full_name(self) -> None:
        items = (
            _item(
                "YouTube · видео (googlevideo.com)",
                key="yt-0",
                group="youtube",
                group_name="YouTube",
                match="--hostlist=lists/googlevideo.txt",
            ),
        )
        for query in ("youtube", "видео", "googlevideo"):
            with self.subTest(query=query):
                state = build_profile_list_view_state(
                    items, active_profile_types={"all"}, search_query=query, group_expanded={}
                )
                self.assertEqual([row["key"] for row in state.rows if row.get("kind") == "profile"], ["yt-0"])

    def test_model_gives_the_tile_the_short_name_and_others_the_full_one(self) -> None:
        from profile.ui.profile_list_model import ProfileListModel

        model = ProfileListModel()
        model.apply_view_state(self._state())
        names = {}
        for row in range(model.rowCount()):
            index = model.index(row, 0)
            if index.data(ProfileListModel.KindRole) == "profile":
                names[index.data(ProfileListModel.DisplayNameRole)] = index.data(ProfileListModel.TileNameRole)

        self.assertEqual(
            names,
            {
                "YouTube · видео (googlevideo.com)": "Видео (googlevideo.com)",
                "Twitter/X · картинки (twimg.com)": "Twitter/X · картинки (twimg.com)",
            },
        )

    def test_tile_row_paints_the_short_name(self) -> None:
        import inspect

        from profile.ui.profile_list_delegate import ProfileListDelegate

        source = inspect.getsource(ProfileListDelegate._paint_tile_profile_row)
        self.assertIn("TileNameRole", source)
        # Обычный список («Порядок в пресете») шапок не имеет: там имя полное.
        self.assertNotIn("TileNameRole", inspect.getsource(ProfileListDelegate._paint_profile_row))


@unittest.skipUnless(ALL_PROFILES_PATH.is_file(), "нет каталога шаблонов профилей")
class ShippedProfileNamesTests(unittest.TestCase):
    def _template_blocks(self):
        text = ALL_PROFILES_PATH.read_text(encoding="utf-8")
        for block in re.split(r"^--new\s*$", text, flags=re.MULTILINE):
            lines = [line.strip() for line in block.splitlines() if line.strip() and not line.strip().startswith("#")]
            name = next((line[len("--name=") :] for line in lines if line.startswith("--name=")), "")
            if name:
                yield name, lines

    def test_old_names_are_gone_from_templates_and_builtin_presets(self) -> None:
        template_names = {name for name, _lines in self._template_blocks()}
        self.assertEqual(sorted(template_names & set(OLD_NAMES)), [])

        offenders = []
        for path in sorted(BUILTIN_DIR.glob("*/*.txt")):
            for line in path.read_text(encoding="utf-8").splitlines():
                for prefix in ("--name=", "--comment="):
                    if line.startswith(prefix) and line[len(prefix) :].strip() in OLD_NAMES:
                        offenders.append(f"{path.parent.name}/{path.name}: {line}")
        self.assertEqual(offenders, [])

    def test_every_service_role_name_is_shortened_in_its_own_group(self) -> None:
        titles = {
            key: str(folder.get("name") or "")
            for key, folder in build_default_profile_folders()["folders"].items()
        }
        shortened = {}
        for name, lines in self._template_blocks():
            if " · " not in name:
                continue
            group_title = titles[classify_profile_folder("\n".join(lines))]
            shortened[name] = profile_name_inside_group(name, group_title)

        self.assertEqual(shortened["YouTube · видео (googlevideo.com)"], "Видео (googlevideo.com)")
        self.assertEqual(shortened["Discord · голос и видео (discord.media)"], "Голос и видео (discord.media)")
        self.assertEqual(shortened["GitHub · файлы и картинки"], "Файлы и картинки")
        self.assertEqual(shortened["WhatsApp · звонки (UDP)"], "Звонки (UDP)")
        self.assertEqual(shortened["Roblox · игра (UDP)"], "Игра (UDP)")
        # Картинки Twitter лежат в общей группе «Соцсети»: сервис в имени остаётся.
        self.assertEqual(shortened["Twitter/X · картинки (twimg.com)"], "Twitter/X · картинки (twimg.com)")
        not_shortened = sorted(name for name, short in shortened.items() if short == name)
        self.assertEqual(not_shortened, ["Twitter/X · картинки (twimg.com)"])


if __name__ == "__main__":
    unittest.main()
