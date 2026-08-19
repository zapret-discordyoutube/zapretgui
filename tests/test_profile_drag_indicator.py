from __future__ import annotations

import inspect
import unittest
from unittest.mock import Mock

from PyQt6.QtCore import QPoint

from profile.ui import profile_list_delegate, profile_list_view


class ProfileDragIndicatorTests(unittest.TestCase):
    def test_drop_marker_maps_targets_to_clear_visual_modes(self) -> None:
        self.assertEqual(
            profile_list_view.profile_drop_marker_for_target(2, "folder"),
            {"row": 2, "mode": "folder"},
        )
        self.assertEqual(
            profile_list_view.profile_drop_marker_for_target(4, "profile"),
            {"row": 4, "mode": "before"},
        )
        self.assertEqual(
            profile_list_view.profile_drop_marker_for_target(-1, ""),
            {"row": -1, "mode": ""},
        )

    def test_drop_target_uses_lower_half_as_after_row(self) -> None:
        self.assertEqual(
            profile_list_view.profile_drop_target_for_position(4, "profile", y=109, row_top=100, row_height=20),
            {"marker": {"row": 4, "mode": "before"}, "destination_kind": "profile", "destination_row": 4},
        )
        self.assertEqual(
            profile_list_view.profile_drop_target_for_position(4, "profile", y=112, row_top=100, row_height=20),
            {"marker": {"row": 4, "mode": "after"}, "destination_kind": "profile_after", "destination_row": 4},
        )

    def test_adjacent_profile_gap_has_one_canonical_drop_target(self) -> None:
        lower_half_target = profile_list_view.profile_drop_target_for_position(
            4,
            "profile",
            y=112,
            row_top=100,
            row_height=20,
        )

        self.assertEqual(
            profile_list_view.profile_canonical_drop_target_for_next_row(
                lower_half_target,
                next_row=5,
                next_kind="profile",
            ),
            {"marker": {"row": 5, "mode": "before"}, "destination_kind": "profile", "destination_row": 5},
        )

    def test_view_supports_folder_drop_and_clears_marker(self) -> None:
        view_source = inspect.getsource(profile_list_view.ProfileListView)

        self.assertIn("profile_move_to_folder_requested", view_source)
        self.assertIn("set_drop_marker", view_source)
        self.assertIn("dragLeaveEvent", view_source)
        self.assertIn("self.set_drop_marker(-1, \"\")", view_source)

    def test_internal_drag_does_not_depend_on_windows_ole_drop_target(self) -> None:
        view_source = inspect.getsource(profile_list_view.ProfileListView.mouseMoveEvent)
        finish_source = inspect.getsource(profile_list_view.ProfileListView._finish_internal_drag)

        self.assertIn('self._drag_source = ("profile", source_key)', view_source)
        self.assertIn("self._update_internal_drag", view_source)
        self.assertNotIn("QDrag", view_source)
        self.assertNotIn("drag.exec", view_source)
        self.assertIn("profile_move_to_folder_requested", finish_source)
        self.assertIn("profile_move_requested", finish_source)
        self.assertIn("profile_move_after_requested", finish_source)
        self.assertIn("profile_move_to_end_requested", finish_source)

    def test_internal_mouse_drag_emits_before_after_folder_and_end_moves(self) -> None:
        view = profile_list_view.ProfileListView()
        self.addCleanup(view.deleteLater)
        view.resize(500, 300)

        moved: list[tuple[str, tuple[str, ...]]] = []
        view.profile_move_requested.connect(lambda *args: moved.append(("before", tuple(args))))
        view.profile_move_after_requested.connect(lambda *args: moved.append(("after", tuple(args))))
        view.profile_move_to_folder_requested.connect(lambda *args: moved.append(("folder", tuple(args))))
        view.profile_move_to_end_requested.connect(lambda *args: moved.append(("end", tuple(args))))

        targets = (
            (
                {"marker": {"row": 1, "mode": "before"}, "destination_kind": "profile"},
                "profile:b",
                "games",
                "before",
            ),
            (
                {"marker": {"row": 1, "mode": "after"}, "destination_kind": "profile_after"},
                "profile:b",
                "games",
                "after",
            ),
            (
                {"marker": {"row": 0, "mode": "folder"}, "destination_kind": "folder"},
                "games",
                "games",
                "folder",
            ),
            (
                {"marker": {"row": -1, "mode": ""}, "destination_kind": "end"},
                "",
                "",
                "end",
            ),
        )

        for target, destination_id, destination_group_key, action in targets:
            with self.subTest(action=action):
                view._drag_source = ("profile", "profile:a")
                view._drop_target_at = Mock(
                    return_value=(target, destination_id, destination_group_key)
                )

                self.assertTrue(view._finish_internal_drag(QPoint(20, 20)))

        self.assertEqual(
            moved,
            [
                ("before", ("profile:a", "profile:b", "games")),
                ("after", ("profile:a", "profile:b", "games")),
                ("folder", ("profile:a", "games")),
                ("end", ("profile:a",)),
            ],
        )
        self.assertIsNone(view._drag_source)

    def test_view_updates_only_drop_marker_rows(self) -> None:
        payload_source = inspect.getsource(profile_list_view.ProfileListView.set_drop_marker_payload)
        update_source = inspect.getsource(profile_list_view.ProfileListView._update_drop_marker_rows)

        self.assertIn("_update_drop_marker_rows", payload_source)
        self.assertNotIn("viewport().update()", payload_source)
        self.assertIn("viewport().update(rect", update_source)

    def test_view_updates_same_drop_marker_row_once(self) -> None:
        class _Rect:
            def __init__(self, row: int) -> None:
                self.row = row

            def adjusted(self, *_args):
                return self

            def isValid(self) -> bool:  # noqa: N802
                return True

        class _Index:
            def __init__(self, row: int) -> None:
                self.row = row

            def isValid(self) -> bool:  # noqa: N802
                return True

        class _Model:
            def rowCount(self) -> int:  # noqa: N802
                return 5

            def index(self, row: int, _column: int):
                return _Index(row)

        class _Viewport:
            def __init__(self) -> None:
                self.updated_rows: list[int] = []

            def update(self, rect) -> None:
                self.updated_rows.append(rect.row)

        view = profile_list_view.ProfileListView.__new__(profile_list_view.ProfileListView)
        viewport = _Viewport()
        view.model = lambda: _Model()
        view.visualRect = lambda index: _Rect(index.row)
        view.viewport = lambda: viewport

        profile_list_view.ProfileListView._update_drop_marker_rows(
            view,
            {"row": 2, "mode": "before"},
            {"row": 2, "mode": "after"},
        )

        self.assertEqual(viewport.updated_rows, [2])

    def test_profile_current_index_helper_skips_already_selected_index(self) -> None:
        class _Index:
            def __init__(self, row: int) -> None:
                self.row = row

            def __eq__(self, other) -> bool:
                return isinstance(other, _Index) and self.row == other.row

        index = _Index(3)
        view = Mock()
        view.currentIndex.return_value = index

        self.assertFalse(profile_list_view.set_current_index_if_changed(view, index))

        view.setCurrentIndex.assert_not_called()

    def test_view_sends_destination_group_with_row_drop(self) -> None:
        view_source = inspect.getsource(profile_list_view.ProfileListView)

        self.assertIn("profile_move_requested = pyqtSignal(str, str, str)", view_source)
        self.assertIn("profile_move_after_requested = pyqtSignal(str, str, str)", view_source)
        self.assertIn("ProfileListModel.GroupRole", view_source)
        self.assertIn("destination_group_key", view_source)

    def test_delegate_draws_folder_and_before_row_drop_markers(self) -> None:
        delegate_source = inspect.getsource(profile_list_delegate.ProfileListDelegate)

        self.assertIn("_paint_drop_marker", delegate_source)
        self.assertIn('marker.get("mode") == "folder"', delegate_source)
        self.assertIn('marker.get("mode") == "before"', delegate_source)
        self.assertIn('marker.get("mode") == "after"', delegate_source)


if __name__ == "__main__":
    unittest.main()
