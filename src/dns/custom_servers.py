"""Свои DNS-серверы пользователя: разбор введённого, список и плитки.

Сервер хранится в settings.sqlite3 (dns → custom_servers) записью
{"id", "name", "ipv4", "ipv6", "doh"}. "doh" — адрес шифрованного DNS поверх
HTTPS (https://имя/dns-query) или пустая строка.

Здесь нет ни окон, ни сети, ни чтения настроек: только правила. Страница
«Свой DNS» отдаёт сюда текст из полей (read_form) и получает либо готовую
запись, либо ошибку с названием поля. Сохранение и поиск адресов делают
dns.commands в фоновом потоке.

Сервер можно задать тремя способами:
- только IP-адреса — обычный DNS;
- IP-адреса и адрес DoH — Windows 11 шифрует запросы к этим адресам;
- только адрес DoH — IP-адреса программа находит сама (dns.doh_lookup).
"""

from __future__ import annotations

import copy
import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import uuid4

CUSTOM_DNS_CATEGORY = "Свои DNS"
DEFAULT_DOH_PATH = "/dns-query"

FIELD_NAME = "name"
FIELD_DOH = "doh"
FIELD_ADDRESSES = "addresses"

_HOST_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_ADDRESS_SEPARATORS = re.compile(r"[\s,;]+")


@dataclass(frozen=True, slots=True)
class DohTemplate:
    """Разобранный адрес DoH. url — он же в едином виде, как хранится в настройках."""

    url: str
    host: str
    port: int = 443
    path: str = DEFAULT_DOH_PATH

    @property
    def host_is_address(self) -> bool:
        return _ip(self.host) is not None


@dataclass(frozen=True, slots=True)
class CustomServerForm:
    """Итог разбора полей: запись сервера или ошибка и поле, где она."""

    server: dict | None = None
    error: str = ""
    field: str = ""

    @property
    def needs_lookup(self) -> bool:
        """Задан только адрес DoH: IP-адреса ещё предстоит найти."""
        server = self.server or {}
        return bool(server.get("doh")) and not server.get("ipv4") and not server.get("ipv6")


def _ip(value: str):
    try:
        return ipaddress.ip_address(str(value or "").strip().strip("[]"))
    except ValueError:
        return None


def parse_doh_template(text: str) -> DohTemplate | None:
    """Адрес DoH из того, что вставил пользователь, или None.

    Понимает адрес без «https://» и хвост «{?dns}» из описаний серверов.
    Путь по умолчанию — /dns-query. Только https: открытый http для DoH не
    годится.
    """
    raw = str(text or "").strip()
    if not raw or any(char.isspace() for char in raw):
        return None
    raw = raw.split("{", 1)[0]
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parts = urlsplit(raw)
        port = parts.port or 443
    except ValueError:
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme.lower() != "https" or not host or parts.username or parts.query or parts.fragment:
        return None
    address = _ip(host)
    if address is None:
        labels = host.split(".")
        if len(labels) < 2 or len(host) > 253 or not all(_HOST_LABEL.match(label) for label in labels):
            return None
        # Имя из одних цифр («149.112») — недописанный IP-адрес, а не сервер.
        if labels[-1].isdigit():
            return None
        shown_host = host
    else:
        host = str(address)
        shown_host = f"[{host}]" if address.version == 6 else host
    path = parts.path or DEFAULT_DOH_PATH
    shown_port = "" if port == 443 else f":{port}"
    return DohTemplate(url=f"https://{shown_host}{shown_port}{path}", host=host, port=port, path=path)


def split_addresses(text: str) -> tuple[list[str], list[str], list[str]]:
    """IP-адреса из одной строки: (IPv4, IPv6, не адреса).

    Адреса разделяются пробелом, запятой или точкой с запятой; порядок
    сохраняется (первый — основной), повторы убираются.
    """
    ipv4: list[str] = []
    ipv6: list[str] = []
    bad: list[str] = []
    for part in _ADDRESS_SEPARATORS.split(str(text or "").strip()):
        if not part:
            continue
        address = _ip(part)
        if address is None:
            bad.append(part)
            continue
        bucket = ipv4 if address.version == 4 else ipv6
        if str(address) not in bucket:
            bucket.append(str(address))
    return ipv4, ipv6, bad


def split_pasted(text: str) -> tuple[str, str] | None:
    """Текст «адрес DoH и IP-адреса вместе» → (адрес DoH, IP-адреса) или None.

    Так выглядит то, что кладёт в буфер «Копировать DNS» (clipboard_text), и
    строка из описания сервера на его сайте. Вставленное в одно поле
    раскладывается по двум, только если разбор однозначен: ровно один адрес
    DoH и хотя бы один IP-адрес.
    """
    parts = [part for part in _ADDRESS_SEPARATORS.split(str(text or "").strip()) if part]
    addresses = [part for part in parts if _ip(part) is not None]
    others = [part for part in parts if _ip(part) is None]
    if not addresses or len(others) != 1 or parse_doh_template(others[0]) is None:
        return None
    return others[0], " ".join(addresses)


def new_server_id() -> str:
    return f"custom-{uuid4().hex[:12]}"


def read_form(*, server_id: str = "", name: str = "", doh: str = "", addresses: str = "") -> CustomServerForm:
    """Запись сервера из текста полей страницы «Свой DNS».

    Название можно не писать: им станет имя сервера из адреса DoH или первый
    IP-адрес. Обязательно одно из двух — адрес DoH или хотя бы один IP.
    """
    doh_text = str(doh or "").strip()
    template = parse_doh_template(doh_text) if doh_text else None
    if doh_text and template is None:
        return CustomServerForm(
            error="Адрес DoH должен выглядеть так: https://dns.example.com/dns-query",
            field=FIELD_DOH,
        )
    ipv4, ipv6, bad = split_addresses(addresses)
    if bad:
        return CustomServerForm(
            error=f"«{bad[0]}» — не IP-адрес. Пишите адреса через пробел, например: 9.9.9.9 149.112.112.112",
            field=FIELD_ADDRESSES,
        )
    if template is None and not ipv4 and not ipv6:
        return CustomServerForm(
            error="Вставьте адрес DoH или впишите хотя бы один IP-адрес сервера.",
            field=FIELD_DOH,
        )
    clean_name = " ".join(str(name or "").split())
    if not clean_name:
        clean_name = template.host if template is not None else (ipv4 or ipv6)[0]
    return CustomServerForm(
        server={
            "id": str(server_id or "").strip() or new_server_id(),
            "name": clean_name,
            "ipv4": ipv4,
            "ipv6": ipv6,
            "doh": template.url if template is not None else "",
        }
    )


def copy_server(server: dict | None) -> dict:
    data = dict(server or {})
    return {
        "id": str(data.get("id") or ""),
        "name": str(data.get("name") or ""),
        "ipv4": [str(item) for item in data.get("ipv4", []) or []],
        "ipv6": [str(item) for item in data.get("ipv6", []) or []],
        "doh": str(data.get("doh") or ""),
    }


def addresses_text(server: dict | None) -> str:
    """IP-адреса сервера одной строкой — так, как их пишут в поле страницы."""
    data = copy_server(server)
    return " ".join([*data["ipv4"], *data["ipv6"]])


def clipboard_text(server: dict | None) -> str:
    """Что уходит в буфер обмена: адрес DoH (если есть) и IP-адреса."""
    data = copy_server(server)
    return ", ".join(item for item in (data["doh"], *data["ipv4"], *data["ipv6"]) if item)


# ── список серверов ───────────────────────────────────────────────────────


def _name_key(name: str) -> str:
    return str(name or "").strip().casefold()


def upsert_server(servers: list[dict], server: dict, *, reserved_names=()) -> tuple[list[dict], str]:
    """Список с добавленным или заменённым (по id) сервером и текст ошибки.

    Название — ключ плитки, поэтому оно не может совпадать ни с другим своим
    сервером, ни с сервером из списка программы (reserved_names).
    """
    new = copy_server(server)
    taken = {_name_key(name) for name in reserved_names}
    taken.update(_name_key(item.get("name")) for item in servers if str(item.get("id") or "") != new["id"])
    if _name_key(new["name"]) in taken:
        return list(servers), f"Сервер с названием «{new['name']}» уже есть. Впишите другое название."
    result = [copy_server(item) for item in servers]
    for index, item in enumerate(result):
        if item["id"] == new["id"]:
            result[index] = new
            return result, ""
    return [*result, new], ""


def remove_server(servers: list[dict], server_id: str) -> list[dict]:
    return [copy_server(item) for item in servers if str(item.get("id") or "") != str(server_id or "")]


def unique_copy_name(base_name: str, existing_names) -> str:
    base = str(base_name or "").strip() or "Свой DNS"
    existing = {_name_key(name) for name in existing_names}
    first = f"{base} копия"
    if _name_key(first) not in existing:
        return first
    index = 2
    while _name_key(f"{first} {index}") in existing:
        index += 1
    return f"{first} {index}"


def duplicate_server(servers: list[dict], server_id: str, *, reserved_names=()) -> list[dict]:
    """Список с копией сервера в конце; неизвестный id список не меняет."""
    result = [copy_server(item) for item in servers]
    source = next((item for item in result if item["id"] == str(server_id or "")), None)
    if source is None:
        return result
    clone = copy_server(source)
    clone["id"] = new_server_id()
    clone["name"] = unique_copy_name(source["name"], [*reserved_names, *(item["name"] for item in result)])
    return [*result, clone]


# ── плитки и запись в Windows ─────────────────────────────────────────────


def build_dns_providers_with_custom(base_providers: dict, custom_servers) -> dict:
    """Серверы программы и группа «Свои DNS» в том же виде, что dns.dns_providers."""
    providers = copy.deepcopy(base_providers)
    custom_group: dict[str, dict] = {}
    for server in custom_servers or ():
        data = copy_server(server)
        ipv4 = [item.strip() for item in data["ipv4"] if item.strip()]
        ipv6 = [item.strip() for item in data["ipv6"] if item.strip()]
        name = data["name"].strip()
        if not name or (not ipv4 and not ipv6):
            continue
        entry = {
            "ipv4": ipv4,
            "ipv6": ipv6,
            "desc": "Пользовательский",
            "icon": "fa5s.edit",
            "color": "#22c55e",
            "custom_id": data["id"],
        }
        if data["doh"]:
            entry["doh"] = data["doh"]
        custom_group[name] = entry
    if custom_group:
        providers[CUSTOM_DNS_CATEGORY] = custom_group
    return providers


def custom_doh_templates(custom_servers) -> dict[str, str]:
    """{адрес сервера: шаблон DoH} для своих серверов — как dns_providers.doh_templates()."""
    templates: dict[str, str] = {}
    for server in custom_servers or ():
        data = copy_server(server)
        if not data["doh"]:
            continue
        for address in (*data["ipv4"], *data["ipv6"]):
            templates.setdefault(address.strip(), data["doh"])
    return templates


__all__ = [
    "CUSTOM_DNS_CATEGORY",
    "DEFAULT_DOH_PATH",
    "FIELD_ADDRESSES",
    "FIELD_DOH",
    "FIELD_NAME",
    "CustomServerForm",
    "DohTemplate",
    "addresses_text",
    "build_dns_providers_with_custom",
    "clipboard_text",
    "copy_server",
    "custom_doh_templates",
    "duplicate_server",
    "new_server_id",
    "parse_doh_template",
    "read_form",
    "remove_server",
    "split_addresses",
    "split_pasted",
    "unique_copy_name",
    "upsert_server",
]
