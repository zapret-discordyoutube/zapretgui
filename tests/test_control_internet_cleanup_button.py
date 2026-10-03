from __future__ import annotations

import inspect
import unittest

import presets.ui.control.zapret1.page as zapret1_page
import presets.ui.control.zapret2.page as zapret2_page
from presets.ui.control.quick_actions import quick_action_specs
from presets.ui.control.windows_features.runtime import ControlPageWindowsFeatureMixin


class ControlInternetCleanupButtonTests(unittest.TestCase):
    def test_zapret_control_pages_add_internet_cleanup_action_tile(self) -> None:
        for prefix in ("page.winws1_control", "page.winws2_control"):
            keys = [spec.key for spec in quick_action_specs(prefix)]
            self.assertIn("internet_cleanup", keys)

        for page_cls in (zapret1_page.Zapret1ModeControlPage, zapret2_page.Zapret2ModeControlPage):
            source = inspect.getsource(page_cls._build_ui)
            self.assertIn("build_quick_actions(", source)
            self.assertIn("on_open_internet_cleanup=self._on_internet_cleanup_clicked", source)
            self.assertIn("self.internet_cleanup_card = quick_actions.internet_cleanup_card", source)

    def test_windows_feature_mixin_waits_for_internet_cleanup_worker_on_cleanup(self) -> None:
        source = inspect.getsource(ControlPageWindowsFeatureMixin._stop_internet_cleanup_worker)

        self.assertIn("blocking=True", source)
        self.assertIn("wait_timeout_ms=45000", source)
        self.assertIn("Internet cleanup worker", source)


if __name__ == "__main__":
    unittest.main()
