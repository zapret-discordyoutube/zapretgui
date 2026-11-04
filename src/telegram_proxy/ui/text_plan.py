"""Короткие тексты страниц Telegram Proxy."""

from __future__ import annotations

from dataclasses import astuple, dataclass


@dataclass(frozen=True, slots=True)
class TelegramProxySettingsText:
    page_subtitle: str
    setup_title: str
    setup_description: str
    zastogram_title: str
    zastogram_description: str
    settings_title: str
    host_port_title: str
    host_port_description: str
    lan_title: str
    lan_description: str
    device_title: str
    device_description_ready: str
    device_description_no_network: str
    device_description_custom: str
    device_button: str
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
    hosts_group_title: str
    hosts_title: str
    hosts_hint: str
    hosts_state_checking: str
    hosts_state_none: str
    hosts_state_all: str
    hosts_state_partial: str
    hosts_state_error: str
    hosts_add_button: str
    hosts_remove_button: str
    hosts_add_accessible_name: str
    hosts_remove_accessible_name: str
    hosts_error_title: str
    hosts_done_title: str
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
    page_subtitle=(
        "Небольшой прокси на этом компьютере: Telegram подключается к нему, а он доводит соединение до серверов "
        "Telegram обходным путём. Включайте, если Telegram долго «подключается» или не грузит сообщения и медиа."
    ),
    setup_title="Подключить Telegram",
    setup_description=(
        "Откройте ссылку. Telegram сам предложит добавить прокси. "
        "Если Telegram не открылся, скопируйте ссылку и отправьте её себе в чат."
    ),
    zastogram_title="Не помогает прокси?",
    zastogram_description=(
        "Скачайте ZaStoGram — Telegram Desktop для сетей с блокировками: "
        "он сам обходит DPI, без прокси"
    ),
    settings_title="Основные настройки",
    host_port_title="Адрес и порт",
    host_port_description="127.0.0.1 — только этот компьютер, 0.0.0.0 — вся сеть",
    lan_title="Доступ с других устройств",
    lan_description=(
        "Телефон или другой компьютер из вашей домашней сети сможет пользоваться этим прокси. "
        "Выключите, чтобы прокси слышал только этот компьютер."
    ),
    device_title="Подключить телефон",
    device_description_ready=(
        "Адрес в вашей сети: {address}:{port}. Телефон должен быть в той же сети Wi-Fi. "
        "Скопируйте ссылку, отправьте её себе в Telegram на телефоне и нажмите на неё."
    ),
    device_description_no_network=(
        "Не нашли адрес этого компьютера в сети. Подключитесь к Wi-Fi или кабелю."
    ),
    device_description_custom="Адрес в вашей сети: {address}:{port}.",
    device_button="Копировать",
    proxy_mode_title="Режим прокси",
    proxy_mode_description=(
        "SOCKS5 — обычный прокси, выбирайте его. MTProxy — для опытных: соединение с ключом (secret), "
        "которое можно замаскировать под обычный сайт (Fake TLS)."
    ),
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
    hosts_group_title="Сайты Telegram в hosts",
    hosts_title="Записи Telegram в hosts",
    hosts_hint="Нужны, чтобы web.telegram.org и t.me открывались в браузере. Самому прокси не нужны.",
    hosts_state_checking="Проверяем файл hosts",
    hosts_state_none="Не прописаны",
    hosts_state_all="Прописаны все {total}",
    hosts_state_partial="Прописаны частично: {present} из {total}",
    hosts_state_error="Не удалось прочитать файл hosts",
    hosts_add_button="Прописать",
    hosts_remove_button="Убрать",
    hosts_add_accessible_name="Прописать записи Telegram в hosts",
    hosts_remove_accessible_name="Убрать записи Telegram из hosts",
    hosts_error_title="Не удалось изменить hosts",
    hosts_done_title="Файл hosts обновлён",
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
    upstream_mode_description="Если выключено — сначала WSS, а SOCKS5 подключается, только когда WSS не ответил.",
    upstream_udp_title="Звонки через SOCKS5 UDP",
    upstream_udp_description="Экспериментально: сервер должен поддерживать UDP",
    cloudflare_group_title="Cloudflare",
    cloudflare_toggle_title="Свои Cloudflare-домены",
    cloudflare_toggle_description="Встроенные фронты Cloudflare работают всегда. Свои домены пробуются раньше них.",
    cloudflare_domains_title="Домены",
    cloudflare_domains_description="Пусто — только встроенные фронты",
    cloudflare_worker_toggle_title="Свой Cloudflare Worker",
    cloudflare_worker_toggle_description="Пробовать свой Worker раньше встроенных фронтов и туннеля.",
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
