"""Сквозная проверка запуска и остановки обхода на живой Windows.

В отличие от ``run_engine_e2e.py`` (нижний слой в отдельности), здесь
работают настоящие раннеры программы: запуск из файла пресета, остановка,
быстрая смена пресета, подхват осиротевшего процесса, поведение при чужом
winws и при застрявшей службе драйвера.

Запуск — на машине с установленной программой, от администратора. Скрипт
должен лежать в копии установки (папки exe, lua, lists, bin,
windivert.filter, presets рядом с src), чтобы пути программы указывали на
эту копию, а не на рабочую установку:

    python tests/e2e_windows/run_runtime_e2e.py

Пресеты берутся настоящие, из папки presets: на время проверки обход
действительно включается на этой машине.
"""

from __future__ import annotations

import ctypes
import glob
import os
import subprocess
import sys
import threading
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from config.runtime_layout import APPLICATION_PATHS  # noqa: E402
from winws_runtime.engine import driver, process_control, startup, winapi  # noqa: E402
from winws_runtime.runners.preset_runner_support import PresetRunnerState  # noqa: E402
from winws_runtime.runners.zapret1_runner import Winws1StrategyRunner  # noqa: E402
from winws_runtime.runners.zapret2_runner import Winws2StrategyRunner  # noqa: E402
from winws_runtime.runtime import system_ops  # noqa: E402
from winws_runtime.runtime.runtime_api import PresetLaunchRuntimeApi  # noqa: E402

CREATE_NO_WINDOW = 0x08000000
SERVICE = driver.OWN_DRIVER_SERVICE_NAME


class Lab:
    def __init__(self) -> None:
        self.root = str(APPLICATION_PATHS.root)
        self.exe2 = os.path.join(self.root, "exe", "winws2.exe")
        self.exe1 = os.path.join(self.root, "exe", "winws.exe")
        presets2 = sorted(glob.glob(os.path.join(self.root, "presets", "winws2_builtin", "*.txt")))
        presets1 = sorted(glob.glob(os.path.join(self.root, "presets", "winws1_builtin", "*.txt")))
        assert len(presets2) >= 2, "нужны хотя бы два пресета winws2"
        assert presets1, "нужен хотя бы один пресет winws1"
        self.preset_a, self.preset_b = presets2[0], presets2[1]
        self.preset_v1 = presets1[0]
        self.runner2 = Winws2StrategyRunner(self.exe2)
        self.runner1 = Winws1StrategyRunner(self.exe1)
        self.unexpected_exit = threading.Event()
        self.runner2.configure_runtime_callbacks(unexpected_process_exit=self.unexpected_exit.set)
        self.extra: list[subprocess.Popen] = []

    def own_pids(self) -> list[int]:
        expected = {os.path.normcase(self.exe1), os.path.normcase(self.exe2)}
        pids = []
        for pid, _name in process_control.list_engine_processes(("winws.exe", "winws2.exe")):
            try:
                handle = winapi.open_process(pid, winapi.PROCESS_QUERY_LIMITED_INFORMATION)
            except winapi.WinApiError:
                continue
            try:
                if process_control.normalize_image_path(winapi.query_image_path(handle)) in expected:
                    pids.append(pid)
            finally:
                winapi.close_handle(handle)
        return sorted(pids)

    def service(self):
        return winapi.query_service(SERVICE)

    def spawn_raw(self, exe: str):
        """Запускает winws2 в обход раннера (осиротевший или чужой процесс)."""
        output_path = os.path.join(self.root, "user", "tmp", f"e2e_raw_{len(self.extra)}.log")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        for _ in range(3):
            with open(output_path, "wb") as output:
                process = subprocess.Popen(
                    [exe, "--wf-tcp-out=9", "--wf-dup-check=0"],
                    cwd=os.path.dirname(os.path.dirname(exe)),
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=output,
                    creationflags=CREATE_NO_WINDOW,
                )
            self.extra.append(process)
            if startup.wait_engine_ready(process, output_path).started:
                return process
        raise AssertionError("не удалось запустить вспомогательный winws2")

    def cleanup(self) -> None:
        for runner in (self.runner2, self.runner1):
            try:
                runner.stop(cleanup_services=False)
            except Exception:
                pass
        for process in self.extra:
            try:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            except Exception:
                pass
        self.extra.clear()
        system_ops.stop_own_winws_processes_runtime()
        system_ops.release_windivert_driver_runtime()


def _clean_slate(lab: Lab) -> None:
    lab.cleanup()
    lab.unexpected_exit.clear()
    assert lab.own_pids() == [], f"остались свои процессы: {lab.own_pids()}"
    assert lab.service() is None, f"служба драйвера не убрана: {lab.service()}"


def _state(runner) -> PresetRunnerState:
    return runner.get_runner_state_snapshot().state


# ---------------------------------------------------------------------------
#  Сценарии
# ---------------------------------------------------------------------------

def scenario_start_and_full_stop_removes_driver(lab: Lab) -> None:
    """Запуск из пресета; после полной остановки служба драйвера исчезает."""
    started = time.perf_counter()
    assert lab.runner2.start_from_preset_file(lab.preset_a, "e2e"), lab.runner2.last_error
    start_seconds = time.perf_counter() - started
    assert _state(lab.runner2) == PresetRunnerState.RUNNING
    assert lab.own_pids() == [lab.runner2.running_process.pid]
    info = lab.service()
    assert info is not None and info.state == winapi.SERVICE_RUNNING, info
    assert driver.is_own_driver(info.image_path, [lab.root]), info

    started = time.perf_counter()
    assert lab.runner2.stop(cleanup_services=True)
    stop_seconds = time.perf_counter() - started
    assert _state(lab.runner2) == PresetRunnerState.IDLE
    assert lab.own_pids() == []
    assert lab.service() is None, "служба драйвера осталась после полной остановки"
    print(f"      запуск {start_seconds:.2f} с, полная остановка {stop_seconds:.2f} с")


def scenario_restart_keeps_driver_loaded(lab: Lab) -> None:
    """Остановка внутри перезапуска драйвер не трогает; перезапуски без сбоев."""
    for _ in range(5):
        assert lab.runner2.start_from_preset_file(lab.preset_a, "e2e"), lab.runner2.last_error
        assert lab.runner2.stop(cleanup_services=False)
        assert lab.own_pids() == []
        info = lab.service()
        assert info is not None and info.state == winapi.SERVICE_RUNNING, info
    assert lab.runner2.stop(cleanup_services=True)
    assert lab.service() is None


def scenario_fast_preset_switch(lab: Lab) -> None:
    """Быстрая смена пресета: в итоге жив ровно один свой процесс — новый."""
    assert lab.runner2.start_from_preset_file(lab.preset_a, "a"), lab.runner2.last_error
    for index in range(6):
        previous_pid = lab.runner2.running_process.pid
        target = lab.preset_b if index % 2 == 0 else lab.preset_a
        assert lab.runner2.switch_preset_file_fast(target, f"switch{index}"), lab.runner2.last_error
        current_pid = lab.runner2.running_process.pid
        assert current_pid != previous_pid
        assert lab.own_pids() == [current_pid], lab.own_pids()
        assert _state(lab.runner2) == PresetRunnerState.RUNNING
    assert lab.runner2.stop(cleanup_services=True)


def scenario_orphan_engine_is_replaced(lab: Lab) -> None:
    """Свой winws, оставшийся от прошлого запуска программы, останавливается."""
    orphan = lab.spawn_raw(lab.exe2)
    assert lab.own_pids() == [orphan.pid]
    assert lab.runner2.start_from_preset_file(lab.preset_a, "e2e"), lab.runner2.last_error
    assert orphan.poll() is not None, "осиротевший процесс остался жив"
    assert lab.own_pids() == [lab.runner2.running_process.pid]
    assert lab.runner2.stop(cleanup_services=True)


def scenario_foreign_engine_is_not_touched(lab: Lab) -> None:
    """Чужой winws из другой папки не убивается и не считается остатком."""
    import shutil
    import tempfile

    foreign_root = tempfile.mkdtemp(prefix="zapret_e2e_foreign_")
    try:
        shutil.copytree(os.path.join(lab.root, "exe"), os.path.join(foreign_root, "exe"))
        foreign = lab.spawn_raw(os.path.join(foreign_root, "exe", "winws2.exe"))

        assert lab.runner2.start_from_preset_file(lab.preset_a, "e2e"), lab.runner2.last_error
        api = PresetLaunchRuntimeApi(lab.exe2)
        assert api.has_residual_processes(silent=True)

        assert lab.runner2.stop(cleanup_services=True)
        assert foreign.poll() is None, "чужой winws был убит"
        assert not api.has_residual_processes(silent=True), "чужой winws посчитан остатком"
        # Драйвером ещё пользуется чужой winws — выгружать его нельзя.
        info = lab.service()
        assert info is not None and info.state == winapi.SERVICE_RUNNING, info

        assert process_control.stop_process(foreign)
    finally:
        shutil.rmtree(foreign_root, ignore_errors=True)


def scenario_unexpected_death_is_reported_at_once(lab: Lab) -> None:
    """Смерть процесса извне замечается сразу, а не через секунды опроса."""
    assert lab.runner2.start_from_preset_file(lab.preset_a, "e2e"), lab.runner2.last_error
    process = lab.runner2.running_process
    started = time.perf_counter()
    subprocess.run(
        ["taskkill.exe", "/F", "/PID", str(process.pid)],
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    )
    assert lab.unexpected_exit.wait(3.0), "неожиданная смерть процесса не замечена"
    elapsed = time.perf_counter() - started
    assert elapsed < 1.5, f"смерть процесса замечена через {elapsed:.2f} с"
    assert process.poll() != process_control.ENGINE_KILL_EXIT_CODE
    lab.runner2.stop(cleanup_services=True)


def scenario_stuck_driver_entry_gives_clear_error(lab: Lab) -> None:
    """Застрявшая служба драйвера: понятная ошибка вместо долгих повторов."""
    assert lab.runner2.start_from_preset_file(lab.preset_a, "e2e"), lab.runner2.last_error
    assert lab.runner2.stop(cleanup_services=False)

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.OpenSCManagerW.restype = ctypes.c_void_p
    advapi32.OpenSCManagerW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    advapi32.OpenServiceW.restype = ctypes.c_void_p
    advapi32.OpenServiceW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    advapi32.CloseServiceHandle.argtypes = [ctypes.c_void_p]
    scm = advapi32.OpenSCManagerW(None, None, 1)
    held = advapi32.OpenServiceW(scm, SERVICE, 4)
    assert held
    try:
        # Имитация чужой программы: держит хэндл службы, драйвер остановлен.
        system_ops.release_windivert_driver_runtime(wait_seconds=0.5)
        started = time.perf_counter()
        ok = lab.runner2.start_from_preset_file(lab.preset_a, "e2e")
        elapsed = time.perf_counter() - started
        assert not ok, "запуск прошёл при застрявшей службе драйвера"
        message = str(lab.runner2.last_error or "")
        assert SERVICE in message and "держит" in message, message
        assert elapsed < 8.0, f"отказ занял {elapsed:.2f} с"
        assert lab.own_pids() == []
        print(f"      отказ за {elapsed:.2f} с: {message[:110]}...")
    finally:
        advapi32.CloseServiceHandle(held)
        advapi32.CloseServiceHandle(scm)

    assert lab.runner2.start_from_preset_file(lab.preset_a, "e2e"), lab.runner2.last_error
    assert lab.runner2.stop(cleanup_services=True)


def scenario_zapret1_start_stop(lab: Lab) -> None:
    """Раннер первой версии: запуск подтверждён выводом, остановка — хэндлом."""
    assert lab.runner1.start_from_preset_file(lab.preset_v1, "e2e"), lab.runner1.last_error
    assert _state(lab.runner1) == PresetRunnerState.RUNNING
    assert lab.own_pids() == [lab.runner1.running_process.pid]
    output = lab.runner1.read_post_mortem_output()
    assert "windivert initialized" in output, output[:300]
    assert lab.runner1.stop(cleanup_services=True)
    assert lab.own_pids() == []
    assert lab.service() is None


def scenario_orchestra_start_restart_stop(lab: Lab) -> None:
    """Оркестратор: запуск по строке готовности, перезапуск без паузы."""
    from orchestra.orchestra_runner import OrchestraRunner

    runner = OrchestraRunner()
    try:
        started = time.perf_counter()
        assert runner.start(), runner.last_start_error
        start_seconds = time.perf_counter() - started
        first_pid = runner.get_pid()
        assert lab.own_pids() == [first_pid]

        started = time.perf_counter()
        assert runner.restart(), runner.last_start_error
        restart_seconds = time.perf_counter() - started
        second_pid = runner.get_pid()
        assert second_pid != first_pid
        assert lab.own_pids() == [second_pid], lab.own_pids()

        started = time.perf_counter()
        assert runner.stop()
        stop_seconds = time.perf_counter() - started
        assert lab.own_pids() == []
        assert not runner.is_running()
        print(
            f"      запуск {start_seconds:.2f} с, перезапуск {restart_seconds:.2f} с, "
            f"остановка {stop_seconds:.2f} с"
        )
    finally:
        if runner.is_running():
            runner.stop()


SCENARIOS = (
    scenario_start_and_full_stop_removes_driver,
    scenario_restart_keeps_driver_loaded,
    scenario_fast_preset_switch,
    scenario_orphan_engine_is_replaced,
    scenario_foreign_engine_is_not_touched,
    scenario_unexpected_death_is_reported_at_once,
    scenario_stuck_driver_entry_gives_clear_error,
    scenario_zapret1_start_stop,
    scenario_orchestra_start_restart_stop,
)


def main() -> int:
    if os.name != "nt":
        print("Сквозная проверка работает только на Windows")
        return 2
    lab = Lab()
    print(f"Корень проверки: {lab.root}")
    failures = 0
    try:
        for scenario in SCENARIOS:
            name = scenario.__name__.removeprefix("scenario_")
            started = time.perf_counter()
            try:
                _clean_slate(lab)
                scenario(lab)
                print(f"PASS  {name}  ({time.perf_counter() - started:.2f} s)")
            except Exception:
                failures += 1
                print(f"FAIL  {name}  ({time.perf_counter() - started:.2f} s)")
                traceback.print_exc(file=sys.stdout)
        try:
            _clean_slate(lab)
        except Exception:
            failures += 1
            print("FAIL  final_cleanup")
            traceback.print_exc(file=sys.stdout)
    finally:
        try:
            lab.cleanup()
        except Exception:
            pass

    print(f"\n{max(0, len(SCENARIOS) - failures)}/{len(SCENARIOS)} сценариев прошло")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
