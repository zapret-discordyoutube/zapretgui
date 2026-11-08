from __future__ import annotations

"""Связь между копиями по TLS: свой сертификат Ed25519, сосед по интернету отдаёт файл как по LAN."""

import hashlib
import os
import socket
import ssl

import pytest

from zapret_update import share_cert, share_discover, share_prefill, share_serve
from zapret_update.manifest import FileEntry
from zapret_update.plan import UpdatePlan, Want

BIG = 64 * 1024


def test_generated_certificate_is_a_valid_ed25519_x509_that_openssl_accepts() -> None:
    certificate, key = share_cert.make_self_signed(seed=bytes(range(32)), now=1_800_000_000)
    assert certificate.startswith(b"-----BEGIN CERTIFICATE-----") and key.startswith(b"-----BEGIN PRIVATE KEY-----")
    der = ssl.PEM_cert_to_DER_cert(certificate.decode())
    assert der[0] == 0x30 and len(der) < 400  # SEQUENCE; маленький сертификат
    context = share_cert.server_context()  # загрузка пары в OpenSSL: он проверяет, что ключ подходит к сертификату
    assert isinstance(context, ssl.SSLContext)


def test_every_certificate_is_different() -> None:
    first, _ = share_cert.make_self_signed()
    second, _ = share_cert.make_self_signed()
    assert first != second


def test_bad_seed_is_refused() -> None:
    with pytest.raises(ValueError):
        share_cert.make_self_signed(seed=b"short")


def _serve(tmp_path, *, tls: bool):
    data = os.urandom(BIG * 2)
    (tmp_path / "a.bin").write_bytes(data)
    entry = FileEntry("a.bin", len(data), hashlib.sha256(data).hexdigest(), len(data))
    server = share_serve.ShareServer(
        share_serve.InstalledFiles(tmp_path, [entry]), host="127.0.0.1", allow_loopback=True,
        ssl_context=share_cert.server_context() if tls else None,
    )
    server.start()
    return server, entry, data


def test_neighbour_over_tls_gives_a_verified_file(tmp_path) -> None:
    (tmp_path / "n").mkdir()
    server, entry, data = _serve(tmp_path / "n", tls=True)
    try:
        work = tmp_path / "work"
        neighbour = share_discover.Neighbour("127.0.0.1", server.port, tls=True)
        result = share_prefill.prefill_from_neighbours(
            UpdatePlan(version="x", want=[Want(entry)]), work_dir=work, neighbours=[neighbour]
        )
        assert result.files == 1 and (work / "ready" / entry.sha256).read_bytes() == data
    finally:
        server.stop()


def test_tls_server_does_not_answer_plain_http(tmp_path) -> None:
    (tmp_path / "n").mkdir()
    server, entry, _ = _serve(tmp_path / "n", tls=True)
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=3) as plain:
            plain.sendall(f"GET {share_serve.ROUTE}{entry.sha256} HTTP/1.0\r\n\r\n".encode())
            plain.settimeout(3)
            try:
                reply = plain.recv(200)
            except (TimeoutError, ConnectionError, OSError):
                reply = b""
        assert b"HTTP/1" not in reply and b"200" not in reply  # открытого HTTP-ответа нет
    finally:
        server.stop()


def test_client_without_tls_cannot_talk_to_a_tls_neighbour_and_it_is_dropped(tmp_path) -> None:
    (tmp_path / "n").mkdir()
    server, entry, _ = _serve(tmp_path / "n", tls=True)
    try:
        work = tmp_path / "work"
        plain = share_discover.Neighbour("127.0.0.1", server.port, tls=False)
        result = share_prefill.prefill_from_neighbours(
            UpdatePlan(version="x", want=[Want(entry)]), work_dir=work, neighbours=[plain], timeout=2
        )
        assert result.files == 0 and not (work / "ready" / entry.sha256).exists()
    finally:
        server.stop()


def test_slow_handshake_does_not_block_other_clients(tmp_path) -> None:
    (tmp_path / "n").mkdir()
    server, entry, data = _serve(tmp_path / "n", tls=True)
    try:
        idle = socket.create_connection(("127.0.0.1", server.port), timeout=3)  # молчит, рукопожатия нет
        try:
            work = tmp_path / "work"
            neighbour = share_discover.Neighbour("127.0.0.1", server.port, tls=True)
            result = share_prefill.prefill_from_neighbours(
                UpdatePlan(version="x", want=[Want(entry)]), work_dir=work, neighbours=[neighbour], timeout=5
            )
            assert result.files == 1
        finally:
            idle.close()
    finally:
        server.stop()
