"""Сквозная проверка слоя управления движком на живой Windows.

Запускается на машине с установленной программой, от администратора:

    python tests/e2e_windows/run_engine_e2e.py [C:\\Zapret\\Dev]

Проверяет на настоящих winws2.exe и драйвере WinDivert то, что нельзя
проверить подделками: что хэндлы службы не утекают, что остановка
подтверждается сигналом Windows, что перезапуск не требует пауз, что драйвер
выгружается только когда им никто не пользуется, и что оба вида «служба
зависла» распознаются и проходят сами.

Движок запускается с фильтром на TCP-порт 9 (discard): он открывает драйвер
по-настоящему, но чужой трафик не трогает.

Имя файла намеренно не начинается с ``test_``: обычный pytest на Linux его не
собирает. Скрипт сам печатает итог и возвращает ненулевой код при провале.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.normpath(os.path.join(_HERE, "..", "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from winws_runtime.engine import driver, process_control, startup, winapi  # noqa: E402

CREATE_NO_WINDOW = 0x08000000
ENGINE_ARGS = ("--wf-tcp-out=9", "--wf-dup-check=0")
SERVICE = driver.OWN_DRIVER_SERVICE_NAME
STATUS_DLL_INIT_FAILED = 0xC0000142
DLL_INIT_RETRIES = 2


class Lab:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.exe = os.path.join(self.root, "exe", "winws2.exe")
        self.tmp = tempfile.mkdtemp(prefix="zapret_e2e_")
        self.foreign_dir = os.path.join(self.tmp, "foreign", "exe")
        self._seq = 0
        self.spawned: list[subprocess.Popen] = []
        self.dll_init_failures = 0

    # --- запуск -------------------------------------------------------------
    def spawn(self, exe: str | None = None, args=ENGINE_ARGS):
        self._seq += 1
        output_path = os.path.join(self.tmp, f"out_{self._seq}.log")
        exe_path = exe or self.exe
        with open(output_path, "wb") as output:
            process = subprocess.Popen(
                [exe_path, *args],
                cwd=os.path.dirname(os.path.dirname(exe_path)),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                creationflags=CREATE_NO_WINDOW,
            )
        self.spawned.append(process)
        return process, output_path

    def start_ready(self, exe: str | None = None):
        """Запускает движок и ждёт подтверждения готовности.

        Повторяет запуск при сбое инициализации процесса (0xC0000142) — так
        же, как это делает программа. Замер: сбой случается примерно раз на
        200 запусков, от пауз не зависит, немедленный повтор проходит.
        """
        for attempt in range(DLL_INIT_RETRIES + 1):
            process, output_path = self.spawn(exe)
            outcome = startup.wait_engine_ready(process, output_path)
            if outcome.started:
                break
            exit_code = process.poll()
            if exit_code is not None and (exit_code & 0xFFFFFFFF) == STATUS_DLL_INIT_FAILED:
                self.dll_init_failures += 1
                if attempt < DLL_INIT_RETRIES:
                    continue
            break
        assert outcome.started and outcome.reason == startup.REASON_READY, (
            f"движок не запустился: {outcome}; код {process.poll()}; вывод: {self.read(output_path)}"
        )
        return process, outcome

    @staticmethod
    def read(path: str) -> str:
        try:
            with open(path, "rb") as stream:
                return stream.read().decode("utf-8", "replace")
        except OSError:
            return ""

    # --- состояние ----------------------------------------------------------
    def any_engine_alive(self) -> bool:
        return bool(process_control.list_engine_processes(("winws.exe", "winws2.exe")))

    def release(self, **kwargs):
        return driver.release_driver_if_unused(
            own_roots=[self.root],
            engine_in_use=self.any_engine_alive,
            **kwargs,
        )

    def preflight(self, wait_seconds: float = 3.0):
        return driver.ensure_driver_startable(own_roots=[self.root], wait_seconds=wait_seconds)

    def make_foreign_copy(self) -> str:
        os.makedirs(self.foreign_dir, exist_ok=True)
        source_dir = os.path.dirname(self.exe)
        for name in os.listdir(source_dir):
            source = os.path.join(source_dir, name)
            if os.path.isfile(source):
                shutil.copy2(source, os.path.join(self.foreign_dir, name))
        return os.path.join(self.foreign_dir, "winws2.exe")

    def cleanup(self) -> None:
        for process in self.spawned:
            try:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)


def _clean_slate(lab: Lab) -> None:
    """Приводит систему к исходному виду: нет своих winws, драйвер выгружен."""
    process_control.stop_engine_processes([lab.exe])
    for process in lab.spawned:
        if process.poll() is None:
            process_control.stop_process(process)
    result = lab.release()
    assert not result.stuck, f"исходное состояние: драйвер застрял: {result}"


# ---------------------------------------------------------------------------
#  Сценарии
# ---------------------------------------------------------------------------

def scenario_bindings(lab: Lab) -> None:
    """У каждой функции WinAPI заданы типы; раскладка структур верная."""
    for library_name, table in winapi.signature_tables():
        library = getattr(winapi, f"_{library_name}")
        for name, (restype, argtypes) in table.items():
            function = getattr(library, name)
            assert function.argtypes is not None, f"{library_name}.{name}: нет argtypes"
            assert list(function.argtypes) == list(argtypes), f"{library_name}.{name}: argtypes"
            assert function.restype is restype, f"{library_name}.{name}: restype"
    # Именно эти две функции закрывают хэндлы; без argtypes 64-битный хэндл
    # службы не закрывался и сам держал службу драйвера.
    assert list(winapi._advapi32.CloseServiceHandle.argtypes) == [winapi.HANDLE]
    assert list(winapi._kernel32.CloseHandle.argtypes) == [winapi.HANDLE]
    assert ctypes.sizeof(winapi.SERVICE_STATUS) == 28
    assert ctypes.sizeof(winapi.QUERY_SERVICE_CONFIGW) == 64


def scenario_start_is_confirmed_by_engine_output(lab: Lab) -> None:
    """Запуск подтверждается строкой готовности самого движка."""
    process, outcome = lab.start_ready()
    assert outcome.marker_seen
    assert process.poll() is None
    info = winapi.query_service(SERVICE)
    assert info is not None and info.state == winapi.SERVICE_RUNNING, info
    # Штатное состояние работающего драйвера: отключён и помечен на удаление.
    assert info.start_type == winapi.SERVICE_DISABLED, info
    assert driver.is_own_driver(info.image_path, [lab.root]), info
    assert lab.preflight().ok
    assert process_control.stop_process(process)


def scenario_stop_is_confirmed_and_marked(lab: Lab) -> None:
    """Остановка подтверждена хэндлом процесса; код завершения — наш."""
    process, _ = lab.start_ready()
    started = time.perf_counter()
    assert process_control.stop_process(process, timeout=5.0)
    elapsed = time.perf_counter() - started
    assert process.poll() == process_control.ENGINE_STOP_EXIT_CODE, process.poll()
    assert elapsed < 1.0, f"остановка заняла {elapsed:.2f} с"


def scenario_failed_start_is_detected(lab: Lab) -> None:
    """Движок, умерший при запуске, распознаётся сразу, без ожидания срока."""
    process, output_path = lab.spawn(args=("--wf-raw=this is not a filter", "--wf-dup-check=0"))
    started = time.perf_counter()
    outcome = startup.wait_engine_ready(process, output_path)
    elapsed = time.perf_counter() - started
    assert not outcome.started and outcome.reason == startup.REASON_EXITED, outcome
    assert elapsed < 3.0, f"провал запуска замечен через {elapsed:.2f} с"


def scenario_restart_needs_no_pauses(lab: Lab) -> None:
    """20 перезапусков подряд: новый запуск сразу после подтверждённого выхода."""
    process, _ = lab.start_ready()
    for _ in range(20):
        assert process_control.stop_process(process)
        process, _ = lab.start_ready()
    assert process_control.stop_process(process)


def scenario_handoff_keeps_new_engine(lab: Lab) -> None:
    """Быстрая смена пресета: новый движок стартует при живом старом."""
    old, _ = lab.start_ready()
    new, _ = lab.start_ready()
    assert process_control.stop_process(old)
    assert not process_control.wait_process_exit(new, 0.3), "новый движок умер после остановки старого"
    assert process_control.stop_process(new)


def scenario_only_own_engines_are_stopped(lab: Lab) -> None:
    """Останавливаются только движки из нашей папки; чужой остаётся жив."""
    foreign_exe = lab.make_foreign_copy()
    own_a, _ = lab.start_ready()
    own_b, _ = lab.start_ready()
    foreign, _ = lab.start_ready(exe=foreign_exe)

    result = process_control.stop_engine_processes([lab.exe])
    assert result.ok, result
    assert sorted(result.stopped) == sorted([own_a.pid, own_b.pid]), result
    assert [record.pid for record in result.foreign] == [foreign.pid], result
    assert own_a.poll() is not None and own_b.poll() is not None
    assert foreign.poll() is None, "чужой движок был убит"

    # Пока жив чужой движок, драйвер занят — выгружать его нельзя.
    released = lab.release()
    assert released.outcome == driver.RELEASE_SKIPPED_IN_USE, released
    info = winapi.query_service(SERVICE)
    assert info is not None and info.state == winapi.SERVICE_RUNNING, info

    assert process_control.stop_process(foreign)


def scenario_driver_released_only_when_unused(lab: Lab) -> None:
    """Драйвер выгружается, только когда им никто не пользуется."""
    process, _ = lab.start_ready()
    assert lab.release().outcome == driver.RELEASE_SKIPPED_IN_USE
    assert process.poll() is None
    assert process_control.stop_process(process)

    started = time.perf_counter()
    released = lab.release()
    elapsed = time.perf_counter() - started
    assert released.outcome == driver.RELEASE_RELEASED, released
    assert winapi.query_service(SERVICE) is None
    assert elapsed < 3.0, f"выгрузка заняла {elapsed:.2f} с"
    assert lab.release().outcome == driver.RELEASE_ABSENT

    # После выгрузки движок сам ставит драйвер заново.
    process, _ = lab.start_ready()
    assert process_control.stop_process(process)


def scenario_antivirus_blocks_unload(lab: Lab) -> None:
    """При опасном антивирусе драйвер не выгружается."""
    process, _ = lab.start_ready()
    assert process_control.stop_process(process)
    result = lab.release(antivirus_blocks_unload=lambda: True)
    assert result.outcome == driver.RELEASE_SKIPPED_ANTIVIRUS, result
    info = winapi.query_service(SERVICE)
    assert info is not None and info.state == winapi.SERVICE_RUNNING, info


def scenario_service_queries_do_not_leak_handles(lab: Lab) -> None:
    """Сотни запросов состояния не держат службу: она исчезает после остановки.

    Это проверка на исходную поломку: незакрытый хэндл службы в нашем
    процессе не давал Windows убрать запись, помеченную на удаление.
    """
    process, _ = lab.start_ready()
    for _ in range(300):
        assert winapi.query_service(SERVICE) is not None
        lab.preflight(wait_seconds=0.0)
    assert process_control.stop_process(process)
    released = lab.release()
    assert released.outcome == driver.RELEASE_RELEASED, released
    assert winapi.query_service(SERVICE) is None


def scenario_stop_pending_is_named_and_recovers(lab: Lab) -> None:
    """Вид зависания №1: драйвер останавливают при живом движке."""
    process, _ = lab.start_ready()
    state = winapi.send_service_stop(SERVICE)  # так делать нельзя — это и есть поломка
    assert state == winapi.SERVICE_STOP_PENDING, state

    preflight = lab.preflight(wait_seconds=0.3)
    assert not preflight.ok and preflight.blocker == driver.BLOCKER_STOP_PENDING, preflight
    assert SERVICE in preflight.message

    # Лечение — завершить свои движки: драйвер выгружается сам.
    assert process_control.stop_engine_processes([lab.exe]).ok
    assert lab.preflight(wait_seconds=3.0).ok
    process, _ = lab.start_ready()
    assert process_control.stop_process(process)


def scenario_stuck_entry_is_named_and_recovers(lab: Lab) -> None:
    """Вид зависания №2: чужая программа держит хэндл службы."""
    process, _ = lab.start_ready()
    assert process_control.stop_process(process)

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.OpenSCManagerW.restype = ctypes.c_void_p
    advapi32.OpenSCManagerW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    advapi32.OpenServiceW.restype = ctypes.c_void_p
    advapi32.OpenServiceW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    advapi32.CloseServiceHandle.argtypes = [ctypes.c_void_p]
    scm = advapi32.OpenSCManagerW(None, None, 1)
    held = advapi32.OpenServiceW(scm, SERVICE, 4)
    assert held, "не удалось открыть хэндл службы для имитации"
    try:
        released = lab.release(wait_seconds=0.5)
        assert released.outcome == driver.RELEASE_STUCK_ENTRY, released
        preflight = lab.preflight(wait_seconds=0.3)
        assert not preflight.ok and preflight.blocker == driver.BLOCKER_STUCK_ENTRY, preflight

        # Именно так выглядит поломка для пользователя: движок не стартует.
        failed, output_path = lab.spawn()
        outcome = startup.wait_engine_ready(failed, output_path)
        assert not outcome.started, outcome
    finally:
        advapi32.CloseServiceHandle(held)
        advapi32.CloseServiceHandle(scm)

    # Хэндл закрыт — запись уходит сама, реестр никто не трогал.
    assert lab.preflight(wait_seconds=3.0).ok
    assert winapi.query_service(SERVICE) is None
    process, _ = lab.start_ready()
    assert process_control.stop_process(process)


def scenario_plain_leftover_entry_is_removed(lab: Lab) -> None:
    """Остановленная запись без пометки удаления убирается штатным вызовом."""
    _clean_slate(lab)
    driver_file = os.path.join(lab.root, "exe", f"{SERVICE}64.sys")
    created = subprocess.run(
        ["sc.exe", "create", SERVICE, "type=", "kernel", "start=", "demand", "binPath=", driver_file],
        capture_output=True,
        text=True,
        errors="replace",
    )
    assert created.returncode == 0, created.stdout + created.stderr
    info = winapi.query_service(SERVICE)
    assert info is not None and info.state == winapi.SERVICE_STOPPED, info
    assert info.start_type == winapi.SERVICE_DEMAND_START, info
    # Такая запись запуску не мешает: движок запустит драйвер сам.
    assert lab.preflight(wait_seconds=0.0).ok

    released = lab.release()
    assert released.outcome == driver.RELEASE_RELEASED, released
    assert winapi.query_service(SERVICE) is None


SCENARIOS = (
    scenario_bindings,
    scenario_start_is_confirmed_by_engine_output,
    scenario_stop_is_confirmed_and_marked,
    scenario_failed_start_is_detected,
    scenario_restart_needs_no_pauses,
    scenario_handoff_keeps_new_engine,
    scenario_only_own_engines_are_stopped,
    scenario_driver_released_only_when_unused,
    scenario_antivirus_blocks_unload,
    scenario_service_queries_do_not_leak_handles,
    scenario_stop_pending_is_named_and_recovers,
    scenario_stuck_entry_is_named_and_recovers,
    scenario_plain_leftover_entry_is_removed,
)


def main(argv: list[str]) -> int:
    if os.name != "nt":
        print("Сквозная проверка работает только на Windows")
        return 2
    root = argv[1] if len(argv) > 1 else os.environ.get("ZAPRET_E2E_ROOT", r"C:\Zapret\Dev")
    lab = Lab(root)
    if not os.path.exists(lab.exe):
        print(f"Не найден движок: {lab.exe}")
        return 2

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
        lab.cleanup()

    passed = max(0, len(SCENARIOS) - failures)
    print(f"\n{passed}/{len(SCENARIOS)} сценариев прошло")
    if lab.dll_init_failures:
        print(f"Разовых сбоев инициализации процесса (0xC0000142), снятых повтором: {lab.dll_init_failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
