"""Одноразовый самоподписанный сертификат для связи между копиями программы.

Передача файлов между компьютерами в интернете идёт по TLS, чтобы со стороны
это был обычный зашифрованный HTTPS, а не открытый HTTP с узнаваемыми словами
в запросе. Сертификат здесь не доказывает ничего: сосед может быть кем угодно.
Доверие даёт только SHA-256 файла из подписанного списка выпуска, поэтому
клиент сертификат не проверяет (``client_context``).

Сертификат Ed25519 собирается вручную в формате DER (X.509 v3) и подписывается
нашим чистым Python-кодом ``ed25519``: сторонних библиотек у программ нет.
Каждый запуск раздачи делает новый: имя и срок случайные, чтобы у разных
компьютеров не было общего отпечатка.

Только стандартная библиотека.
"""

from __future__ import annotations

import base64
import os
import secrets
import ssl
import tempfile
import time
from pathlib import Path

from .ed25519 import public_key_from_seed, sign_ed25519

_OID_ED25519 = bytes.fromhex("06032b6570")  # 1.3.101.112
_OID_COMMON_NAME = bytes.fromhex("0603550403")  # 2.5.4.3
_DAY = 86400


def _der(tag: int, body: bytes) -> bytes:
    size = len(body)
    if size < 0x80:
        head = bytes([tag, size])
    else:
        raw = size.to_bytes((size.bit_length() + 7) // 8, "big")
        head = bytes([tag, 0x80 | len(raw)]) + raw
    return head + body


def _utc_time(moment: int) -> bytes:
    return _der(0x17, time.strftime("%y%m%d%H%M%SZ", time.gmtime(moment)).encode("ascii"))


def _pem(label: str, der: bytes) -> bytes:
    text = base64.b64encode(der).decode("ascii")
    lines = [text[i : i + 64] for i in range(0, len(text), 64)]
    return ("-----BEGIN %s-----\n%s\n-----END %s-----\n" % (label, "\n".join(lines), label)).encode("ascii")


def make_self_signed(*, seed: bytes | None = None, now: int | None = None) -> tuple[bytes, bytes]:
    """PEM сертификата и PEM закрытого ключа (PKCS#8)."""
    seed = bytes(seed) if seed is not None else secrets.token_bytes(32)
    if len(seed) != 32:
        raise ValueError("seed должен быть 32 байта")
    moment = int(time.time() if now is None else now)
    public = public_key_from_seed(seed)
    name = _der(0x30, _der(0x31, _der(0x30, _OID_COMMON_NAME + _der(0x0C, secrets.token_hex(8).encode("ascii")))))
    # Срок начинается в прошлом на случайное число суток: у разных компьютеров он разный.
    start = moment - (1 + secrets.randbelow(60)) * _DAY
    end = moment + (180 + secrets.randbelow(400)) * _DAY
    algorithm = _der(0x30, _OID_ED25519)
    serial = _der(0x02, b"\x01" + secrets.token_bytes(15))  # старший бит 0: число положительное
    tbs = _der(
        0x30,
        _der(0xA0, _der(0x02, b"\x02"))  # версия v3
        + serial
        + algorithm
        + name
        + _der(0x30, _utc_time(start) + _utc_time(end))
        + name
        + _der(0x30, algorithm + _der(0x03, b"\x00" + public)),
    )
    signature = sign_ed25519(seed, tbs)
    certificate = _der(0x30, tbs + algorithm + _der(0x03, b"\x00" + signature))
    key = _der(0x30, _der(0x02, b"\x00") + algorithm + _der(0x04, _der(0x04, seed)))
    return _pem("CERTIFICATE", certificate), _pem("PRIVATE KEY", key)


def server_context() -> ssl.SSLContext:
    """Контекст сервера с новым самоподписанным сертификатом."""
    certificate, key = make_self_signed()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # ``load_cert_chain`` читает только файлы: пишем во временную папку и сразу стираем.
    with tempfile.TemporaryDirectory(prefix="zs-") as directory:
        cert_path, key_path = Path(directory) / "c.pem", Path(directory) / "k.pem"
        for path, data in ((key_path, key), (cert_path, certificate)):
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
        context.load_cert_chain(cert_path, key_path)
    return context


def client_context() -> ssl.SSLContext:
    """Клиент не проверяет сертификат: доверие даёт SHA-256 файла, а не сосед."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


__all__ = ["client_context", "make_self_signed", "server_context"]
