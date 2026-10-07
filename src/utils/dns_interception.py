"""Перехват DNS по дороге: признаки того, что отвечает не выбранный сервер.

Провайдер или роутер может заворачивать все обычные DNS-запросы (порт 53) на
свой сервер, какой бы адрес ни стоял в настройках. Ответы при этом приходят
будто бы от выбранного сервера, и сами по себе выглядят правдоподобно.

Контрольный адрес — из сети 192.0.2.0/24 (TEST-NET-1, RFC 5737): она отведена
под примеры в документации, настоящего DNS-сервера там нет и быть не может.
Если на запрос туда пришёл ответ, значит запросы перехватываются.
"""

from __future__ import annotations

from utils.dns_wire import TYPE_A, query_udp
from utils.socket_cancel import SocketCancel

__all__ = ["CANARY_SERVER", "canary_answered"]

CANARY_SERVER = "192.0.2.53"
_CANARY_DOMAIN = "google.com"
_CANARY_TIMEOUT_S = 1.0


def canary_answered(
    domain: str = _CANARY_DOMAIN,
    *,
    timeout_s: float = _CANARY_TIMEOUT_S,
    cancel: SocketCancel | None = None,
) -> bool:
    """Ответил ли адрес, где DNS-сервера нет. Любой ответ, даже «сайта нет», — перехват."""
    return query_udp(CANARY_SERVER, domain, TYPE_A, timeout_s=timeout_s, cancel=cancel).answered
