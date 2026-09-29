from __future__ import annotations

"""Изменения всех пропущенных версий для окна обновления.

Forgejo отдаёт список выпусков канала целиком (``release["history"]``), из
него берутся версии новее установленной и не новее предлагаемой. Зеркала
знают только последний выпуск — тогда история состоит из него одного.
"""

from typing import Any

from ..versions import version_key
from .forgejo import release_page_url


def _entry(version: str, notes: str, published_at: str, url: str) -> dict[str, str]:
    return {
        "version": str(version or ""),
        "notes": str(notes or ""),
        "published_at": str(published_at or ""),
        "url": str(url or ""),
    }


def release_history_since(release: dict[str, Any], *, current_version: str) -> tuple[dict[str, str], ...]:
    """Выпуски ``current_version < v <= release.version``, от новых к старым."""
    target = str(release.get("version") or "")
    try:
        current_key = version_key(current_version)
        target_key = version_key(target)
    except ValueError:
        current_key = target_key = None

    entries: dict[tuple[int, ...], dict[str, str]] = {}
    if current_key is not None and target_key is not None:
        for item in release.get("history") or ():
            if not isinstance(item, dict):
                continue
            try:
                key = version_key(str(item.get("version") or ""))
            except ValueError:
                continue
            if current_key < key <= target_key:
                entries[key] = _entry(
                    str(item.get("version") or ""),
                    str(item.get("notes") or ""),
                    str(item.get("published_at") or ""),
                    str(item.get("url") or ""),
                )
    if target_key is not None and target_key not in entries:
        # Зеркало или Forgejo без этой строки: сам выпуск всё равно показываем.
        entries[target_key] = _entry(
            target,
            str(release.get("release_notes") or ""),
            str(release.get("published_at") or ""),
            release_url_for(release),
        )
    return tuple(entries[key] for key in sorted(entries, reverse=True))


def release_url_for(release: dict[str, Any]) -> str:
    """Страница выпуска на Forgejo; тег выпуска совпадает с номером версии."""
    url = str(release.get("release_url") or "")
    if url:
        return url
    version = str(release.get("version") or "")
    return release_page_url(version) if version else ""


__all__ = ["release_history_since", "release_url_for"]
