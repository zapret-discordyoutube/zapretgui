"""Telegram Proxy слушает 0.0.0.0, чтобы им пользовался и телефон из домашней сети."""

from __future__ import annotations

import asyncio
import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from settings.normalize import normalize_telegram_proxy
from settings.schema import default_telegram_proxy
from telegram_proxy.config import settings as tg_settings
from telegram_proxy.ui import page_runtime
from telegram_proxy.ui.page import TelegramProxyPage
from telegram_proxy.wss_proxy import TelegramWSProxy, is_local_network_peer


class DefaultAndMigrationTests(unittest.TestCase):
    def test_new_install_listens_on_whole_network(self) -> None:
        self.assertEqual(default_telegram_proxy()["host"], "0.0.0.0")
        self.assertEqual(normalize_telegram_proxy({})["host"], "0.0.0.0")
        self.assertEqual(tg_settings.default_state().host, "0.0.0.0")

    def test_old_default_is_moved_once(self) -> None:
        # Старая установка: 127.0.0.1 записан как значение по умолчанию, флага ещё нет.
        migrated = normalize_telegram_proxy({"host": "127.0.0.1"})
        self.assertEqual(migrated["host"], "0.0.0.0")
        self.assertTrue(migrated["lan_default_applied"])

    def test_later_choice_of_localhost_is_kept(self) -> None:
        # После перехода флаг записан: выбранный пользователем 127.0.0.1 больше не трогаем.
        again = normalize_telegram_proxy({"host": "127.0.0.1", "lan_default_applied": True})
        self.assertEqual(again["host"], "127.0.0.1")

    def test_custom_address_is_never_touched(self) -> None:
        self.assertEqual(normalize_telegram_proxy({"host": "192.168.1.5"})["host"], "192.168.1.5")


class LinkAddressTests(unittest.TestCase):
    def test_local_link_never_contains_wildcard(self) -> None:
        url = tg_settings.build_proxy_url("0.0.0.0", 1353, mode="socks5")
        self.assertEqual(url, "tg://socks?server=127.0.0.1&port=1353")

    def test_local_link_keeps_specific_address(self) -> None:
        url = tg_settings.build_proxy_url("192.168.1.5", 1353, mode="socks5")
        self.assertEqual(url, "tg://socks?server=192.168.1.5&port=1353")

    def test_device_link_uses_computer_address_in_network(self) -> None:
        url = tg_settings.build_device_proxy_url("0.0.0.0", 1353, mode="socks5", lan_address="192.168.1.5")
        self.assertEqual(url, "tg://socks?server=192.168.1.5&port=1353")

    def test_device_link_for_mtproxy_carries_secret(self) -> None:
        secret = "0123456789abcdef0123456789abcdef"
        url = tg_settings.build_device_proxy_url(
            "0.0.0.0", 1353, mode="mtproxy", mtproxy_secret=secret, lan_address="10.0.0.7"
        )
        self.assertTrue(url.startswith("tg://proxy?server=10.0.0.7&port=1353&secret=dd"))

    def test_no_device_link_when_proxy_is_local_only(self) -> None:
        self.assertEqual(tg_settings.device_link_host("127.0.0.1", lan_address="192.168.1.5"), "")
        self.assertEqual(tg_settings.build_device_proxy_url("127.0.0.1", 1353, lan_address="192.168.1.5"), "")

    def test_no_device_link_without_network(self) -> None:
        self.assertEqual(tg_settings.build_device_proxy_url("0.0.0.0", 1353, lan_address=""), "")

    def test_specific_address_is_given_to_device_as_is(self) -> None:
        self.assertEqual(tg_settings.device_link_host("192.168.1.9", lan_address="10.0.0.1"), "192.168.1.9")

    def test_open_to_network_flag(self) -> None:
        self.assertTrue(tg_settings.is_open_to_network("0.0.0.0"))
        self.assertTrue(tg_settings.is_open_to_network("192.168.1.5"))
        self.assertFalse(tg_settings.is_open_to_network("127.0.0.1"))

    def test_detect_lan_address_ignores_loopback_and_errors(self) -> None:
        probe = MagicMock()
        probe.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 1)
        with patch("telegram_proxy.config.settings.socket.socket", return_value=probe):
            self.assertEqual(tg_settings.detect_lan_address(), "")
        with patch("telegram_proxy.config.settings.socket.socket", side_effect=OSError("no network")):
            self.assertEqual(tg_settings.detect_lan_address(), "")
        probe.__enter__.return_value.getsockname.return_value = ("192.168.1.5", 1)
        with patch("telegram_proxy.config.settings.socket.socket", return_value=probe):
            self.assertEqual(tg_settings.detect_lan_address(), "192.168.1.5")


class ClientGuardTests(unittest.TestCase):
    def test_only_local_network_clients_are_allowed(self) -> None:
        for allowed in ("127.0.0.1", "192.168.1.20", "10.1.2.3", "172.16.5.5", "169.254.1.1", "100.64.0.9"):
            self.assertTrue(is_local_network_peer(allowed), allowed)
        for denied in ("8.8.8.8", "93.184.216.34", "?", "", "not-an-ip"):
            self.assertFalse(is_local_network_peer(denied), denied)

    def test_proxy_bound_to_wildcard_rejects_internet_client(self) -> None:
        proxy = TelegramWSProxy(host="0.0.0.0", port=0, mode="socks5")
        self.assertTrue(proxy._peer_allowed(("192.168.1.20", 5555)))
        self.assertFalse(proxy._peer_allowed(("8.8.8.8", 5555)))

    def test_proxy_bound_to_localhost_skips_the_check(self) -> None:
        proxy = TelegramWSProxy(host="127.0.0.1", port=0, mode="socks5")
        self.assertTrue(proxy._peer_allowed(("?", 0)))

    def test_rejected_client_is_closed_without_a_byte(self) -> None:
        async def scenario() -> tuple[bytes, list[str]]:
            logs: list[str] = []
            proxy = TelegramWSProxy(host="0.0.0.0", port=0, mode="socks5", on_log=logs.append)
            reader = asyncio.StreamReader()
            reader.feed_data(b"\x05\x01\x00")
            writer = MagicMock()
            writer.get_extra_info.side_effect = lambda name, default=None: (
                ("8.8.8.8", 4444) if name == "peername" else default
            )

            async def _wait_closed():
                return None

            writer.wait_closed.side_effect = _wait_closed
            await proxy._handle_socks5_client(reader, writer)
            writer.write.assert_not_called()
            return b"", logs

        _, logs = asyncio.run(scenario())
        self.assertTrue(any("outside the local network" in line for line in logs))


class UdpRelayAddressTests(unittest.TestCase):
    def test_wildcard_proxy_relay_listens_where_client_came_from(self) -> None:
        """UDP-relay для телефона обязан слушать адрес сети, а не 127.0.0.1."""

        captured: list[str] = []

        class _Loop:
            async def create_datagram_endpoint(self, factory, local_addr):
                captured.append(local_addr[0])
                transport = MagicMock()
                transport.get_extra_info.return_value = (local_addr[0], 50000)
                return transport, factory()

        async def scenario(client_facing_host: str) -> None:
            proxy = TelegramWSProxy(host="0.0.0.0", port=0, mode="socks5")
            proxy._upstream = SimpleNamespace(enabled=True, udp_enabled=True)
            proxy._upstream_runtime = MagicMock()
            proxy._upstream_runtime.current_endpoint.return_value = SimpleNamespace(
                host="203.0.113.1", port=1080, username="", password="", tls=False,
                tls_server_name="", tls_verify=True,
            )
            session = SimpleNamespace(relay_host="203.0.113.1", relay_port=40000, writer=MagicMock())
            with patch("telegram_proxy.wss_proxy.socks5.open_udp_associate_via_socks5", return_value=session) as opened, \
                    patch("telegram_proxy.wss_proxy.asyncio.get_running_loop", return_value=_Loop()):
                async def _fake(*args, **kwargs):
                    return session
                opened.side_effect = _fake
                relay = await proxy._open_udp_relay("t", client_facing_host)
            self.assertEqual(relay.local_host, client_facing_host or "127.0.0.1")

        asyncio.run(scenario("192.168.1.5"))
        asyncio.run(scenario(""))
        self.assertEqual(captured[0], "192.168.1.5")
        self.assertEqual(captured[2], "127.0.0.1")


class StatusTextTests(unittest.TestCase):
    def test_wildcard_status_says_whole_network(self) -> None:
        self.assertEqual(page_runtime.listen_address_text("0.0.0.0", 1353), "0.0.0.0:1353 (вся сеть)")
        self.assertEqual(page_runtime.listen_address_text("127.0.0.1", 1353), "127.0.0.1:1353")
        plan = page_runtime.build_status_plan(
            running=True, restarting=False, starting=False, host="0.0.0.0", port=1353
        )
        self.assertEqual(plan.status_text, "Работает на 0.0.0.0:1353 (вся сеть)")


def _feature() -> MagicMock:
    feature = MagicMock()
    manager = feature.get_proxy_manager.return_value
    manager.is_running = False
    manager.host = "0.0.0.0"
    manager.port = 1353
    feature.has_pending_settings_saves.return_value = False
    return feature


class PageLanControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _page(self, feature=None) -> TelegramProxyPage:
        host = QWidget()
        self.addCleanup(host.deleteLater)
        page = TelegramProxyPage(
            host,
            telegram_proxy_feature=feature or _feature(),
            get_zapret_running=lambda: False,
            open_advanced_settings=lambda: None,
        )
        # Блок настроек собирается при первом обращении к его виджетам; собираем сразу,
        # чтобы подмена адреса сети в тесте действовала на сборку, а не после неё.
        page._device_btn
        return page

    def test_default_state_shows_switch_on_and_phone_row(self) -> None:
        page = self._page()
        with patch("telegram_proxy.config.settings.detect_lan_address", return_value="192.168.1.5"):
            page._apply_initial_settings_state(tg_settings.default_state())
        self.assertEqual(page._host_edit.text(), "0.0.0.0")
        self.assertTrue(page._lan_toggle.isChecked())
        self.assertFalse(page._device_row.isHidden())
        self.assertTrue(page._device_btn.isEnabled())

    def test_localhost_state_hides_phone_row(self) -> None:
        page = self._page()
        page._apply_initial_settings_state(replace(tg_settings.default_state(), host="127.0.0.1"))
        self.assertFalse(page._lan_toggle.isChecked())
        self.assertTrue(page._device_row.isHidden())

    def test_switch_saves_host_and_follows_it(self) -> None:
        feature = _feature()
        page = self._page(feature)
        page._apply_initial_settings_state(tg_settings.default_state())

        page._lan_toggle.toggled.emit(False)
        self.assertEqual(page._host_edit.text(), "127.0.0.1")
        feature.request_settings_save.assert_called_with("host", host="127.0.0.1")
        self.assertTrue(page._device_row.isHidden())

        page._lan_toggle.toggled.emit(True)
        self.assertEqual(page._host_edit.text(), "0.0.0.0")
        feature.request_settings_save.assert_called_with("host", host="0.0.0.0")
        self.assertFalse(page._device_row.isHidden())

    def test_typing_an_address_moves_the_switch(self) -> None:
        page = self._page()
        page._apply_initial_settings_state(tg_settings.default_state())
        page._host_edit.setText("127.0.0.1")
        page._on_host_changed()
        self.assertFalse(page._lan_toggle.isChecked())

    def test_no_network_disables_copy_button(self) -> None:
        page = self._page()
        with patch("telegram_proxy.config.settings.detect_lan_address", return_value=""):
            page._apply_initial_settings_state(tg_settings.default_state())
        self.assertFalse(page._device_btn.isEnabled())

    def test_copy_device_link_copies_network_address(self) -> None:
        feature = _feature()
        page = self._page(feature)
        page._apply_initial_settings_state(replace(tg_settings.default_state(), mode="socks5"))
        with patch("telegram_proxy.config.settings.detect_lan_address", return_value="192.168.1.5"):
            page._on_copy_device_link()
        self.assertEqual(feature.copy_text.call_args.args[0], "tg://socks?server=192.168.1.5&port=1353")

    def test_copy_local_link_never_uses_wildcard(self) -> None:
        feature = _feature()
        page = self._page(feature)
        page._apply_initial_settings_state(replace(tg_settings.default_state(), mode="socks5"))
        page._on_copy_link()
        self.assertEqual(feature.copy_text.call_args.args[0], "tg://socks?server=127.0.0.1&port=1353")


if __name__ == "__main__":
    unittest.main()
