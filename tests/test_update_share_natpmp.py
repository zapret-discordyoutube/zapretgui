from __future__ import annotations

"""Порт на роутере по PCP и NAT-PMP: второй способ, когда UPnP выключен (против поддельного роутера из UDP)."""

import socket
import struct
import threading

import pytest

from zapret_update import share_natpmp, share_portmap
from zapret_update.share_portmap import Mapping

WHITE = "93.184.216.34"
HOST = "127.0.0.1"


def _fast(host, port, payload):
    return share_natpmp.exchange(host, port, payload, delays=(0.05, 0.05))


class FakeRouter:
    """UDP-роутер на локальном порту: ``handler(пакет) -> ответ или None``; все запросы копятся в ``seen``."""

    def __init__(self, handler):
        self.handler, self.seen = handler, []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((HOST, 0))
        self.sock.settimeout(0.1)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                data, sender = self.sock.recvfrom(2048)
            except OSError:
                continue
            self.seen.append(data)
            reply = self.handler(data)
            if reply is not None:
                self.sock.sendto(reply, sender)

    def close(self):
        self._stop.set()
        self._thread.join(2)
        self.sock.close()


def _pcp_answer(data: bytes, *, outside=WHITE, result=0, port=None, nonce=None) -> bytes:
    """Ответ PCP MAP: заголовок 24 байта и тело 36."""
    request_nonce = data[24:36]
    internal, wanted = struct.unpack("!HH", data[40:44])
    lifetime = struct.unpack("!I", data[4:8])[0]
    header = struct.pack("!BBBBII", 2, 0x81, 0, result, lifetime, 1) + b"\x00" * 12
    mapped = b"\x00" * 10 + b"\xff\xff" + socket.inet_aton(outside)
    body = (nonce or request_nonce) + struct.pack("!B3xHH", 6, internal, port or wanted) + mapped
    return header + body


def _pmp_answer(data: bytes, *, outside=WHITE, result=0) -> bytes:
    if data[1] == 0:
        return struct.pack("!BBHI", 0, 128, result, 7) + socket.inet_aton(outside)
    internal, wanted, lifetime = struct.unpack("!HHI", data[4:12])
    return struct.pack("!BBHIHHI", 0, 130, result, 7, internal, wanted, lifetime)


def pcp_router(**kw):
    return FakeRouter(lambda data: _pcp_answer(data, **kw) if data[0] == 2 else None)


def natpmp_only_router(**kw):
    def handle(data):
        if data[0] == 2:  # PCP-запрос версии 2: роутер знает только NAT-PMP и отвечает «версия не поддерживается»
            return struct.pack("!BBHI", 0, 128, 1, 7)
        return _pmp_answer(data, **kw)

    return FakeRouter(handle)


def _open(router, **kw):
    return share_natpmp.try_open(
        [HOST], port=45123, send=_fast, udp_port=router.port, local_address=lambda _gateway: "192.168.1.20", **kw
    )


def test_pcp_router_opens_the_port_and_names_the_outside_address() -> None:
    router = pcp_router()
    try:
        mapping, via, gateway, why = _open(router)
    finally:
        router.close()
    assert (via, gateway, why) == ("pcp", HOST, "")
    assert mapping == Mapping(WHITE, 45123, "192.168.1.20", 45123)
    request = router.seen[0]
    assert len(request) == 60 and request[0] == 2 and request[1] == 1  # PCP MAP, версия 2
    assert struct.unpack("!B", request[36:37])[0] == 6  # протокол TCP
    assert socket.inet_ntoa(request[20:24]) == "192.168.1.20"  # адрес компьютера в запросе (IPv4 в IPv6-виде)


def test_router_with_only_natpmp_is_asked_the_second_way() -> None:
    router = natpmp_only_router()
    try:
        mapping, via, _gateway, why = _open(router)
    finally:
        router.close()
    assert (via, why) == ("natpmp", "")
    assert mapping == Mapping(WHITE, 45123, "192.168.1.20", 45123)


def test_silent_router_gives_the_word_silent_and_no_mapping() -> None:
    router = FakeRouter(lambda data: None)
    try:
        mapping, via, _gateway, why = _open(router)
    finally:
        router.close()
    assert mapping is None and via == "" and why == "silent"


def test_router_that_refuses_gives_its_reason() -> None:
    router = pcp_router(result=2)
    try:
        mapping, _via, _gateway, why = _open(router)
    finally:
        router.close()
    assert mapping is None and "не разрешает" in why


def test_answer_for_another_request_is_not_taken() -> None:
    router = pcp_router(nonce=b"x" * 12)
    try:
        mapping, _via, _gateway, why = _open(router)
    finally:
        router.close()
    assert mapping is None and "не к нашему" in why


def test_not_white_outside_address_is_rejected_and_the_port_is_closed_again() -> None:
    router = pcp_router(outside="100.64.3.7")
    try:
        mapping, _via, _gateway, why = _open(router)
        sent = list(router.seen)
    finally:
        router.close()
    assert mapping is None and "CGNAT" in why
    assert struct.unpack("!I", sent[-1][4:8])[0] == 0  # последний запрос — «аренда 0»: проброс снят


def test_unmap_uses_zero_lease_for_pcp_and_zero_external_port_for_natpmp() -> None:
    mapping = Mapping(WHITE, 45123, "192.168.1.20", 45123)
    pcp = pcp_router()
    pmp = natpmp_only_router()
    try:
        assert share_natpmp.unmap(HOST, mapping, "pcp", send=_fast, udp_port=pcp.port) is True
        assert struct.unpack("!I", pcp.seen[-1][4:8])[0] == 0
        assert share_natpmp.unmap(HOST, mapping, "natpmp", send=_fast, udp_port=pmp.port) is True
        internal, external, lifetime = struct.unpack("!HHI", pmp.seen[-1][4:12])
        assert (internal, external, lifetime) == (45123, 0, 0)
    finally:
        pcp.close()
        pmp.close()


WINDOWS_ROUTES = """
IPv4 Route Table
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0      192.168.1.254    192.168.1.20     35
          0.0.0.0          0.0.0.0        10.8.0.1         10.8.0.2      5
        127.0.0.0        255.0.0.0         On-link         127.0.0.1    331
"""


def test_gateways_come_from_the_route_table_by_metric_then_a_guess_in_each_subnet() -> None:
    found = share_natpmp.default_gateways(run_route=lambda: WINDOWS_ROUTES, lan_addresses=lambda: ["192.168.1.20", "172.20.0.5"])
    if share_natpmp.sys.platform == "win32":
        assert found[:2] == ["10.8.0.1", "192.168.1.254"]
    assert "192.168.1.1" in found and "172.20.0.1" in found
    assert len(found) <= share_natpmp.MAX_GATEWAYS


def test_a_public_gateway_address_is_never_asked() -> None:
    found = share_natpmp.default_gateways(
        run_route=lambda: "  0.0.0.0  0.0.0.0  8.8.8.8  8.8.8.9  10\n", lan_addresses=lambda: ["93.184.216.34"],
        linux_table=lambda: "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\neth0 00000000 08080808 0003 0 0 0 00000000 0 0 0\n",
    )
    assert "8.8.8.8" not in found and "93.184.216.1" not in found


def test_linux_table_is_read_as_little_endian_hex() -> None:
    table = "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\neth0 00000000 0101A8C0 0003 0 0 100 00000000 0 0 0\n"
    assert share_natpmp._linux_gateways(table) == [(100, "192.168.1.1")]


# ── PortMapper: UPnP, потом NAT-PMP/PCP, и слово-причина для счётчика ─────────────────

def _no_upnp():
    return None


def test_mapper_falls_back_to_pcp_when_upnp_is_absent_and_closes_it_on_stop() -> None:
    mapping = Mapping(WHITE, 45200, "192.168.1.20", 45200)
    closed = []
    mapper = share_portmap.PortMapper(45200, gateway_finder=_no_upnp, pmp_hosts=lambda: [HOST],
                                      pmp_open=lambda hosts, port: (mapping, "pcp", HOST, ""))
    import zapret_update.share_natpmp as natpmp

    original = natpmp.unmap
    natpmp.unmap = lambda host, found, via, **kw: closed.append((host, via)) or True
    try:
        assert mapper.start() == mapping
        assert (mapper.via, mapper.code, mapper.reason) == ("pcp", "via_pcp", "")
        mapper.stop()
    finally:
        natpmp.unmap = original
    assert closed == [(HOST, "pcp")]


def test_mapper_names_why_nothing_worked_in_one_word() -> None:
    both_silent = share_portmap.PortMapper(45201, gateway_finder=_no_upnp, pmp_hosts=lambda: [HOST],
                                           pmp_open=lambda hosts, port: (None, "", "", "silent"))
    assert both_silent.start() is None
    assert both_silent.code == "no_router" and "UPnP" in both_silent.reason and "NAT-PMP" in both_silent.reason
    refused = share_portmap.PortMapper(45202, gateway_finder=_no_upnp, pmp_hosts=lambda: [HOST],
                                       pmp_open=lambda hosts, port: (None, "", "", "роутер не разрешает открывать порты (NOT_AUTHORIZED)"))
    assert refused.start() is None
    assert refused.code == "refused" and "не разрешает" in refused.reason
    cgnat = share_portmap.PortMapper(45203, gateway_finder=_no_upnp, pmp_hosts=lambda: [HOST],
                                     pmp_open=lambda hosts, port: (None, "pcp", HOST, "внешний адрес роутера не белый: общий адрес провайдера (CGNAT)"))
    cgnat.start()
    assert cgnat.code == "cgnat"


def test_a_broken_second_way_never_breaks_the_first_report() -> None:
    def boom(hosts, port):
        raise RuntimeError("socket gone")

    mapper = share_portmap.PortMapper(45204, gateway_finder=_no_upnp, pmp_hosts=lambda: [HOST], pmp_open=boom)
    assert mapper.start() is None and mapper.code == "no_router"


@pytest.mark.parametrize("reason,code", [
    ("внешний адрес роутера не белый: общий адрес провайдера (CGNAT)", "cgnat"),
    ("внешний адрес роутера не белый: частный (за другим роутером или NAT провайдера)", "double_nat"),
    ("роутер отказался открыть порт (ответ 500)", "refused"),
    ("роутер не назвал внешний адрес", "no_external_ip"),
    ("роутер не ответил на просьбу открыть порт", "no_answer"),
    ("что-то новое", "other"),
])
def test_reason_words_map_to_stable_codes(reason, code) -> None:
    assert share_portmap.PortMapper._code_of(reason) == code
