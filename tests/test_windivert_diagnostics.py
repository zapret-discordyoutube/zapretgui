"""Тесты единого центра диагностики WinDivert (windivert_diagnostics).

Покрытие по spec windivert-check-center:
- AC6a: полнота декларативной таблицы кодов;
- AC6b/AC8: describe_windivert_error содержит десятичный код и осмысленную
  русскую подсказку для 5/1058/1060/1072/1275/577;
- AC6c: transient-набор readiness recovery == {5, 1058, 1060, 1753, 1072}
  плюс FWP_E_IN_USE — остаточное состояние WFP лечится тем же recovery-циклом.

Плюс порядок уточняющих проверок причины 1058 и разделение измеренного
Win32-кода и восстановленного эвристикой (34 → 1058).
"""

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from winws_runtime.health import launch_conflicts, windivert_diagnostics
from winws_runtime.health.windivert_diagnostics import (
    TRANSIENT_WINDIVERT_READINESS_CODES,
    WINDIVERT_ERROR_TABLE,
    describe_windivert_error,
    describe_windivert_readiness_failure,
)

# Служебный баннер winws2: печатается всегда и диагностикой не является.
_BANNER = "github version v1.0.3 (b78b52c4) lua_compat_ver 6"

# Настоящий текст Win32-ошибки 1058, который winws2 пишет в вывод.
_SERVICE_DISABLED_STDERR = (
    "windivert: error opening filter: The service cannot be started, either "
    "because it is disabled or because it has no enabled devices associated with it."
)


def _probe(*, installed: bool, ready: bool, error_code):
    from winws_runtime.runtime.system_ops import WinDivertRuntimeProbeResult

    return WinDivertRuntimeProbeResult(
        installed=installed,
        ready=ready,
        error_code=error_code,
        stage="network_open",
    )


@contextmanager
def _system_state(*, adapters=True, antivirus="", conflict=None, probe=None):
    """Полностью подменяет системные проверки причины 1058.

    Без этого тесты зависели бы от реальной машины: на не-Windows все probe
    отвечают случайно, и порядок проверок проверить нельзя.
    """
    from winws_runtime.health import winws_exit_diagnosis
    from winws_runtime.runtime import system_ops

    state = probe if probe is not None else _probe(installed=False, ready=False, error_code=1060)
    with (
        patch.object(winws_exit_diagnosis, "_check_windivert_files", return_value=[]),
        patch.object(winws_exit_diagnosis, "_check_bfe_service", return_value=True),
        patch.object(
            winws_exit_diagnosis, "_find_disabled_windivert_driver_service", return_value=None
        ),
        patch.object(winws_exit_diagnosis, "_detect_active_antivirus", return_value=antivirus),
        patch.object(winws_exit_diagnosis, "_check_network_adapters", return_value=adapters),
        patch.object(launch_conflicts, "build_launch_conflict_advice", return_value=conflict),
        patch.object(
            system_ops, "probe_windivert_state_runtime", return_value=state
        ) as probe_mock,
    ):
        yield probe_mock

# Коды из AC2 плюс дополнительные коды диагностики exit-кодов.
_REQUIRED_TABLE_CODES = (
    5, 577, 654, 1058, 1060, 1068, 1072, 1275, 8, 31, 87, 161, 1067, 0x80320010,
)

# Смысловая инварианта пользовательских текстов (AC8): код → ключевое слово
# readiness-подсказки (доступ / отключена служба / не установлен /
# помечена на удаление / HVCI / подпись).
_READINESS_SEMANTIC_KEYWORDS = {
    5: "доступ",
    1058: "отключена",
    1060: "не установлен",
    1072: "удаление",
    1275: "HVCI",
    577: "подпис",
    0x80320010: "заняты",
}


class WinDivertErrorTableTests(unittest.TestCase):
    def test_table_contains_all_required_codes(self) -> None:
        for code in _REQUIRED_TABLE_CODES:
            record = WINDIVERT_ERROR_TABLE.get(code)
            self.assertIsNotNone(record, f"нет записи для кода {code}")
            self.assertEqual(record.code, code)
            self.assertTrue(record.cause.strip(), f"пустая причина для кода {code}")
            self.assertTrue(record.solution.strip(), f"пустое решение для кода {code}")

    def test_codes_are_defined_once_via_named_constants(self) -> None:
        self.assertEqual(windivert_diagnostics._ERROR_ACCESS_DENIED, 5)
        self.assertEqual(windivert_diagnostics._ERROR_INVALID_IMAGE_HASH, 577)
        self.assertEqual(windivert_diagnostics._ERROR_DRIVER_FAILED_PRIOR_UNLOAD, 654)
        self.assertEqual(windivert_diagnostics._ERROR_SERVICE_DISABLED, 1058)
        self.assertEqual(windivert_diagnostics._ERROR_SERVICE_DOES_NOT_EXIST, 1060)
        self.assertEqual(windivert_diagnostics._ERROR_SERVICE_DEPENDENCY_FAIL, 1068)
        self.assertEqual(windivert_diagnostics._ERROR_SERVICE_MARKED_FOR_DELETE, 1072)
        self.assertEqual(windivert_diagnostics._ERROR_DRIVER_BLOCKED, 1275)
        self.assertEqual(windivert_diagnostics._ERROR_EPT_S_NOT_REGISTERED, 1753)

    def test_system_ops_imports_canonical_1072_constant(self) -> None:
        from winws_runtime.runtime import system_ops

        self.assertIs(
            system_ops._ERROR_SERVICE_MARKED_FOR_DELETE,
            windivert_diagnostics._ERROR_SERVICE_MARKED_FOR_DELETE,
        )


class DescribeWindivertErrorTests(unittest.TestCase):
    def test_readiness_text_contains_code_and_russian_hint(self) -> None:
        for code, keyword in _READINESS_SEMANTIC_KEYWORDS.items():
            text = describe_windivert_error(code, "readiness")
            self.assertIn(str(code), text, f"нет кода {code} в тексте: {text}")
            self.assertIn(keyword, text, f"нет ключевого слова '{keyword}' для {code}: {text}")
            self.assertTrue(WINDIVERT_ERROR_TABLE[code].short_hint_ru.strip())

    def test_exit_text_contains_code_and_solution(self) -> None:
        for code in _READINESS_SEMANTIC_KEYWORDS:
            record = WINDIVERT_ERROR_TABLE[code]
            text = describe_windivert_error(code, "exit")
            self.assertIn(str(code), text)
            self.assertIn(record.cause, text)
            self.assertIn(record.solution, text)

    def test_unknown_code_still_mentions_code(self) -> None:
        self.assertIn("4242", describe_windivert_error(4242, "exit"))
        self.assertIn("4242", describe_windivert_error(4242, "readiness"))

    def test_readiness_generic_text_keeps_probe_stage(self) -> None:
        text = describe_windivert_error(1753, "readiness", probe_stage="network_open")
        self.assertIn("1753", text)
        self.assertIn("network_open", text)
        self.assertIn("WinDivert ещё не готов к открытию фильтра", text)

    def test_exit_diagnosis_uses_table_base_texts(self) -> None:
        from winws_runtime.health.process_health_check import diagnose_winws_exit

        for code in (1275, 654, 1067):
            diagnosis = diagnose_winws_exit(code, "")
            self.assertIsNotNone(diagnosis)
            self.assertEqual(diagnosis.cause, WINDIVERT_ERROR_TABLE[code].cause)
            self.assertEqual(diagnosis.solution, WINDIVERT_ERROR_TABLE[code].solution)


class TransientReadinessCodesTests(unittest.TestCase):
    def test_transient_set_matches_frozen_recovery_codes(self) -> None:
        self.assertEqual(
            TRANSIENT_WINDIVERT_READINESS_CODES,
            frozenset({5, 1058, 1060, 1753, 1072, 0x80320010}),
        )

    def test_non_transient_probe_skips_recovery_cycle(self) -> None:
        from winws_runtime.runtime.system_ops import WinDivertRuntimeProbeResult

        blocked_probe = WinDivertRuntimeProbeResult(
            installed=True,
            ready=False,
            error_code=1275,
            stage="network_open",
        )
        calls = []
        result = windivert_diagnostics.retry_windivert_spawn_readiness_after_recovery(
            blocked_probe,
            aggressive_cleanup=lambda: calls.append("cleanup"),
            wait_after_cleanup=lambda: calls.append("wait"),
        )
        self.assertIs(result, blocked_probe)
        self.assertEqual(calls, [])


class ReadinessFailureDescriptionTests(unittest.TestCase):
    def test_failure_text_for_disabled_service_probe(self) -> None:
        from winws_runtime.runtime.system_ops import WinDivertRuntimeProbeResult

        probe = WinDivertRuntimeProbeResult(
            installed=False,
            ready=False,
            error_code=1058,
            stage="network_open",
        )
        with patch.object(launch_conflicts, "build_windivert_conflict_hint", return_value=None):
            message = describe_windivert_readiness_failure(probe)

        self.assertIn("1058", message)
        self.assertIn("служба WinDivert отключена в системе", message)

    def test_conflict_hint_is_appended_when_found(self) -> None:
        hint = "Возможный конфликт: GoodbyeDPI.exe (PID 7, C:\\G\\GoodbyeDPI.exe) держит WinDivert — закройте эту программу"
        with patch.object(launch_conflicts, "build_windivert_conflict_hint", return_value=hint):
            message = describe_windivert_readiness_failure(None)

        self.assertIn("WinDivert ещё не готов к открытию фильтра", message)
        self.assertIn("GoodbyeDPI.exe", message)


class ServiceDisabledCauseOrderTests(unittest.TestCase):
    """Порядок уточняющих проверок причины 1058.

    Probe выполняется уже после смерти winws2 и с флагом NO_INSTALL, а
    WinDivert снимает свою службу при закрытии последнего дескриптора.
    Поэтому «службы нет» (1060) — обычное состояние покоя, и такая ветка не
    должна перехватывать управление у проверок конфликта/антивируса/адаптеров.
    """

    def _cause(self, **kwargs):
        from winws_runtime.health.winws_exit_diagnosis import _probe_service_disabled_cause

        with _system_state(**kwargs) as probe_mock:
            return _probe_service_disabled_cause(), probe_mock

    def test_absent_driver_service_does_not_become_the_cause(self) -> None:
        (cause, solution, auto_fix), _ = self._cause(
            probe=_probe(installed=False, ready=False, error_code=1060)
        )

        record = WINDIVERT_ERROR_TABLE[1058]
        self.assertEqual(cause, record.cause)
        self.assertEqual(solution, record.solution)
        self.assertEqual(auto_fix, record.auto_fix_action)
        self.assertNotIn("1060", cause)
        self.assertNotIn("не установил", cause)

    def test_adapter_check_is_reachable_behind_absent_service_probe(self) -> None:
        (cause, _solution, auto_fix), _ = self._cause(
            adapters=False,
            probe=_probe(installed=False, ready=False, error_code=1060),
        )

        self.assertIn("сетевой адаптер", cause)
        self.assertEqual(auto_fix, "enable_adapters")

    def test_antivirus_check_runs_before_probe(self) -> None:
        (cause, _solution, _auto_fix), probe_mock = self._cause(antivirus="Kaspersky")

        self.assertIn("Kaspersky", cause)
        probe_mock.assert_not_called()

    def test_conflict_advice_runs_before_probe(self) -> None:
        advice = ("Kaspersky перехватывает трафик", "Отключите защиту сети")
        (cause, solution, _auto_fix), probe_mock = self._cause(conflict=advice)

        self.assertEqual((cause, solution), advice)
        probe_mock.assert_not_called()

    def test_installed_but_not_ready_probe_is_still_reported(self) -> None:
        (cause, _solution, _auto_fix), _ = self._cause(
            probe=_probe(installed=True, ready=False, error_code=1072)
        )

        self.assertIn("ещё не готов после предыдущего запуска", cause)
        self.assertIn("1072", cause)


class FwpInUseDiagnosisTests(unittest.TestCase):
    """Остатки WFP от прошлого запуска: FWP_E_IN_USE распознаётся по тексту."""

    _STDERR = (
        "windivert: error opening filter: The object is referenced by other "
        "objects so cannot be deleted"
    )

    def _diagnose(self, *, hint=None):
        from winws_runtime.health import winws_exit_diagnosis

        with patch.object(
            winws_exit_diagnosis, "describe_windivert_conflict_hint", return_value=hint or ""
        ):
            diagnosis = winws_exit_diagnosis.diagnose_winws_exit(10, f"{_BANNER}\n{self._STDERR}")
        self.assertIsNotNone(diagnosis)
        return diagnosis

    def test_text_signature_maps_to_fwp_in_use_record(self) -> None:
        diagnosis = self._diagnose()
        record = WINDIVERT_ERROR_TABLE[0x80320010]

        self.assertEqual(diagnosis.win32_error, 0x80320010)
        self.assertEqual(diagnosis.exit_code, 10)
        self.assertFalse(diagnosis.win32_error_inferred)
        self.assertEqual(diagnosis.cause, record.cause)

    def test_conflict_holder_is_named_in_solution(self) -> None:
        hint = "Возможный конфликт: GoodbyeDPI.exe (PID 7, C:\\G\\GoodbyeDPI.exe) держит WinDivert — закройте эту программу"
        diagnosis = self._diagnose(hint=hint)

        self.assertIn("GoodbyeDPI.exe", diagnosis.solution)
        self.assertIn(WINDIVERT_ERROR_TABLE[0x80320010].solution, diagnosis.solution)

    def test_message_shows_both_decimal_and_hex_code(self) -> None:
        from winws_runtime.health.winws_exit_diagnosis import format_winws_exit_diagnosis

        message = format_winws_exit_diagnosis(self._diagnose(), exe_name="winws2")

        self.assertIn("0x80320010", message)
        self.assertIn("код завершения процесса 10", message)

    def test_classified_as_retryable_conflict_not_user_problem(self) -> None:
        from winws_runtime.runners.spawn_failure import SpawnFailureKind, classify_spawn_failure

        result = classify_spawn_failure(10, self._STDERR)

        self.assertTrue(result.is_conflict)
        self.assertFalse(result.is_system)
        self.assertTrue(result.retryable)
        self.assertTrue(result.needs_aggressive_cleanup)
        self.assertEqual(result.kind, SpawnFailureKind.WINDIVERT_CONFLICT)


class WindowsErrorCodeFormatTests(unittest.TestCase):
    def test_plain_win32_codes_stay_decimal(self) -> None:
        from winws_runtime.health.windivert_diagnostics import format_windows_error_code

        self.assertEqual(format_windows_error_code(1058), "1058")
        self.assertEqual(format_windows_error_code(0), "0")

    def test_hresult_and_ntstatus_get_hex_form(self) -> None:
        from winws_runtime.health.windivert_diagnostics import format_windows_error_code

        self.assertEqual(format_windows_error_code(0x80320010), "2150760464 / 0x80320010")
        self.assertEqual(format_windows_error_code(0xC0000142), "3221225794 / 0xC0000142")

    def test_post_mortem_reuses_the_same_formatter(self) -> None:
        from winws_runtime.health import post_mortem
        from winws_runtime.health.windivert_diagnostics import format_windows_error_code

        self.assertEqual(
            post_mortem._format_exit_code(0xC0000142),
            format_windows_error_code(0xC0000142),
        )


class InferredWin32CodeTests(unittest.TestCase):
    """Измеренный Win32-код и восстановленный эвристикой подаются по-разному."""

    def _diagnose(self, exit_code: int, stderr: str):
        from winws_runtime.health.winws_exit_diagnosis import (
            diagnose_winws_exit,
            format_winws_exit_diagnosis,
        )

        with _system_state():
            diagnosis = diagnose_winws_exit(exit_code, stderr)
        self.assertIsNotNone(diagnosis)
        return diagnosis, format_winws_exit_diagnosis(diagnosis, exe_name="winws2")

    def test_truncated_exit_code_is_presented_as_assumption(self) -> None:
        diagnosis, message = self._diagnose(34, _BANNER)

        self.assertEqual(diagnosis.win32_error, 1058)
        self.assertTrue(diagnosis.win32_error_inferred)
        self.assertIn("Вероятная причина", message)
        self.assertNotIn("Найдена причина", message)
        self.assertIn("предположительно код ошибки Windows 1058", message)
        self.assertLess(
            message.index("код завершения процесса 34"),
            message.index("предположительно код ошибки Windows 1058"),
            f"измеренный код должен стоять первым: {message}",
        )

    def test_code_measured_from_stderr_stays_assertive(self) -> None:
        diagnosis, message = self._diagnose(34, f"{_BANNER}\n{_SERVICE_DISABLED_STDERR}")

        self.assertEqual(diagnosis.win32_error, 1058)
        self.assertFalse(diagnosis.win32_error_inferred)
        self.assertIn("Найдена причина", message)
        self.assertIn("код ошибки Windows 1058", message)
        self.assertIn("код завершения процесса 34", message)
        self.assertNotIn("предположительно", message)

    def test_exact_code_without_truncation_is_not_marked_inferred(self) -> None:
        diagnosis, message = self._diagnose(1275, "")

        self.assertFalse(diagnosis.win32_error_inferred)
        self.assertIn("код ошибки 1275", message)


if __name__ == "__main__":
    unittest.main()
