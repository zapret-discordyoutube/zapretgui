"""Адресная книга соседей по интернету и обмен адресами между ними.

Чтобы раздачу нельзя было запретить одним росчерком, у неё нет единственного
адреса. Откуда программа берёт соседей, по порядку:

1. книга на диске (``peers.json``): те, кто отвечал раньше;
2. обмен адресами: известный сосед отвечает списком других известных ему
   (``GET /zapret-share/1/peers``);
3. сервер очереди обновлений, который программа и так знает (см. ``share_registry``).

Книге не верят на слово: в неё попадает только «белый» адрес с настоящим
портом; запись, которая не отвечает или обманывает, быстро вычёркивается;
книга ограничена по размеру, а файл читается с проверкой каждой записи.

Только стандартная библиотека.
"""

from __future__ import annotations

import ipaddress
import json
import os
import random
import threading
import time
from collections.abc import Callable
from pathlib import Path

PEERS_ROUTE = "/zapret-share/1/peers"
MAX_PEERS = 200
MAX_FILE_BYTES = 64 * 1024
MAX_SHARED = 20
STRIKES_TO_DROP = 3
FORGET_AFTER = 30 * 86400


def clean_peer(host: object, port: object, *, allow_local: bool = False) -> tuple[str, int] | None:
    """Приводит адрес к виду книги или ``None``: не «белый» адрес и плохой порт не берём."""
    if not isinstance(host, str) or isinstance(port, bool) or not isinstance(port, int):
        return None
    if not 1024 <= port <= 65535:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return (str(address), port) if address.is_global or (allow_local and (address.is_loopback or address.is_private)) else None


class PeerBook:
    def __init__(
        self,
        path: Path | None = None,
        *,
        now: Callable[[], float] = time.time,
        rng: random.Random | None = None,
        allow_local: bool = False,
    ):
        self._allow_local = allow_local  # только для тестов: в жизни адрес должен быть «белым»
        self._path = Path(path) if path is not None else None
        self._now, self._rng = now, rng or random.Random()
        self._lock = threading.Lock()
        # (host, port) -> {"ok": время последнего ответа, "seen": время добавления, "strikes": число неудач}
        self._items: dict[tuple[str, int], dict[str, float]] = {}
        self._load()

    # ── чтение и запись файла ──────────────────────────────────────────

    def _load(self) -> None:
        if self._path is None:
            return
        try:
            if self._path.stat().st_size > MAX_FILE_BYTES:
                return
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(loaded, list):
            return
        for item in loaded[:MAX_PEERS]:
            if not isinstance(item, list) or len(item) != 5:
                continue
            key = clean_peer(item[0], item[1], allow_local=self._allow_local)
            if key is None or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in item[2:]):
                continue
            self._items[key] = {"ok": float(item[2]), "seen": float(item[3]), "strikes": float(item[4])}
        self._forget_old()

    def save(self) -> None:
        if self._path is None:
            return
        with self._lock:
            rows = [[h, p, v["ok"], v["seen"], v["strikes"]] for (h, p), v in self._items.items()]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(self._path.name + ".tmp")
        temporary.write_text(json.dumps(rows), encoding="utf-8")
        os.replace(temporary, self._path)

    # ── книга ──────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._items)

    def _forget_old(self) -> None:
        horizon = self._now() - FORGET_AFTER
        for key in [k for k, v in self._items.items() if max(v["ok"], v["seen"]) < horizon]:
            del self._items[key]

    def add(self, host: object, port: object) -> bool:
        """Новый адрес в книгу; ``True`` — принят (или уже был)."""
        key = clean_peer(host, port, allow_local=self._allow_local)
        if key is None:
            return False
        with self._lock:
            if key in self._items:
                return True
            if len(self._items) >= MAX_PEERS:
                # Место нужно: уходит тот, кто хуже всех (больше неудач, дольше молчит).
                worst = max(self._items, key=lambda k: (self._items[k]["strikes"], -self._items[k]["ok"], -self._items[k]["seen"]))
                del self._items[worst]
            self._items[key] = {"ok": 0.0, "seen": self._now(), "strikes": 0.0}
        return True

    def good(self, host: str, port: int) -> None:
        with self._lock:
            item = self._items.get((host, port))
            if item is not None:
                item["ok"], item["strikes"] = self._now(), 0.0

    def bad(self, host: str, port: int) -> None:
        with self._lock:
            item = self._items.get((host, port))
            if item is None:
                return
            item["strikes"] += 1
            if item["strikes"] >= STRIKES_TO_DROP:
                del self._items[(host, port)]

    def pick(self, count: int, exclude: set[tuple[str, int]] | None = None) -> list[tuple[str, int]]:
        """Кого спросить: сначала недавно ответившие, потом остальные; внутри групп вразнобой."""
        skip = exclude or set()
        with self._lock:
            known = [k for k in self._items if k not in skip]
            answered = [k for k in known if self._items[k]["ok"] > 0]
            rest = [k for k in known if self._items[k]["ok"] <= 0]
        answered.sort(key=lambda k: -self._items[k]["ok"])
        head, tail = answered[: max(count * 2, count)], rest
        self._rng.shuffle(head)
        self._rng.shuffle(tail)
        return (head + tail)[:count]

    def sample(self, count: int = MAX_SHARED) -> list[tuple[str, int]]:
        """Что отдать соседу при обмене адресами: только те, кто хоть раз ответил."""
        with self._lock:
            answered = [k for k, v in self._items.items() if v["ok"] > 0 and v["strikes"] == 0]
        self._rng.shuffle(answered)
        return answered[: min(count, MAX_SHARED)]


def parse_peer_reply(raw: bytes, *, allow_local: bool = False) -> tuple[list[tuple[str, int]], str, str]:
    """Адреса, продукт и версия из ответа соседа; всё лишнее и нечестное отбрасывается."""
    if len(raw) > MAX_FILE_BYTES:
        return [], "", ""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return [], "", ""
    items = data.get("peers") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return [], "", ""
    found: list[tuple[str, int]] = []
    for entry in items[: MAX_SHARED * 2]:
        if isinstance(entry, list) and len(entry) == 2:
            key = clean_peer(entry[0], entry[1], allow_local=allow_local)
            if key is not None and key not in found:
                found.append(key)
    product, version = data.get("product"), data.get("version")
    return (
        found[:MAX_SHARED],
        product[:64] if isinstance(product, str) else "",
        version[:64] if isinstance(version, str) else "",
    )


def parse_peer_list(raw: bytes, *, allow_local: bool = False) -> list[tuple[str, int]]:
    return parse_peer_reply(raw, allow_local=allow_local)[0]


def encode_peer_list(peers: list[tuple[str, int]], *, product: str = "", version: str = "") -> bytes:
    body: dict = {"peers": [[h, p] for h, p in peers[:MAX_SHARED]]}
    if product or version:
        body.update(product=product, version=version)
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


__all__ = [
    "MAX_PEERS", "MAX_SHARED", "PEERS_ROUTE", "PeerBook", "clean_peer", "encode_peer_list", "parse_peer_list",
    "parse_peer_reply",
]
