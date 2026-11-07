# dns/ui/page.py
"""Страница «Настройка DNS».

Сверху — панель «Сейчас»: какой DNS стоит на отмеченных адаптерах, сами
адаптеры и действия (замерить скорость, сбросить кэш). Ниже — фильтр по
группам и сетка плиток: щелчок по плитке сразу применяет DNS к отмеченным
адаптерам. Первая плитка — «Автоматически»: она возвращает DNS роутера или
провайдера. Рядом с ней, пока показаны все группы, стоит широкая рекламная
плитка своего сервера проекта «Zapret DNS»: она ничего не применяет, а
открывает сайт сервера в браузере.

Свои серверы пользователя приходят вместе с состоянием страницы
(DnsState.custom_servers). Добавляет и правит их отдельная вложенная
страница «Свой DNS» (dns.ui.custom_server_page), сюда она возвращает уже
сохранённый результат: при каждом открытии страница перечитывает состояние.

Страница только показывает состояние и зовёт готовые действия DNS-слоя.
Всё долгое (загрузка адаптеров, запись DNS, замер, сброс кэша, изменение
списка своих серверов, решение о предупреждении про DNS провайдера) идёт в
фоновых дорожках (ui.latest_worker_lane): одна задача за раз, из новых
запросов побеждает последний.
"""

from __future__ import annotations

import re
import time
from textwrap import fill

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, InfoBar, InfoBarPosition, PushButton, RoundMenu, SegmentedWidget

from app.ui_texts import tr as tr_catalog
from config import zapret_dns
from diagnostics.history import format_time
from dns import page_plans as dns_page_plans
from dns import custom_servers
from dns import doh_switch, joke_confirm, joke_effects
from dns.custom_servers import CUSTOM_DNS_CATEGORY, build_dns_providers_with_custom
from dns.dns_providers import (
    DNS_PROVIDERS,
    FUN_GROUP,
    LOCAL_PROXY_GROUP,
    STATUS_AT_RISK,
    STATUS_BLOCKED,
    ZAPRET_DNS_GROUP,
    ZAPRET_DNS_PROVIDER_NAME,
    is_encrypted_only,
    zapret_dns_provider,
)
from dns.server_check_plans import CARD_NETWORK, CARD_OK, CARD_SILENT, CARD_TITLES
from dns.state import LineCheck
from dns.ui.now_panel import AdapterChip, DnsNowPanel, NowState
from dns.ui.provider_grid import ADD_TILE_KEY, PROMO_TILE_KEY, DnsProviderGrid, DnsTile, GridTexts
from log.log import log
from ui.accessibility import set_control_accessibility
from ui.fluent_widgets import set_tooltip
from ui.latest_worker_lane import LatestWorkerLane
from ui.pages.base_page import BasePage
from ui.popup_menu import exec_popup_menu
from ui.presets_menu.common import fluent_icon, make_menu_action
from ui.widgets.aligned_switch import AlignedSwitchButton


AUTO_CHOICE = "__auto__"
FILTER_ALL = "all"
RECOMMENDED_PROVIDER = ("Безопасные", "Quad9")
# Группа, которую экскурсия открывает как пример обхода гео-ограничений.
ONBOARDING_AI_GROUP = "Для ИИ"
# Сколько помнить, кто отвечает на DNS: чтобы не спрашивать сеть при каждом входе на страницу.
ANSWERING_CACHE_S = 600.0
# Свой сервер проекта: его значок и цвет берёт рекламная плитка рядом с «Автоматически».
PROMO_PROVIDER = ("Для ИИ", "Zapret DNS")
# Значки сервисов на рекламной плитке: то, что сервер открывает (имя значка, цвет).
PROMO_SERVICE_ICONS = (
    ("own:openai", "#10A37F"),
    ("simple:claude", "#D97757"),
    ("simple:googlegemini", "#8E75B2"),
    ("simple:spotify", "#1ED760"),
    ("own:grok", "#E6E6E6"),
    ("simple:twitch", "#9146FF"),
)

# Группы из dns_providers → ключ перевода подписи.
GROUP_TEXT_KEYS = {
    LOCAL_PROXY_GROUP: ("page.network.group.encrypted", "Шифрованные"),
    "Популярные": ("page.network.group.popular", "Популярные"),
    "Безопасные": ("page.network.group.secure", "Безопасные"),
    "Малоизвестные": ("page.network.group.lesser_known", "Малоизвестные"),
    "Для ИИ": ("page.network.group.ai", "Для ИИ"),
    FUN_GROUP: ("page.network.group.fun", "Для смеха"),
    CUSTOM_DNS_CATEGORY: ("page.network.group.custom", "Свои DNS"),
}
# Короткое пояснение справа от названия группы.
GROUP_NOTE_KEYS = {
    LOCAL_PROXY_GROUP: (
        "page.network.group.encrypted.note",
        "запросы шифрует встроенный dnscrypt-proxy — их не подменить и не закрыть по имени",
    ),
    "Малоизвестные": ("page.network.group.lesser_known.note", "реже попадают под блокировки"),
    "Для ИИ": ("page.network.group.ai.note", "серверы сообщества — доверия к ним меньше"),
    FUN_GROUP: ("page.network.group.fun.note", "шутка — ставить не стоит"),
}
# Пометка сервера → строка подсказки плитки.
STATUS_TOOLTIP_KEYS = {
    STATUS_BLOCKED: (
        "page.network.status.blocked.tooltip",
        "В России блокируется: обычные запросы к этому серверу не доходят или подменяются по дороге.",
    ),
    STATUS_AT_RISK: (
        "page.network.status.at_risk.tooltip",
        "Под угрозой блокировки в России: пока работает, но может перестать отвечать.",
    ),
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
        self._open_custom_server = deps.open_custom_server
        self._create_open_url_worker = deps.create_open_url_worker

        # Свои серверы приходят с состоянием страницы; до загрузки видны серверы программы.
        self._custom_servers: list[dict] = []
        self._providers: dict = build_dns_providers_with_custom(DNS_PROVIDERS, self._custom_servers)
        # Адаптеры из DNS-слоя (dns.adapters.DnsAdapter), опознаются по GUID.
        self._adapters: tuple = ()
        self._ipv6_available = False
        self._doh_supported = False
        # Переключатель «Шифровать DNS (DoH)» (dns.doh_switch).
        self._doh_enabled = True
        # Номер последней показанной шуточной фразы (dns.joke_confirm): та же подряд не выпадает.
        self._last_joke = 0
        # Прошлая проверка «DNS-серверов» на этой линии (dns.state.LineCheck) или None.
        self._line_check = None
        # Режим работающего шифрованного DNS; по нему находится его плитка.
        self._local_proxy_mode = ""
        self._local_proxy_server = ""
        self._loaded = False
        self._load_started = False
        self._closed = False
        self._filter = FILTER_ALL
        # Что сейчас применяется: имя сервера или AUTO_CHOICE.
        self._pending_choice: str | None = None
        self._measuring = False
        self._flushing = False
        self._latency: dict[str, float | None] = {}
        self._intercepted = False
        # Кто на самом деле отвечает: (адрес, режим шифрованного DNS) → (когда узнали, dns.answering.Answering).
        self._answering: dict[tuple[str, str], tuple[float, object]] = {}
        self._answering_pending: tuple[str, str] | None = None

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
        self._answering_lane = LatestWorkerLane(
            name="dns_answering",
            create_worker=lambda request_id, payload: self._dns.create_dns_answering_worker(
                request_id, address=payload[0], parent=self
            ),
            on_result=lambda payload, result: self._on_answering_done(payload, result),
            on_error=lambda payload, _error: self._on_answering_done(payload, None),
            log_fn=log,
        )
        self._custom_lane = LatestWorkerLane(
            name="dns_custom_servers",
            create_worker=lambda request_id, payload: self._dns.create_custom_server_worker(
                request_id, action=payload["action"], server_id=payload["server_id"], parent=self
            ),
            on_result=lambda _payload, result: self._on_custom_servers_changed(result),
            on_error=lambda _payload, error: self._info("warning", self._t("page.network.error.title", "Ошибка"), error),
            log_fn=log,
        )
        self._site_lane = LatestWorkerLane(
            name="dns_open_site",
            create_worker=lambda request_id, url: self._create_open_url_worker(request_id, url=url, parent=self),
            on_result=lambda url, result: self._on_site_opened(url, result),
            on_error=lambda url, error: self._on_site_open_failed(url, error),
            result_signal="loaded",
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

    def _group_note(self, group: str) -> str:
        key, default = GROUP_NOTE_KEYS.get(group, ("", ""))
        return self._t(key, default) if key else ""

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
            status_blocked=self._t("page.network.status.blocked", "блокируется"),
            status_at_risk=self._t("page.network.status.at_risk", "под угрозой"),
            grid_name=self._t("page.network.grid.name", "DNS-серверы"),
            grid_description=self._t(
                "page.network.grid.description",
                "Стрелки — выбор плитки, Enter или пробел — применить DNS.",
            ),
        )

    # ── сборка ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.now_panel = DnsNowPanel(self.content)
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
        self.doh_label = CaptionLabel("", self.filter_row)
        filter_layout.addWidget(self.doh_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.doh_toggle = AlignedSwitchButton(self.filter_row)
        self.doh_toggle.setChecked(True)
        self.doh_toggle.checkedChanged.connect(self._on_doh_toggled)
        filter_layout.addWidget(self.doh_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        self.add_widget(self.filter_row)
        self.add_spacing(6)

        self.grid = DnsProviderGrid(self.content)
        self.grid.activated.connect(self._choose_provider)
        self.grid.add_clicked.connect(lambda: self._open_custom_server(None))
        self.grid.promo_clicked.connect(self._open_promo_site)
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
        panel.flush_button.setText(self._t("page.network.button.flush_dns_cache", "Сбросить кэш DNS"))
        panel.set_measuring(
            self._measuring,
            idle_text=self._t("page.network.button.measure", "Замерить скорость"),
            busy_text=self._t("page.network.button.measure.running", "Замеряю…"),
        )
        panel.set_adapters_caption(
            self._t("page.network.adapters.caption", "Применять к:"),
            self._t("page.network.adapters.empty", "Сетевые адаптеры не найдены"),
        )
        panel.set_all_adapters_text(
            self._t("page.network.adapters.all", "Все"),
            self._t(
                "page.network.adapters.all.tooltip",
                "Отметить все сетевые интерфейсы: выбранный DNS встанет на каждый из них. "
                "Повторное нажатие возвращает исходные отметки.",
            ),
        )
        self.grid.set_texts(self._grid_texts())
        doh_title = self._t("page.network.doh.switch", "Шифровать DNS (DoH)")
        doh_hint = self._t(
            "page.network.doh.switch.tooltip",
            "Включено: серверы с меткой DoH спрашиваются шифрованно (в Windows 10 — через встроенный "
            "dnscrypt-proxy). Выключено: те же серверы ставятся обычными адресами, и провайдер видит запросы. "
            "Группа «Шифрованные» от переключателя не зависит.",
        )
        self.doh_label.setText(doh_title)
        set_tooltip(self.doh_label, doh_hint)
        set_tooltip(self.doh_toggle, doh_hint)
        set_control_accessibility(self.doh_toggle, name=doh_title, description=doh_hint)

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
        """Первый показ загружает состояние, каждый следующий — тихо обновляет его.

        Обновление показывает то, что изменилось, пока страницы не было видно:
        новый свой сервер со страницы «Свой DNS», другой адаптер, DNS,
        поменянный в настройках Windows.
        """
        if self._load_started and not self._closed:
            self._load_lane.request()
            self._request_isp_warning_if_due()
            return
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
        for lane in self._lanes():
            lane.close()

    def _lanes(self) -> tuple[LatestWorkerLane, ...]:
        return (
            self._load_lane,
            self._apply_lane,
            self._flush_lane,
            self._latency_lane,
            self._answering_lane,
            self._custom_lane,
            self._site_lane,
            self._isp_lane,
        )

    def _on_page_data(self, state) -> None:
        if self._closed or state is None:
            return
        first = not self._loaded
        self._apply_state(state)
        if first:
            self._isp_warning_due = True
            self._request_isp_warning_if_due()

    def _request_isp_warning_if_due(self) -> None:
        """Разовый совет про DNS провайдера. Во время экскурсии он бы
        всплыл поверх неё и пропал зря, поэтому ждёт обычного открытия страницы."""
        if not self.__dict__.get("_isp_warning_due") or self._closed:
            return
        from ui.onboarding import is_onboarding_tour_active

        if is_onboarding_tour_active(self.window()):
            return
        self._isp_warning_due = False
        self._isp_lane.request()

    # ── экскурсия ───────────────────────────────────────────

    def onboarding_target(self, name: str):
        if name == "now":
            return self.now_panel
        if name == "providers":
            # Отбор групп и первая строка сетки — «Автоматически» и реклама Zapret DNS:
            # вся сетка слишком велика.
            first_row = self.grid.tile_rect(AUTO_CHOICE).united(self.grid.tile_rect(PROMO_TILE_KEY))
            return [self.filter_row, (self.grid, first_row)]
        if name == "ai":
            return [self.filter_row, self.grid] if self._filter == ONBOARDING_AI_GROUP else None
        return None

    def onboarding_set_state(self, state: str | None) -> None:
        """Экскурсия открывает группу «Для ИИ», а потом возвращает прежний отбор."""
        if state == "ai":
            if ONBOARDING_AI_GROUP not in self._providers or self._filter == ONBOARDING_AI_GROUP:
                return
            self._onboarding_previous_filter = self._filter
            self._show_filter(ONBOARDING_AI_GROUP)
            return
        previous = self.__dict__.pop("_onboarding_previous_filter", None)
        if previous is not None:
            self._show_filter(previous)

    def _show_filter(self, key: str) -> None:
        self.filter_bar.setCurrentItem(key)
        self._set_filter(key)

    def onboarding_open_subpage(self, key: str) -> bool:
        """Экскурсия открывает «Свой DNS» с пустой формой — как плитка «Добавить»."""
        if key != "custom_dns":
            return False
        self._open_custom_server(None)
        return True

    def _apply_state(self, state) -> None:
        """Новый снимок адаптеров; отметки уже известных адаптеров сохраняются."""
        known = self.now_panel.adapter_keys()
        self._adapters = tuple(getattr(state, "adapters", ()) or ())
        self._ipv6_available = bool(getattr(state, "ipv6_available", False))
        self._doh_supported = bool(getattr(state, "doh_supported", False))
        self._doh_enabled = bool(getattr(state, "doh_enabled", True))
        self._set_doh_toggle(self._doh_enabled)
        self._local_proxy_mode = str(getattr(state, "local_proxy_mode", "") or "")
        self._local_proxy_server = str(getattr(state, "local_proxy_server", "") or "")
        line_check = getattr(state, "line_check", None)
        self._line_check = line_check if isinstance(line_check, LineCheck) else None
        self._loaded = True
        self._set_custom_servers(getattr(state, "custom_servers", ()) or (), render=False)
        if [adapter.guid for adapter in self._adapters] != known or not known:
            self.now_panel.set_adapters([self._adapter_chip(adapter) for adapter in self._adapters])
        self._render()
        self._request_answering()

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
            local_proxy_mode=self._local_proxy_mode,
            local_proxy_server=self._local_proxy_server,
        )

    def _render(self) -> None:
        if self._closed:
            return
        self._sync_live_providers()
        plan = self._current_plan() if self._loaded else dns_page_plans.CurrentDnsPlan(kind="none")
        self.now_panel.set_state(self._now_state(plan))
        self.now_panel.update_adapter_tooltips(self._adapter_tooltips())
        self.grid.set_tiles(self._tiles(plan))
        # Предупреждение зависит и от отметок адаптеров: неотмеченный адаптер — дорога мимо выбранного сервера.
        self._render_notice()

    def _sync_live_providers(self) -> None:
        """Копия каталога страницы догоняет живую запись «Zapret DNS» после переезда сервера.

        Страница держит глубокую копию каталога провайдеров, а адрес своего сервера
        меняется во время работы (dns.zapret_dns_server.adopt). Без сверки плитка
        показала бы и записала на адаптер прежний адрес.
        """
        live = zapret_dns_provider()
        shown = self._providers.get(ZAPRET_DNS_GROUP, {}).get(ZAPRET_DNS_PROVIDER_NAME)
        if shown is not None and shown.get("ipv4") != live.get("ipv4"):
            self._providers = build_dns_providers_with_custom(DNS_PROVIDERS, self._custom_servers)

    def _provider(self, name: str) -> dict | None:
        self._sync_live_providers()
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
            # Загрузка — не действие пользователя: значок стоит спокойно, без кометы.
            return NowState(
                self._t("page.network.now.loading", "Загружаю настройки сети…"), "", "fa5s.network-wired", loading=True
            )
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
                self._with_answering(f"{detail}   {addresses}" if addresses else detail, plan),
                "fa5s.sync",
            )
        if plan.kind == "mixed":
            return NowState(
                self._t("page.network.now.mixed.title", "На адаптерах разные DNS"),
                self._t("page.network.now.mixed.detail", "Выберите сервер — он встанет на все отмеченные адаптеры."),
                "fa5s.random",
                "#d29922",
            )
        detail = self._with_answering(self._addresses_text(plan.ipv4, plan.ipv6), plan)
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

    def _all_addresses(self, data: dict) -> list[str]:
        """Все адреса сервера, которые имеет смысл замерять в этой сети."""
        if data.get("local_proxy") or is_encrypted_only(data):
            # Режим шифрованного DNS живёт на этом компьютере: замерять дорогу до него
            # незачем. А сервер только с шифрованием на обычный запрос замера не ответит.
            return []
        addresses = dns_page_plans.normalize_dns_list(data.get("ipv4", []))
        if self._ipv6_available:
            addresses = [*addresses, *dns_page_plans.normalize_dns_list(data.get("ipv6", []))]
        return list(dict.fromkeys(addresses))

    def _addresses_with_latency(self, addresses: list[str]) -> str:
        """Адреса через запятую; после замера у каждого — его время или «нет ответа»."""
        parts = []
        for address in dict.fromkeys(addresses):
            if address in self._latency:
                value = self._latency[address]
                speed = (
                    self._t("page.network.latency.ms", "{ms} мс", ms=max(1, round(value)))
                    if value is not None
                    else self._t("page.network.latency.timeout", "нет ответа")
                )
                parts.append(f"{address} — {speed}")
            else:
                parts.append(address)
        return ", ".join(parts)

    def _tiles(self, plan: dns_page_plans.CurrentDnsPlan) -> list[DnsTile]:
        current = plan.provider if plan.kind == "provider" else None
        chosen = self._pending_choice if self._pending_choice not in (None, AUTO_CHOICE) else None
        # «Быстрее всех» выбирается среди основных адресов: именно он встанет первым.
        primary = {
            address
            for group in self._providers.values()
            for data in group.values()
            if not data.get("local_proxy") and (address := self._primary_address(data))
        }
        measured = {
            address: value
            for address, value in self._latency.items()
            if value is not None and address in primary
        }
        fastest_address = min(measured, key=measured.get) if measured else ""

        groups = list(self._providers.items())
        if CUSTOM_DNS_CATEGORY not in self._providers:
            groups.append((CUSTOM_DNS_CATEGORY, {}))
        if self._filter != FILTER_ALL:
            groups = [(name, items) for name, items in groups if name == self._filter]

        tiles: list[DnsTile] = [self._auto_tile(plan)]
        promo = self._promo_tile()
        if promo is not None:
            tiles.append(promo)
        for group, items in groups:
            if self._filter == FILTER_ALL:
                tiles.append(
                    DnsTile(
                        kind="group",
                        title=self._group_title(group),
                        counter=str(len(items)) if items else "",
                        note=self._group_note(group),
                    )
                )
            for name, data in items.items():
                address = self._primary_address(data)
                local_proxy = bool(data.get("local_proxy"))
                encrypted_only = is_encrypted_only(data)
                # Обычная плитка, которую программа поставит через встроенный шифрованный DNS:
                # Windows сама шифровать не умеет, а у сервера есть шифрованный адрес.
                via_proxy = not local_proxy and bool(
                    doh_switch.proxy_mode(data, doh_supported=self._doh_supported, doh_enabled=self._doh_enabled)
                )
                if local_proxy or encrypted_only:
                    latency, latency_ms = "", 0.0
                elif self._measuring and address not in self._latency:
                    latency, latency_ms = "measuring", 0.0
                elif address in self._latency:
                    value = self._latency[address]
                    latency, latency_ms = ("ok", float(value)) if value is not None else ("timeout", 0.0)
                else:
                    latency, latency_ms = "", 0.0
                ipv4 = dns_page_plans.normalize_dns_list(data.get("ipv4", []))
                ipv6 = dns_page_plans.normalize_dns_list(data.get("ipv6", []))
                custom = bool(data.get("custom_id"))
                status, measured = self._line_status(name, data)
                tooltip_lines = [f"{name} — {data.get('desc', '')}".rstrip(" —")]
                if measured:
                    tooltip_lines.append(measured)
                elif status in STATUS_TOOLTIP_KEYS:
                    tooltip_lines.append(self._t(*STATUS_TOOLTIP_KEYS[status]))
                if local_proxy:
                    tooltip_lines.append(
                        self._t(
                            "page.network.tile.local_proxy",
                            "Программа запустит на компьютере службу dnscrypt-proxy и пропишет адаптеру "
                            "адрес 127.0.0.1. Если шифрованные серверы не ответят, DNS не изменится.",
                        )
                    )
                if via_proxy:
                    tooltip_lines.append(
                        self._t(
                            "page.network.tile.via_proxy",
                            "Эта Windows сама шифровать DNS не умеет, поэтому программа поставит сервер через "
                            "встроенный шифрованный DNS: запустит службу dnscrypt-proxy и пропишет адаптеру адрес "
                            "127.0.0.1. Если шифрованный путь до сервера закрыт, сервер встанет обычным адресом — "
                            "программа об этом скажет.",
                        )
                    )
                if encrypted_only and not local_proxy and not self._doh_enabled:
                    tooltip_lines.append(
                        self._t(
                            "page.network.tile.encrypted_only.switch_off",
                            "Принимает только шифрованные запросы: включите «Шифровать DNS (DoH)», "
                            "иначе этот сервер не поставить.",
                        )
                    )
                if encrypted_only and not via_proxy and self._doh_enabled:
                    tooltip_lines.append(
                        self._t(
                            "page.network.tile.encrypted_only",
                            "Принимает только шифрованные запросы: Windows 11 спрашивает его по DoH, "
                            "скорость обычным запросом не замерить.",
                        )
                        if self._doh_supported
                        else self._t(
                            "page.network.tile.encrypted_only.unsupported",
                            "Принимает только шифрованные запросы, а эта Windows сама шифровать DNS не умеет "
                            "(нужна Windows 11): здесь сервер работать не будет.",
                        )
                    )
                group_note = self._group_note(group)
                if group_note and self._filter != FILTER_ALL:
                    # Заголовка группы при фильтре нет — пояснение уходит в подсказку.
                    tooltip_lines.append(f"{self._group_title(group)}: {group_note}")
                if ipv4:
                    tooltip_lines.append("IPv4: " + self._addresses_with_latency(ipv4))
                if ipv6:
                    tooltip_lines.append("IPv6: " + self._addresses_with_latency(ipv6))
                if data.get("dnssec"):
                    tooltip_lines.append(
                        self._t(
                            "page.network.tile.dnssec",
                            "DNSSEC: сервер проверяет подписи ответов и не отдаёт подделанные.",
                        )
                    )
                if custom and data.get("doh"):
                    tooltip_lines.append(f"DoH: {data['doh']}")
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
                        has_doh=doh_switch.tile_encrypts(
                            data, doh_supported=self._doh_supported, doh_enabled=self._doh_enabled
                        ),
                        has_dnssec=bool(data.get("dnssec")),
                        custom=custom,
                        latency=latency,
                        latency_ms=latency_ms,
                        fastest=bool(fastest_address) and address == fastest_address and not local_proxy,
                        tooltip="\n".join(tooltip_lines),
                        status=status,
                    )
                )
            if group == CUSTOM_DNS_CATEGORY:
                tiles.append(
                    DnsTile(
                        kind="add",
                        key=ADD_TILE_KEY,
                        title=self._t("page.network.add_tile.title", "Свой DNS"),
                        note=self._t("page.network.add_tile.note", "Добавить свой адрес"),
                        tooltip=self._t(
                            "page.network.custom.button.description",
                            "Открывает страницу добавления своего DNS-сервера: по адресу DoH или по IP-адресам.",
                        ),
                    )
                )
        return tiles

    def _auto_tile(self, plan: dns_page_plans.CurrentDnsPlan) -> DnsTile:
        """Первая плитка: вернуть DNS, который выдаёт роутер или провайдер."""
        pending = self._pending_choice == AUTO_CHOICE
        return DnsTile(
            kind="provider",
            key=AUTO_CHOICE,
            title=self._t("page.network.auto_tile.title", "Автоматически"),
            note=self._t("page.network.auto_tile.note", "DNS от роутера"),
            address=plan.ipv4[0] if plan.kind == "auto" and plan.ipv4 else "",
            icon_name="fa5s.sync",
            selected=pending or (plan.kind == "auto" and self._pending_choice is None),
            pending=pending,
            tooltip=self._t(
                "page.network.auto_tile.tooltip",
                "DNS снова будет получаться автоматически от роутера или провайдера (DHCP). "
                "Помогает, если после ручной настройки интернет работает нестабильно.",
            ),
        )

    def _promo_tile(self) -> DnsTile | None:
        """Реклама своего сервера проекта: ссылка на его сайт, DNS не меняет.

        Стоит в первой строке, пока показаны все группы: заголовок первой группы
        всё равно начинает новую строку, и место рядом с «Автоматически» пустует.
        При отборе одной группы плитки идут сразу за «Автоматически» — рекламы нет.
        """
        group, name = PROMO_PROVIDER
        data = self._providers.get(group, {}).get(name)
        if self._filter != FILTER_ALL or not data:
            return None
        return DnsTile(
            kind="promo",
            key=PROMO_TILE_KEY,
            title=name,
            badge=self._t("page.network.promo.badge", "сервер проекта"),
            note=self._t("page.network.promo.note", "Открывает ChatGPT, Claude, Gemini и Spotify без VPN"),
            address=zapret_dns.HOST,
            icon_name=str(data.get("icon", "")),
            color=str(data.get("color", "")),
            icons=PROMO_SERVICE_ICONS,
            tooltip=self._t(
                "page.network.promo.tooltip",
                "Откроется сайт сервера в браузере: работает ли он сейчас, какие сервисы открывает и как "
                "проверить подключение.\nЧтобы включить сам сервер, нажмите плитку «Zapret DNS» в группе «Для ИИ».",
            ),
        )

    def _open_promo_site(self) -> None:
        """Сайт открывает фоновая задача: запуск браузера не должен задерживать окно."""
        self._site_lane.request(zapret_dns.SITE_URL)

    def _on_site_opened(self, url, result) -> None:
        if not getattr(result, "ok", False):
            self._on_site_open_failed(url, str(getattr(result, "error", "") or ""))

    def _on_site_open_failed(self, url, error: str) -> None:
        log(f"DNS: не удалось открыть сайт {url}: {error}", "WARNING")
        self._info(
            "warning",
            self._t("page.network.promo.open_failed.title", "Не удалось открыть сайт"),
            self._t(
                "page.network.promo.open_failed.content",
                "Откройте его в браузере сами: {url}",
                url=str(url or ""),
            ),
        )

    def _set_filter(self, key: str) -> None:
        self._filter = key
        set_control_accessibility(
            self.filter_bar,
            name=self._t("page.network.filter.name", "Группа DNS-серверов"),
            description=self._filter_title(key),
        )
        self._render()

    # ── применение DNS ──────────────────────────────────────

    def _line_status(self, name: str, data: dict) -> tuple[str, str]:
        """Отметка плитки и строка подсказки о проверке на линии пользователя.

        Своя недавняя проверка «DNS-серверов» точнее общей отметки каталога:
        блокировки у каждого провайдера свои. Нет проверки или о сервере в ней
        ничего не известно — остаётся отметка каталога и пустая строка.
        """
        status = str(data.get("status", "") or "")
        line_check = self.__dict__.get("_line_check")
        found = line_check.card(name) if line_check is not None else None
        if found is None:
            return status, ""
        card, note = found
        if card in (CARD_NETWORK, CARD_SILENT):
            status = STATUS_BLOCKED
        elif card == CARD_OK:
            status = ""
        verdict = f"{CARD_TITLES[card]} — {note}" if note else CARD_TITLES[card]
        return status, self._t(
            "page.network.tile.line_check",
            "Проверка {time} на вашей линии: {verdict}",
            time=format_time(line_check.time),
            verdict=verdict,
        )

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
        if name == AUTO_CHOICE:
            self._reset_to_auto()
            return
        data = self._provider(name)
        if data is None:
            return
        adapters = self._ready_adapters()
        if adapters is None:
            return
        if joke_confirm.needs_confirmation(data) and not self._confirm_joke():
            return
        proxy_mode = doh_switch.proxy_mode(data, doh_supported=self._doh_supported, doh_enabled=self._doh_enabled)
        refusal = doh_switch.refusal(data, doh_supported=self._doh_supported, doh_enabled=self._doh_enabled)
        if refusal == doh_switch.REFUSE_SWITCH_OFF:
            self._info(
                "warning",
                self._t("page.network.doh.refused.title", "{name} работает только с шифрованием", name=name),
                self._t(
                    "page.network.doh.refused.content",
                    "Сервер принимает только шифрованные запросы, а переключатель «Шифровать DNS (DoH)» выключен. "
                    "DNS не изменён. Включите переключатель, чтобы поставить этот сервер.",
                ),
            )
            return
        if refusal == doh_switch.REFUSE_WINDOWS:
            # Обычный DNS такой сервер не принимает: с ним не открылся бы ни один сайт.
            self._info(
                "warning",
                self._t("page.network.encrypted_only.refused.title", "{name} работает только в Windows 11", name=name),
                self._t(
                    "page.network.encrypted_only.refused.content",
                    "Сервер принимает только шифрованные запросы, а эта Windows сама шифровать DNS не умеет. "
                    "DNS не изменён. Шифрование без Windows 11 даёт группа «Шифрованные».",
                ),
            )
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
                "proxy_mode": proxy_mode,
            }
        )

    def _confirm_joke(self) -> bool:
        """Шуточная плитка «НСДИ»: окно со случайной фразой. True — человек настаивает."""
        from ui.fluent_dialog import MessageBox
        from ui.message_box_accessibility import set_message_box_button_accessibility

        self._last_joke = joke_confirm.pick(self._last_joke)
        title_key, content_key = joke_confirm.phrase_keys(self._last_joke)
        title = self._t(title_key, "")
        box = MessageBox(f"{joke_effects.title_emoji(self._last_joke)} {title}", self._t(content_key, ""), self.window())
        yes_text = self._t("page.network.joke.yes", "Да, я уверен")
        no_text = self._t("page.network.joke.no", "Нет, передумал")
        box.yesButton.setText(yes_text)
        box.cancelButton.setText(no_text)
        set_message_box_button_accessibility(
            box,
            yes_name=yes_text,
            yes_description=self._t(
                "page.network.joke.yes.description",
                "Ставит DNS «НСДИ» на отмеченные адаптеры. Вернуть прежний можно плиткой «Автоматически».",
            ),
            cancel_name=no_text,
            cancel_description=self._t("page.network.joke.no.description", "DNS не изменится."),
        )
        joke_effects.play(box, index=self._last_joke, title=title)
        return bool(box.exec())

    def _set_doh_toggle(self, checked: bool) -> None:
        """Ставит переключатель DoH, не вызывая его обработчик (состояние пришло от программы, а не от человека)."""
        was_blocked = self.doh_toggle.blockSignals(True)
        self.doh_toggle.setChecked(bool(checked))
        self.doh_toggle.blockSignals(was_blocked)

    def _on_doh_toggled(self, checked: bool) -> None:
        """Переключатель DoH: запоминает выбор и сразу ставит выбранный сейчас сервер заново.

        Заново ставится только обычный сервер с DoH: плитки «Шифрованные» и сервер
        без DoH от переключателя не зависят, для них выбор лишь запоминается.
        """
        checked = bool(checked)
        if self._closed or checked == self._doh_enabled:
            return
        plan = self._current_plan() if self._loaded else dns_page_plans.CurrentDnsPlan(kind="none")
        name = plan.provider if plan.kind == "provider" else None
        data = self._provider(name) if name else None
        if (
            data is not None
            and doh_switch.refusal(data, doh_supported=self._doh_supported, doh_enabled=checked)
            == doh_switch.REFUSE_SWITCH_OFF
        ):
            # Сейчас стоит сервер только с шифрованием: без DoH компьютер остался бы без DNS.
            self._set_doh_toggle(True)
            self._info(
                "warning",
                self._t("page.network.doh.keep_on.title", "DoH остаётся включённым"),
                self._t(
                    "page.network.doh.keep_on.content",
                    "Сейчас стоит {name}: он принимает только шифрованные запросы и без DoH не заработает. "
                    "Выберите другой сервер — тогда DoH можно выключить.",
                    name=name,
                ),
            )
            return
        self._doh_enabled = checked
        adapters = self._selected_adapters()
        if data is not None and adapters and not data.get("local_proxy") and data.get("doh"):
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
                    "proxy_mode": doh_switch.proxy_mode(
                        data, doh_supported=self._doh_supported, doh_enabled=checked
                    ),
                    "doh_enabled": checked,
                }
            )
            return
        self._render()
        self._apply_lane.request({"action": "doh_setting", "adapters": [], "doh_enabled": checked})

    def _reset_to_auto(self) -> None:
        """Плитка «Автоматически»: как и любой сервер, применяется сразу, без вопроса."""
        adapters = self._ready_adapters()
        if adapters is None:
            return
        self._pending_choice = AUTO_CHOICE
        self.grid.flash(AUTO_CHOICE)
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
            proxy_mode=str(payload.get("proxy_mode") or ""),
            doh_enabled=payload.get("doh_enabled"),
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
            total = int(getattr(plan, "adapter_count", 0))
            failed = total - int(getattr(plan, "success_count", 0))
            if total and failed >= total:
                # Ни один адаптер не изменён — это отказ, а не «встал не везде».
                self._info(
                    "warning",
                    self._t("page.network.error.apply.title", "DNS не применён"),
                    str(getattr(plan, "error", "") or ""),
                )
            elif failed > 0:
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
            elif (
                payload.get("action") == "provider"
                and self._line_status(str(payload.get("name") or ""), payload.get("data") or {})[0] == STATUS_BLOCKED
            ):
                # О блокировке выбранного сервера — только когда DNS и правда поставлен.
                name = str(payload.get("name") or "")
                self._info(
                    "warning",
                    self._t("page.network.status.blocked.chosen.title", "{name} в России блокируется", name=name),
                    self._t(
                        "page.network.status.blocked.chosen.content",
                        "DNS поставлен, как вы выбрали. Если сайты перестанут открываться, выберите другой сервер. "
                        "Что отвечает на вашей линии, покажет BlockCheck → «DNS-серверы».",
                    ),
                )
            elif getattr(plan, "doh_closed", False):
                # Windows записана с откатом на обычный DNS: без этого окна человек
                # считал бы запросы зашифрованными.
                self._info(
                    "warning",
                    self._t("page.network.doh_closed.title", "DNS поставлен без шифрования"),
                    self._t(
                        "page.network.doh_closed.content",
                        "Шифрованный путь (DoH) до этого сервера из вашей сети закрыт, поэтому компьютер спрашивает "
                        "его обычными запросами — провайдер их видит и может подменить. Нужно шифрование — "
                        "выберите сервер из группы «Шифрованные».",
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
        # Кнопка не выключается: выключенная кнопка теряет фокус, он уходит на
        # сетку плиток, и страница прокручивается к выбранному серверу.
        if self._flushing:
            return
        self._flushing = True
        self._flush_lane.request()

    def _on_flush_done(self, plan) -> None:
        self._flushing = False
        if getattr(plan, "success", False):
            self._info("success", self._t("page.network.info.flush_done", "Кэш DNS очищен"))
            return
        self._info("warning", getattr(plan, "title", "") or self._t("page.network.error.title", "Ошибка"), getattr(plan, "content", ""))

    def _on_flush_failed(self, error: str) -> None:
        self._flushing = False
        self._info("warning", self._t("page.network.error.title", "Ошибка"), error)

    # ── замер скорости ──────────────────────────────────────

    def _measure_latency(self) -> None:
        if self._measuring:
            return
        # Замеряются все адреса сервера, а не только первый: запасной может
        # молчать или быть закрыт отдельно, и это видно в подсказке плитки.
        servers = list(
            dict.fromkeys(
                address
                for group in self._providers.values()
                for data in group.values()
                for address in self._all_addresses(data)
            )
        )
        if not servers:
            return
        self._measuring = True
        self._latency = {}
        self._intercepted = False
        self._render_notice()
        self.latency_summary.hide()
        self._retranslate()
        self._render()
        self._latency_lane.request(servers)

    def _on_latency_done(self, report) -> None:
        self._measuring = False
        self._latency = dict(getattr(report, "results", {}) or {})
        self._intercepted = bool(getattr(report, "intercepted", False))
        self._retranslate()
        self._render_notice()
        self._update_latency_summary()
        self._render()

    # ── кто отвечает на самом деле ──────────────────────────

    def _answering_key(self, plan) -> tuple[str, str] | None:
        """Что проверять: первый адрес на адаптерах и режим шифрованного DNS. None — проверять нечего."""
        if plan.kind not in ("auto", "provider", "custom") or not plan.ipv4:
            return None
        return plan.ipv4[0], self._local_proxy_mode

    def _current_answering(self, plan):
        key = self._answering_key(plan)
        found = self._answering.get(key) if key is not None else None
        return found[1] if found is not None else None

    def _request_answering(self) -> None:
        """Узнаёт в фоне, кто отвечает; итог хранится 10 минут, во время экскурсии не спрашиваем."""
        from ui.onboarding import is_onboarding_tour_active

        if self._closed or not self._loaded or is_onboarding_tour_active(self.window()):
            return
        key = self._answering_key(self._current_plan())
        if key is None or key == self._answering_pending:
            return
        found = self._answering.get(key)
        if found is not None and time.monotonic() - found[0] < ANSWERING_CACHE_S:
            return
        self._answering_pending = key
        self._answering_lane.request(key)

    def _on_answering_done(self, key, result) -> None:
        if self._answering_pending == key:
            self._answering_pending = None
        if result is None or self._closed:
            return
        self._answering[key] = (time.monotonic(), result)
        self._render()
        self._render_notice()

    def _with_answering(self, detail: str, plan) -> str:
        """Вторая строка под адресами: «Отвечает: Cloudflare (AS13335)». Не узнали — строки нет."""
        answering = self._current_answering(plan)
        owner = str(getattr(answering, "owner", "") or "")
        asn = str(getattr(answering, "asn", "") or "")
        if not owner and not asn:
            return detail
        who = f"{owner} (AS{asn})" if owner and asn else (owner or f"AS{asn}")
        line = self._t("page.network.now.answering", "Отвечает: {who}", who=who)
        if self._interception_shown(plan, answering):
            line += self._t("page.network.now.answering.foreign", " — не выбранный сервер")
        return f"{detail}\n{line}" if detail else line

    def _interception_shown(self, plan, answering) -> bool:
        """Перехват называется, только когда выбран конкретный сервер обычным путём.

        Канарейка идёт по обычному порту 53: шифрованный режим она не касается, а при
        «Автоматически» сервер и так провайдерский.
        """
        return (
            bool(getattr(answering, "intercepted", False))
            and plan.kind in ("provider", "custom")
            and not self._local_proxy_mode
        )

    def _render_notice(self) -> None:
        """Подпись-предупреждение под панелью: одна на два источника, чтобы они не стирали друг друга."""
        plan = self._current_plan() if self._loaded else dns_page_plans.CurrentDnsPlan(kind="none")
        answering = self._current_answering(plan)
        if self._interception_shown(plan, answering):
            name = plan.provider or self._t("page.network.now.custom.title", "Свой DNS")
            owner = str(getattr(answering, "owner", "") or "")
            text = (
                self._t(
                    "page.network.now.intercepted",
                    "Вы выбрали {name}, но отвечает не он, а {owner}: провайдер или роутер перехватывает обычные "
                    "DNS-запросы. Выберите сервер из группы «Шифрованные» — его ответы не подменить.",
                    name=name,
                    owner=owner,
                )
                if owner
                else self._t(
                    "page.network.now.intercepted.unknown",
                    "Вы выбрали {name}, но отвечает не он, а чужая сеть: провайдер или роутер перехватывает "
                    "обычные DNS-запросы. Выберите сервер из группы «Шифрованные» — его ответы не подменить.",
                    name=name,
                )
            )
        elif self._intercepted:
            text = self._t(
                "page.network.latency.intercepted",
                "Похоже, DNS-запросы перехватываются по пути (провайдером или роутером): ответил даже адрес, "
                "где DNS-сервера нет. Цифры показывают перехватчик, а не выбранные серверы.",
            )
        else:
            text = self._leftover_text()
        self.now_panel.set_notice(text)

    def _leftover_text(self) -> str:
        """Дороги, по которым запросы идут мимо выбранного сервера: IPv6-DNS роутера, неотмеченный адаптер."""
        if not self._loaded:
            return ""
        leftovers = dns_page_plans.find_dns_leftovers(self._adapters, self._selected_adapters())
        parts: list[str] = []
        for item in leftovers:
            addresses = ", ".join(item.addresses[:2])
            if item.kind == dns_page_plans.LEFTOVER_IPV6:
                parts.append(
                    self._t(
                        "page.network.now.leftover.ipv6",
                        "На адаптере «{adapter}» по IPv6 остался DNS роутера или провайдера ({addresses}): Windows "
                        "спрашивает и его, так что ответ может прийти не от выбранного сервера. У выбранного сервера "
                        "адресов IPv6 нет — выберите сервер с IPv6 или из группы «Шифрованные».",
                        adapter=item.adapter,
                        addresses=addresses,
                    )
                )
            else:
                parts.append(
                    self._t(
                        "page.network.now.leftover.adapter",
                        "Адаптер «{adapter}» подключён, но не отмечен: на нём свой DNS ({addresses}), и запросы "
                        "через него идут мимо выбранного сервера.",
                        adapter=item.adapter,
                        addresses=addresses,
                    )
                )
        return "\n".join(parts)

    def _update_latency_summary(self) -> None:
        best_name, best_ms = "", None
        for group in self._providers.values():
            for name, data in group.items():
                value = None if data.get("local_proxy") else self._latency.get(self._primary_address(data))
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

    def _set_custom_servers(self, servers, *, render: bool = True) -> None:
        servers = [custom_servers.copy_server(item) for item in servers]
        if servers == self._custom_servers:
            return
        self._custom_servers = servers
        self._providers = build_dns_providers_with_custom(DNS_PROVIDERS, servers)
        self._rebuild_filter_bar()
        if render:
            self._render()

    def _on_custom_servers_changed(self, result) -> None:
        if self._closed:
            return
        if not getattr(result, "success", False):
            self._info("warning", self._t("page.network.error.title", "Ошибка"), str(getattr(result, "error", "") or ""))
        self._set_custom_servers(getattr(result, "servers", ()) or ())

    def _custom_server(self, name: str) -> dict | None:
        """Запись своего сервера по названию его плитки."""
        custom_id = str((self._provider(name) or {}).get("custom_id") or "")
        return next((server for server in self._custom_servers if custom_id and server["id"] == custom_id), None)

    def _show_custom_server_menu(self, name: str, global_pos: QPoint) -> None:
        server = self._custom_server(name)
        if server is None:
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
            self._open_custom_server(custom_servers.copy_server(server))
        elif command == "copy":
            self._copy_custom_server(server)
        elif command in ("duplicate", "delete"):
            self._custom_lane.request({"action": command, "server_id": server["id"]})

    def _copy_custom_server(self, server: dict) -> None:
        text = custom_servers.clipboard_text(server)
        clipboard = QApplication.clipboard()
        if not text or clipboard is None:
            return
        clipboard.setText(text)
        self._info(
            "success",
            self._t("page.network.custom.copied.title", "DNS скопирован"),
            self._t("page.network.custom.copied.content", "Адреса DNS в буфере обмена."),
        )


__all__ = ["NetworkPage"]
