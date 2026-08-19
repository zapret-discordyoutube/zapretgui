from __future__ import annotations

import unittest

from PyQt6.QtCore import Qt

from ui.presets_menu.model import PresetListModel


class PresetListModelGuardTests(unittest.TestCase):
    def test_rename_preset_skips_same_file_and_name(self) -> None:
        model = PresetListModel()
        model.set_rows([
            {
                "kind": "preset",
                "file_name": "Default.txt",
                "name": "Default",
                "folder_key": "common",
            },
        ])
        self.assertFalse(model.rename_preset("Default.txt", "Default.txt", name="Default"))

    def test_set_folder_collapsed_removes_only_visible_folder_rows(self) -> None:
        model = PresetListModel()
        model.set_rows(
            [
                {"kind": "folder", "folder_key": "common", "name": "Common", "is_collapsed": False, "count": 2},
                {"kind": "preset", "file_name": "A.txt", "name": "A", "folder_key": "common"},
                {"kind": "preset", "file_name": "B.txt", "name": "B", "folder_key": "common"},
                {"kind": "folder", "folder_key": "games", "name": "Games", "is_collapsed": False, "count": 1},
                {"kind": "preset", "file_name": "C.txt", "name": "C", "folder_key": "games"},
            ]
        )

        self.assertTrue(model.set_folder_collapsed("common", True))

        self.assertEqual(model.rowCount(), 3)
        self.assertTrue(model.index(0, 0).data(PresetListModel.CollapsedRole))
        self.assertEqual(model.index(1, 0).data(PresetListModel.FolderKeyRole), "games")
        self.assertEqual(model.index(2, 0).data(PresetListModel.FileNameRole), "C.txt")

    def test_set_folder_collapsed_does_not_expand_without_cached_rows(self) -> None:
        model = PresetListModel()
        model.set_rows(
            [
                {"kind": "folder", "folder_key": "common", "name": "Common", "is_collapsed": True, "count": 2},
            ]
        )

        self.assertFalse(model.set_folder_collapsed("common", False))

    def test_set_folder_collapsed_expands_cached_rows_without_rows_plan(self) -> None:
        model = PresetListModel()
        model.set_rows(
            [
                {"kind": "folder", "folder_key": "common", "name": "Common", "is_collapsed": False, "count": 2},
                {"kind": "preset", "file_name": "A.txt", "name": "A", "folder_key": "common"},
                {"kind": "preset", "file_name": "B.txt", "name": "B", "folder_key": "common"},
                {"kind": "folder", "folder_key": "games", "name": "Games", "is_collapsed": False, "count": 1},
                {"kind": "preset", "file_name": "C.txt", "name": "C", "folder_key": "games"},
            ]
        )
        self.assertTrue(model.set_folder_collapsed("common", True))

        self.assertTrue(model.set_folder_collapsed("common", False))

        self.assertEqual(model.rowCount(), 5)
        self.assertFalse(model.index(0, 0).data(PresetListModel.CollapsedRole))
        self.assertEqual(model.index(1, 0).data(PresetListModel.FileNameRole), "A.txt")
        self.assertEqual(model.index(2, 0).data(PresetListModel.FileNameRole), "B.txt")
        self.assertEqual(model.index(3, 0).data(PresetListModel.FolderKeyRole), "games")

    def test_move_to_collapsed_folder_caches_row_until_folder_expands(self) -> None:
        model = PresetListModel()
        model.set_rows(
            [
                {"kind": "folder", "folder_key": "common", "name": "Общие", "is_collapsed": False, "count": 1},
                {
                    "kind": "preset",
                    "file_name": "A.txt",
                    "name": "A",
                    "folder_key": "common",
                    "folder_name": "Общие",
                },
                {"kind": "folder", "folder_key": "games", "name": "Игры", "is_collapsed": True, "count": 0},
            ]
        )

        self.assertTrue(model.move_preset("A.txt", "folder", "games", "games"))
        self.assertEqual(model.rowCount(), 2)
        self.assertEqual(model.index(0, 0).data(PresetListModel.CountRole), 0)
        self.assertEqual(model.index(1, 0).data(PresetListModel.CountRole), 1)

        self.assertTrue(model.set_folder_collapsed("games", False))
        moved_row = model.index(2, 0)
        self.assertEqual(moved_row.data(PresetListModel.FileNameRole), "A.txt")
        self.assertEqual(moved_row.data(PresetListModel.FolderKeyRole), "games")
        self.assertIn("папка: Игры", moved_row.data(Qt.ItemDataRole.AccessibleTextRole))

    def test_folder_updates_emit_accessible_text_role(self) -> None:
        model = PresetListModel()
        model.set_rows(
            [
                {"kind": "folder", "folder_key": "common", "name": "Общие", "is_collapsed": False, "count": 1},
                {"kind": "preset", "file_name": "A.txt", "name": "A", "folder_key": "common"},
            ]
        )
        changed_roles: list[list[int]] = []
        model.dataChanged.connect(
            lambda _top_left, _bottom_right, roles: changed_roles.append([int(role) for role in roles])
        )

        self.assertTrue(model.set_folder_collapsed("common", True))

        self.assertIn(int(Qt.ItemDataRole.AccessibleTextRole), changed_roles[-1])
        self.assertIn(PresetListModel.CollapsedRole, changed_roles[-1])

    def test_remote_row_updates_emit_remote_roles(self) -> None:
        model = PresetListModel()
        model.set_rows(
            [
                {
                    "kind": "preset",
                    "file_name": "Remote.txt",
                    "name": "Remote",
                    "is_remote": False,
                    "remote_state": "",
                }
            ]
        )
        changed_roles: list[list[int]] = []
        model.dataChanged.connect(
            lambda _top_left, _bottom_right, roles: changed_roles.append([int(role) for role in roles])
        )

        self.assertTrue(model.update_preset_row("Remote.txt", is_remote=True, remote_state="ok"))

        self.assertIn(PresetListModel.RemoteRole, changed_roles[-1])
        self.assertIn(PresetListModel.RemoteStateRole, changed_roles[-1])
        self.assertIn(int(Qt.ItemDataRole.AccessibleTextRole), changed_roles[-1])

    def test_preset_display_name_cache_updates_with_row_metadata(self) -> None:
        model = PresetListModel()
        model.set_rows(
            [
                {"kind": "preset", "file_name": "A.txt", "name": "Old"},
            ]
        )

        self.assertEqual(model.preset_display_name("A.txt"), "Old")
        self.assertTrue(model.update_preset_row("A.txt", name="New"))

        self.assertEqual(model.preset_display_name("A.txt"), "New")

    def test_preset_builtin_cache_updates_with_row_metadata(self) -> None:
        model = PresetListModel()
        model.set_rows(
            [
                {"kind": "preset", "file_name": "A.txt", "name": "A", "is_builtin": False},
            ]
        )

        self.assertFalse(model.preset_is_builtin("A.txt"))
        self.assertTrue(model.update_preset_row("A.txt", is_builtin=True))

        self.assertTrue(model.preset_is_builtin("A.txt"))

    def test_preset_rating_cache_updates_with_row_metadata(self) -> None:
        model = PresetListModel()
        model.set_rows(
            [
                {"kind": "preset", "file_name": "A.txt", "name": "A", "rating": 2},
            ]
        )

        self.assertEqual(model.preset_rating("A.txt"), 2)
        self.assertTrue(model.update_preset_row("A.txt", rating=7))

        self.assertEqual(model.preset_rating("A.txt"), 7)

    def test_update_preset_row_emits_screen_reader_role_for_spoken_state(self) -> None:
        model = PresetListModel()
        model.set_rows(
            [
                {"kind": "preset", "file_name": "A.txt", "name": "A", "rating": 2},
            ]
        )
        changed_roles: list[list[int]] = []
        model.dataChanged.connect(
            lambda _top_left, _bottom_right, roles: changed_roles.append([int(role) for role in roles])
        )

        self.assertTrue(model.update_preset_row("A.txt", rating=7))

        self.assertIn(int(Qt.ItemDataRole.AccessibleTextRole), changed_roles[-1])

    def test_set_active_preset_emits_screen_reader_role_for_spoken_state(self) -> None:
        model = PresetListModel()
        model.set_rows(
            [
                {"kind": "preset", "file_name": "A.txt", "name": "A", "is_active": False},
            ]
        )
        changed_roles: list[list[int]] = []
        model.dataChanged.connect(
            lambda _top_left, _bottom_right, roles: changed_roles.append([int(role) for role in roles])
        )

        self.assertTrue(model.set_active_preset("A.txt"))

        self.assertIn(int(Qt.ItemDataRole.AccessibleTextRole), changed_roles[-1])


if __name__ == "__main__":
    unittest.main()
