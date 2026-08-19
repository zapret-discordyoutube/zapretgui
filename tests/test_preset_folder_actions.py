from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from folders.defaults import COMMON_FOLDER_KEY, PINNED_FOLDER_KEY
from presets.folders import (
    copy_preset_item_meta,
    create_preset_folder,
    delete_preset_folder,
    delete_preset_item_meta,
    get_preset_item_meta,
    load_preset_folder_state,
    move_preset_folder_by_step,
    move_preset_by_step,
    move_preset_after,
    move_preset_before,
    move_preset_to_end,
    rename_preset_folder,
    rename_preset_item_meta,
    reset_preset_folders,
    save_preset_folder_state,
    set_preset_folder_collapsed,
    set_preset_rating,
    toggle_preset_pin,
)
from settings.mode import PRESETS_SCOPE_WINWS2


class PresetFolderActionTests(unittest.TestCase):
    def test_create_folder_places_it_after_common(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                folder_key = create_preset_folder(PRESETS_SCOPE_WINWS2, "Моя папка")
                state = load_preset_folder_state(PRESETS_SCOPE_WINWS2)

        ordered_names = [
            folder["name"]
            for _key, folder in sorted(state["folders"].items(), key=lambda pair: pair[1]["order"])
        ]
        self.assertEqual(folder_key, "моя-папка")
        self.assertEqual(
            ordered_names,
            ["ALL TCP & UDP", "Общие", "Моя папка", "1.10.0", "1.9.9", "Game filter", "Circular"],
        )

    def test_delete_folder_moves_presets_to_common(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                folder_key = create_preset_folder(PRESETS_SCOPE_WINWS2, "Моя папка")
                from presets.folders import move_preset_to_folder

                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "Custom.txt", folder_key))
                self.assertTrue(delete_preset_folder(PRESETS_SCOPE_WINWS2, folder_key))
                state = load_preset_folder_state(PRESETS_SCOPE_WINWS2)

        self.assertNotIn(folder_key, state["folders"])
        self.assertEqual(state["items"]["Custom.txt"]["folder_key"], COMMON_FOLDER_KEY)

    def test_system_folder_cannot_be_renamed_but_can_move(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                self.assertFalse(rename_preset_folder(PRESETS_SCOPE_WINWS2, COMMON_FOLDER_KEY, "Другая"))
                self.assertTrue(move_preset_folder_by_step(PRESETS_SCOPE_WINWS2, COMMON_FOLDER_KEY, 1))
                state = load_preset_folder_state(PRESETS_SCOPE_WINWS2)

        ordered_names = [
            folder["name"]
            for _key, folder in sorted(state["folders"].items(), key=lambda pair: pair[1]["order"])
        ]
        self.assertEqual(ordered_names[:2], ["ALL TCP & UDP", "1.10.0"])
        self.assertIn("Общие", ordered_names)

    def test_duplicate_preset_folder_rename_skips_folder_state_save(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                folder_key = create_preset_folder(PRESETS_SCOPE_WINWS2, "Моя папка")
                with patch(
                    "presets.folders.save_preset_folder_state",
                    side_effect=AssertionError("unchanged preset folder name must not be saved"),
                ):
                    self.assertFalse(rename_preset_folder(PRESETS_SCOPE_WINWS2, folder_key, "Моя   папка"))

    def test_rating_and_pin_are_stored_in_folders_items(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                self.assertTrue(set_preset_rating(PRESETS_SCOPE_WINWS2, "ALL TCP & UDP v3.txt", 8))
                self.assertTrue(toggle_preset_pin(PRESETS_SCOPE_WINWS2, "ALL TCP & UDP v3.txt"))
                meta = get_preset_item_meta(PRESETS_SCOPE_WINWS2, "ALL TCP & UDP v3.txt")

        self.assertEqual(meta["folder_key"], "all-tcp-udp")
        self.assertEqual(meta["rating"], 8)
        self.assertTrue(meta["pinned"])

    def test_duplicate_rating_and_pin_skip_folder_state_save(self) -> None:
        from presets.folders import set_preset_pin

        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                self.assertTrue(set_preset_rating(PRESETS_SCOPE_WINWS2, "ALL TCP & UDP v3.txt", 8))
                self.assertTrue(set_preset_pin(PRESETS_SCOPE_WINWS2, "ALL TCP & UDP v3.txt", True))
                with patch(
                    "presets.folders.save_preset_folder_state",
                    side_effect=AssertionError("unchanged preset item metadata must not be saved"),
                ):
                    self.assertFalse(set_preset_rating(PRESETS_SCOPE_WINWS2, "ALL TCP & UDP v3.txt", 8))
                    self.assertFalse(set_preset_pin(PRESETS_SCOPE_WINWS2, "ALL TCP & UDP v3.txt", True))

    def test_duplicate_pinned_folder_collapsed_skips_folder_state_save(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                self.assertTrue(set_preset_folder_collapsed(PRESETS_SCOPE_WINWS2, PINNED_FOLDER_KEY, True))
                with patch(
                    "presets.folders.save_preset_folder_state",
                    side_effect=AssertionError("unchanged pinned folder collapsed state must not be saved"),
                ):
                    self.assertFalse(set_preset_folder_collapsed(PRESETS_SCOPE_WINWS2, PINNED_FOLDER_KEY, True))

    def test_default_pinned_folder_expanded_state_skips_folder_state_save(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                load_preset_folder_state(PRESETS_SCOPE_WINWS2)
                with patch(
                    "presets.folders.save_preset_folder_state",
                    side_effect=AssertionError("default pinned folder expanded state must not be saved"),
                ):
                    self.assertFalse(set_preset_folder_collapsed(PRESETS_SCOPE_WINWS2, PINNED_FOLDER_KEY, False))

    def test_duplicate_preset_folder_reset_skips_folder_state_save(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                load_preset_folder_state(PRESETS_SCOPE_WINWS2)
                with patch(
                    "presets.folders.save_preset_folder_state",
                    side_effect=AssertionError("unchanged preset folder reset must not be saved"),
                ):
                    self.assertFalse(reset_preset_folders(PRESETS_SCOPE_WINWS2))

    def test_save_preset_folder_state_skips_settings_write_when_state_is_unchanged(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                state = load_preset_folder_state(PRESETS_SCOPE_WINWS2)
                with patch(
                    "presets.folders.settings_store.set_folders_settings",
                    side_effect=AssertionError("unchanged preset folder state must not write settings"),
                ):
                    self.assertEqual(save_preset_folder_state(PRESETS_SCOPE_WINWS2, state), state)

    def test_rating_action_preserves_display_default_folder(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                self.assertTrue(
                    set_preset_rating(
                        PRESETS_SCOPE_WINWS2,
                        "custom-name.txt",
                        6,
                        display_name="Preset X Game filter",
                    )
                )
                self.assertTrue(
                    toggle_preset_pin(
                        PRESETS_SCOPE_WINWS2,
                        "another-custom-name.txt",
                        display_name="ALL TCP & UDP v3",
                    )
                )
                rated_meta = get_preset_item_meta(PRESETS_SCOPE_WINWS2, "custom-name.txt")
                pinned_meta = get_preset_item_meta(PRESETS_SCOPE_WINWS2, "another-custom-name.txt")

        self.assertEqual(rated_meta["folder_key"], "game-filter")
        self.assertEqual(pinned_meta["folder_key"], "all-tcp-udp")

    def test_rename_copy_and_delete_update_folder_item_meta(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                folder_key = create_preset_folder(PRESETS_SCOPE_WINWS2, "Моя папка")
                from presets.folders import move_preset_to_folder

                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "Source.txt", folder_key))
                self.assertTrue(set_preset_rating(PRESETS_SCOPE_WINWS2, "Source.txt", 7))
                self.assertTrue(toggle_preset_pin(PRESETS_SCOPE_WINWS2, "Source.txt"))

                self.assertTrue(rename_preset_item_meta(PRESETS_SCOPE_WINWS2, "Source.txt", "Renamed.txt"))
                self.assertEqual(get_preset_item_meta(PRESETS_SCOPE_WINWS2, "Renamed.txt")["folder_key"], folder_key)
                self.assertEqual(get_preset_item_meta(PRESETS_SCOPE_WINWS2, "Source.txt")["rating"], 0)

                self.assertTrue(copy_preset_item_meta(PRESETS_SCOPE_WINWS2, "Renamed.txt", "Copy.txt"))
                copy_meta = get_preset_item_meta(PRESETS_SCOPE_WINWS2, "Copy.txt")
                self.assertEqual(copy_meta["folder_key"], folder_key)
                self.assertEqual(copy_meta["rating"], 0)
                self.assertFalse(copy_meta.get("pinned", False))

                self.assertTrue(delete_preset_item_meta(PRESETS_SCOPE_WINWS2, "Renamed.txt"))
                self.assertEqual(get_preset_item_meta(PRESETS_SCOPE_WINWS2, "Renamed.txt")["rating"], 0)

    def test_move_preset_by_step_uses_folder_rows(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                folder_key = create_preset_folder(PRESETS_SCOPE_WINWS2, "Моя папка")
                from presets.folders import move_preset_to_folder

                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "A.txt", COMMON_FOLDER_KEY))
                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "B.txt", folder_key))
                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "C.txt", folder_key))

                moved = move_preset_by_step(
                    PRESETS_SCOPE_WINWS2,
                    "A.txt",
                    1,
                    live_items=[
                        {"key": "A.txt", "name": "A"},
                        {"key": "B.txt", "name": "B"},
                        {"key": "C.txt", "name": "C"},
                    ],
                )
                state = load_preset_folder_state(PRESETS_SCOPE_WINWS2)

        self.assertTrue(moved)
        self.assertEqual(state["items"]["A.txt"]["folder_key"], folder_key)
        self.assertEqual(state["items"]["A.txt"]["order"], 1)

    def test_pinned_move_stays_in_pinned_sequence_and_preserves_real_folders(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                from presets.folders import move_preset_to_folder

                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "Pinned A.txt", "game-filter"))
                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "Pinned B.txt", COMMON_FOLDER_KEY))
                self.assertTrue(toggle_preset_pin(PRESETS_SCOPE_WINWS2, "Pinned A.txt"))
                self.assertTrue(toggle_preset_pin(PRESETS_SCOPE_WINWS2, "Pinned B.txt"))

                moved = move_preset_by_step(
                    PRESETS_SCOPE_WINWS2,
                    "Pinned A.txt",
                    1,
                    live_items=[
                        {"key": "Pinned A.txt", "name": "Pinned A"},
                        {"key": "Pinned B.txt", "name": "Pinned B"},
                        {"key": "Regular.txt", "name": "Regular"},
                    ],
                )
                state = load_preset_folder_state(PRESETS_SCOPE_WINWS2)

        self.assertTrue(moved)
        self.assertEqual(state["items"]["Pinned A.txt"]["folder_key"], "game-filter")
        self.assertEqual(state["items"]["Pinned B.txt"]["folder_key"], COMMON_FOLDER_KEY)
        self.assertEqual(state["items"]["Pinned A.txt"]["order"], 1)
        self.assertEqual(state["items"]["Pinned B.txt"]["order"], 0)
        self.assertTrue(state["items"]["Pinned A.txt"]["pinned"])
        self.assertTrue(state["items"]["Pinned B.txt"]["pinned"])

    def test_pinned_mouse_drop_uses_same_virtual_order_as_step_move(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                from presets.folders import move_preset_to_folder

                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "Pinned A.txt", "game-filter"))
                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "Pinned B.txt", COMMON_FOLDER_KEY))
                self.assertTrue(toggle_preset_pin(PRESETS_SCOPE_WINWS2, "Pinned A.txt"))
                self.assertTrue(toggle_preset_pin(PRESETS_SCOPE_WINWS2, "Pinned B.txt"))
                live_items = [
                    {"key": "Pinned A.txt", "name": "Pinned A"},
                    {"key": "Pinned B.txt", "name": "Pinned B"},
                ]

                self.assertTrue(
                    move_preset_after(
                        PRESETS_SCOPE_WINWS2,
                        "Pinned A.txt",
                        "Pinned B.txt",
                        destination_folder_key=COMMON_FOLDER_KEY,
                        live_items=live_items,
                    )
                )
                state = load_preset_folder_state(PRESETS_SCOPE_WINWS2)

        self.assertEqual(state["items"]["Pinned B.txt"]["order"], 0)
        self.assertEqual(state["items"]["Pinned A.txt"]["order"], 1)
        self.assertEqual(state["items"]["Pinned A.txt"]["folder_key"], "game-filter")
        self.assertEqual(state["items"]["Pinned B.txt"]["folder_key"], COMMON_FOLDER_KEY)

    def test_last_pinned_move_down_is_a_noop_even_with_regular_presets_below(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                from presets.folders import move_preset_to_folder

                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "Pinned A.txt", "game-filter"))
                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "Pinned B.txt", COMMON_FOLDER_KEY))
                self.assertTrue(toggle_preset_pin(PRESETS_SCOPE_WINWS2, "Pinned A.txt"))
                self.assertTrue(toggle_preset_pin(PRESETS_SCOPE_WINWS2, "Pinned B.txt"))
                before = load_preset_folder_state(PRESETS_SCOPE_WINWS2)

                moved = move_preset_by_step(
                    PRESETS_SCOPE_WINWS2,
                    "Pinned B.txt",
                    1,
                    live_items=[
                        {"key": "Pinned A.txt", "name": "Pinned A"},
                        {"key": "Pinned B.txt", "name": "Pinned B"},
                        {"key": "Regular.txt", "name": "Regular"},
                    ],
                )
                after = load_preset_folder_state(PRESETS_SCOPE_WINWS2)

        self.assertFalse(moved)
        self.assertEqual(after, before)

    def test_first_regular_move_up_does_not_cross_virtual_pinned_group(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                from presets.folders import set_preset_pin

                self.assertTrue(set_preset_pin(PRESETS_SCOPE_WINWS2, "Pinned.txt", True))
                before = load_preset_folder_state(PRESETS_SCOPE_WINWS2)

                moved = move_preset_by_step(
                    PRESETS_SCOPE_WINWS2,
                    "Regular.txt",
                    -1,
                    live_items=[
                        {"key": "Pinned.txt", "name": "Pinned", "pinned": True},
                        {"key": "Regular.txt", "name": "Regular"},
                    ],
                )
                after = load_preset_folder_state(PRESETS_SCOPE_WINWS2)

        self.assertFalse(moved)
        self.assertEqual(after, before)

    def test_move_down_to_last_visible_preset_uses_target_folder(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                from presets.folders import move_preset_to_folder

                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "A.txt", COMMON_FOLDER_KEY))
                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "B.txt", "game-filter"))
                moved = move_preset_by_step(
                    PRESETS_SCOPE_WINWS2,
                    "A.txt",
                    1,
                    live_items=[
                        {"key": "A.txt", "name": "A"},
                        {"key": "B.txt", "name": "B"},
                    ],
                )
                state = load_preset_folder_state(PRESETS_SCOPE_WINWS2)

        self.assertTrue(moved)
        self.assertEqual(state["items"]["A.txt"]["folder_key"], "game-filter")
        self.assertEqual(state["items"]["A.txt"]["order"], 1)
        self.assertEqual(state["items"]["B.txt"]["order"], 0)

    def test_virtual_pinned_folder_is_not_a_preset_destination(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                from presets.folders import move_preset_to_folder

                self.assertTrue(set_preset_folder_collapsed(PRESETS_SCOPE_WINWS2, PINNED_FOLDER_KEY, True))
                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "Regular.txt", COMMON_FOLDER_KEY))
                before = load_preset_folder_state(PRESETS_SCOPE_WINWS2)
                moved = move_preset_to_folder(PRESETS_SCOPE_WINWS2, "Regular.txt", PINNED_FOLDER_KEY)
                after = load_preset_folder_state(PRESETS_SCOPE_WINWS2)

        self.assertFalse(moved)
        self.assertEqual(after, before)
        self.assertEqual(after["items"]["Regular.txt"]["folder_key"], COMMON_FOLDER_KEY)

    def test_move_preset_after_matches_lower_drop_marker(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                from presets.folders import move_preset_to_folder

                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "A.txt", COMMON_FOLDER_KEY))
                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "B.txt", COMMON_FOLDER_KEY))
                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "C.txt", COMMON_FOLDER_KEY))
                self.assertTrue(move_preset_after(PRESETS_SCOPE_WINWS2, "A.txt", "B.txt"))
                state = load_preset_folder_state(PRESETS_SCOPE_WINWS2)

        self.assertEqual(state["items"]["B.txt"]["order"], 0)
        self.assertEqual(state["items"]["A.txt"]["order"], 1)
        self.assertEqual(state["items"]["C.txt"]["order"], 2)

    def test_duplicate_move_preset_after_skips_folder_state_save(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                from presets.folders import move_preset_to_folder

                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "A.txt", COMMON_FOLDER_KEY))
                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "B.txt", COMMON_FOLDER_KEY))
                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "C.txt", COMMON_FOLDER_KEY))
                self.assertTrue(move_preset_after(PRESETS_SCOPE_WINWS2, "A.txt", "B.txt"))
                with patch(
                    "presets.folders.save_preset_folder_state",
                    side_effect=AssertionError("unchanged preset folder order must not be saved"),
                ):
                    self.assertFalse(move_preset_after(PRESETS_SCOPE_WINWS2, "A.txt", "B.txt"))

    def test_duplicate_move_preset_to_end_skips_folder_state_save(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                from presets.folders import move_preset_to_folder

                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "A.txt", COMMON_FOLDER_KEY))
                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "B.txt", COMMON_FOLDER_KEY))
                self.assertTrue(move_preset_to_end(PRESETS_SCOPE_WINWS2, "A.txt"))
                with patch(
                    "presets.folders.save_preset_folder_state",
                    side_effect=AssertionError("unchanged preset move-to-end must not be saved"),
                ):
                    self.assertFalse(move_preset_to_end(PRESETS_SCOPE_WINWS2, "A.txt"))

    def test_move_preset_after_uses_visible_destination_folder(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                from presets.folders import move_preset_to_folder

                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "A.txt", COMMON_FOLDER_KEY))
                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "C.txt", COMMON_FOLDER_KEY))
                self.assertTrue(
                    move_preset_after(
                        PRESETS_SCOPE_WINWS2,
                        "A.txt",
                        "B.txt",
                        destination_folder_key="game-filter",
                    )
                )
                state = load_preset_folder_state(PRESETS_SCOPE_WINWS2)

        self.assertEqual(state["items"]["B.txt"]["folder_key"], "game-filter")
        self.assertEqual(state["items"]["A.txt"]["folder_key"], "game-filter")
        self.assertEqual(state["items"]["B.txt"]["order"], 0)
        self.assertEqual(state["items"]["A.txt"]["order"], 1)
        self.assertEqual(state["items"]["C.txt"]["folder_key"], COMMON_FOLDER_KEY)

    def test_move_preset_before_uses_visible_destination_folder(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                from presets.folders import move_preset_to_folder

                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "A.txt", COMMON_FOLDER_KEY))
                self.assertTrue(move_preset_to_folder(PRESETS_SCOPE_WINWS2, "C.txt", COMMON_FOLDER_KEY))
                self.assertTrue(
                    move_preset_before(
                        PRESETS_SCOPE_WINWS2,
                        "A.txt",
                        "B.txt",
                        destination_folder_key="game-filter",
                    )
                )
                state = load_preset_folder_state(PRESETS_SCOPE_WINWS2)

        self.assertEqual(state["items"]["A.txt"]["folder_key"], "game-filter")
        self.assertEqual(state["items"]["B.txt"]["folder_key"], "game-filter")
        self.assertEqual(state["items"]["A.txt"]["order"], 0)
        self.assertEqual(state["items"]["B.txt"]["order"], 1)
        self.assertEqual(state["items"]["C.txt"]["folder_key"], COMMON_FOLDER_KEY)


if __name__ == "__main__":
    unittest.main()
