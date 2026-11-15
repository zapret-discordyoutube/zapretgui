"""HiDPI and text smoothing are set in main.qt_runtime before the first window.

The rounding rule only counts before QApplication exists, so the checks run in a
fresh interpreter (a pytest process may already own a QApplication).
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"


def _run(code: str, **env: str) -> str:
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(PROJECT_SRC), **env}
    result = subprocess.run(
        [sys.executable, "-I", "-c", textwrap.dedent(code)],
        capture_output=True, text=True, env=environment, timeout=60, check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


class QtRuntimeDisplayTests(unittest.TestCase):
    def test_fractional_scale_is_kept_even_if_user_environment_says_round(self) -> None:
        answer = _run(
            """
            import sys
            sys.path.insert(0, %r)
            from main import qt_runtime
            from PyQt6.QtGui import QGuiApplication
            from PyQt6.QtWidgets import QApplication
            applied = qt_runtime.apply_high_dpi_policy()
            app = QApplication([])
            print(applied, QGuiApplication.highDpiScaleFactorRoundingPolicy().name)
            """ % str(PROJECT_SRC),
            QT_SCALE_FACTOR_ROUNDING_POLICY="Round",
        )
        self.assertEqual(answer, "True PassThrough")

    def test_policy_is_not_claimed_after_the_application_exists(self) -> None:
        answer = _run(
            """
            import sys
            sys.path.insert(0, %r)
            from main import qt_runtime
            from PyQt6.QtWidgets import QApplication
            app = QApplication([])
            print(qt_runtime.apply_high_dpi_policy())
            """ % str(PROJECT_SRC),
        )
        self.assertEqual(answer, "False")

    def test_default_font_asks_for_antialiasing_once(self) -> None:
        answer = _run(
            """
            import sys
            sys.path.insert(0, %r)
            from main import qt_runtime
            from PyQt6.QtGui import QFont
            from PyQt6.QtWidgets import QApplication
            app = QApplication([])
            first = qt_runtime.apply_text_antialiasing(app)
            second = qt_runtime.apply_text_antialiasing(app)
            print(first, second, app.font().styleStrategy() == QFont.StyleStrategy.PreferAntialias)
            """ % str(PROJECT_SRC),
        )
        self.assertEqual(answer, "True False True")

    def test_qt5_attributes_are_not_set_any_more(self) -> None:
        import ast

        tree = ast.parse((PROJECT_SRC / "main" / "qt_runtime.py").read_text(encoding="utf-8"))
        strings = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        # Docstrings may name the old settings; code must not use them.
        code_strings = {value for value in strings if "\n" not in value}
        for dead in ("AA_EnableHighDpiScaling", "AA_UseHighDpiPixmaps", "QT_AUTO_SCREEN_SCALE_FACTOR"):
            self.assertNotIn(dead, code_strings)


if __name__ == "__main__":
    unittest.main()
