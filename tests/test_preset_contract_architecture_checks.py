"""Архитектурные проверки договора «Пресет — точка истины» ловят нарушения.

Каждая проверка получает синтетический исходник с нарушением и должна его
найти, а на правильном варианте — промолчать. Отдельно: весь проект проходит
app.architecture_checks, и модуль договора импортируется без зависимостей
(его читает CI на голом python3).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from app import architecture_checks as checks
from presets.preset_contract import GENERATED_CONFIG_EXEMPTIONS


REPO_ROOT = checks.REPO_ROOT


def _source(rel_path: str, code: str) -> tuple[Path, str]:
    return REPO_ROOT / rel_path, textwrap.dedent(code)


class LaunchCommandCheckTests(unittest.TestCase):
    def _problems(self, rel_path: str, code: str):
        return checks.check_winws_launch_command_is_exe_plus_at_config([_source(rel_path, code)])

    def test_command_from_artifact_is_allowed(self) -> None:
        code = """
            def spawn(self, artifact):
                cmd = [self.winws_exe, *artifact.launch_args]
        """
        self.assertEqual(self._problems("src/winws_runtime/runners/zapret2_runner.py", code), [])

    def test_extra_argument_after_config_is_caught(self) -> None:
        code = """
            def spawn(self, artifact):
                cmd = [self.winws_exe, *artifact.launch_args, "--wf-dup-check=0"]
        """
        self.assertEqual(len(self._problems("src/winws_runtime/runners/zapret2_runner.py", code)), 1)

    def test_argv_built_without_at_config_is_caught(self) -> None:
        code = """
            def spawn(self, args):
                cmd = [self.winws_exe, *args]
                other = [self._winws2_exe] + args
        """
        self.assertEqual(len(self._problems("src/winws_runtime/runners/zapret1_runner.py", code)), 2)

    def test_own_at_config_only_in_dry_run_function_or_generated_config_module(self) -> None:
        code = """
            def _run_preset_dry_run_locked(self, path):
                return [self.winws_exe, f"@{path}"]

            def spawn(self, path):
                return [self.winws_exe, f"@{path}"]
        """
        problems = self._problems("src/winws_runtime/runners/zapret1_runner.py", code)
        self.assertEqual([problem.line for problem in problems], [6])
        for rel_path in GENERATED_CONFIG_EXEMPTIONS:
            with self.subTest(module=rel_path):
                self.assertEqual(self._problems(rel_path, code), [])

    def test_artifact_launch_args_must_be_single_at_config(self) -> None:
        code = """
            def compile(self, p, path, args):
                PreparedPresetArtifact(p, None, "", tuple(), False, "")
                PreparedPresetArtifact(preset_path=p, launch_args=(f"@{path}",))
                PreparedPresetArtifact(preset_path=p, launch_args=tuple(args))
                PreparedPresetArtifact(p, None, "", (f"@{path}", "--dry-run"), True, "")
        """
        problems = self._problems("src/winws_runtime/runners/zapret2_runner.py", code)
        self.assertEqual(sorted(problem.line for problem in problems), [5, 6])


class PresetFileWriteOwnershipCheckTests(unittest.TestCase):
    def _problems(self, rel_path: str, code: str):
        return checks.check_preset_files_written_only_by_owners([_source(rel_path, code)])

    def test_store_write_outside_owners_is_caught(self) -> None:
        code = """
            def apply(services, text):
                services.preset_file_store.update_preset("winws2", "a.txt", text, None)
        """
        self.assertEqual(len(self._problems("src/profile/user_profiles/fake.py", code)), 1)
        self.assertEqual(len(self._problems("src/blockcheck/fake.py", code)), 1)

    def test_owner_must_normalize_before_writing(self) -> None:
        bad = """
            def save(backend, text):
                return backend.preset_file_store.create_preset(backend.engine, "x", text)
        """
        good = """
            def save(backend, text):
                text = backend.normalize_source_text(text)
                return backend.preset_file_store.create_preset(backend.engine, "x", text)
        """
        self.assertEqual(len(self._problems("src/presets/preset_file_ops.py", bad)), 1)
        self.assertEqual(self._problems("src/presets/preset_file_ops.py", good), [])

    def test_normalization_after_the_write_does_not_count(self) -> None:
        code = """
            def save(backend, text):
                created = backend.preset_file_store.create_preset(backend.engine, "x", text)
                backend.normalize_source_text(text)
                return created
        """
        self.assertEqual(len(self._problems("src/presets/portable_archive.py", code)), 1)

    def test_write_source_only_inside_store(self) -> None:
        code = """
            def save(store, path, text):
                store._write_source(path, text)
        """
        self.assertEqual(len(self._problems("src/presets/file_service.py", code)), 1)
        self.assertEqual(self._problems("src/presets/file_store.py", code), [])

    def test_store_does_not_create_presets_on_its_own(self) -> None:
        # Так выглядел удалённый PresetFileStore.import_preset: ZIP писался
        # в файл пресета мимо нормализации сохранения.
        code = """
            class PresetFileStore:
                def import_preset(self, engine, src_path):
                    source_text = read_zip(src_path)
                    return self.create_preset(engine, "Imported", source_text, kind="imported")
        """
        self.assertEqual(len(self._problems("src/presets/file_store.py", code)), 1)

    def test_raw_write_to_preset_file_is_caught(self) -> None:
        code = """
            import os, shutil

            def prepare(preset_path, text, tmp):
                preset_path.write_text(text)
                with open(preset_path, "w") as f:
                    f.write(text)
                shutil.copyfile(tmp, preset_path)
                with open(preset_path, "r") as f:
                    f.read()
                with open(self.config_path, "w") as f:
                    f.write(text)
        """
        problems = self._problems("src/winws_runtime/flow/start_preparation.py", code)
        self.assertEqual(sorted(problem.line for problem in problems), [5, 6, 8])
        for rel_path in GENERATED_CONFIG_EXEMPTIONS:
            with self.subTest(module=rel_path):
                self.assertEqual(self._problems(rel_path, code), [])


class LaunchPreparationCheckTests(unittest.TestCase):
    REL = "src/winws_runtime/preset_launch_text.py"

    def _problems(self, code: str):
        return checks.check_launch_preparation_returns_source_text([_source(self.REL, code)])

    def test_unchanged_source_text_is_allowed(self) -> None:
        code = """
            def prepare(source_content, *, source_name=""):
                raw_text = str(source_content or "")
                validate(raw_text)
                return PreparedLaunchPresetText(text=raw_text)
        """
        self.assertEqual(self._problems(code), [])

    def test_rewritten_text_is_caught(self) -> None:
        code = """
            def prepare(source_content):
                raw_text = str(source_content or "")
                raw_text = ensure_lua_init(raw_text)
                return PreparedLaunchPresetText(text=raw_text)

            def prepare2(source_content):
                fixed = normalize(source_content)
                return PreparedLaunchPresetText(text=fixed)

            def prepare3(source_content):
                return PreparedLaunchPresetText(text=source_content + "--new")
        """
        self.assertEqual(sorted(problem.line for problem in self._problems(code)), [5, 9, 12])


class WholeProjectTests(unittest.TestCase):
    def test_project_passes_architecture_checks(self) -> None:
        problems = checks.run_checks()
        self.assertEqual([problem.format() for problem in problems], [])

    def test_contract_module_imports_without_project_dependencies(self) -> None:
        code = (
            "import sys; sys.path.insert(0, 'src'); import presets.preset_contract; "
            "print(sorted({m.split('.')[0] for m in sys.modules} & {'PyQt6', 'log', 'settings', 'profile', 'requests'}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "[]")


if __name__ == "__main__":
    unittest.main()
