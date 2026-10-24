from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication

import ui.presets_menu.delegate as delegate_module
import ui.widgets.hover_row as hover_row_module
from ui.presets_menu import common
from ui.presets_menu.delegate import PresetListDelegate
from ui.presets_menu.model import PresetListModel
from ui.presets_menu.view import LinkedWheelListView


def _rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = [
        {"kind": "folder", "folder_key": "all", "name": "ALL TCP & UDP", "is_collapsed": False, "count": 3},
        # Имя повторяет заголовок папки: плитка показывает только отличие.
        {"kind": "preset", "name": "ALL TCP & UDP discord", "file_name": "a.txt", "folder_key": "all", "depth": 1,
         "icon_color": "#e0a030", "is_active": True, "is_pinned": True, "rating": 4},
        {"kind": "preset", "name": "ALL TCP & UDP", "file_name": "b.txt", "folder_key": "all", "depth": 1},
        {"kind": "preset", "name": "v1 (game filter) с очень длинным именем, которое не поместится в плитку", "file_name": "c.txt",
         "folder_key": "all", "depth": 1, "is_remote": True, "remote_state": "detached", "icon_color": None, "rating": None},
        {"kind": "folder", "folder_key": "common", "name": "Общие", "is_collapsed": False, "count": 2},
        {"kind": "preset", "name": "general ALT", "file_name": "d.txt", "folder_key": "common", "depth": 1, "icon_color": ""},
        {"kind": "preset", "file_name": "e.txt", "folder_key": "common", "depth": 1},
        {"kind": "section", "text": "Раздел"},
        {"kind": "empty", "text": "Пусто"},
    ]
    return rows


class TileValuesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_tile_values_equal_what_the_roles_return(self) -> None:
        model = PresetListModel()
        model.set_rows(_rows())
        for row in range(model.rowCount()):
            index = model.index(row, 0)
            values = model.tile_values(row)
            self.assertIsNotNone(values)
            name = str(index.data(PresetListModel.NameRole) or "")
            name = name[int(index.data(PresetListModel.RepeatedPrefixLengthRole) or 0):]
            expected = (
                name,
                bool(index.data(PresetListModel.ActiveRole)),
                bool(index.data(PresetListModel.PinnedRole)),
                int(index.data(PresetListModel.RatingRole) or 0),
                str(index.data(PresetListModel.FileNameRole) or ""),
                str(index.data(PresetListModel.IconColorRole) or ""),
                bool(index.data(PresetListModel.RemoteRole)),
                str(index.data(PresetListModel.RemoteStateRole) or ""),
            )
            self.assertEqual(tuple(values), expected, f"row {row}")

    def test_the_repeated_folder_title_is_cut_from_the_shown_name(self) -> None:
        model = PresetListModel()
        model.set_rows(_rows())
        self.assertEqual(model.tile_values(1).name, "discord")

    def test_rows_outside_the_list_have_no_values(self) -> None:
        model = PresetListModel()
        model.set_rows(_rows())
        self.assertIsNone(model.tile_values(-1))
        self.assertIsNone(model.tile_values(model.rowCount()))


class TilePaintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.view = LinkedWheelListView()
        self.addCleanup(self.view.deleteLater)
        self.model = PresetListModel(self.view)
        self.model.set_rows(_rows())
        self.view.setModel(self.model)
        self.delegate = PresetListDelegate(self.view)
        self.view.setItemDelegate(self.delegate)
        self.view.resize(900, 420)
        self.view.show()
        QApplication.processEvents()
        self.view.doItemsLayout()

    def _render(self) -> QImage:
        image = QImage(self.view.viewport().size(), QImage.Format.Format_RGB32)
        image.fill(QColor("#202020"))
        self.view.viewport().render(image)
        return image

    def test_fast_data_path_draws_the_same_picture_as_the_generic_one(self) -> None:
        fast = self._render()
        with mock.patch.object(PresetListModel, "tile_values", None):
            generic = self._render()
        self.assertEqual(fast, generic)

    def test_theme_tokens_are_taken_once_per_repaint_not_once_per_tile(self) -> None:
        rows = [{"kind": "folder", "folder_key": "all", "name": "ALL", "is_collapsed": False, "count": 60}]
        rows += [
            {"kind": "preset", "name": f"preset {number}", "file_name": f"p{number}.txt", "folder_key": "all", "depth": 1}
            for number in range(60)
        ]
        self.model.set_rows(rows)
        self.view.resize(900, 900)
        QApplication.processEvents()
        self.view.doItemsLayout()

        real = delegate_module.get_theme_tokens
        drawn: list[int] = []
        original = self.delegate._paint_preset_row
        self.delegate._paint_preset_row = lambda painter, option, index: (drawn.append(index.row()), original(painter, option, index))[1]
        frames = []
        original_begin = self.delegate.begin_frame
        self.delegate.begin_frame = lambda: (frames.append(1), original_begin())[1]
        with mock.patch.object(delegate_module, "get_theme_tokens", side_effect=real) as in_delegate, \
                mock.patch.object(hover_row_module, "get_theme_tokens", side_effect=real) as in_hover_row:
            self._render()

        self.assertGreater(len(drawn), 25)
        self.assertEqual(in_delegate.call_count, len(frames))
        self.assertLessEqual(len(frames), 3)
        self.assertEqual(in_hover_row.call_count, 0)

    def test_painting_a_tile_without_a_repaint_in_progress_still_gets_the_tokens(self) -> None:
        # delegate.paint() можно позвать и напрямую, не из paintEvent списка.
        image = QImage(300, 40, QImage.Format.Format_RGB32)
        image.fill(QColor("#202020"))
        painter = QPainter(image)
        from PyQt6.QtWidgets import QStyleOptionViewItem

        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 300, 40)
        self.delegate.paint(painter, option, self.model.index(1, 0))
        painter.end()
        self.assertIsNone(self.delegate._frame_tokens)


class ContrastMemoTests(unittest.TestCase):
    def setUp(self) -> None:
        common._CONTRAST_CACHE.clear()

    def test_memoised_answer_equals_the_computed_one(self) -> None:
        backgrounds = [QColor("#1f1f1f"), QColor("#ffffff"), QColor(31, 31, 31, 90), QColor("#5caee8"), QColor()]
        preferred = ["#e0a030", "#202020", "", "bogus", "#ffffff"]
        for bg in backgrounds:
            for color in preferred:
                for ratio in (2.4, 2.6):
                    expected_bg = QColor(bg) if QColor(bg).isValid() else QColor("#000000")
                    expected_bg.setAlpha(255)
                    expected = common._pick_contrast_color_uncached(color, expected_bg, ["#5caee8", "#f5f5f5"], ratio)
                    first = common.pick_contrast_color(color, bg, ["#5caee8", "#f5f5f5"], minimum_ratio=ratio)
                    second = common.pick_contrast_color(color, bg, ["#5caee8", "#f5f5f5"], minimum_ratio=ratio)
                    self.assertEqual((first, second), (expected, expected))

    def test_the_computation_runs_once_for_repeated_calls(self) -> None:
        with mock.patch.object(common, "_pick_contrast_color_uncached", wraps=common._pick_contrast_color_uncached) as inner:
            for _ in range(50):
                common.pick_contrast_color("#e0a030", QColor("#1f1f1f"), ["#5caee8"], minimum_ratio=2.6)
        self.assertEqual(inner.call_count, 1)

    def test_background_alpha_does_not_split_the_cache(self) -> None:
        with mock.patch.object(common, "_pick_contrast_color_uncached", wraps=common._pick_contrast_color_uncached) as inner:
            common.pick_contrast_color("#e0a030", QColor(31, 31, 31, 255), [])
            common.pick_contrast_color("#e0a030", QColor(31, 31, 31, 40), [])
        self.assertEqual(inner.call_count, 1)

    def test_cache_does_not_grow_without_limit(self) -> None:
        for number in range(common._CONTRAST_CACHE_LIMIT * 2 + 5):
            common.pick_contrast_color(f"#{number % 0xFFFFFF:06x}", QColor("#1f1f1f"), [])
        self.assertLessEqual(len(common._CONTRAST_CACHE), common._CONTRAST_CACHE_LIMIT)


class HoverRowTokensTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_given_tokens_are_used_everywhere_including_the_fading_branch(self) -> None:
        tokens = hover_row_module.get_theme_tokens()
        image = QImage(200, 40, QImage.Format.Format_RGB32)
        image.fill(QColor("#202020"))
        painter = QPainter(image)
        with mock.patch.object(hover_row_module, "get_theme_tokens", side_effect=AssertionError("tokens were not passed on")):
            hover_row_module.paint_profile_hover_row(painter, QRect(2, 2, 196, 36), residual_active=0.5, tokens=tokens)
            hover_row_module.paint_profile_hover_row(painter, QRect(2, 2, 196, 36), active=True, active_reveal=0.4, tokens=tokens)
            hover_row_module.paint_profile_hover_row(painter, QRect(2, 2, 196, 36), hovered=True, tokens=tokens)
        painter.end()


if __name__ == "__main__":
    unittest.main()
