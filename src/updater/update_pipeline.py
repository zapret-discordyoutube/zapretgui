"""Поэтапный конвейер загрузки и передачи обновления установщику.

Главный принцип: worker-ы этого модуля не получают runtime, окно или другие
QObject-ы приложения. Они выполняют только сеть, файлы и внешний PowerShell.
Переходами между стадиями и жизненным циклом DPI владеет UpdatePageRuntime.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable

import requests
from PyQt6.QtCore import QObject, pyqtSignal

from config.build_info import APP_VERSION, CHANNEL
from config.runtime_layout import APPLICATION_PATHS
from log.log import log
from utils.file_digest import sha256_file

from . import update_paths
from .forgejo_release import normalize_version
from .handoff_state import HandoffState, UpdateHandoffRecord
from .network_hints import maybe_log_disable_dpi_for_update
from .recovery_hook import build_recovery_command, clear_recovery_hook, set_recovery_hook
from .release_manager import get_latest_release
from .update import compare_versions
from .update_watchdog import build_watchdog_command, launch_update_watchdog


NUM_SEGMENTS = 4
CHUNK_SIZE = 1024 * 1024
PROGRESS_INTERVAL_SECONDS = 0.25
SHA256_HEX_LENGTH = 64
CACHED_INSTALLER_NAME = update_paths.CACHED_INSTALLER_NAME
CACHED_INSTALLER_META_NAME = update_paths.CACHED_INSTALLER_META_NAME


class UpdateStage(StrEnum):
    CHECK = "check"
    RESOLVE = "resolve"
    CONNECTIVITY = "connectivity"
    DOWNLOAD = "download"
    VERIFY = "verify"
    HANDOFF = "handoff"
    INSTALLER = "installer"


class UpdatePipelineError(RuntimeError):
    """Понятная пользователю ошибка одной стадии конвейера."""


class UpdateCancelled(UpdatePipelineError):
    """Конвейер остановлен по запросу приложения."""


class UpdateIntegrityError(UpdatePipelineError):
    """Файл не прошёл проверку размера или SHA-256."""


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


@dataclass(frozen=True, slots=True)
class UpdatePreflightResult:
    artifact: UpdateArtifact
    connectivity_ok: bool


@dataclass(frozen=True, slots=True)
class InstallerHandoff:
    version: str
    installer_path: str
    arguments: tuple[str, ...]


class CancellationToken:
    """Общий потокобезопасный признак отмены."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def checkpoint(self) -> None:
        if self._event.is_set():
            raise UpdateCancelled("Обновление остановлено")

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()


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


def _emit_stage(
    callback: Callable[[str, str], None] | None,
    stage: UpdateStage,
    message: str,
) -> None:
    log(f"Стадия обновления {stage.value}: {message}", "🔁 UPDATE")
    if callback is not None:
        callback(stage.value, message)


def _make_session(verify_ssl: bool = True) -> requests.Session:
    import urllib3

    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": None, "https": None}
    session.headers.update(
        {
            "User-Agent": "Zapret-Updater/5.0",
            "Accept": "application/octet-stream",
            "Accept-Encoding": "identity",
        }
    )
    if not verify_ssl:
        session.verify = False
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return session


def normalize_sha256(value: object) -> str:
    """Принимает чистый SHA-256 или совместимый digest ``sha256:...``."""
    normalized = str(value or "").strip().lower()
    if normalized.startswith("sha256:"):
        normalized = normalized.split(":", 1)[1].strip()
    if len(normalized) != SHA256_HEX_LENGTH:
        return ""
    try:
        int(normalized, 16)
    except ValueError:
        return ""
    return normalized


def file_sha256(path: str | os.PathLike[str], token: CancellationToken) -> str:
    return sha256_file(path, checkpoint=token.checkpoint, chunk_size=CHUNK_SIZE)


def _release_sha256(release_info: dict) -> str:
    return normalize_sha256(
        release_info.get("sha256")
        or release_info.get("digest")
        or release_info.get("checksum")
    )


def _matching_forgejo_integrity(release_info: dict) -> tuple[str, int]:
    """При необходимости получает SHA-256 из проверенного выпуска Forgejo."""
    expected_sha256 = _release_sha256(release_info)
    expected_size = int(release_info.get("file_size") or 0)
    if expected_sha256 and expected_size > 0:
        return expected_sha256, expected_size

    try:
        from .forgejo_release import get_latest_release as get_forgejo_release

        forgejo_info = get_forgejo_release(CHANNEL)
    except Exception as exc:
        log(f"Не удалось получить контрольную сумму Forgejo: {exc}", "WARNING")
        forgejo_info = None

    if not forgejo_info:
        return expected_sha256, expected_size

    try:
        same_version = normalize_version(str(forgejo_info.get("version") or "")) == normalize_version(
            str(release_info.get("version") or "")
        )
    except ValueError:
        same_version = False
    if not same_version:
        return expected_sha256, expected_size

    return (
        expected_sha256 or _release_sha256(forgejo_info),
        expected_size or int(forgejo_info.get("file_size") or 0),
    )


def build_download_sources(release_info: dict) -> tuple[DownloadSource, ...]:
    """Собирает зеркала один раз и удаляет повторы."""
    update_url = str(release_info.get("update_url") or "").strip()
    verify_ssl = bool(release_info.get("verify_ssl", True))
    file_name = str(release_info.get("file_name") or "").strip()
    if not file_name and update_url and not update_url.startswith("telegram://"):
        file_name = update_url.rsplit("/", 1)[-1]
    if not file_name:
        from .channel_utils import get_channel_installer_name

        file_name = get_channel_installer_name(CHANNEL)

    candidates: list[DownloadSource] = []
    if update_url and not update_url.startswith("telegram://"):
        candidates.append(DownloadSource(update_url, verify_ssl))

    if "git.zapret.moe" not in update_url:
        try:
            from .forgejo_release import get_latest_release as get_forgejo_release

            forgejo_info = get_forgejo_release(CHANNEL)
            if forgejo_info and forgejo_info.get("update_url"):
                candidates.append(DownloadSource(str(forgejo_info["update_url"]), True))
        except Exception as exc:
            log(f"Не удалось добавить зеркало Forgejo: {exc}", "WARNING")

    try:
        from .server_config import VPS_SERVERS, should_verify_ssl

        for server in VPS_SERVERS:
            candidates.append(
                DownloadSource(
                    f"https://{server['host']}:{server['https_port']}/download/{file_name}",
                    bool(should_verify_ssl()),
                )
            )
        for server in VPS_SERVERS:
            candidates.append(
                DownloadSource(
                    f"http://{server['host']}:{server['http_port']}/download/{file_name}",
                    False,
                )
            )
    except Exception as exc:
        log(f"Не удалось добавить зеркала VPS: {exc}", "WARNING")

    unique: list[DownloadSource] = []
    seen: set[str] = set()
    for source in candidates:
        if not source.url or source.url in seen:
            continue
        seen.add(source.url)
        unique.append(source)
    return tuple(unique)


def test_connectivity(source: DownloadSource, token: CancellationToken) -> bool:
    token.checkpoint()
    session = _make_session(source.verify_ssl)
    try:
        response = session.head(
            source.url,
            timeout=(5, 5),
            verify=source.verify_ssl,
            allow_redirects=True,
        )
        token.checkpoint()
        return response.status_code < 500
    except Exception:
        return False
    finally:
        session.close()


def prepare_update(
    *,
    requested_version: str,
    token: CancellationToken,
    on_stage: Callable[[str, str], None] | None = None,
    allow_same_version: bool = False,
) -> UpdatePreflightResult:
    """Выполняет check → resolve → connectivity.

    ``allow_same_version`` нужен восстановлению поставки: там запрашивается
    ровно установленная версия, а не более новая.
    """
    token.checkpoint()
    _emit_stage(on_stage, UpdateStage.CHECK, "Проверка выпуска…")
    release_info = get_latest_release(CHANNEL, use_cache=False)
    if not release_info:
        raise UpdatePipelineError("Не удалось получить данные выпуска")

    remote_version = normalize_version(str(release_info.get("version") or ""))
    version_gap = compare_versions(APP_VERSION, remote_version)
    if version_gap > 0 or (version_gap == 0 and not allow_same_version):
        raise UpdatePipelineError(f"Обновление v{remote_version} уже не требуется")
    if requested_version and compare_versions(remote_version, requested_version) < 0:
        raise UpdatePipelineError("Сервер вернул более старый выпуск")

    token.checkpoint()
    _emit_stage(on_stage, UpdateStage.RESOLVE, "Подготовка источников и проверки файла…")
    sources = build_download_sources(release_info)
    if not sources:
        raise UpdatePipelineError("Нет доступных источников обновления")

    expected_sha256, expected_size = _matching_forgejo_integrity(release_info)
    if not expected_sha256:
        raise UpdateIntegrityError("В метаданных выпуска нет SHA-256")
    if expected_size <= 0:
        raise UpdateIntegrityError("В метаданных выпуска нет размера файла")

    file_name = str(release_info.get("file_name") or "").strip() or "Zapret2Setup.exe"
    artifact = UpdateArtifact(
        version=remote_version,
        file_name=file_name,
        expected_size=expected_size,
        expected_sha256=expected_sha256,
        sources=sources,
    )

    token.checkpoint()
    _emit_stage(on_stage, UpdateStage.CONNECTIVITY, "Проверка соединения с сервером…")
    connectivity_ok = test_connectivity(sources[0], token)
    return UpdatePreflightResult(artifact=artifact, connectivity_ok=connectivity_ok)


def _supports_range(source: DownloadSource, token: CancellationToken) -> tuple[bool, int]:
    token.checkpoint()
    session = _make_session(source.verify_ssl)
    try:
        response = session.head(
            source.url,
            timeout=(10, 15),
            verify=source.verify_ssl,
            allow_redirects=True,
        )
        response.raise_for_status()
        token.checkpoint()
        accepts = response.headers.get("Accept-Ranges", "").lower()
        length = int(response.headers.get("Content-Length", 0) or 0)
        return accepts == "bytes" and length > 0, length
    finally:
        session.close()


def _download_segment(
    source: DownloadSource,
    *,
    start: int,
    end: int,
    destination: str,
    segment_index: int,
    progress_sizes: list[int],
    progress_lock: threading.Lock,
    total: int,
    token: CancellationToken,
    progress: ThrottledProgress,
) -> None:
    session = _make_session(source.verify_ssl)
    session.headers["Range"] = f"bytes={start}-{end}"
    try:
        with session.get(
            source.url,
            stream=True,
            timeout=(10, 90),
            verify=source.verify_ssl,
        ) as response:
            response.raise_for_status()
            if response.status_code != 206:
                raise UpdatePipelineError("Сервер не подтвердил сегментную загрузку")
            written = 0
            with open(destination, "wb") as file_obj:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    token.checkpoint()
                    if not chunk:
                        continue
                    file_obj.write(chunk)
                    written += len(chunk)
                    with progress_lock:
                        progress_sizes[segment_index] = written
                        done = sum(progress_sizes)
                    progress.update(done, total)
            expected = end - start + 1
            if written != expected:
                raise UpdateIntegrityError(
                    f"Размер сегмента не совпадает: {written} вместо {expected}"
                )
    finally:
        session.close()


def _download_segmented(
    source: DownloadSource,
    destination: str,
    *,
    total: int,
    token: CancellationToken,
    progress: ThrottledProgress,
) -> None:
    segment_size = total // NUM_SEGMENTS
    segments = [
        (
            index * segment_size,
            total - 1 if index == NUM_SEGMENTS - 1 else (index + 1) * segment_size - 1,
        )
        for index in range(NUM_SEGMENTS)
    ]
    segment_dir = destination + "_segments"
    os.makedirs(segment_dir, exist_ok=True)
    segment_paths = [os.path.join(segment_dir, f"segment_{index}") for index in range(NUM_SEGMENTS)]
    progress_sizes = [0] * NUM_SEGMENTS
    progress_lock = threading.Lock()

    try:
        with ThreadPoolExecutor(max_workers=NUM_SEGMENTS) as pool:
            futures = [
                pool.submit(
                    _download_segment,
                    source,
                    start=start,
                    end=end,
                    destination=segment_paths[index],
                    segment_index=index,
                    progress_sizes=progress_sizes,
                    progress_lock=progress_lock,
                    total=total,
                    token=token,
                    progress=progress,
                )
                for index, (start, end) in enumerate(segments)
            ]
            for future in futures:
                future.result()

        token.checkpoint()
        with open(destination, "wb") as output:
            for segment_path in segment_paths:
                token.checkpoint()
                with open(segment_path, "rb") as segment_file:
                    shutil.copyfileobj(segment_file, output, length=CHUNK_SIZE)
    finally:
        shutil.rmtree(segment_dir, ignore_errors=True)


def _download_single(
    source: DownloadSource,
    destination: str,
    *,
    token: CancellationToken,
    progress: ThrottledProgress,
) -> int:
    session = _make_session(source.verify_ssl)
    try:
        with session.get(
            source.url,
            stream=True,
            timeout=(10, 90),
            verify=source.verify_ssl,
        ) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length", 0) or 0)
            done = 0
            with open(destination, "wb") as file_obj:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    token.checkpoint()
                    if not chunk:
                        continue
                    file_obj.write(chunk)
                    done += len(chunk)
                    progress.update(done, total)
            return total
    finally:
        session.close()


def download_source(
    source: DownloadSource,
    destination: str,
    *,
    token: CancellationToken,
    progress: ThrottledProgress,
    max_retries: int = 2,
) -> None:
    """Скачивает одно зеркало с ограниченным числом повторов."""
    last_error: Exception | None = None
    for attempt in range(max(int(max_retries), 1)):
        token.checkpoint()
        try:
            supports_range, total = _supports_range(source, token)
            if supports_range and total > NUM_SEGMENTS * CHUNK_SIZE:
                _download_segmented(
                    source,
                    destination,
                    total=total,
                    token=token,
                    progress=progress,
                )
            else:
                total = _download_single(
                    source,
                    destination,
                    token=token,
                    progress=progress,
                )

            actual_size = os.path.getsize(destination)
            if total > 0 and actual_size != total:
                raise UpdateIntegrityError(
                    f"Размер загрузки не совпадает: {actual_size} вместо {total}"
                )
            progress.update(actual_size, total or actual_size, force=True)
            return
        except UpdateCancelled:
            raise
        except Exception as exc:
            last_error = exc
            maybe_log_disable_dpi_for_update(exc, scope="download", level="🔄 DOWNLOAD")
            try:
                os.remove(destination)
            except FileNotFoundError:
                pass
            if attempt + 1 < max_retries:
                token.checkpoint()
                time.sleep(min(2 ** (attempt + 1), 4))
    raise UpdatePipelineError(f"Не удалось скачать файл: {last_error}")


def verify_artifact(
    artifact: UpdateArtifact,
    installer_path: str,
    token: CancellationToken,
) -> None:
    token.checkpoint()
    actual_size = os.path.getsize(installer_path)
    if actual_size != artifact.expected_size:
        raise UpdateIntegrityError(
            f"Размер установщика не совпадает: {actual_size} вместо {artifact.expected_size}"
        )

    actual_sha256 = file_sha256(installer_path, token)
    if actual_sha256 != artifact.expected_sha256:
        raise UpdateIntegrityError("SHA-256 установщика не совпадает с метаданными выпуска")
    log(f"✅ SHA-256 установщика проверен: {actual_sha256}", "🔁 UPDATE")


def cached_installer_path() -> Path:
    return update_paths.cached_installer_path()


def cached_installer_meta_path() -> Path:
    return update_paths.cached_installer_meta_path()


def read_cached_installer_meta() -> dict:
    """Метаданные последнего сохранённого установщика.

    Нужны восстановлению поставки: по ним видно, какой версии лежит файл и
    какой у него SHA-256, поэтому починка возможна без сети — а без движка
    сеть у пользователя как раз может не работать.
    """
    try:
        raw = cached_installer_meta_path().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def installer_arguments(*, log_name: str = update_paths.SETUP_LOG_NAME) -> tuple[str, ...]:
    """Единственный набор аргументов Inno Setup для установки без вопросов.

    ``/SUPPRESSMSGBOXES`` здесь недопустим: в Inno он означает ответ Abort в
    ситуациях Abort/Retry, то есть превращает сбой распаковки в молчаливый
    выход без единого сообщения. ``/SILENT`` вместо ``/VERYSILENT`` оставляет
    пользователю полосу прогресса и текст любой ошибки установщика.
    """
    setup_log = update_paths.setup_log_path(log_name)
    return (
        "/AUTOUPDATE",
        "/SILENT",
        "/NORESTART",
        "/NOCANCEL",
        "/CLOSEAPPLICATIONS",
        f"/DIR={APPLICATION_PATHS.root}",
        f"/LOG={setup_log}",
    )


def prepare_handoff(
    artifact: UpdateArtifact,
    downloaded_path: str,
    token: CancellationToken,
) -> InstallerHandoff:
    """Копирует уже проверенный файл в каталог, переживающий переустановку."""
    token.checkpoint()
    persistent_dir = update_paths.update_state_dir()
    persistent_path = persistent_dir / update_paths.CACHED_INSTALLER_NAME
    temporary_path = persistent_path.with_suffix(".exe.new")

    try:
        temporary_path.unlink(missing_ok=True)
        shutil.copy2(downloaded_path, temporary_path)
        token.checkpoint()
        os.replace(temporary_path, persistent_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    try:
        cached_installer_meta_path().write_text(
            json.dumps(
                {
                    "version": artifact.version,
                    "sha256": artifact.expected_sha256,
                    "size": int(artifact.expected_size),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        log(f"Не удалось сохранить метаданные установщика: {exc}", "WARNING")

    return InstallerHandoff(
        version=artifact.version,
        installer_path=str(persistent_path),
        arguments=installer_arguments(),
    )


def start_supervised_installation(handoff: InstallerHandoff) -> bool:
    """Передаёт установку наблюдателю и ставит системную страховку.

    Приложение закрывается сразу после этого вызова, поэтому исход установки
    больше некому увидеть: наблюдатель — единственный участник, который
    дождётся кода возврата установщика и сверит версию на диске. Страховка в
    ``RunOnce`` покрывает случай, когда не выживет и он.
    """
    record = UpdateHandoffRecord(
        state=HandoffState.PREPARED,
        version=handoff.version,
        target_root=str(APPLICATION_PATHS.root),
        installer_path=handoff.installer_path,
        arguments=tuple(handoff.arguments),
    )

    hook_command = build_recovery_command(
        build_watchdog_command(
            script_path=update_paths.watchdog_script_path(),
            state_path=update_paths.handoff_state_path(),
            recovery=True,
        )
    )
    hook_set = set_recovery_hook(hook_command)

    if launch_update_watchdog(record):
        return True

    # Наблюдатель не поднялся, установка не начнётся — страховка стала бы
    # обещанием восстановить то, чего никто не ломал.
    if hook_set:
        clear_recovery_hook()
    return False


class UpdatePipeline:
    """Единый сетевой/файловый конвейер без зависимостей от GUI и runtime."""

    def __init__(
        self,
        *,
        token: CancellationToken,
        on_stage: Callable[[str, str], None] | None = None,
        on_progress: Callable[[int, int, int], None] | None = None,
        silent: bool = True,
    ) -> None:
        self._token = token
        self._on_stage = on_stage
        self._on_progress = on_progress
        self._silent = bool(silent)

    def preflight(
        self,
        *,
        requested_version: str,
        allow_same_version: bool = False,
    ) -> UpdatePreflightResult:
        return prepare_update(
            requested_version=requested_version,
            token=self._token,
            on_stage=self._on_stage,
            allow_same_version=allow_same_version,
        )

    def download_and_prepare(self, artifact: UpdateArtifact) -> InstallerHandoff:
        temporary_dir = tempfile.mkdtemp(prefix="zapret_upd_")
        destination = os.path.join(temporary_dir, "Zapret2Setup.exe")
        progress = ThrottledProgress(self._on_progress or (lambda *_args: None))
        try:
            _emit_stage(self._on_stage, UpdateStage.DOWNLOAD, "Скачивание обновления…")
            self._download_from_mirrors(artifact, destination, progress)

            self._token.checkpoint()
            _emit_stage(self._on_stage, UpdateStage.VERIFY, "Проверка SHA-256…")
            verify_artifact(artifact, destination, self._token)

            self._token.checkpoint()
            _emit_stage(self._on_stage, UpdateStage.HANDOFF, "Подготовка установщика…")
            return prepare_handoff(artifact, destination, self._token)
        finally:
            shutil.rmtree(temporary_dir, ignore_errors=True)

    def _download_from_mirrors(
        self,
        artifact: UpdateArtifact,
        destination: str,
        progress: ThrottledProgress,
    ) -> None:
        last_error: Exception | None = None
        for index, source in enumerate(artifact.sources):
            self._token.checkpoint()
            try:
                log(
                    f"Источник обновления #{index + 1}: {source.url} "
                    f"(SSL={source.verify_ssl})",
                    "🔁 UPDATE",
                )
                download_source(
                    source,
                    destination,
                    token=self._token,
                    progress=progress,
                    max_retries=1 if self._silent else 2,
                )
                return
            except UpdateCancelled:
                raise
            except Exception as exc:
                last_error = exc
                log(f"Источник #{index + 1} не подошёл: {exc}", "WARNING")
        raise UpdatePipelineError(str(last_error or "Нет доступных источников обновления"))


class UpdatePreflightWorker(QObject):
    stage_changed = pyqtSignal(str, str)
    ready = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()
    finished = pyqtSignal(bool)

    def __init__(self, requested_version: str) -> None:
        super().__init__()
        self._requested_version = str(requested_version or "")
        self._token = CancellationToken()

    def stop(self) -> None:
        self._token.cancel()

    def run(self) -> None:
        try:
            result = UpdatePipeline(
                token=self._token,
                on_stage=self.stage_changed.emit,
            ).preflight(requested_version=self._requested_version)
        except UpdateCancelled:
            self.cancelled.emit()
            self.finished.emit(False)
            return
        except Exception as exc:
            log(f"Подготовка обновления не удалась: {exc}", "🔁❌ ERROR")
            self.failed.emit(str(exc))
            self.finished.emit(False)
            return
        self.ready.emit(result)
        self.finished.emit(True)


class UpdateDownloadWorker(QObject):
    stage_changed = pyqtSignal(str, str)
    progress_bytes = pyqtSignal(int, int, int)
    ready = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()
    finished = pyqtSignal(bool)

    def __init__(self, artifact: UpdateArtifact, *, silent: bool = True) -> None:
        super().__init__()
        self._artifact = artifact
        self._silent = bool(silent)
        self._token = CancellationToken()

    def stop(self) -> None:
        self._token.cancel()

    def run(self) -> None:
        try:
            handoff = UpdatePipeline(
                token=self._token,
                on_stage=self.stage_changed.emit,
                on_progress=self.progress_bytes.emit,
                silent=self._silent,
            ).download_and_prepare(self._artifact)
        except UpdateCancelled:
            self.cancelled.emit()
            self.finished.emit(False)
            return
        except Exception as exc:
            log(f"Загрузка обновления не удалась: {exc}", "🔁❌ ERROR")
            self.failed.emit(str(exc))
            self.finished.emit(False)
            return
        self.ready.emit(handoff)
        self.finished.emit(True)


class UpdateInstallerWorker(QObject):
    stage_changed = pyqtSignal(str, str)
    launched = pyqtSignal()
    failed = pyqtSignal(str)
    finished = pyqtSignal(bool)

    def __init__(self, handoff: InstallerHandoff) -> None:
        super().__init__()
        self._handoff = handoff
        self._stop_requested = threading.Event()

    def stop(self) -> None:
        self._stop_requested.set()

    def run(self) -> None:
        if self._stop_requested.is_set():
            self.finished.emit(False)
            return
        _emit_stage(
            self.stage_changed.emit,
            UpdateStage.INSTALLER,
            "Запуск установщика…",
        )
        if not start_supervised_installation(self._handoff):
            self.failed.emit("Не удалось запустить установщик")
            self.finished.emit(False)
            return
        self.launched.emit()
        self.finished.emit(True)


__all__ = [
    "CancellationToken",
    "DownloadSource",
    "InstallerHandoff",
    "ThrottledProgress",
    "UpdateArtifact",
    "UpdateCancelled",
    "UpdateDownloadWorker",
    "UpdateInstallerWorker",
    "UpdateIntegrityError",
    "UpdatePipelineError",
    "UpdatePipeline",
    "UpdatePreflightResult",
    "UpdatePreflightWorker",
    "UpdateStage",
    "build_download_sources",
    "cached_installer_meta_path",
    "cached_installer_path",
    "download_source",
    "file_sha256",
    "installer_arguments",
    "read_cached_installer_meta",
    "normalize_sha256",
    "prepare_handoff",
    "prepare_update",
    "start_supervised_installation",
    "test_connectivity",
    "verify_artifact",
]
