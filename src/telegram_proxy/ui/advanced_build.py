"""Build-helper страницы «Продвинутые настройки» Telegram Proxy."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtWidgets import QVBoxLayout
from qfluentwidgets import FluentIcon, PasswordLineEdit, SettingCardGroup, SpinBox

from ui.fluent_widgets import enable_setting_card_group_auto_height
from ui.widgets.win11_controls import Win11ComboRow, Win11ToggleRow
from telegram_proxy.ui.settings_build import (
    build_action_row,
    build_line_edit,
    build_row_button,
    build_settings_row,
    set_spinbox_value_accessibility,
)
from telegram_proxy.ui.text_plan import TELEGRAM_PROXY_SETTINGS_TEXT


@dataclass(slots=True)
class TelegramProxyAdvancedWidgets:
    upstream_card: object
    upstream_toggle: object
    upstream_preset_row: object
    upstream_address_row: object
    upstream_host_edit: object
    upstream_port_spin: object
    upstream_user_row: object
    upstream_user_edit: object
    upstream_pass_row: object
    upstream_pass_edit: object
    mtproxy_action_card: object
    mtproxy_action_btn: object
    upstream_mode_toggle: object
    upstream_udp_toggle: object
    cloudflare_card: object
    cloudflare_toggle: object
    cloudflare_domains_row: object
    cloudflare_domains_edit: object
    cloudflare_test_btn: object
    cloudflare_dns_btn: object
    cloudflare_worker_toggle: object
    cloudflare_worker_domains_row: object
    cloudflare_worker_domains_edit: object
    cloudflare_worker_test_btn: object
    cloudflare_worker_code_btn: object
    network_card: object
    dc_ip_row: object
    dc_ip_edit: object
    pool_size_row: object
    pool_size_spin: object
    buffer_kb_row: object
    buffer_kb_spin: object


def _build_spin_box(*, minimum: int, maximum: int, value: int, name: str, description: str) -> SpinBox:
    spin = SpinBox()
    spin.setRange(int(minimum), int(maximum))
    spin.setValue(int(value))
    spin.setFixedWidth(130)
    set_spinbox_value_accessibility(spin, name=name, description=description)
    return spin


def _build_upstream_group(*, content_parent, upstream_catalog, on_open_mtproxy) -> dict:
    text = TELEGRAM_PROXY_SETTINGS_TEXT
    card = SettingCardGroup(text.upstream_group_title, content_parent)

    upstream_toggle = Win11ToggleRow(
        "fa5s.shield-alt",
        text.upstream_toggle_title,
        text.upstream_toggle_description,
    )
    upstream_toggle.setChecked(False)
    card.addSettingCard(upstream_toggle)

    upstream_preset_row = Win11ComboRow(
        icon_name="fa5s.server",
        title=text.upstream_preset_title,
        description=text.upstream_preset_description,
        items=upstream_catalog.items(),
    )
    upstream_preset_row.combo.setFixedWidth(250)
    card.addSettingCard(upstream_preset_row)

    upstream_address_row = build_settings_row(
        "fa5s.globe",
        text.upstream_address_title,
        text.upstream_address_description,
    )
    upstream_host_edit = build_line_edit(
        placeholder="192.168.1.100 или proxy.example.com",
        accessible_name="Хост upstream-прокси Telegram Proxy",
        description="Введите IP-адрес или домен upstream-прокси для Telegram Proxy.",
        width=260,
    )
    upstream_address_row.add_control(upstream_host_edit)
    upstream_port_spin = _build_spin_box(
        minimum=1,
        maximum=65535,
        value=1080,
        name="Порт upstream-прокси Telegram Proxy",
        description="Введите порт upstream-прокси для Telegram Proxy.",
    )
    upstream_address_row.add_control(upstream_port_spin)
    card.addSettingCard(upstream_address_row)

    upstream_user_row = build_settings_row("fa5s.user", text.upstream_user_title, text.upstream_user_description)
    upstream_user_edit = build_line_edit(
        placeholder="username",
        accessible_name="Логин upstream-прокси Telegram Proxy",
        description="Введите логин upstream-прокси, если он нужен.",
        width=260,
    )
    upstream_user_row.add_control(upstream_user_edit)
    card.addSettingCard(upstream_user_row)

    upstream_pass_row = build_settings_row(
        "fa5s.lock",
        text.upstream_password_title,
        text.upstream_password_description,
    )
    upstream_pass_edit = build_line_edit(
        placeholder="password",
        accessible_name="Пароль upstream-прокси Telegram Proxy",
        description="Введите пароль upstream-прокси, если он нужен.",
        width=260,
        line_edit_cls=PasswordLineEdit,
    )
    upstream_pass_row.add_control(upstream_pass_edit)
    card.addSettingCard(upstream_pass_row)

    mtproxy_action_card, mtproxy_action_btn = build_action_row(
        icon_name="fa5s.paper-plane",
        title=text.upstream_mtproxy_title,
        description=text.upstream_mtproxy_description,
        button_text="Открыть",
        button_icon=FluentIcon.SEND,
        accessible_name="Открыть MTProxy в Telegram",
        on_click=on_open_mtproxy,
    )
    card.addSettingCard(mtproxy_action_card)

    upstream_mode_toggle = Win11ToggleRow(
        "fa5s.route",
        text.upstream_mode_title,
        text.upstream_mode_description,
    )
    upstream_mode_toggle.setChecked(True)
    card.addSettingCard(upstream_mode_toggle)

    upstream_udp_toggle = Win11ToggleRow(
        "fa5s.phone-alt",
        text.upstream_udp_title,
        text.upstream_udp_description,
    )
    upstream_udp_toggle.setChecked(False)
    card.addSettingCard(upstream_udp_toggle)

    # Строки сервера показываются после загрузки настроек.
    for widget in (
        upstream_preset_row,
        upstream_address_row,
        upstream_user_row,
        upstream_pass_row,
        mtproxy_action_card,
        upstream_mode_toggle,
        upstream_udp_toggle,
    ):
        widget.setVisible(False)
    enable_setting_card_group_auto_height(card)

    return {
        "upstream_card": card,
        "upstream_toggle": upstream_toggle,
        "upstream_preset_row": upstream_preset_row,
        "upstream_address_row": upstream_address_row,
        "upstream_host_edit": upstream_host_edit,
        "upstream_port_spin": upstream_port_spin,
        "upstream_user_row": upstream_user_row,
        "upstream_user_edit": upstream_user_edit,
        "upstream_pass_row": upstream_pass_row,
        "upstream_pass_edit": upstream_pass_edit,
        "mtproxy_action_card": mtproxy_action_card,
        "mtproxy_action_btn": mtproxy_action_btn,
        "upstream_mode_toggle": upstream_mode_toggle,
        "upstream_udp_toggle": upstream_udp_toggle,
    }


def _build_cloudflare_group(
    *,
    content_parent,
    on_test_cloudflare,
    on_copy_cloudflare_dns,
    on_test_cloudflare_worker,
    on_copy_cloudflare_worker_code,
) -> dict:
    text = TELEGRAM_PROXY_SETTINGS_TEXT
    card = SettingCardGroup(text.cloudflare_group_title, content_parent)

    cloudflare_toggle = Win11ToggleRow(
        "fa5s.cloud",
        text.cloudflare_toggle_title,
        text.cloudflare_toggle_description,
    )
    cloudflare_toggle.setChecked(False)
    card.addSettingCard(cloudflare_toggle)

    cloudflare_domains_row = build_settings_row(
        "fa5s.list",
        text.cloudflare_domains_title,
        text.cloudflare_domains_description,
    )
    cloudflare_domains_edit = build_line_edit(
        placeholder="example.com, backup.example.com",
        accessible_name="Домены Cloudflare для Telegram Proxy",
        description="Cloudflare-домены для запасного WSS-пути. Оставьте пустым для авто-списка.",
        width=240,
    )
    cloudflare_domains_row.add_control(cloudflare_domains_edit)
    cloudflare_test_btn = build_row_button(
        "Проверить",
        FluentIcon.SEARCH,
        accessible_name="Проверить Cloudflare-домен Telegram Proxy",
        description="Проверить, отвечает ли ваш Cloudflare-домен для Telegram.",
        on_click=on_test_cloudflare,
    )
    cloudflare_test_btn.setFixedWidth(128)
    cloudflare_domains_row.add_control(cloudflare_test_btn)
    cloudflare_dns_btn = build_row_button(
        "DNS",
        FluentIcon.COPY,
        accessible_name="Скопировать DNS-записи Cloudflare Telegram Proxy",
        description="Скопировать DNS-записи kws1, kws2, kws3, kws4, kws5 и kws203.",
        on_click=on_copy_cloudflare_dns,
    )
    cloudflare_dns_btn.setFixedWidth(96)
    cloudflare_domains_row.add_control(cloudflare_dns_btn)
    card.addSettingCard(cloudflare_domains_row)

    cloudflare_worker_toggle = Win11ToggleRow(
        "fa5s.code",
        text.cloudflare_worker_toggle_title,
        text.cloudflare_worker_toggle_description,
    )
    cloudflare_worker_toggle.setChecked(False)
    card.addSettingCard(cloudflare_worker_toggle)

    cloudflare_worker_domains_row = build_settings_row(
        "fa5s.link",
        text.cloudflare_worker_domains_title,
        text.cloudflare_worker_domains_description,
    )
    cloudflare_worker_domains_edit = build_line_edit(
        placeholder="worker-name.workers.dev",
        accessible_name="Домены Cloudflare Worker для Telegram Proxy",
        description="Домены Cloudflare Worker для отдельного запасного пути Telegram Proxy.",
        width=240,
    )
    cloudflare_worker_domains_row.add_control(cloudflare_worker_domains_edit)
    cloudflare_worker_test_btn = build_row_button(
        "Проверить",
        FluentIcon.SEARCH,
        accessible_name="Проверить Cloudflare Worker Telegram Proxy",
        description="Проверить, отвечает ли ваш Cloudflare Worker.",
        on_click=on_test_cloudflare_worker,
    )
    cloudflare_worker_test_btn.setFixedWidth(128)
    cloudflare_worker_domains_row.add_control(cloudflare_worker_test_btn)
    cloudflare_worker_code_btn = build_row_button(
        "Код Worker",
        FluentIcon.COPY,
        accessible_name="Скопировать код Cloudflare Worker",
        description="Скопировать готовый код для Cloudflare Worker.",
        on_click=on_copy_cloudflare_worker_code,
    )
    cloudflare_worker_code_btn.setFixedWidth(128)
    cloudflare_worker_domains_row.add_control(cloudflare_worker_code_btn)
    card.addSettingCard(cloudflare_worker_domains_row)

    cloudflare_domains_row.setVisible(False)
    cloudflare_worker_domains_row.setVisible(False)
    enable_setting_card_group_auto_height(card)

    return {
        "cloudflare_card": card,
        "cloudflare_toggle": cloudflare_toggle,
        "cloudflare_domains_row": cloudflare_domains_row,
        "cloudflare_domains_edit": cloudflare_domains_edit,
        "cloudflare_test_btn": cloudflare_test_btn,
        "cloudflare_dns_btn": cloudflare_dns_btn,
        "cloudflare_worker_toggle": cloudflare_worker_toggle,
        "cloudflare_worker_domains_row": cloudflare_worker_domains_row,
        "cloudflare_worker_domains_edit": cloudflare_worker_domains_edit,
        "cloudflare_worker_test_btn": cloudflare_worker_test_btn,
        "cloudflare_worker_code_btn": cloudflare_worker_code_btn,
    }


def _build_network_group(*, content_parent) -> dict:
    text = TELEGRAM_PROXY_SETTINGS_TEXT
    card = SettingCardGroup(text.network_group_title, content_parent)

    dc_ip_row = build_settings_row("fa5s.map-marker-alt", text.dc_ip_title, text.dc_ip_description)
    dc_ip_edit = build_line_edit(
        placeholder="4:149.154.167.220, 5:91.108.56.100",
        accessible_name="Ручные адреса Telegram DC",
        description="Введите номер дата-центра и IP-адрес в формате номер:IP, через запятую.",
        width=320,
    )
    dc_ip_row.add_control(dc_ip_edit)
    card.addSettingCard(dc_ip_row)

    pool_size_row = build_settings_row("fa5s.layer-group", text.pool_size_title, text.pool_size_description)
    pool_size_spin = _build_spin_box(
        minimum=0,
        maximum=32,
        value=4,
        name="Пул WSS Telegram Proxy",
        description="Сколько запасных WSS-соединений держать в пуле. 4 — обычное значение.",
    )
    pool_size_row.add_control(pool_size_spin)
    card.addSettingCard(pool_size_row)

    buffer_kb_row = build_settings_row("fa5s.memory", text.buffer_kb_title, text.buffer_kb_description)
    buffer_kb_spin = _build_spin_box(
        minimum=4,
        maximum=4096,
        value=256,
        name="Размер буфера Telegram Proxy",
        description="Размер сетевого буфера. 256 КБ обычно достаточно.",
    )
    buffer_kb_row.add_control(buffer_kb_spin)
    card.addSettingCard(buffer_kb_row)
    enable_setting_card_group_auto_height(card)

    return {
        "network_card": card,
        "dc_ip_row": dc_ip_row,
        "dc_ip_edit": dc_ip_edit,
        "pool_size_row": pool_size_row,
        "pool_size_spin": pool_size_spin,
        "buffer_kb_row": buffer_kb_row,
        "buffer_kb_spin": buffer_kb_spin,
    }


def build_telegram_proxy_advanced_panel(
    layout: QVBoxLayout,
    *,
    content_parent,
    upstream_catalog,
    on_open_mtproxy,
    on_test_cloudflare,
    on_copy_cloudflare_dns,
    on_test_cloudflare_worker,
    on_copy_cloudflare_worker_code,
) -> TelegramProxyAdvancedWidgets:
    upstream = _build_upstream_group(
        content_parent=content_parent,
        upstream_catalog=upstream_catalog,
        on_open_mtproxy=on_open_mtproxy,
    )
    layout.addWidget(upstream["upstream_card"])
    cloudflare = _build_cloudflare_group(
        content_parent=content_parent,
        on_test_cloudflare=on_test_cloudflare,
        on_copy_cloudflare_dns=on_copy_cloudflare_dns,
        on_test_cloudflare_worker=on_test_cloudflare_worker,
        on_copy_cloudflare_worker_code=on_copy_cloudflare_worker_code,
    )
    layout.addWidget(cloudflare["cloudflare_card"])
    network = _build_network_group(content_parent=content_parent)
    layout.addWidget(network["network_card"])
    layout.addStretch()
    return TelegramProxyAdvancedWidgets(**upstream, **cloudflare, **network)
