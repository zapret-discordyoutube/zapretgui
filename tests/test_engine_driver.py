"""Правила обращения со службой драйвера WinDivert (winws_runtime.engine.driver).

Диспетчер служб здесь подделан, но ведёт себя так, как настоящий вёл себя в
замерах на живой Windows: работающий драйвер помечен на удаление и исчезает
после остановки; при живом держателе остановка зависает в «останавливается»;
при чужом открытом хэндле остановленная запись остаётся «отключённой».
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from winws_runtime.engine import driver, winapi

OWN_ROOT = r"C:\Zapret\Dev"
OWN_DRIVER = r"\??\C:\Zapret\Dev\exe\Monkey64.sys"
FOREIGN_DRIVER = r"\??\C:\Other\zapret\exe\Monkey64.sys"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeScm:
    """Подделка диспетчера служб с поведением настоящего."""

    def __init__(self) -> None:
        self.services: dict[str, winapi.ServiceInfo] = {}
        # Держатель устройства драйвера: пока он жив, остановка не завершается.
        self.device_holder_alive = False
        # Чужой открытый хэндл службы: запись не исчезает после остановки.
        self.foreign_service_handle = False
        self.stop_requests: list[str] = []
        self.delete_requests: list[str] = []
        self.query_error: winapi.WinApiError | None = None

    def add(self, name, state, start_type, image_path=OWN_DRIVER) -> None:
        self.services[name] = winapi.ServiceInfo(name, state, start_type, image_path)

    def add_running_driver(self, name="Monkey", image_path=OWN_DRIVER) -> None:
        # Штатный вид работающего драйвера: отключён и помечен на удаление.
        self.add(name, winapi.SERVICE_RUNNING, winapi.SERVICE_DISABLED, image_path)

    def _settle(self, name: str) -> None:
        info = self.services.get(name)
        if info is None:
            return
        if info.state == winapi.SERVICE_STOP_PENDING and not self.device_holder_alive:
            info = winapi.ServiceInfo(name, winapi.SERVICE_STOPPED, info.start_type, info.image_path)
            self.services[name] = info
        marked = info.start_type == winapi.SERVICE_DISABLED
        if info.state == winapi.SERVICE_STOPPED and marked and not self.foreign_service_handle:
            del self.services[name]

    def query_service(self, name: str):
        if self.query_error is not None:
            raise self.query_error
        self._settle(name)
        return self.services.get(name)

    def send_service_stop(self, name: str):
        self.stop_requests.append(name)
        info = self.services.get(name)
        if info is None:
            return None
        next_state = winapi.SERVICE_STOP_PENDING if self.device_holder_alive else winapi.SERVICE_STOPPED
        self.services[name] = winapi.ServiceInfo(name, next_state, info.start_type, info.image_path)
        return next_state

    def delete_service(self, name: str) -> bool:
        self.delete_requests.append(name)
        info = self.services.get(name)
        if info is None:
            return False
        self.services[name] = winapi.ServiceInfo(name, info.state, winapi.SERVICE_DISABLED, info.image_path)
        return True


class DriverTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.scm = FakeScm()
        self.time = FakeClock()
        for name in ("query_service", "send_service_stop", "delete_service"):
            patcher = patch.object(winapi, name, getattr(self.scm, name))
            patcher.start()
            self.addCleanup(patcher.stop)

    def release(self, *, in_use=False, antivirus=None, wait_seconds=3.0):
        return driver.release_driver_if_unused(
            own_roots=[OWN_ROOT],
            engine_in_use=lambda: in_use,
            antivirus_blocks_unload=antivirus,
            wait_seconds=wait_seconds,
            clock=self.time.clock,
            sleep=self.time.sleep,
        )

    def preflight(self, wait_seconds=3.0):
        return driver.ensure_driver_startable(
            own_roots=[OWN_ROOT],
            wait_seconds=wait_seconds,
            clock=self.time.clock,
            sleep=self.time.sleep,
        )


class ReleaseDriverTests(DriverTestCase):
    def test_absent_service_is_normal(self) -> None:
        result = self.release()

        self.assertEqual(result.outcome, driver.RELEASE_ABSENT)
        self.assertEqual(self.scm.stop_requests, [])

    def test_unused_driver_is_stopped_and_its_entry_disappears(self) -> None:
        self.scm.add_running_driver()

        result = self.release()

        self.assertEqual(result.outcome, driver.RELEASE_RELEASED)
        self.assertEqual(self.scm.stop_requests, ["Monkey"])
        self.assertNotIn("Monkey", self.scm.services)
        # Запись помечена на удаление самим WinDivert: удалять её не нужно.
        self.assertEqual(self.scm.delete_requests, [])
        # Запись исчезла сразу — ждать было нечего.
        self.assertEqual(self.time.sleeps, [])

    def test_driver_in_use_is_never_stopped(self) -> None:
        # Остановка при живом winws и создаёт зависшую службу.
        self.scm.add_running_driver()

        result = self.release(in_use=True)

        self.assertEqual(result.outcome, driver.RELEASE_SKIPPED_IN_USE)
        self.assertEqual(self.scm.stop_requests, [])
        self.assertEqual(self.scm.services["Monkey"].state, winapi.SERVICE_RUNNING)

    def test_failed_in_use_check_counts_as_in_use(self) -> None:
        self.scm.add_running_driver()

        def broken() -> bool:
            raise OSError("snapshot failed")

        result = driver.release_driver_if_unused(
            own_roots=[OWN_ROOT],
            engine_in_use=broken,
            clock=self.time.clock,
            sleep=self.time.sleep,
        )

        self.assertEqual(result.outcome, driver.RELEASE_SKIPPED_IN_USE)
        self.assertEqual(self.scm.stop_requests, [])

    def test_antivirus_blocks_unload(self) -> None:
        self.scm.add_running_driver()

        result = self.release(antivirus=lambda: True)

        self.assertEqual(result.outcome, driver.RELEASE_SKIPPED_ANTIVIRUS)
        self.assertEqual(self.scm.stop_requests, [])

    def test_failed_antivirus_detection_blocks_unload(self) -> None:
        # Сбой детекта трактуется в безопасную сторону: драйвер не выгружаем.
        self.scm.add_running_driver()

        def broken() -> bool:
            raise RuntimeError("probe failed")

        result = self.release(antivirus=broken)

        self.assertEqual(result.outcome, driver.RELEASE_SKIPPED_ANTIVIRUS)
        self.assertEqual(self.scm.stop_requests, [])

    def test_stop_pending_is_reported_as_stuck_with_reason(self) -> None:
        # Кто-то, кого мы не видим, держит устройство драйвера.
        self.scm.add_running_driver()
        self.scm.device_holder_alive = True

        result = self.release(wait_seconds=0.2)

        self.assertEqual(result.outcome, driver.RELEASE_STUCK_STOP_PENDING)
        self.assertTrue(result.stuck)
        self.assertIn("Monkey", result.message)
        self.assertEqual(self.scm.services["Monkey"].state, winapi.SERVICE_STOP_PENDING)
        # Ожидание ограничено сроком, а не длится вечно.
        self.assertLessEqual(sum(self.time.sleeps), 0.2 + driver._POLL_INTERVAL_SECONDS)

    def test_foreign_service_handle_is_reported_as_stuck_entry(self) -> None:
        self.scm.add_running_driver()
        self.scm.foreign_service_handle = True

        result = self.release(wait_seconds=0.2)

        self.assertEqual(result.outcome, driver.RELEASE_STUCK_ENTRY)
        self.assertTrue(result.stuck)
        self.assertIn("держит", result.message)
        # Запись не «добивается» повторным удалением и правкой реестра.
        self.assertEqual(self.scm.delete_requests, [])

    def test_entry_disappears_while_waiting(self) -> None:
        # Держатель завершился во время ожидания — запись уходит сама.
        self.scm.add_running_driver()
        self.scm.device_holder_alive = True
        original_sleep = self.time.sleep

        def sleep_and_release_holder(seconds: float) -> None:
            original_sleep(seconds)
            if len(self.time.sleeps) >= 3:
                self.scm.device_holder_alive = False

        result = driver.release_driver_if_unused(
            own_roots=[OWN_ROOT],
            engine_in_use=lambda: False,
            wait_seconds=3.0,
            clock=self.time.clock,
            sleep=sleep_and_release_holder,
        )

        self.assertEqual(result.outcome, driver.RELEASE_RELEASED)
        self.assertEqual(len(self.time.sleeps), 3)

    def test_plain_stopped_leftover_is_deleted_through_scm(self) -> None:
        # Остановленная запись без пометки — остаток прошлых версий программы.
        self.scm.add("Monkey", winapi.SERVICE_STOPPED, winapi.SERVICE_DEMAND_START)

        result = self.release()

        self.assertEqual(result.outcome, driver.RELEASE_RELEASED)
        self.assertEqual(self.scm.delete_requests, ["Monkey"])
        self.assertEqual(self.scm.stop_requests, [])
        self.assertNotIn("Monkey", self.scm.services)

    def test_unused_monkey_driver_from_another_folder_is_released(self) -> None:
        # Под именем Monkey работает и наш winws; неиспользуемый драйвер из
        # чужой папки выгружаем, чтобы следующий запуск поставил свой.
        self.scm.add_running_driver(image_path=FOREIGN_DRIVER)

        result = self.release()

        self.assertEqual(result.outcome, driver.RELEASE_RELEASED)

    def test_original_windivert_of_another_program_is_never_touched(self) -> None:
        self.scm.add("WinDivert", winapi.SERVICE_RUNNING, winapi.SERVICE_DEMAND_START,
                     r"\??\C:\GoodbyeDPI\x86_64\WinDivert64.sys")

        result = self.release()

        self.assertEqual(result.outcome, driver.RELEASE_ABSENT)
        self.assertEqual(self.scm.stop_requests, [])
        self.assertEqual(self.scm.delete_requests, [])
        self.assertIn("WinDivert", self.scm.services)

    def test_original_windivert_from_own_folder_is_ours(self) -> None:
        self.scm.add("WinDivert", winapi.SERVICE_RUNNING, winapi.SERVICE_DISABLED,
                     r"\??\C:\Zapret\Dev\exe\WinDivert64.sys")

        result = self.release()

        self.assertEqual(result.outcome, driver.RELEASE_RELEASED)
        self.assertEqual(self.scm.stop_requests, ["WinDivert"])

    def test_scm_failure_is_an_error_not_absence(self) -> None:
        # «Не удалось узнать» нельзя принимать за «службы нет».
        self.scm.query_error = winapi.WinApiError("OpenSCManagerW", 5)

        result = self.release()

        self.assertEqual(result.outcome, driver.RELEASE_ERROR)
        self.assertFalse(result.stuck)


class DriverPreflightTests(DriverTestCase):
    def test_absent_service_is_startable(self) -> None:
        self.assertTrue(self.preflight().ok)

    def test_running_disabled_marked_driver_is_the_normal_state(self) -> None:
        # Именно это состояние прежний код принимал за поломку.
        self.scm.add_running_driver()

        result = self.preflight()

        self.assertTrue(result.ok)
        self.assertEqual(self.time.sleeps, [])
        self.assertEqual(self.scm.stop_requests, [])

    def test_plain_stopped_entry_is_startable(self) -> None:
        self.scm.add("Monkey", winapi.SERVICE_STOPPED, winapi.SERVICE_DEMAND_START)

        self.assertTrue(self.preflight().ok)

    def test_stop_pending_blocks_start_and_names_the_service(self) -> None:
        self.scm.add("Monkey", winapi.SERVICE_STOP_PENDING, winapi.SERVICE_DISABLED)
        self.scm.device_holder_alive = True

        result = self.preflight(wait_seconds=0.2)

        self.assertFalse(result.ok)
        self.assertEqual(result.blocker, driver.BLOCKER_STOP_PENDING)
        self.assertEqual(result.service, "Monkey")
        self.assertIn("Monkey", result.message)

    def test_stuck_entry_blocks_start(self) -> None:
        self.scm.add("Monkey", winapi.SERVICE_STOPPED, winapi.SERVICE_DISABLED)
        self.scm.foreign_service_handle = True

        result = self.preflight(wait_seconds=0.2)

        self.assertFalse(result.ok)
        self.assertEqual(result.blocker, driver.BLOCKER_STUCK_ENTRY)

    def test_unloading_driver_gets_time_to_finish(self) -> None:
        self.scm.add("Monkey", winapi.SERVICE_STOP_PENDING, winapi.SERVICE_DISABLED)
        self.scm.device_holder_alive = True
        original_sleep = self.time.sleep

        def sleep_and_release_holder(seconds: float) -> None:
            original_sleep(seconds)
            self.scm.device_holder_alive = False

        result = driver.ensure_driver_startable(
            own_roots=[OWN_ROOT],
            wait_seconds=3.0,
            clock=self.time.clock,
            sleep=sleep_and_release_holder,
        )

        self.assertTrue(result.ok)
        self.assertEqual(len(self.time.sleeps), 1)

    def test_preflight_never_changes_the_service(self) -> None:
        self.scm.add("Monkey", winapi.SERVICE_STOPPED, winapi.SERVICE_DISABLED)
        self.scm.foreign_service_handle = True

        self.preflight(wait_seconds=0.2)

        self.assertEqual(self.scm.stop_requests, [])
        self.assertEqual(self.scm.delete_requests, [])

    def test_foreign_windivert_entry_does_not_block_our_start(self) -> None:
        self.scm.add("WinDivert", winapi.SERVICE_STOP_PENDING, winapi.SERVICE_DEMAND_START,
                     r"\??\C:\GoodbyeDPI\x86_64\WinDivert64.sys")

        self.assertTrue(self.preflight(wait_seconds=0.2).ok)

    def test_scm_failure_does_not_block_start(self) -> None:
        # Причину отказа, если она есть, назовёт сам winws в своём выводе.
        self.scm.query_error = winapi.WinApiError("OpenSCManagerW", 5)

        self.assertTrue(self.preflight().ok)


class DriverPathTests(unittest.TestCase):
    def test_image_path_prefix_and_case_are_normalised(self) -> None:
        self.assertTrue(driver.is_own_driver(r"\??\c:\zapret\dev\EXE\Monkey64.sys", [OWN_ROOT]))
        self.assertTrue(driver.is_own_driver('"C:\\Zapret\\Dev\\exe\\Monkey64.sys"', [OWN_ROOT]))

    def test_sibling_folder_is_not_own(self) -> None:
        # C:\Zapret\Dev2 не должна считаться частью C:\Zapret\Dev.
        self.assertFalse(driver.is_own_driver(r"\??\C:\Zapret\Dev2\exe\Monkey64.sys", [OWN_ROOT]))

    def test_empty_path_is_not_own(self) -> None:
        self.assertFalse(driver.is_own_driver("", [OWN_ROOT]))


class NoRegistryTamperingTests(unittest.TestCase):
    def test_driver_layer_does_not_touch_the_registry(self) -> None:
        # Правка реестра за спиной диспетчера служб и создавала записи,
        # которые оставались до перезагрузки.
        import inspect

        for module in (driver, winapi):
            source = inspect.getsource(module)
            self.assertNotIn("winreg", source, module.__name__)
            self.assertNotIn("ChangeServiceConfig", source, module.__name__)
            self.assertNotIn("sc.exe", source, module.__name__)


if __name__ == "__main__":
    unittest.main()
