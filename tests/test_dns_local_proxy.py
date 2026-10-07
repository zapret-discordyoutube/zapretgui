from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dns import local_proxy
from dns import local_proxy_catalog as catalog
from dns.local_proxy_config import build_config, read_mode


class FakeSystem:
    """Windows для тестов: служба, порт 53 и ответы движка — в памяти."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.exe = root / "exe" / "dnscrypt-proxy.exe"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b"engine v1")
        self.state = ""
        self.image_path = ""
        self.port_free = True
        self.ipv6 = True
        self.config_ok = True
        self.create_ok = True
        self.start_ok = True
        self.granted = None
        # Отвечает ли запущенный движок; список — ответы по очереди запусков.
        self.answers_after_start = [True]
        self._answering = False
        self.calls: list[str] = []
        self.slept = 0.0

    def work_dir(self) -> Path:
        return self.root / "user" / "dnscrypt"

    def shipped_exe(self) -> Path:
        return self.exe

    def service_state(self, name: str) -> str:
        return self.state

    def service_image_path(self, name: str) -> str:
        return self.image_path

    def create_service(self, name, command, *, display_name, description):
        self.calls.append("create")
        if not self.create_ok:
            return False, "Отказано в доступе"
        self.state, self.image_path = "stopped", command
        return True, ""

    def start_service(self, name: str):
        self.calls.append("start")
        if not self.start_ok:
            return False, "Отказано в доступе."
        self.state = "running"
        self._answering = self.answers_after_start.pop(0) if self.answers_after_start else True
        return True, ""

    def grant_service_access(self, folder: Path) -> None:
        self.granted = folder

    def stop_service(self, name: str) -> bool:
        self.calls.append("stop")
        self.state, self._answering = "stopped", False
        return True

    def delete_service(self, name: str) -> bool:
        self.calls.append("delete")
        self.state, self.image_path = "", ""
        return True

    def ipv6_loopback_available(self) -> bool:
        return self.ipv6

    def port_53_free(self, *, ipv6: bool) -> bool:
        return self.port_free and self.state != "running"

    def check_config(self, exe: Path, config: Path):
        self.calls.append("check")
        return (True, "") if self.config_ok else (False, "bad stamp")

    def answers(self, address: str) -> bool:
        return self._answering

    def sleep(self, seconds: float) -> None:
        self.slept += seconds


class LocalProxyConfigTests(unittest.TestCase):
    def test_config_names_its_mode_and_lists_every_server_by_stamp(self) -> None:
        for mode in catalog.MODES:
            with self.subTest(mode=mode):
                text = build_config(mode, log_path=r"C:\Zapret\Dev\user\dnscrypt\dnscrypt-proxy.log", listen_ipv6=True)

                self.assertEqual(read_mode(text), mode)
                self.assertIn("listen_addresses = ['127.0.0.1:53', '[::1]:53']", text)
                self.assertIn(r"log_file = 'C:\Zapret\Dev\user\dnscrypt\dnscrypt-proxy.log'", text)
                for item in (*catalog.servers(mode), *catalog.relays(mode)):
                    self.assertIn(f"[static.'{item.name}']", text)
                    self.assertIn(f"stamp = '{item.stamp}'", text)
                # Списки из сети движок не скачивает.
                self.assertNotIn("[sources", text)

    def test_only_odoh_mode_turns_odoh_servers_on(self) -> None:
        self.assertIn("odoh_servers = true", build_config("odoh", log_path="x", listen_ipv6=False))
        self.assertIn("dnscrypt_servers = false", build_config("odoh", log_path="x", listen_ipv6=False))
        plain = build_config("dnscrypt", log_path="x", listen_ipv6=False)
        self.assertIn("odoh_servers = false", plain)
        self.assertNotIn("[anonymized_dns]", plain)
        self.assertIn("listen_addresses = ['127.0.0.1:53']\n", plain)

    def test_relay_never_belongs_to_the_owner_of_the_server(self) -> None:
        for mode in (catalog.MODE_ANONYMIZED, catalog.MODE_ODOH):
            owners = {item.name: item.operator for item in (*catalog.servers(mode), *catalog.relays(mode))}
            routes = catalog.routes(mode)
            self.assertEqual({server for server, _via in routes}, {item.name for item in catalog.servers(mode)})
            for server, via in routes:
                with self.subTest(mode=mode, server=server):
                    self.assertTrue(via)
                    self.assertNotIn(owners[server], {owners[relay] for relay in via})

    def test_anonymized_mode_leaves_out_servers_that_refuse_relays(self) -> None:
        names = {item.name for item in catalog.servers(catalog.MODE_ANONYMIZED)}

        self.assertNotIn("quad9-dnscrypt-ip4-nofilter-pri", names)
        self.assertIn("quad9-dnscrypt-ip4-nofilter-pri", {item.name for item in catalog.servers(catalog.MODE_DNSCRYPT)})

    def test_every_stamp_is_unique_and_well_formed(self) -> None:
        items = (*catalog.DNSCRYPT_SERVERS, *catalog.DNSCRYPT_RELAYS, *catalog.ODOH_SERVERS, *catalog.ODOH_RELAYS)

        self.assertEqual(len({item.name for item in items}), len(items))
        self.assertEqual(len({item.stamp for item in items}), len(items))
        for item in items:
            self.assertRegex(item.stamp, r"^sdns://[A-Za-z0-9_-]+$")

    def test_mode_is_read_only_from_a_proper_first_line(self) -> None:
        self.assertEqual(read_mode(""), "")
        self.assertEqual(read_mode("listen_addresses = []\n# zapret-mode: odoh"), "")
        self.assertEqual(read_mode("# zapret-mode: чужой\n"), "")


class LocalProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.system = FakeSystem(Path(folder.name))

    def _config(self) -> Path:
        return self.system.work_dir() / "dnscrypt-proxy.toml"

    def test_start_copies_engine_creates_service_and_waits_for_an_answer(self) -> None:
        result = local_proxy.start("dnscrypt", system=self.system)

        self.assertTrue(result.success)
        self.assertTrue(result.listen_ipv6)
        self.assertEqual(self.system.calls, ["check", "create", "start"])
        self.assertEqual((self.system.work_dir() / "dnscrypt-proxy.exe").read_bytes(), b"engine v1")
        self.assertEqual(
            self.system.image_path,
            f'"{self.system.work_dir() / "dnscrypt-proxy.exe"}" -config "{self._config()}"',
        )
        self.assertEqual(local_proxy.active_mode(system=self.system), "dnscrypt")
        self.assertEqual(self.system.granted, self.system.work_dir())

    def test_service_that_windows_refuses_to_start_reports_the_reason(self) -> None:
        self.system.start_ok = False

        result = local_proxy.start("dnscrypt", system=self.system)

        self.assertFalse(result.success)
        self.assertIn("не запустилась: Отказано в доступе.", result.message)
        self.assertEqual(self.system.state, "")
        self.assertEqual(self.system.slept, 0.0)

    def test_no_ipv6_loopback_means_no_ipv6_listener(self) -> None:
        self.system.ipv6 = False

        result = local_proxy.start("odoh", system=self.system)

        self.assertTrue(result.success)
        self.assertFalse(result.listen_ipv6)
        self.assertNotIn("[::1]", self._config().read_text(encoding="utf-8"))

    def test_busy_port_53_stops_before_anything_is_installed(self) -> None:
        self.system.port_free = False

        result = local_proxy.start("dnscrypt", system=self.system)

        self.assertFalse(result.success)
        self.assertIn("Порт 53", result.message)
        self.assertEqual(self.system.calls, [])
        self.assertFalse(self._config().exists())

    def test_engine_that_never_answers_is_removed_again(self) -> None:
        self.system.answers_after_start = [False]

        result = local_proxy.start("anonymized", system=self.system)

        self.assertFalse(result.success)
        self.assertIn("DNS на адаптерах не менялся", result.message)
        self.assertEqual(self.system.state, "")
        self.assertFalse(self._config().exists())
        self.assertGreaterEqual(self.system.slept, local_proxy.START_TIMEOUT_S)
        self.assertEqual(local_proxy.active_mode(system=self.system), "")

    def test_failed_switch_brings_the_previous_mode_back(self) -> None:
        self.assertTrue(local_proxy.start("dnscrypt", system=self.system).success)
        self.system.answers_after_start = [False, True]

        result = local_proxy.start("odoh", system=self.system)

        self.assertFalse(result.success)
        # Интернет пользователя уже шёл через движок: прежний режим снова работает.
        self.assertEqual(self.system.state, "running")
        self.assertEqual(local_proxy.active_mode(system=self.system), "dnscrypt")
        self.assertTrue(self.system.answers("127.0.0.1"))

    def test_rejected_config_and_refused_service_are_reported(self) -> None:
        self.system.config_ok = False
        result = local_proxy.start("dnscrypt", system=self.system)
        self.assertFalse(result.success)
        self.assertIn("не принял настройки", result.message)
        self.assertEqual(self.system.state, "")

        self.system.config_ok, self.system.create_ok = True, False
        result = local_proxy.start("dnscrypt", system=self.system)
        self.assertFalse(result.success)
        self.assertIn("Отказано в доступе", result.message)

    def test_missing_engine_and_unknown_mode_are_refused(self) -> None:
        self.assertFalse(local_proxy.start("doh", system=self.system).success)
        self.system.exe.unlink()

        result = local_proxy.start("dnscrypt", system=self.system)

        self.assertFalse(result.success)
        self.assertIn("dnscrypt-proxy.exe", result.message)

    def test_switching_mode_reuses_the_service(self) -> None:
        local_proxy.start("dnscrypt", system=self.system)
        self.system.calls.clear()

        self.assertTrue(local_proxy.start("anonymized", system=self.system).success)

        self.assertEqual(self.system.calls, ["stop", "check", "start"])
        self.assertEqual(local_proxy.active_mode(system=self.system), "anonymized")

    def test_stop_if_unused_keeps_engine_while_an_adapter_points_at_it(self) -> None:
        local_proxy.start("dnscrypt", system=self.system)

        self.assertFalse(local_proxy.stop_if_unused(["9.9.9.9", "::1"], system=self.system))
        self.assertEqual(self.system.state, "running")

        self.assertTrue(local_proxy.stop_if_unused(["9.9.9.9"], system=self.system))
        self.assertEqual(self.system.state, "")
        self.assertFalse(self._config().exists())
        self.assertFalse(local_proxy.stop_if_unused([], system=self.system))

    def test_repair_restarts_a_stopped_engine_and_updates_an_old_one(self) -> None:
        local_proxy.start("dnscrypt", system=self.system)
        self.assertEqual(local_proxy.repair(["127.0.0.1"], system=self.system), "")

        self.system.stop_service(local_proxy.SERVICE_NAME)
        self.assertIn("поднят заново", local_proxy.repair(["127.0.0.1"], system=self.system))
        self.assertEqual(self.system.state, "running")

        # Программа обновилась и принесла новый движок.
        self.system.exe.write_bytes(b"engine v2!")
        self.assertIn("поднят заново", local_proxy.repair(["127.0.0.1"], system=self.system))
        self.assertEqual((self.system.work_dir() / "dnscrypt-proxy.exe").read_bytes(), b"engine v2!")

    def test_repair_removes_a_service_nobody_uses_and_ignores_foreign_local_dns(self) -> None:
        local_proxy.start("dnscrypt", system=self.system)
        self.assertIn("служба убрана", local_proxy.repair(["8.8.8.8"], system=self.system))
        self.assertEqual(self.system.state, "")

        # 127.0.0.1 на адаптере без наших настроек — чужой локальный DNS: не трогаем.
        self.system.calls.clear()
        self.assertEqual(local_proxy.repair(["127.0.0.1"], system=self.system), "")
        self.assertEqual(self.system.calls, [])


class LocalProxyApplyWorkerTests(unittest.TestCase):
    """Порядок действий при выборе плитки: сначала движок, потом адаптеры."""

    def _worker(self, *, action="provider", data=None, started=None):
        from dns.page_workers import DnsApplyWorker
        from dns.state import DnsCommandResult

        self.log: list = []

        def apply_dns(adapters, ipv4, ipv6):
            self.log.append(("apply", list(ipv4), list(ipv6)))
            return DnsCommandResult(success=True, affected_count=len(adapters), total_count=len(adapters))

        def start_local_proxy(mode):
            self.log.append(("start", mode))
            return started

        return DnsApplyWorker(
            1,
            action=action,
            adapters=["{guid}"],
            name="DNSCrypt",
            data=data or {},
            ipv6_available=True,
            apply_dns=apply_dns,
            reset_to_auto=lambda adapters: apply_dns(adapters, [], []),
            load_state=lambda: "state",
            start_local_proxy=start_local_proxy,
            stop_local_proxy_if_unused=lambda: self.log.append(("stop_if_unused",)),
        )

    def test_engine_answers_first_and_only_then_adapters_get_the_local_address(self) -> None:
        from dns.state import DnsCommandResult

        data = {"ipv4": ["127.0.0.1"], "ipv6": ["::1"], "local_proxy": "dnscrypt"}
        result = self._worker(data=data, started=DnsCommandResult(success=True, listen_ipv6=True))._run_apply()

        self.assertEqual(self.log, [("start", "dnscrypt"), ("apply", ["127.0.0.1"], ["::1"])])
        self.assertEqual(result["plan"].success_count, 1)

    def test_engine_without_ipv6_listener_leaves_ipv6_dns_automatic(self) -> None:
        from dns.state import DnsCommandResult

        data = {"ipv4": ["127.0.0.1"], "ipv6": ["::1"], "local_proxy": "odoh"}
        self._worker(data=data, started=DnsCommandResult(success=True, listen_ipv6=False))._run_apply()

        self.assertEqual(self.log, [("start", "odoh"), ("apply", ["127.0.0.1"], [])])

    def test_silent_engine_leaves_adapters_untouched(self) -> None:
        from dns.state import DnsCommandResult

        data = {"ipv4": ["127.0.0.1"], "ipv6": ["::1"], "local_proxy": "dnscrypt"}
        result = self._worker(data=data, started=DnsCommandResult(success=False, message="Порт 53 занят"))._run_apply()

        self.assertEqual(self.log, [("start", "dnscrypt")])
        self.assertFalse(result["plan"].valid)
        self.assertEqual(result["plan"].log_message, "Порт 53 занят")

    def test_ordinary_dns_and_automatic_dns_release_the_engine(self) -> None:
        self._worker(data={"ipv4": ["9.9.9.9"], "ipv6": []})._run_apply()
        self.assertEqual(self.log, [("apply", ["9.9.9.9"], []), ("stop_if_unused",)])

        self._worker(action="auto")._run_apply()
        self.assertEqual(self.log, [("apply", [], []), ("stop_if_unused",)])


if __name__ == "__main__":
    unittest.main()
