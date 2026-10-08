from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RouteStatus(str, Enum):
    """Route confidence level for Telegram WSS routing."""

    STABLE = "stable"
    CANDIDATE = "candidate"
    FALLBACK_ONLY = "fallback_only"


@dataclass(frozen=True)
class WssRoute:
    dc: int
    hostname: str
    media_hostname: str
    relay_ip: str
    status: RouteStatus
    source: str
    note: str

    def domains_for(self, *, is_media: bool) -> tuple[str, str]:
        if is_media:
            return (self.media_hostname, self.hostname)
        return (self.hostname, self.media_hostname)


# Telegram's working WebSocket relay IP found in ZapretGUI, Flowseal
# tg-ws-proxy, and the decrypted GhostWire 1.0.13 config.
WSS_RELAY_IP = "149.154.167.220"
WSS_PATH = "/apiws"


# Keep this map conservative.  "candidate" means the WebSocket upgrade worked
# during manual checks, but the route is not yet allowed for automatic runtime
# routing until real Telegram traffic proves it safe for both SOCKS5 and MTProxy.
WSS_ROUTES: tuple[WssRoute, ...] = (
    WssRoute(
        dc=2,
        hostname="kws2.web.telegram.org",
        media_hostname="kws2-1.web.telegram.org",
        relay_ip=WSS_RELAY_IP,
        status=RouteStatus.STABLE,
        source="ZapretGUI + Flowseal tg-ws-proxy",
        note="Stable on 2026-06-14: repeated /apiws WebSocket Upgrade returned HTTP 101.",
    ),
    WssRoute(
        dc=4,
        hostname="kws4.web.telegram.org",
        media_hostname="kws4-1.web.telegram.org",
        relay_ip=WSS_RELAY_IP,
        status=RouteStatus.STABLE,
        source="ZapretGUI + Flowseal tg-ws-proxy",
        note="Stable on 2026-06-14: repeated /apiws WebSocket Upgrade returned HTTP 101.",
    ),
    WssRoute(
        dc=2,
        hostname="zws2.web.telegram.org",
        media_hostname="zws2-1.web.telegram.org",
        relay_ip=WSS_RELAY_IP,
        status=RouteStatus.CANDIDATE,
        source="GhostWire 1.0.13 decrypted config",
        note=(
            "Candidate only: HTTP 101 worked, but a 2026-06-14 live SOCKS5 "
            "probe made Telegram disable the proxy and produced recv=0/"
            "IncompleteReadError signs."
        ),
    ),
    WssRoute(
        dc=4,
        hostname="zws4.web.telegram.org",
        media_hostname="zws4-1.web.telegram.org",
        relay_ip=WSS_RELAY_IP,
        status=RouteStatus.CANDIDATE,
        source="GhostWire 1.0.13 decrypted config",
        note=(
            "Candidate only: HTTP 101 worked, but a 2026-06-14 live SOCKS5 "
            "probe made Telegram disable the proxy and produced recv=0/"
            "IncompleteReadError signs."
        ),
    ),
)


@dataclass(frozen=True)
class CdnFront:
    """Фронт Cloudflare: домен зоны и два её адреса (104.21.x и 172.67.x).

    Хост для DC строится как kwsN.<domain>; SNI и Host совпадают с ним
    (это не domain fronting). Подключение идёт сразу по адресу, без DNS.
    """

    domain: str
    addresses: tuple[str, str]

    def host_for(self, dc: int) -> str:
        return f"kws{int(dc)}.{self.domain}"


# Фронты tg-ws-proxy (github.com/Flowseal/tg-ws-proxy) в том же порядке, что и
# в ZaStoGram (jni/tgnet/wss/WssSocket.cpp, kCdnFronts). На 27.09.2026 по логам
# тестеров ZaStoGram фронты приносили данные там, где релей kwsN заблокирован.
CDN_FRONTS: tuple[CdnFront, ...] = (
    CdnFront("pclead.co.uk", ("104.21.80.254", "172.67.155.165")),
    CdnFront("offshor.co.uk", ("104.21.43.90", "172.67.177.105")),
    CdnFront("cakeisalie.co.uk", ("104.21.41.25", "172.67.159.17")),
    CdnFront("noskomnadzor.co.uk", ("104.21.70.196", "172.67.138.236")),
    CdnFront("lovetrue.co.uk", ("104.21.21.168", "172.67.199.162")),
    CdnFront("sorokdva.co.uk", ("104.21.69.145", "172.67.209.89")),
    CdnFront("pyatdesyatdva.co.uk", ("104.21.73.83", "172.67.189.26")),
    CdnFront("kartoshka.co.uk", ("104.21.39.36", "172.67.142.232")),
    CdnFront("sorokodin.co.uk", ("104.21.84.223", "172.67.197.117")),
    CdnFront("pyatdesyatodin.co.uk", ("104.21.48.178", "172.67.155.85")),
    CdnFront("notelega.co.uk", ("104.21.33.146", "172.67.146.105")),
    CdnFront("ebally.co.uk", ("104.21.78.6", "172.67.214.68")),
    CdnFront("nebally.co.uk", ("104.21.7.253", "172.67.156.145")),
    CdnFront("havegreatday.co.uk", ("104.21.25.159", "172.67.134.93")),
    CdnFront("pomogite.co.uk", ("104.21.44.55", "172.67.195.218")),
    CdnFront("fixtelega.co.uk", ("104.21.64.155", "172.67.152.37")),
    CdnFront("sadnews.co.uk", ("104.21.51.133", "172.67.180.160")),
    CdnFront("onedaychamp.co.uk", ("104.21.37.105", "172.67.207.129")),
    CdnFront("stopblocking.co.uk", ("104.21.35.206", "172.67.179.145")),
    CdnFront("nothingthere.co.uk", ("104.21.78.5", "172.67.214.67")),
)

# Фронты обслуживают только основные DC; у DC203 своего kws нет.
CDN_FRONT_DCS: tuple[int, ...] = (1, 2, 3, 4, 5)

# Туннель через воркер Cloudflare (zastogram-ws-worker/worker.js):
# wss://<host>/apiws?dst=<IPv4 DC>, дальше воркер идёт на DC обычным TCP.
# Воркеры лежат на разных учётных записях Cloudflare: у бесплатной записи
# 100 000 запросов в сутки, после них воркер до 00:00 UTC отвечает 429
# (error code: 1027). Каждая установка начинает перебор со случайного воркера,
# так нагрузка делится между записями. Новый воркер — новая строка здесь и в
# ZaStoGram (jni/tgnet/wss/WssSocket.cpp, mtproto/proxy/wss/socket.cpp).
TUNNEL_HOSTS: tuple[str, ...] = (
    "edge.amberwick.workers.dev",
)


FALLBACK_ONLY_REASONS: dict[int, str] = {
    1: "kws1/zws1 did not prove a stable HTTP 101 route; use Cloudflare/Worker, TCP, or upstream SOCKS5.",
    3: "kws3/zws3 did not prove a stable HTTP 101 route; use Cloudflare/Worker, TCP, or upstream SOCKS5.",
    5: "kws5/zws5 did not prove a stable HTTP 101 route; use Cloudflare/Worker, TCP, or upstream SOCKS5.",
    203: "kws203 and direct DC203 IP did not prove stable HTTP 101; zws2 is only a candidate and must not be automatic yet.",
}


def _routes_for(dc: int, status: RouteStatus) -> tuple[WssRoute, ...]:
    return tuple(route for route in WSS_ROUTES if route.dc == int(dc) and route.status == status)


def wss_enabled_dcs() -> tuple[int, ...]:
    """Return DCs allowed for automatic WSS routing."""

    return tuple(sorted({route.dc for route in WSS_ROUTES if route.status == RouteStatus.STABLE}))


def stable_wss_domains_for_dc(dc: int, *, is_media: bool) -> tuple[str, ...]:
    """Return stable WSS domains for a DC in the order used by runtime routing."""

    domains: list[str] = []
    for route in _routes_for(dc, RouteStatus.STABLE):
        domains.extend(route.domains_for(is_media=is_media))
    return tuple(domains)


def candidate_wss_domains_for_dc(dc: int, *, is_media: bool) -> tuple[str, ...]:
    """Return visible-but-disabled WSS candidates for diagnostics and future testing."""

    domains: list[str] = []
    for route in _routes_for(dc, RouteStatus.CANDIDATE):
        domains.extend(route.domains_for(is_media=is_media))
    return tuple(domains)


def route_status_for_dc(dc: int) -> str:
    if stable_wss_domains_for_dc(dc, is_media=False):
        return RouteStatus.STABLE.value
    if candidate_wss_domains_for_dc(dc, is_media=False):
        return RouteStatus.CANDIDATE.value
    return RouteStatus.FALLBACK_ONLY.value


def fallback_only_reason(dc: int) -> str:
    return FALLBACK_ONLY_REASONS.get(
        int(dc),
        "No stable WSS route is recorded for this DC; use fallback routing.",
    )


def stable_wss_domain_map() -> dict[int, list[str]]:
    return {
        dc: list(stable_wss_domains_for_dc(dc, is_media=False))
        for dc in wss_enabled_dcs()
    }


__all__ = [
    "CDN_FRONTS",
    "CDN_FRONT_DCS",
    "CdnFront",
    "FALLBACK_ONLY_REASONS",
    "RouteStatus",
    "TUNNEL_HOSTS",
    "WSS_PATH",
    "WSS_RELAY_IP",
    "WSS_ROUTES",
    "WssRoute",
    "candidate_wss_domains_for_dc",
    "fallback_only_reason",
    "route_status_for_dc",
    "stable_wss_domain_map",
    "stable_wss_domains_for_dc",
    "wss_enabled_dcs",
]
