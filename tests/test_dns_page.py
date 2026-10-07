from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow

from dataclasses import replace

from dns import page_plans
from dns.adapters import DnsAdapter
from dns.dns_providers import DNS_PROVIDERS
from dns.latency import DnsLatencyReport
from dns.state import CustomServerResult, DnsState
from dns.ui.page import AUTO_CHOICE

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

HOME = {"id": "custom-1", "name": "Дом", "ipv4": ["192.168.1.1"], "ipv6": ["fd00::1"], "doh": ""}
SECURE = {
    "id": "custom-2",
    "name": "dns.example.com",
    "ipv4": ["203.0.113.5"],
    "ipv6": [],
    "doh": "https://dns.example.com/dns-query",
}


def _feature(state=STATE):
    return SimpleNamespace(
        consume_warmed_page_data=Mock(return_value=state),
        create_page_load_worker=Mock(),
        create_dns_apply_worker=Mock(),
        create_dns_flush_cache_worker=Mock(),
        create_dns_latency_worker=Mock(),
        create_isp_dns_warning_worker=Mock(),
        create_custom_server_worker=Mock(),
    )


class DnsPageTests(unittest.TestCase):
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

    def _page(self, *, load: bool = True, state=STATE):
        from dns.ui.page import NetworkPage

        feature = _feature(state)
        self.open_custom_server = Mock()
        page = NetworkPage(deps=SimpleNamespace(dns_feature=feature, open_custom_server=self.open_custom_server))
        self.addCleanup(page.deleteLater)
        page.resize(1200, 900)
        for lane in page._lanes():
            lane.request = Mock()
        if load:
            page.on_page_activated()
        return page

    # ── экскурсия ────────────────────────────────────────────

    def test_tour_opens_ai_group_and_returns_the_previous_filter(self) -> None:
        page = self._page()
        page.filter_bar.setCurrentItem("Безопасные")
        page._set_filter("Безопасные")
        self.assertIsNone(page.onboarding_target("ai"))

        page.onboarding_set_state("ai")

        self.assertEqual(page.onboarding_target("ai"), [page.filter_row, page.grid])
        self.assertEqual({tile.key for tile in self._provider_tiles(page)}, set(DNS_PROVIDERS["Для ИИ"]))
        self.assertEqual(page.filter_bar.currentRouteKey(), "Для ИИ")

        page.onboarding_set_state(None)

        self.assertEqual(page._filter, "Безопасные")
        self.assertEqual(page.filter_bar.currentRouteKey(), "Безопасные")
        self.assertEqual({tile.key for tile in self._provider_tiles(page)}, set(DNS_PROVIDERS["Безопасные"]))

    def test_tour_points_at_filters_with_the_automatic_tile_and_opens_empty_form(self) -> None:
        page = self._page()

        filter_row, (grid, rect) = page.onboarding_target("providers")

        self.assertIs(filter_row, page.filter_row)
        self.assertIs(grid, page.grid)
        self.assertEqual(rect, page.grid.tile_rect(AUTO_CHOICE))
        self.assertFalse(rect.isEmpty())
        self.assertIs(page.onboarding_target("now"), page.now_panel)
        self.assertTrue(page.onboarding_open_subpage("custom_dns"))
        self.open_custom_server.assert_called_once_with(None)
        self.assertFalse(page.onboarding_open_subpage("hosts_file"))

    def test_one_time_provider_dns_advice_waits_until_the_tour_is_over(self) -> None:
        # Во время экскурсии совет всплыл бы поверх неё и пропал зря: он показывается один раз.
        with patch("ui.onboarding.is_onboarding_tour_active", return_value=True):
            page = self._page()
            page._isp_lane.request.assert_not_called()
            page.on_page_activated()
            page._isp_lane.request.assert_not_called()

        page.on_page_activated()
        page._isp_lane.request.assert_called_once_with()
        page.on_page_activated()
        page._isp_lane.request.assert_called_once_with()

    @staticmethod
    def _provider_tiles(page):
        """Плитки серверов без плитки «Автоматически»."""
        return [tile for tile in page.grid.tiles() if tile.kind == "provider" and tile.key != AUTO_CHOICE]

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
        page = NetworkPage(deps=SimpleNamespace(dns_feature=feature, open_custom_server=Mock()))
        self.addCleanup(page.deleteLater)
        page._load_lane.request = Mock()

        page.on_page_activated()

        page._load_lane.request.assert_called_once_with()
        # Повторное открытие обновляет состояние, а заранее загруженные данные больше не ищет.
        page.on_page_activated()
        self.assertEqual(page._load_lane.request.call_count, 2)
        feature.consume_warmed_page_data.assert_called_once_with()

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

        page.grid.activated.emit("Quad9")

        payload = page._apply_lane.request.call_args.args[0]
        self.assertEqual(payload["action"], "provider")
        self.assertEqual(payload["adapters"], [ETH])
        self.assertEqual(payload["name"], "Quad9")
        self.assertEqual(payload["data"]["ipv4"][0], "9.9.9.9")
        self.assertFalse(payload["ipv6_available"])
        pending = [tile.key for tile in self._provider_tiles(page) if tile.pending]
        self.assertEqual(pending, ["Quad9"])
        self.assertTrue(page.grid.is_charging("Quad9"))
        self.assertTrue(page.now_panel.badge.is_busy())
        self.assertEqual(page.now_panel.detail_label.text(), "Применяю…")

        plan = page_plans.build_provider_dns_apply_result_plan(name="Quad9", adapter_count=1, success_count=1, ipv6=[])
        quad9 = replace(ETHERNET, static_ipv4=("9.9.9.9", "149.112.112.112"), static_ipv6=())
        page._on_apply_done(payload, {"plan": plan, "state": replace(STATE, adapters=(quad9, WIFI_ADAPTER, SPARE_ADAPTER))})

        self.assertIsNone(page._pending_choice)
        self.assertEqual(page.now_panel.title_label.text(), "Quad9")
        # DNS встал мгновенно: комета сначала замыкает круг, потом значок делает оборот.
        self.assertTrue(page.grid.is_charging("Quad9"))
        page.grid._clock = lambda: 10**9
        page.grid._tick()
        self.assertFalse(page.grid.is_charging())
        self.assertEqual(page.grid.settling_keys(), ["Quad9"])
        self.info_bar.warning.assert_not_called()

    def test_refreshed_adapters_keep_user_checks(self) -> None:
        page = self._page()
        page.now_panel._chips[WIFI].setChecked(False)
        page.now_panel._chips[SPARE].setChecked(True)

        page._on_apply_done({}, {"plan": None, "state": STATE})

        chips = page.now_panel._chips
        self.assertEqual([chips[key].isChecked() for key in (ETH, WIFI, SPARE)], [True, False, True])

    def test_all_button_checks_every_adapter_and_restores_initial_checks(self) -> None:
        page = self._page()
        panel = page.now_panel
        chips = panel._chips

        self.assertFalse(panel.all_chip.isHidden())
        self.assertFalse(panel.all_chip.isChecked())

        panel.all_chip.click()
        self.assertEqual(page._selected_adapters(), [ETH, WIFI, SPARE])
        self.assertTrue(panel.all_chip.isChecked())

        page.grid.activated.emit("Quad9")
        self.assertEqual(page._apply_lane.request.call_args.args[0]["adapters"], [ETH, WIFI, SPARE])

        chips[SPARE].setChecked(False)
        self.assertFalse(panel.all_chip.isChecked())

        chips[SPARE].setChecked(True)
        panel.all_chip.click()
        # Все уже были отмечены — возвращаются исходные отметки: только подключённые.
        self.assertEqual(page._selected_adapters(), [ETH, WIFI])
        self.assertFalse(panel.all_chip.isChecked())

    def test_all_button_is_hidden_for_a_single_adapter(self) -> None:
        page = self._page(state=replace(STATE, adapters=(ETHERNET,)))

        self.assertTrue(page.now_panel.all_chip.isHidden())

    def test_tiles_carry_status_dnssec_and_group_notes(self) -> None:
        page = self._page()
        tiles = {tile.key: tile for tile in self._provider_tiles(page)}

        self.assertEqual(tiles["Google DNS"].status, "blocked")
        self.assertEqual(tiles["AdGuard"].status, "at_risk")
        self.assertEqual(tiles["Quad9"].status, "")
        self.assertIn("В России блокируется", tiles["Cloudflare"].tooltip)
        self.assertTrue(tiles["Quad9"].has_dnssec)
        self.assertIn("DNSSEC", tiles["Quad9"].tooltip)
        self.assertFalse(tiles["Яндекс DNS"].has_dnssec)

        notes = {tile.title: tile.note for tile in page.grid.tiles() if tile.kind == "group"}
        self.assertEqual(notes["Для ИИ"], "серверы сообщества — доверия к ним меньше")
        self.assertEqual(notes["Популярные"], "")

        # При фильтре заголовка группы нет — пояснение уходит в подсказку плитки.
        page._set_filter("Для ИИ")
        self.assertIn("доверия к ним меньше", self._provider_tiles(page)[0].tooltip)

    def test_choosing_blocked_server_applies_it_and_warns(self) -> None:
        page = self._page()

        page.grid.activated.emit("Google DNS")

        self.assertEqual(page._apply_lane.request.call_args.args[0]["name"], "Google DNS")
        self.assertIn("Google DNS в России блокируется", self.info_bar.warning.call_args.kwargs["title"])

        self.info_bar.reset_mock()
        page.grid.activated.emit("AdGuard")
        self.info_bar.warning.assert_not_called()

    def test_encrypted_dns_tile_is_selected_by_the_running_mode(self) -> None:
        local = replace(ETHERNET, static_ipv4=("127.0.0.1",), static_ipv6=("::1",))
        adapters = (local, WIFI_ADAPTER, SPARE_ADAPTER)

        page = self._page(state=replace(STATE, adapters=adapters, local_proxy_mode="anonymized"))
        page.now_panel._chips[WIFI].setChecked(False)
        selected = [tile.key for tile in self._provider_tiles(page) if tile.selected]
        self.assertEqual(selected, ["DNSCrypt анонимный"])
        self.assertEqual(page.now_panel.title_label.text(), "DNSCrypt анонимный")

        # Движок не работает: 127.0.0.1 на адаптере — чужой локальный DNS, а не наш режим.
        page = self._page(state=replace(STATE, adapters=adapters, local_proxy_mode=""))
        page.now_panel._chips[WIFI].setChecked(False)
        self.assertEqual([tile.key for tile in self._provider_tiles(page) if tile.selected], [])
        self.assertEqual(page.now_panel.title_label.text(), "Свой DNS")

    def test_choosing_encrypted_dns_sends_its_mode_to_the_worker(self) -> None:
        page = self._page()

        page.grid.activated.emit("DNSCrypt")

        payload = page._apply_lane.request.call_args.args[0]
        self.assertEqual(payload["data"]["local_proxy"], "dnscrypt")
        self.assertEqual(payload["data"]["ipv4"], ["127.0.0.1"])
        self.assertIn("dnscrypt-proxy", {tile.key: tile for tile in self._provider_tiles(page)}["DNSCrypt"].tooltip)

        # Движок не ответил: страница показывает причину, DNS не менялся.
        failed = page_plans.NetworkProviderDnsPlan(valid=False, ipv4=[], ipv6=[], log_level="WARNING", log_message="Порт 53 занят")
        page._on_apply_done(payload, {"plan": failed, "state": STATE})
        self.assertEqual(self.info_bar.warning.call_args.kwargs["content"], "Порт 53 занят")
        self.assertIsNone(page._pending_choice)

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

    def test_automatic_dns_is_the_first_tile_in_every_filter(self) -> None:
        page = self._page(state=replace(STATE, adapters=(WIFI_ADAPTER,)))

        for key in ("all", "Популярные", "Свои DNS"):
            with self.subTest(filter=key):
                page._set_filter(key)
                first = page.grid.tiles()[0]
                self.assertEqual((first.kind, first.key, first.title), ("provider", AUTO_CHOICE, "Автоматически"))
                self.assertEqual(first.note, "DNS от роутера")
                # На адаптере автоматический DNS: плитка выбрана и показывает адрес роутера.
                self.assertTrue(first.selected)
                self.assertEqual(first.address, "192.168.1.1")
                self.assertIn("DHCP", first.tooltip)

    def test_automatic_tile_is_not_selected_while_a_server_is_set(self) -> None:
        page = self._page(state=replace(STATE, adapters=(ETHERNET,)))

        auto = page.grid.tile(AUTO_CHOICE)

        self.assertFalse(auto.selected)
        self.assertEqual(auto.address, "")
        self.assertFalse(hasattr(page.now_panel, "reset_button"))

    def test_automatic_tile_resets_dns_at_once_without_a_question(self) -> None:
        page = self._page()

        page.grid.activated.emit(AUTO_CHOICE)

        payload = page._apply_lane.request.call_args.args[0]
        self.assertEqual(payload, {"action": "auto", "adapters": [ETH, WIFI]})
        self.assertEqual(page.now_panel.title_label.text(), "Автоматически (DHCP)")
        self.assertEqual(page.now_panel.detail_label.text(), "Применяю…")
        auto = page.grid.tile(AUTO_CHOICE)
        self.assertTrue(auto.pending and auto.selected)
        # Пока DNS возвращается, ни один сервер не выглядит выбранным.
        self.assertFalse(any(tile.selected for tile in self._provider_tiles(page)))

    # ── фильтр ──────────────────────────────────────────────

    def test_filter_shows_one_group_without_headers(self) -> None:
        page = self._page()

        page._set_filter("Для ИИ")
        kinds = {tile.kind for tile in page.grid.tiles()}
        self.assertEqual(kinds, {"provider"})
        # Плитка «Автоматически» остаётся первой и при фильтре.
        self.assertEqual(len(page.grid.tiles()), len(DNS_PROVIDERS["Для ИИ"]) + 1)

        page._set_filter("Свои DNS")
        self.assertEqual([(tile.kind, tile.key) for tile in page.grid.tiles()], [("provider", AUTO_CHOICE), ("add", "__add__")])

    # ── замер скорости ──────────────────────────────────────

    def test_latency_measurement_marks_tiles_and_fastest_server(self) -> None:
        page = self._page()

        page.now_panel.measure_button.click()
        servers = page._latency_lane.request.call_args.args[0]
        self.assertIn("1.1.1.1", servers)
        self.assertTrue(all(":" not in server for server in servers))
        self.assertFalse(page.now_panel.measure_button.isEnabled())
        self.assertNotIn("127.0.0.1", servers)
        network_tiles = [tile for tile in self._provider_tiles(page) if tile.address != "127.0.0.1"]
        self.assertEqual({tile.latency for tile in network_tiles}, {"measuring"})
        # Режимы шифрованного DNS живут на этом компьютере и не замеряются.
        self.assertEqual({tile.latency for tile in self._provider_tiles(page) if tile.address == "127.0.0.1"}, {""})

        page._on_latency_done(DnsLatencyReport(results={"1.1.1.1": 30.0, "8.8.8.8": 9.6, "9.9.9.9": None}))

        tiles = {tile.key: tile for tile in self._provider_tiles(page)}
        self.assertTrue(tiles["Google DNS"].fastest)
        self.assertEqual(tiles["Quad9"].latency, "timeout")
        self.assertEqual(tiles["AdGuard"].latency, "")
        self.assertEqual(page.latency_summary.text(), "Быстрее всех: Google DNS — 10 мс")
        self.assertTrue(page.now_panel.measure_button.isEnabled())
        self.assertTrue(page.now_panel.notice_label.isHidden())

    def test_backup_address_is_measured_and_shown_in_tooltip(self) -> None:
        """Как «Результат 2» в DNS Jumper: запасной адрес может молчать, когда основной отвечает."""
        page = self._page()

        page.now_panel.measure_button.click()
        servers = page._latency_lane.request.call_args.args[0]
        self.assertIn("8.8.8.8", servers)
        self.assertIn("8.8.4.4", servers)
        self.assertEqual(len(servers), len(set(servers)))

        page._on_latency_done(
            DnsLatencyReport(results={"8.8.8.8": 12.0, "8.8.4.4": None, "1.1.1.1": 30.0, "1.0.0.1": 3.0})
        )

        tiles = {tile.key: tile for tile in self._provider_tiles(page)}
        self.assertIn("IPv4: 8.8.8.8 — 12 мс, 8.8.4.4 — нет ответа", tiles["Google DNS"].tooltip)
        # Плитка показывает основной адрес; запасной адрес другого сервера «быстрее всех» не делает.
        self.assertEqual((tiles["Google DNS"].latency, tiles["Google DNS"].latency_ms), ("ok", 12.0))
        self.assertTrue(tiles["Google DNS"].fastest)
        self.assertFalse(tiles["Cloudflare"].fastest)
        self.assertEqual(page.latency_summary.text(), "Быстрее всех: Google DNS — 12 мс")
        # Без замера в подсказке просто адреса.
        self.assertIn("IPv4: 94.140.14.14, 94.140.15.15", tiles["AdGuard"].tooltip)

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

    def test_custom_servers_come_with_the_page_state(self) -> None:
        page = self._page(state=replace(STATE, custom_servers=(HOME, SECURE)))

        tiles = {tile.key: tile for tile in self._provider_tiles(page)}

        self.assertTrue(tiles["Дом"].custom)
        self.assertFalse(tiles["Дом"].has_doh)
        self.assertTrue(tiles["dns.example.com"].has_doh)
        self.assertIn("DoH: https://dns.example.com/dns-query", tiles["dns.example.com"].tooltip)
        self.assertEqual(tiles["dns.example.com"].address, "203.0.113.5")

    def test_page_without_loaded_state_shows_builtin_servers_only(self) -> None:
        page = self._page(load=False)

        self.assertFalse(any(tile.custom for tile in self._provider_tiles(page)))
        self.assertIn("Quad9", [tile.key for tile in self._provider_tiles(page)])

    def test_add_tile_and_edit_open_the_custom_server_page(self) -> None:
        page = self._page(state=replace(STATE, custom_servers=(HOME, SECURE)))

        page.grid.add_clicked.emit()
        self.open_custom_server.assert_called_once_with(None)

        self._run_menu(page, "dns.example.com", "Редактировать")
        self.assertEqual(self.open_custom_server.call_args.args[0], SECURE)

    def test_duplicate_and_delete_run_in_background_and_refresh_tiles(self) -> None:
        page = self._page(state=replace(STATE, custom_servers=(HOME, SECURE)))

        self._run_menu(page, "Дом", "Создать копию")
        self.assertEqual(page._custom_lane.request.call_args.args[0], {"action": "duplicate", "server_id": "custom-1"})
        self._run_menu(page, "Дом", "Удалить")
        self.assertEqual(page._custom_lane.request.call_args.args[0], {"action": "delete", "server_id": "custom-1"})

        page._on_custom_servers_changed(CustomServerResult(success=True, servers=(SECURE,)))

        keys = [tile.key for tile in self._provider_tiles(page)]
        self.assertNotIn("Дом", keys)
        self.assertIn("dns.example.com", keys)

    def test_custom_lane_worker_gets_action_and_server_id(self) -> None:
        page = self._page()

        page._custom_lane._create_worker(5, {"action": "delete", "server_id": "custom-1"})

        page._dns.create_custom_server_worker.assert_called_once_with(
            5, action="delete", server_id="custom-1", parent=page
        )

    def test_failed_custom_change_is_reported_and_keeps_the_list(self) -> None:
        page = self._page(state=replace(STATE, custom_servers=(HOME,)))

        page._on_custom_servers_changed(CustomServerResult(success=False, servers=(HOME,), error="база занята"))

        self.assertEqual(self.info_bar.warning.call_args.kwargs["content"], "база занята")
        self.assertIn("Дом", [tile.key for tile in self._provider_tiles(page)])

    def test_copy_puts_doh_and_addresses_into_clipboard(self) -> None:
        page = self._page(state=replace(STATE, custom_servers=(HOME, SECURE)))

        self._run_menu(page, "Дом", "Копировать DNS в буфер обмена")
        self.assertEqual(QApplication.clipboard().text(), "192.168.1.1, fd00::1")

        self._run_menu(page, "dns.example.com", "Копировать DNS в буфер обмена")
        self.assertEqual(QApplication.clipboard().text(), "https://dns.example.com/dns-query, 203.0.113.5")

    def test_reopening_the_page_reloads_state_with_new_custom_servers(self) -> None:
        page = self._page()
        page._load_lane.request.assert_not_called()
        page._isp_lane.request.reset_mock()

        page.on_page_activated()
        page._load_lane.request.assert_called_once_with()
        page._on_page_data(replace(STATE, custom_servers=(SECURE,)))

        self.assertIn("dns.example.com", [tile.key for tile in self._provider_tiles(page)])
        # Совет про DNS провайдера решается один раз, а не при каждом возврате на страницу.
        page._isp_lane.request.assert_not_called()

    def _run_menu(self, page, name: str, text: str) -> None:
        def choose(menu, _pos, **_kwargs):
            return next(action for action in menu.actions() if action.text() == text)

        with patch("dns.ui.page.exec_popup_menu", side_effect=choose):
            page.grid.context_menu_wanted.emit(name, QPoint(0, 0))

    # ── прочее ──────────────────────────────────────────────

    def test_flush_cache_reports_result(self) -> None:
        page = self._page()

        page.now_panel.flush_button.click()
        page.now_panel.flush_button.click()
        # Второе нажатие, пока кэш чистится, новый сброс не запускает.
        page._flush_lane.request.assert_called_once_with()
        self.assertTrue(page.now_panel.flush_button.isEnabled())
        page._on_flush_done(page_plans.build_flush_dns_cache_result_plan(success=True, message=""))

        self.assertEqual(self.info_bar.success.call_args.kwargs["title"], "Кэш DNS очищен")
        page.now_panel.flush_button.click()
        self.assertEqual(page._flush_lane.request.call_count, 2)

    def test_flush_button_does_not_scroll_the_page(self) -> None:
        # Раньше кнопка на время сброса выключалась: фокус уходил на сетку
        # плиток, и страница прокручивалась к выбранному серверу.
        page = self._page(state=replace(STATE, adapters=(replace(ETHERNET, static_ipv4=("193.233.112.67",)),)))
        window = QMainWindow()
        self.addCleanup(window.deleteLater)
        window.setCentralWidget(page)
        window.resize(1200, 500)
        window.show()
        window.activateWindow()
        self._app.processEvents()
        bar = page.verticalScrollBar()
        self.assertGreater(bar.maximum(), 300)

        QTest.mouseClick(page.now_panel.flush_button, Qt.MouseButton.LeftButton)
        self._app.processEvents()

        page._flush_lane.request.assert_called_once_with()
        self.assertEqual(bar.value(), 0)
        self.assertIs(self._app.focusWidget(), page.now_panel.flush_button)
        window.takeCentralWidget()

    def test_english_interface(self) -> None:
        page = self._page()

        page.set_ui_language("en")

        self.assertEqual(page.now_panel.measure_button.text(), "Measure speed")
        self.assertEqual(page.now_panel.title_label.text(), "Adapters use different DNS")
        self.assertEqual(page.grid.tiles()[0].title, "Automatic")
        self.assertEqual(page.grid.tiles()[1].title, "Encrypted")
        self.assertEqual(page.now_panel.flush_button.text(), "Flush DNS cache")

    def test_cleanup_closes_every_lane(self) -> None:
        page = self._page()
        lanes = [
            page._load_lane,
            page._apply_lane,
            page._flush_lane,
            page._latency_lane,
            page._custom_lane,
            page._isp_lane,
        ]
        for lane in lanes:
            lane.close = Mock()

        page.cleanup()

        for lane in lanes:
            lane.close.assert_called_once_with()
        page._on_page_data(STATE)


if __name__ == "__main__":
    unittest.main()
