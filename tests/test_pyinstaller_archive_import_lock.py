from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


class PyInstallerArchiveImportLockTests(unittest.TestCase):
    def test_installer_wraps_pyinstaller_archive_extract_once(self) -> None:
        from main import pyinstaller_archive_import_lock

        class FakeZlibArchiveReader:
            def extract(self, name):
                return f"module:{name}"

        fake_module = types.SimpleNamespace(ZlibArchiveReader=FakeZlibArchiveReader)
        previous_frozen = getattr(sys, "frozen", None)
        sys.frozen = True
        try:
            with patch.dict(sys.modules, {"pyimod01_archive": fake_module}):
                self.assertTrue(pyinstaller_archive_import_lock.install_pyinstaller_archive_import_lock())
                first_extract = FakeZlibArchiveReader.extract
                self.assertTrue(pyinstaller_archive_import_lock.install_pyinstaller_archive_import_lock())
                self.assertIs(FakeZlibArchiveReader.extract, first_extract)
                self.assertEqual(FakeZlibArchiveReader().extract("demo"), "module:demo")
        finally:
            if previous_frozen is None:
                try:
                    delattr(sys, "frozen")
                except AttributeError:
                    pass
            else:
                sys.frozen = previous_frozen
            pyinstaller_archive_import_lock._reset_for_tests()

    def test_prelaunch_installs_archive_lock_before_background_preload(self) -> None:
        from main import prelaunch

        calls: list[str] = []
        prelaunch._PRELAUNCH_DONE = False
        with (
            patch.object(prelaunch, "_set_workdir_to_app", side_effect=lambda: calls.append("workdir")),
            patch.object(prelaunch, "_install_crash_handler", side_effect=lambda: calls.append("crash")),
            patch.object(prelaunch, "install_pyinstaller_archive_import_lock", side_effect=lambda: calls.append("lock")),
            patch.object(prelaunch, "_preload_slow_modules", side_effect=lambda: calls.append("preload")),
        ):
            prelaunch.prepare_prelaunch()

        self.assertEqual(calls, ["workdir", "crash", "lock", "preload"])


if __name__ == "__main__":
    unittest.main()
