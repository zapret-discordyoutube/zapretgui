from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

from core.paths import AppPaths
from profile.strategy_catalog import load_strategy_catalogs
from profile.strategy_visuals import _TECHNIQUES, describe_strategy_visual

PUBLIC_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_LUA_DIR = PUBLIC_ROOT.parent / "private_zapretgui" / "dist" / "lua"
# Стандартные функции --lua-desync из оригинального zapret2.
STANDARD_LUA_FILES = ("zapret-lib.lua", "zapret-antidpi.lua")
_DESYNC_FUNCTION_RE = re.compile(r"^function\s+([a-z0-9_]+)\(ctx,\s*desync\)", re.MULTILINE)
_LUA_DESYNC_NAME_RE = re.compile(r"--lua-desync=([A-Za-z0-9_]+)")


def _winws2_catalog_entries():
    paths = AppPaths(user_root=PUBLIC_ROOT / "src", local_root=PUBLIC_ROOT / "src")
    for catalog in load_strategy_catalogs(paths, "winws2").values():
        yield from catalog.values()


class ProfileStrategyVisualTests(unittest.TestCase):
    def test_detects_multiple_lua_desync_techniques_in_order(self) -> None:
        visual = describe_strategy_visual(
            "--out-range=-d8\n"
            "--lua-desync=fake:blob=tls_max:badsum:repeats=8\n"
            "--lua-desync=multidisorder:pos=1:seqovl=681\n"
        )

        self.assertEqual(visual.technique_keys, ("fake", "multidisorder"))
        self.assertEqual(visual.label, "Fake + MultiDisorder")
        self.assertEqual(visual.icon_name, "fa5s.magic")
        self.assertEqual(visual.color, "#ff6b6b")
        self.assertIn("Fake", visual.description)
        self.assertIn("MultiDisorder", visual.description)

    def test_ignores_non_strategy_options_for_visual_identity(self) -> None:
        left = describe_strategy_visual("--out-range=-d8\n--lua-desync=multisplit:seqovl=652")
        right = describe_strategy_visual("--payload=all\n--in-range=-n8\n--lua-desync=multisplit:seqovl=652")

        self.assertEqual(left.technique_keys, right.technique_keys)
        self.assertEqual(left.label, "MultiSplit")

    def test_unknown_strategy_has_neutral_visual(self) -> None:
        visual = describe_strategy_visual("--payload=all")

        self.assertEqual(visual.technique_keys, ())
        self.assertEqual(visual.label, "Своя")
        self.assertEqual(visual.icon_name, "fa5s.question")

    def test_strategy_icons_use_the_shipped_font_bundle(self) -> None:
        samples = [
            "--lua-desync=fake",
            "--lua-desync=multisplit",
            "--lua-desync=multidisorder",
            "--lua-desync=syndata",
            "--lua-desync=udplen",
        ]

        for args in samples:
            with self.subTest(args=args):
                visual = describe_strategy_visual(args)
                self.assertTrue(visual.icon_name.startswith("fa5s."))

    def test_standard_lua_desync_functions_map_to_own_technique(self) -> None:
        expected = {
            "pass": "pass",
            "send": "send",
            "syndata": "syndata",
            "udplen": "udplen",
            "oob": "oob",
            "tcpseg": "tcpseg",
            "fake": "fake",
            "multisplit": "multisplit",
            "multidisorder": "multidisorder",
            "multidisorder_legacy": "multidisorder",
            "hostfakesplit": "hostfakesplit",
            "hostfakesplit_multi": "hostfakesplit",
            "hostfakesplit_stealth": "hostfakesplit",
            "fakedsplit": "fakedsplit",
            "fakeddisorder": "fakeddisorder",
            "fakemultisplit": "fakemultisplit",
            "fakemultidisorder": "fakemultidisorder",
            "drop": "drop",
            "wssize": "wssize",
            "pktmod": "pktmod",
            "dht_dn": "tamper",
            "http_hostcase": "tamper",
            "http_methodeol": "tamper",
        }
        for function_name, technique in expected.items():
            with self.subTest(function_name=function_name):
                visual = describe_strategy_visual(f"--lua-desync={function_name}:repeats=2")
                self.assertEqual(visual.technique_keys, (technique,))

    def test_custom_function_names_still_use_substring_fallback(self) -> None:
        cases = {
            "tls_multisplit_sni": "multisplit",
            "tls_split_gentle": "split",
            "tls_disorder_gentle": "disorder",
            "tls_fake_flood": "fake",
            "http_methodeol_safe": None,
        }
        for function_name, technique in cases.items():
            with self.subTest(function_name=function_name):
                visual = describe_strategy_visual(f"--lua-desync={function_name}")
                self.assertEqual(visual.technique_keys, (technique,) if technique else ())

    def test_every_technique_has_russian_description_and_fa5s_icon(self) -> None:
        for key, technique in _TECHNIQUES.items():
            with self.subTest(key=key):
                self.assertEqual(technique.key, key)
                self.assertTrue(technique.icon_name.startswith("fa5s."))
                self.assertRegex(technique.color, r"^#[0-9a-f]{6}$")
                self.assertRegex(technique.description, r"[а-яё]")

    def test_technique_icons_exist_in_qtawesome_font(self) -> None:
        try:
            import qtawesome
        except Exception as exc:  # pragma: no cover - зависит от окружения
            self.skipTest(f"qtawesome недоступен: {exc}")
        charmap_files = sorted(
            (Path(qtawesome.__file__).parent / "fonts").glob("fontawesome5-solid-webfont-charmap*.json")
        )
        if not charmap_files:
            self.skipTest("нет charmap fontawesome5-solid")
        charmap = json.loads(charmap_files[0].read_text(encoding="utf-8"))
        for key, technique in _TECHNIQUES.items():
            with self.subTest(key=key):
                self.assertIn(technique.icon_name.removeprefix("fa5s."), charmap)

    def test_catalog_hostfakesplit_entries_are_not_plain_split(self) -> None:
        checked = 0
        for entry in _winws2_catalog_entries():
            names = [name.lower() for name in _LUA_DESYNC_NAME_RE.findall(entry.args)]
            if not any(name.startswith("hostfakesplit") for name in names):
                continue
            checked += 1
            with self.subTest(strategy_id=entry.strategy_id):
                self.assertIn("hostfakesplit", entry.visual.technique_keys)
                if not any("split" in name and not name.startswith("hostfakesplit") for name in names):
                    self.assertNotIn("split", entry.visual.technique_keys)
        self.assertGreater(checked, 0)

    def test_every_standard_function_in_winws2_catalogs_has_technique(self) -> None:
        if not PRIVATE_LUA_DIR.is_dir():
            self.skipTest(f"Нет private-репозитория: {PRIVATE_LUA_DIR}")
        standard: set[str] = set()
        for file_name in STANDARD_LUA_FILES:
            text = (PRIVATE_LUA_DIR / file_name).read_text(encoding="utf-8", errors="replace")
            standard |= set(_DESYNC_FUNCTION_RE.findall(text))
        used = {
            name
            for entry in _winws2_catalog_entries()
            for name in _LUA_DESYNC_NAME_RE.findall(entry.args)
        }
        standard_used = sorted(used & standard)
        self.assertGreater(len(standard_used), 5)
        for function_name in standard_used:
            with self.subTest(function_name=function_name):
                visual = describe_strategy_visual(f"--lua-desync={function_name}")
                self.assertEqual(len(visual.technique_keys), 1)
                self.assertIn(visual.technique_keys[0], _TECHNIQUES)


if __name__ == "__main__":
    unittest.main()
