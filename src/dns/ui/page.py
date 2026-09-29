# dns/ui/page.py
"""Страница «Настройка DNS».

Сверху — панель «Сейчас»: какой DNS стоит на отмеченных адаптерах, сами
адаптеры и действия (вернуть автоматически, замерить скорость, сбросить
кэш). Ниже — фильтр по группам и сетка плиток серверов: щелчок по плитке
сразу применяет DNS к отмеченным адаптерам.

Страница только показывает состояние и зовёт готовые действия DNS-слоя.
Всё долгое (загрузка адаптеров, запись DNS, замер, сброс кэша, решение о
предупреждении про DNS провайдера) идёт в фоновых дорожках
(ui.latest_worker_lane): одна задача за раз, из новых запросов побеждает
последний.
"""

from __future__ import annotations

import re
from textwrap import fill
from uuid import uuid4

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, InfoBar, InfoBarPosition, PushButton, RoundMenu, SegmentedWidget

from app.ui_texts import tr as tr_catalog
from dns import page_plans as dns_page_plans
from dns.custom_providers import CUSTOM_DNS_CATEGORY, build_dns_providers_with_custom
from dns.dns_providers import DNS_PROVIDERS
from dns.ui.custom_dns_dialog import CustomDnsDialog, unique_copy_name
from dns.ui.now_panel import AdapterChip, DnsNowPanel, NowState
from dns.ui.provider_grid import ADD_TILE_KEY, DnsProviderGrid, DnsTile, GridTexts
from log.log import log
from settings.store import get_custom_dns_servers, set_custom_dns_servers
from ui.accessibility import set_control_accessibility
from ui.fluent_dialog import MessageBox
from ui.latest_worker_lane import LatestWorkerLane
from ui.message_box_accessibility import set_message_box_button_accessibility
from ui.pages.base_page import BasePage
from ui.popup_menu import exec_popup_menu
from ui.presets_menu.common import fluent_icon, make_menu_action


AUTO_CHOICE = "__auto__"
FILTER_ALL = "all"
RECOMMENDED_PROVIDER = ("Безопасные", "Quad9")

# Группы из dns_providers → ключ перевода подписи.
GROUP_TEXT_KEYS = {
    "Популярные": ("page.network.group.popular", "Популярные"),
    "Безопасные": ("page.network.group.secure", "Безопасные"),
    "Для ИИ": ("page.network.group.ai", "Для ИИ"),
    CUSTOM_DNS_CATEGORY: ("page.network.group.custom", "Свои DNS"),
}
FILTER_TEXT_KEYS = {
    FILTER_ALL: ("page.network.filter.all", "Все"),
    CUSTOM_DNS_CATEGORY: ("page.network.filter.custom", "Свои"),
}


def wrap_infobar_text(content: str, *, width: int = 86) -> str:
    """Переносит длинный текст всплывашки по фразам: InfoBar сам строки не переносит."""
    paragraphs = []
    for paragraph in str(content or "").split("\n\n"):
        lines = []
        for line in paragraph.splitlines() or [""]:
            sentences = [part for part in re.split(r"(?<=[.!?])\s+", line.strip()) if part]
            lines.extend(fill(sentence, width=width, break_long_words=False, break_on_hyphens=False) for sentence in sentences)
        paragraphs.append("\n".join(lines))
    return "\n\n".join(paragraphs)


class NetworkPage(BasePage):
    """Выбор DNS-сервера для сетевых адаптеров Windows."""

    def __init__(self, parent=None, *, deps):
        super().__init__(
            "Настройка DNS",
            "Выберите DNS-сервер — он сразу встанет на отмеченные сетевые адаптеры.",
            parent,
            title_key="page.network.title",
            subtitle_key="page.network.subtitle",
        )
        self._dns = deps.dns_feature

        self._custom_servers: list[dict] = get_custom_dns_servers()
        self._providers: dict = build_dns_providers_with_custom(DNS_PROVIDERS, self._custom_servers)
        # Адаптеры из DNS-слоя (dns.adapters.DnsAdapter), опознаются по GUID.
        self._adapters: tuple = ()
        self._ipv6_available = False
        self._doh_supported = False
        self._loaded = False
        self._load_started = False
        self._closed = False
        self._filter = FILTER_ALL
        # Что сейчас применяется: имя сервера или AUTO_CHOICE.
        self._pending_choice: str | None = None
        self._measuring = False
        self._latency: dict[str, float | None] = {}
        self._intercepted = False

        self._load_lane = LatestWorkerLane(
            name="dns_page_load",
            create_worker=lambda request_id, _payload: self._dns.create_page_load_worker(request_id, parent=self),
            on_result=lambda _payload, state: self._on_page_data(state),
            result_signal="loaded",
            log_fn=log,
        )
        self._apply_lane = LatestWorkerLane(
            name="dns_apply",
            create_worker=self._create_apply_worker,
            on_result=self._on_apply_done,
            on_error=self._on_apply_failed,
            log_fn=log,
        )
        self._flush_lane = LatestWorkerLane(
            name="dns_flush_cache",
            create_worker=lambda request_id, _payload: self._dns.create_dns_flush_cache_worker(
                request_id, language=self._ui_language, parent=self
            ),
            on_result=lambda _payload, plan: self._on_flush_done(plan),
            on_error=lambda _payload, error: self._on_flush_failed(error),
            log_fn=log,
        )
        self._latency_lane = LatestWorkerLane(
            name="dns_latency",
            create_worker=lambda request_id, servers: self._dns.create_dns_latency_worker(
                request_id, servers=servers, parent=self
            ),
            on_result=lambda _payload, report: self._on_latency_done(report),
            on_error=lambda _payload, _error: self._on_latency_done(None),
            log_fn=log,
        )
        self._isp_lane = LatestWorkerLane(
            name="dns_isp_warning",
            create_worker=lambda request_id, _payload: self._dns.create_isp_dns_warning_worker(
                request_id,
                adapters=tuple(self._adapters),
                language=self._ui_language,
                parent=self,
            ),
            on_result=lambda _payload, plan: self._show_isp_warning(plan),
            log_fn=log,
        )

        self._build_ui()
        self._render()

    # ── тексты ──────────────────────────────────────────────

    def _t(self, key: str, default: str, **values) -> str:
        text = tr_catalog(key, language=self._ui_language, default=default)
        return text.format(**values) if values else text

    def _group_title(self, group: str) -> str:
        key, default = GROUP_TEXT_KEYS.get(group, ("", group))
        return self._t(key, default) if key else group

    def _filter_title(self, group: str) -> str:
        key, default = FILTER_TEXT_KEYS.get(group, GROUP_TEXT_KEYS.get(group, ("", group)))
        return self._t(key, default) if key else group

    def _grid_texts(self) -> GridTexts:
        return GridTexts(
            measuring=self._t("page.network.latency.measuring", "замер…"),
            timeout=self._t("page.network.latency.timeout", "нет ответа"),
            ms=self._t("page.network.latency.ms", "{ms} мс"),
            applying=self._t("page.network.tile.applying", "применяю…"),
            selected=self._t("page.network.tile.selected", "выбран"),
            not_selected=self._t("page.network.tile.not_selected", "не выбран"),
            fastest=self._t("page.network.tile.fastest", "быстрее всех"),
            custom_hint=self._t("page.network.tile.custom_hint", "свой DNS, меню правки — клавиша меню"),
            add=self._t("page.network.add_tile.title", "Свой DNS"),
            grid_name=self._t("page.network.grid.name", "DNS-серверы"),
            grid_description=self._t(
                "page.network.grid.description",
                "Стрелки — выбор плитки, Enter или пробел — применить DNS.",
            ),
        )

    # ── сборка ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.now_panel = DnsNowPanel(self.content)
        self.now_panel.reset_clicked.connect(self._confirm_reset_to_auto)
        self.now_panel.measure_clicked.connect(self._measure_latency)
        self.now_panel.flush_clicked.connect(self._flush_cache)
        self.now_panel.adapters_changed.connect(lambda _names: self._render())
        self.add_widget(self.now_panel)
        self.add_spacing(18)

        self.filter_row = QWidget(self.content)
        filter_layout = QHBoxLayout(self.filter_row)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(16)
        self.filter_bar = SegmentedWidget(self.filter_row)
        filter_layout.addWidget(self.filter_bar, 0, Qt.AlignmentFlag.AlignVCenter)
        filter_layout.addStretch(1)
        self.latency_summary = CaptionLabel("", self.filter_row)
        self.latency_summary.hide()
        filter_layout.addWidget(self.latency_summary, 0, Qt.AlignmentFlag.AlignVCenter)
        self.add_widget(self.filter_row)
        self.add_spacing(6)

        self.grid = DnsProviderGrid(self.content)
        self.grid.activated.connect(self._choose_provider)
        self.grid.add_clicked.connect(self._add_custom_server)
        self.grid.context_menu_wanted.connect(self._show_custom_server_menu)
        self.add_widget(self.grid)

        self._rebuild_filter_bar()
        self._retranslate()

    def _rebuild_filter_bar(self) -> None:
        keys = [FILTER_ALL, *self._providers.keys()]
        if CUSTOM_DNS_CATEGORY not in keys:
            keys.append(CUSTOM_DNS_CATEGORY)
        if self._filter not in keys:
            self._filter = FILTER_ALL
        self.filter_bar.clear()
        for key in keys:
            self.filter_bar.addItem(
                routeKey=key,
                text=self._filter_title(key),
                onClick=lambda _checked=False, value=key: self._set_filter(value),
            )
        self.filter_bar.setCurrentItem(self._filter)
        set_control_accessibility(
            self.filter_bar,
            name=self._t("page.network.filter.name", "Группа DNS-серверов"),
            description=self._filter_title(self._filter),
        )

    def _retranslate(self) -> None:
        panel = self.now_panel
        panel.eyebrow_label.setText(self._t("page.network.now.eyebrow", "Сейчас на отмеченных адаптерах"))
        panel.reset_button.setText(self._t("page.network.button.reset", "Вернуть автоматически"))
        panel.flush_button.setText(self._t("page.network.button.flush_dns_cache", "Сбросить кэш"))
        panel.set_measuring(
            self._measuring,
            idle_text=self._t("page.network.button.measure", "Замерить скорость"),
            busy_text=self._t("page.network.button.measure.running", "Замеряю…"),
        )
        panel.set_adapters_caption(
            self._t("page.network.adapters.caption", "Применять к:"),
            self._t("page.network.adapters.empty", "Сетевые адаптеры не найдены"),
        )
        self.grid.set_texts(self._grid_texts())

    def set_ui_language(self, language: str) -> None:
        super().set_ui_language(language)
        self._rebuild_filter_bar()
        self._retranslate()
        self._render()

    def _apply_page_theme(self, tokens=None, force: bool = False) -> None:
        """Рисуемые сетка и значок берут цвета темы при отрисовке — просто перерисовать."""
        _ = tokens, force
        panel = getattr(self, "now_panel", None)
        for widget in (getattr(self, "grid", None), getattr(panel, "badge", None)):
            if widget is not None:
                widget.update()

    # ── жизненный цикл ──────────────────────────────────────

    def on_page_activated(self) -> None:
        self._run_runtime_init_once()

    def _run_runtime_init_once(self) -> None:
        """Первый показ страницы: берём заранее загруженные данные или грузим в фоне."""
        if self._load_started or self._closed:
            return
        self._load_started = True
        try:
            warmed = self._dns.consume_warmed_page_data()
        except Exception as exc:
            log(f"DNS: не удалось взять заранее загруженные данные: {exc}", "DEBUG")
            warmed = None
        if warmed is not None:
            self._on_page_data(warmed)
            return
        self._load_lane.request()

    def cleanup(self) -> None:
        self._closed = True
        for lane in (self._load_lane, self._apply_lane, self._flush_lane, self._latency_lane, self._isp_lane):
            lane.close()

    def _on_page_data(self, state) -> None:
        if self._closed or state is None:
            return
        self._apply_state(state)
        self._isp_lane.request()

    def _apply_state(self, state) -> None:
        """Новый снимок адаптеров; отметки уже известных адаптеров сохраняются."""
        known = self.now_panel.adapter_keys()
        self._adapters = tuple(getattr(state, "adapters", ()) or ())
        self._ipv6_available = bool(getattr(state, "ipv6_available", False))
        self._doh_supported = bool(getattr(state, "doh_supported", False))
        self._loaded = True
        if [adapter.guid for adapter in self._adapters] != known or not known:
            self.now_panel.set_adapters([self._adapter_chip(adapter) for adapter in self._adapters])
        self._render()

    def _adapter_chip(self, adapter) -> AdapterChip:
        if adapter.internet:
            status = self._t("page.network.adapter.internet", "интернет")
        elif not adapter.connected:
            status = self._t("page.network.adapter.disconnected", "не подключён")
        else:
            status = ""
        return AdapterChip(
            key=adapter.guid,
            text=f"{adapter.name} · {status}" if status else adapter.name,
            kind=adapter.kind,
            checked=adapter.connected,
        )

    # ── отрисовка состояния ─────────────────────────────────

    def _selected_adapters(self) -> list[str]:
        """GUID отмеченных адаптеров."""
        return self.now_panel.selected_adapters()

    def _selected_adapter_objects(self) -> list:
        selected = set(self._selected_adapters())
        return [adapter for adapter in self._adapters if adapter.guid in selected]

    def _current_plan(self) -> dns_page_plans.CurrentDnsPlan:
        return dns_page_plans.build_current_dns_plan(
            adapters=self._selected_adapter_objects(),
            providers=self._providers,
        )

    def _render(self) -> None:
        if self._closed:
            return
        plan = self._current_plan() if self._loaded else dns_page_plans.CurrentDnsPlan(kind="none")
        self.now_panel.set_state(self._now_state(plan))
        self.now_panel.update_adapter_tooltips(self._adapter_tooltips())
        self.grid.set_tiles(self._tiles(plan))

    def _provider(self, name: str) -> dict | None:
        for group in self._providers.values():
            if name in group:
                return group[name]
        return None

    @staticmethod
    def _addresses_text(ipv4, ipv6) -> str:
        parts = [" · ".join(ipv4)] if ipv4 else []
        if ipv6:
            parts.append("IPv6: " + " · ".join(ipv6))
        return "   ".join(parts)

    def _now_state(self, plan: dns_page_plans.CurrentDnsPlan) -> NowState:
        pending = self._pending_choice
        if pending is not None:
            applying = self._t("page.network.now.applying", "Применяю…")
            if pending == AUTO_CHOICE:
                return NowState(self._t("page.network.dns.auto", "Автоматически (DHCP)"), applying, "fa5s.sync", busy=True)
            data = self._provider(pending) or {}
            return NowState(pending, applying, data.get("icon", ""), data.get("color", ""), busy=True)
        if not self._loaded:
            return NowState(self._t("page.network.now.loading", "Загружаю настройки сети…"), "", "fa5s.network-wired", busy=True)
        if plan.kind == "none":
            if not self.now_panel.adapter_keys():
                return NowState(self._t("page.network.adapters.empty", "Сетевые адаптеры не найдены"), "", "fa5s.plug")
            return NowState(
                self._t("page.network.now.no_adapters.title", "Адаптеры не отмечены"),
                self._t("page.network.now.no_adapters.detail", "Отметьте адаптер ниже — выбранный DNS встанет на него."),
                "fa5s.plug",
            )
        if plan.kind == "auto":
            detail = self._t("page.network.now.auto.detail", "DNS выдаёт роутер или провайдер.")
            addresses = self._addresses_text(plan.ipv4, plan.ipv6)
            return NowState(
                self._t("page.network.dns.auto", "Автоматически (DHCP)"),
                f"{detail}   {addresses}" if addresses else detail,
                "fa5s.sync",
            )
        if plan.kind == "mixed":
            return NowState(
                self._t("page.network.now.mixed.title", "На адаптерах разные DNS"),
                self._t("page.network.now.mixed.detail", "Выберите сервер — он встанет на все отмеченные адаптеры."),
                "fa5s.random",
                "#d29922",
            )
        detail = self._addresses_text(plan.ipv4, plan.ipv6)
        if plan.kind == "provider" and plan.provider:
            data = self._provider(plan.provider) or {}
            return NowState(plan.provider, detail, data.get("icon", ""), data.get("color", ""))
        return NowState(self._t("page.network.now.custom.title", "Свой DNS"), detail, "fa5s.edit", "#22c55e")

    def _adapter_tooltips(self) -> dict[str, str]:
        tooltips: dict[str, str] = {}
        auto = self._t("page.network.dns.auto", "Автоматически (DHCP)")
        for adapter in self._adapters:
            if adapter.is_automatic:
                addresses = self._addresses_text(adapter.auto_ipv4, adapter.auto_ipv6)
                current = f"{auto}: {addresses}" if addresses else auto
            else:
                current = self._addresses_text(adapter.static_ipv4, adapter.static_ipv6)
            tooltips[adapter.guid] = "\n".join(part for part in (adapter.description, f"DNS: {current}") if part)
        return tooltips

    def _primary_address(self, data: dict) -> str:
        ipv4 = dns_page_plans.normalize_dns_list(data.get("ipv4", []))
        ipv6 = dns_page_plans.normalize_dns_list(data.get("ipv6", []))
        return ipv4[0] if ipv4 else (ipv6[0] if ipv6 else "")

    def _tiles(self, plan: dns_page_plans.CurrentDnsPlan) -> list[DnsTile]:
        current = plan.provider if plan.kind == "provider" else None
        chosen = self._pending_choice if self._pending_choice not in (None, AUTO_CHOICE) else None
        measured = {
            address: value for address, value in self._latency.items() if value is not None
        }
        fastest_address = min(measured, key=measured.get) if measured else ""

        groups = list(self._providers.items())
        if CUSTOM_DNS_CATEGORY not in self._providers:
            groups.append((CUSTOM_DNS_CATEGORY, {}))
        if self._filter != FILTER_ALL:
            groups = [(name, items) for name, items in groups if name == self._filter]

        tiles: list[DnsTile] = []
        for group, items in groups:
            if self._filter == FILTER_ALL:
                tiles.append(DnsTile(kind="group", title=self._group_title(group), counter=str(len(items)) if items else ""))
            for name, data in items.items():
                address = self._primary_address(data)
                if self._measuring and address not in self._latency:
                    latency, latency_ms = "measuring", 0.0
                elif address in self._latency:
                    value = self._latency[address]
                    latency, latency_ms = ("ok", float(value)) if value is not None else ("timeout", 0.0)
                else:
                    latency, latency_ms = "", 0.0
                ipv4 = dns_page_plans.normalize_dns_list(data.get("ipv4", []))
                ipv6 = dns_page_plans.normalize_dns_list(data.get("ipv6", []))
                custom = bool(data.get("custom_id"))
                tooltip_lines = [f"{name} — {data.get('desc', '')}".rstrip(" —")]
                if ipv4:
                    tooltip_lines.append("IPv4: " + ", ".join(dict.fromkeys(ipv4)))
                if ipv6:
                    tooltip_lines.append("IPv6: " + ", ".join(dict.fromkeys(ipv6)))
                if custom:
                    tooltip_lines.append(self._t("page.network.tile.custom_menu", "Правая кнопка мыши — изменить или удалить"))
                tiles.append(
                    DnsTile(
                        kind="provider",
                        key=name,
                        title=name,
                        note=self._t("page.network.tile.custom_note", "Свой сервер") if custom else str(data.get("desc", "")),
                        address=address,
                        icon_name=str(data.get("icon", "")),
                        color=str(data.get("color", "")),
                        selected=(name == chosen) if chosen else (name == current and self._pending_choice is None),
                        pending=name == chosen,
                        has_ipv6=bool(ipv6),
                        has_doh=self._doh_supported and bool(data.get("doh")),
                        custom=custom,
                        latency=latency,
                        latency_ms=latency_ms,
                        fastest=bool(fastest_address) and address == fastest_address,
                        tooltip="\n".join(tooltip_lines),
                    )
                )
            if group == CUSTOM_DNS_CATEGORY:
                tiles.append(
                    DnsTile(
                        kind="add",
                        key=ADD_TILE_KEY,
                        title=self._t("page.network.add_tile.title", "Свой DNS"),
                        note=self._t("page.network.add_tile.note", "Добавить свой адрес"),
                        tooltip=self._t("page.network.custom.button.description", "Открывает окно добавления нового DNS сервера."),
                    )
                )
        return tiles

    def _set_filter(self, key: str) -> None:
        self._filter = key
        set_control_accessibility(
            self.filter_bar,
            name=self._t("page.network.filter.name", "Группа DNS-серверов"),
            description=self._filter_title(key),
        )
        self._render()

    # ── применение DNS ──────────────────────────────────────

    def _info(self, level: str, title: str, content: str = "") -> None:
        getattr(InfoBar, level)(
            title=title,
            content=wrap_infobar_text(content),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=4000 if level == "success" else 6000,
            parent=self.window(),
        )

    def _ready_adapters(self) -> list[str] | None:
        if not self._loaded:
            self._info("info", self._t("page.network.info.wait", "Секунду — загружаю список адаптеров"))
            return None
        adapters = self._selected_adapters()
        if not adapters:
            self._info(
                "warning",
                self._t("page.network.info.no_adapters.title", "Нет отмеченных адаптеров"),
                self._t("page.network.info.no_adapters.content", "Отметьте хотя бы один адаптер в панели сверху."),
            )
            return None
        return adapters

    def _choose_provider(self, name: str) -> None:
        data = self._provider(name)
        if data is None:
            return
        adapters = self._ready_adapters()
        if adapters is None:
            return
        self._pending_choice = name
        self.grid.flash(name)
        self._render()
        self._apply_lane.request(
            {
                "action": "provider",
                "adapters": adapters,
                "name": name,
                "data": dict(data),
                "ipv6_available": self._ipv6_available,
            }
        )

    def _confirm_reset_to_auto(self) -> None:
        adapters = self._ready_adapters()
        if adapters is None:
            return
        title = self._t("page.network.force_dns.reset.button", "Вернуть DNS автоматически")
        body = self._t(
            "page.network.force_dns.reset.confirm",
            "Программа вернёт автоматическое получение DNS через DHCP для выбранных адаптеров. Продолжить?",
        )
        box = MessageBox(title, body, self.window())
        set_message_box_button_accessibility(
            box,
            yes_name=title,
            yes_description=body,
            cancel_name=f"Отменить действие: {title}",
            cancel_description="Закрывает диалог без изменения DNS.",
        )
        if not box.exec():
            return
        self._pending_choice = AUTO_CHOICE
        self._render()
        self._apply_lane.request({"action": "auto", "adapters": adapters})

    def _create_apply_worker(self, request_id: int, payload: dict):
        return self._dns.create_dns_apply_worker(
            request_id,
            action=str(payload.get("action") or ""),
            adapters=list(payload.get("adapters") or []),
            name=str(payload.get("name") or ""),
            data=payload.get("data") or {},
            ipv6_available=bool(payload.get("ipv6_available", False)),
            parent=self,
        )

    def _on_apply_done(self, payload: dict, result) -> None:
        self._pending_choice = None
        data = result if isinstance(result, dict) else {}
        plan = data.get("plan")
        if isinstance(plan, dns_page_plans.NetworkProviderDnsPlan) and not plan.valid:
            self._info("warning", self._t("page.network.error.apply.title", "DNS не применён"), plan.log_message)
        elif plan is not None:
            if getattr(plan, "log_message", ""):
                log(plan.log_message, getattr(plan, "log_level", None) or "INFO")
            failed = int(getattr(plan, "adapter_count", 0)) - int(getattr(plan, "success_count", 0))
            if failed > 0:
                self._info(
                    "warning",
                    self._t("page.network.error.apply.partial.title", "DNS встал не везде"),
                    "\n\n".join(
                        part
                        for part in (
                            self._t(
                                "page.network.error.apply.partial.content",
                                "Не удалось изменить DNS на адаптерах: {failed} из {total}.",
                                failed=failed,
                                total=plan.adapter_count,
                            ),
                            str(getattr(plan, "error", "") or ""),
                        )
                        if part
                    ),
                )
        state = data.get("state")
        if state is not None:
            self._apply_state(state)
        else:
            self._render()

    def _on_apply_failed(self, _payload, error: str) -> None:
        self._pending_choice = None
        self._info("error", self._t("page.network.error.apply.title", "DNS не применён"), error)
        self._render()

    # ── сброс кэша ──────────────────────────────────────────

    def _flush_cache(self) -> None:
        self.now_panel.flush_button.setEnabled(False)
        self._flush_lane.request()

    def _on_flush_done(self, plan) -> None:
        self.now_panel.flush_button.setEnabled(True)
        if getattr(plan, "success", False):
            self._info("success", self._t("page.network.info.flush_done", "Кэш DNS очищен"))
            return
        self._info("warning", getattr(plan, "title", "") or self._t("page.network.error.title", "Ошибка"), getattr(plan, "content", ""))

    def _on_flush_failed(self, error: str) -> None:
        self.now_panel.flush_button.setEnabled(True)
        self._info("warning", self._t("page.network.error.title", "Ошибка"), error)

    # ── замер скорости ──────────────────────────────────────

    def _measure_latency(self) -> None:
        servers = [
            address
            for group in self._providers.values()
            for data in group.values()
            if (address := self._primary_address(data)) and (":" not in address or self._ipv6_available)
        ]
        if not servers:
            return
        self._measuring = True
        self._latency = {}
        self._intercepted = False
        self.now_panel.set_notice("")
        self.latency_summary.hide()
        self._retranslate()
        self._render()
        self._latency_lane.request(servers)

    def _on_latency_done(self, report) -> None:
        self._measuring = False
        self._latency = dict(getattr(report, "results", {}) or {})
        self._intercepted = bool(getattr(report, "intercepted", False))
        self._retranslate()
        self.now_panel.set_notice(
            self._t(
                "page.network.latency.intercepted",
                "Похоже, DNS-запросы перехватываются по пути (провайдером или роутером): ответил даже адрес, "
                "где DNS-сервера нет. Цифры показывают перехватчик, а не выбранные серверы.",
            )
            if self._intercepted
            else ""
        )
        self._update_latency_summary()
        self._render()

    def _update_latency_summary(self) -> None:
        best_name, best_ms = "", None
        for group in self._providers.values():
            for name, data in group.items():
                value = self._latency.get(self._primary_address(data))
                if value is not None and (best_ms is None or value < best_ms):
                    best_name, best_ms = name, value
        if not self._latency:
            text = self._t("page.network.latency.failed", "Замер не удался")
        elif best_ms is None:
            text = self._t("page.network.latency.none", "Ни один сервер не ответил")
        else:
            text = self._t(
                "page.network.latency.best",
                "Быстрее всех: {name} — {ms} мс",
                name=best_name,
                ms=max(1, round(best_ms)),
            )
        self.latency_summary.setText(text)
        self.latency_summary.show()

    # ── предупреждение про DNS провайдера ───────────────────

    def _show_isp_warning(self, plan) -> None:
        if self._closed or not getattr(plan, "should_show", False):
            return
        bar = InfoBar.warning(
            title=plan.title,
            content=wrap_infobar_text(plan.content),
            orient=Qt.Orientation.Vertical,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=10000,
            parent=self.window(),
        )
        if bar is None or not plan.action_text:
            return
        button = PushButton(plan.action_text)

        def accept() -> None:
            bar.close()
            group, name = RECOMMENDED_PROVIDER
            if name in self._providers.get(group, {}):
                self._choose_provider(name)

        button.clicked.connect(accept)
        bar.addWidget(button)

    # ── свои DNS ────────────────────────────────────────────

    def _save_custom_servers(self, servers: list[dict]) -> None:
        self._custom_servers = set_custom_dns_servers(servers)
        self._providers = build_dns_providers_with_custom(DNS_PROVIDERS, self._custom_servers)
        self._rebuild_filter_bar()
        self._render()

    def _custom_index(self, name: str) -> int:
        data = self._provider(name) or {}
        custom_id = str(data.get("custom_id") or "")
        for index, server in enumerate(self._custom_servers):
            if custom_id and str(server.get("id") or "") == custom_id:
                return index
        for index, server in enumerate(self._custom_servers):
            if str(server.get("name") or "") == name:
                return index
        return -1

    def _add_custom_server(self) -> None:
        dialog = CustomDnsDialog(self, ipv6_available=self._ipv6_available)
        if not dialog.exec():
            return
        server = dialog.server()
        if server.get("id"):
            self._save_custom_servers([*self._custom_servers, server])

    def _show_custom_server_menu(self, name: str, global_pos: QPoint) -> None:
        index = self._custom_index(name)
        if index < 0:
            return
        menu = RoundMenu(parent=self)
        commands: dict[object, str] = {}

        def add(text: str, icon_name: str, command: str) -> None:
            action = make_menu_action(text, icon=fluent_icon(icon_name), parent=menu)
            menu.addAction(action)
            item = menu.view.item(menu.view.count() - 1)
            if item is not None:
                item.setData(Qt.ItemDataRole.AccessibleTextRole, text)
                item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, text)
            commands[action] = command

        add(self._t("page.network.custom.menu.edit", "Редактировать"), "EDIT", "edit")
        add(self._t("page.network.custom.menu.duplicate", "Создать копию"), "COPY", "duplicate")
        add(self._t("page.network.custom.menu.copy", "Копировать DNS в буфер обмена"), "COPY", "copy")
        menu.addSeparator()
        add(self._t("page.network.custom.menu.delete", "Удалить"), "DELETE", "delete")

        command = commands.get(exec_popup_menu(menu, global_pos, owner=self, capture_action=True), "")
        if command == "edit":
            self._edit_custom_server(index)
        elif command == "duplicate":
            self._duplicate_custom_server(index)
        elif command == "copy":
            self._copy_custom_server(index)
        elif command == "delete":
            self._save_custom_servers([s for i, s in enumerate(self._custom_servers) if i != index])

    def _edit_custom_server(self, index: int) -> None:
        dialog = CustomDnsDialog(self, server=self._custom_servers[index], ipv6_available=self._ipv6_available)
        if not dialog.exec():
            return
        updated = dialog.server()
        if updated.get("id"):
            servers = list(self._custom_servers)
            servers[index] = updated
            self._save_custom_servers(servers)

    def _duplicate_custom_server(self, index: int) -> None:
        server = dict(self._custom_servers[index])
        server["id"] = f"custom-{uuid4().hex[:12]}"
        server["name"] = unique_copy_name(
            str(server.get("name") or "Свой DNS"),
            [str(item.get("name") or "") for item in self._custom_servers],
        )
        self._save_custom_servers([*self._custom_servers, server])

    def _copy_custom_server(self, index: int) -> None:
        server = self._custom_servers[index]
        values = [
            str(item).strip()
            for item in [*(server.get("ipv4", []) or []), *(server.get("ipv6", []) or [])]
            if str(item).strip()
        ]
        clipboard = QApplication.clipboard()
        if not values or clipboard is None:
            return
        clipboard.setText(", ".join(values))
        self._info(
            "success",
            self._t("page.network.custom.copied.title", "DNS скопирован"),
            self._t("page.network.custom.copied.content", "Адреса DNS в буфере обмена."),
        )


__all__ = ["NetworkPage"]
