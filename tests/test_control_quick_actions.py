from __future__ import annotations

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
            on_open_internet_cleanup=lambda: self.calls.append("internet_cleanup"),
            on_open_folder=lambda: self.calls.append("folder"),
            on_open_docs=lambda: self.calls.append("docs"),
            on_open_git=lambda: self.calls.append("git"),
            parent=self.host,
        )
        layout = QVBoxLayout(self.host)
        layout.addWidget(self.widgets.title_label)
        layout.addWidget(self.widgets.grid)

    def _tiles(self) -> list[ActionTile]:
        w = self.widgets
        return [w.tour_card, w.internet_cleanup_card, w.folder_card, w.docs_card, w.git_card]

    def test_every_tile_runs_its_own_action(self) -> None:
        for tile in self._tiles():
            tile.click()

        self.assertEqual(self.calls, ["tour", "internet_cleanup", "folder", "docs", "git"])

    def test_tiles_are_buttons_for_keyboard_and_screen_reader(self) -> None:
        tile = self.widgets.git_card

        self.assertEqual(tile.focusPolicy(), Qt.FocusPolicy.StrongFocus)
        self.assertEqual(tile.title(), "Исходный код")
        self.assertEqual(tile.accessibleName(), "Открыть сайт git.zapret.moe")
        self.assertEqual(tile.property("screenReaderStateText"), "Открыть сайт git.zapret.moe")
        self.assertIn("git.zapret.moe: код и выпуски", tile.accessibleDescription())
        self.assertIn("Enter или Пробел", tile.accessibleDescription())

        tile.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(self.calls, ["git"])

    def test_git_tile_stands_right_after_the_wiki_tile(self) -> None:
        # Два сайта проекта — вики и git — стоят рядом и в одном ряду, и после переноса.
        self.host.resize(1200, 600)
        self.host.show()
        QApplication.processEvents()
        docs, git = self.widgets.docs_card, self.widgets.git_card
        self.assertEqual(git.y(), docs.y())
        self.assertEqual(git.x(), docs.x() + docs.width() + 12)

        self.host.resize(820, 700)
        QApplication.processEvents()
        self.assertEqual(self.widgets.grid.columns(), 3)
        self.assertEqual(git.y(), docs.y())
        self.assertGreater(docs.y(), self.widgets.folder_card.y())
        self.assertEqual(git.x(), docs.x() + docs.width() + 12)

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
            internet_cleanup_card=w.internet_cleanup_card,
            folder_card=w.folder_card,
            docs_card=w.docs_card,
            git_card=w.git_card,
        )

        self.assertEqual(w.title_label.text(), "Quick actions")
        self.assertEqual(w.tour_card.title(), "How to use the app")
        self.assertEqual(w.tour_card.accessibleName(), "Show the guided tour")
        self.assertEqual(w.docs_card.title(), tr_catalog("page.winws2_control.button.documentation", language="en"))
        self.assertEqual(w.git_card.title(), "Source code")
        self.assertEqual(w.git_card.accessibleName(), "Open the git.zapret.moe site")

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
        self.assertEqual(self.widgets.docs_card.x(), self.widgets.tour_card.x())
        self.assertEqual(self.widgets.docs_card.width(), self.widgets.tour_card.width())

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

    def test_icon_is_darker_in_light_theme_and_follows_theme_switch(self) -> None:
        from qfluentwidgets import Theme, setTheme

        tile = self.widgets.tour_card
        tile._start_icon()
        self.addCleanup(setTheme, Theme.DARK)

        setTheme(Theme.DARK)
        self.assertEqual(tile.shown_icon_color(), QColor("#b39ddb"))
        setTheme(Theme.LIGHT)
        light = tile.shown_icon_color()
        self.assertLess(light.lightness(), QColor("#b39ddb").lightness())
        tile._apply_icon(force=True)
        self.assertFalse(tile._icon.pixmap().isNull())

    def test_specs_use_mode_texts_for_both_pages(self) -> None:
        for prefix in ("page.winws1_control", "page.winws2_control"):
            specs = {spec.key: spec for spec in quick_action_specs(prefix)}
            self.assertEqual(list(specs), ["tour", "internet_cleanup", "folder", "docs", "git"])
            self.assertTrue(specs["docs"].title[0].startswith(prefix))
            self.assertTrue(specs["folder"].accessible_name[0].startswith(prefix))


class SettingsGroupsTests(unittest.TestCase):
    """Настройки на главной разложены по смыслу в три группы."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _build(self, module, *, fakes: bool = False):
        """Три группы настроек одного режима — теми же строителями, что и страница."""
        from types import SimpleNamespace

        from qfluentwidgets import PushSettingCard, SettingCardGroup

        from presets.ui.control.windows_features.build import build_windows_settings_group
        from ui.widgets.win11_controls import Win11ComboRow, Win11ToggleRow

        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        noop = lambda *_args, **_kwargs: None  # noqa: E731
        program = module.build_program_settings_group(
            tr_fn=_tr("ru"),
            content_parent=parent,
            setting_card_group_cls=SettingCardGroup,
            win11_toggle_row_cls=Win11ToggleRow,
            win11_combo_row_cls=Win11ComboRow,
            on_gui_autostart_toggled=noop,
            on_auto_dpi_toggled=noop,
            on_tray_close_mode_changed=noop,
            on_discord_restart_changed=noop,
        )
        windows = build_windows_settings_group(
            tr_fn=_tr("ru"),
            content_parent=parent,
            setting_card_group_cls=SettingCardGroup,
            win11_toggle_row_cls=Win11ToggleRow,
            on_defender_toggled=noop,
            on_max_blocker_toggled=noop,
            on_state_media_block_toggled=noop,
        )
        fine = module.build_fine_tuning_group(
            tr_fn=_tr("ru"),
            content_parent=parent,
            win11_toggle_row_cls=Win11ToggleRow,
            on_wssize_toggled=noop,
            on_debug_log_toggled=noop,
            **({"push_setting_card_cls": PushSettingCard, "on_open_fakes": noop} if fakes else {}),
        )
        return SimpleNamespace(
            program_settings_card=program.card,
            gui_autostart_toggle=program.gui_autostart_toggle,
            auto_dpi_toggle=program.auto_dpi_toggle,
            tray_close_mode_combo=program.tray_close_mode_combo,
            discord_restart_toggle=program.discord_restart_toggle,
            windows_settings_card=windows.card,
            defender_toggle=windows.defender_toggle,
            max_block_toggle=windows.max_block_toggle,
            state_media_block_toggle=windows.state_media_block_toggle,
            additional_settings_card=fine.card,
            additional_settings_notice=fine.notice,
            wssize_toggle=fine.wssize_toggle,
            debug_log_toggle=fine.debug_log_toggle,
            fakes_card=getattr(fine, "fakes_card", None),
        )

    def _assert_groups(self, widgets, *, fakes: bool) -> None:
        launch, windows, advanced = (
            widgets.program_settings_card,
            widgets.windows_settings_card,
            widgets.additional_settings_card,
        )
        self.assertEqual(launch.titleLabel.text(), "Запуск и поведение")
        self.assertEqual(windows.titleLabel.text(), "Windows и блокировки")
        self.assertEqual(advanced.titleLabel.text(), "Тонкая настройка обхода")

        def group_of(row):
            return row.parent()

        for row in (
            widgets.gui_autostart_toggle,
            widgets.auto_dpi_toggle,
            widgets.tray_close_mode_combo,
            widgets.discord_restart_toggle,
        ):
            self.assertIs(group_of(row), launch)
        for row in (widgets.defender_toggle, widgets.max_block_toggle, widgets.state_media_block_toggle):
            self.assertIs(group_of(row), windows)
        for row in (widgets.wssize_toggle, widgets.debug_log_toggle):
            self.assertIs(group_of(row), advanced)
        if fakes:
            self.assertIs(group_of(widgets.fakes_card), advanced)
        # Предупреждение стоит у параметров движка, а не у настроек программы.
        self.assertIs(widgets.additional_settings_notice.parent(), advanced)

    def test_zapret2_groups(self) -> None:
        widgets = self._build(zapret2_sections, fakes=True)
        self._assert_groups(widgets, fakes=True)

    def test_zapret1_groups(self) -> None:
        widgets = self._build(zapret1_sections)
        self._assert_groups(widgets, fakes=False)

    def test_language_switch_retitles_all_three_groups(self) -> None:
        from presets.ui.control.zapret2.runtime_helpers import apply_profile_language

        widgets = self._build(zapret2_sections, fakes=True)
        close_btn = __import__("qfluentwidgets").TransparentPushButton("x")
        self.addCleanup(close_btn.deleteLater)
        apply_profile_language(
            language="en",
            close_btn=close_btn,
            internet_cleanup_card=None,
            folder_card=None,
            docs_card=None,
            git_card=None,
            additional_settings_notice=widgets.additional_settings_notice,
            fakes_card=None,
            program_settings_card=widgets.program_settings_card,
            windows_settings_card=widgets.windows_settings_card,
            auto_dpi_toggle=widgets.auto_dpi_toggle,
            gui_autostart_toggle=widgets.gui_autostart_toggle,
            tray_close_mode_combo=widgets.tray_close_mode_combo,
            defender_toggle=widgets.defender_toggle,
            max_block_toggle=widgets.max_block_toggle,
            state_media_block_toggle=widgets.state_media_block_toggle,
            additional_settings_card=widgets.additional_settings_card,
            discord_restart_toggle=widgets.discord_restart_toggle,
            wssize_toggle=widgets.wssize_toggle,
            debug_log_toggle=widgets.debug_log_toggle,
        )

        self.assertEqual(widgets.program_settings_card.titleLabel.text(), "Startup and behavior")
        self.assertEqual(widgets.windows_settings_card.titleLabel.text(), "Windows and blocking")
        self.assertEqual(widgets.additional_settings_card.titleLabel.text(), "Fine-tuning the bypass")


if __name__ == "__main__":
    unittest.main()
