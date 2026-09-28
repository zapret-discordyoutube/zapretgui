"""Строки профиля подбора — одни и те же для пробы и для «Применить».

Пресет — точка истины: что подбор проверил, ровно то и записывается в пресет.
Поэтому профиль собирается здесь один раз, результат стратегии несёт его
строки с собой, а «Применить» пишет их без пересборки.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

PROTOCOL_TCP_HTTPS = "tcp_https"
PROTOCOL_STUN_VOICE = "stun_voice"
PROTOCOL_UDP_GAMES = "udp_games"

UDP_GAMES_PORT_FILTER = "443,50000-65535"
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

    def apply_lines(self) -> list[str]:
        """Строки для «Применить»: преамбула сливается с пресетом, остальное — профиль."""
        return [*self.preamble_lines, *self.filter_lines, *self.strategy_lines]


def strategy_lines_from_args(strategy_args: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in str(strategy_args or "").splitlines() if line.strip())


def build_probe_profile(
    scan_protocol: str,
    *,
    strategy_args: str,
    match_domain: str = "",
    games_ipset_paths: Sequence[str] = (),
    games_addresses: Sequence[str] = (),
    games_ports: Sequence[int] = (),
) -> ProbeProfile:
    """Профиль одной стратегии для выбранного режима подбора.

    - Сайты: только TCP 443 и только домен цели (``--hostlist-domains``
      покрывает и поддомены). ``--out-range=-d8`` — стратегия работает на
      первых пакетах соединения, как в обычных пресетах.
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
    return ProbeProfile(
        ("--wf-tcp-out=443",),
        ("--filter-tcp=443", f"--hostlist-domains={domain}", "--out-range=-d8"),
        strategy,
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
    lines.extend(profile.filter_lines)
    lines.extend(profile.strategy_lines)
    return "\n".join(lines) + "\n"
