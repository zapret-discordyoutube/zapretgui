from __future__ import annotations

import ast
from pathlib import Path
import unittest


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"

ALLOWED_UI_IMPORTS = {
    "ui.latest_value_worker_state",
    "app.ui_texts",
    "ui.one_shot_worker_runtime",
    "ui.queued_worker_state",
    # Инфраструктура запуска воркеров, а не страницы: гейт параллелизма и
    # проверка потокового контракта.
    "ui.background_worker_gate",
    "ui.ui_thread_guard",
}


class WorkerUiBoundaryTests(unittest.TestCase):
    def test_worker_modules_do_not_import_page_ui_packages(self) -> None:
        offenders: list[str] = []
        for path in self._iter_worker_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            rel_path = path.relative_to(SRC_ROOT).as_posix()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        module = str(alias.name or "")
                        if self._is_forbidden_ui_import(module):
                            offenders.append(f"{rel_path}:{node.lineno}:import {module}")
                elif isinstance(node, ast.ImportFrom):
                    module = str(node.module or "")
                    if self._is_forbidden_ui_import(module):
                        offenders.append(f"{rel_path}:{node.lineno}:from {module} import ...")

        self.assertEqual([], offenders)

    def _iter_worker_modules(self) -> list[Path]:
        return sorted(
            path
            for path in SRC_ROOT.rglob("*.py")
            if "worker" in path.name or path.name.endswith("workers.py")
        )

    def _is_forbidden_ui_import(self, module: str) -> bool:
        clean = str(module or "")
        if clean in ALLOWED_UI_IMPORTS:
            return False
        return clean.startswith("ui.") or ".ui." in clean or clean.endswith(".ui")


if __name__ == "__main__":
    unittest.main()
