from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication

from dataclasses import replace

from dns import page_plans
from dns.adapters import DnsAdapter
from dns.dns_providers import DNS_PROVIDERS
from dns.latency import DnsLatencyReport
from dns.state import DnsState

ETH = "{00000000-0000-0000-0000-000000000009}"
WIFI = "{00000000-0000-0000-0000-000000000012}"
SPARE = "{00000000-0000-0000-0000-000000000020}"

ETHERNET = DnsAdapter(
    ETH, "Ethernet", "Intel I219-V", "ethernet", connected=True, internet=True,
    static_ipv4=("1.1.1.1", "1.0.0.1"), static_ipv6=("2606:4700:4700::1111",),
)
WIFI_ADAPTER = DnsAdapter(WIFI, "Wi-Fi", "Intel AX201", "wifi", connected=True, internet=False, auto_ipv4=("192.168.1.1",))
# Отключённый адаптер виден, но не отмечен.
SPARE_ADAPTER = DnsAdapter(SPARE, "Ethernet 2", "Realtek", "ethernet", connected=False, internet=False)

STATE = DnsState(adapters=(ETHERNET, WIFI_ADAPTER, SPARE_ADAPTER), ipv6_available=False, doh_supported=True)


def _feature(state=STATE):
    return SimpleNamespace(
        consume_warmed_page_data=Mock(return_value=state),
        create_page_load_worker=Mock(),
        create_dns_apply_worker=Mock(),
        create_dns_flush_cache_worker=Mock(),
        create_dns_latency_worker=Mock(),
        create_isp_dns_warning_worker=Mock(),
    )


class DnsPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.saved_servers: list[list[dict]] = []
        self.custom_servers: list[dict] = []
        patches = [
            patch("dns.ui.page.get_custom_dns_servers", side_effect=lambda: list(self.custom_servers)),
            patch("dns.ui.page.set_custom_dns_servers", side_effect=self._save_servers),
            patch("dns.ui.page.InfoBar"),
            patch("dns.ui.now_panel.are_live_animations_enabled", return_value=True),
            patch("dns.ui.provider_grid.are_live_animations_enabled", return_value=True),
        ]
        for item in patches:
            started = item.start()
            self.addCleanup(item.stop)
            if item.attribute == "InfoBar":
                self.info_bar = started

    def _save_servers(self, servers):
        self.custom_servers = list(servers)
        self.saved_servers.append(list(servers))
        return list(servers)

    def _page(self, *, load: bool = True, state=STATE):
        from dns.ui.page import NetworkPage

        feature = _feature(state)
        page = NetworkPage(deps=SimpleNamespace(dns_feature=feature))
        self.addCleanup(page.deleteLater)
        page.resize(1200, 900)
        for lane_name in ("_load_lane", "_apply_lane", "_flush_lane", "_latency_lane", "_isp_lane"):
            getattr(page, lane_name).request = Mock()
        if load:
            page.on_page_activated()
        return page

    @staticmethod
    def _provider_tiles(page):
        return [tile for tile in page.grid.tiles() if tile.kind == "provider"]

    # ── построение ──────────────────────────────────────────

    def test_server_tiles_are_ready_before_network_data_loads(self) -> None:
        page = self._page(load=False)

        total = sum(len(group) for group in DNS_PROVIDERS.values())
        self.assertEqual(len(self._provider_tiles(page)), total)
        self.assertEqual(page.grid.tiles()[-1].kind, "add")
        self.assertEqual(page.now_panel.title_label.text(), "Загружаю настройки сети…")
        self.assertTrue(page.now_panel.badge.is_busy())
        page._load_lane.request.assert_not_called()

    def test_warmed_data_is_used_without_loading_again(self) -> None:
        page = self._page()

        page._load_lane.request.assert_not_called()
        page._isp_lane.request.assert_called_once_with()
        self.assertEqual(page.now_panel.adapter_keys(), [ETH, WIFI, SPARE])
        chips = page.now_panel._chips
        self.assertEqual(chips[ETH].text(), "Ethernet · интернет")
        self.assertEqual(chips[SPARE].text(), "Ethernet 2 · не подключён")
        self.assertEqual([chips[key].isChecked() for key in (ETH, WIFI, SPARE)], [True, True, False])
        page.on_page_activated()
        page._dns.consume_warmed_page_data.assert_called_once_with()

    def test_page_loads_in_background_without_warmed_data(self) -> None:
        from dns.ui.page import NetworkPage

        feature = _feature(None)
        page = NetworkPage(deps=SimpleNamespace(dns_feature=feature))
        self.addCleanup(page.deleteLater)
        page._load_lane.request = Mock()

        page.on_page_activated()
        page.on_page_activated()

        page._load_lane.request.assert_called_once_with()

    # ── текущий DNS ─────────────────────────────────────────

    def test_mixed_adapters_then_single_adapter_states(self) -> None:
        page = self._page()

        self.assertEqual(page.now_panel.title_label.text(), "На адаптерах разные DNS")
        page.now_panel._chips[WIFI].setChecked(False)
        self.assertEqual(page.now_panel.title_label.text(), "Cloudflare")
        self.assertIn("1.1.1.1 · 1.0.0.1", page.now_panel.detail_label.text())
        selected = [tile.key for tile in self._provider_tiles(page) if tile.selected]
        self.assertEqual(selected, ["Cloudflare"])

        page.now_panel._chips[ETH].setChecked(False)
        page.now_panel._chips[WIFI].setChecked(True)
        self.assertEqual(page.now_panel.title_label.text(), "Автоматически (DHCP)")
        page.now_panel._chips[WIFI].setChecked(False)
        self.assertEqual(page.now_panel.title_label.text(), "Адаптеры не отмечены")

    def test_current_dns_plan_matches_providers(self) -> None:
        providers = {"G": {"Only6": {"ipv4": [], "ipv6": ["2a00::1"]}, "Q": {"ipv4": ["9.9.9.9"], "ipv6": []}}}
        base = DnsAdapter("{A}", "A", "", "ethernet", True, False)

        def plan(*static):
            adapters = [replace(base, static_ipv4=tuple(v4), static_ipv6=tuple(v6)) for v4, v6 in static]
            return page_plans.build_current_dns_plan(adapters=adapters, providers=providers)

        self.assertEqual(plan(([], ["2a00::1"]), ([], ["2a00::1"])).provider, "Only6")
        self.assertEqual(plan((["9.9.9.9", "1.2.3.4"], []), (["9.9.9.9"], [])).provider, "Q")
        self.assertEqual(plan((["10.0.0.1"], []), (["10.0.0.1"], [])).kind, "custom")
        self.assertEqual(plan((["10.0.0.1"], []), ([], [])).kind, "mixed")
        self.assertEqual(plan(([], []), ([], [])).kind, "auto")
        self.assertEqual(plan().kind, "none")

    # ── применение ──────────────────────────────────────────

    def test_choosing_server_applies_it_to_checked_adapters_only(self) -> None:
        page = self._page()
        page.now_panel._chips[WIFI].setChecked(False)

        page.grid.activated.emit("Google DNS")

        payload = page._apply_lane.request.call_args.args[0]
        self.assertEqual(payload["action"], "provider")
        self.assertEqual(payload["adapters"], [ETH])
        self.assertEqual(payload["name"], "Google DNS")
        self.assertEqual(payload["data"]["ipv4"][0], "8.8.8.8")
        self.assertFalse(payload["ipv6_available"])
        pending = [tile.key for tile in self._provider_tiles(page) if tile.pending]
        self.assertEqual(pending, ["Google DNS"])
        self.assertTrue(page.grid.is_charging("Google DNS"))
        self.assertTrue(page.now_panel.badge.is_busy())
        self.assertEqual(page.now_panel.detail_label.text(), "Применяю…")

        plan = page_plans.build_provider_dns_apply_result_plan(name="Google DNS", adapter_count=1, success_count=1, ipv6=[])
        google = replace(ETHERNET, static_ipv4=("8.8.8.8", "8.8.4.4"), static_ipv6=())
        page._on_apply_done(payload, {"plan": plan, "state": replace(STATE, adapters=(google, WIFI_ADAPTER, SPARE_ADAPTER))})

        self.assertIsNone(page._pending_choice)
        self.assertEqual(page.now_panel.title_label.text(), "Google DNS")
        # DNS встал мгновенно: комета сначала замыкает круг, потом значок делает оборот.
        self.assertTrue(page.grid.is_charging("Google DNS"))
        page.grid._clock = lambda: 10**9
        page.grid._tick()
        self.assertFalse(page.grid.is_charging())
        self.assertEqual(page.grid.settling_keys(), ["Google DNS"])
        self.info_bar.warning.assert_not_called()

    def test_refreshed_adapters_keep_user_checks(self) -> None:
        page = self._page()
        page.now_panel._chips[WIFI].setChecked(False)
        page.now_panel._chips[SPARE].setChecked(True)

        page._on_apply_done({}, {"plan": None, "state": STATE})

        chips = page.now_panel._chips
        self.assertEqual([chips[key].isChecked() for key in (ETH, WIFI, SPARE)], [True, False, True])

    def test_isp_warning_looks_only_at_connected_adapters(self) -> None:
        self.assertTrue(page_plans.should_show_isp_dns_warning([WIFI_ADAPTER, SPARE_ADAPTER], warning_already_shown=False))
        self.assertFalse(page_plans.should_show_isp_dns_warning([WIFI_ADAPTER], warning_already_shown=True))
        self.assertFalse(page_plans.should_show_isp_dns_warning([ETHERNET, WIFI_ADAPTER], warning_already_shown=False))
        self.assertFalse(page_plans.should_show_isp_dns_warning([SPARE_ADAPTER], warning_already_shown=False))

    def test_partial_apply_and_errors_are_reported(self) -> None:
        page = self._page()
        plan = page_plans.build_auto_dns_apply_result_plan(adapter_count=2, success_count=1, error="«Wi-Fi»: ошибка Windows 5")

        page._on_apply_done({}, {"plan": plan, "state": None})
        content = self.info_bar.warning.call_args.kwargs["content"]
        self.assertIn("1 из 2", content)
        self.assertIn("«Wi-Fi»: ошибка Windows 5", content)

        invalid = page_plans.build_provider_dns_plan(name="X", data={"ipv4": [], "ipv6": ["2a00::1"]}, ipv6_available=False)
        page._on_apply_done({}, {"plan": invalid, "state": None})
        self.assertIn("нет DNS адресов", self.info_bar.warning.call_args.kwargs["content"])

        page._pending_choice = "Quad9"
        page._on_apply_failed({}, "Нет прав администратора")
        self.assertIsNone(page._pending_choice)
        self.assertEqual(self.info_bar.error.call_args.kwargs["content"], "Нет прав администратора")

    def test_nothing_is_applied_without_adapters_or_before_loading(self) -> None:
        page = self._page(load=False)
        page.grid.activated.emit("Quad9")
        self.info_bar.info.assert_called_once()

        page.on_page_activated()
        for chip in page.now_panel._chips.values():
            chip.setChecked(False)
        page.grid.activated.emit("Quad9")

        page._apply_lane.request.assert_not_called()
        self.assertEqual(self.info_bar.warning.call_args.kwargs["title"], "Нет отмеченных адаптеров")

    def test_reset_to_auto_asks_first_and_names_dialog_buttons(self) -> None:
        page = self._page()
        boxes = []

        class Box:
            answer = False

            def __init__(self, title, body, parent=None):
                self.title, self.body = title, body
                self.yesButton, self.cancelButton = Mock(), Mock()
                boxes.append(self)

            def exec(self):
                return Box.answer

        with patch("dns.ui.page.MessageBox", Box):
            page.now_panel.reset_button.click()
            page._apply_lane.request.assert_not_called()
            Box.answer = True
            page.now_panel.reset_button.click()

        self.assertIn("DHCP", boxes[0].body)
        boxes[0].yesButton.setAccessibleName.assert_called_with(boxes[0].title)
        boxes[0].cancelButton.setAccessibleName.assert_called_with(f"Отменить действие: {boxes[0].title}")
        payload = page._apply_lane.request.call_args.args[0]
        self.assertEqual(payload, {"action": "auto", "adapters": [ETH, WIFI]})
        self.assertEqual(page.now_panel.title_label.text(), "Автоматически (DHCP)")
        self.assertEqual(page.now_panel.detail_label.text(), "Применяю…")

    # ── фильтр ──────────────────────────────────────────────

    def test_filter_shows_one_group_without_headers(self) -> None:
        page = self._page()

        page._set_filter("Для ИИ")
        kinds = {tile.kind for tile in page.grid.tiles()}
        self.assertEqual(kinds, {"provider"})
        self.assertEqual(len(page.grid.tiles()), len(DNS_PROVIDERS["Для ИИ"]))

        page._set_filter("Свои DNS")
        self.assertEqual([tile.kind for tile in page.grid.tiles()], ["add"])

    # ── замер скорости ──────────────────────────────────────

    def test_latency_measurement_marks_tiles_and_fastest_server(self) -> None:
        page = self._page()

        page.now_panel.measure_button.click()
        servers = page._latency_lane.request.call_args.args[0]
        self.assertIn("1.1.1.1", servers)
        self.assertTrue(all(":" not in server for server in servers))
        self.assertFalse(page.now_panel.measure_button.isEnabled())
        self.assertEqual({tile.latency for tile in self._provider_tiles(page)}, {"measuring"})

        page._on_latency_done(DnsLatencyReport(results={"1.1.1.1": 30.0, "8.8.8.8": 9.6, "9.9.9.9": None}))

        tiles = {tile.key: tile for tile in self._provider_tiles(page)}
        self.assertTrue(tiles["Google DNS"].fastest)
        self.assertEqual(tiles["Quad9"].latency, "timeout")
        self.assertEqual(tiles["AdGuard"].latency, "")
        self.assertEqual(page.latency_summary.text(), "Быстрее всех: Google DNS — 10 мс")
        self.assertTrue(page.now_panel.measure_button.isEnabled())
        self.assertTrue(page.now_panel.notice_label.isHidden())

    def test_intercepted_dns_is_explained(self) -> None:
        page = self._page()
        page._measure_latency()

        page._on_latency_done(DnsLatencyReport(results={"1.1.1.1": 48.0}, intercepted=True))

        self.assertFalse(page.now_panel.notice_label.isHidden())
        self.assertIn("перехватываются", page.now_panel.notice_label.text())

        page._measure_latency()
        page._on_latency_done(None)
        self.assertEqual(page.latency_summary.text(), "Замер не удался")

    # ── предупреждение про DNS провайдера ───────────────────

    def test_isp_warning_offers_quad9_in_top_right_infobar(self) -> None:
        page = self._page()
        bar = Mock()
        self.info_bar.warning.return_value = bar
        plan = page_plans.build_isp_dns_warning_plan([WIFI_ADAPTER], warning_already_shown=False)

        page._show_isp_warning(plan)

        kwargs = self.info_bar.warning.call_args.kwargs
        self.assertEqual(kwargs["duration"], 10000)
        self.assertEqual(kwargs["position"].name, "TOP_RIGHT")
        self.assertLessEqual(max(len(line) for line in kwargs["content"].splitlines()), 86)
        button = bar.addWidget.call_args.args[0]
        button.click()
        bar.close.assert_called_once_with()
        self.assertEqual(page._apply_lane.request.call_args.args[0]["name"], "Quad9")

    def test_isp_warning_is_skipped_when_not_needed(self) -> None:
        page = self._page()

        page._show_isp_warning(SimpleNamespace(should_show=False))

        self.info_bar.warning.assert_not_called()

    # ── свои DNS ────────────────────────────────────────────

    def test_custom_server_add_edit_duplicate_copy_delete(self) -> None:
        page = self._page()
        page._ipv6_available = True
        dialogs = []

        class Dialog:
            result = {"id": "custom-1", "name": "Дом", "ipv4": ["192.168.1.1"], "ipv6": ["fd00::1"]}

            def __init__(self, parent=None, *, server=None, ipv6_available=False):
                dialogs.append((server, ipv6_available))

            def exec(self):
                return True

            def server(self):
                return dict(Dialog.result)

        with patch("dns.ui.page.CustomDnsDialog", Dialog):
            page.grid.add_clicked.emit()
            self.assertEqual(dialogs[-1], (None, True))
            self.assertIn("Дом", [tile.key for tile in self._provider_tiles(page)])

            Dialog.result = {"id": "custom-1", "name": "Дача", "ipv4": ["10.0.0.1"], "ipv6": []}
            self._run_menu(page, "Дом", "Редактировать")
            self.assertEqual(dialogs[-1][0]["name"], "Дом")

        self.assertEqual([server["name"] for server in self.custom_servers], ["Дача"])
        self._run_menu(page, "Дача", "Создать копию")
        self.assertEqual(len(self.custom_servers), 2)
        self.assertNotEqual(self.custom_servers[1]["id"], "custom-1")

        self._run_menu(page, "Дача", "Копировать DNS в буфер обмена")
        self.assertEqual(QApplication.clipboard().text(), "10.0.0.1")

        self._run_menu(page, "Дача", "Удалить")
        self.assertEqual(len(self.custom_servers), 1)
        self.assertNotIn("Дача", [tile.key for tile in self._provider_tiles(page)])

    def _run_menu(self, page, name: str, text: str) -> None:
        def choose(menu, _pos, **_kwargs):
            return next(action for action in menu.actions() if action.text() == text)

        with patch("dns.ui.page.exec_popup_menu", side_effect=choose):
            page.grid.context_menu_wanted.emit(name, QPoint(0, 0))

    # ── прочее ──────────────────────────────────────────────

    def test_flush_cache_reports_result(self) -> None:
        page = self._page()

        page.now_panel.flush_button.click()
        page._flush_lane.request.assert_called_once_with()
        self.assertFalse(page.now_panel.flush_button.isEnabled())
        page._on_flush_done(page_plans.build_flush_dns_cache_result_plan(success=True, message=""))

        self.assertTrue(page.now_panel.flush_button.isEnabled())
        self.assertEqual(self.info_bar.success.call_args.kwargs["title"], "Кэш DNS очищен")

    def test_english_interface(self) -> None:
        page = self._page()

        page.set_ui_language("en")

        self.assertEqual(page.now_panel.measure_button.text(), "Measure speed")
        self.assertEqual(page.now_panel.title_label.text(), "Adapters use different DNS")
        self.assertEqual(page.grid.tiles()[0].title, "Popular")

    def test_cleanup_closes_every_lane(self) -> None:
        page = self._page()
        lanes = [page._load_lane, page._apply_lane, page._flush_lane, page._latency_lane, page._isp_lane]
        for lane in lanes:
            lane.close = Mock()

        page.cleanup()

        for lane in lanes:
            lane.close.assert_called_once_with()
        page._on_page_data(STATE)


if __name__ == "__main__":
    unittest.main()
