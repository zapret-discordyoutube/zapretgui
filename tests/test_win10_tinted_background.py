import unittest
from types import SimpleNamespace
from unittest.mock import patch


class Win10TintedBackgroundTests(unittest.TestCase):
    def test_standard_background_reapplies_aero_effect_on_windows_10(self) -> None:
        import settings.appearance as appearance_settings
        import ui.theme as theme

        class _Window:
            def __init__(self) -> None:
                self.mica_values = []

            def setMicaEffectEnabled(self, value) -> None:
                self.mica_values.append(bool(value))

            def setCustomBackgroundColor(self, *_args) -> None:
                raise AssertionError("Win10 standard background must use the aero path")

        appearance_settings.store_warmed_background_preset("standard")
        appearance_settings.store_warmed_window_opacity(37)

        window = _Window()
        with (
            patch.object(theme.sys, "platform", "win32"),
            patch.object(
                theme.sys,
                "getwindowsversion",
                return_value=SimpleNamespace(build=19045),
                create=True,
            ),
            patch.object(theme, "apply_aero_effect") as apply_aero,
        ):
            theme.apply_window_background(window)

        self.assertEqual(window.mica_values, [False])
        apply_aero.assert_called_once_with(window, 37)


if __name__ == "__main__":
    unittest.main()
