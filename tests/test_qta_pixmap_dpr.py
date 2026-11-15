"""Значки qtawesome остаются чёткими при 125–150 %: картинка 16 логических точек имеет 20 (24) физических.

QIcon.pixmap(w, h) в Qt 6 сам берёт коэффициент экрана. Тест закрепляет это: если кто-то заменит
вызов на ручную картинку QPixmap(w, h), значки при дробном масштабе станут мыльными.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"


def _run(scale: str | None, code: str) -> str:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(PROJECT_SRC)}
    env.pop("QT_SCREEN_SCALE_FACTORS", None)
    if scale:
        env["QT_SCREEN_SCALE_FACTORS"] = scale
    result = subprocess.run(
        [sys.executable, "-I", "-c", textwrap.dedent(code)],
        capture_output=True, text=True, env=env, timeout=90, check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip().splitlines()[-1]


_CODE = """
import sys
sys.path.insert(0, %r)
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from ui.theme import get_cached_qta_pixmap
parts = []
for size in (16, 18, 24):
    pm = get_cached_qta_pixmap("fa5s.lock", color="#ff0000", size=size)
    logical = pm.deviceIndependentSize()
    parts.append(f"{size}:{pm.width()}x{pm.height()}@{round(pm.devicePixelRatio(), 3)}->{round(logical.width(), 2)}")
print(" ".join(parts))
""" % str(PROJECT_SRC)


class QtaPixmapPixelRatioTests(unittest.TestCase):
    def test_plain_screen_keeps_exact_pixels(self) -> None:
        self.assertEqual(_run(None, _CODE), "16:16x16@1.0->16.0 18:18x18@1.0->18.0 24:24x24@1.0->24.0")

    def test_125_percent_screen_gets_more_pixels_and_the_same_logical_size(self) -> None:
        answer = _run("1.25", _CODE)
        first, _second, third = answer.split()
        self.assertTrue(first.startswith("16:20x20@1.25->16"), first)
        self.assertTrue(third.startswith("24:30x30@1.25->24"), third)

    def test_150_percent_screen(self) -> None:
        answer = _run("1.5", _CODE)
        self.assertTrue(answer.split()[0].startswith("16:24x24@1.5->16"), answer)


if __name__ == "__main__":
    unittest.main()
