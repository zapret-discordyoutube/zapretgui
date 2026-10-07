from __future__ import annotations

import unittest
from unittest.mock import patch

from windows_features import internet_cleanup
from windows_features.internet_cleanup import CleanupStep, run_internet_cleanup
from windows_features.internet_cleanup_winapi import NetworkWinApiError, SystemProxy, WinsockProvider


class _FakeWindows:
    """Windows для тестов: помнит, что в ней поменяли."""

    def __init__(
        self,
        *,
        winhttp_proxy: str = "",
        system_proxy: SystemProxy = SystemProxy(enabled=False, server=""),
        listening: set[tuple[str, int]] = frozenset(),
        winsock: tuple[WinsockProvider, ...] = (),
        fail: set[str] = frozenset(),
    ) -> None:
        self.winhttp_proxy = winhttp_proxy
        self.system_proxy = system_proxy
        self.listening = set(listening)
        self.winsock = list(winsock)
        self.fail = set(fail)
        self.calls: list[str] = []

    def _call(self, name: str) -> None:
        if name in self.fail:
            raise NetworkWinApiError("нужны права администратора")
        self.calls.append(name)

    def flush_resolver_cache(self) -> bool:
        self.calls.append("flush_dns")
        return "flush_dns" not in self.fail

    def flush_neighbor_and_path_caches(self) -> None:
        self._call("flush_caches")

    def read_winhttp_proxy(self) -> str:
        return self.winhttp_proxy

    def reset_winhttp_proxy(self) -> None:
        self._call("reset_winhttp")
        self.winhttp_proxy = ""

    def read_system_proxy(self) -> SystemProxy:
        return self.system_proxy

    def disable_system_proxy(self) -> None:
        self._call("disable_system_proxy")
        self.system_proxy = SystemProxy(enabled=False, server=self.system_proxy.server)

    def list_layered_winsock_providers(self) -> tuple[WinsockProvider, ...]:
        return tuple(self.winsock)

    def remove_winsock_provider(self, provider: WinsockProvider) -> None:
        self._call("remove_winsock")
        self.winsock.remove(provider)

    def accepts_connections(self, host: str, port: int) -> bool:
        self.calls.append(f"probe {host}:{port}")
        return (host, port) in self.listening


class InternetCleanupTests(unittest.TestCase):
    def _run(self, windows: _FakeWindows):
        winapi_names = (
            "flush_neighbor_and_path_caches",
            "read_winhttp_proxy",
            "reset_winhttp_proxy",
            "read_system_proxy",
            "disable_system_proxy",
            "list_layered_winsock_providers",
            "remove_winsock_provider",
        )
        patchers = [
            *(patch.object(internet_cleanup.winapi, name, getattr(windows, name)) for name in winapi_names),
            patch("dns.winapi.flush_resolver_cache", windows.flush_resolver_cache),
            patch.object(internet_cleanup, "_accepts_connections", windows.accepts_connections),
        ]
        for item in patchers:
            item.start()
            self.addCleanup(item.stop)
        return run_internet_cleanup()

    def test_healthy_windows_only_gets_its_caches_flushed(self) -> None:
        windows = _FakeWindows()

        result = self._run(windows)

        self.assertEqual(windows.calls, ["flush_dns", "flush_caches"])
        self.assertEqual(result.level, "success")
        self.assertEqual(
            result.content,
            "Кэш DNS очищен.\n"
            "Кэш адресов и маршрутов очищен.\n"
            "Менять не пришлось: прокси WinHTTP, системный прокси, Winsock.",
        )

    def test_winhttp_proxy_is_reset_when_set(self) -> None:
        windows = _FakeWindows(winhttp_proxy="10.0.0.5:3128")

        result = self._run(windows)

        self.assertEqual(windows.winhttp_proxy, "")
        self.assertIn("Прокси WinHTTP (10.0.0.5:3128) сброшен.", result.content)

    def test_dead_local_system_proxy_is_switched_off(self) -> None:
        windows = _FakeWindows(
            system_proxy=SystemProxy(enabled=True, server="http=127.0.0.1:10809;socks=127.0.0.1:10808"),
        )

        result = self._run(windows)

        self.assertFalse(windows.system_proxy.enabled)
        self.assertIn("probe 127.0.0.1:10809", windows.calls)
        self.assertIn("probe 127.0.0.1:10808", windows.calls)
        self.assertIn("Системный прокси http=127.0.0.1:10809;socks=127.0.0.1:10808 отключён", result.content)

    def test_working_or_remote_system_proxy_is_left_alone(self) -> None:
        cases = {
            "локальный прокси отвечает": _FakeWindows(
                system_proxy=SystemProxy(enabled=True, server="127.0.0.1:10809"),
                listening={("127.0.0.1", 10809)},
            ),
            "отвечает хотя бы один из адресов": _FakeWindows(
                system_proxy=SystemProxy(enabled=True, server="http=localhost:8080;socks=[::1]:1080"),
                listening={("::1", 1080)},
            ),
            "прокси в сети": _FakeWindows(system_proxy=SystemProxy(enabled=True, server="proxy.corp.example:3128")),
            "часть адресов в сети": _FakeWindows(
                system_proxy=SystemProxy(enabled=True, server="http=127.0.0.1:8080;https=10.0.0.5:3128"),
            ),
            "адрес без порта": _FakeWindows(system_proxy=SystemProxy(enabled=True, server="127.0.0.1")),
            "прокси выключен": _FakeWindows(system_proxy=SystemProxy(enabled=False, server="127.0.0.1:10809")),
        }
        for name, windows in cases.items():
            with self.subTest(name):
                result = self._run(windows)

                self.assertNotIn("disable_system_proxy", windows.calls)
                self.assertIn("системный прокси", result.content.split("Менять не пришлось:")[1])

    def test_remote_proxy_is_never_probed(self) -> None:
        windows = _FakeWindows(system_proxy=SystemProxy(enabled=True, server="proxy.corp.example:3128"))

        self._run(windows)

        self.assertFalse([call for call in windows.calls if call.startswith("probe")])

    def test_foreign_winsock_addons_are_removed_from_both_catalogs(self) -> None:
        addons = (
            WinsockProvider(name="Speed Booster over [TCP/IP]", provider_id=b"a" * 16, for_32bit_apps=False),
            WinsockProvider(name="Speed Booster over [TCP/IP]", provider_id=b"a" * 16, for_32bit_apps=True),
            WinsockProvider(name="Speed Booster", provider_id=b"b" * 16, for_32bit_apps=False),
        )
        windows = _FakeWindows(winsock=addons)

        result = self._run(windows)

        self.assertEqual(windows.winsock, [])
        self.assertEqual(windows.calls.count("remove_winsock"), 3)
        self.assertIn("Удалены надстройки Winsock: «Speed Booster over [TCP/IP]», «Speed Booster».", result.content)
        self.assertIn("Перезапустите браузер", result.content)

    def test_failed_step_does_not_stop_the_rest(self) -> None:
        windows = _FakeWindows(winhttp_proxy="10.0.0.5:3128", fail={"flush_caches", "reset_winhttp"})

        result = self._run(windows)

        self.assertEqual(result.level, "warning")
        self.assertEqual(result.title, "Сеть сброшена частично")
        self.assertIn("Кэш DNS очищен.", result.content)
        self.assertIn(
            "Не получилось: кэш адресов и маршрутов — нужны права администратора; "
            "прокси WinHTTP — нужны права администратора.",
            result.content,
        )

    def test_dns_cache_refusal_is_reported(self) -> None:
        result = self._run(_FakeWindows(fail={"flush_dns"}))

        self.assertEqual(result.level, "warning")
        self.assertIn("кэш DNS — Windows не смогла очистить кэш DNS", result.content)

    def test_all_steps_failed_is_an_error(self) -> None:
        def broken() -> str:
            raise OSError("нет доступа")

        result = run_internet_cleanup([CleanupStep("кэш DNS", broken), CleanupStep("Winsock", broken)])

        self.assertEqual(result.level, "error")
        self.assertEqual(result.title, "Сброс сети не выполнен")
        self.assertEqual(result.content, "Не получилось: кэш DNS — нет доступа; Winsock — нет доступа.")

    def test_proxy_address_forms(self) -> None:
        parse = internet_cleanup._proxy_endpoints

        self.assertEqual(parse("127.0.0.1:10809"), [("127.0.0.1", 10809)])
        self.assertEqual(parse("http://localhost:8080/"), [("localhost", 8080)])
        self.assertEqual(
            parse("http=127.0.0.1:1;https=127.0.0.1:1;socks=[::1]:2"),
            [("127.0.0.1", 1), ("::1", 2)],
        )
        for unclear in ("", "127.0.0.1", "127.0.0.1:0", "127.0.0.1:99999", "::1:8080", "host:port"):
            with self.subTest(unclear):
                self.assertIsNone(parse(unclear))

    def test_module_does_not_start_external_programs(self) -> None:
        import inspect

        from windows_features import internet_cleanup_winapi

        for module in (internet_cleanup, internet_cleanup_winapi):
            source = inspect.getsource(module)
            self.assertNotIn("subprocess", source)
            self.assertNotIn("run_hidden", source)

    def test_confirmation_lists_what_is_done_and_promises_no_reboot(self) -> None:
        import presets.ui.control.control_runtime as control_runtime

        plan = control_runtime.build_internet_cleanup_start_plan(language="ru")

        self.assertFalse(plan.blocked)
        self.assertEqual(len(plan.confirmations), 1)
        content = plan.confirmations[0].content
        for expected in ("кэша DNS", "WinHTTP", "системного прокси", "Winsock", "Перезагрузка не нужна"):
            self.assertIn(expected, content)


if __name__ == "__main__":
    unittest.main()
