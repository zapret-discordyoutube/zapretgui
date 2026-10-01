from __future__ import annotations

"""История выпусков для окна обновления и «Что нового».

Forgejo отдаёт список выпусков канала целиком (``release["history"]``).
Показываются все версии, которые пользователь пропустил (``is_new``), и
предыдущие выпуски — раздел «Ранее»: до десяти в Dev и до трёх в Stable.
Зеркала знают только последний выпуск — тогда история состоит из него одного.
"""

from typing import Any

from ..channel_utils import is_dev_update_channel
from ..versions import version_key
from .forgejo import release_page_url


# Сколько выпусков показывать: новые для пользователя — все, остальное
# добирается предыдущими выпусками до этого числа (раздел «Ранее»).
RECENT_HISTORY_LIMIT = 10
# Текст выпуска Stable — статья обо всём, что изменилось с прошлого Stable,
# поэтому десять таких подряд читать невозможно.
STABLE_HISTORY_LIMIT = 3


def history_limit(channel: str | None = None) -> int:
    """Сколько выпусков показывать; без ``channel`` — канал этой сборки."""
    if channel is None:
        from config.build_info import CHANNEL

        channel = CHANNEL
    return RECENT_HISTORY_LIMIT if is_dev_update_channel(channel) else STABLE_HISTORY_LIMIT


def _entry(version: str, notes: str, published_at: str, url: str, *, is_new: bool) -> dict:
    return {
        "version": str(version or ""),
        "notes": str(notes or ""),
        "published_at": str(published_at or ""),
        "url": str(url or ""),
        "is_new": bool(is_new),
    }


def recent_history(
    entries,
    *,
    up_to_version: str,
    new_after_version: str,
    limit: int | None = None,
) -> tuple[dict, ...]:
    """Выпуски ``<= up_to_version``, от новых к старым.

    Новые для пользователя (``> new_after_version``) попадают все и помечены
    ``is_new``; предыдущие добирают список до ``limit`` (по умолчанию — по
    каналу сборки).
    """
    if limit is None:
        limit = history_limit()
    try:
        top_key = version_key(up_to_version)
    except ValueError:
        return ()
    try:
        new_after_key = version_key(new_after_version)
    except ValueError:
        new_after_key = None

    by_key: dict[tuple[int, ...], dict] = {}
    for item in entries or ():
        if not isinstance(item, dict):
            continue
        try:
            key = version_key(str(item.get("version") or ""))
        except ValueError:
            continue
        if key > top_key or key in by_key:
            continue
        by_key[key] = _entry(
            str(item.get("version") or ""),
            str(item.get("notes") or ""),
            str(item.get("published_at") or ""),
            str(item.get("url") or ""),
            is_new=new_after_key is None or key > new_after_key,
        )
    ordered = [by_key[key] for key in sorted(by_key, reverse=True)]
    new_count = sum(1 for item in ordered if item["is_new"])
    return tuple(ordered[: max(int(limit), new_count)])


def release_history_since(release: dict[str, Any], *, current_version: str) -> tuple[dict, ...]:
    """История для окна обновления: пропущенные версии и предыдущие до лимита канала.

    Пропущенные (``current_version < v <= release.version``) помечены
    ``is_new``. Зеркало или Forgejo без строки предлагаемой версии — она всё
    равно в списке, собранная из самого выпуска.
    """
    target = str(release.get("version") or "")
    entries = [item for item in release.get("history") or () if isinstance(item, dict)]
    known = set()
    for item in entries:
        try:
            known.add(version_key(str(item.get("version") or "")))
        except ValueError:
            pass
    try:
        if version_key(target) not in known:
            entries.append(
                {
                    "version": target,
                    "notes": str(release.get("release_notes") or ""),
                    "published_at": str(release.get("published_at") or ""),
                    "url": release_url_for(release),
                }
            )
    except ValueError:
        return ()
    return recent_history(entries, up_to_version=target, new_after_version=current_version)


def release_url_for(release: dict[str, Any]) -> str:
    """Страница выпуска на Forgejo; тег выпуска совпадает с номером версии."""
    url = str(release.get("release_url") or "")
    if url:
        return url
    version = str(release.get("version") or "")
    return release_page_url(version) if version else ""


__all__ = [
    "RECENT_HISTORY_LIMIT",
    "STABLE_HISTORY_LIMIT",
    "history_limit",
    "recent_history",
    "release_history_since",
    "release_url_for",
]
