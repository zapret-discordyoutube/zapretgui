"""Что файл hosts говорит об имени. Нужно, чтобы отличить «адрес задан в hosts» от подмены провайдером."""

from __future__ import annotations

import socket

from platforms import current as current_platform

__all__ = ["hosts_file_addresses", "hosts_file_ipv4"]


def hosts_file_addresses(name: str, *, path: str | None = None) -> tuple[str, ...]:
    """Все адреса (IPv4 и IPv6), которые файл hosts задаёт для имени, в порядке строк файла."""
    host = str(name or "").strip().rstrip(".").lower()
    if not host:
        return ()
    try:
        with open(path or current_platform().netprobe.hosts_file_path(), "r", encoding="utf-8", errors="ignore") as handle:
            lines = handle.readlines()
    except OSError:
        return ()

    found: list[str] = []
    for line in lines:
        content = line.split("#", 1)[0].split()
        if len(content) < 2:
            continue
        address, names = content[0], content[1:]
        try:
            socket.inet_pton(socket.AF_INET6 if ":" in address else socket.AF_INET, address)
        except OSError:
            continue
        if ":" not in address and address.count(".") != 3:
            continue
        if host in (item.rstrip(".").lower() for item in names) and address not in found:
            found.append(address)
    return tuple(found)


def hosts_file_ipv4(name: str, *, path: str | None = None) -> tuple[str, ...]:
    """IPv4-адреса, которые файл hosts задаёт для имени (без учёта регистра)."""
    return tuple(address for address in hosts_file_addresses(name, path=path) if ":" not in address)
