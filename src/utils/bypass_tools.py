"""Какие программы обхода блокировок и VPN сейчас запущены.

Такие программы меняют сами соединения: дробят пакеты, подставляют свой DNS,
уводят трафик в туннель. Сетевая проверка при них показывает не «чистую»
сеть провайдера, и об этом надо сказать рядом с результатом.

Только чтение списка процессов: ничего не останавливается и не меняется.
Сам Zapret сюда не входит — о нём проверки сообщают отдельно.
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = [
    "BYPASS_TOOLS",
    "PACKET_TOOLS",
    "PROXY_TOOLS",
    "bypass_tools_among",
    "running_bypass_tools",
    "tool_kind",
    "tools_in_path",
]

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


# Три вида программ, и мешают проверке они по-разному.
#
# Проверка открывает соединения сама, напрямую. Поэтому:
# - программа, переделывающая пакеты компьютера, задевает любое соединение — всегда;
# - VPN задевает её, только когда через его адаптер проложена дорога в интернет;
# - прокси её не задевает вовсе: через прокси ходят лишь программы, которым он назначен
#   (обычно браузер). Исключение — режим TUN: тогда у прокси появляется свой адаптер с
#   дорогой в интернет, и он виден так же, как VPN.
PACKET_TOOLS = frozenset({"GoodbyeDPI"})
PROXY_TOOLS = frozenset(
    {
        "ByeDPI",
        "SpoofDPI",
        "Xray",
        "V2Ray",
        "v2rayN",
        "sing-box",
        "Mihomo (Clash)",
        "Clash",
        "Clash Verge",
        "NekoRay",
        "NekoBox",
        "Hiddify",
        "Happ",
    }
)


def tool_kind(name: str) -> str:
    """``packet`` — меняет пакеты, ``proxy`` — прокси, ``vpn`` — VPN со своим адаптером."""
    if name in PACKET_TOOLS:
        return "packet"
    return "proxy" if name in PROXY_TOOLS else "vpn"


def tools_in_path(tools: Iterable[str], routed_adapters: Iterable[str] | None) -> tuple[str, ...]:
    """Что из запущенного стоит на дороге проверки.

    ``routed_adapters`` — VPN-адаптеры, через которые сейчас проложена дорога в
    интернет (пусто — интернет идёт напрямую). Тогда на дороге стоят они и
    программы, меняющие пакеты; запущенный, но не подключённый VPN и прокси в
    обычном режиме — нет. ``None`` — узнать про адаптеры не удалось: считаем,
    что VPN мешает, чтобы не выдать искажённый вывод за чистый. Прокси не
    считаем и тогда: без адаптера он до проверки не дотягивается.
    """
    tools = tuple(str(name) for name in tools)
    found = [name for name in tools if tool_kind(name) == "packet"]
    if routed_adapters is None:
        found += [name for name in tools if tool_kind(name) == "vpn"]
    else:
        found += [f"VPN-подключение «{name}»" for name in routed_adapters]
    return tuple(dict.fromkeys(found))


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
