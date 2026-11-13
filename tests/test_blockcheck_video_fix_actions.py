"""Кнопки исправления видео в итоге BlockCheck: у каждой есть слова и значок, пресет берётся с тем же игровым фильтром."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.page_names import PageName
from ui.page_deps.system import build_blockcheck_page_kwargs

_ACTIONS = ("youtube_block_caches", "youtube_unblock_caches", "youtube_preset")


class _Presets:
    def __init__(self, current: str) -> None:
        self.current = current
        self.activated: list[tuple[str, str]] = []

    def get_selected_source_preset_file_name(self, _method: str) -> str:
        return self.current

    def activate_preset_file(self, method: str, file_name: str) -> None:
        self.activated.append((method, file_name))


def _kwargs(presets) -> dict:
    runtime = SimpleNamespace(start=lambda: True, stop=lambda: True, is_running=lambda: False, shutdown_sync_from_worker=None)
    return build_blockcheck_page_kwargs(
        page_name=PageName.BLOCKCHECK, blockcheck_feature=object(), dns_feature=object(), runtime_feature=runtime, presets_feature=presets
    )


class PresetButtonTests(unittest.TestCase):
    def test_game_filter_preset_switches_to_the_game_variant(self) -> None:
        presets = _Presets("Default (game filter).txt")
        ok, name = _kwargs(presets)["activate_preset"]("Default + YouTube no QUIC + wssize.txt")
        self.assertTrue(ok)
        self.assertEqual(presets.activated[0][1], "Default + YouTube no QUIC + wssize (game filter).txt")
        self.assertEqual(name, "Default + YouTube no QUIC + wssize (game filter)")

    def test_plain_preset_switches_to_the_plain_variant(self) -> None:
        presets = _Presets("Default multisplit_sni.txt")
        _kwargs(presets)["activate_preset"]("Default + YouTube no QUIC + wssize.txt")
        self.assertEqual(presets.activated[0][1], "Default + YouTube no QUIC + wssize.txt")

    def test_no_presets_feature_means_no_button(self) -> None:
        self.assertIsNone(_kwargs(None)["activate_preset"])

    def test_target_preset_files_exist(self) -> None:
        from pathlib import Path

        from diagnostics.problems import YOUTUBE_FIX_PRESET

        folder = Path(__file__).resolve().parents[1] / "src" / "presets" / "builtin" / "winws2"
        self.assertTrue((folder / YOUTUBE_FIX_PRESET).is_file())
        self.assertTrue((folder / YOUTUBE_FIX_PRESET.replace(".txt", " (game filter).txt")).is_file())


class ActionWordsTests(unittest.TestCase):
    def test_every_video_fix_action_has_text_description_and_icon(self) -> None:
        from blockcheck.ui import check_results

        for action in _ACTIONS:
            self.assertTrue(check_results._ACTION_TEXT.get(action), action)
            self.assertTrue(check_results._ACTION_DESCRIPTION.get(action), action)
            self.assertTrue(check_results._ACTION_ICONS.get(action), action)


if __name__ == "__main__":
    unittest.main()
