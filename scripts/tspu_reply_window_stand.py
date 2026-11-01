"""Стенд: подражание фильтру ТСПУ «TLS с адреса режут по ответу сервера, но только в пределах окна».

Только для хоста Proxmox, на котором живёт виртуальная машина win10, и только от root. Нужен,
чтобы проверить BlockCheck (пробу малого окна, ``diagnostics.block_cause``) и подбор стратегии с
``wssize`` от начала до конца, не имея линии с настоящим фильтром.

Как устроен настоящий фильтр (вики: DPI/tspu-tls-ip-filter-tcp-window-october-2026): для адресов
из списка он узнаёт TLS по первым трём байтам ответа сервера (``16 03 0x``), но читает ответ только
в пределах окна приёма, которое объявил клиент. Окно в 1–2 байта он не может разобрать и пропускает
соединение; иначе соединение глохнет.

Что делает стенд:

- своя таблица nftables ``inet tspu_reply_stand`` на пересылке хоста, только трафик ``--client`` ↔
  ``--server`` по TCP 443, через очередь ``--queue`` с ``bypass``: если программа упала, пакеты идут
  как обычно;
- по пакетам клиента помнит его окно (множитель из SYN, окно из последнего пакета);
- первый пакет ответа сервера с данными решает судьбу соединения: видно 3 байта и больше и это
  начало записи TLS — соединение глушится до конца, иначе — пропускается;
- ``--blind`` — контроль без учёта окна: любой ответ, начинающийся с ``16 03``, глушится;
- при выходе (Ctrl+C, SIGTERM, ``--seconds``) таблица удаляется.

Пример::

    sudo python3 scripts/tspu_reply_window_stand.py --server 74.125.110.106 --seconds 900

Это модель по описанию фильтра, а не сам фильтр: вывод «работает у пользователя» по стенду делать
нельзя, только «механика пробы и подбора верна».
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import ipaddress
import signal
import socket
import struct
import subprocess
import sys
import time

TABLE = "tspu_reply_stand"
NF_DROP, NF_ACCEPT = 0, 1
NFQNL_COPY_PACKET = 2
TLS_RECORD_START = (0x14, 0x15, 0x16, 0x17)


def nft(script: str) -> None:
    subprocess.run(["nft", "-f", "-"], input=script.encode(), check=True)


def install(client: str, servers: list[str], queue: int) -> None:
    elements = ", ".join(servers)
    nft(
        f"""
table inet {TABLE} {{
  set servers {{ type ipv4_addr; elements = {{ {elements} }} }}
  chain forward {{
    type filter hook forward priority -5; policy accept;
    ip saddr {client} ip daddr @servers tcp dport 443 queue flags bypass to {queue}
    ip daddr {client} ip saddr @servers tcp sport 443 queue flags bypass to {queue}
  }}
}}
"""
    )


def uninstall() -> None:
    subprocess.run(["nft", "delete", "table", "inet", TABLE], check=False, stderr=subprocess.DEVNULL)


def window_scale(options: bytes) -> int | None:
    index = 0
    while index < len(options):
        kind = options[index]
        if kind == 0:
            break
        if kind == 1:
            index += 1
            continue
        if index + 1 >= len(options):
            break
        length = options[index + 1]
        if kind == 3 and length == 3 and index + 2 < len(options):
            return options[index + 2]
        index += max(length, 2)
    return None


class Flow:
    __slots__ = ("scale", "ack", "window", "verdict")

    def __init__(self) -> None:
        self.scale = 0
        self.ack = None
        self.window = None
        self.verdict = ""


class Filter:
    def __init__(self, client: str, *, blind: bool) -> None:
        self.client = socket.inet_aton(client)
        self.blind = blind
        self.flows: dict[tuple[int, bytes], Flow] = {}
        self.stats = {"blocked": 0, "passed": 0}

    def judge(self, packet: bytes) -> int:
        if len(packet) < 40 or packet[0] >> 4 != 4 or packet[9] != 6:
            return NF_ACCEPT
        ihl = (packet[0] & 15) * 4
        tcp = packet[ihl:]
        sport, dport, seq, ack = struct.unpack("!HHII", tcp[:12])
        offset = (tcp[12] >> 4) * 4
        flags = tcp[13]
        window = struct.unpack("!H", tcp[14:16])[0]
        data = tcp[offset:]
        syn = bool(flags & 0x02)
        if packet[12:16] == self.client:
            flow = self.flows.setdefault((sport, packet[16:20]), Flow())
            if syn:
                flow.__init__()
                flow.scale = window_scale(tcp[20:offset]) or 0
                flow.window = window  # в SYN окно без множителя
            else:
                flow.window = window << flow.scale
                flow.ack = ack
            return NF_ACCEPT
        flow = self.flows.get((dport, packet[12:16]))
        if flow is None:
            return NF_ACCEPT
        if syn or not data:
            return NF_ACCEPT
        if flow.verdict == "blocked":
            return NF_DROP
        if flow.verdict == "passed":
            return NF_ACCEPT
        is_tls = len(data) >= 2 and data[0] in TLS_RECORD_START and data[1] == 0x03
        if self.blind:
            visible = len(data)
        elif flow.ack is None or flow.window is None:
            visible = len(data)
        else:
            room = (flow.ack + flow.window - seq) & 0xFFFFFFFF
            visible = 0 if room >= 1 << 31 else min(len(data), room)
        if visible >= 3 and is_tls:
            flow.verdict = "blocked"
            self.stats["blocked"] += 1
            print(f"blocked: client port {dport}, window {flow.window}, visible {visible}", flush=True)
            return NF_DROP
        flow.verdict = "passed"
        self.stats["passed"] += 1
        print(f"passed: client port {dport}, window {flow.window}, visible {visible}", flush=True)
        return NF_ACCEPT


class PacketHeader(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("packet_id", ctypes.c_uint32), ("hw_protocol", ctypes.c_uint16), ("hook", ctypes.c_uint8)]


def run(queue: int, verdict_of, seconds: float) -> None:
    lib = ctypes.CDLL(ctypes.util.find_library("netfilter_queue") or "libnetfilter_queue.so.1", use_errno=True)
    lib.nfq_open.restype = ctypes.c_void_p
    lib.nfq_create_queue.restype = ctypes.c_void_p
    lib.nfq_get_msg_packet_hdr.restype = ctypes.POINTER(PacketHeader)
    lib.nfq_bind_pf.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
    lib.nfq_fd.argtypes = [ctypes.c_void_p]
    lib.nfq_set_mode.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint32]
    lib.nfq_handle_packet.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    lib.nfq_get_payload.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_char))]
    lib.nfq_get_msg_packet_hdr.argtypes = [ctypes.c_void_p]
    lib.nfq_set_verdict.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    lib.nfq_destroy_queue.argtypes = [ctypes.c_void_p]
    lib.nfq_close.argtypes = [ctypes.c_void_p]

    callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    lib.nfq_create_queue.argtypes = [ctypes.c_void_p, ctypes.c_uint16, callback_type, ctypes.c_void_p]

    def on_packet(queue_handle, _message, nfa, _data):
        header = lib.nfq_get_msg_packet_hdr(nfa)
        packet_id = socket.ntohl(header.contents.packet_id)
        pointer = ctypes.POINTER(ctypes.c_char)()
        length = lib.nfq_get_payload(nfa, ctypes.byref(pointer))
        packet = ctypes.string_at(pointer, length) if length > 0 else b""
        try:
            verdict = verdict_of(packet)
        except Exception as error:  # стенд не должен глушить трафик из-за своей ошибки
            print(f"error: {error}", file=sys.stderr, flush=True)
            verdict = NF_ACCEPT
        return lib.nfq_set_verdict(queue_handle, packet_id, verdict, 0, None)

    callback = callback_type(on_packet)
    handle = lib.nfq_open()
    if not handle:
        raise OSError("nfq_open failed")
    lib.nfq_bind_pf(handle, socket.AF_INET)
    queue_handle = lib.nfq_create_queue(handle, queue, callback, None)
    if not queue_handle:
        lib.nfq_close(handle)
        raise OSError(f"queue {queue} is busy")
    lib.nfq_set_mode(queue_handle, NFQNL_COPY_PACKET, 0xFFFF)
    fd = lib.nfq_fd(handle)
    sock = socket.socket(fileno=fd)
    sock.settimeout(1.0)
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            try:
                data = sock.recv(65536)
            except TimeoutError:
                continue
            lib.nfq_handle_packet(handle, data, len(data))
    finally:
        sock.detach()
        lib.nfq_destroy_queue(queue_handle)
        lib.nfq_close(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--client", default="10.20.0.20", help="адрес win10 за NAT хоста")
    parser.add_argument("--server", action="append", required=True, help="адрес сервера под фильтром (можно несколько)")
    parser.add_argument("--queue", type=int, default=4242)
    parser.add_argument("--seconds", type=float, default=900.0, help="сколько работать; затем таблица удаляется")
    parser.add_argument("--blind", action="store_true", help="контроль: глушить ответ TLS без учёта окна")
    args = parser.parse_args()
    servers = [str(ipaddress.IPv4Address(item)) for item in args.server]
    ipaddress.IPv4Address(args.client)

    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    flt = Filter(args.client, blind=args.blind)
    uninstall()
    install(args.client, servers, args.queue)
    print(f"stand up: {args.client} <-> {', '.join(servers)}, queue {args.queue}, blind={args.blind}", flush=True)
    try:
        run(args.queue, flt.judge, args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        uninstall()
        print(f"stand down: {flt.stats}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
