from __future__ import annotations

import ast
import pathlib
import unittest

PACKAGE_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "ui" / "code_editor"

# Пакет — общий UI-примитив: он не должен знать о пресетах, профилях,
# запуске winws и файловых операциях. Иначе его нельзя переиспользовать
# в других страницах, ради чего он и выделен.
FORBIDDEN_PREFIXES = (
    "presets",
    "profile",
    "winws_runtime",
    "orchestra",
    "settings",
    "config",
    "log",
)


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


class CodeEditorDependencyBoundaryTests(unittest.TestCase):
    def test_package_does_not_depend_on_feature_modules(self) -> None:
        offenders: dict[str, set[str]] = {}
        for path in sorted(PACKAGE_DIR.glob("*.py")):
            forbidden = {
                module
                for module in _imported_modules(path)
                if module.split(".")[0] in FORBIDDEN_PREFIXES
            }
            if forbidden:
                offenders[path.name] = forbidden

        self.assertEqual(offenders, {})

    def test_package_touches_no_filesystem(self) -> None:
        for path in sorted(PACKAGE_DIR.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            for marker in ("open(", "Path(", "os.path", "read_text", "write_text"):
                self.assertNotIn(marker, source, f"{path.name} работает с файлами напрямую")

    def test_theme_signals_go_through_theme_refresh_binding(self) -> None:
        # Прямые подписки на qconfig.themeChanged в Nuitka-сборке не рвутся
        # при удалении виджета — правило проекта требует ThemeRefreshBinding.
        for path in sorted(PACKAGE_DIR.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("themeChanged.connect", source)
            self.assertNotIn("themeColorChanged.connect", source)

        editor_source = (PACKAGE_DIR / "editor.py").read_text(encoding="utf-8")
        self.assertIn("ThemeRefreshBinding", editor_source)


if __name__ == "__main__":
    unittest.main()
