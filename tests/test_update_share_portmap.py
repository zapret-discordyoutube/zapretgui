from __future__ import annotations

"""Проброс порта на роутере (UPnP): настоящий обмен с поддельным роутером на loopback."""

import http.server
import re
import socket
import threading
import time

import pytest

from zapret_update import share_portmap

SERVICE = "urn:schemas-upnp-org:service:WANIPConnection:1"



def _NO_PMP(hosts, port):  # NAT-PMP в этих тестах не проверяется и в сеть не ходит
    return None, "", "", "silent"

def _description(control_url: str) -> bytes:
    return (
        '<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device><serviceList><service>'
        f"<serviceType>{SERVICE}</serviceType><controlURL>{control_url}</controlURL>"
        "</service></serviceList></device></root>"
    ).encode()


class FakeRouter:
    """SSDP-ответчик и HTTP-сторона роутера. ``wan`` — внешний адрес, который он назовёт."""

    def __init__(self, *, wan="93.184.216.34", description=None, location_host="127.0.0.1", refuse=False, only_st=None):
        self.wan, self.refuse, self.actions, self.args = wan, refuse, [], []
        self.only_st = only_st  # роутер отвечает только на этот вид поиска (как некоторые настоящие)
        self.description = description
        router = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(self):  # noqa: N802
                body = router.description if router.description is not None else _description("/ctl")
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):  # noqa: N802
                payload = self.rfile.read(int(self.headers["Content-Length"])).decode()
                action = self.headers["SOAPAction"].strip('"').split("#")[1]
                router.actions.append(action)
                router.args.append(dict(re.findall(r"<(New\w+)>([^<]*)</", payload)))
                code, body = 200, "<ok/>"
                if action == "GetExternalIPAddress":
                    body = f"<NewExternalIPAddress>{router.wan}</NewExternalIPAddress>"
                if action == "AddPortMapping" and router.refuse:
                    code = 500
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body.encode())

        self.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.ssdp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ssdp.bind(("127.0.0.1", 0))
        self.ssdp.settimeout(0.2)
        self.location = f"http://{location_host}:{self.http.server_address[1]}/desc.xml"
        self._stop = threading.Event()
        threading.Thread(target=self._answer, daemon=True).start()

    def _answer(self):
        while not self._stop.is_set():
            try:
                _data, sender = self.ssdp.recvfrom(1024)
                if self.only_st and f"ST: {self.only_st}".encode() not in _data:
                    continue
            except TimeoutError:
                continue
            except OSError:
                return
            reply = f"HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=120\r\nLOCATION: {self.location}\r\nST: x\r\n\r\n"
            self.ssdp.sendto(reply.encode(), sender)

    @property
    def target(self):
        return ("127.0.0.1", self.ssdp.getsockname()[1])

    def close(self):
        self._stop.set()
        self.http.shutdown()
        self.http.server_close()
        self.ssdp.close()


@pytest.fixture
def router():
    made = []

    def make(**kwargs):
        item = FakeRouter(**kwargs)
        made.append(item)
        return item

    yield make
    for item in made:
        item.close()


def _find(item, **kwargs):
    return share_portmap.find_gateway(timeout=1.5, target=item.target, source="127.0.0.1", allow_loopback=True, **kwargs)


def test_gateway_is_found_and_the_port_is_opened_for_this_computer(router):
    fake = router()
    gateway = _find(fake)
    assert gateway is not None and gateway.control_path == "/ctl" and gateway.service_type == SERVICE
    mapping = share_portmap.add_mapping(gateway, internal_ip="192.168.1.20", port=45000, lease=600)
    assert mapping == share_portmap.Mapping("93.184.216.34", 45000, "192.168.1.20", 45000)
    sent = fake.args[fake.actions.index("AddPortMapping")]
    assert sent["NewInternalClient"] == "192.168.1.20" and sent["NewInternalPort"] == "45000"
    assert sent["NewProtocol"] == "TCP" and sent["NewLeaseDuration"] == "600" and sent["NewRemoteHost"] == ""


@pytest.mark.parametrize("wan", ["192.168.0.1", "10.1.2.3", "100.64.5.5", "169.254.1.1", "not-an-ip"])
def test_provider_nat_or_private_wan_means_no_internet_sharing(router, wan):
    fake = router(wan=wan)
    gateway = _find(fake)
    assert gateway is not None
    assert share_portmap.add_mapping(gateway, internal_ip="192.168.1.20", port=45000) is None
    assert "AddPortMapping" not in fake.actions  # порт даже не просим открыть


def test_router_that_refuses_gives_none(router):
    gateway = _find(router(refuse=True))
    assert share_portmap.add_mapping(gateway, internal_ip="192.168.1.20", port=45000) is None


@pytest.mark.parametrize(
    "description",
    [
        b'<!DOCTYPE lolz [<!ENTITY a "aaaa">]><root>&a;</root>',
        b"not xml at all",
        b"<root/>" + b" " * (share_portmap.MAX_DESCRIPTION + 10),
        _description("http://10.9.9.9/ctl"),  # управление не на самом роутере
    ],
)
def test_hostile_or_broken_router_description_is_rejected(router, description):
    assert _find(router(description=description)) is None


def test_description_on_another_host_than_the_answerer_is_rejected(router):
    assert _find(router(location_host="localhost")) is None  # LOCATION называет не того, кто ответил


def test_no_router_gives_none_without_hanging():
    silent = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    silent.bind(("127.0.0.1", 0))
    try:
        began = time.monotonic()
        found = share_portmap.find_gateway(
            timeout=0.5, target=("127.0.0.1", silent.getsockname()[1]), source="127.0.0.1", allow_loopback=True
        )
        assert found is None and time.monotonic() - began < 3
    finally:
        silent.close()


def test_answer_from_a_non_lan_address_is_ignored(router):
    assert share_portmap.find_gateway(timeout=0.6, target=router().target, source="127.0.0.1") is None  # loopback не LAN


def test_mapper_keeps_the_mapping_and_removes_it_on_stop(router, monkeypatch):
    fake = router()
    monkeypatch.setattr(share_portmap, "local_address_towards", lambda gateway: "192.168.1.20")
    mapper = share_portmap.PortMapper(45001, renew_every=0.05, gateway_finder=lambda: _find(fake), pmp_open=_NO_PMP)
    mapping = mapper.start()
    assert mapping is not None and mapping.external_ip == "93.184.216.34"
    for _ in range(60):
        if fake.actions.count("AddPortMapping") >= 3:
            break
        time.sleep(0.05)
    assert fake.actions.count("AddPortMapping") >= 3  # аренда продлевается
    mapper.stop()
    assert fake.actions[-1] == "DeletePortMapping" and fake.args[-1]["NewExternalPort"] == "45001"


def test_mapper_without_a_router_does_nothing():
    mapper = share_portmap.PortMapper(45002, gateway_finder=lambda: None, pmp_open=_NO_PMP)
    assert mapper.start() is None
    mapper.stop()


@pytest.mark.parametrize(
    ("wan", "expected"),
    [
        ("93.184.216.34", "белый"),
        ("100.64.5.5", "CGNAT"),
        ("192.168.0.1", "частный"),
        ("10.1.2.3", "частный"),
        ("169.254.1.1", "служебный"),
        ("garbage", "неизвестен"),
        (None, "неизвестен"),
    ],
)
def test_address_kind_names_the_kind_but_never_the_address(wan, expected):
    kind = share_portmap.address_kind(wan)
    assert expected in kind and (wan is None or str(wan) not in kind)


def test_every_failure_has_its_own_reason(router, monkeypatch):
    monkeypatch.setattr(share_portmap, "local_address_towards", lambda gateway: "192.168.1.20")
    cases = {
        "refused": (dict(refuse=True), "отказался"),
        "cgnat": (dict(wan="100.64.1.1"), "CGNAT"),
        "private": (dict(wan="192.168.1.1"), "частный"),
    }
    for name, (kwargs, word) in cases.items():
        fake = router(**kwargs)
        mapper = share_portmap.PortMapper(45010, gateway_finder=lambda fake=fake: _find(fake), pmp_open=_NO_PMP)
        assert mapper.start() is None
        assert word in mapper.reason, (name, mapper.reason)
        assert "100.64.1.1" not in mapper.reason and "192.168.1.1" not in mapper.reason  # адрес в журнал не идёт
    assert share_portmap.PortMapper(45011, gateway_finder=lambda: None, pmp_open=_NO_PMP).start() is None
    silent = share_portmap.PortMapper(45012, gateway_finder=lambda: None, pmp_open=_NO_PMP)
    silent.start()
    assert "UPnP" in silent.reason
    ok = share_portmap.PortMapper(45013, gateway_finder=lambda: _find(router()), pmp_open=_NO_PMP)
    assert ok.start() is not None and ok.reason == ""
    ok.stop()


def test_router_answering_only_to_rootdevice_search_is_still_found(router):
    gateway = _find(router(only_st="upnp:rootdevice"))
    assert gateway is not None and gateway.control_path == "/ctl"


def test_search_goes_out_of_every_adapter_and_a_dead_one_does_not_stop_it(router):
    fake = router()
    gateway = share_portmap.find_gateway(
        timeout=1.5, target=fake.target, sources=["192.0.2.55", "127.0.0.1"], allow_loopback=True
    )
    assert gateway is not None  # первого адреса у компьютера нет (bind не вышел), второй работает


def test_search_without_any_usable_adapter_gives_none_quickly():
    began = time.monotonic()
    assert share_portmap.find_gateway(timeout=0.5, target=("127.0.0.1", 9), sources=["192.0.2.55"]) is None
    assert time.monotonic() - began < 2


def test_many_foreign_devices_do_not_make_the_search_endless(router):
    junk = [router(description=b"<root/>") for _ in range(share_portmap.MAX_DESCRIPTIONS + 3)]
    began = time.monotonic()
    assert share_portmap.find_gateway(
        timeout=2.0, target=junk[0].target, source="127.0.0.1", allow_loopback=True
    ) is None
    assert time.monotonic() - began < 8
