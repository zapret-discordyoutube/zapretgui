"""Короткие тексты страниц Telegram Proxy."""

from __future__ import annotations

from dataclasses import astuple, dataclass


@dataclass(frozen=True, slots=True)
class TelegramProxySettingsText:
    page_subtitle: str
    setup_title: str
    setup_description: str
    settings_title: str
    host_port_title: str
    host_port_description: str
    proxy_mode_title: str
    proxy_mode_description: str
    mtproxy_secret_title: str
    mtproxy_secret_description: str
    fake_tls_domain_title: str
    fake_tls_domain_description: str
    proxy_protocol_title: str
    proxy_protocol_description: str
    auto_setup_title: str
    auto_setup_description: str
    advanced_nav_title: str
    advanced_nav_description: str
    upstream_group_title: str
    upstream_toggle_title: str
    upstream_toggle_description: str
    upstream_preset_title: str
    upstream_preset_description: str
    upstream_catalog_missing: str
    upstream_address_title: str
    upstream_address_description: str
    upstream_user_title: str
    upstream_user_description: str
    upstream_password_title: str
    upstream_password_description: str
    upstream_mtproxy_title: str
    upstream_mtproxy_description: str
    upstream_mode_title: str
    upstream_mode_description: str
    upstream_udp_title: str
    upstream_udp_description: str
    cloudflare_group_title: str
    cloudflare_toggle_title: str
    cloudflare_toggle_description: str
    cloudflare_domains_title: str
    cloudflare_domains_description: str
    cloudflare_worker_toggle_title: str
    cloudflare_worker_toggle_description: str
    cloudflare_worker_domains_title: str
    cloudflare_worker_domains_description: str
    network_group_title: str
    dc_ip_title: str
    dc_ip_description: str
    pool_size_title: str
    pool_size_description: str
    buffer_kb_title: str
    buffer_kb_description: str
    diag_description: str

    def __iter__(self):
        return iter(astuple(self))


TELEGRAM_PROXY_SETTINGS_TEXT = TelegramProxySettingsText(
    page_subtitle="Локальный прокси для Telegram. Используйте его, если Telegram подключается нестабильно.",
    setup_title="Подключить Telegram",
    setup_description=(
        "Откройте ссылку. Telegram сам предложит добавить прокси. "
        "Если Telegram не открылся, скопируйте ссылку и отправьте её себе в чат. "
        "Если ничего не помогает — скачайте Zastogram."
    ),
    settings_title="Основные настройки",
    host_port_title="Адрес и порт",
    host_port_description="127.0.0.1 — только этот компьютер, 0.0.0.0 — вся сеть",
    proxy_mode_title="Режим прокси",
    proxy_mode_description="SOCKS5 — основной режим, MTProxy — для secret и Fake TLS",
    mtproxy_secret_title="Secret MTProxy",
    mtproxy_secret_description="Ключ подключения для Telegram",
    fake_tls_domain_title="Домен Fake TLS",
    fake_tls_domain_description="Только для своего домена с Nginx",
    proxy_protocol_title="Proxy protocol для Nginx",
    proxy_protocol_description="Включайте только если Nginx передаёт MTProxy через proxy_protocol.",
    auto_setup_title="Авто-настройка Telegram",
    auto_setup_description="Открыть ссылку в Telegram при первом запуске прокси",
    advanced_nav_title="Продвинутые настройки",
    advanced_nav_description="Внешний прокси, Cloudflare, DC→IP, пул и буфер",
    upstream_group_title="Внешний прокси",
    upstream_toggle_title="Использовать внешний прокси",
    upstream_toggle_description="Резервный SOCKS5, если часть серверов Telegram не отвечает.",
    upstream_preset_title="Сервер",
    upstream_preset_description="Выберите сервер из списка или переключитесь на ручной ввод",
    upstream_catalog_missing="В этой сборке список готовых серверов не загружен. Доступен только ручной ввод.",
    upstream_address_title="Адрес",
    upstream_address_description="IP-адрес или домен SOCKS5-сервера и его порт",
    upstream_user_title="Логин",
    upstream_user_description="Если сервер просит вход",
    upstream_password_title="Пароль",
    upstream_password_description="Если сервер просит вход",
    upstream_mtproxy_title="MTProxy-сервер",
    upstream_mtproxy_description="Этот сервер добавляется в Telegram напрямую по ссылке",
    upstream_mode_title="Весь TCP через SOCKS5",
    upstream_mode_description="Если выключено — через SOCKS5 идут только проблемные серверы Telegram.",
    upstream_udp_title="Звонки через SOCKS5 UDP",
    upstream_udp_description="Экспериментально: сервер должен поддерживать UDP",
    cloudflare_group_title="Cloudflare",
    cloudflare_toggle_title="Запасные Cloudflare-домены",
    cloudflare_toggle_description="Пробовать Cloudflare-домены, если прямой путь не работает.",
    cloudflare_domains_title="Домены",
    cloudflare_domains_description="Пусто — встроенный список",
    cloudflare_worker_toggle_title="Запасной Cloudflare Worker",
    cloudflare_worker_toggle_description="Пробовать свой Worker как отдельный запасной путь.",
    cloudflare_worker_domains_title="Домены Worker",
    cloudflare_worker_domains_description="Например name.workers.dev",
    network_group_title="Сеть",
    dc_ip_title="DC → IP",
    dc_ip_description="Свои IP дата-центров, например 4:149.154.167.220",
    pool_size_title="Пул WSS",
    pool_size_description="Сколько запасных WSS-соединений держать наготове. Обычно 4.",
    buffer_kb_title="Буфер, КБ",
    buffer_kb_description="Размер сетевого буфера. Обычно хватает 256 КБ.",
    diag_description=(
        "Проверяет соединение с серверами Telegram, локальный SOCKS5 прокси "
        "и возможные проблемы с маршрутом."
    ),
)
