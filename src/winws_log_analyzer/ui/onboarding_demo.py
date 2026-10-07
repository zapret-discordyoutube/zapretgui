"""Пример разбора debug-лога для экскурсии.

Пока своего лога нет, таблицы страницы пустые, и объяснить по ним нечего.
Экскурсия на время своих шагов кладёт в них этот вымышленный разбор. Файла
за ним нет, и никуда он не записывается.
"""

from __future__ import annotations

from winws_log_analyzer.models import (
    VERDICT_DROP,
    VERDICT_MODIFIED,
    VERDICT_UNMODIFIED,
    ConnectionRecord,
    PacketRecord,
    WinwsLogParseResult,
)


def _packet(packet_id: int, direction: str, length: int, flags: str, verdict: str, **extra) -> PacketRecord:
    return PacketRecord(
        packet_id=packet_id,
        line_no=packet_id * 12 + 1,
        direction=direction,
        length=length,
        ip_version=4,
        proto="tcp",
        tcp_flags=flags,
        verdict=verdict,
        **extra,
    )


def demo_parse_result() -> WinwsLogParseResult:
    discord_profile = {"profile_id": 3, "profile_name": "discord"}
    discord_packets = [
        _packet(1, "out", 52, "S", VERDICT_UNMODIFIED, **discord_profile),
        _packet(2, "in", 52, "SA", VERDICT_UNMODIFIED, **discord_profile),
        _packet(3, "out", 40, "A", VERDICT_UNMODIFIED, **discord_profile),
        _packet(
            4,
            "out",
            557,
            "AP",
            VERDICT_MODIFIED,
            l7proto="tls",
            hostname="discord.com",
            payload_type="tls_client_hello",
            lua_applied=("multisplit",),
            tls_details=("TLS 1.3", "SNI discord.com"),
            **discord_profile,
        ),
        _packet(5, "in", 1400, "A", VERDICT_UNMODIFIED, l7proto="tls", payload_type="tls_server_hello", **discord_profile),
        _packet(6, "out", 104, "AP", VERDICT_UNMODIFIED, l7proto="tls", **discord_profile),
    ]
    connections = [
        ConnectionRecord(
            proto="tcp",
            remote_ip="162.159.137.232",
            remote_port=443,
            hostname="discord.com",
            l7proto="tls",
            profile_ids=(3,),
            profile_names=("discord",),
            packets_total=len(discord_packets),
            packets_out=4,
            packets_in=2,
            verdict_counts={VERDICT_MODIFIED: 1, VERDICT_UNMODIFIED: 5},
            positive_lists=("discord.txt",),
            lua_applied=("multisplit",),
            packets=discord_packets,
            first_line_no=13,
        ),
        ConnectionRecord(
            proto="udp",
            remote_ip="142.250.74.110",
            remote_port=443,
            hostname="www.youtube.com",
            l7proto="quic",
            profile_ids=(2,),
            profile_names=("youtube quic",),
            packets_total=4,
            packets_out=3,
            packets_in=1,
            verdict_counts={VERDICT_MODIFIED: 1, VERDICT_UNMODIFIED: 3},
            positive_lists=("youtube.txt",),
            lua_applied=("fake",),
        ),
        ConnectionRecord(
            proto="tcp",
            remote_ip="5.255.255.242",
            remote_port=443,
            hostname="ya.ru",
            l7proto="tls",
            profile_ids=(0,),
            profile_names=("no_action",),
            packets_total=5,
            packets_out=3,
            packets_in=2,
            verdict_counts={VERDICT_UNMODIFIED: 5},
        ),
        ConnectionRecord(
            proto="udp",
            remote_ip="185.26.182.94",
            remote_port=50001,
            profile_ids=(7,),
            profile_names=("discord voice",),
            packets_total=2,
            packets_out=2,
            packets_in=0,
            verdict_counts={VERDICT_DROP: 1, VERDICT_UNMODIFIED: 1},
            positive_lists=("ipset-discord.txt",),
        ),
    ]
    return WinwsLogParseResult(
        profiles={0: "no_action", 2: "youtube quic: fake", 3: "discord: multisplit", 7: "discord voice: fake"},
        packets_total=sum(connection.packets_total for connection in connections),
        connections=connections,
        positive_checks_total=3,
    )


__all__ = ["demo_parse_result"]
