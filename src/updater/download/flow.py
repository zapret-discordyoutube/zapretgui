from __future__ import annotations

"""Путь установки обновления целиком — без Qt и без окон.

1. Узнать выпуск и собрать источники (Forgejo или зеркало, плюс зеркала).
2. Скачать и проверить установщик прямо в закрытый каталог состояния.
3. Если сеть не пустила, а DPI работает, — один повтор с остановленным DPI.
4. Остановить DPI (установщик заменяет его файлы) и передать установку
   наблюдателю.

Если установщик так и не запустился, DPI запускается обратно в ``finally`` —
при любой ошибке и при отмене. Страница больше ничем из этого не владеет.
"""

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from config.build_info import APP_VERSION, CHANNEL
from log.log import log

from ..dpi_guard import DpiGuard
from ..install import paths
from ..install.launcher import (
    InstallerHandoff,
    ensure_private_state_dir,
    stage_installer,
    start_supervised_installation,
)
from ..release.resolver import lookup_latest_release
from ..release_contract import ReleaseArtifactMetadata
from ..versions import compare_versions, normalize_version
from .downloader import (
    CancellationToken,
    LocalWriteError,
    UpdateArtifact,
    UpdateCancelled,
    UpdatePipelineError,
    build_download_sources,
    download_artifact,
)


UPDATE_LOG_LEVEL = "🔁 UPDATE"
PART_SUFFIX = ".part"


def resolve_artifact(*, requested_version: str = "", allow_same_version: bool = False) -> UpdateArtifact:
    """Выпуск для установки: версия, размер, SHA-256 и источники одного ответа.

    ``allow_same_version`` нужен починке: там ставится ровно установленная
    версия, а не более новая.
    """
    lookup = lookup_latest_release(CHANNEL)
    if not lookup.ok:
        raise UpdatePipelineError(lookup.error or "Не удалось получить данные выпуска")

    metadata = ReleaseArtifactMetadata.from_mapping(lookup.release or {})
    remote_version = normalize_version(metadata.version)
    gap = compare_versions(APP_VERSION, remote_version)
    if gap > 0 or (gap == 0 and not allow_same_version):
        raise UpdatePipelineError(f"Обновление v{remote_version} уже не требуется")
    if requested_version and compare_versions(remote_version, requested_version) < 0:
        raise UpdatePipelineError("Сервер вернул более старый выпуск")

    return UpdateArtifact(
        version=remote_version,
        file_name=metadata.file_name,
        expected_size=metadata.file_size,
        expected_sha256=metadata.sha256,
        sources=build_download_sources(metadata),
    )


def _remove_leftovers(state_dir: Path) -> None:
    """Убирает недокачанные файлы прошлых попыток."""
    for pattern in (f"{paths.CACHED_INSTALLER_NAME}.*{PART_SUFFIX}", f"{paths.CACHED_INSTALLER_NAME}.*.tmp"):
        for leftover in state_dir.glob(pattern):
            try:
                leftover.unlink()
            except OSError:
                pass


def download_and_stage(
    artifact: UpdateArtifact,
    *,
    token: CancellationToken,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> InstallerHandoff:
    """Скачивает и проверяет установщик и кладёт его на постоянное место."""
    ensure_private_state_dir()
    state_dir = paths.update_state_dir()
    _remove_leftovers(state_dir)
    try:
        descriptor, part_name = tempfile.mkstemp(
            prefix=f"{paths.CACHED_INSTALLER_NAME}.", suffix=PART_SUFFIX, dir=str(state_dir)
        )
        os.close(descriptor)
    except OSError as exc:
        raise LocalWriteError(f"Не удалось создать файл обновления: {exc.strerror or exc}") from exc

    part_path = Path(part_name)
    try:
        download_artifact(artifact, part_path, token=token, on_progress=on_progress)
        token.checkpoint()
        try:
            return stage_installer(
                part_path,
                version=artifact.version,
                sha256=artifact.expected_sha256,
                size=artifact.expected_size,
                checkpoint=token.checkpoint,
                move=True,
            )
        except PermissionError as exc:
            raise LocalWriteError(
                "Сохранённый установщик занят другой программой (антивирус или прошлая установка)"
            ) from exc
        except OSError as exc:
            raise LocalWriteError(f"Не удалось сохранить установщик: {exc.strerror or exc}") from exc
    finally:
        try:
            part_path.unlink(missing_ok=True)
        except OSError:
            pass


def _resolve_and_download(
    requested_version: str,
    *,
    token: CancellationToken,
    on_stage: Callable[[str], None],
    on_progress: Callable[[int, int, int], None] | None,
) -> InstallerHandoff:
    on_stage("Проверка выпуска…")
    artifact = resolve_artifact(requested_version=requested_version)
    token.checkpoint()
    on_stage("Скачивание обновления…")
    return download_and_stage(artifact, token=token, on_progress=on_progress)


def run_update_install(
    requested_version: str,
    *,
    token: CancellationToken,
    dpi: DpiGuard,
    on_stage: Callable[[str], None],
    on_progress: Callable[[int, int, int], None] | None = None,
    on_downloaded: Callable[[], None] = lambda: None,
    start_installation: Callable[[InstallerHandoff], bool] = start_supervised_installation,
) -> None:
    """Скачивает, проверяет и передаёт установщик наблюдателю.

    Возвращается только после успешной передачи. Любая неудача —
    ``UpdatePipelineError`` с понятным текстом; DPI к этому моменту уже
    запущен обратно.
    """
    launched = False
    try:
        try:
            handoff = _resolve_and_download(
                requested_version, token=token, on_stage=on_stage, on_progress=on_progress
            )
        except (UpdateCancelled, LocalWriteError):
            raise
        except UpdatePipelineError as first_error:
            if not dpi.is_running():
                raise
            log(f"Обновление не скачалось при работающем DPI: {first_error}", UPDATE_LOG_LEVEL)
            on_stage("Остановка DPI и повтор скачивания…")
            dpi.stop(reason="updater_download_connectivity", update_runtime_state=False)
            handoff = _resolve_and_download(
                requested_version, token=token, on_stage=on_stage, on_progress=on_progress
            )

        on_downloaded()
        token.checkpoint()
        if dpi.is_running():
            on_stage("Остановка DPI перед установкой…")
            dpi.stop(reason="updater_installer_handoff", update_runtime_state=False)
        token.checkpoint()
        on_stage("Запуск установщика…")
        if not start_installation(handoff):
            raise UpdatePipelineError("Не удалось запустить установщик")
        launched = True
    except UpdatePipelineError:
        raise
    except Exception as exc:
        raise UpdatePipelineError(str(exc) or type(exc).__name__) from exc
    finally:
        if not launched:
            dpi.restore()


__all__ = [
    "download_and_stage",
    "resolve_artifact",
    "run_update_install",
]
