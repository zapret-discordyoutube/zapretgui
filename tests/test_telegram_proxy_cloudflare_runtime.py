from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch


class TelegramProxyCloudflareRuntimeTests(unittest.TestCase):
    def test_upstream_mode_decides_main_or_fallback_route_for_any_server(self) -> None:
        from telegram_proxy.proxy.routing import UpstreamProxyConfig, should_route_upstream

        preset_fallback = UpstreamProxyConfig(
            enabled=True, host="150.241.74.19", port=443, tls=True, mode="fallback", preset_id="ee"
        )
        preset_always = UpstreamProxyConfig(
            enabled=True, host="150.241.74.19", port=443, tls=True, mode="always", preset_id="ee"
        )
        manual_fallback = UpstreamProxyConfig(enabled=True, host="127.0.0.1", port=1080, mode="fallback")
        disabled = UpstreamProxyConfig(enabled=False, host="127.0.0.1", port=1080, mode="always")

        # По умолчанию сервер страны — запасной путь после WSS.
        self.assertFalse(should_route_upstream(preset_fallback, mode="always"))
        self.assertTrue(should_route_upstream(preset_fallback, mode="fallback"))
        # «Весь TCP через SOCKS5» работает и для сервера страны.
        self.assertTrue(should_route_upstream(preset_always, mode="always"))
        self.assertTrue(should_route_upstream(manual_fallback, mode="fallback"))
        self.assertFalse(should_route_upstream(disabled, mode="always"))

    def test_cloudflare_settings_are_normalized_in_settings_schema_shape(self) -> None:
        from settings.normalize import normalize_telegram_proxy
        from settings.schema import default_telegram_proxy

        defaults = default_telegram_proxy()

        self.assertIn("cloudflare_enabled", defaults)
        self.assertIn("cloudflare_domains", defaults)
        self.assertIn("cloudflare_worker_enabled", defaults)
        self.assertIn("cloudflare_worker_domains", defaults)

        normalized = normalize_telegram_proxy(
            {
                "cloudflare_enabled": "yes",
                "cloudflare_domains": [" Example.COM ", "example.com", "", "bad domain"],
                "cloudflare_worker_enabled": 1,
                "cloudflare_worker_domains": "demo.workers.dev, DEMO.workers.dev; worker.example.dev",
            }
        )

        self.assertTrue(normalized["cloudflare_enabled"])
        self.assertEqual(normalized["cloudflare_domains"], ["example.com"])
        self.assertTrue(normalized["cloudflare_worker_enabled"])
        self.assertEqual(
            normalized["cloudflare_worker_domains"],
            ["demo.workers.dev", "worker.example.dev"],
        )

    def test_cloudflare_config_is_built_from_settings_store(self) -> None:
        import telegram_proxy.config.settings as telegram_proxy_settings

        with (
            patch("settings.store.get_tg_proxy_cloudflare_enabled", return_value=True),
            patch("settings.store.get_tg_proxy_cloudflare_domains", return_value=[" Example.COM ", "example.com"]),
            patch("settings.store.get_tg_proxy_cloudflare_worker_enabled", return_value=True),
            patch("settings.store.get_tg_proxy_cloudflare_worker_domains", return_value=["demo.workers.dev"]),
        ):
            config = telegram_proxy_settings.build_cloudflare_config()

        self.assertTrue(config.enabled)
        self.assertEqual(config.domains, ("example.com",))
        self.assertTrue(config.worker_enabled)
        self.assertEqual(config.worker_domains, ("demo.workers.dev",))

    def test_cloudflare_enabled_without_custom_domains_uses_builtin_auto_pool(self) -> None:
        import telegram_proxy.config.settings as telegram_proxy_settings
        from telegram_proxy.proxy.cloudflare import AUTO_CLOUDFLARE_DOMAINS

        with (
            patch("settings.store.get_tg_proxy_cloudflare_enabled", return_value=True),
            patch("settings.store.get_tg_proxy_cloudflare_domains", return_value=[]),
            patch("settings.store.get_tg_proxy_cloudflare_worker_enabled", return_value=False),
            patch("settings.store.get_tg_proxy_cloudflare_worker_domains", return_value=[]),
        ):
            config = telegram_proxy_settings.build_cloudflare_config()

        self.assertTrue(config.enabled)
        self.assertEqual(config.domains, AUTO_CLOUDFLARE_DOMAINS)

    def test_cloudflare_settings_are_saved_through_runtime_command(self) -> None:
        import telegram_proxy.runtime.commands as commands

        with (
            patch("telegram_proxy.config.settings.set_cloudflare_enabled") as set_enabled,
            patch("telegram_proxy.config.settings.set_cloudflare_domains") as set_domains,
            patch("telegram_proxy.config.settings.set_cloudflare_worker_enabled") as set_worker_enabled,
            patch("telegram_proxy.config.settings.set_cloudflare_worker_domains") as set_worker_domains,
        ):
            commands.save_settings_action("cloudflare_enabled", enabled=True)
            commands.save_settings_action("cloudflare_domains", value="example.com, demo.example.com")
            commands.save_settings_action("cloudflare_worker_enabled", enabled=True)
            commands.save_settings_action("cloudflare_worker_domains", value="worker.example.dev")

        set_enabled.assert_called_once_with(True)
        set_domains.assert_called_once_with("example.com, demo.example.com")
        set_worker_enabled.assert_called_once_with(True)
        set_worker_domains.assert_called_once_with("worker.example.dev")

    def test_advanced_performance_settings_are_saved_through_runtime_command(self) -> None:
        import telegram_proxy.runtime.commands as commands

        with (
            patch("telegram_proxy.config.settings.set_pool_size") as set_pool_size,
            patch("telegram_proxy.config.settings.set_buffer_kb") as set_buffer_kb,
        ):
            commands.save_settings_action("pool_size", value=8)
            commands.save_settings_action("buffer_kb", value=512)

        set_pool_size.assert_called_once_with(8)
        set_buffer_kb.assert_called_once_with(512)

    def test_upstream_udp_setting_is_saved_and_passed_to_upstream_config(self) -> None:
        import telegram_proxy.runtime.commands as commands
        import telegram_proxy.config.settings as telegram_proxy_settings

        with patch("telegram_proxy.config.settings.set_upstream_udp_enabled") as set_udp_enabled:
            commands.save_settings_action("upstream_udp_enabled", enabled=True)

        set_udp_enabled.assert_called_once_with(True)

        with (
            patch("settings.store.get_tg_proxy_upstream_enabled", return_value=True),
            patch("settings.store.get_tg_proxy_upstream_host", return_value="127.0.0.1"),
            patch("settings.store.get_tg_proxy_upstream_port", return_value=1080),
            patch("settings.store.get_tg_proxy_upstream_user", return_value=""),
            patch("settings.store.get_tg_proxy_upstream_pass", return_value=""),
            patch("settings.store.get_tg_proxy_upstream_preset_id", return_value=""),
            patch("settings.store.get_tg_proxy_upstream_mode", return_value="always"),
            patch("settings.store.get_tg_proxy_upstream_udp_enabled", return_value=True),
        ):
            config = telegram_proxy_settings.build_upstream_config()

        self.assertTrue(config.enabled)
        self.assertEqual(config.mode, "always")
        self.assertTrue(config.udp_enabled)

    def test_socks5_handler_accepts_udp_associate_when_udp_relay_is_enabled(self) -> None:
        from telegram_proxy.proxy import socks5
        from telegram_proxy.proxy.routing import UpstreamProxyConfig
        from telegram_proxy.wss_proxy import TelegramWSProxy

        class _Reader:
            async def read(self):
                return b""

        class _Writer:
            def get_extra_info(self, name, default=None):
                if name == "peername":
                    return ("127.0.0.1", 50000)
                return default

        class _Relay:
            local_host = "127.0.0.1"
            local_port = 45678

            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        relay = _Relay()
        callback_bound: list[tuple[str, int]] = []

        async def fake_handshake(_reader, _writer, *, allow_udp_associate, on_udp_associate):
            self.assertTrue(allow_udp_associate)
            callback_bound.append(await on_udp_associate())
            return socks5.UdpAssociateRequest("0.0.0.0", 9)

        proxy = TelegramWSProxy(
            port=0,
            upstream_config=UpstreamProxyConfig(
                enabled=True,
                host="127.0.0.1",
                port=1080,
                udp_enabled=True,
            ),
        )

        async def run_check() -> None:
            with (
                patch("telegram_proxy.wss_proxy.socks5.handshake", side_effect=fake_handshake),
                patch.object(proxy, "_open_udp_relay", return_value=relay) as open_udp_relay,
            ):
                await proxy._handle_socks5_client(_Reader(), _Writer())
            open_udp_relay.assert_called_once()

        asyncio.run(run_check())

        self.assertEqual(callback_bound, [("127.0.0.1", 45678)])
        self.assertTrue(relay.closed)

    def test_cloudflare_helpers_build_worker_path_and_builtin_pool(self) -> None:
        from telegram_proxy.proxy.cloudflare import AUTO_CLOUDFLARE_DOMAINS, build_worker_path
        from telegram_proxy.proxy.route_catalog import CDN_FRONTS

        self.assertEqual(build_worker_path("149.154.167.91", 4), "/apiws?dst=149.154.167.91&dc=4")
        self.assertEqual(AUTO_CLOUDFLARE_DOMAINS, tuple(front.domain for front in CDN_FRONTS))
        self.assertEqual(len(AUTO_CLOUDFLARE_DOMAINS), 20)

    def test_cloudflare_guides_include_dns_records_and_worker_code(self) -> None:
        from telegram_proxy.proxy.cloudflare import build_cfproxy_dns_records_text, build_cfworker_code

        dns_text = build_cfproxy_dns_records_text()
        worker_code = build_cfworker_code()

        self.assertIn("kws1", dns_text)
        self.assertIn("149.154.175.50", dns_text)
        self.assertIn("kws203", dns_text)
        self.assertIn("91.105.192.100", dns_text)
        self.assertIn('url.pathname !== "/apiws"', worker_code)
        self.assertIn('request.headers.get("Upgrade")', worker_code)
        self.assertIn("function toBytes(data)", worker_code)
        self.assertIn("connect({ hostname: dst, port: 443 })", worker_code)
        self.assertIn("await tcpWriter.write(await toBytes(event.data))", worker_code)
        self.assertIn("tcpReader.releaseLock()", worker_code)
        self.assertIn("socket.close()", worker_code)

    def test_cloudflare_connectivity_check_builds_domain_and_worker_probes(self) -> None:
        from telegram_proxy.proxy.cloudflare import check_cloudflare_connectivity

        class _Ws:
            async def close(self):
                return None

        calls = []

        async def fake_connect(target, *, timeout):
            calls.append((target.connect_host, target.sni, target.path, timeout))
            return _Ws()

        domain_result = asyncio.run(
            check_cloudflare_connectivity(
                "domain",
                ["Example.COM"],
                dcs=(4,),
                timeout=1.5,
                connect=fake_connect,
            )
        )
        worker_result = asyncio.run(
            check_cloudflare_connectivity(
                "worker",
                ["worker.example.dev"],
                dcs=(4,),
                timeout=1.5,
                connect=fake_connect,
            )
        )

        self.assertTrue(domain_result.ok)
        self.assertTrue(worker_result.ok)
        self.assertEqual(
            calls,
            [
                ("kws4.example.com", "kws4.example.com", "/apiws", 1.5),
                ("worker.example.dev", "worker.example.dev", "/apiws?dst=149.154.167.91&dc=4", 1.5),
            ],
        )

    def test_http_transport_records_failure_for_status_without_upstream(self) -> None:
        from telegram_proxy.wss_proxy import TelegramWSProxy

        class _Reader:
            async def readexactly(self, size):
                init = b"GET /api HTTP/1.1\r\nHost: telegram\r\n\r\n"
                return init[:size].ljust(size, b"x")

        class _Writer:
            def get_extra_info(self, name, default=None):
                if name == "peername":
                    return ("127.0.0.1", 34567)
                return default

            def close(self):
                return None

            async def wait_closed(self):
                return None

        async def fail_direct_tcp(*_args, **_kwargs):
            raise TimeoutError()

        logs: list[str] = []
        proxy = TelegramWSProxy(on_log=logs.append)

        with (
            patch("telegram_proxy.wss_proxy.socks5.handshake", return_value=("149.154.175.50", 80)),
            patch("telegram_proxy.wss_proxy.asyncio.open_connection", side_effect=fail_direct_tcp),
        ):
            asyncio.run(proxy._handle_socks5_client(_Reader(), _Writer()))

        self.assertEqual(len(proxy.stats.route_events), 1)
        event = proxy.stats.route_events[0]
        self.assertEqual(event.dc, 0)
        self.assertEqual(event.route, "HTTP direct TCP")
        self.assertIn("ошибка", event.status)
        self.assertIn("TimeoutError", event.reason)

    def test_upstream_connect_failure_is_written_to_detailed_route_log(self) -> None:
        from telegram_proxy.proxy.routing import UpstreamProxyConfig
        from telegram_proxy.wss_proxy import TelegramWSProxy

        logs: list[str] = []
        proxy = TelegramWSProxy(
            on_log=logs.append,
            upstream_config=UpstreamProxyConfig(
                enabled=True,
                host="127.0.0.1",
                port=1080,
                username="secret-user",
                password="secret-pass",
                tls=True,
                mode="fallback",
            ),
        )

        async def fail_upstream(*_args, **_kwargs):
            raise TimeoutError()

        async def run_check():
            return await proxy.open_upstream(
                target_host="149.154.175.50",
                target_port=443,
                label="test",
                dc=1,
                is_media=False,
            )

        with patch("telegram_proxy.proxy.socks5.connect_via_socks5", side_effect=fail_upstream):
            opened = asyncio.run(run_check())

        self.assertIsNone(opened)
        joined = "\n".join(logs)
        self.assertIn("route=upstream SOCKS5", joined)
        self.assertIn("dc=1", joined)
        self.assertIn("target=149.154.175.50:443 via 127.0.0.1:1080", joined)
        self.assertIn("result=error", joined)
        self.assertIn("TimeoutError", joined)
        self.assertIn("next=следующее соединение использует общий активный сервер", joined)
        self.assertNotIn("secret-user", joined)
        self.assertNotIn("secret-pass", joined)

    def test_telegram_ipv6_media_target_becomes_same_dc_ipv4(self) -> None:
        from telegram_proxy.proxy.obfs import build_server_header
        from telegram_proxy.wss_proxy import TelegramWSProxy

        header, _cipher = build_server_header(-1)

        class _Reader:
            async def readexactly(self, size):
                return header[:size]

        class _Writer:
            def get_extra_info(self, name, default=None):
                return ("127.0.0.1", 40000) if name == "peername" else default

            def close(self):
                return None

            async def wait_closed(self):
                return None

        seen: list[dict] = []

        class _Session:
            def __init__(self, _host, **kwargs):
                seen.append(kwargs)

            async def run(self):
                return None

        proxy = TelegramWSProxy()
        with (
            patch("telegram_proxy.wss_proxy.socks5.handshake", return_value=("2001:b28:f23d:f001:0:0:0:7", 443)),
            patch("telegram_proxy.wss_proxy.TelegramSession", _Session),
        ):
            asyncio.run(proxy._handle_socks5_client(_Reader(), _Writer()))

        self.assertEqual((seen[0]["dc"], seen[0]["is_media"]), (1, True))
        self.assertEqual((seen[0]["target_host"], seen[0]["target_port"]), ("149.154.175.52", 443))

    def test_proxy_server_is_bound_before_explicit_single_start_serving(self) -> None:
        from telegram_proxy.wss_proxy import TelegramWSProxy

        class _Server:
            def __init__(self):
                self.start_serving_calls = 0

            async def start_serving(self):
                self.start_serving_calls += 1

            def close(self):
                return None

            async def wait_closed(self):
                return None

        server = _Server()

        async def fake_start_server(*_args, **_kwargs):
            return server

        async def run_proxy_once():
            proxy = TelegramWSProxy(port=0)
            await proxy.start()
            await proxy.stop()

        with (
            patch("telegram_proxy.wss_proxy.asyncio.start_server", side_effect=fake_start_server) as start_server,
        ):
            asyncio.run(run_proxy_once())

        self.assertEqual(server.start_serving_calls, 1)
        self.assertEqual(start_server.await_args.kwargs.get("start_serving"), False)

    def test_upstream_socks5_client_sends_ipv6_address_type(self) -> None:
        from telegram_proxy.proxy import socks5

        class _Reader:
            def __init__(self):
                self._data = bytearray(b"\x05\x00" + b"\x05\x00\x00\x04" + b"\x00" * 18)

            async def readexactly(self, size):
                chunk = bytes(self._data[:size])
                del self._data[:size]
                return chunk

        class _Writer:
            def __init__(self):
                self.writes: list[bytes] = []

            def write(self, data):
                self.writes.append(bytes(data))

            async def drain(self):
                return None

            def close(self):
                return None

        async def fake_open_connection(*_args, **_kwargs):
            writer = _Writer()
            opened.append(writer)
            return _Reader(), writer

        opened: list[_Writer] = []
        with patch("telegram_proxy.proxy.socks5.asyncio.open_connection", side_effect=fake_open_connection):
            asyncio.run(
                socks5.connect_via_socks5(
                    "127.0.0.1",
                    1080,
                    "2001:b28:f23d:f001:0:0:0:7",
                    443,
                )
            )

        request = opened[0].writes[1]
        self.assertEqual(request[:4], b"\x05\x01\x00\x04")
        self.assertEqual(len(request), 4 + 16 + 2)

if __name__ == "__main__":
    unittest.main()
