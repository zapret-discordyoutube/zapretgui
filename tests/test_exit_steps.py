"""Шаги выхода (utils/exit_steps.py) и проверка, что у выхода один владелец."""

from __future__ import annotations

import textwrap
import unittest
from unittest.mock import patch

from app import architecture_checks as checks
from utils import exit_steps

REPO_ROOT = checks.REPO_ROOT


class ExitStepsTests(unittest.TestCase):
    def setUp(self) -> None:
        # Свой пустой список: настоящие шаги (журнал, база настроек) в общем
        # процессе тестов выполнять нельзя.
        patcher = patch.object(exit_steps, "_steps", [])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_steps_run_in_reverse_order_of_registration(self) -> None:
        calls: list[str] = []
        exit_steps.register_exit_step("журнал", lambda: calls.append("журнал"))
        exit_steps.register_exit_step("настройки", lambda: calls.append("настройки"))
        exit_steps.register_exit_step("мьютекс", lambda: calls.append("мьютекс"))

        exit_steps.run_exit_steps()

        # Журнал зарегистрирован первым и закрывается последним.
        self.assertEqual(calls, ["мьютекс", "настройки", "журнал"])

    def test_each_step_runs_once(self) -> None:
        calls: list[str] = []
        exit_steps.register_exit_step("шаг", lambda: calls.append("шаг"))

        exit_steps.run_exit_steps()
        exit_steps.run_exit_steps()

        self.assertEqual(calls, ["шаг"])

    def test_failed_step_does_not_stop_the_rest(self) -> None:
        calls: list[str] = []

        def _broken() -> None:
            raise RuntimeError("сбой шага")

        exit_steps.register_exit_step("журнал", lambda: calls.append("журнал"))
        exit_steps.register_exit_step("сломанный", _broken)

        with patch.object(exit_steps.traceback, "print_exc"), patch("builtins.print"):
            exit_steps.run_exit_steps()

        self.assertEqual(calls, ["журнал"])

    def test_step_registered_by_another_step_still_runs(self) -> None:
        calls: list[str] = []
        exit_steps.register_exit_step(
            "первый",
            lambda: exit_steps.register_exit_step("поздний", lambda: calls.append("поздний")),
        )

        exit_steps.run_exit_steps()

        self.assertEqual(calls, ["поздний"])


def _problems(rel_path: str, code: str):
    return checks.check_process_exit_has_single_owners([(REPO_ROOT / rel_path, textwrap.dedent(code))])


class ExitOwnershipCheckTests(unittest.TestCase):
    def test_direct_atexit_is_caught(self) -> None:
        problems = _problems(
            "src/log/some_log.py",
            """
            import atexit
            atexit.register(close_files)
            """,
        )
        self.assertEqual(len(problems), 2)
        self.assertIn("register_exit_step", problems[0].message)

    def test_closing_the_application_outside_lifecycle_is_caught(self) -> None:
        problems = _problems(
            "src/winws_runtime/runtime/lifecycle_feedback.py",
            """
            def on_stop_and_exit_finished(owner):
                QApplication.closeAllWindows()
                QApplication.quit()
            """,
        )
        self.assertEqual(len(problems), 2)
        self.assertIn("ApplicationLifecycle", problems[0].message)

    def test_hard_exit_outside_process_exit_is_caught(self) -> None:
        problems = _problems("src/updater/somewhere.py", "os._exit(0)\n")
        self.assertEqual(len(problems), 1)

    def test_owners_and_plain_mentions_are_allowed(self) -> None:
        self.assertEqual(_problems("src/utils/exit_steps.py", "import atexit\natexit.register(run)\n"), [])
        self.assertEqual(_problems("src/main/application_lifecycle.py", "QApplication.quit()\n"), [])
        self.assertEqual(_problems("src/main/process_exit.py", "os._exit(int(exit_code))\n"), [])
        # Остановка потока и упоминание в тексте — не закрытие программы.
        self.assertEqual(
            _problems("src/ui/page.py", "thread.quit()\n# concurrent.futures вешает atexit-хук\n"),
            [],
        )

    def test_current_sources_have_single_owners(self) -> None:
        sources = [
            (path, path.read_text(encoding="utf-8", errors="replace"))
            for path in checks._python_files()
        ]
        self.assertEqual(checks.check_process_exit_has_single_owners(sources), [])


if __name__ == "__main__":
    unittest.main()
