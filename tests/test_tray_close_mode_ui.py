from __future__ import annotations

import inspect
import unittest


class TrayCloseModeUiTests(unittest.TestCase):
    def test_control_pages_build_tray_close_mode_as_combo_with_three_options(self) -> None:
        import presets.ui.control.zapret1.sections_build as zapret1_sections
        import presets.ui.control.zapret2.sections_build as zapret2_sections

        for builder in (zapret1_sections.build_program_settings_group, zapret2_sections.build_program_settings_group):
            source = inspect.getsource(builder)
            self.assertIn("win11_combo_row_cls", source)
            self.assertIn('("Свернуть и крестик скрывают в трей", "minimize_and_close")', source)
            self.assertIn('("Только свернуть скрывает в трей", "minimize_only")', source)
            self.assertIn('("Не скрывать в трей", "normal")', source)


if __name__ == "__main__":
    unittest.main()
