"""Какие программы обхода блокировок и VPN сейчас запущены.

Такие программы меняют сами соединения: дробят пакеты, подставляют свой DNS,
уводят трафик в туннель. Сетевая проверка при них показывает не «чистую»
сеть провайдера, и об этом надо сказать рядом с результатом.

Только чтение списка процессов: ничего не останавливается и не меняется.
Сам Zapret сюда не входит — о нём проверки сообщают отдельно.
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = ["BYPASS_TOOLS", "PACKET_TOOLS", "bypass_tools_among", "running_bypass_tools", "tools_in_path"]

# Имя процесса в нижнем регистре → как назвать программу пользователю.
BYPASS_TOOLS: dict[str, str] = {
    "goodbyedpi.exe": "GoodbyeDPI",
    "ciadpi.exe": "ByeDPI",
    "byedpi.exe": "ByeDPI",
    "spoofdpi.exe": "SpoofDPI",
    "xray.exe": "Xray",
    "v2ray.exe": "V2Ray",
    "v2rayn.exe": "v2rayN",
    "sing-box.exe": "sing-box",
    "mihomo.exe": "Mihomo (Clash)",
    "clash.exe": "Clash",
    "clash-verge.exe": "Clash Verge",
    "verge-mihomo.exe": "Clash Verge",
    "nekoray.exe": "NekoRay",
    "nekobox.exe": "NekoBox",
    "hiddify.exe": "Hiddify",
    "happ.exe": "Happ",
    "tun2socks.exe": "tun2socks",
    "warp-svc.exe": "Cloudflare WARP",
    "amneziavpn.exe": "AmneziaVPN",
    "amneziawg.exe": "AmneziaWG",
    "wireguard.exe": "WireGuard",
    "openvpn.exe": "OpenVPN",
    "tailscaled.exe": "Tailscale",
}


# Программы, которые переделывают сами пакеты компьютера: они задевают любое соединение,
# включая пробы проверки, — куда бы ни вела дорога в интернет.
PACKET_TOOLS = frozenset({"GoodbyeDPI"})


def tools_in_path(tools: Iterable[str], vpn_routed: bool | None) -> tuple[str, ...]:
    """Какие из запущенных программ стоят на дороге проверки, а какие просто запущены.

    Проверка ходит в сеть напрямую. VPN и прокси её задевают, только когда через
    их подключение проложена дорога в интернет (``vpn_routed``): запущенный, но
    не включённый VPN ничего не меняет. ``None`` — узнать не удалось: тогда
    считаем, что задевают, чтобы не выдать искажённый вывод за чистый.
    """
    tools = tuple(str(name) for name in tools)
    if vpn_routed is not False:
        return tools
    return tuple(name for name in tools if name in PACKET_TOOLS)


def bypass_tools_among(process_names: Iterable[str]) -> tuple[str, ...]:
    """Названия программ обхода среди имён процессов, без повторов, в порядке списка."""
    running = {str(name or "").strip().lower() for name in process_names}
    return tuple(dict.fromkeys(title for name, title in BYPASS_TOOLS.items() if name in running))


def running_bypass_tools() -> tuple[str, ...]:
    """Программы обхода и VPN, запущенные сейчас. Пусто, если узнать не удалось."""
    try:
        from utils.windows_process_probe import iter_process_records_winapi

        return bypass_tools_among(name for _pid, name in iter_process_records_winapi())
    except Exception:
        return ()
