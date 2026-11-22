"""Суточная проверка адресов DNS после запуска: долгая проверка не теряется из-за оборванного запуска.

Так было на деле (win10, 2026-11-22): первый запуск взял суточное разрешение, умер через 7 секунд, не дойдя до
конца минутной проверки адресов чужих серверов, а второй запуск получил отказ на сутки.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from main import post_startup_zapret_dns as watch
from utils import probe_gate


class _Gate:
    """Хранилище отметок шлюза проб в памяти вместо settings.sqlite3, часы — наши."""

    def __init__(self) -> None:
        self.now = 1_000_000.0
        self.stored: dict[str, float] = {}

    def patches(self):
        real = probe_gate.try_background

        # Часы в ``try_background`` стоят аргументом по умолчанию (``time.time`` вычислен при определении
        # функции), поэтому подмена ``time.time`` не подействует: часы передаются явно.
        def with_our_clock(kind, **kwargs):
            return real(kind, clock=lambda: self.now, **kwargs)

        return [
            patch("settings.store.get_probe_gate_last_run", lambda key: self.stored.get(key, 0.0)),
            patch("settings.store.set_probe_gate_last_run", lambda key, when: self.stored.__setitem__(key, when)),
            patch.object(probe_gate, "try_background", with_our_clock),
        ]


class _Dns:
    def __init__(self, *, answered: int = 33, silent: int = 0, die: bool = False) -> None:
        self.answered, self.silent, self.die = answered, silent, die
        self.zapret_calls = 0
        self.provider_calls = 0
        self.migrations = 0

    def refresh_zapret_dns_server(self):
        self.zapret_calls += 1
        return SimpleNamespace(changed=False, relay_changed=False, detail="адрес не изменился")

    def refresh_provider_addresses(self):
        self.provider_calls += 1
        if self.die:
            raise SystemExit("программу закрыли на середине проверки")
        make = SimpleNamespace
        return [make(name=f"a{i}", changed=False, answered=True) for i in range(self.answered)] + [
            make(name=f"s{i}", changed=False, answered=False) for i in range(self.silent)
        ]

    def provider_run_completed(self, changes) -> bool:
        from dns.provider_addresses import ProviderChange, run_completed

        return run_completed(
            [ProviderChange(item.name, (), item.changed, "", answered=item.answered) for item in changes]
        )

    def migrate_outdated_dns_addresses(self):
        self.migrations += 1
        return []


def _launch(gate: _Gate, dns: _Dns) -> None:
    """Один запуск программы: ставит наблюдателя и сразу выполняет его задачу в этом же потоке."""
    host = SimpleNamespace(startup_interactive_ready=None, startup_state=SimpleNamespace(interactive_logged=True))
    with (
        patch.object(watch, "bind_startup_gate", lambda _signal, start, is_ready=None: start()),
        patch.object(watch, "enqueue_subsystem_task", lambda _queue, _name, task: task()),
        patch.object(watch, "schedule_after", lambda *_a, **_k: None),
        patch.object(watch, "is_startup_host_alive", lambda _host: True),
    ):
        watch.install_zapret_dns_server_watch(
            host, dns_feature=dns, hosts_feature=None, log_startup_metric=lambda *_a, **_k: None
        )


class ZapretDnsWatchGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = _Gate()
        for item in self.gate.patches():
            item.start()
            self.addCleanup(item.stop)

    def test_launch_that_dies_mid_check_does_not_cost_the_provider_check_a_whole_day(self) -> None:
        dying = _Dns(die=True)
        with self.assertRaises(SystemExit):
            _launch(self.gate, dying)  # SystemExit не ловится: это «процесс умер»
        self.assertEqual(dying.provider_calls, 1)
        self.assertNotIn("dns_provider_addresses", self.gate.stored, "«сделано» без окончания проверки записывать нельзя")

        self.gate.now += 10  # второй запуск через секунды: пачку запросов не повторяем
        quick = _Dns()
        _launch(self.gate, quick)
        self.assertEqual(quick.provider_calls, 0)

        self.gate.now += 1800  # а через полчаса оборванную проверку нужно довести до конца
        later = _Dns()
        _launch(self.gate, later)
        self.assertEqual(later.provider_calls, 1)
        self.assertIn("dns_provider_addresses", self.gate.stored)

    def test_finished_provider_check_waits_a_day(self) -> None:
        _launch(self.gate, _Dns())
        self.gate.now += 6 * 3600
        again = _Dns()
        _launch(self.gate, again)
        self.assertEqual(again.provider_calls, 0)
        self.gate.now += 24 * 3600
        _launch(self.gate, again)
        self.assertEqual(again.provider_calls, 1)

    def test_no_network_is_retried_in_half_an_hour(self) -> None:
        _launch(self.gate, _Dns(answered=0, silent=33))
        self.assertNotIn("dns_provider_addresses", self.gate.stored)
        self.gate.now += 1800
        retry = _Dns()
        _launch(self.gate, retry)
        self.assertEqual(retry.provider_calls, 1)

    def test_quick_zapret_dns_check_does_not_use_up_the_provider_check(self) -> None:
        # Раньше обе шли под одним разрешением: быстрая брала сутки, и долгая могла не дойти до конца.
        _launch(self.gate, _Dns(die=False, answered=0, silent=33))
        self.assertIn("zapret_dns_address", self.gate.stored)
        self.assertNotIn("dns_provider_addresses", self.gate.stored)

    def test_failure_of_one_check_does_not_cancel_the_other(self) -> None:
        dns = _Dns()
        dns.refresh_zapret_dns_server = lambda: (_ for _ in ()).throw(RuntimeError("сеть"))
        _launch(self.gate, dns)
        self.assertEqual(dns.provider_calls, 1)

    def test_changed_addresses_move_adapters(self) -> None:
        dns = _Dns()
        original = dns.refresh_provider_addresses
        dns.refresh_provider_addresses = lambda: [
            SimpleNamespace(name="Comss DNS", changed=True, answered=True),
            *original(),
        ]
        _launch(self.gate, dns)
        self.assertEqual(dns.migrations, 1)


if __name__ == "__main__":
    unittest.main()
