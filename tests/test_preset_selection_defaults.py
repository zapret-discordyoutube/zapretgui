from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from core.paths import AppPaths
from presets.file_store import PresetFileStore
from presets.mode_coordinator import PresetModeCoordinator, PresetModeError
from presets.selection_service import PresetSelectionService
from settings.mode import DEFAULT_PRESET_FILE_NAME_BY_ENGINE, ENGINE_WINWS2, ZAPRET2_MODE
from settings import store as settings_store


class PresetSelectionDefaultsTests(unittest.TestCase):
    def test_first_start_selects_configured_default_preset_not_first_sorted_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            builtin_dir = root / "presets" / "winws2_builtin"
            builtin_dir.mkdir(parents=True)
            (builtin_dir / "A first alphabetically.txt").write_text(
                "# Preset: A first alphabetically\n--wf-tcp-out=80\n",
                encoding="utf-8",
            )
            default_file_name = DEFAULT_PRESET_FILE_NAME_BY_ENGINE[ENGINE_WINWS2]
            (builtin_dir / default_file_name).write_text(
                "# Preset: Default v1 (game filter)\n--wf-tcp-out=80\n",
                encoding="utf-8",
            )

            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                store = PresetFileStore(AppPaths(user_root=root, local_root=root))
                selection = PresetSelectionService(store)
                coordinator = PresetModeCoordinator(AppPaths(user_root=root, local_root=root), selection, store)

                manifest = coordinator.get_selected_source_manifest(ZAPRET2_MODE)

                settings = settings_store.read_settings()

        self.assertEqual(manifest.file_name, default_file_name)
        self.assertEqual(
            settings["program"]["selected_source_preset_file_name_winws2"],
            default_file_name,
        )

    def test_missing_saved_selection_is_replaced_with_configured_default_preset(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            builtin_dir = root / "presets" / "winws2_builtin"
            builtin_dir.mkdir(parents=True)
            default_file_name = DEFAULT_PRESET_FILE_NAME_BY_ENGINE[ENGINE_WINWS2]
            (builtin_dir / default_file_name).write_text(
                "# Preset: Default v1 (game filter)\n--wf-tcp-out=80\n",
                encoding="utf-8",
            )
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                settings_store.set_strategy_launch_method(ZAPRET2_MODE)
                settings_store.set_selected_source_preset_file_name(ENGINE_WINWS2, "missing.txt")
                store = PresetFileStore(AppPaths(user_root=root, local_root=root))
                selection = PresetSelectionService(store)
                coordinator = PresetModeCoordinator(AppPaths(user_root=root, local_root=root), selection, store)

                manifest = coordinator.get_selected_source_manifest(ZAPRET2_MODE)

                settings = settings_store.read_settings()

        self.assertEqual(manifest.file_name, default_file_name)
        self.assertEqual(
            settings["program"]["selected_source_preset_file_name_winws2"],
            default_file_name,
        )

    def test_missing_presets_error_names_searched_directories(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                store = PresetFileStore(AppPaths(user_root=root, local_root=root))
                selection = PresetSelectionService(store)
                coordinator = PresetModeCoordinator(AppPaths(user_root=root, local_root=root), selection, store)

                with self.assertRaises(PresetModeError) as ctx:
                    coordinator.get_selected_source_manifest(ZAPRET2_MODE)

        message = str(ctx.exception)
        self.assertIn(str(root / "presets" / "winws2"), message)
        self.assertIn(str(root / "presets" / "winws2_builtin"), message)


if __name__ == "__main__":
    unittest.main()
