"""Молчаливое завершение winws/winws2 и служебный баннер версии.

winws2 первой строкой всегда печатает `github version ... lua_compat_ver N`.
Из-за этого «пустого вывода» у него не бывает, и наивная проверка
`not output.strip()` отключала всю диагностику молчаливой смерти, а строка
версии уходила пользователю вместо причины отказа.

Покрывается:
AC1: winws_output отделяет служебный баннер от диагностики.
AC2: spawn_failure классифицирует «код 1 без диагностики» как EXTERNAL_KILL.
AC3: winws2 показывает понятную причину, а не баннер, и повторяет старт один раз.
AC4: winws1 ведёт себя так же (баннер больше не блокирует его ретрай).
AC5: post-mortem и WinDivert-диагноз не слепнут из-за баннера.
AC6: полный стартовый вывод winws2 попадает в общий лог при отказе.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


BANNER = "github version v1.0.3 (b78b52c4cd7f843da3ff0848a3430afbd401bdf2) lua_compat_ver 6"
BANNER_SHORT = "github version v1.0.1 lua_compat_ver 6"
WINDIVERT_ERROR = "windivert: error opening filter: The service cannot be started"


class _LogRecorder:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def __call__(self, message, level="INFO", *args, **kwargs) -> None:
        self.records.append((str(message), str(level)))

    def levels(self) -> list[str]:
        return [level for _message, level in self.records]

    def messages(self) -> list[str]:
        return [message for message, _level in self.records]

    def error_messages(self) -> list[str]:
        return [message for message, level in self.records if level == "ERROR"]


def _report_with_confirmed_fact():
    """Детерминированный отчёт проб для тестов раннеров."""
    from winws_runtime.health.silent_exit_probe import SilentExitFinding, SilentExitReport

    return SilentExitReport(
        findings=(
            SilentExitFinding(
                fact="исполняемый файл исчез с диска (winws2.exe)",
                solution="Восстановите файл из карантина антивируса",
                confirmed=True,
            ),
        ),
        verified=("файлы WinDivert на месте",),
    )


def _make_winws2_runner(tmp_dir: str):
    from winws_runtime.runners.zapret2_runner import Winws2StrategyRunner

    runner = object.__new__(Winws2StrategyRunner)
    runner.winws_exe = "winws2.exe"
    runner.work_dir = tmp_dir
    runner._state_lock = threading.RLock()
    runner.running_process = None
    runner.current_launch_label = None
    runner.current_strategy_args = None
    runner._preset_file_path = None
    runner.last_error = None
    runner._last_spawn_exit_code = None
    runner._last_spawn_stderr = ""
    runner._transition_in_progress_callback = None
    runner._runner_failure_callback = Mock()
    runner._launch_error_callback = Mock()
    runner._active_preset_content_changed_callback = None

    runner._compile_preset_artifact = Mock(
        return_value=SimpleNamespace(
            validation_ok=True,
            validation_report="",
            preset_path="preset.txt",
            cache_key=None,
            normalized_text="--wf-tcp-out=443\n",
            launch_args=("--wf-tcp-out=443",),
        )
    )
    runner._resolve_cleanup_required_before_spawn = Mock(return_value=False)
    runner._perform_cleanup_before_spawn_locked = Mock()
    runner._aggressive_windivert_cleanup = Mock()
    runner._wait_after_aggressive_windivert_cleanup = Mock()
    runner._ensure_windivert_ready_before_spawn = Mock(return_value=True)
    return runner


class WinwsOutputParsingTests(unittest.TestCase):
    """AC1: баннер версии — не диагностика."""

    def test_banner_lines_are_recognized(self) -> None:
        from winws_runtime.health.winws_output import is_banner_line

        for line in (
            BANNER,
            BANNER_SHORT,
            "version v1.0.3 lua_compat_ver 6",
            "winws v70.3",
            "winws2.exe v1.0.3",
            "nfqws v2.1",
        ):
            self.assertTrue(is_banner_line(line), line)

    def test_meaningful_lines_are_not_banners(self) -> None:
        from winws_runtime.health.winws_output import is_banner_line

        for line in (
            "",
            "   ",
            WINDIVERT_ERROR,
            "error: unknown parameter --nope",
            "ошибка загрузки lua",
            "desync function 'fake' does not exist",
        ):
            self.assertFalse(is_banner_line(line), repr(line))

    def test_banner_shaped_line_with_error_marker_stays_diagnostic(self) -> None:
        """Строка версии с признаком ошибки не должна проглатываться."""
        from winws_runtime.health.winws_output import is_banner_line

        self.assertFalse(is_banner_line("github version v1.0.3: version error"))

    def test_has_diagnostic_output_ignores_banner_only_output(self) -> None:
        from winws_runtime.health.winws_output import has_diagnostic_output

        self.assertFalse(has_diagnostic_output(""))
        self.assertFalse(has_diagnostic_output(BANNER))
        self.assertFalse(has_diagnostic_output(f"{BANNER}\n\n   \n"))
        self.assertTrue(has_diagnostic_output(f"{BANNER}\n{WINDIVERT_ERROR}"))
        self.assertTrue(has_diagnostic_output("usage: winws2 <options>"))

    def test_relevant_error_line_prefers_windivert_from_the_tail(self) -> None:
        from winws_runtime.health.winws_output import relevant_error_line

        output = "\n".join([BANNER, "error: something earlier", WINDIVERT_ERROR])
        self.assertEqual(relevant_error_line(output), WINDIVERT_ERROR)

    def test_relevant_error_line_prefers_last_error_marker(self) -> None:
        from winws_runtime.health.winws_output import relevant_error_line

        output = "\n".join([BANNER, "error: first", "error: last"])
        self.assertEqual(relevant_error_line(output), "error: last")

    def test_relevant_error_line_fallbacks_skip_the_banner(self) -> None:
        from winws_runtime.health.winws_output import relevant_error_line

        output = "\n".join([BANNER, "usage: winws2 <options>", "see docs"])
        self.assertEqual(relevant_error_line(output, fallback="first"), "usage: winws2 <options>")
        self.assertEqual(relevant_error_line(output, fallback="last"), "see docs")
        self.assertEqual(relevant_error_line(output, fallback="none"), "")

    def test_banner_only_output_has_no_relevant_line(self) -> None:
        from winws_runtime.health.winws_output import relevant_error_line

        for fallback in ("first", "last", "none"):
            self.assertEqual(relevant_error_line(BANNER, fallback=fallback), "", fallback)
        self.assertEqual(relevant_error_line("", fallback="first"), "")


class SilentExitClassificationTests(unittest.TestCase):
    """AC2: код 1 без диагностики — «умер молча», причина пока неизвестна."""

    def test_code_one_without_diagnostics_is_silent_exit(self) -> None:
        from winws_runtime.runners.spawn_failure import is_silent_exit

        self.assertTrue(is_silent_exit(1, ""))
        self.assertTrue(is_silent_exit(1, BANNER))
        self.assertTrue(is_silent_exit("1", f"{BANNER}\n"))

    def test_other_failures_are_not_silent_exit(self) -> None:
        from winws_runtime.runners.spawn_failure import is_silent_exit

        self.assertFalse(is_silent_exit(1, f"{BANNER}\n{WINDIVERT_ERROR}"))
        self.assertFalse(is_silent_exit(9, ""))
        self.assertFalse(is_silent_exit(0, ""))
        self.assertFalse(is_silent_exit(0xC0000142, BANNER))

    def test_classification_marks_silent_exit_without_retry_or_cleanup(self) -> None:
        from winws_runtime.runners.spawn_failure import SpawnFailureKind, classify_spawn_failure

        classification = classify_spawn_failure(1, BANNER)
        self.assertEqual(classification.kind, SpawnFailureKind.SILENT_EXIT)
        self.assertTrue(classification.is_silent_exit)
        self.assertFalse(classification.retryable)
        self.assertFalse(classification.needs_aggressive_cleanup)

    def test_classification_never_guesses_a_cause(self) -> None:
        """Уровень классификации не вправе называть виновника."""
        from winws_runtime.runners.spawn_failure import classify_spawn_failure

        message = str(classify_spawn_failure(1, BANNER).user_message or "")
        self.assertNotIn("антивирус", message.lower())
        self.assertNotIn("оптимизатор", message.lower())

    def test_conflict_output_still_wins_over_silent_exit(self) -> None:
        from winws_runtime.runners.spawn_failure import SpawnFailureKind, classify_spawn_failure

        classification = classify_spawn_failure(
            1,
            f"{BANNER}\nA copy of winws2 is already running with the same filter",
        )
        self.assertEqual(classification.kind, SpawnFailureKind.WINDIVERT_CONFLICT)
        self.assertFalse(classification.is_silent_exit)
        self.assertTrue(classification.retryable)


class SilentExitProbeTests(unittest.TestCase):
    """AC7: причина устанавливается пробами, а не догадкой."""

    def setUp(self) -> None:
        from winws_runtime.health.silent_exit_probe import reset_silent_exit_probe_cache

        reset_silent_exit_probe_cache()
        self.addCleanup(reset_silent_exit_probe_cache)

    def _clean_system(self, **overrides):
        """Патчи «в системе всё в порядке», поверх которых ставится сценарий."""
        defaults = {
            "windivert_files": [],
            "application_errors": [],
            "defender": [],
            "antivirus": None,
        }
        defaults.update(overrides)
        return (
            patch(
                "winws_runtime.health.winws_exit_diagnosis._check_windivert_files",
                return_value=defaults["windivert_files"],
            ),
            patch(
                "utils.windows_event_log.get_recent_application_error_messages",
                return_value=defaults["application_errors"],
            ),
            patch(
                "utils.windows_event_log.get_recent_defender_detections",
                return_value=defaults["defender"],
            ),
            patch(
                "winws_runtime.health.antivirus_detection._detect_active_antivirus",
                return_value=defaults["antivirus"],
            ),
        )

    def _probe(self, exe_path: str, **overrides):
        from winws_runtime.health.silent_exit_probe import probe_silent_exit

        patches = self._clean_system(**overrides)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return probe_silent_exit(exe_path=exe_path, process_name="winws2.exe")

    def test_missing_executable_is_a_confirmed_fact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = self._probe(str(Path(tmp_dir) / "gone.exe"))

        self.assertTrue(report.confirmed)
        self.assertIn("исчез с диска", report.confirmed[0].fact)
        self.assertIn("карантин", report.confirmed[0].solution)

    def test_application_error_event_points_at_a_crash_not_a_killer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            exe = Path(tmp_dir) / "winws2.exe"
            exe.write_bytes(b"MZ")
            report = self._probe(
                str(exe),
                application_errors=["Faulting application winws2.exe\nException code: 0xc0000005"],
            )

        self.assertTrue(report.confirmed)
        fact = report.confirmed[0].fact
        self.assertIn("аварийное завершение", fact)
        self.assertNotIn("антивирус", fact.lower())

    def test_defender_detection_is_reported_with_its_own_words(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            exe = Path(tmp_dir) / "winws2.exe"
            exe.write_bytes(b"MZ")
            report = self._probe(
                str(exe),
                defender=["HackTool:Win32/Zapret | Removed | C:\\zapret\\winws2.exe"],
            )

        self.assertTrue(report.confirmed)
        self.assertIn("HackTool:Win32/Zapret", report.confirmed[0].fact)

    def test_present_antivirus_is_only_a_suspicion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            exe = Path(tmp_dir) / "winws2.exe"
            exe.write_bytes(b"MZ")
            report = self._probe(str(exe), antivirus="Kaspersky")

        self.assertFalse(report.confirmed)
        self.assertTrue(report.suspected)
        self.assertIn("Kaspersky", report.suspected[0].fact)

    def test_clean_system_yields_no_findings_but_records_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            exe = Path(tmp_dir) / "winws2.exe"
            exe.write_bytes(b"MZ")
            report = self._probe(str(exe))

        self.assertEqual(report.findings, ())
        self.assertEqual(len(report.verified), 5)

    def test_unreadable_defender_log_is_not_reported_as_clean(self) -> None:
        """«Не смогли проверить» не должно превращаться в «проверено, чисто»."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            exe = Path(tmp_dir) / "winws2.exe"
            exe.write_bytes(b"MZ")
            report = self._probe(str(exe), defender=None)

        self.assertEqual(report.findings, ())
        self.assertNotIn("Defender по файлам программы не срабатывал", report.verified)
        self.assertIn("файлы программы на месте", report.verified)

    def test_unknown_executable_path_is_not_reported_as_checked(self) -> None:
        """Без пути к exe нельзя утверждать, что файлы на месте."""
        report = self._probe("")

        self.assertNotIn("файлы программы на месте", report.verified)
        self.assertIn("файлы WinDivert на месте", report.verified)


class DefenderEventParsingTests(unittest.TestCase):
    """AC7: событие Defender разбирается только когда оно про наши файлы."""

    EVENT_XML = (
        "<Event><EventData>"
        "<Data Name='Threat Name'>HackTool:Win32/Zapret</Data>"
        "<Data Name='Path'>file:_C:\\zapret\\winws2.exe</Data>"
        "<Data Name='Action Name'>Remove</Data>"
        "</EventData></Event>"
    )

    def test_event_for_our_folder_is_summarized(self) -> None:
        from utils.windows_event_log import _summarize_defender_event

        summary = _summarize_defender_event(self.EVENT_XML, path_marker="C:\\zapret")
        self.assertIn("HackTool:Win32/Zapret", summary)
        self.assertIn("Remove", summary)
        self.assertIn("winws2.exe", summary)

    def test_event_for_another_path_is_ignored(self) -> None:
        from utils.windows_event_log import _summarize_defender_event

        self.assertEqual(
            _summarize_defender_event(self.EVENT_XML, path_marker="D:\\games"),
            "",
        )

    def test_unparsable_event_yields_nothing(self) -> None:
        from utils.windows_event_log import _summarize_defender_event

        self.assertEqual(_summarize_defender_event("", path_marker=""), "")
        self.assertEqual(_summarize_defender_event("<Event/>", path_marker=""), "")

    def test_marker_is_case_insensitive(self) -> None:
        from utils.windows_event_log import _summarize_defender_event

        summary = _summarize_defender_event(self.EVENT_XML, path_marker="c:\\ZAPRET")
        self.assertIn("HackTool:Win32/Zapret", summary)


class SilentExitMessageTests(unittest.TestCase):
    """AC8: текст сообщения соответствует уровню доказанности."""

    def _report(self, findings=(), verified=("файлы программы на месте",)):
        from winws_runtime.health.silent_exit_probe import SilentExitReport

        return SilentExitReport(findings=tuple(findings), verified=tuple(verified))

    def _finding(self, *, confirmed: bool):
        from winws_runtime.health.silent_exit_probe import SilentExitFinding

        return SilentExitFinding(
            fact="исполняемый файл исчез с диска (winws2.exe)",
            solution="Восстановите файл из карантина",
            confirmed=confirmed,
        )

    def test_confirmed_fact_is_stated_as_the_cause(self) -> None:
        from winws_runtime.health.silent_exit_probe import format_silent_exit_message

        message = format_silent_exit_message(
            self._report([self._finding(confirmed=True)]),
            exe_name="winws2",
            exit_code=1,
        )
        self.assertIn("Найдена причина: исполняемый файл исчез с диска", message)
        self.assertIn("Восстановите файл из карантина", message)
        self.assertNotIn("Вероятная причина", message)
        self.assertNotIn("не установлена", message)

    def test_suspicion_is_marked_as_probable_and_lists_checks(self) -> None:
        from winws_runtime.health.silent_exit_probe import format_silent_exit_message

        message = format_silent_exit_message(
            self._report([self._finding(confirmed=False)]),
            exe_name="winws2",
            exit_code=1,
        )
        self.assertIn("Вероятная причина", message)
        self.assertIn("Проверено: файлы программы на месте", message)
        self.assertNotIn("Найдена причина", message)

    def test_without_findings_the_message_admits_it_does_not_know(self) -> None:
        from winws_runtime.health.silent_exit_probe import format_silent_exit_message

        message = format_silent_exit_message(
            self._report(),
            exe_name="winws2",
            exit_code=1,
            lifetime_seconds=0.42,
        )
        self.assertIn("Причина не установлена", message)
        self.assertIn("проверено: файлы программы на месте", message)
        self.assertIn("прожив 0.4 с", message)
        self.assertIn("лог", message)
        self.assertNotIn("антивирус", message.lower())

    def test_message_never_leaks_the_banner(self) -> None:
        from winws_runtime.health.silent_exit_probe import format_silent_exit_message

        message = format_silent_exit_message(self._report(), exe_name="winws2", exit_code=1)
        self.assertNotIn("github version", message)
        self.assertIn("winws2", message)
        self.assertIn("кодом 1", message)


class Winws2SilentExitPublicationTests(unittest.TestCase):
    """AC3/AC6: понятная причина вместо баннера, повтор и полный лог."""

    def test_summary_of_banner_only_output_is_empty(self) -> None:
        from winws_runtime.runners.zapret2_runner import Winws2StrategyRunner

        self.assertEqual(Winws2StrategyRunner._summarize_startup_output(BANNER), "")
        self.assertEqual(
            Winws2StrategyRunner._summarize_startup_output(f"{BANNER}\n{WINDIVERT_ERROR}"),
            WINDIVERT_ERROR,
        )

    def test_spawn_exit_error_runs_the_probe_instead_of_showing_the_banner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            runner = _make_winws2_runner(tmp_dir)
            runner._set_last_error = Mock()

            with (
                patch("winws_runtime.runners.zapret2_runner.log"),
                patch(
                    "winws_runtime.runners.zapret2_runner.probe_silent_exit",
                    return_value=_report_with_confirmed_fact(),
                ) as probe,
            ):
                runner._set_spawn_exit_error(1, BANNER, lifetime_seconds=0.4)

        probe.assert_called_once()
        self.assertEqual(probe.call_args.kwargs["exe_path"], "winws2.exe")
        message = runner._set_last_error.call_args.args[0]
        self.assertNotIn("github version", message)
        self.assertNotIn("lua_compat_ver", message)
        self.assertIn("Найдена причина: исполняемый файл исчез с диска", message)
        self.assertIn("прожив 0.4 с", message)

    def test_spawn_exit_error_keeps_real_output_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            runner = _make_winws2_runner(tmp_dir)
            runner._set_last_error = Mock()

            with patch("winws_runtime.runners.zapret2_runner.log"):
                runner._set_spawn_exit_error(2, f"{BANNER}\nfatal: preset is broken")

        message = runner._set_last_error.call_args.args[0]
        self.assertIn("fatal: preset is broken", message)
        self.assertNotIn("github version", message)

    def test_full_startup_output_reaches_the_log(self) -> None:
        from winws_runtime.runners.zapret2_runner import Winws2StrategyRunner

        recorder = _LogRecorder()
        with patch("winws_runtime.runners.zapret2_runner.log", recorder):
            Winws2StrategyRunner._log_full_startup_output(f"{BANNER}\n{WINDIVERT_ERROR}")

        logged = "\n".join(recorder.messages())
        self.assertIn("github version", logged)
        self.assertIn(WINDIVERT_ERROR, logged)
        self.assertNotIn("DEBUG", recorder.levels())

    def test_empty_startup_output_is_reported_explicitly(self) -> None:
        from winws_runtime.runners.zapret2_runner import Winws2StrategyRunner

        recorder = _LogRecorder()
        with patch("winws_runtime.runners.zapret2_runner.log", recorder):
            Winws2StrategyRunner._log_full_startup_output("")

        self.assertIn("не оставил стартового вывода", "\n".join(recorder.messages()))

    def test_long_startup_output_is_truncated_with_marker(self) -> None:
        from winws_runtime.runners.zapret2_runner import (
            _STARTUP_OUTPUT_LOG_LIMIT,
            Winws2StrategyRunner,
        )

        recorder = _LogRecorder()
        with patch("winws_runtime.runners.zapret2_runner.log", recorder):
            Winws2StrategyRunner._log_full_startup_output("x" * (_STARTUP_OUTPUT_LOG_LIMIT + 100))

        logged = "\n".join(recorder.messages())
        self.assertIn("[…]", logged)
        self.assertLess(len(logged), _STARTUP_OUTPUT_LOG_LIMIT + 200)

    def test_silent_code_one_is_retried_once_then_published(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "preset.txt"
            preset_path.write_text("--wf-tcp-out=443", encoding="utf-8")

            runner = _make_winws2_runner(tmp_dir)

            def spawn_always_silent_code_one(*_args, **_kwargs):
                runner._last_spawn_exit_code = 1
                runner._last_spawn_stderr = BANNER
                runner._set_spawn_exit_error(1, BANNER)
                return False

            runner._spawn_process_locked = Mock(side_effect=spawn_always_silent_code_one)

            recorder = _LogRecorder()
            with (
                patch("winws_runtime.runners.zapret2_runner.log", recorder),
                patch("winws_runtime.runners.runner_base.log", recorder),
                patch(
                    "winws_runtime.runners.zapret2_runner.probe_silent_exit",
                    return_value=_report_with_confirmed_fact(),
                ),
                patch(
                    "winws_runtime.runners.zapret2_runner.find_stale_windivert_delete_pending_services_runtime",
                    return_value=[],
                ),
            ):
                success = runner.start_from_preset_file(str(preset_path), "Preset")

        self.assertFalse(success)
        self.assertEqual(runner._spawn_process_locked.call_count, 2)
        self.assertEqual(len(recorder.error_messages()), 1)
        self.assertIn("исполняемый файл исчез с диска", recorder.error_messages()[0])
        self.assertNotIn("github version", recorder.error_messages()[0])

    def test_silent_code_one_retry_can_succeed_quietly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "preset.txt"
            preset_path.write_text("--wf-tcp-out=443", encoding="utf-8")

            runner = _make_winws2_runner(tmp_dir)

            def spawn_silent_then_success(*_args, **_kwargs):
                if runner._spawn_process_locked.call_count == 1:
                    runner._last_spawn_exit_code = 1
                    runner._last_spawn_stderr = BANNER
                    return False
                return True

            runner._spawn_process_locked = Mock(side_effect=spawn_silent_then_success)

            recorder = _LogRecorder()
            with (
                patch("winws_runtime.runners.zapret2_runner.log", recorder),
                patch("winws_runtime.runners.runner_base.log", recorder),
                patch(
                    "winws_runtime.runners.zapret2_runner.find_stale_windivert_delete_pending_services_runtime",
                    return_value=[],
                ),
            ):
                success = runner.start_from_preset_file(str(preset_path), "Preset")

        self.assertTrue(success)
        self.assertEqual(runner._spawn_process_locked.call_count, 2)
        self.assertNotIn("ERROR", recorder.levels())
        runner._launch_error_callback.assert_not_called()

    def test_dry_run_failure_with_banner_only_output_runs_the_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            runner = _make_winws2_runner(tmp_dir)
            runner._set_last_error = Mock()
            runner._set_runner_state_locked = Mock()

            artifact = SimpleNamespace(
                preset_path=str(Path(tmp_dir) / "preset.txt"),
                normalized_text="--wf-tcp-out=443\n",
                launch_args=("--wf-tcp-out=443",),
                validation_ok=True,
                validation_report="",
            )

            with (
                patch("winws_runtime.runners.zapret2_runner.log"),
                patch(
                    "winws_runtime.runners.zapret2_runner.probe_silent_exit",
                    return_value=_report_with_confirmed_fact(),
                ),
                patch(
                    "winws_runtime.runners.zapret2_runner.subprocess.run",
                    return_value=SimpleNamespace(returncode=1, stdout=BANNER.encode(), stderr=b""),
                ),
                patch.object(
                    type(runner),
                    "_create_startup_info",
                    Mock(return_value=None),
                ),
            ):
                ok = runner._run_preset_dry_run_locked(
                    artifact,
                    "Preset",
                    preset_switch=False,
                    notify_failure=False,
                )

        self.assertFalse(ok)
        message = runner._set_last_error.call_args.args[0]
        self.assertNotIn("github version", message)
        self.assertIn("исполняемый файл исчез с диска", message)


class Winws1SilentExitTests(unittest.TestCase):
    """AC4: winws1 не должен слепнуть от собственного баннера."""

    def test_retry_predicate_ignores_banner_only_output(self) -> None:
        from winws_runtime.runners.zapret1_runner import Winws1StrategyRunner

        self.assertTrue(
            Winws1StrategyRunner._should_retry_unclassified_code_one(1, "", retry_count=0)
        )
        self.assertTrue(
            Winws1StrategyRunner._should_retry_unclassified_code_one(1, "winws v70.3", retry_count=0)
        )
        self.assertFalse(
            Winws1StrategyRunner._should_retry_unclassified_code_one(1, "", retry_count=1)
        )
        self.assertFalse(
            Winws1StrategyRunner._should_retry_unclassified_code_one(
                1, "windivert: error opening filter", retry_count=0
            )
        )


    def test_dry_run_failure_with_banner_only_output_runs_the_probe(self) -> None:
        from winws_runtime.runners.zapret1_runner import Winws1StrategyRunner

        with tempfile.TemporaryDirectory() as tmp_dir:
            runner = object.__new__(Winws1StrategyRunner)
            runner.winws_exe = "winws.exe"
            runner.work_dir = tmp_dir
            runner._last_spawn_exit_code = None
            runner._last_spawn_stderr = ""
            runner._set_last_error = Mock()
            runner._get_missing_windows_system_dependencies = Mock(return_value=[])
            runner._write_winws1_dry_run_at_config = Mock(return_value=str(Path(tmp_dir) / "cfg.txt"))
            runner._create_startup_info = Mock(return_value=None)

            with (
                patch("winws_runtime.runners.zapret1_runner.log"),
                patch(
                    "winws_runtime.runners.zapret1_runner.probe_silent_exit",
                    return_value=_report_with_confirmed_fact(),
                ),
                patch(
                    "winws_runtime.runners.zapret1_runner.subprocess.run",
                    return_value=SimpleNamespace(
                        returncode=1,
                        stdout=b"winws v70.3\n",
                        stderr=b"",
                    ),
                ),
            ):
                ok = runner._run_preset_dry_run_locked(
                    SimpleNamespace(
                        preset_path="preset.txt",
                        normalized_text="--wf-tcp=443\n",
                        launch_args=("--wf-tcp=443",),
                    ),
                    notify_failure=False,
                )

        self.assertFalse(ok)
        message = runner._set_last_error.call_args.args[0]
        self.assertIn("исполняемый файл исчез с диска", message)
        self.assertNotIn("winws v70.3", message)


class PostMortemBannerTests(unittest.TestCase):
    """AC5: смерть по ходу сессии диагностируется одинаково с баннером и без."""

    def test_banner_only_output_is_still_a_silent_exit(self) -> None:
        from winws_runtime.health.post_mortem import diagnose_unexpected_winws_exit

        with patch(
            "winws_runtime.health.post_mortem.probe_silent_exit",
            return_value=_report_with_confirmed_fact(),
        ) as probe:
            result = diagnose_unexpected_winws_exit(
                1,
                BANNER,
                exe_name="winws2.exe",
                exe_path="C:\\zapret\\winws2.exe",
            )

        probe.assert_called_once()
        self.assertEqual(probe.call_args.kwargs["exe_path"], "C:\\zapret\\winws2.exe")
        self.assertEqual(result.kind, "silent_exit")
        self.assertIn("исполняемый файл исчез с диска", result.message)
        self.assertNotIn("github version", result.message)

    def test_real_output_is_preferred_over_banner(self) -> None:
        from winws_runtime.health.post_mortem import diagnose_unexpected_winws_exit

        result = diagnose_unexpected_winws_exit(
            7,
            f"{BANNER}\nsomething went wrong",
            exe_name="winws2.exe",
        )
        self.assertIn("something went wrong", result.message)
        self.assertNotIn("github version", result.message)


class WinDivertDiagnosisBannerTests(unittest.TestCase):
    """AC5: усечённый код 1058→34 распознаётся и при наличии баннера."""

    def test_truncated_service_disabled_code_is_diagnosed_with_banner(self) -> None:
        from winws_runtime.health.winws_exit_diagnosis import diagnose_winws_exit

        diagnosis = diagnose_winws_exit(34, BANNER)
        self.assertIsNotNone(diagnosis)
        self.assertEqual(diagnosis.win32_error, 1058)
        self.assertEqual(diagnosis.exit_code, 34)

    def test_relevant_line_extraction_skips_banner(self) -> None:
        from winws_runtime.health.winws_exit_diagnosis import _extract_relevant_error_line

        self.assertEqual(_extract_relevant_error_line(BANNER), "")
        self.assertEqual(
            _extract_relevant_error_line(f"{BANNER}\n{WINDIVERT_ERROR}"),
            WINDIVERT_ERROR,
        )


if __name__ == "__main__":
    unittest.main()
