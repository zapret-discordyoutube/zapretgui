from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class OutRangeSettings:
    enabled: bool = False
    value: int = 0
    mode: str = "n"
    expression: str = ""
    raw_line: str = ""


_OUT_RANGE_SIMPLE_AUTO_RE = re.compile(r"^a$", re.IGNORECASE)
_OUT_RANGE_SIMPLE_NEVER_RE = re.compile(r"^x$", re.IGNORECASE)
_OUT_RANGE_SIMPLE_RE = re.compile(r"^-(?P<mode>[nd])(?P<value>\d+)$", re.IGNORECASE)
_OUT_RANGE_BOUND_RE = r"(?:[ndspb]\d+)"
_OUT_RANGE_RANGE_RE = re.compile(
    rf"^(?:{_OUT_RANGE_BOUND_RE}(?:-|<){_OUT_RANGE_BOUND_RE}|(?:-|<){_OUT_RANGE_BOUND_RE}|{_OUT_RANGE_BOUND_RE}-)$",
    re.IGNORECASE,
)
# Типы данных winws2 в порядке zapret2/nfq2/protocol.c (l7payload_name).
WINWS2_PAYLOAD_NAMES: tuple[str, ...] = (
    "all",
    "unknown",
    "empty",
    "known",
    "ipv4",
    "ipv6",
    "icmp",
    "http_req",
    "http_reply",
    "tls_client_hello",
    "tls_server_hello",
    "dtls_client_hello",
    "dtls_server_hello",
    "quic_initial",
    "wireguard_initiation",
    "wireguard_response",
    "wireguard_cookie",
    "wireguard_keepalive",
    "wireguard_data",
    "dht",
    "discord_ip_discovery",
    "stun",
    "xmpp_stream",
    "xmpp_starttls",
    "xmpp_proceed",
    "xmpp_features",
    "dns_query",
    "dns_response",
    "mtproto_initial",
    "bt_handshake",
    "utp_bt_handshake",
)
_VALID_PAYLOAD_NAMES = frozenset(WINWS2_PAYLOAD_NAMES)


def normalize_out_range_expression(expression: object) -> str:
    return _strip_outer_quotes(str(expression or "")).strip().lower()


def parse_out_range_expression(expression: object, *, raw_line: str = "") -> OutRangeSettings | None:
    expr = normalize_out_range_expression(expression)
    if not expr:
        return None

    if _OUT_RANGE_SIMPLE_AUTO_RE.match(expr):
        return OutRangeSettings(enabled=True, value=0, mode="a", expression="a", raw_line=raw_line)

    if _OUT_RANGE_SIMPLE_NEVER_RE.match(expr):
        return OutRangeSettings(enabled=True, value=0, mode="x", expression="x", raw_line=raw_line)

    simple_match = _OUT_RANGE_SIMPLE_RE.match(expr)
    if simple_match:
        mode = simple_match.group("mode").lower()
        value = int(simple_match.group("value"))
        return OutRangeSettings(enabled=True, value=value, mode=mode, expression=f"-{mode}{value}", raw_line=raw_line)

    if _OUT_RANGE_RANGE_RE.match(expr):
        return OutRangeSettings(enabled=True, value=0, mode="", expression=expr, raw_line=raw_line)

    return None


def validate_winws2_payload_filter(value: object) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return False
    for item in raw.split(","):
        name = item.strip()
        if not name or name not in _VALID_PAYLOAD_NAMES:
            return False
        if name == "all":
            return True
    return True


def _strip_outer_quotes(value: str) -> str:
    text = str(value or "").strip()
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        text = text[1:-1]
    return text.strip()
