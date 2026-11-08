"""Файлы новой версии у соседей: кладём в ``ready/`` до обычного скачивания.

``zapret_update.fetch.fetch_plan`` не качает файл, который уже лежит в
``<рабочая папка>/ready/<sha256>`` с верным размером и суммой. Поэтому соседей
подключаем без правки общего ядра: сначала сюда, потом ``fetch_plan`` берёт
с серверов всё, чего не хватило.

Сосед отдаёт файл целиком (как он лежит у него на диске), а не сжатый кусок
хранилища. Берём у него не всё подряд:

- мелочь (меньше ``MIN_SIZE``) — запрос стоит дороже файла;
- если есть заплатка и она сильно меньше файла (``PATCH_GAIN``), заплатка с
  сервера выгоднее: ``Zapret.exe`` в 85 МБ обновляется заплаткой в 2 МБ.

Сосед не обязан быть честным: файл принимается, только если его размер и
SHA-256 совпали со списком выпуска. Обман, обрыв и молчание не ломают
обновление — сосед вычёркивается, файл берётся с сервера.

Только стандартная библиотека.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import ssl
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .plan import UpdatePlan, Want

from .share_discover import Neighbour
from .share_serve import ROUTE

MIN_SIZE = 16 * 1024
PATCH_GAIN = 4
STALL_TIMEOUT = 3.0
STRIKES_TO_DROP = 2
THREADS = 2
CHUNK = 256 * 1024


@dataclass
class PrefillResult:
    files: int = 0
    bytes: int = 0


class _Mismatch(Exception):
    """Сосед прислал не то, что в списке выпуска."""


class _Absent(Exception):
    """У соседа этого файла нет (или он занят): без наказания."""


def worth_asking(want: Want) -> bool:
    entry = want.entry
    if entry.size < MIN_SIZE:
        return False
    if want.patch is not None and entry.size > want.patch.size * PATCH_GAIN:
        return False
    return True


class _Neighbours:
    def __init__(self, neighbours: Sequence[Neighbour]) -> None:
        self._items = list(neighbours)
        self._strikes = {item: 0 for item in self._items}
        self._lock = threading.Lock()

    def alive(self) -> list[Neighbour]:
        with self._lock:
            return [item for item in self._items if self._strikes[item] < STRIKES_TO_DROP]

    def strike(self, item: Neighbour, *, heavy: bool = False) -> None:
        with self._lock:
            self._strikes[item] = STRIKES_TO_DROP if heavy else self._strikes[item] + 1


def _download(neighbour: Neighbour, want: Want, part: Path, timeout: float, cancelled: Callable[[], bool]) -> None:
    entry = want.entry
    if neighbour.tls:
        from .share_cert import client_context

        connection = http.client.HTTPSConnection(neighbour.host, neighbour.port, timeout=timeout, context=client_context())
    else:
        connection = http.client.HTTPConnection(neighbour.host, neighbour.port, timeout=timeout)
    try:
        connection.request("GET", ROUTE + entry.sha256, headers={"User-Agent": "ZapretShare/1"})
        response = connection.getresponse()
        if response.status in (404, 503):
            raise _Absent
        if response.status != 200:
            raise _Mismatch(f"ответ {response.status}")
        if response.getheader("Content-Length") != str(entry.size):
            raise _Mismatch("размер не тот, что в списке выпуска")
        # Длину не превысить: http.client читает ровно Content-Length байт.
        digest, have = hashlib.sha256(), 0
        with open(part, "wb") as handle:
            while chunk := response.read(CHUNK):
                if cancelled():
                    raise _Absent
                have += len(chunk)
                digest.update(chunk)
                handle.write(chunk)
        if have != entry.size or digest.hexdigest() != entry.sha256:
            raise _Mismatch("содержимое не совпало с суммой из списка выпуска")
    finally:
        connection.close()


def prefill_from_neighbours(
    plan: UpdatePlan,
    *,
    work_dir: Path,
    neighbours: Sequence[Neighbour],
    timeout: float = STALL_TIMEOUT,
    cancelled: Callable[[], bool] = lambda: False,
    threads: int = THREADS,
    everything: bool = False,
    activity=None,
) -> PrefillResult:
    """Кладёт в ``ready/`` всё, что нашлось у соседей; остальное — забота ``fetch_plan``.

    ``everything`` — просить и то, что обычно выгоднее взять с сервера (мелочь, файлы с
    маленькой заплаткой): так делают, когда серверов нет и файл нужен любой ценой.
    """
    result = PrefillResult()
    if not neighbours:
        return result
    ready_dir, parts_dir = Path(work_dir) / "ready", Path(work_dir) / "parts"
    ready_dir.mkdir(parents=True, exist_ok=True)
    parts_dir.mkdir(parents=True, exist_ok=True)
    unique: dict[str, Want] = {}
    for want in plan.want:
        if everything or worth_asking(want):
            unique.setdefault(want.entry.sha256, want)
    pool_state = _Neighbours(neighbours)
    lock = threading.Lock()

    def one(want: Want) -> None:
        sha = want.entry.sha256
        ready = ready_dir / sha
        if ready.exists():
            return  # fetch_plan сам проверит, годится ли он
        part = parts_dir / f"{sha}.lan.part"

        def note(neighbour: Neighbour, result: str, size: int = 0) -> None:
            if activity is not None:
                activity.add("out", "internet" if neighbour.tls else "lan", neighbour.host, "file", want.entry.path, size, result)

        for neighbour in pool_state.alive():
            if cancelled():
                return
            try:
                _download(neighbour, want, part, timeout, cancelled)
            except _Absent:
                note(neighbour, "not_found")
                continue
            except _Mismatch:
                note(neighbour, "failed")
                pool_state.strike(neighbour, heavy=True)
            except (OSError, http.client.HTTPException, ssl.SSLError):
                note(neighbour, "failed")
                pool_state.strike(neighbour)
            else:
                note(neighbour, "ok", want.entry.size)
                os.replace(part, ready)
                with lock:
                    result.files += 1
                    result.bytes += want.entry.size
                return
            finally:
                part.unlink(missing_ok=True)

    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        list(pool.map(one, unique.values()))
    return result


__all__ = ["MIN_SIZE", "PATCH_GAIN", "PrefillResult", "prefill_from_neighbours", "worth_asking"]
