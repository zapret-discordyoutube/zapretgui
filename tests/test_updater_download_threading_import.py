from __future__ import annotations

import ast
from pathlib import Path
import unittest


class UpdaterDownloadThreadingImportTests(unittest.TestCase):
    def _source(self) -> str:
        root = Path(__file__).resolve().parents[1]
        return (root / "src" / "updater" / "update_pipeline.py").read_text(encoding="utf-8")

    def test_pipeline_imports_threading_for_segments_and_cancellation(self) -> None:
        tree = ast.parse(self._source())
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertIn("threading", imported_names)
        self.assertIn("threading.Lock()", self._source())
        self.assertIn("threading.Event()", self._source())


if __name__ == "__main__":
    unittest.main()
