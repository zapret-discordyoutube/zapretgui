"""Настройки MTProxy: секрет и ссылка tg://proxy.

Разбор заголовка клиента и шифры — в proxy/obfs.py.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

from telegram_proxy.proxy.fake_tls import build_fake_tls_secret


def normalize_secret(value: object) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 32:
        return ""
    if not all(ch in "0123456789abcdef" for ch in text):
        return ""
    return text


def generate_secret() -> str:
    return secrets.token_hex(16)


def build_mtproxy_link(host: str, port: int, secret: str, *, fake_tls_domain: str = "") -> str:
    normalized_secret = normalize_secret(secret)
    link_secret = f"dd{normalized_secret}"
    if fake_tls_domain:
        fake_tls_secret = build_fake_tls_secret(normalized_secret, fake_tls_domain)
        if fake_tls_secret.startswith("ee"):
            link_secret = fake_tls_secret
    query = urlencode(
        {
            "server": str(host or "127.0.0.1").strip() or "127.0.0.1",
            "port": str(int(port or 0)),
            "secret": link_secret,
        }
    )
    return f"tg://proxy?{query}"


__all__ = [
    "build_mtproxy_link",
    "generate_secret",
    "normalize_secret",
]
