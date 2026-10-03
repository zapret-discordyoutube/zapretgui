from __future__ import annotations

import inspect
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QColor, QKeyEvent, QPixmap
from PyQt6.QtWidgets import QApplication, QGraphicsOpacityEffect, QVBoxLayout, QWidget

import presets.ui.control.zapret1.sections_build as zapret1_sections
import presets.ui.control.zapret2.sections_build as zapret2_sections
import ui.widgets.motion_icon as motion_module
import ui.widgets.tile_grid as tile_module
from app.ui_texts import tr as tr_catalog
from presets.ui.control.quick_actions import apply_quick_actions_language, build_quick_actions, quick_action_specs
from ui.widgets.action_tile import ActionTile
from ui.widgets.motion_icon import GESTURE_BOUNCE


def _tr(language: str):
    return lambda key, default: tr_catalog(key, language=language, default=default)


class QuickActionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        for module in (tile_module, motion_module):
            patcher = mock.patch.object(module, "are_live_animations_enabled", return_value=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.calls: list[str] = []
        self.host = QWidget()
        self.addCleanup(self.host.deleteLater)
        self.widgets = build_quick_actions(
            tr_fn=_tr("ru"),
            text_prefix="page.winws2_control",
            on_open_onboarding_tour=lambda: self.calls.append("tour"),
            on_open_connection_test=lambda: self.calls.append("test"),
            on_open_internet_cleanup=lambda: self.calls.append("internet_cleanup"),
            on_open_folder=lambda: self.calls.append("folder"),
            on_open_docs=lambda: self.calls.append("docs"),
            parent=self.host,
        )
        layout = QVBoxLayout(self.host)
        layout.addWidget(self.widgets.title_label)
        layout.addWidget(self.widgets.grid)

    def _tiles(self) -> list[ActionTile]:
        w = self.widgets
        return [w.tour_card, w.test_card, w.internet_cleanup_card, w.folder_card, w.docs_card]

    def test_every_tile_runs_its_own_action(self) -> None:
        for tile in self._tiles():
            tile.click()

        self.assertEqual(self.calls, ["tour", "test", "internet_cleanup", "folder", "docs"])

    def test_tiles_are_buttons_for_keyboard_and_screen_reader(self) -> None:
        tile = self.widgets.test_card

        self.assertEqual(tile.focusPolicy(), Qt.FocusPolicy.StrongFocus)
        self.assertEqual(tile.accessibleName(), "Открыть тест соединения")
        self.assertEqual(tile.property("screenReaderStateText"), "Открыть тест соединения")
        self.assertIn("Проверить доступность сети", tile.accessibleDescription())
        self.assertIn("Enter или Пробел", tile.accessibleDescription())

        tile.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(self.calls, ["test"])

    def test_busy_tile_fades_and_ignores_clicks(self) -> None:
        tile = self.widgets.internet_cleanup_card
        tile.setEnabled(False)

        tile.click()
        self.assertEqual(self.calls, [])
        self.assertIsInstance(tile.graphicsEffect(), QGraphicsOpacityEffect)
        self.assertEqual(tile.accessibleName(), "Сбросить сеть Windows, недоступно")

        tile.setEnabled(True)
        self.assertIsNone(tile.graphicsEffect())
        self.assertEqual(tile.accessibleName(), "Сбросить сеть Windows")
        tile.click()
        self.assertEqual(self.calls, ["internet_cleanup"])

    def test_language_switch_retranslates_tiles_and_title(self) -> None:
        w = self.widgets
        apply_quick_actions_language(
            tr_fn=_tr("en"),
            text_prefix="page.winws2_control",
            title_label=w.title_label,
            tour_card=w.tour_card,
            test_card=w.test_card,
            internet_cleanup_card=w.internet_cleanup_card,
            folder_card=w.folder_card,
            docs_card=w.docs_card,
        )

        self.assertEqual(w.title_label.text(), "Quick actions")
        self.assertEqual(w.tour_card.title(), "How to use the app")
        self.assertEqual(w.tour_card.accessibleName(), "Show the guided tour")
        self.assertEqual(w.test_card.title(), tr_catalog("page.winws2_control.button.connection_test", language="en"))

    def test_five_tiles_stand_in_one_row_and_wrap_three_plus_two(self) -> None:
        self.host.resize(1200, 600)
        self.host.show()
        QApplication.processEvents()
        grid = self.widgets.grid
        self.assertEqual(grid.columns(), 5)
        self.assertEqual(len({tile.height() for tile in self._tiles()}), 1)

        self.host.resize(820, 700)
        QApplication.processEvents()
        self.assertEqual(grid.columns(), 3)
        self.assertEqual(self.widgets.folder_card.x(), self.widgets.tour_card.x())
        self.assertEqual(self.widgets.folder_card.width(), self.widgets.tour_card.width())

    def test_narrow_tile_grows_taller_for_wrapped_text(self) -> None:
        tile = self.widgets.internet_cleanup_card
        self.assertTrue(tile.hasHeightForWidth())
        self.assertGreater(tile.heightForWidth(170), tile.heightForWidth(420))

    def test_hover_bounces_icon_and_tile_glows_with_its_own_color(self) -> None:
        self.host.resize(1200, 600)
        self.host.show()
        QApplication.processEvents()
        tile = self.widgets.folder_card

        tile.enterEvent(None)

        self.assertEqual(tile._icon.gesture(), GESTURE_BOUNCE)
        self.assertEqual(tile.accent_color(), QColor("#f5c04d"))
        self.assertLess(tile.chevron_center_y(), tile.height() / 2)

        tile._hover_t = 1.0
        image = QPixmap(tile.size())
        image.fill(QColor(0, 0, 0, 0))
        tile.render(image)
        self.assertFalse(image.isNull())

    def test_specs_use_mode_texts_for_both_pages(self) -> None:
        for prefix in ("page.winws1_control", "page.winws2_control"):
            specs = {spec.key: spec for spec in quick_action_specs(prefix)}
            self.assertEqual(list(specs), ["tour", "test", "internet_cleanup", "folder", "docs"])
            self.assertTrue(specs["test"].title[0].startswith(prefix))
            self.assertTrue(specs["folder"].accessible_name[0].startswith(prefix))


class ProgramSettingsGroupTests(unittest.TestCase):
    def test_state_media_toggle_lives_with_other_windows_blocks(self) -> None:
        for module in (zapret1_sections, zapret2_sections):
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module)
                self.assertIn("program_settings_card.addSettingCard(state_media_block_toggle)", source)
                self.assertNotIn("extra_card", source)


if __name__ == "__main__":
    unittest.main()
