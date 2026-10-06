"""До появления окна версию Windows не спрашивают через WMI.

platform.platform(), platform.release() и platform.version() на Windows идут
в WMI. Замер на win10: четыре запроса и ~75 мс до первого кадра на быстром
компьютере. Спрашивали трое: заголовок крэш-лога, darkdetect (его тянет
qfluentwidgets) и проверка «Windows 7?» в библиотеке окна.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import inspect
import platform
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class CrashLogSessionHeaderTests(unittest.TestCase):
    def test_session_header_does_not_query_wmi(self) -> None:
        from log import crash_handler

        with (
            patch.object(sys, "getwindowsversion", create=True, return_value=SimpleNamespace(major=10, minor=0, build=19045)),
            patch.object(platform, "platform", side_effect=AssertionError("WMI до появления окна")),
            patch.object(platform, "release", side_effect=AssertionError("WMI до появления окна")),
        ):
            line = crash_handler._session_platform_line()

        self.assertEqual(line, "Windows 10.0.19045")

    def test_install_writes_cheap_header(self) -> None:
        from log import crash_handler

        source = inspect.getsource(crash_handler.install_crash_handler)

        self.assertIn("_session_platform_line()", source)
        self.assertNotIn("platform.platform()", source)

    def test_non_windows_falls_back_to_platform_name(self) -> None:
        from log import crash_handler

        if hasattr(sys, "getwindowsversion"):
            self.skipTest("проверка для систем без sys.getwindowsversion")
        self.assertEqual(crash_handler._session_platform_line(), sys.platform)


class _RecordingDarkdetectFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Подставной darkdetect: запоминает, что platform отвечал при его импорте."""

    def __init__(self) -> None:
        self.seen: dict[str, str] = {}

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "darkdetect":
            return None
        return importlib.machinery.ModuleSpec(fullname, self)

    def create_module(self, spec):
        return types.ModuleType(spec.name)

    def exec_module(self, module) -> None:
        self.seen["release"] = platform.release()
        self.seen["version"] = platform.version()


class DarkdetectPreloadTests(unittest.TestCase):
    def test_darkdetect_gets_version_without_wmi(self) -> None:
        from main import qt_runtime

        finder = _RecordingDarkdetectFinder()
        saved_module = sys.modules.pop("darkdetect", None)
        sys.meta_path.insert(0, finder)
        original_release, original_version = platform.release, platform.version
        try:
            with (
                patch.object(sys, "platform", "win32"),
                patch.object(sys, "getwindowsversion", create=True, return_value=SimpleNamespace(major=10, minor=0, build=22631)),
            ):
                qt_runtime.preload_darkdetect_without_wmi()
        finally:
            sys.meta_path.remove(finder)
            sys.modules.pop("darkdetect", None)
            if saved_module is not None:
                sys.modules["darkdetect"] = saved_module

        # darkdetect проверяет «release — число не меньше 10» и номер сборки
        # третьим полем версии.
        self.assertEqual(finder.seen, {"release": "10", "version": "10.0.22631"})
        self.assertGreaterEqual(int(finder.seen["version"].split(".")[2]), 14393)
        # Подмена действует только на время импорта.
        self.assertIs(platform.release, original_release)
        self.assertIs(platform.version, original_version)

    def test_other_systems_are_left_alone(self) -> None:
        from main import qt_runtime

        with (
            patch.object(sys, "platform", "linux"),
            patch.object(platform, "release", side_effect=AssertionError("не должно вызываться")),
        ):
            qt_runtime.preload_darkdetect_without_wmi()


class FramelessWindowsVersionProbeTests(unittest.TestCase):
    def test_is_win7_answers_without_platform_query(self) -> None:
        from main import qt_runtime

        def _slow_is_win7():
            raise AssertionError("WMI в конструкторе окна")

        win32_utils = types.ModuleType("qframelesswindow.utils.win32_utils")
        win32_utils.isWin7 = _slow_is_win7
        utils = types.ModuleType("qframelesswindow.utils")
        utils.win32_utils = win32_utils
        package = types.ModuleType("qframelesswindow")
        package.utils = utils

        with patch.dict(
            sys.modules,
            {
                "qframelesswindow": package,
                "qframelesswindow.utils": utils,
                "qframelesswindow.utils.win32_utils": win32_utils,
            },
        ):
            qt_runtime.skip_frameless_wmi_version_probe()

        self.assertIs(win32_utils.isWin7(), False)

    def test_runtime_installs_both_before_first_fluent_import(self) -> None:
        from main import qt_runtime

        source = inspect.getsource(qt_runtime.ensure_qt_runtime)

        self.assertIn("preload_darkdetect_without_wmi()", source)
        self.assertIn("skip_frameless_wmi_version_probe()", source)
        self.assertLess(
            source.index("preload_darkdetect_without_wmi()"),
            source.index("install_fluent_translator"),
        )


if __name__ == "__main__":
    unittest.main()
