"""Проверка значений опций winws2 по правилам zapret2/nfq2.

Каждая функция возвращает текст ошибки по-русски или пустую строку, если
значение верное. Правила взяты из разбора опций в ``nfqws.c``, ``filter.c``
и ``protocol.c``; для ``--payload`` и диапазонов используются те же функции,
что и проверка перед запуском (``profile.winws2_transport``), чтобы редактор
и запуск не расходились.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re

from profile.winws2_transport import (
    WINWS2_PAYLOAD_NAMES,
    parse_out_range_expression,
    validate_winws2_payload_filter,
)

PAYLOAD_NAMES: tuple[str, ...] = WINWS2_PAYLOAD_NAMES
_PAYLOAD_NAME_SET = frozenset(PAYLOAD_NAMES)
L7_PROTO_NAMES: tuple[str, ...] = (
    "all", "unknown", "known", "http", "tls", "dtls", "quic", "wireguard",
    "dht", "discord", "stun", "xmpp", "dns", "mtproto", "bt", "utp_bt",
)
POS_MARKER_NAMES: tuple[str, ...] = (
    "abs", "host", "endhost", "sld", "midsld", "endsld", "method", "extlen", "sniext",
)
TLS_MOD_NAMES: tuple[str, ...] = ("rnd", "rndsni", "dupsid", "padencap", "sni", "none")

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_UINT_RE = re.compile(r"^\d+$")
_INT_RE = re.compile(r"^[+-]?\d+$")
_HEX_RE = re.compile(r"^0x[0-9A-Fa-f]*$")
_PORT_RANGE_RE = re.compile(r"^(\d+)(?:-(\d+))?$")
_ICMP_RE = re.compile(r"^(\d+)(?::(\d+))?$")
_POS_MARKER_RE = re.compile(r"^([A-Za-z]+)([+-]\d+)?$")
_CTRACK_RE = re.compile(r"^\d+:\d+:\d+(?::\d+)?$")
_WF_IFACE_RE = re.compile(r"^\d+(?:\.\d+)?$")
_MAX_HEX_BLOB_BYTES = 16 * 1024


def _split_list(value: str) -> list[str]:
    return str(value or "").split(",")


def check_ports(value: str) -> str:
    items = _split_list(value)
    for item in items:
        if item == "*":
            continue
        body = item[1:] if item.startswith("~") else item
        match = _PORT_RANGE_RE.match(body)
        if not match:
            return f"«{item}» — не порт. Нужно: 443, 80-90, ~443 (кроме) или *."
        low = int(match.group(1))
        high = int(match.group(2)) if match.group(2) is not None else low
        if low > 65535 or high > 65535:
            return f"Порт {item}: больше 65535."
        if low > high:
            return f"Диапазон {item}: начало больше конца."
    return ""


def check_icmp(value: str) -> str:
    for item in _split_list(value):
        if item in {"*", "-"}:
            continue
        match = _ICMP_RE.match(item)
        if not match or any(int(g) > 255 for g in match.groups() if g is not None):
            return f"«{item}» — нужно тип[:код] от 0 до 255, * или -."
    return ""


def check_ipp(value: str) -> str:
    for item in _split_list(value):
        if item in {"*", "-"}:
            continue
        if not _UINT_RE.match(item) or int(item) > 255:
            return f"«{item}» — нужен номер IP-протокола от 0 до 255, * или -."
    return ""


def check_l3(value: str) -> str:
    for item in _split_list(value):
        if item not in {"ipv4", "ipv6"}:
            return f"«{item}» — допустимо только ipv4 и ipv6 через запятую."
    return ""


def check_l7_list(value: str) -> str:
    for item in _split_list(value):
        if item not in L7_PROTO_NAMES:
            return f"Неизвестный протокол «{item}». Допустимо: {', '.join(L7_PROTO_NAMES)}."
    return ""


def check_payload_list(value: str) -> str:
    if validate_winws2_payload_filter(value):
        return ""
    for item in _split_list(str(value or "").strip().lower()):
        name = item.strip()
        if name not in _PAYLOAD_NAME_SET:
            return f"Неизвестный тип данных «{name}»."
    return "Неверный список типов данных."


def check_range(value: str) -> str:
    if parse_out_range_expression(value) is not None:
        return ""
    return (
        f"Неверный диапазон «{value}». Примеры: -d8 (первые 8 пакетов с данными), "
        "-n3, n2-n5, a (всегда), x (никогда)."
    )


def check_debug(value: str) -> str:
    if value.startswith("@"):
        return "" if value[1:] else "После @ нужен путь к файлу журнала."
    if value == "syslog" or (value and value[0] in "01"):
        return ""
    return "Нужно 0, 1, syslog или @файл."


def check_ctrack_timeouts(value: str) -> str:
    return "" if _CTRACK_RE.match(value) else "Нужно SYN:ESTABLISHED:FIN[:UDP], например 60:300:60:60."


def check_int(value: str, *, minimum: int | None = None, maximum: int | None = None) -> str:
    if not _INT_RE.match(value):
        return f"«{value}» — нужно целое число."
    number = int(value)
    if minimum is not None and number < minimum:
        return f"Значение должно быть не меньше {minimum}."
    if maximum is not None and number > maximum:
        return f"Значение должно быть не больше {maximum}."
    return ""


def check_bool01(value: str) -> str:
    return "" if value in {"0", "1"} else "Нужно 0 или 1."


def check_wf_iface(value: str) -> str:
    return "" if _WF_IFACE_RE.match(value) else "Нужен номер интерфейса, например 12 или 12.0."


@dataclass(frozen=True, slots=True)
class BlobDeclaration:
    name: str
    value: str
    file: str
    hex_value: str
    error: str


def parse_blob_value(value: str) -> BlobDeclaration:
    """``имя:@файл``, ``имя:+смещение@файл``, ``имя:файл`` или ``имя:0xHEX``."""
    raw = str(value or "")
    name, separator, tail = raw.partition(":")
    if not IDENTIFIER_RE.match(name):
        return BlobDeclaration(name, tail, "", "", f"«{name}» — не подходит как имя фейка: только латиница, цифры и _, "
                               "не с цифры.")
    if not separator or not tail:
        return BlobDeclaration(name, tail, "", "", "После имени нужно «:@путь/к/файлу.bin» или «:0xHEX».")
    if tail[:2].lower() == "0x":
        if not _HEX_RE.match(tail) or len(tail) == 2 or (len(tail) - 2) % 2:
            return BlobDeclaration(name, tail, "", tail, "Неверная hex-строка: нужно чётное число цифр 0-9, A-F.")
        if (len(tail) - 2) // 2 > _MAX_HEX_BLOB_BYTES:
            return BlobDeclaration(name, tail, "", tail, "Hex-фейк длиннее 16 КБ.")
        return BlobDeclaration(name, tail, "", tail, "")
    spec = tail
    if spec.startswith("+"):
        digits, at, rest = spec[1:].partition("@")
        if not _UINT_RE.match(digits) or not at:
            return BlobDeclaration(name, tail, "", "", "Смещение пишется так: имя:+100@файл.")
        spec = "@" + rest
    file_path = spec[1:] if spec.startswith("@") else spec
    if not file_path:
        return BlobDeclaration(name, tail, "", "", "Не указан файл фейка.")
    return BlobDeclaration(name, tail, file_path, "", "")


def check_pos_list(value: str) -> str:
    if not value:
        return "Нужен маркер позиции: host, midsld, sniext, число…"
    for item in _split_list(value):
        if _INT_RE.match(item):
            if not -32768 <= int(item) <= 32767:
                return f"Позиция {item} вне диапазона -32768..32767."
            continue
        match = _POS_MARKER_RE.match(item)
        if not match or match.group(1) not in POS_MARKER_NAMES:
            return (
                f"«{item}» — не маркер позиции. Допустимо число или "
                f"{', '.join(POS_MARKER_NAMES)} (можно +N/-N)."
            )
    return ""


def check_tls_mod(value: str) -> str:
    for item in _split_list(value):
        key, separator, sub = item.partition("=")
        if key not in TLS_MOD_NAMES:
            return f"«{item}» — неизвестная модификация. Допустимо: rnd, rndsni, dupsid, padencap, sni=домен, none."
        if key == "sni" and not sub:
            return "После sni= нужно имя сервера."
    return ""


def check_lua_payload(value: str) -> str:
    body = value[1:] if value.startswith("~") else value
    for item in _split_list(body):
        if item not in _PAYLOAD_NAME_SET:
            return f"Неизвестный тип данных «{item}» — функция никогда не сработает."
    return ""


@dataclass(frozen=True, slots=True)
class LuaCallArg:
    key: str
    key_start: int
    key_end: int
    value: str | None
    value_start: int
    value_end: int


@dataclass(frozen=True, slots=True)
class LuaCall:
    function: str
    function_start: int
    function_end: int
    args: tuple[LuaCallArg, ...]
    error: str
    error_start: int
    error_end: int


@lru_cache(maxsize=8192)
def parse_lua_call(value: str) -> LuaCall:
    """Разбор ``функция:арг=знач:арг2`` как ``parse_lua_call`` в nfqws.c.

    Колонки — внутри ``value``. ``\\:`` внутри значения — это двоеточие, а не
    граница аргумента. Результат неизменяемый и кэшируется: в пресетах одни и
    те же вызовы повторяются сотнями.
    """
    text = str(value or "")
    colon = text.find(":")
    name_end = colon if colon >= 0 else len(text)
    name = text[:name_end]
    args: list[LuaCallArg] = []
    if not IDENTIFIER_RE.match(name):
        message = "Не указана функция." if not name else f"«{name}» — не подходит как имя функции."
        return LuaCall(name, 0, name_end, (), message, 0, max(1, name_end))
    position = name_end + 1 if colon >= 0 else len(text)
    length = len(text)
    while position < length:
        end = text.find(":", position)
        while end > 0 and text[end - 1] == "\\":
            end = text.find(":", end + 1)
        if end < 0:
            end = length
        part = text[position:end]
        key, separator, raw_value = part.partition("=")
        key_end = position + len(key)
        if not IDENTIFIER_RE.match(key):
            message = (
                "Пустой аргумент: два двоеточия подряд." if not part else f"«{key}» — не подходит как имя аргумента."
            )
            return LuaCall(name, 0, name_end, tuple(args), message, position, max(position + 1, end))
        args.append(
            LuaCallArg(
                key=key,
                key_start=position,
                key_end=key_end,
                value=raw_value.replace("\\:", ":") if separator else None,
                value_start=key_end + 1 if separator else key_end,
                value_end=end,
            )
        )
        position = end + 1
    return LuaCall(name, 0, name_end, tuple(args), "", 0, 0)


__all__ = [
    "BlobDeclaration",
    "IDENTIFIER_RE",
    "L7_PROTO_NAMES",
    "LuaCall",
    "LuaCallArg",
    "PAYLOAD_NAMES",
    "POS_MARKER_NAMES",
    "TLS_MOD_NAMES",
    "check_bool01",
    "check_ctrack_timeouts",
    "check_debug",
    "check_icmp",
    "check_int",
    "check_ipp",
    "check_l3",
    "check_l7_list",
    "check_lua_payload",
    "check_payload_list",
    "check_ports",
    "check_pos_list",
    "check_range",
    "check_tls_mod",
    "check_wf_iface",
    "parse_blob_value",
    "parse_lua_call",
]
