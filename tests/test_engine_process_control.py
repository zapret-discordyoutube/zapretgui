"""Остановка процессов движка с подтверждением и подтверждение запуска."""

from __future__ import annotations

import ctypes
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from utils.windows_process_probe import ProcessSnapshotError
from winws_runtime.engine import process_control, startup, winapi

OWN_EXE = r"C:\Zapret\Dev\exe\winws2.exe"
FOREIGN_EXE = r"C:\Other\zapret\exe\winws2.exe"


class FakeProcess:
    """Подделка Popen: выход наступает после заданного числа ожиданий."""

    def __init__(self, *, exits_after_waits: int | None = None, exit_code: int = 0, pid: int = 4321):
        self.pid = pid
        self._exit_code = exit_code
        self._exits_after_waits = exits_after_waits
        self.returncode: int | None = None
        self.wait_timeouts: list[float] = []
        self.terminated = False

    def _exit(self) -> None:
        self.returncode = self._exit_code

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        if self.returncode is not None:
            return self.returncode
        if self._exits_after_waits is not None and len(self.wait_timeouts) >= self._exits_after_waits:
            self._exit()
            return self.returncode
        raise subprocess.TimeoutExpired("winws2", timeout)

    def terminate(self) -> None:
        self.terminated = True
        self._exits_after_waits = len(self.wait_timeouts) + 1


class StopProcessTests(unittest.TestCase):
    def test_stop_is_confirmed_by_process_exit(self) -> None:
        process = FakeProcess()

        self.assertTrue(process_control.stop_process(process, timeout=5.0))

        self.assertTrue(process.terminated)
        # Ожидание — одно, на хэндле процесса; никакого опроса с паузами.
        self.assertEqual(process.wait_timeouts, [5.0])

    def test_stop_reports_failure_when_process_does_not_exit(self) -> None:
        process = FakeProcess()
        process.terminate = lambda: None  # процесс не реагирует на завершение

        self.assertFalse(process_control.stop_process(process, timeout=0.5))

    def test_already_exited_process_needs_no_termination(self) -> None:
        process = FakeProcess()
        process.returncode = 0

        self.assertTrue(process_control.stop_process(process))
        self.assertFalse(process.terminated)

    def test_none_process_is_already_stopped(self) -> None:
        self.assertTrue(process_control.stop_process(None))

    def test_engine_stop_code_differs_from_silent_crash_code(self) -> None:
        # Код 1 — молчаливое падение winws; остановка программой должна быть
        # отличима от него, а код 259 означает «ещё работает».
        self.assertNotIn(process_control.ENGINE_STOP_EXIT_CODE, (0, 1, 259))


class StopEngineProcessesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.processes: dict[int, tuple[str, str]] = {}
        self.terminated: list[int] = []
        self.closed: list[int] = []
        self.unkillable: set[int] = set()
        self.open_errors: dict[int, int] = {}

        patches = {
            "open_process": self._open_process,
            "query_image_path": self._query_image_path,
            "terminate_process": self._terminate_process,
            "wait_for_all_handles": self._wait_for_all_handles,
            "wait_for_handle": self._wait_for_handle,
            "close_handle": self.closed.append,
        }
        for name, replacement in patches.items():
            patcher = patch.object(winapi, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(process_control, "iter_process_records_winapi_strict", self._snapshot)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(process_control, "normalize_image_path", lambda path: str(path or "").lower())
        patcher.start()
        self.addCleanup(patcher.stop)

    # Хэндл в подделке — это pid со сдвигом, чтобы путаница всплыла сразу.
    def _snapshot(self):
        return [(pid, name) for pid, (name, _path) in self.processes.items()]

    def _open_process(self, pid, _access):
        if pid in self.open_errors:
            raise winapi.WinApiError("OpenProcess", self.open_errors[pid])
        return pid + 100000

    def _query_image_path(self, handle):
        return self.processes[handle - 100000][1]

    def _terminate_process(self, handle, exit_code):
        self.assertEqual(exit_code, process_control.ENGINE_STOP_EXIT_CODE)
        self.terminated.append(handle - 100000)

    def _wait_for_all_handles(self, handles, _timeout):
        return all((handle - 100000) not in self.unkillable for handle in handles)

    def _wait_for_handle(self, handle, _timeout):
        return (handle - 100000) not in self.unkillable

    def test_only_engines_from_own_folder_are_stopped(self) -> None:
        self.processes = {
            10: ("winws2.exe", OWN_EXE),
            11: ("winws2.exe", FOREIGN_EXE),
            12: ("explorer.exe", r"C:\Windows\explorer.exe"),
        }

        result = process_control.stop_engine_processes([OWN_EXE])

        self.assertTrue(result.ok)
        self.assertEqual(result.stopped, (10,))
        self.assertEqual(self.terminated, [10])
        self.assertEqual([record.pid for record in result.foreign], [11])
        self.assertEqual(result.foreign[0].exe_path, FOREIGN_EXE.lower())

    def test_every_opened_handle_is_closed(self) -> None:
        self.processes = {10: ("winws2.exe", OWN_EXE), 11: ("winws2.exe", FOREIGN_EXE)}

        process_control.stop_engine_processes([OWN_EXE])

        self.assertEqual(sorted(self.closed), [100010, 100011])

    def test_process_that_does_not_exit_is_a_failure(self) -> None:
        self.processes = {10: ("winws2.exe", OWN_EXE), 13: ("winws2.exe", OWN_EXE)}
        self.unkillable = {13}

        result = process_control.stop_engine_processes([OWN_EXE])

        self.assertFalse(result.ok)
        self.assertEqual(result.stopped, (10,))
        self.assertEqual(result.failed, (13,))

    def test_failed_snapshot_is_not_reported_as_nothing_to_stop(self) -> None:
        # Прежний код принимал сбой списка процессов за «процессов нет».
        def broken():
            raise ProcessSnapshotError("snapshot failed")

        with patch.object(process_control, "iter_process_records_winapi_strict", broken):
            result = process_control.stop_engine_processes([OWN_EXE])

        self.assertFalse(result.ok)
        self.assertTrue(result.snapshot_failed)

    def test_process_that_cannot_be_verified_is_not_killed(self) -> None:
        self.processes = {10: ("winws2.exe", OWN_EXE)}
        self.open_errors = {10: winapi.ERROR_ACCESS_DENIED}

        result = process_control.stop_engine_processes([OWN_EXE])

        self.assertEqual(self.terminated, [])
        self.assertEqual([record.pid for record in result.foreign], [10])

    def test_process_that_already_exited_is_ignored(self) -> None:
        self.processes = {10: ("winws2.exe", OWN_EXE)}
        self.open_errors = {10: winapi.ERROR_INVALID_PARAMETER}

        result = process_control.stop_engine_processes([OWN_EXE])

        self.assertTrue(result.ok)
        self.assertEqual(result.stopped, ())
        self.assertEqual(result.foreign, ())

    def test_nothing_to_stop_is_success(self) -> None:
        self.assertTrue(process_control.stop_engine_processes([OWN_EXE]).ok)
        self.assertTrue(process_control.stop_engine_processes([]).ok)


class WaitEngineReadyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output_path = os.path.join(self.directory.name, "startup.log")

    def write(self, text: str, mode: str = "wb") -> None:
        with open(self.output_path, mode) as stream:
            stream.write(text.encode("utf-8"))

    def test_ready_line_confirms_start(self) -> None:
        self.write("github version v1\nwindivert initialized. capture is started.\n")
        process = FakeProcess()

        outcome = startup.wait_engine_ready(process, self.output_path, settle=0.35)

        self.assertTrue(outcome.started)
        self.assertEqual(outcome.reason, startup.REASON_READY)
        self.assertTrue(outcome.marker_seen)
        # После строки готовности — одно окно на ошибки Lua-скриптов.
        self.assertEqual(process.wait_timeouts, [0.35])

    def test_death_before_ready_line_is_a_failed_start(self) -> None:
        self.write("github version v1\nwindivert: error opening filter: access denied\n")
        process = FakeProcess(exits_after_waits=1, exit_code=5)

        outcome = startup.wait_engine_ready(process, self.output_path)

        self.assertFalse(outcome.started)
        self.assertEqual(outcome.reason, startup.REASON_EXITED)
        self.assertEqual(process.poll(), 5)

    def test_death_right_after_ready_line_is_a_failed_start(self) -> None:
        # Строка печатается до загрузки Lua: ошибка скрипта убивает процесс
        # уже после неё.
        self.write("windivert initialized. capture is started.\nLUA ERROR: boom\n")
        process = FakeProcess(exits_after_waits=1, exit_code=87)

        outcome = startup.wait_engine_ready(process, self.output_path)

        self.assertFalse(outcome.started)
        self.assertEqual(outcome.reason, startup.REASON_EXITED_AFTER_READY)

    def test_ready_line_split_across_reads_is_found(self) -> None:
        self.write("github version v1\nwindivert initi")
        process = FakeProcess()
        original_wait = process.wait

        def wait_and_finish_line(timeout=None):
            if len(process.wait_timeouts) == 0:
                self.write("alized. capture is started.\n", mode="ab")
            return original_wait(timeout=timeout)

        process.wait = wait_and_finish_line

        outcome = startup.wait_engine_ready(process, self.output_path)

        self.assertTrue(outcome.started)
        self.assertEqual(outcome.reason, startup.REASON_READY)

    def test_alive_engine_without_ready_line_is_accepted_after_timeout(self) -> None:
        # Не хуже прежнего правила «жив — значит запустился», но с пометкой.
        self.write("github version v1\n")
        process = FakeProcess()
        ticks = iter(range(1000))

        outcome = startup.wait_engine_ready(
            process,
            self.output_path,
            ready_timeout=5.0,
            clock=lambda: float(next(ticks)),
        )

        self.assertTrue(outcome.started)
        self.assertEqual(outcome.reason, startup.REASON_ALIVE_WITHOUT_MARKER)
        self.assertFalse(outcome.marker_seen)

    def test_without_output_file_only_survival_is_checked(self) -> None:
        process = FakeProcess()

        outcome = startup.wait_engine_ready(process, "", alive_window=1.0)

        self.assertTrue(outcome.started)
        self.assertEqual(outcome.reason, startup.REASON_ALIVE_WITHOUT_MARKER)
        self.assertEqual(process.wait_timeouts, [1.0])

    def test_without_output_file_death_is_a_failed_start(self) -> None:
        process = FakeProcess(exits_after_waits=1, exit_code=1)

        outcome = startup.wait_engine_ready(process, "", alive_window=1.0)

        self.assertFalse(outcome.started)


class WinApiBindingTests(unittest.TestCase):
    def test_every_bound_function_declares_argument_types(self) -> None:
        # Вызов без argtypes укладывал 64-битный хэндл службы в 32 бита и
        # падал; хэндл оставался открытым и держал службу драйвера.
        for library_name, table in winapi.signature_tables():
            for name, (restype, argtypes) in table.items():
                self.assertIsNotNone(restype, f"{library_name}.{name}")
                self.assertIsInstance(argtypes, list, f"{library_name}.{name}")

    def test_handle_closing_functions_take_a_pointer_sized_handle(self) -> None:
        tables = dict(winapi.signature_tables())
        self.assertEqual(tables["advapi32"]["CloseServiceHandle"][1], [winapi.HANDLE])
        self.assertEqual(tables["kernel32"]["CloseHandle"][1], [winapi.HANDLE])
        self.assertEqual(ctypes.sizeof(winapi.HANDLE), ctypes.sizeof(ctypes.c_void_p))

    def test_structures_have_windows_layout_on_any_platform(self) -> None:
        self.assertEqual(ctypes.sizeof(winapi.DWORD), 4)
        self.assertEqual(ctypes.sizeof(winapi.SERVICE_STATUS), 28)
        self.assertEqual(ctypes.sizeof(winapi.QUERY_SERVICE_CONFIGW), 64)

    def test_closing_handles_never_raises(self) -> None:
        winapi.close_handle(None)
        winapi.close_handle(0)
        winapi._close_service_handle(None)

    def test_private_libraries_are_used_not_the_shared_windll(self) -> None:
        # Общий ctypes.windll делит прототипы функций между всеми модулями.
        import inspect

        self.assertNotIn("ctypes.windll", inspect.getsource(winapi).replace("``ctypes.windll``", ""))


if __name__ == "__main__":
    unittest.main()
