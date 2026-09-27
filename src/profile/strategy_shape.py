"""Форма стратегии profile winws2: настройки profile и сама стратегия.

Строки стратегии profile-а делятся на две части:

- диапазоны ``--in-range``/``--out-range`` ДО первой ``--lua-desync`` — это
  настройки profile-а (их правят поля страницы profile-а), в стратегию они не
  входят;
- всё остальное по порядку — сама стратегия: ``--payload``, диапазоны после
  первой ``--lua-desync`` и строки ``--lua-desync``.

Стратегия «составная», если после первой ``--lua-desync`` встречается
внутрипрофильный фильтр (``--payload``/``--in-range``/``--out-range``): она
состоит из нескольких веток для разных типов пакетов, но это ОДНА стратегия —
одно имя, одна оценка, выбирается и применяется целиком. Готовая составная
стратегия в каталоге записана ровно этими строками.

Обычная (одноветочная) стратегия узнаётся только по строкам ``--lua-desync``:
ведущий ``--payload`` у неё остаётся настройкой profile-а, как и раньше.

Модуль — единственный источник этих правил: каталог, распознавание, запись
выбранной стратегии и значок типов пакетов берут их отсюда.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


PAYLOAD_OPTION = "--payload"
RANGE_OPTIONS = ("--in-range", "--out-range")
FILTER_OPTIONS = (PAYLOAD_OPTION, *RANGE_OPTIONS)
LUA_DESYNC_PREFIX = "--lua-desync="
PAYLOAD_ALL = "all"

# Набор UDP-пакетов игр и VPN из пресетов Flowseal — в значке это «игры».
_GAME_PAYLOAD_TOKENS = frozenset({"wireguard_initiation", "dht", "discord_ip_discovery", "stun"})
_PAYLOAD_TOKEN_LABELS = {
    "tls_client_hello": "TLS",
    "http_req": "HTTP",
    "quic_initial": "QUIC",
    "wireguard_initiation": "WireGuard",
    "dht": "DHT",
    "discord_ip_discovery": "Discord",
    "stun": "STUN",
    "unknown": "прочее",
    "known": "известные",
    "empty": "пустые",
}
PAYLOAD_BADGE_SEPARATOR = " · "


def option_name(line: str) -> str:
    return str(line or "").strip().split("=", 1)[0].strip().lower()


def option_value(line: str) -> str:
    return str(line or "").strip().partition("=")[2].strip()


def is_lua_desync_line(line: str) -> bool:
    return str(line or "").strip().lower().startswith(LUA_DESYNC_PREFIX)


def is_filter_line(line: str) -> bool:
    return option_name(line) in FILTER_OPTIONS


def is_range_line(line: str) -> bool:
    return option_name(line) in RANGE_OPTIONS


@dataclass(frozen=True)
class StrategyShape:
    # Диапазоны до первой --lua-desync: настройки profile-а.
    profile_range_lines: tuple[str, ...]
    # Остальные строки стратегии по порядку.
    body_lines: tuple[str, ...]
    # После первой --lua-desync есть внутрипрофильный фильтр.
    composite: bool
    # --payload каждой ветки (группы подряд идущих --lua-desync) по порядку.
    payload_scopes: tuple[str, ...]

    @property
    def lua_lines(self) -> tuple[str, ...]:
        return tuple(line for line in self.body_lines if is_lua_desync_line(line))


def strategy_shape(lines: Iterable[str]) -> StrategyShape:
    """Делит строки стратегии winws2 (``--payload``/диапазоны/``--lua-desync``)."""
    clean = [str(line or "").strip() for line in lines or () if str(line or "").strip()]
    first_lua = next((index for index, line in enumerate(clean) if is_lua_desync_line(line)), None)
    profile_ranges: list[str] = []
    body: list[str] = []
    for index, line in enumerate(clean):
        if is_range_line(line) and (first_lua is None or index < first_lua):
            profile_ranges.append(line)
        else:
            body.append(line)
    composite = first_lua is not None and any(is_filter_line(line) for line in clean[first_lua + 1 :])
    return StrategyShape(
        profile_range_lines=tuple(profile_ranges),
        body_lines=tuple(body),
        composite=composite,
        payload_scopes=_payload_scopes(body),
    )


def _payload_scopes(body_lines: Iterable[str]) -> tuple[str, ...]:
    payload = PAYLOAD_ALL
    scopes: list[str] = []
    in_branch = False
    for line in body_lines:
        if is_lua_desync_line(line):
            if not in_branch:
                scopes.append(payload)
                in_branch = True
            continue
        if is_filter_line(line):
            in_branch = False
            if option_name(line) == PAYLOAD_OPTION:
                payload = option_value(line) or PAYLOAD_ALL
    return tuple(scopes)


def _payload_tokens(value: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for part in str(value or "").split(","):
        token = part.strip().lower()
        if token and token not in tokens:
            tokens.append(token)
    return tuple(tokens) or (PAYLOAD_ALL,)


def composite_identity(body_lines: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """Отпечаток составной стратегии для сравнения profile-а с каталогом.

    Порядок строк важен. ``--payload`` сравнивается как набор типов,
    отсутствие ``--payload`` перед первой ``--lua-desync`` равно
    ``--payload=all``, из нескольких ``--payload`` подряд действует последний.
    """
    items: list[tuple[str, str]] = []
    for line in body_lines or ():
        clean = str(line or "").strip()
        if not clean:
            continue
        name = option_name(clean)
        if is_lua_desync_line(clean):
            if not any(kind == "payload" for kind, _value in items):
                items.insert(0, ("payload", PAYLOAD_ALL))
            items.append(("lua", clean))
            continue
        if name == PAYLOAD_OPTION:
            value = ",".join(sorted(_payload_tokens(option_value(clean))))
            if items and items[-1][0] == "payload":
                items[-1] = ("payload", value)
            else:
                items.append(("payload", value))
            continue
        if name in RANGE_OPTIONS:
            items.append(("range", f"{name}={option_value(clean)}"))
            continue
        items.append(("other", clean))
    while items and items[-1][0] != "lua":
        items.pop()
    return tuple(items)


def union_payload(scopes: Iterable[str]) -> str:
    """Один ``--payload`` вместо нескольких веток: объединение типов, ``all`` поглощает всё."""
    tokens: list[str] = []
    for scope in scopes or ():
        for token in _payload_tokens(scope):
            if token == PAYLOAD_ALL:
                return PAYLOAD_ALL
            if token not in tokens:
                tokens.append(token)
    return ",".join(tokens) or PAYLOAD_ALL


def payload_scope_label(scope: str, *, only: bool = False) -> str:
    tokens = _payload_tokens(scope)
    if tokens == (PAYLOAD_ALL,):
        return "всё" if only else "прочее"
    if frozenset(tokens) == _GAME_PAYLOAD_TOKENS:
        return "игры"
    return "+".join(_PAYLOAD_TOKEN_LABELS.get(token, token) for token in tokens)


def payload_badge_text(scopes: Iterable[str]) -> str:
    """Короткая подпись типов пакетов составной стратегии: «TLS · HTTP · TLS+HTTP».

    Для обычной (одноветочной) стратегии — пустая строка.
    """
    ordered = tuple(scopes or ())
    if len(ordered) < 2:
        return ""
    labels: list[str] = []
    unique = len({*(tuple(sorted(_payload_tokens(scope))) for scope in ordered)}) == 1
    for scope in ordered:
        label = payload_scope_label(scope, only=unique)
        if label not in labels:
            labels.append(label)
    return PAYLOAD_BADGE_SEPARATOR.join(labels)


def payload_badge_accessible_text(badge: str) -> str:
    """Значок типов пакетов для экранного диктора."""
    clean = str(badge or "").strip()
    return f"типы пакетов: {clean}" if clean else ""


__all__ = [
    "FILTER_OPTIONS",
    "LUA_DESYNC_PREFIX",
    "PAYLOAD_ALL",
    "PAYLOAD_BADGE_SEPARATOR",
    "PAYLOAD_OPTION",
    "RANGE_OPTIONS",
    "StrategyShape",
    "composite_identity",
    "is_filter_line",
    "is_lua_desync_line",
    "is_range_line",
    "option_name",
    "option_value",
    "payload_badge_accessible_text",
    "payload_badge_text",
    "payload_scope_label",
    "strategy_shape",
    "union_payload",
]
