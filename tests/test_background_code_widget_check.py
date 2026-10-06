"""Архитектурная проверка: фоновый код не создаёт элементы окна.

Создание элемента окна из фонового потока на Windows останавливает программу
целиком (Dev 21.1.7.19). Проверка получает синтетический исходник с нарушением
и должна его найти, а на правильном варианте — промолчать.
"""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path

from app import architecture_checks as checks

REPO_ROOT = checks.REPO_ROOT


def _problems(rel_path: str, code: str):
    return checks.check_background_code_does_not_create_widgets([(REPO_ROOT / rel_path, textwrap.dedent(code))])


class BackgroundCodeWidgetCheckTests(unittest.TestCase):
    def test_infobar_in_a_post_startup_task_is_caught(self) -> None:
        code = """
            def _run_refresh():
                InfoBar.info(title="Готово", content="Текст", parent=window)
        """
        problems = _problems("src/main/post_startup_builtin_preset_refresh.py", code)

        self.assertEqual(len(problems), 1)
        self.assertIn("фоновый код создаёт элемент окна", problems[0].message)

    def test_notice_through_the_notification_center_is_allowed(self) -> None:
        code = """
            def _run_refresh():
                notify(advisory_notification(level="info", title="Готово", content="Текст"))
                log(f"Ошибка при показе InfoBar: {exc}", "ERROR")
        """
        self.assertEqual(_problems("src/main/post_startup_builtin_preset_refresh.py", code), [])

    def test_worker_and_loader_modules_are_background_code(self) -> None:
        code = """
            def run(self):
                box = MessageBox("Заголовок", "Текст", self._parent)
        """
        for rel_path in (
            "src/presets/user_presets_action_workers.py",
            "src/profile/profile_setup_loader.py",
            "src/dns/workers.py",
        ):
            with self.subTest(path=rel_path):
                self.assertEqual(len(_problems(rel_path, code)), 1)

    def test_any_module_with_a_qthread_subclass_is_background_code(self) -> None:
        code = """
            from qfluentwidgets import InfoBar

            class Checker(QThread):
                def run(self):
                    InfoBar.error("Ошибка", "Текст")
        """
        problems = _problems("src/dns/ui/dns_check_page.py", code)

        # И импорт виджетов, и сама плашка.
        self.assertEqual(len(problems), 2)

    def test_ordinary_page_may_show_an_infobar(self) -> None:
        code = """
            from qfluentwidgets import InfoBar

            class Page(BasePage):
                def _on_saved(self):
                    InfoBar.success("Готово", "Сохранено", parent=self.window())
        """
        self.assertEqual(_problems("src/dns/ui/page.py", code), [])

    def test_commented_out_line_is_not_a_violation(self) -> None:
        code = """
            def _run():
                # InfoBar.info("так нельзя")
                pass
        """
        self.assertEqual(_problems("src/main/post_startup_update.py", code), [])

    def test_project_has_background_modules_and_they_are_clean(self) -> None:
        sources = [
            (path, path.read_text(encoding="utf-8", errors="replace")) for path in checks._python_files()
        ]
        background = [
            path
            for path, source in sources
            if checks.is_background_code_module(path.relative_to(REPO_ROOT).as_posix(), source)
        ]

        # Проверка действительно что-то проверяет, а не молчит на пустом наборе.
        self.assertGreater(len(background), 50)
        self.assertTrue(any(Path(path).name == "post_startup_builtin_preset_refresh.py" for path in background))
        self.assertEqual(checks.check_background_code_does_not_create_widgets(sources), [])

    def test_check_is_part_of_the_project_run(self) -> None:
        import inspect

        self.assertIn(
            "check_background_code_does_not_create_widgets(",
            inspect.getsource(checks.run_checks),
        )


if __name__ == "__main__":
    unittest.main()
