from __future__ import annotations

import types
import unittest
from pathlib import Path

from startup.windows_version_guard import (
    MIN_WINDOWS_10_1809_BUILD,
    evaluate_windows_support,
)


ROOT = Path(__file__).resolve().parents[1]


def _windows_version(major: int, minor: int, build: int = 0):
    return types.SimpleNamespace(major=major, minor=minor, build=build)


class EarlyWindowsVersionGuardTests(unittest.TestCase):
    def test_rejects_windows_7_8_and_81_with_console_version_guidance(self) -> None:
        cases = [
            (_windows_version(6, 1), "Windows 7"),
            (_windows_version(6, 2), "Windows 8"),
            (_windows_version(6, 3), "Windows 8.1"),
        ]

        for version, os_name in cases:
            with self.subTest(os_name=os_name):
                result = evaluate_windows_support(version, platform_name="win32")

                self.assertFalse(result.supported)
                self.assertEqual(result.os_name, os_name)
                self.assertIn("GUI-версия", result.message)
                self.assertIn("консольная версия", result.message)
                self.assertIn("https://t.me/bypassblock/666", result.message)

    def test_rejects_windows_10_before_1809(self) -> None:
        result = evaluate_windows_support(
            _windows_version(10, 0, MIN_WINDOWS_10_1809_BUILD - 1),
            platform_name="win32",
        )

        self.assertFalse(result.supported)
        self.assertEqual(result.os_name, "Windows 10 до 1809")
        self.assertIn("Windows 10 1809", result.message)
        self.assertIn(str(MIN_WINDOWS_10_1809_BUILD), result.message)
        self.assertIn("консольная версия", result.message)

    def test_accepts_windows_10_1809_and_newer(self) -> None:
        supported_versions = [
            _windows_version(10, 0, MIN_WINDOWS_10_1809_BUILD),
            _windows_version(10, 0, 19045),
            _windows_version(10, 0, 22000),
        ]

        for version in supported_versions:
            with self.subTest(build=version.build):
                result = evaluate_windows_support(version, platform_name="win32")
                self.assertTrue(result.supported)
                self.assertEqual(result.message, "")

    def test_non_windows_platform_is_not_blocked(self) -> None:
        result = evaluate_windows_support(
            _windows_version(0, 0, 0),
            platform_name="linux",
        )

        self.assertTrue(result.supported)
        self.assertEqual(result.message, "")

    def test_guard_module_has_no_qt_dependency(self) -> None:
        source = (ROOT / "src" / "startup" / "windows_version_guard.py").read_text(encoding="utf-8")

        self.assertNotIn("PyQt6", source)
        self.assertNotIn("qfluentwidgets", source)


if __name__ == "__main__":
    unittest.main()
