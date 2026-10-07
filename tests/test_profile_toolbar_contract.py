from __future__ import annotations

import inspect
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from profile.ui import shell as profile_shell
from profile.ui import preset_setup_page
from presets.ui.common import user_presets_build
from presets.ui.common import user_presets_page


class ProfileToolbarContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_profile_toolbar_has_no_manual_refresh_button(self) -> None:
        source = inspect.getsource(profile_shell)

        self.assertNotIn("RefreshButton", source)
        self.assertNotIn("reload_btn", source)
        self.assertNotIn("on_reload", source)

    def test_github_buttons_use_direct_fluent_icons(self) -> None:
        profile_source = inspect.getsource(profile_shell.build_profile_shell)
        presets_source = inspect.getsource(user_presets_build.build_user_presets_page_shell)

        self.assertIn("request_btn = PrimaryPushButton(", profile_source)
        self.assertIn("icon=FluentIcon.GITHUB", profile_source)
        self.assertIn("get_configs_btn = toolbar_layout.create_action_button(", presets_source)
        self.assertIn("FluentIcon.GITHUB", presets_source)

    def test_profile_request_button_opens_github_form_not_info_popup(self) -> None:
        source = inspect.getsource(preset_setup_page.PresetSetupPageBase._build_content)

        self.assertIn("on_open_profile_request_form=self._open_profile_request_form", source)
        self.assertIn("on_show_info_popup=self._show_profile_info", source)
        self.assertNotIn("on_open_profile_request_form=self._show_profile_info", source)

    def test_user_presets_list_reserves_space_for_visible_fluent_scrollbar(self) -> None:
        presets_source = inspect.getsource(user_presets_build.build_user_presets_page_shell)

        self.assertIn("reserve_vertical_space=True", presets_source)

    def test_user_presets_status_icon_is_next_to_title_not_in_toolbar(self) -> None:
        source = inspect.getsource(user_presets_page.UserPresetsPageBase._build_ui)
        install_source = inspect.getsource(user_presets_page.UserPresetsPageBase._install_title_status_icon)

        self.assertIn("self._install_title_status_icon()", source)
        self.assertIn("title_layout.addWidget(self._preset_status_icon", install_source)
        self.assertNotIn("set_inline_widget(self._preset_status", source)
        self.assertNotIn("self.add_widget(self._preset_status_bar)", source)


if __name__ == "__main__":
    unittest.main()
