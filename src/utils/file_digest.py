from __future__ import annotations

"""Единственная реализация потокового SHA-256 по файлу.

Обновление считает хэш установщика с возможностью отмены, проверка
целостности — хэши поставляемых файлов без отмены. Разница только в
колбэке ``checkpoint``, поэтому реализация одна.
"""

import hashlib
import os
from typing import Callable


CHUNK_SIZE = 1024 * 1024


def sha256_file(
    path: str | os.PathLike[str],
    *,
    checkpoint: Callable[[], None] | None = None,
    chunk_size: int = CHUNK_SIZE,
) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file_obj:
        while True:
            if checkpoint is not None:
                checkpoint()
            chunk = file_obj.read(max(int(chunk_size), 1))
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["CHUNK_SIZE", "sha256_file"]
