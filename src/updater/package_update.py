"""A newer version on a system without self-update: find it, download the package, check it, hand it to the system.

The program never replaces itself here. The release lookup is the shared one (it picks this system's package file out of
the release); this module downloads that file next to the other update files, checks the size and the SHA-256 that the
release announced, and gives it to ``Platform.updates.install_package`` (on Linux: ``pkexec apt-get install``).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable

from log.log import log

from .release_contract import ReleaseArtifactMetadata, ReleaseMetadataError

CHUNK = 1 << 16
MAX_PACKAGE_BYTES = 1 << 30  # a package larger than 1 GiB is not ours


class PackageUpdateError(RuntimeError):
    """The package could not be fetched or did not match what the release announced; the text is for the person."""


def update_directory() -> Path:
    from config.runtime_layout import APPLICATION_PATHS

    return Path(APPLICATION_PATHS.update_cache_dir)


def download_package(
    release: dict,
    *,
    directory: Path | None = None,
    session=None,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> Path:
    """Download the release's package and return its path; it is verified before the name appears."""
    try:
        meta = ReleaseArtifactMetadata.from_mapping(release)
    except ReleaseMetadataError as exc:
        raise PackageUpdateError(f"The release does not describe a package we can check: {exc}") from exc
    if meta.file_size > MAX_PACKAGE_BYTES:
        raise PackageUpdateError("The announced package is too large.")
    folder = Path(directory) if directory is not None else update_directory()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / meta.file_name
    partial = folder / (meta.file_name + ".part")
    if session is None:
        from .release.http import new_session

        session = new_session()
    digest = hashlib.sha256()
    received = 0
    try:
        response = session.get(meta.update_url, stream=True, timeout=(10, 30), verify=meta.verify_ssl)
        response.raise_for_status()
        with open(partial, "wb") as handle:
            for chunk in response.iter_content(CHUNK):
                if cancelled():
                    raise PackageUpdateError("The download was cancelled.")
                if not chunk:
                    continue
                received += len(chunk)
                if received > meta.file_size:
                    raise PackageUpdateError("The server sent more bytes than the release announced.")
                digest.update(chunk)
                handle.write(chunk)
                if progress is not None:
                    progress(received, meta.file_size)
            handle.flush()
            os.fsync(handle.fileno())
        if received != meta.file_size:
            raise PackageUpdateError(f"The download is incomplete: {received} of {meta.file_size} bytes.")
        if digest.hexdigest() != meta.sha256:
            raise PackageUpdateError("The checksum of the downloaded package does not match the release: it was not installed.")
        os.replace(partial, target)
    except PackageUpdateError:
        _forget(partial)
        raise
    except Exception as exc:  # network and disk errors: one readable text
        _forget(partial)
        raise PackageUpdateError(f"Could not download the package: {exc}") from exc
    finally:
        try:
            session.close()
        except Exception:
            pass
    log(f"Пакет обновления {meta.file_name} скачан и проверен ({received} байт)", "🔁 UPDATE")
    return target


_found: dict[str, dict] = {}


def remember_found_release(release: dict) -> None:
    """The release the last check found, so the button of its notification can install exactly that one."""
    version = str(release.get("version") or "")
    if version:
        _found.clear()
        _found[version] = dict(release)


def install_found_release(version: str, **download_options) -> tuple[bool, str]:
    release = _found.get(str(version or ""))
    if release is None:
        return False, "This release is not known any more: open the program again to look for it."
    return download_and_install(release, **download_options)


def _forget(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def download_and_install(release: dict, **download_options) -> tuple[bool, str]:
    """The whole step behind the button: ``(success, text for the person)``. Never raises."""
    from platforms import current

    try:
        package = download_package(release, **download_options)
    except PackageUpdateError as exc:
        return False, str(exc)
    done, text = current().updates.install_package(package)
    if done:
        _forget(package)
    return done, text


__all__ = [
    "PackageUpdateError",
    "download_and_install",
    "download_package",
    "install_found_release",
    "remember_found_release",
    "update_directory",
]
