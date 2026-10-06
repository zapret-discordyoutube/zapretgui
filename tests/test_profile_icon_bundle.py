from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import profile.ui.profile_icon as profile_icon
from profile.ui.simple_icons_bundle import SIMPLE_ICON_SVGS


def _catalog_simple_slugs() -> set[str]:
    """Собирает simple-слаги из каталога иконок тем же способом, что и генератор."""
    import profile.icons as profile_icons

    slugs: set[str] = set()
    for attr_name in dir(profile_icons):
        attr = getattr(profile_icons, attr_name)
        if not isinstance(attr, dict):
            continue
        for value in attr.values():
            icon_name = str(getattr(value, "icon_name", "") or "")
            if not icon_name.startswith("simple:"):
                continue
            slug = icon_name.removeprefix("simple:").partition(":")[0]
            slug = slug.strip().lower().replace("-", "")
            if slug:
                slugs.add(slug)
    return slugs


class SimpleIconsBundleTests(unittest.TestCase):
    """Рантайм рисует брендовые иконки из сгенерированного бандла, без simplepycons."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        profile_icon._PROFILE_PIXMAP_CACHE.clear()

    def test_bundle_covers_all_catalog_slugs(self) -> None:
        """При добавлении сервиса в profile/icons.py нужно перегенерировать бандл:
        PYTHONPATH=src python tools/generate_profile_icon_bundle.py"""
        catalog_slugs = _catalog_simple_slugs()
        self.assertTrue(catalog_slugs, "каталог не дал ни одного simple-слага")
        missing = sorted(catalog_slugs - set(SIMPLE_ICON_SVGS))
        self.assertEqual(missing, [], f"бандл не покрывает слаги каталога: {missing}")

    def test_bundle_entries_are_valid_svgs(self) -> None:
        for slug, (primary_color, raw_svg) in SIMPLE_ICON_SVGS.items():
            with self.subTest(slug=slug):
                self.assertTrue(raw_svg.lstrip().startswith("<svg"), "не SVG")
                if primary_color:
                    self.assertRegex(primary_color, r"^#[0-9A-Fa-f]{6}$")

    def test_simple_icon_renders_from_bundle(self) -> None:
        pixmap = profile_icon.profile_icon_pixmap(
            "simple:youtube:YT",
            color="#FF0000",
            size=18,
        )

        self.assertFalse(pixmap.isNull())
        cache_kinds = {key[0] for key in profile_icon._PROFILE_PIXMAP_CACHE}
        self.assertIn("simple", cache_kinds, "иконка не отрисована из бандла")
        self.assertNotIn("initials", cache_kinds, "не должно быть заглушки для иконки из бандла")

    def test_vencord_icon_renders_from_bundle(self) -> None:
        pixmap = profile_icon.profile_icon_pixmap(
            "simple:vencord:VC",
            color="#EB7396",
            size=18,
        )

        self.assertFalse(pixmap.isNull())
        self.assertTrue(any(key[0] == "simple" and key[1] == "vencord" for key in profile_icon._PROFILE_PIXMAP_CACHE))

    def test_unknown_slug_falls_back_to_initials(self) -> None:
        pixmap = profile_icon.profile_icon_pixmap(
            "simple:definitely-not-in-bundle:NB",
            color="#3B82F6",
            size=18,
        )

        self.assertFalse(pixmap.isNull())
        cache_kinds = {key[0] for key in profile_icon._PROFILE_PIXMAP_CACHE}
        self.assertIn("initials", cache_kinds)

    def test_runtime_never_imports_simplepycons(self) -> None:
        """Регрессия: импорт simplepycons (~3400 модулей, ~2.6с) морозил первую
        отрисовку списка профилей. Рантайм обязан обходиться бандлом."""
        for slug in list(SIMPLE_ICON_SVGS)[:5]:
            profile_icon.profile_icon_pixmap(f"simple:{slug}:XX", color="", size=18)

        self.assertNotIn("simplepycons", sys.modules)

    def test_profile_icon_module_has_no_simplepycons_reference(self) -> None:
        source = (PROJECT_SRC / "profile" / "ui" / "profile_icon.py").read_text(encoding="utf-8")
        self.assertNotIn("import simplepycons", source)
        self.assertNotIn("from simplepycons", source)


def _catalog_own_slugs() -> set[str]:
    import profile.icons as profile_icons

    slugs: set[str] = set()
    for attr_name in dir(profile_icons):
        attr = getattr(profile_icons, attr_name)
        if not isinstance(attr, dict):
            continue
        for value in attr.values():
            icon_name = str(getattr(value, "icon_name", "") or "")
            if icon_name.startswith("own:"):
                slugs.add(icon_name.removeprefix("own:").partition(":")[0].strip().lower())
    return slugs


class OwnIconsTests(unittest.TestCase):
    """Свои SVG-значки — для сервисов, которых нет в Simple Icons."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        profile_icon._PROFILE_PIXMAP_CACHE.clear()

    def _painted_pixels(self, pixmap) -> int:
        image = pixmap.toImage()
        return sum(
            1
            for x in range(image.width())
            for y in range(image.height())
            if image.pixelColor(x, y).alpha() > 40
        )

    def test_every_own_icon_used_by_the_catalog_exists(self) -> None:
        from profile.ui.own_icons import OWN_ICON_SVGS

        used = _catalog_own_slugs()
        self.assertIn("openai", used)
        self.assertEqual(sorted(used - set(OWN_ICON_SVGS)), [])

    def test_own_icons_are_plain_one_color_svgs(self) -> None:
        from profile.ui.own_icons import OWN_ICON_SVGS

        for slug, (color, raw_svg) in OWN_ICON_SVGS.items():
            with self.subTest(slug=slug):
                self.assertRegex(color, r"^#[0-9A-Fa-f]{6}$")
                self.assertTrue(raw_svg.startswith("<svg"))
                self.assertIn('viewBox="0 0 24 24"', raw_svg)
                # Цвет подставляет программа: своего fill у рисунка быть не должно.
                self.assertNotIn("fill=", raw_svg.split(">", 1)[0])
                # Имя не должно совпадать с набором Simple Icons: генератор
                # перезаписывает его файл целиком.
                self.assertNotIn(slug, SIMPLE_ICON_SVGS)

    def test_chatgpt_profile_draws_the_openai_logo_in_the_asked_color(self) -> None:
        from profile.icons import resolve_profile_icon

        icon = resolve_profile_icon("chatgpt", ("--filter-tcp=80-65535", "--hostlist=lists/chatgpt.txt"))
        self.assertEqual(icon.icon_name, "own:openai:AI")

        pixmap = profile_icon.profile_icon_pixmap(icon.icon_name, color="#10A37F", size=18)
        self.assertFalse(pixmap.isNull())
        self.assertGreater(self._painted_pixels(pixmap), 100)
        # Линии логотипа тонкие, поэтому края сглажены: смотрим на плотные точки.
        image = pixmap.toImage()
        solid = [
            image.pixelColor(x, y)
            for x in range(image.width())
            for y in range(image.height())
            if image.pixelColor(x, y).alpha() > 150
        ]
        self.assertGreater(len(solid), 20)
        for color in solid:
            self.assertLessEqual(abs(color.red() - 0x10), 4)
            self.assertLessEqual(abs(color.green() - 0xA3), 4)
            self.assertLessEqual(abs(color.blue() - 0x7F), 4)

        # Не буквы на цветной плашке: у запасного значка закрашен весь квадрат.
        fallback = profile_icon.profile_icon_pixmap("own:no-such-icon:AI", color="#10A37F", size=18)
        self.assertGreater(self._painted_pixels(fallback), self._painted_pixels(pixmap) + 60)

    def test_group_of_chatgpt_profiles_can_show_the_logo_in_the_tile_header(self) -> None:
        from profile.list_view_state import build_profile_list_view_state
        from profile.state import ProfileListItem

        items = tuple(
            ProfileListItem(
                key=f"gpt-{position}",
                persistent_key=f"gpt-{position}",
                profile_index=position,
                display_name="chatgpt",
                enabled=True,
                in_preset=True,
                strategy_id="pass",
                strategy_name="pass",
                match_lines=("--filter-tcp=443", "--hostlist=lists/chatgpt.txt"),
                list_type="hostlist",
                rating="",
                favorite=False,
                group="ai",
                group_name="ИИ",
                order=position,
                profile_name="chatgpt",
            )
            for position in range(2)
        )
        state = build_profile_list_view_state(
            items, active_profile_types={"all"}, search_query="", group_expanded={}
        )

        self.assertEqual(state.rows[0]["icon_name"], "own:openai:AI")
        self.assertTrue(all(row["icon_in_header"] for row in state.rows[1:]))


if __name__ == "__main__":
    unittest.main()
