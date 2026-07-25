from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

import utils.windows_process_probe as shared_probe
import winws_runtime.runtime.process_probe as winws_probe


class ProcessProbeCtypesIsolationTests(unittest.TestCase):
    """Регрессия: 'expected LP_PROCESSENTRY32W instance instead of pointer to PROCESSENTRY32W'.

    Два модуля объявляли собственный PROCESSENTRY32W и выставляли argtypes на
    одни и те же функции глобально кэшированного ctypes.windll.kernel32 —
    argtypes последнего импортированного модуля ломали byref() первого.
    """

    def test_winws_probe_has_no_duplicate_struct(self) -> None:
        self.assertFalse(
            hasattr(winws_probe, "PROCESSENTRY32W"),
            "winws-probe должен переиспользовать снапшот из utils.windows_process_probe",
        )

    def test_snapshot_functions_are_not_global_windll_cache(self) -> None:
        import ctypes

        if not hasattr(ctypes, "windll"):
            self.skipTest("не Windows")
        global_kernel32 = ctypes.windll.kernel32
        self.assertIsNot(
            shared_probe._kernel32,
            global_kernel32,
            "снапшот-функции должны жить на приватном WinDLL, а не на глобальном windll",
        )
        self.assertIsNot(
            winws_probe._kernel32,
            global_kernel32,
            "probe-функции должны жить на приватном WinDLL, а не на глобальном windll",
        )

    @unittest.skipUnless(sys.platform == "win32", "снимок процессов делается через WinAPI")
    def test_both_probes_work_after_importing_both_modules(self) -> None:
        """Сценарий бага: оба модуля импортированы, оба зовут Process32FirstW."""
        shared_records = shared_probe.iter_process_records_winapi()
        self.assertTrue(shared_records, "снапшот процессов пуст — WinAPI-вызов сломан")

        # Не должно бросить TypeError и должно вернуть список (обычно пустой,
        # если winws не запущен; сам вызов проходит полный снапшот).
        winws_entries = winws_probe._iter_winws_process_entries()
        self.assertIsInstance(winws_entries, list)

        pids = winws_probe.get_canonical_winws_process_pids()
        self.assertIsInstance(pids, dict)

    @unittest.skipUnless(sys.platform == "win32", "снимок процессов делается через WinAPI")
    def test_winws_entries_are_subset_of_shared_snapshot_names(self) -> None:
        winws_names = {name for _pid, name in winws_probe._iter_winws_process_entries()}
        self.assertTrue(winws_names.issubset(winws_probe._WINWS_NAME_SET))


if __name__ == "__main__":
    unittest.main()
