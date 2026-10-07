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
NETWORK_CALLS = {"https_get", "query_doh", "query_ipv4", "hosts_file_ipv4", "system_dns_servers"}
UPPER_LAYERS = ("engine.py", "sections.py", "problems.py", "report_text.py")


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

    def test_gateway_is_the_module_that_owns_the_network_calls(self) -> None:
        source = (SRC / "net_access.py").read_text(encoding="utf-8")
        self.assertEqual(imported_names(source) & NETWORK_CALLS, NETWORK_CALLS)

    def test_the_check_notices_a_direct_import(self) -> None:
        self.assertEqual(imported_names("from diagnostics.tls_probe import https_get\n") & NETWORK_CALLS, {"https_get"})

    def test_engine_stays_small(self) -> None:
        """Движок — порядок проверок и общий отчёт. Новую проверку кладут в свой модуль."""
        lines = len((SRC / "engine.py").read_text(encoding="utf-8").splitlines())
        self.assertLess(lines, 1000)


if __name__ == "__main__":
    unittest.main()
