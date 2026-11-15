"""Слои BlockCheck: в сеть ходит только ``diagnostics.net_access``.

Движок и проверки разделов собирают отчёт, но сами сокеты не открывают. Пока
это так, у каждого запроса общий срок и общая отмена по «Стоп», а подменить
сеть в тестах можно в одном месте.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "diagnostics"

# Функции, которые открывают соединение или спрашивают DNS.
NETWORK_CALLS = {"chrome_get", "query_doh", "query_ipv4", "hosts_file_ipv4", "system_dns_servers"}
# Измерения платформы (DNS системы, ping, трассировка) тоже идут только через шлюз net_access.
PLATFORM_NETPROBE = "netprobe"
UPPER_LAYERS = ("engine.py", "sections.py", "problems.py", "report_text.py", "registry.py", "filter_place.py", "filter_habits.py")


# Слой, который управляет проверкой и делает выводы. Пробы (``tls_probe``,
# ``quic_probe``, ``block_cause``…) — листья: они открывают сокеты сами, а этот
# слой зовёт их или ``net_access``, но не трогает сеть напрямую.
ORCHESTRATION = (
    "engine.py",
    "sections.py",
    "problems.py",
    "report_text.py",
    "report_upgrade.py",
    "report_contract.py",
    "verdict.py",
    "levels.py",
    "limits.py",
    "services.py",
    "block_kind.py",
    "history.py",
    "compare.py",
    "run_context.py",
    "reach.py",
    "registry.py",
    "filter_place.py",
    "filter_habits.py",
)
# Модули, весь смысл которых — сеть: сокеты, TLS, HTTP, пинг. Модули DNS здесь не
# названы: из них слой берёт типы и коды ответа, а сами запросы ловит список имён ниже.
NETWORK_MODULES = {
    "socket",
    "ssl",
    "urllib.request",
    "http.client",
    "requests",
    "platforms.windows.icmp",
    "utils.http2_post",
    "utils.chrome_https",
    "curl_cffi",
}
# Запросы DNS напрямую по протоколу: проверки DNS-серверов ходят через свои модули, не через этот слой.
DNS_WIRE_CALLS = {"query_udp", "query_tcp", "query_dot", "query_server"}


def imported_modules(source: str) -> set[str]:
    """Все импортируемые модули, в том числе внутри функций."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            # «from platforms.windows import icmp» — тоже сетевой модуль.
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def imported_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


class NetworkGatewayTests(unittest.TestCase):
    def test_upper_layers_do_not_touch_the_network_directly(self) -> None:
        for name in UPPER_LAYERS:
            with self.subTest(module=name):
                source = (SRC / name).read_text(encoding="utf-8")
                self.assertEqual(imported_names(source) & NETWORK_CALLS, set())

    def test_orchestration_opens_no_sockets_itself(self) -> None:
        for name in ORCHESTRATION:
            with self.subTest(module=name):
                source = (SRC / name).read_text(encoding="utf-8")
                self.assertEqual(imported_modules(source) & NETWORK_MODULES, set())
                self.assertEqual(imported_names(source) & (NETWORK_CALLS | DNS_WIRE_CALLS), set())

    def test_the_module_check_sees_nested_and_package_imports(self) -> None:
        source = "def f():\n    from platforms.windows import icmp\n    import socket\n"
        self.assertEqual(imported_modules(source) & NETWORK_MODULES, {"platforms.windows.icmp", "socket"})

    def test_gateway_is_the_module_that_owns_the_network_calls(self) -> None:
        source = (SRC / "net_access.py").read_text(encoding="utf-8")
        defined = {node.name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef)}
        self.assertEqual((imported_names(source) | defined) & NETWORK_CALLS, NETWORK_CALLS)

    def test_upper_layers_do_not_ask_the_platform_for_measurements_themselves(self) -> None:
        for name in (*UPPER_LAYERS, *ORCHESTRATION):
            with self.subTest(module=name):
                source = (SRC / name).read_text(encoding="utf-8")
                self.assertNotIn(PLATFORM_NETPROBE, source.replace("netprobe_types", ""))

    def test_gateway_asks_the_platform_for_measurements(self) -> None:
        source = (SRC / "net_access.py").read_text(encoding="utf-8")
        for call in ("query_ipv4", "system_dns_servers", "trace_hop_ipv4", "ping_ipv4_host", "ping_ipv6"):
            self.assertIn(f"netprobe.{call}(", source)

    def test_the_check_notices_a_direct_import(self) -> None:
        self.assertEqual(imported_names("from utils.chrome_https import chrome_get\n") & NETWORK_CALLS, {"chrome_get"})

    def test_progress_steps_have_one_owner(self) -> None:
        """Шаги хода проверки задаёт ``services``; подписи к ним есть и в тексте, и на экране."""
        from blockcheck.ui.result_cards import _STEP_TITLES
        from diagnostics import report_text, services

        self.assertEqual(tuple(report_text.STEP_TITLES), services.PROGRESS_STEPS)
        self.assertEqual(tuple(_STEP_TITLES), services.PROGRESS_STEPS)
        self.assertEqual(services.progress_steps(services.SCOPE_FULL), services.PROGRESS_STEPS)
        short = services.progress_steps(services.SCOPE_MAIN)
        self.assertNotIn(services.STEP_FILTER, short)
        self.assertEqual(short, services.progress_steps(services.SCOPE_ALL))

    def test_summary_model_works_without_qt(self) -> None:
        """Итог для экрана решается без окна: модуль модели не тянет PyQt и qfluentwidgets."""
        import subprocess
        import sys

        code = (
            "import sys; sys.path.insert(0, 'src'); import blockcheck.ui.summary_model, blockcheck.ui.result_cards_model; "
            "print(sorted(name for name in sys.modules if name.startswith(('PyQt6', 'qfluentwidgets'))))"
        )
        root = SRC.parents[1]
        out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), "[]")

    def test_engine_stays_small(self) -> None:
        """Движок — порядок проверок и общий отчёт. Новую проверку кладут в свой модуль."""
        lines = len((SRC / "engine.py").read_text(encoding="utf-8").splitlines())
        self.assertLess(lines, 1000)


if __name__ == "__main__":
    unittest.main()
