"""Guard против NameError на именах, импортированных только под TYPE_CHECKING.

История бага: в blockcheck/dns_integrity.py ``Callable`` импортировался внутри
``if TYPE_CHECKING:``, но файл не имел ``from __future__ import annotations``.
Аннотации в таком файле вычисляются при импорте модуля, поэтому сборка падала
на старте BlockCheck:

    File "...\\blockcheck\\dns_integrity.py", line 144, in <module ...>
    NameError: name 'Callable' is not defined

Тест ловит три варианта одной и той же ошибки:
  * аннотация в файле без ``from __future__ import annotations``;
  * имя в runtime-позиции (базовый класс, декоратор, значение по умолчанию,
    псевдоним типа) — там future-импорт не спасает;
  * присваивание вида ``Handler = Callable[[int], None]`` на уровне модуля.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

SKIP_DIR_PARTS = {".claude", "__pycache__", "build", "dist", ".git"}


def _names_in(node: ast.AST | None) -> set[str]:
    found: set[str] = set()
    if node is None:
        return found
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            base: ast.AST = child
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name):
                found.add(base.id)
    return found


def _collect(tree: ast.Module) -> tuple[set[str], set[str], bool]:
    """Возвращает (имена только из TYPE_CHECKING, имена из runtime, есть ли future)."""
    type_checking: set[str] = set()
    runtime: set[str] = set()
    has_future = False

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(alias.name == "annotations" for alias in node.names):
                has_future = True

    def walk(body: list[ast.stmt], in_type_checking: bool) -> None:
        for node in body:
            if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.dump(node.test):
                walk(node.body, True)
                walk(node.orelse, in_type_checking)
                continue

            if isinstance(node, (ast.Import, ast.ImportFrom)):
                target = type_checking if in_type_checking else runtime
                for alias in node.names:
                    target.add((alias.asname or alias.name).split(".")[0])
                continue

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not in_type_checking:
                    runtime.add(node.name)
                walk(node.body, in_type_checking)
                continue

            if isinstance(node, ast.Assign) and not in_type_checking:
                for target_node in node.targets:
                    runtime.update(_names_in(target_node))

            for field in ("body", "orelse", "finalbody"):
                nested = getattr(node, field, None)
                if nested:
                    walk(list(nested), in_type_checking)
            for handler in getattr(node, "handlers", []) or []:
                walk(list(handler.body), in_type_checking)

    walk(tree.body, False)
    return type_checking, runtime, has_future


def _scan(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, str(path))

    type_checking, runtime, has_future = _collect(tree)
    guarded = type_checking - runtime
    if not guarded:
        return []

    problems: list[str] = []

    def report(kind: str, lineno: int, names: set[str]) -> None:
        problems.append(f"{path.name}:{lineno}: {kind} использует {sorted(names)}")

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            annotations = [
                arg.annotation
                for arg in [
                    *args.posonlyargs, *args.args, *args.kwonlyargs,
                    args.vararg, args.kwarg,
                ]
                if arg is not None and arg.annotation is not None
            ]
            if node.returns is not None:
                annotations.append(node.returns)

            if not has_future:
                for annotation in annotations:
                    bad = _names_in(annotation) & guarded
                    if bad:
                        report("аннотация без from __future__ import annotations",
                               annotation.lineno, bad)

            defaults = [*args.defaults, *[d for d in args.kw_defaults if d is not None]]
            for default in defaults:
                bad = _names_in(default) & guarded
                if bad:
                    report("значение по умолчанию (вычисляется всегда)", default.lineno, bad)

            for decorator in node.decorator_list:
                bad = _names_in(decorator) & guarded
                if bad:
                    report("декоратор (вычисляется всегда)", decorator.lineno, bad)

        elif isinstance(node, ast.ClassDef):
            for base in [*node.bases, *node.keywords, *node.decorator_list]:
                bad = _names_in(base) & guarded
                if bad:
                    report("объявление класса (вычисляется всегда)", node.lineno, bad)

        elif isinstance(node, ast.AnnAssign):
            if not has_future and node.annotation is not None:
                bad = _names_in(node.annotation) & guarded
                if bad:
                    report("аннотация без from __future__ import annotations",
                           node.lineno, bad)
            if node.value is not None:
                bad = _names_in(node.value) & guarded
                if bad:
                    report("значение присваивания (вычисляется всегда)", node.lineno, bad)

        elif isinstance(node, ast.Assign):
            bad = _names_in(node.value) & guarded
            if bad:
                report("псевдоним типа (вычисляется всегда)", node.lineno, bad)

    # Убираем дубли, сохраняя порядок
    seen: set[str] = set()
    unique: list[str] = []
    for problem in problems:
        if problem not in seen:
            seen.add(problem)
            unique.append(problem)
    return unique


class TypeCheckingRuntimeNameTests(unittest.TestCase):
    def test_no_type_checking_name_is_evaluated_at_runtime(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src"
        offenders: list[str] = []

        for path in sorted(root.rglob("*.py")):
            if any(part in SKIP_DIR_PARTS for part in path.parts):
                continue
            offenders.extend(_scan(path))

        self.assertEqual(
            offenders, [],
            "имя из блока TYPE_CHECKING вычисляется во время выполнения — "
            "модуль упадёт с NameError при импорте:\n" + "\n".join(offenders),
        )

    def test_detector_catches_the_original_dns_integrity_bug(self) -> None:
        """Проверяем сам детектор на воспроизведённом виде исходного бага."""
        import tempfile

        broken = (
            "from typing import TYPE_CHECKING\n"
            "\n"
            "if TYPE_CHECKING:\n"
            "    from collections.abc import Callable\n"
            "\n"
            "def check(callback: Callable[[str], None] | None = None) -> None:\n"
            "    pass\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dns_integrity_like.py"
            path.write_text(broken, encoding="utf-8")
            problems = _scan(path)

        self.assertTrue(problems, "детектор не заметил исходный баг")
        self.assertIn("Callable", problems[0])

        fixed = "from __future__ import annotations\n\n" + broken
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dns_integrity_like.py"
            path.write_text(fixed, encoding="utf-8")
            self.assertEqual(_scan(path), [], "детектор ложно срабатывает после фикса")


if __name__ == "__main__":
    unittest.main()
