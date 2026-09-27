from __future__ import annotations

"""Скачивание установщика и проверка его байтов.

Правила:

* файл скачивается сразу в закрытый каталог состояния обновления под
  случайным именем; куски пишутся на свои места в одном файле — без
  склейки и без копирования;
* каждый источник проверяется целиком: размер из ответа сервера сверяется
  до скачивания, а размер и SHA-256 файла — после. Испорченное зеркало не
  мешает следующему;
* если упал один кусок, остальные останавливаются сразу;
* ошибка записи на диск (нет места, файл занят) останавливает загрузку:
  другие зеркала тут не помогут.
"""

import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import requests
import urllib3

from log.log import log
from utils.file_digest import sha256_file

from ..network_hints import maybe_log_disable_dpi_for_update
from ..release import mirrors
from ..release.http import new_session, short_error
from ..release_contract import ReleaseArtifactMetadata


NUM_SEGMENTS = 4
CHUNK_SIZE = 1024 * 1024
PROGRESS_INTERVAL_SECONDS = 0.25
HEAD_TIMEOUT = (5, 10)
DOWNLOAD_TIMEOUT = (10, 60)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


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
    url: str
    verify_ssl: bool


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


class ThrottledProgress:
    """Не пропускает в Qt больше одного события прогресса за интервал."""

    def __init__(
        self,
        callback: Callable[[int, int, int], None],
        *,
        interval_seconds: float = PROGRESS_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._callback = callback
        self._interval_seconds = float(interval_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._last_emit_at = 0.0
        self._last_done = -1

    def update(self, done: int, total: int, *, force: bool = False) -> None:
        done = max(int(done), 0)
        total = max(int(total), 0)
        now = self._clock()
        with self._lock:
            completed = total > 0 and done >= total
            if (
                not force
                and not completed
                and self._last_done >= 0
                and now - self._last_emit_at < self._interval_seconds
            ):
                return
            if done == self._last_done and not force:
                return
            self._last_emit_at = now
            self._last_done = done

        percent = min(done * 100 // total, 100) if total > 0 else 0
        self._callback(percent, done, total)


def build_download_sources(metadata: ReleaseArtifactMetadata) -> tuple[DownloadSource, ...]:
    """Источник из ответа выпуска и зеркала того же проверенного файла."""
    candidates = [DownloadSource(metadata.update_url, metadata.verify_ssl)]
    candidates.extend(
        DownloadSource(url, verify_ssl)
        for url, verify_ssl in mirrors.download_sources(metadata.file_name)
    )
    unique: list[DownloadSource] = []
    seen: set[str] = set()
    for source in candidates:
        if source.url and source.url not in seen:
            seen.add(source.url)
            unique.append(source)
    return tuple(unique)


def _is_local_io_error(exc: BaseException) -> bool:
    # Сетевые ошибки requests тоже наследуют OSError — их отделяем первыми.
    return isinstance(exc, OSError) and not isinstance(exc, requests.RequestException)


def _local_write_error(exc: OSError) -> LocalWriteError:
    return LocalWriteError(f"Не удалось записать файл обновления: {exc.strerror or exc}")


def _session(source: DownloadSource) -> requests.Session:
    session = new_session()
    session.headers.update({"Accept": "application/octet-stream", "Accept-Encoding": "identity"})
    session.verify = bool(source.verify_ssl)
    return session


def _probe(source: DownloadSource, token: CancellationToken) -> tuple[bool, int]:
    """Поддержка докачки кусками и размер файла на сервере (0, если неизвестен)."""
    token.checkpoint()
    session = _session(source)
    try:
        response = session.head(source.url, timeout=HEAD_TIMEOUT, allow_redirects=True)
        response.raise_for_status()
    finally:
        session.close()
    accepts = response.headers.get("Accept-Ranges", "").lower()
    try:
        length = int(response.headers.get("Content-Length", 0) or 0)
    except ValueError:
        length = 0
    return accepts == "bytes" and length > 0, length


def _write_stream(
    response: requests.Response,
    file_obj,
    *,
    token: CancellationToken,
    on_chunk: Callable[[int], None],
) -> int:
    written = 0
    for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
        token.checkpoint()
        if not chunk:
            continue
        try:
            file_obj.write(chunk)
        except OSError as exc:
            raise _local_write_error(exc) from exc
        written += len(chunk)
        on_chunk(written)
    return written


def _download_segment(
    source: DownloadSource,
    path: Path,
    *,
    start: int,
    end: int,
    token: CancellationToken,
    on_chunk: Callable[[int], None],
) -> None:
    session = _session(source)
    session.headers["Range"] = f"bytes={start}-{end}"
    try:
        with session.get(source.url, stream=True, timeout=DOWNLOAD_TIMEOUT) as response:
            response.raise_for_status()
            if response.status_code != 206:
                raise UpdatePipelineError("Сервер не подтвердил загрузку кусками")
            try:
                file_obj = open(path, "r+b")
            except OSError as exc:
                raise _local_write_error(exc) from exc
            with file_obj:
                file_obj.seek(start)
                written = _write_stream(response, file_obj, token=token, on_chunk=on_chunk)
        expected = end - start + 1
        if written != expected:
            raise UpdateIntegrityError(f"Кусок файла неполный: {written} байт вместо {expected}")
    finally:
        session.close()


def _download_segmented(
    source: DownloadSource,
    path: Path,
    *,
    total: int,
    token: CancellationToken,
    progress: ThrottledProgress,
) -> None:
    try:
        with open(path, "r+b") as file_obj:
            file_obj.truncate(total)
    except OSError as exc:
        raise _local_write_error(exc) from exc

    segment_size = total // NUM_SEGMENTS
    bounds = [
        (index * segment_size, total - 1 if index == NUM_SEGMENTS - 1 else (index + 1) * segment_size - 1)
        for index in range(NUM_SEGMENTS)
    ]
    done_by_segment = [0] * NUM_SEGMENTS
    lock = threading.Lock()
    # Свой признак отмены у попытки: упал один кусок — остальные встают.
    attempt = CancellationToken(parent=token)

    def report(index: int, written: int) -> None:
        with lock:
            done_by_segment[index] = written
            done = sum(done_by_segment)
        progress.update(done, total)

    def run(index: int) -> None:
        start, end = bounds[index]
        try:
            _download_segment(
                source,
                path,
                start=start,
                end=end,
                token=attempt,
                on_chunk=lambda written, index=index: report(index, written),
            )
        except BaseException:
            attempt.cancel()
            raise

    with ThreadPoolExecutor(max_workers=NUM_SEGMENTS, thread_name_prefix="update-segment") as pool:
        futures = [pool.submit(run, index) for index in range(NUM_SEGMENTS)]
    errors = [future.exception() for future in futures if future.exception() is not None]
    token.checkpoint()
    # Первой показываем настоящую причину, а не вызванные ею остановки соседей.
    real_errors = [error for error in errors if not isinstance(error, UpdateCancelled)]
    if real_errors:
        raise real_errors[0]
    if errors:
        raise errors[0]


def _download_single(
    source: DownloadSource,
    path: Path,
    *,
    token: CancellationToken,
    progress: ThrottledProgress,
    expected_size: int,
) -> None:
    session = _session(source)
    try:
        with session.get(source.url, stream=True, timeout=DOWNLOAD_TIMEOUT) as response:
            response.raise_for_status()
            try:
                file_obj = open(path, "wb")
            except OSError as exc:
                raise _local_write_error(exc) from exc
            with file_obj:
                _write_stream(
                    response,
                    file_obj,
                    token=token,
                    on_chunk=lambda written: progress.update(written, expected_size),
                )
    finally:
        session.close()


def verify_file(path: str | os.PathLike[str], artifact: UpdateArtifact, token: CancellationToken) -> None:
    token.checkpoint()
    actual_size = os.path.getsize(path)
    if actual_size != artifact.expected_size:
        raise UpdateIntegrityError(
            f"Размер установщика не совпадает: {actual_size} вместо {artifact.expected_size}"
        )
    actual_sha256 = sha256_file(path, checkpoint=token.checkpoint, chunk_size=CHUNK_SIZE)
    if actual_sha256 != artifact.expected_sha256:
        raise UpdateIntegrityError("SHA-256 установщика не совпадает с данными выпуска")


def _download_from_source(
    source: DownloadSource,
    path: Path,
    artifact: UpdateArtifact,
    *,
    token: CancellationToken,
    progress: ThrottledProgress,
) -> None:
    supports_range, length = _probe(source, token)
    if length and length != artifact.expected_size:
        raise UpdateIntegrityError(
            f"на сервере файл другого размера: {length} вместо {artifact.expected_size}"
        )
    if supports_range and artifact.expected_size > NUM_SEGMENTS * CHUNK_SIZE:
        _download_segmented(source, path, total=artifact.expected_size, token=token, progress=progress)
    else:
        _download_single(source, path, token=token, progress=progress, expected_size=artifact.expected_size)
    verify_file(path, artifact, token)
    progress.update(artifact.expected_size, artifact.expected_size, force=True)


def download_artifact(
    artifact: UpdateArtifact,
    path: str | os.PathLike[str],
    *,
    token: CancellationToken,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> None:
    """Скачивает и проверяет установщик в ``path`` из первого исправного источника."""
    target = Path(path)
    progress = ThrottledProgress(on_progress or (lambda *_args: None))
    errors: list[str] = []
    for index, source in enumerate(artifact.sources, start=1):
        token.checkpoint()
        log(f"Источник обновления #{index}: {source.url}", "🔁 UPDATE")
        try:
            _download_from_source(source, target, artifact, token=token, progress=progress)
            log(f"✅ Установщик скачан и проверен: {artifact.expected_sha256}", "🔁 UPDATE")
            return
        except (UpdateCancelled, LocalWriteError):
            raise
        except Exception as exc:
            if _is_local_io_error(exc):
                raise _local_write_error(exc) from exc
            maybe_log_disable_dpi_for_update(exc, scope="download", level="🔄 DOWNLOAD")
            reason = str(exc) if isinstance(exc, UpdatePipelineError) else short_error(exc)
            log(f"Источник #{index} не подошёл: {reason}", "WARNING")
            errors.append(reason)
    raise UpdatePipelineError(
        "Не удалось скачать обновление: " + ("; ".join(dict.fromkeys(errors)) or "нет источников")
    )


__all__ = [
    "CancellationToken",
    "DownloadSource",
    "LocalWriteError",
    "ThrottledProgress",
    "UpdateArtifact",
    "UpdateCancelled",
    "UpdateIntegrityError",
    "UpdatePipelineError",
    "build_download_sources",
    "download_artifact",
    "verify_file",
]
