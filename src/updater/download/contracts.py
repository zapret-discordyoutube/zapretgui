from __future__ import annotations

"""Общие понятия скачивания: что качаем, откуда, отмена и виды ошибок."""

import threading
from dataclasses import dataclass
from urllib.parse import urlsplit


class UpdatePipelineError(RuntimeError):
    """Понятная пользователю ошибка обновления."""


class UpdateCancelled(UpdatePipelineError):
    """Обновление остановлено по запросу приложения."""


class UpdateIntegrityError(UpdatePipelineError):
    """Файл не прошёл проверку размера или SHA-256."""


class LocalWriteError(UpdatePipelineError):
    """Файл не записать на диск: другие источники тут не помогут."""


@dataclass(frozen=True, slots=True)
class DownloadSource:
    """Один сервер с файлом выпуска.

    ``fallback_url`` — тот же файл на том же сервере по запасному адресу
    (у зеркал это HTTP). Он пробуется, только если основной адрес не дал
    соединиться.
    """

    url: str
    verify_ssl: bool
    name: str = ""
    fallback_url: str = ""

    @property
    def label(self) -> str:
        """Имя для журнала и сообщений: заданное или адрес сервера."""
        return self.name or urlsplit(self.url).netloc or self.url


@dataclass(frozen=True, slots=True)
class UpdateArtifact:
    version: str
    file_name: str
    expected_size: int
    expected_sha256: str
    sources: tuple[DownloadSource, ...]


class CancellationToken:
    """Потокобезопасный признак отмены. Дочерний признак видит отмену родителя."""

    def __init__(self, parent: "CancellationToken | None" = None) -> None:
        self._event = threading.Event()
        self._parent = parent

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set() or bool(self._parent is not None and self._parent.is_cancelled)

    def checkpoint(self) -> None:
        if self.is_cancelled:
            raise UpdateCancelled("Обновление остановлено")


__all__ = [
    "CancellationToken",
    "DownloadSource",
    "LocalWriteError",
    "UpdateArtifact",
    "UpdateCancelled",
    "UpdateIntegrityError",
    "UpdatePipelineError",
]
