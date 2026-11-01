"""Строки профиля подбора — одни и те же для пробы и для «Применить».

Пресет — точка истины: что подбор проверил, ровно то и записывается в пресет.
Поэтому профиль собирается здесь один раз, результат стратегии несёт его
строки с собой, а «Применить» пишет их без пересборки.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from dataclasses import dataclass

PROTOCOL_TCP_HTTPS = "tcp_https"
# Сайт без HTTPS: подбор переходит сюда сам, когда порт 443 не отвечает, а
# обычный HTTP на порту 80 закрыт фильтром (заглушка, сброс). Отдельной плитки
# у этого режима нет — его выбирает сама плитка «Сайты и приложения».
PROTOCOL_HTTP80 = "tcp_http"
# QUIC (UDP 443) сайта: подбор переходит сюда сам, когда сайт по TCP открывается
# без обхода, а первый пакет QUIC с его именем пропадает, хотя с посторонним
# именем тот же сервер отвечает. Тоже без своей плитки.
PROTOCOL_QUIC = "udp_quic"
PROTOCOL_STUN_VOICE = "stun_voice"
PROTOCOL_UDP_GAMES = "udp_games"

UDP_GAMES_PORT_FILTER = "443,50000-65535"
# Малое окно приёма для адресов, где ТСПУ режет TLS по ответу сервера (``block_cause.CAUSE_REPLY_WINDOW``):
# окно 1 с множителем 2¹ — сервер видит «принимаю 2 байта», и фильтр не узнаёт подпись TLS, ей нужно три.
# С окном от 3 байт фильтр проходит через раз (bolvan, ntc.party, 29.10.2026). Множитель задаётся
# при открытии соединения и потом не меняется: скорость падает до конца соединения, поэтому приём
# пишется только в профиль по адресам — список доменов на первом пакете ещё не известен.
WINDOW_STRATEGY_LINE = "--lua-desync=wssize:wsize=1:scale=1"
# Фильтр держит в списке группы серверов Google по 32 адреса: профиль малого окна берёт всю группу
# адреса (IPv4 /27), чтобы покрыть соседние видеосерверы того же узла. IPv6 — только сам адрес.
WINDOW_GROUP_PREFIX_V4 = 27
VOICE_PORT_FILTER = "443-65535"


def games_port_filter(extra_ports: Sequence[int] = ()) -> str:
    """Игровые порты плюс порты проверок подбора.

    Иначе winws2 не перехватит ни одного проверяемого пакета (STUN 3478 и
    19302, игровые серверы 27015, 28015, 19132), и результат подбора игр
    зависел бы только от того, открыта ли цель без обхода.
    """
    extra: list[int] = []
    for raw in extra_ports:
        try:
            port = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= port <= 65535 and port != 443 and not 50000 <= port <= 65535 and port not in extra:
            extra.append(port)
    return ",".join([UDP_GAMES_PORT_FILTER, *(str(port) for port in sorted(extra))])


@dataclass(frozen=True, slots=True)
class ProbeProfile:
    # Строки перехвата WinDivert (``--wf-*``): в пресете они живут в преамбуле.
    preamble_lines: tuple[str, ...]
    # Фильтры профиля: на какой трафик действует стратегия.
    filter_lines: tuple[str, ...]
    # Сама готовая стратегия (строки ``--lua-desync`` и её модификаторы).
    strategy_lines: tuple[str, ...]
    # Профиль малого окна перед основным (фильтры и стратегия целиком). Пусто — его нет.
    window_lines: tuple[str, ...] = ()

    def profile_lines(self) -> list[str]:
        """Профили без преамбулы: малое окно (если есть), ``--new``, основной."""
        window = [*self.window_lines, "--new"] if self.window_lines else []
        return [*window, *self.filter_lines, *self.strategy_lines]

    def apply_lines(self) -> list[str]:
        """Строки для «Применить»: преамбула сливается с пресетом, остальное — профили."""
        return [*self.preamble_lines, *self.profile_lines()]


def window_groups(addresses: Sequence[str]) -> tuple[str, ...]:
    """Группы адресов для профиля малого окна: IPv4 — сеть /27 адреса, IPv6 — сам адрес."""
    groups: list[str] = []
    for raw in addresses:
        try:
            address = ipaddress.ip_address(str(raw or "").strip())
        except ValueError:
            continue
        if address.version == 4:
            groups.append(str(ipaddress.ip_network(f"{address}/{WINDOW_GROUP_PREFIX_V4}", strict=False)))
        else:
            groups.append(str(address))
    return tuple(dict.fromkeys(groups))


def strategy_lines_from_args(strategy_args: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in str(strategy_args or "").splitlines() if line.strip())


# Функции, которые до конца соединения правят номера TCP в обе стороны: им нужен
# перехват входящих пакетов и всё соединение, а не первые пакеты (описание
# функции в ``lua/custom_funcs.lua``). С обычным профилем такая стратегия
# проверялась бы нечестно и всегда «не работала».
_FULL_FLOW_FUNCTIONS = frozenset({"tlsrec"})


def needs_full_flow(strategy: Sequence[str]) -> bool:
    """Стратегия ведёт всё соединение: её проверяли со своим фильтром пакетов
    (``--in-range=a --out-range=a --payload=all``), и «Применить» обязано
    записать именно его — с диапазонами обычного профиля она не работает."""
    for line in strategy:
        if line.startswith("--lua-desync="):
            name = line.split("=", 1)[1].split(":", 1)[0].strip()
            if name in _FULL_FLOW_FUNCTIONS:
                return True
    return False


def build_probe_profile(
    scan_protocol: str,
    *,
    strategy_args: str,
    match_domain: str = "",
    games_ipset_paths: Sequence[str] = (),
    games_addresses: Sequence[str] = (),
    games_ports: Sequence[int] = (),
    window_addresses: Sequence[str] = (),
) -> ProbeProfile:
    """Профиль одной стратегии для выбранного режима подбора.

    - Сайты: только TCP 443 и только домен цели (``--hostlist-domains``
      покрывает и поддомены). ``--out-range=-d8`` — стратегия работает на
      первых пакетах соединения, как в обычных пресетах. Стратегии, которые
      ведут всё соединение (``tlsrec``), получают входящие пакеты и всё
      соединение — так, как требует сама функция. ``window_addresses`` —
      адреса, где TLS режут по ответу сервера: перед основным профилем идёт
      профиль по их группам (``--ipset-ip``) с малым окном и той же стратегией.
      Профиль по адресам выбирается уже на первом пакете соединения, когда
      имя сайта ещё не известно, и стоит выше профиля по именам, чтобы
      соединение не ушло из него, когда имя станет известно.
    - Голос: STUN и Discord IP discovery на любых UDP-портах.
    - Игры: UDP игровых портов и портов проверок на адресах из игровых
      ipset и на адресах, которыми подбор проверял стратегию.
    """
    strategy = strategy_lines_from_args(strategy_args)
    if scan_protocol == PROTOCOL_STUN_VOICE:
        return ProbeProfile(
            (f"--wf-udp-out={VOICE_PORT_FILTER}",),
            ("--filter-l7=stun,discord", "--payload=stun,discord_ip_discovery"),
            strategy,
        )
    if scan_protocol == PROTOCOL_UDP_GAMES:
        ports = games_port_filter(games_ports)
        filters = [f"--filter-udp={ports}"]
        filters.extend(f"--ipset={path}" for path in games_ipset_paths if str(path or "").strip())
        addresses = [str(address).strip() for address in games_addresses if str(address or "").strip()]
        if addresses:
            filters.append(f"--ipset-ip={','.join(dict.fromkeys(addresses))}")
        return ProbeProfile((f"--wf-udp-out={ports}",), tuple(filters), strategy)
    domain = str(match_domain or "").strip().lower()
    if scan_protocol == PROTOCOL_QUIC:
        return ProbeProfile(
            ("--wf-udp-out=443",),
            ("--filter-udp=443", f"--hostlist-domains={domain}", "--payload=quic_initial"),
            strategy,
        )
    if scan_protocol == PROTOCOL_HTTP80:
        return ProbeProfile(
            ("--wf-tcp-out=80",),
            ("--filter-tcp=80", f"--hostlist-domains={domain}", "--out-range=-d8"),
            strategy,
        )
    full_flow = needs_full_flow(strategy)
    ranges = ("--in-range=a", "--out-range=a", "--payload=all") if full_flow else ("--out-range=-d8",)
    groups = window_groups(window_addresses)
    if groups and strategy == (WINDOW_STRATEGY_LINE,):
        # Одно малое окно — это профиль по адресам и только он: профиль по именам с пустой стратегией
        # ничего бы не делал, а «Применить» перебил бы им рабочую стратегию сайта на остальных адресах.
        return ProbeProfile(("--wf-tcp-out=443",), ("--filter-tcp=443", f"--ipset-ip={','.join(groups)}", *ranges), strategy)
    window = (
        ("--filter-tcp=443", f"--ipset-ip={','.join(groups)}", *ranges, WINDOW_STRATEGY_LINE, *strategy)
        if groups
        else ()
    )
    return ProbeProfile(
        ("--wf-tcp-out=443", "--wf-tcp-in=443") if full_flow else ("--wf-tcp-out=443",),
        ("--filter-tcp=443", f"--hostlist-domains={domain}", *ranges),
        strategy,
        window,
    )


def build_probe_config_text(profile: ProbeProfile, blob_lines: Sequence[str]) -> str:
    """Текст @config для winws2: обязательный блок Lua, фейки, преамбула, профиль.

    Фейки (``--blob=``) объявляются явно, как в обычном пресете: иначе
    стратегия с фейком работала бы как пустая.
    """
    from profile.winws2_preset_source import WINWS2_LUA_INIT_LINES

    lines: list[str] = [*WINWS2_LUA_INIT_LINES]
    lines.extend(line for line in blob_lines if line)
    lines.extend(profile.preamble_lines)
    lines.extend(profile.profile_lines())
    return "\n".join(lines) + "\n"
