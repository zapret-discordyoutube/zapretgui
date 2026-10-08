from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from windows_features import internet_cleanup
from windows_features.internet_cleanup import CleanupStep, run_internet_cleanup
from windows_features.internet_cleanup_winapi import NetworkWinApiError, SystemProxy, WinsockProvider

# Настоящий ответ `netsh interface ipv4 reset` исправной русской Windows 10 с правами
# администратора (сокращён): код завершения 1 и один защищённый пункт с отказом.
RESET_OUTPUT = (
    "Сброс Пересылка секций - OK!\n"
    "Сброс Глобальный - OK!\n"
    "Сброс Интерфейс - OK!\n"
    "Сброс Маршрут - OK!\n"
    "Сброс  - OK!\n"
    "Сброс  - сбой.\n"
    "Отказано в доступе.\n"
    "\n"
    "Сброс  - OK!\n"
    "Для завершения этого действия требуется перезагрузка.\n"
)
NO_RIGHTS_OUTPUT = "Запрошенная операция требует повышения прав (запустите от имени администратора).\n"
NETSH_COMMANDS = [
    ("interface", "ipv4", "reset"),
    ("interface", "ipv6", "reset"),
    ("interface", "ipv4", "set", "dynamicport", "tcp", "start=10000", "num=30000"),
]


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
        netsh: dict[str, tuple[int, str]] | None = None,
    ) -> None:
        self.winhttp_proxy = winhttp_proxy
        self.system_proxy = system_proxy
        self.listening = set(listening)
        self.winsock = list(winsock)
        self.fail = set(fail)
        # Ответ netsh по третьему слову команды: «reset» или «set».
        self.netsh = {"reset": (1, RESET_OUTPUT), "set": (0, "ОК.\n"), **(netsh or {})}
        self.netsh_calls: list[tuple[str, ...]] = []
        self.calls: list[str] = []

    def _call(self, name: str) -> None:
        if name in self.fail:
            raise NetworkWinApiError("нужны права администратора")
        self.calls.append(name)

    def run_netsh(self, *args: str) -> tuple[int, str]:
        self.netsh_calls.append(args)
        return self.netsh[args[2]]

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
            patch.object(internet_cleanup, "_run_netsh", windows.run_netsh),
        ]
        for item in patchers:
            item.start()
            self.addCleanup(item.stop)
        return run_internet_cleanup()

    def test_healthy_windows_is_reset_without_false_errors(self) -> None:
        # netsh на исправной Windows возвращает код 1 из-за одного защищённого пункта:
        # это не ошибка сброса, и «сброшено частично» из-за него показывать нельзя.
        windows = _FakeWindows()

        result = self._run(windows)

        self.assertEqual(windows.netsh_calls, NETSH_COMMANDS)
        self.assertEqual(windows.calls, ["flush_dns", "flush_caches"])
        self.assertEqual(result.level, "success")
        self.assertEqual(result.title, "Сеть Windows сброшена")
        self.assertEqual(
            result.content,
            "TCP/IP IPv4 сброшен. TCP/IP IPv6 сброшен. Динамические TCP-порты: 10000–39999. "
            "Кэш DNS очищен. Кэш адресов и маршрутов очищен.\n"
            "Менять не пришлось: прокси WinHTTP, системный прокси, Winsock.\n"
            "Перезагрузите Windows, чтобы сброс TCP/IP подействовал.",
        )

    def test_english_netsh_output_is_understood_too(self) -> None:
        output = "Resetting Global, OK!\nResetting Interface, OK!\nResetting , failed.\nAccess is denied.\n\nRestart the computer to complete this action.\n"

        self.assertEqual(internet_cleanup._count_reset_items(output), (2, 1))
        self.assertEqual(internet_cleanup._count_reset_items(RESET_OUTPUT), (6, 1))
        self.assertEqual(internet_cleanup._count_reset_items(NO_RIGHTS_OUTPUT), (0, 0))

    def test_tcpip_reset_without_admin_rights_is_reported(self) -> None:
        windows = _FakeWindows(netsh={"reset": (1, NO_RIGHTS_OUTPUT)})

        result = self._run(windows)

        self.assertEqual(result.level, "warning")
        self.assertEqual(windows.netsh_calls, NETSH_COMMANDS)
        self.assertIn(
            "сброс TCP/IP IPv4 — netsh вернул код 1: Запрошенная операция требует повышения прав",
            result.content,
        )
        self.assertIn("сброс TCP/IP IPv6 — netsh вернул код 1", result.content)
        self.assertNotIn("Перезагрузите Windows", result.content)

    def test_tcpip_reset_refused_in_most_items_is_a_failure(self) -> None:
        mostly_refused = "Сброс Глобальный - OK!\nСброс  - сбой.\nОтказано в доступе.\nСброс  - сбой.\nОтказано в доступе.\n"
        windows = _FakeWindows(netsh={"reset": (1, mostly_refused)})

        result = self._run(windows)

        self.assertEqual(result.level, "warning")
        self.assertIn("сброс TCP/IP IPv4 — netsh вернул код 1: Сброс  - сбой.", result.content)

    def test_dynamic_port_failure_is_reported(self) -> None:
        windows = _FakeWindows(netsh={"set": (1, "Параметр задан неверно.\n")})

        result = self._run(windows)

        self.assertEqual(result.level, "warning")
        self.assertIn("динамические TCP-порты — netsh вернул код 1: Параметр задан неверно.", result.content)
        self.assertIn("Перезагрузите Windows", result.content)

    def test_netsh_is_started_hidden_from_system32_with_windows_encoding(self) -> None:
        started: list[tuple[tuple, dict]] = []

        def fake_run_hidden(command, **kwargs):
            started.append((tuple(command), kwargs))
            return SimpleNamespace(returncode=0, stdout="ОК.\n", stderr="")

        with (
            patch.object(internet_cleanup, "run_hidden", fake_run_hidden),
            patch.object(internet_cleanup, "get_system_exe", lambda name: f"C:/Windows/System32/{name}"),
        ):
            result = internet_cleanup._run_netsh("interface", "ipv4", "reset")

        self.assertEqual(result, (0, "ОК.\n\n"))
        command, kwargs = started[0]
        self.assertEqual(command, ("C:/Windows/System32/netsh.exe", "interface", "ipv4", "reset"))
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["encoding"], internet_cleanup.NETSH_ENCODING)
        self.assertGreater(kwargs["timeout"], 0)
        self.assertNotIn("shell", kwargs)

    def test_hung_netsh_gives_a_readable_error(self) -> None:
        def hung(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        with patch.object(internet_cleanup, "run_hidden", hung):
            result = run_internet_cleanup([CleanupStep("сброс TCP/IP IPv4", internet_cleanup._reset_tcpip_v4)])

        self.assertEqual(result.level, "error")
        self.assertEqual(result.content, "Не получилось: сброс TCP/IP IPv4 — netsh не ответил за 30 секунд.")

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

    def test_winapi_wrapper_does_not_start_external_programs(self) -> None:
        import inspect

        from windows_features import internet_cleanup_winapi

        source = inspect.getsource(internet_cleanup_winapi)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("run_hidden", source)

    def test_confirmation_lists_what_is_done_and_warns_about_reboot(self) -> None:
        import presets.ui.control.control_runtime as control_runtime

        plan = control_runtime.build_internet_cleanup_start_plan(language="ru")

        self.assertFalse(plan.blocked)
        self.assertEqual(len(plan.confirmations), 1)
        content = plan.confirmations[0].content
        for expected in ("TCP/IP", "10000–39999", "кэша DNS", "WinHTTP", "системного прокси", "Winsock"):
            self.assertIn(expected, content)
        self.assertIn("понадобится перезагрузка", content)


if __name__ == "__main__":
    unittest.main()
