from __future__ import annotations

import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from dns import doh_switch, runtime, winapi
from dns.adapters import DnsAdapter
from dns.dns_providers import DNS_PROVIDERS, zapret_dns_provider
from dns.state import DnsCommandResult, DnsState
from settings import schema
from settings.normalize import normalize_dns

ETH = "{00000000-0000-0000-0000-000000000009}"
WIFI = "{00000000-0000-0000-0000-000000000012}"

CLOUDFLARE = DNS_PROVIDERS["Популярные"]["Cloudflare"]
DNSCRYPT = DNS_PROVIDERS["Шифрованные"]["DNSCrypt"]
ZAPRET = zapret_dns_provider()


class DohSwitchRulesTests(unittest.TestCase):
    def test_ordinary_doh_server_goes_through_the_engine_only_on_windows_10_with_the_switch_on(self) -> None:
        self.assertEqual(doh_switch.proxy_mode(CLOUDFLARE, doh_supported=False, doh_enabled=True), "doh")
        self.assertEqual(doh_switch.proxy_mode(CLOUDFLARE, doh_supported=False, doh_enabled=False), "")
        self.assertEqual(doh_switch.proxy_mode(CLOUDFLARE, doh_supported=True, doh_enabled=True), "")
        self.assertEqual(doh_switch.proxy_mode(CLOUDFLARE, doh_supported=True, doh_enabled=False), "")

    def test_encrypted_group_does_not_depend_on_the_switch(self) -> None:
        for enabled in (True, False):
            for supported in (True, False):
                with self.subTest(enabled=enabled, supported=supported):
                    self.assertEqual(
                        doh_switch.proxy_mode(DNSCRYPT, doh_supported=supported, doh_enabled=enabled), "dnscrypt"
                    )
                    self.assertEqual(
                        doh_switch.refusal(DNSCRYPT, doh_supported=supported, doh_enabled=enabled), ""
                    )

    def test_doh_badge_is_shown_only_when_the_tile_will_really_encrypt(self) -> None:
        self.assertTrue(doh_switch.tile_encrypts(CLOUDFLARE, doh_supported=False, doh_enabled=True))
        self.assertTrue(doh_switch.tile_encrypts(CLOUDFLARE, doh_supported=True, doh_enabled=True))
        self.assertFalse(doh_switch.tile_encrypts(CLOUDFLARE, doh_supported=False, doh_enabled=False))
        self.assertFalse(doh_switch.tile_encrypts(CLOUDFLARE, doh_supported=True, doh_enabled=False))

    def test_server_with_encryption_only_is_refused_when_the_switch_is_off(self) -> None:
        self.assertEqual(
            doh_switch.refusal(ZAPRET, doh_supported=True, doh_enabled=False), doh_switch.REFUSE_SWITCH_OFF
        )
        self.assertEqual(doh_switch.refusal(ZAPRET, doh_supported=True, doh_enabled=True), "")
        # Windows 10 с включённым переключателем ставит его через движок.
        self.assertEqual(doh_switch.refusal(ZAPRET, doh_supported=False, doh_enabled=True), "")
        # Обычный сервер можно поставить и без шифрования.
        self.assertEqual(doh_switch.refusal(CLOUDFLARE, doh_supported=True, doh_enabled=False), "")

    def test_switch_is_on_when_settings_cannot_be_read(self) -> None:
        with patch("settings.store.get_dns_doh_enabled", return_value=False):
            self.assertFalse(doh_switch.is_enabled())
        with patch("settings.store.get_dns_doh_enabled", side_effect=RuntimeError("база занята")), patch.object(
            doh_switch, "log"
        ):
            self.assertTrue(doh_switch.is_enabled())


class DohSwitchSettingsTests(unittest.TestCase):
    def test_switch_is_on_by_default_and_remembers_off(self) -> None:
        self.assertIs(schema.default_dns()["doh_enabled"], True)
        self.assertIs(normalize_dns({})["doh_enabled"], True)
        self.assertIs(normalize_dns({"doh_enabled": False})["doh_enabled"], False)
        self.assertIs(normalize_dns({"doh_enabled": True})["doh_enabled"], True)


class DohSwitchWindowsWriteTests(unittest.TestCase):
    def _templates(self, *, supported: bool, enabled: bool):
        with patch.object(winapi, "is_doh_supported", return_value=supported), patch.object(
            doh_switch, "is_enabled", return_value=enabled
        ), patch.object(runtime, "load_custom_servers", return_value=[]):
            return runtime.write_doh_templates()

    def test_windows_11_gets_templates_only_with_the_switch_on(self) -> None:
        on = self._templates(supported=True, enabled=True)
        self.assertTrue(on.get("1.1.1.1"))
        # Пустой словарь, а не None: так Windows при записи снимает прежние ссылки DoH с адаптера.
        self.assertEqual(self._templates(supported=True, enabled=False), {})

    def test_windows_10_never_gets_templates(self) -> None:
        self.assertIsNone(self._templates(supported=False, enabled=True))
        self.assertIsNone(self._templates(supported=False, enabled=False))

    def test_switch_off_writes_plain_addresses_with_empty_doh_list(self) -> None:
        writes = []
        known = DnsState(adapters=(DnsAdapter(ETH, "Ethernet", "", "ethernet", True, True),))
        with patch.object(winapi, "is_doh_supported", return_value=True), patch.object(
            winapi, "write_dns", side_effect=lambda guid, servers, *, ipv6, doh_templates=None: writes.append(doh_templates)
        ), patch.object(winapi, "flush_resolver_cache", return_value=True), patch.object(
            doh_switch, "is_enabled", return_value=False
        ), patch.object(runtime, "load_state", return_value=known), patch.object(
            runtime, "load_custom_servers", return_value=[]
        ), patch.object(runtime, "check_servers", lambda ipv4, ipv6, templates=None: SimpleNamespace(blocked="", doh_closed=False)):
            result = runtime.apply_dns([ETH], ["1.1.1.1"], [])

        self.assertTrue(result.success)
        self.assertEqual(writes, [{}, {}])


class DohSwitchWorkerTests(unittest.TestCase):
    def _worker(self, *, action, adapters, doh_enabled, data=None, proxy_mode=""):
        from dns.page_workers import DnsApplyWorker

        self.log: list = []

        def apply_dns(adapter_ids, ipv4, ipv6):
            self.log.append(("apply", list(ipv4)))
            return DnsCommandResult(success=True, affected_count=len(adapter_ids), total_count=len(adapter_ids))

        return DnsApplyWorker(
            1,
            action=action,
            adapters=adapters,
            name="Cloudflare",
            data=data or {},
            ipv6_available=False,
            proxy_mode=proxy_mode,
            doh_enabled=doh_enabled,
            set_doh_enabled=lambda value: self.log.append(("set_doh", value)),
            apply_dns=apply_dns,
            reset_to_auto=lambda ids: apply_dns(ids, [], []),
            load_state=lambda: "state",
            start_local_proxy=lambda mode, **kwargs: self.log.append(("start", mode)),
            stop_local_proxy_if_unused=lambda: self.log.append(("stop_if_unused",)),
        )

    def test_choice_is_saved_before_the_server_is_written_to_adapters(self) -> None:
        worker = self._worker(action="provider", adapters=[ETH], doh_enabled=False, data=dict(CLOUDFLARE))

        result = worker._run_apply()

        # apply_dns читает переключатель при записи адаптерам, поэтому он записывается первым.
        self.assertEqual(self.log[0], ("set_doh", False))
        self.assertEqual(self.log[1][0], "apply")
        self.assertIn(("stop_if_unused",), self.log)
        self.assertEqual(result["plan"].success_count, 1)

    def test_settings_only_action_saves_the_choice_and_touches_no_adapter(self) -> None:
        worker = self._worker(action="doh_setting", adapters=[], doh_enabled=True)

        result = worker._run_apply()

        self.assertEqual(self.log, [("set_doh", True)])
        self.assertEqual(result, {"plan": None, "state": "state"})

    def test_apply_without_the_flag_does_not_touch_the_switch(self) -> None:
        worker = self._worker(action="provider", adapters=[ETH], doh_enabled=None, data=dict(CLOUDFLARE))

        worker._run_apply()

        self.assertNotIn(("set_doh", True), self.log)
        self.assertNotIn(("set_doh", False), self.log)


def _adapter(ipv4, ipv6=()):
    return DnsAdapter(ETH, "Ethernet", "", "ethernet", connected=True, internet=True, static_ipv4=tuple(ipv4), static_ipv6=tuple(ipv6))


class DohSwitchPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        patches = [
            patch("dns.ui.page.InfoBar"),
            patch("dns.ui.now_panel.are_live_animations_enabled", return_value=True),
            patch("dns.ui.provider_grid.are_live_animations_enabled", return_value=True),
        ]
        for item in patches:
            started = item.start()
            self.addCleanup(item.stop)
            if item.attribute == "InfoBar":
                self.info_bar = started

    def _page(self, state):
        from dns.ui.page import NetworkPage

        feature = SimpleNamespace(
            consume_warmed_page_data=Mock(return_value=state),
            create_page_load_worker=Mock(),
            create_dns_apply_worker=Mock(),
            create_dns_flush_cache_worker=Mock(),
            create_dns_latency_worker=Mock(),
            create_dns_answering_worker=Mock(),
            create_isp_dns_warning_worker=Mock(),
            create_custom_server_worker=Mock(),
        )
        page = NetworkPage(
            deps=SimpleNamespace(dns_feature=feature, open_custom_server=Mock(), create_open_url_worker=Mock())
        )
        self.addCleanup(page.deleteLater)
        page.resize(1200, 900)
        for lane in page._lanes():
            lane.request = Mock()
        page.on_page_activated()
        return page

    def _cloudflare_state(self, **kwargs):
        return DnsState(adapters=(_adapter(("1.1.1.1", "1.0.0.1")),), doh_supported=True, **kwargs)

    def test_switch_shows_the_saved_choice(self) -> None:
        self.assertTrue(self._page(self._cloudflare_state()).doh_toggle.isChecked())
        self.assertFalse(self._page(self._cloudflare_state(doh_enabled=False)).doh_toggle.isChecked())

    def test_turning_off_sets_the_current_server_again_by_its_plain_address(self) -> None:
        page = self._page(self._cloudflare_state())

        page.doh_toggle.setChecked(False)

        payload = page._apply_lane.request.call_args.args[0]
        self.assertEqual((payload["action"], payload["name"]), ("provider", "Cloudflare"))
        self.assertIs(payload["doh_enabled"], False)
        self.assertEqual(payload["proxy_mode"], "")

    def test_turning_off_on_windows_10_leaves_the_engine(self) -> None:
        state = DnsState(
            adapters=(_adapter(("127.0.0.1",), ("::1",)),),
            doh_supported=False,
            local_proxy_mode="doh",
            local_proxy_server="Cloudflare",
        )
        page = self._page(state)

        page.doh_toggle.setChecked(False)

        payload = page._apply_lane.request.call_args.args[0]
        self.assertEqual((payload["name"], payload["proxy_mode"], payload["doh_enabled"]), ("Cloudflare", "", False))

        page.doh_toggle.setChecked(True)
        payload = page._apply_lane.request.call_args.args[0]
        self.assertEqual((payload["proxy_mode"], payload["doh_enabled"]), ("doh", True))

    def test_without_a_doh_server_only_the_choice_is_saved(self) -> None:
        page = self._page(DnsState(adapters=(_adapter(()),), doh_supported=True))

        page.doh_toggle.setChecked(False)

        self.assertEqual(
            page._apply_lane.request.call_args.args[0], {"action": "doh_setting", "adapters": [], "doh_enabled": False}
        )

    def test_server_with_encryption_only_keeps_the_switch_on(self) -> None:
        state = DnsState(adapters=(_adapter(tuple(ZAPRET["ipv4"])),), doh_supported=True)
        page = self._page(state)

        page.doh_toggle.setChecked(False)

        self.assertTrue(page.doh_toggle.isChecked())
        self.info_bar.warning.assert_called_once()
        page._apply_lane.request.assert_not_called()

    def test_tile_with_encryption_only_is_refused_while_the_switch_is_off(self) -> None:
        page = self._page(self._cloudflare_state(doh_enabled=False))

        page.grid.activated.emit("Zapret DNS")

        self.info_bar.warning.assert_called_once()
        page._apply_lane.request.assert_not_called()
        # Обычный сервер при этом ставится обычным адресом.
        page.grid.activated.emit("Quad9")
        payload = page._apply_lane.request.call_args.args[0]
        self.assertEqual((payload["name"], payload["proxy_mode"]), ("Quad9", ""))

    def test_doh_badge_follows_the_switch(self) -> None:
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                page = self._page(self._cloudflare_state(doh_enabled=enabled))
                tiles = {tile.key: tile for tile in page._tiles(page._current_plan()) if tile.kind == "provider"}
                self.assertIs(tiles["Quad9"].has_doh, enabled)


if __name__ == "__main__":
    unittest.main()
