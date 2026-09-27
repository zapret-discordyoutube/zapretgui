from __future__ import annotations

import importlib
import inspect
from pathlib import Path
import time
import unittest

import telegram_proxy.manager as telegram_manager
import telegram_proxy.runtime.commands as telegram_commands
import telegram_proxy.runtime.plans as telegram_plans


class TelegramProxyActionsArchitectureTests(unittest.TestCase):
    def test_spare_pool_skips_websockets_that_are_closing(self) -> None:
        import asyncio
        from types import SimpleNamespace

        from telegram_proxy.proxy.pool import WsSparePool
        from telegram_proxy.proxy.stats import ProxyStats

        class _WebSocket:
            is_closing = True
            opened_at = time.monotonic()

            def has_unread_data(self) -> bool:
                return False

            async def close(self) -> None:
                return None

        async def run_check() -> None:
            stats = ProxyStats()
            pool = WsSparePool(stats)
            pool._spares["relay:kws2"] = [_WebSocket()]
            self.assertIsNone(pool.take(SimpleNamespace(pool_key="relay:kws2")))
            self.assertEqual((stats.pool_hits, stats.pool_misses), (0, 1))
            await pool.close_all()

        asyncio.run(run_check())

    def test_external_open_actions_live_in_commands_not_actions(self) -> None:
        commands_source = inspect.getsource(telegram_commands)
        plans_source = inspect.getsource(telegram_plans)

        self.assertNotIn("subprocess", plans_source)
        self.assertNotIn("webbrowser.open", plans_source)
        self.assertNotIn("open_log_file", plans_source)
        self.assertNotIn("open_external_link", plans_source)

        self.assertIn("def open_log_file", commands_source)
        self.assertIn("def open_external_link", commands_source)
        self.assertIn("subprocess.Popen", commands_source)
        self.assertIn("webbrowser.open", commands_source)

    def test_upstream_config_builder_has_single_settings_owner(self) -> None:
        manager_source = inspect.getsource(telegram_manager.build_upstream_proxy_config_from_settings)
        commands_source = inspect.getsource(telegram_commands.build_upstream_config)

        self.assertIn("telegram_proxy.config.settings", manager_source)
        self.assertIn("build_upstream_config", manager_source)
        self.assertNotIn("get_tg_proxy_upstream_enabled", manager_source)
        self.assertNotIn("get_tg_proxy_upstream_host", manager_source)
        self.assertIn("telegram_proxy.config.settings", commands_source)

    def test_wss_proxy_is_split_into_focused_modules(self) -> None:
        routing = importlib.import_module("telegram_proxy.proxy.routing")
        stats = importlib.import_module("telegram_proxy.proxy.stats")
        cloudflare = importlib.import_module("telegram_proxy.proxy.cloudflare")
        wss_proxy = importlib.import_module("telegram_proxy.wss_proxy")

        self.assertIs(wss_proxy.UpstreamProxyConfig, routing.UpstreamProxyConfig)
        self.assertIs(wss_proxy.check_relay_reachable, routing.check_relay_reachable)
        self.assertIs(wss_proxy.ProxyStats, stats.ProxyStats)
        self.assertIs(wss_proxy.CloudflareFallbackConfig, cloudflare.CloudflareFallbackConfig)

        wss_source = inspect.getsource(wss_proxy)
        # Маршруты, заголовок obfuscated2 и пересылка живут в proxy/*,
        # а wss_proxy только принимает соединения.
        self.assertIn("TelegramSession(", wss_source)
        self.assertNotIn("def _tunnel", wss_source)
        self.assertNotIn("_cloudflare_fallback", wss_source)
        self.assertNotIn("class _MsgSplitter", wss_source)
        self.assertNotIn("transparent_port_to_dc", wss_source)
        self.assertNotIn("TRANSPARENT_PORT_BASE", wss_source)

    def test_proxy_network_helpers_live_under_proxy_package(self) -> None:
        root = Path(__file__).resolve().parents[1]
        telegram_proxy_root = root / "src" / "telegram_proxy"
        proxy_root = telegram_proxy_root / "proxy"

        for name in (
            "__init__.py",
            "obfs.py",
            "ws.py",
            "health.py",
            "routes.py",
            "session.py",
            "pool.py",
            "routing.py",
            "stats.py",
            "dc_map.py",
            "socks5.py",
        ):
            self.assertTrue((proxy_root / name).exists(), name)

        for name in ("transport.py", "relay.py", "mtproto.py"):
            self.assertFalse((proxy_root / name).exists(), name)
        for name in ("raw_websocket.py", "relay.py", "routing.py", "stats.py", "dc_map.py", "socks5.py", "service.py", "__main__.py"):
            self.assertFalse((telegram_proxy_root / name).exists(), name)

    def test_removed_transparent_mode_has_no_proxy_helpers(self) -> None:
        dc_map = importlib.import_module("telegram_proxy.proxy.dc_map")

        self.assertFalse(hasattr(dc_map, "TRANSPARENT_PORT_BASE"))
        self.assertFalse(hasattr(dc_map, "dc_to_transparent_port"))
        self.assertFalse(hasattr(dc_map, "transparent_port_to_dc"))

    def test_config_and_diagnostics_live_in_focused_packages(self) -> None:
        root = Path(__file__).resolve().parents[1]
        telegram_proxy_root = root / "src" / "telegram_proxy"
        config_root = telegram_proxy_root / "config"
        diagnostics_root = telegram_proxy_root / "diagnostics"

        settings = importlib.import_module("telegram_proxy.config.settings")
        upstream_catalog = importlib.import_module("telegram_proxy.config.upstream_catalog")
        diagnostics_runner = importlib.import_module("telegram_proxy.diagnostics.runner")

        self.assertTrue((config_root / "__init__.py").exists())
        self.assertTrue((config_root / "settings.py").exists())
        self.assertTrue((config_root / "upstream_catalog.py").exists())
        self.assertTrue((diagnostics_root / "__init__.py").exists())
        self.assertTrue((diagnostics_root / "runner.py").exists())

        self.assertTrue(hasattr(settings, "build_upstream_config"))
        self.assertTrue(hasattr(upstream_catalog, "UpstreamCatalog"))
        self.assertTrue(hasattr(diagnostics_runner, "run_all"))

        self.assertFalse((telegram_proxy_root / "settings.py").exists())
        self.assertFalse((telegram_proxy_root / "upstream_catalog.py").exists())
        self.assertFalse((telegram_proxy_root / "diagnostics.py").exists())

    def test_runtime_layer_lives_in_runtime_package(self) -> None:
        root = Path(__file__).resolve().parents[1]
        telegram_proxy_root = root / "src" / "telegram_proxy"
        runtime_root = telegram_proxy_root / "runtime"

        commands = importlib.import_module("telegram_proxy.runtime.commands")
        plans = importlib.import_module("telegram_proxy.runtime.plans")
        workers = importlib.import_module("telegram_proxy.runtime.workers")
        public = importlib.import_module("telegram_proxy.public")

        self.assertTrue((runtime_root / "__init__.py").exists())
        self.assertTrue((runtime_root / "commands.py").exists())
        self.assertTrue((runtime_root / "plans.py").exists())
        self.assertTrue((runtime_root / "workers.py").exists())

        self.assertTrue(hasattr(commands, "get_start_config"))
        self.assertTrue(hasattr(plans, "TelegramProxyActionResult"))
        self.assertTrue(hasattr(workers, "TelegramProxyStartWorker"))
        self.assertIs(public.TelegramProxyStartConfig, commands.TelegramProxyStartConfig)

        self.assertFalse((telegram_proxy_root / "actions.py").exists())
        self.assertFalse((telegram_proxy_root / "commands.py").exists())
        self.assertFalse((telegram_proxy_root / "workers.py").exists())


if __name__ == "__main__":
    unittest.main()
