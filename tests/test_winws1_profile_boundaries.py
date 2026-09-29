"""winws1 (nfqws1) принимает только голый `--new`.

`winws.exe` отвергает `--new=<имя>` ("option doesn't take an argument -- new"),
поэтому структурные правки профилей (удалить / дублировать / переместить /
править raw-текст профиля) не должны превращать разделитель в `--new=...`.
Имя профиля в winws1 задаётся только через `--comment`.
"""

from __future__ import annotations

from pathlib import Path
import unittest

from profile.parser import parse_preset_text
from profile.serializer import (
    serialize_preset,
    with_profile_deleted,
    with_profile_duplicated,
    with_profile_moved,
)
from settings.mode import ENGINE_WINWS1, ENGINE_WINWS2

WINWS1_BUILTIN_ROOT = Path(__file__).resolve().parents[1] / "src" / "presets" / "builtin" / "winws1"


def _new_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip().lower().startswith("--new")]


class Winws1ProfileBoundariesTest(unittest.TestCase):
    def test_structural_edits_keep_bare_new_in_every_builtin_winws1_preset(self) -> None:
        paths = sorted(WINWS1_BUILTIN_ROOT.glob("*.txt"))
        self.assertTrue(paths)
        checked = 0
        for path in paths:
            preset = parse_preset_text(path.read_text(encoding="utf-8"), engine=ENGINE_WINWS1, source_name=path.name)
            if len(preset.profiles) < 3:
                continue
            edits = {
                "delete": with_profile_deleted(preset, 1),
                "duplicate": with_profile_duplicated(preset, 1),
                "move": with_profile_moved(preset, 2, 1),
            }
            for action, updated in edits.items():
                with self.subTest(preset=path.name, action=action):
                    bad = [line for line in _new_lines(serialize_preset(updated)) if line != "--new"]
                    self.assertEqual(bad, [])
                    checked += 1
        self.assertGreater(checked, 0)

    def test_winws1_profile_without_comment_gets_bare_new(self) -> None:
        text = (
            "--wf-tcp=80,443\n\n"
            "--filter-tcp=443\n--dpi-desync=fake\n\n--new\n\n"
            "--filter-tcp=80\n--dpi-desync=fake\n\n--new\n\n"
            "--filter-udp=443\n--dpi-desync=fake\n"
        )
        preset = parse_preset_text(text, engine=ENGINE_WINWS1, source_name="t.txt")
        updated = with_profile_moved(preset, 2, 1)
        self.assertEqual(_new_lines(serialize_preset(updated)), ["--new", "--new"])

    def test_winws2_keeps_unnamed_profiles_unnamed_and_named_ones_named(self) -> None:
        # Пресет — точка истины: перемещение не дописывает безымянным профилям
        # вычисленных подписей вроде «--new=TCP 80», а своё имя из «--new=Имя»
        # профиль сохраняет.
        from profile.key_resolution import build_preset_profile_key_map

        text = (
            "--filter-tcp=443\n--lua-desync=pass\n\n--new\n\n"
            "--filter-tcp=80\n--lua-desync=pass\n\n--new=Games\n\n"
            "--filter-udp=443\n--lua-desync=pass\n"
        )
        preset = parse_preset_text(text, engine=ENGINE_WINWS2, source_name="t.txt")
        updated = with_profile_moved(preset, 2, 1)
        self.assertEqual(_new_lines(serialize_preset(updated)), ["--new=Games", "--new"])
        # Ключи всех профилей переносятся: выбор не перескакивает на соседа.
        self.assertEqual(
            build_preset_profile_key_map(preset.profiles, updated.profiles),
            {"profile:0": "profile:0", "profile:1": "profile:2", "profile:2": "profile:1"},
        )


if __name__ == "__main__":
    unittest.main()
