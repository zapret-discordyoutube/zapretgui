"""Файл настроек dnscrypt-proxy для выбранного режима шифрованного DNS.

Файл — единственное место, где записано, в каком режиме работает движок:
первая строка «# zapret-mode: <режим>». Программа читает её обратно
(read_mode), а не хранит режим отдельно, поэтому показанное на странице
всегда совпадает с тем, что на самом деле запущено.

Серверы перечислены в файле целиком, штампами из dns.local_proxy_catalog:
списки из сети движок не скачивает.
"""

from __future__ import annotations

from dns import local_proxy_catalog as catalog

MODE_MARKER = "# zapret-mode: "
LISTEN_IPV4 = "127.0.0.1"
LISTEN_IPV6 = "::1"


def _text(value: str) -> str:
    """Строка TOML. Путь Windows пишется как есть, в одинарных кавычках."""
    value = str(value)
    if "'" not in value and "\n" not in value:
        return f"'{value}'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _list(values) -> str:
    return "[" + ", ".join(_text(item) for item in values) + "]"


def build_config(mode: str, *, log_path: str, listen_ipv6: bool) -> str:
    """Текст dnscrypt-proxy.toml: слушать 127.0.0.1:53 (и [::1]:53), серверы режима."""
    servers = catalog.servers(mode)
    relays = catalog.relays(mode)
    listen = [f"{LISTEN_IPV4}:53"]
    if listen_ipv6:
        listen.append(f"[{LISTEN_IPV6}]:53")
    odoh = mode == catalog.MODE_ODOH
    lines = [
        f"{MODE_MARKER}{mode}",
        "# Файл пишет ZapretGUI при выборе шифрованного DNS; правки руками будут затёрты.",
        "",
        f"listen_addresses = {_list(listen)}",
        f"server_names = {_list(item.name for item in servers)}",
        "ipv4_servers = true",
        "ipv6_servers = false",
        f"dnscrypt_servers = {'false' if odoh else 'true'}",
        "doh_servers = false",
        f"odoh_servers = {'true' if odoh else 'false'}",
        # Свои запросы движок не отдаёт DNS системы: там теперь стоит он сам.
        "ignore_system_dns = true",
        f"bootstrap_resolvers = {_list(catalog.BOOTSTRAP_RESOLVERS)}",
        "netprobe_timeout = 60",
        "netprobe_address = '9.9.9.9:53'",
        "timeout = 5000",
        "keepalive = 30",
        "cache = true",
        "log_level = 2",
        f"log_file = {_text(log_path)}",
        "log_files_max_size = 2",
        "log_files_max_age = 7",
        "log_files_max_backups = 1",
    ]
    routes = catalog.routes(mode)
    if routes:
        lines += ["", "[anonymized_dns]", "routes = ["]
        lines += [f"  {{ server_name = {_text(server)}, via = {_list(via)} }}," for server, via in routes]
        # Сервер, который через посредника не отвечает, пропускается, а не идёт напрямую.
        lines += ["]", "skip_incompatible = true"]
    for item in (*servers, *relays):
        lines += ["", f"[static.{_text(item.name)}]", f"stamp = {_text(item.stamp)}"]
    return "\n".join(lines) + "\n"


def read_mode(config_text: str) -> str:
    """Режим из первой строки файла настроек или пустая строка."""
    first = str(config_text or "").lstrip("\ufeff").split("\n", 1)[0].strip()
    if not first.startswith(MODE_MARKER):
        return ""
    mode = first[len(MODE_MARKER):].strip()
    return mode if mode in catalog.MODES else ""


__all__ = ["LISTEN_IPV4", "LISTEN_IPV6", "MODE_MARKER", "build_config", "read_mode"]
