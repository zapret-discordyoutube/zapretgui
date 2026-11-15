"""One JSON object per line over a Unix socket. Requests: ``{"op": "start" | "stop" | "status" | "cleanup", ...}``.
Answers: ``{"ok": true, ...}`` or ``{"ok": false, "error": "text"}``."""

from __future__ import annotations

import json
import socket

SOCKET_PATH = "/run/zapretgui/helper.sock"
SOCKET_GROUP = "zapretgui"
MAX_MESSAGE_BYTES = 1 << 20
OPERATIONS = frozenset({"start", "stop", "status", "cleanup"})


class ProtocolError(ValueError):
    pass


def encode(message: dict) -> bytes:
    data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(data) > MAX_MESSAGE_BYTES:
        raise ProtocolError("message is too large")
    return data


def read_message(sock: socket.socket) -> dict | None:
    """The next message; None when the other side closed without sending anything."""
    chunks: list[bytes] = []
    size = 0
    while True:
        byte = sock.recv(4096)
        if not byte:
            break
        chunks.append(byte)
        size += len(byte)
        if size > MAX_MESSAGE_BYTES:
            raise ProtocolError("message is too large")
        if b"\n" in byte:
            break
    raw = b"".join(chunks)
    if not raw.strip():
        return None
    line = raw.split(b"\n", 1)[0]
    try:
        message = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"not a JSON message: {exc}") from exc
    if not isinstance(message, dict):
        raise ProtocolError("a message must be a JSON object")
    return message


def answer_ok(**fields) -> dict:
    return {"ok": True, **fields}


def answer_error(text: str, **fields) -> dict:
    return {"ok": False, "error": str(text), **fields}
