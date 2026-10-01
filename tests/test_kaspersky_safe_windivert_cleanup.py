import unittest
from unittest.mock import patch


class KasperskyProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._reset_probe_cache()
        self.addCleanup(self._reset_probe_cache)

    @staticmethod
    def _reset_probe_cache() -> None:
        from utils import antivirus_probe

        antivirus_probe._cached_result = None
        antivirus_probe._cached_at = 0.0

    def test_detects_kaspersky_by_process_name(self) -> None:
        from utils import antivirus_probe

        with (
            patch.object(antivirus_probe, "iter_process_names_winapi", return_value=["explorer.exe", "AVP.exe"]),
            patch.object(antivirus_probe, "iter_uninstall_display_names", return_value=[]),
        ):
            self.assertTrue(antivirus_probe.is_kaspersky_present(force_refresh=True))

    def test_detects_kaspersky_by_uninstall_display_name_latin_and_cyrillic(self) -> None:
        from utils import antivirus_probe

        for display_name in ("Kaspersky Total Security", "Антивирус Касперского"):
            with (
                patch.object(antivirus_probe, "iter_process_names_winapi", return_value=[]),
                patch.object(antivirus_probe, "iter_uninstall_display_names", return_value=[display_name]),
            ):
                self.assertTrue(
                    antivirus_probe.is_kaspersky_present(force_refresh=True),
                    display_name,
                )

    def test_no_kaspersky_detected(self) -> None:
        from utils import antivirus_probe

        with (
            patch.object(antivirus_probe, "iter_process_names_winapi", return_value=["explorer.exe"]),
            patch.object(antivirus_probe, "iter_uninstall_display_names", return_value=["7-Zip"]),
        ):
            self.assertFalse(antivirus_probe.is_kaspersky_present(force_refresh=True))

    def test_probe_exception_yields_false_not_raise(self) -> None:
        from utils import antivirus_probe

        with patch.object(antivirus_probe, "iter_process_names_winapi", side_effect=OSError("boom")):
            self.assertFalse(antivirus_probe.is_kaspersky_present(force_refresh=True))

    def test_result_is_cached_within_ttl(self) -> None:
        from utils import antivirus_probe

        with (
            patch.object(antivirus_probe, "iter_process_names_winapi", return_value=["avp.exe"]) as processes,
            patch.object(antivirus_probe, "iter_uninstall_display_names", return_value=[]),
            patch.object(antivirus_probe.time, "monotonic", side_effect=[100.0, 100.0 + 1.0]),
        ):
            self.assertTrue(antivirus_probe.is_kaspersky_present())
            self.assertTrue(antivirus_probe.is_kaspersky_present())

        processes.assert_called_once()

    def test_cache_expires_after_ttl(self) -> None:
        from utils import antivirus_probe

        ttl = antivirus_probe._CACHE_TTL_SECONDS
        with (
            patch.object(antivirus_probe, "iter_process_names_winapi", return_value=[]) as processes,
            patch.object(antivirus_probe, "iter_uninstall_display_names", return_value=[]),
            patch.object(antivirus_probe.time, "monotonic", side_effect=[100.0, 100.0 + ttl + 1.0]),
        ):
            antivirus_probe.is_kaspersky_present()
            antivirus_probe.is_kaspersky_present()

        self.assertEqual(processes.call_count, 2)

    def test_startup_kaspersky_delegates_to_shared_probe(self) -> None:
        from startup import kaspersky

        self.assertFalse(hasattr(kaspersky, "_KASPERSKY_PROCESS_NAMES"))
        with patch.object(kaspersky, "is_kaspersky_present", return_value=True) as probe:
            self.assertTrue(kaspersky._check_kaspersky_antivirus())
        probe.assert_called_once()


class DriverReleaseKasperskySafeTests(unittest.TestCase):
    """Выгрузка драйвера WinDivert рядом с фильтрами Kaspersky запрещена.

    Принудительная выгрузка драйвера при активных WFP-фильтрах Kaspersky
    провоцировала синий экран в tcpip.sys, поэтому при этом антивирусе
    драйвер остаётся загруженным на любом пути, где программа его выгружает.
    """

    def _release(self, *, kaspersky=False, kaspersky_error=None, engines=()):
        from winws_runtime.engine import winapi
        from winws_runtime.runtime import system_ops

        running_driver = winapi.ServiceInfo(
            "Monkey",
            winapi.SERVICE_RUNNING,
            winapi.SERVICE_DISABLED,
            r"\??\C:\Zapret\Dev\exe\Monkey64.sys",
        )
        state = {"info": running_driver}

        def query_service(name):
            return state["info"] if name == "Monkey" else None

        def send_service_stop(name):
            state["info"] = None
            return winapi.SERVICE_STOPPED

        probe = patch(
            "utils.antivirus_probe.is_kaspersky_present",
            side_effect=kaspersky_error,
            return_value=kaspersky,
        )
        with (
            probe,
            patch.object(winapi, "query_service", side_effect=query_service),
            patch.object(winapi, "send_service_stop", side_effect=send_service_stop) as stop,
            patch.object(system_ops, "list_engine_processes", return_value=list(engines)),
            patch.object(system_ops, "own_windivert_roots", return_value=[r"C:\Zapret\Dev"]),
        ):
            result = system_ops.release_windivert_driver_runtime()
        return result, stop

    def test_kaspersky_blocks_driver_unload(self) -> None:
        from winws_runtime.engine import driver

        result, stop = self._release(kaspersky=True)

        self.assertEqual(result.outcome, driver.RELEASE_SKIPPED_ANTIVIRUS)
        stop.assert_not_called()

    def test_without_kaspersky_unused_driver_is_unloaded(self) -> None:
        from winws_runtime.engine import driver

        result, stop = self._release(kaspersky=False)

        self.assertEqual(result.outcome, driver.RELEASE_RELEASED)
        stop.assert_called_once_with("Monkey")

    def test_kaspersky_detection_failure_blocks_driver_unload(self) -> None:
        # Сбой детекта трактуется в безопасную сторону: цена ошибки — синий
        # экран, цена осторожности — драйвер, оставшийся загруженным.
        from winws_runtime.engine import driver

        result, stop = self._release(kaspersky_error=RuntimeError("probe broken"))

        self.assertEqual(result.outcome, driver.RELEASE_SKIPPED_ANTIVIRUS)
        stop.assert_not_called()

    def test_driver_in_use_is_checked_before_antivirus(self) -> None:
        from winws_runtime.engine import driver

        result, stop = self._release(kaspersky=True, engines=[(4321, "winws2.exe")])

        self.assertEqual(result.outcome, driver.RELEASE_SKIPPED_IN_USE)
        stop.assert_not_called()

    def test_recovery_after_failed_start_respects_kaspersky(self) -> None:
        # Восстановление после сбоя запуска идёт через ту же выгрузку.
        from winws_runtime.engine import driver
        from winws_runtime.runtime import system_ops

        released = driver.DriverReleaseResult(driver.RELEASE_SKIPPED_ANTIVIRUS, service="Monkey")
        with (
            patch.object(system_ops, "stop_own_winws_processes_runtime", return_value=True),
            patch.object(system_ops, "release_windivert_driver_runtime", return_value=released) as release,
            patch.object(
                system_ops,
                "ensure_windivert_driver_startable_runtime",
                return_value=driver.DriverPreflight(ok=True),
            ),
        ):
            recovered = system_ops.recover_windivert_runtime()

        self.assertTrue(recovered)
        release.assert_called_once_with()

if __name__ == "__main__":
    unittest.main()
