from __future__ import annotations

"""Раздача файлов соседям: только файлы списка, только свои, с ограничениями."""

import hashlib
import http.client
import os
from pathlib import Path

import pytest

from zapret_update import share_serve as serve
from zapret_update.manifest import FileEntry


def _make(tmp_path: Path, name: str, data: bytes) -> FileEntry:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return FileEntry(name, len(data), hashlib.sha256(data).hexdigest())


def _get(server: serve.ShareServer, sha: str, headers: dict | None = None, route: str = serve.ROUTE):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    connection.request("GET", route + sha, headers=headers or {})
    response = connection.getresponse()
    return response.status, response.read(), response


@pytest.fixture
def served(tmp_path):
    data = os.urandom(5000)
    entry = _make(tmp_path, "_internal/app.bin", data)
    server = serve.ShareServer(serve.InstalledFiles(tmp_path, [entry]), host="127.0.0.1", allow_loopback=True)
    server.start()
    yield server, entry, data, tmp_path
    server.stop()


def test_serves_listed_file_and_resumes_by_range(served):
    server, entry, data, _ = served
    status, body, _ = _get(server, entry.sha256)
    assert (status, body) == (200, data)
    status, body, response = _get(server, entry.sha256, {"Range": "bytes=1000-"})
    assert (status, body) == (206, data[1000:])
    assert response.getheader("Content-Range") == f"bytes 1000-{len(data) - 1}/{len(data)}"


@pytest.mark.parametrize(
    "name",
    ["../../etc/passwd", "..%2f..%2fetc%2fpasswd", "a" * 63, "A" * 64, "g" * 64, "", "_internal/app.bin"],
)
def test_bad_names_are_not_found(served, name):
    server, *_ = served
    assert _get(server, name)[0] == 404


def test_unlisted_sha_is_not_found(served):
    server, *_ = served
    assert _get(server, hashlib.sha256(b"not listed").hexdigest())[0] == 404


def test_file_changed_on_disk_is_not_served(served):
    server, entry, data, root = served
    (root / "_internal/app.bin").write_bytes(b"x" * len(data))  # тот же размер, другой текст
    assert _get(server, entry.sha256)[0] == 404


def test_range_past_the_end_is_refused(served):
    server, entry, data, _ = served
    assert _get(server, entry.sha256, {"Range": f"bytes={len(data) + 1}-"})[0] == 416


def test_other_route_is_not_found(served):
    server, entry, *_ = served
    assert _get(server, entry.sha256, route="/other/")[0] == 404


def test_loopback_is_refused_unless_allowed(tmp_path):
    entry = _make(tmp_path, "a.bin", b"data")
    server = serve.ShareServer(serve.InstalledFiles(tmp_path, [entry]), host="127.0.0.1")
    server.start()
    try:
        assert _get(server, entry.sha256)[0] == 403
    finally:
        server.stop()


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("192.168.1.20", True),
        ("10.10.10.40", True),
        ("172.16.5.5", True),
        ("169.254.3.4", True),
        ("fe80::1%eth0", True),
        ("::ffff:192.168.0.9", True),
        ("8.8.8.8", False),
        ("100.64.1.1", False),  # общий адрес провайдера — не локальная сеть
        ("2a00:1450::1", False),
        ("127.0.0.1", False),
        ("not an address", False),
    ],
)
def test_lan_address_policy(address, expected):
    assert serve.is_lan_address(address) is expected


def test_only_a_limited_number_of_streams(tmp_path):
    entry = _make(tmp_path, "big.bin", os.urandom(64 * 1024 * 1024))
    server = serve.ShareServer(
        serve.InstalledFiles(tmp_path, [entry]), host="127.0.0.1", allow_loopback=True, max_streams=1
    )
    server.start()
    try:
        first = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        first.request("GET", serve.ROUTE + entry.sha256)
        response = first.getresponse()
        response.read(10)  # поток занят: остальное не вычитано
        assert _get(server, entry.sha256)[0] == 503
        first.close()
    finally:
        server.stop()
