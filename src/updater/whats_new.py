from __future__ import annotations

"""«Что нового»: какие изменения показать и когда.

Перед запуском установщика окно обновления сохраняет изменения всех
пропущенных версий (``remember_pending``). Новая версия при первом запуске
показывает их без сети. Если сохранённого текста нет (программу обновили
вручную), текст выпуска берётся из Forgejo.

При самой первой установке окно не показывается: пользователь ещё ничего не
обновлял. Здесь нет Qt — функции вызываются из фоновых потоков.
"""

from typing import Any

from log.log import log


def _state() -> dict[str, Any]:
    from settings.store import get_whats_new_state

    return dict(get_whats_new_state() or {})


def _pending_history_for(version: str, state: dict[str, Any]) -> tuple[dict[str, str], ...]:
    pending = state.get("pending") if isinstance(state.get("pending"), dict) else {}
    if str(pending.get("version") or "") != str(version or ""):
        return ()
    return tuple(dict(item) for item in pending.get("history") or () if isinstance(item, dict))


def remember_pending(version: str, history) -> None:
    from settings.store import set_whats_new_pending

    set_whats_new_pending(str(version or ""), list(history or ()))


def mark_seen(version: str) -> None:
    from settings.store import set_whats_new_seen_version

    set_whats_new_seen_version(str(version or ""))


def _fetch_from_forgejo(version: str) -> tuple[dict, ...]:
    """Последние выпуски канала до этой версии; новой считается она сама."""
    from config.build_info import CHANNEL
    from updater.release.forgejo import fetch_recent_release_history
    from updater.release.history import recent_history

    entries = fetch_recent_release_history(CHANNEL, up_to_version=version)
    history = recent_history(entries, up_to_version=version, new_after_version=_previous_version(entries, version))
    if not history:
        raise LookupError(f"выпуск {version} не найден среди выпусков канала")
    return history


def _previous_version(entries, version: str) -> str:
    """Версия прямо перед ``version``: всё, что новее неё, — «новое»."""
    from updater.versions import version_key

    try:
        top = version_key(version)
    except ValueError:
        return ""
    older = []
    for item in entries or ():
        try:
            key = version_key(str(item.get("version") or ""))
        except (ValueError, AttributeError):
            continue
        if key < top:
            older.append((key, str(item.get("version") or "")))
    return max(older)[1] if older else ""


def _with_earlier(version: str, pending: tuple[dict, ...]) -> tuple[dict, ...]:
    """Добирает к сохранённому тексту предыдущие выпуски («Ранее»).

    Старые версии сохраняли перед установкой только пропущенные выпуски.
    Если в записи нет раздела «Ранее» и выпусков меньше лимита канала, он берётся
    из списка выпусков Forgejo. Нет сети — показываем сохранённое как есть.
    """
    from updater.release.history import history_limit, recent_history
    from updater.versions import version_key

    if not pending:
        return pending
    if len(pending) >= history_limit() or any(not item.get("is_new", True) for item in pending):
        return pending
    try:
        from config.build_info import CHANNEL
        from updater.release.forgejo import fetch_recent_release_history

        remote = fetch_recent_release_history(CHANNEL, up_to_version=version)
    except Exception as exc:
        log(f"«Что нового»: раздел «Ранее» недоступен: {exc}", "🔁 UPDATE")
        return pending
    new_versions = []
    for item in pending:
        try:
            new_versions.append(version_key(str(item.get("version") or "")))
        except ValueError:
            continue
    if not new_versions:
        return pending
    oldest_new = ".".join(str(part) for part in min(new_versions))
    # Сохранённый текст идёт первым: при совпадении версий побеждает он.
    combined = recent_history(
        tuple(pending) + tuple(remote),
        up_to_version=version,
        new_after_version=_previous_version(remote, oldest_new),
    )
    return combined or pending


def startup_history(app_version: str) -> tuple[dict[str, str], ...]:
    """Что показать при запуске этой версии. Пусто — ничего не показывать.

    Пустой результат, кроме случаев «уже показано», сразу отмечается как
    показанный, иначе окно всплывало бы при каждом запуске.
    """
    version = str(app_version or "")
    state = _state()
    seen = str(state.get("seen_version") or "")
    if seen == version:
        return ()

    history = _with_earlier(version, _pending_history_for(version, state))
    if not history and seen:
        # Обновили не через окно (установщик вручную): берём текст из Forgejo.
        try:
            history = _fetch_from_forgejo(version)
        except Exception as exc:
            log(f"«Что нового»: не удалось получить текст выпуска {version}: {exc}", "🔁 UPDATE")
            history = ()
    if not history:
        mark_seen(version)
    return history


def load_release_history(version: str) -> tuple[dict[str, str], ...]:
    """Текст выпуска для кнопки «Что нового» в «О программе».

    Сначала сохранённое перед установкой (там все пропущенные версии), иначе
    Forgejo. Ошибку сети пробрасывает — окно покажет её пользователю.
    """
    history = _with_earlier(version, _pending_history_for(version, _state()))
    if history:
        return history
    return _fetch_from_forgejo(version)


__all__ = ["load_release_history", "mark_seen", "remember_pending", "startup_history"]
