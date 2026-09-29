"""
updater/startup_update_check.py
────────────────────────────────────────────────────────────────
Проверка обновлений при запуске приложения.
Не содержит Qt-импортов — вызывается из фонового потока.

Каждый запуск делает свежий запрос: он стоит меньше секунды. Пауза нужна
только против частых перезапусков подряд и отсчитывается лишь от успешной
проверки без находки: неудачная не лишает пользователя следующей попытки, а
о найденном обновлении программа напоминает при каждом запуске.
"""
from __future__ import annotations

import time

from log.log import log


AUTO_CHECK_PAUSE_SECONDS = 10 * 60


def _last_successful_check_at() -> float:
    from settings import store as settings_store

    try:
        auto_check = settings_store.get_updater_settings().get("auto_check") or {}
        return max(float(auto_check.get("last_success_at") or 0.0), 0.0)
    except Exception:
        return 0.0


def _remember_successful_check(now: float) -> None:
    from settings import store as settings_store

    try:
        settings_store.set_updater_settings({"auto_check": {"last_success_at": float(now)}})
    except Exception as exc:
        log(f"Не удалось запомнить время проверки обновлений: {exc}", "WARNING")


def _is_skipped_by_user(version: str) -> bool:
    from settings import store as settings_store

    try:
        return str(settings_store.get_update_skipped_version() or "") == str(version or "")
    except Exception:
        return False


def _pause_left(now: float) -> float:
    elapsed = now - _last_successful_check_at()
    # Часы перевели назад: пауза считается истёкшей.
    if elapsed < 0:
        return 0.0
    return max(AUTO_CHECK_PAUSE_SECONDS - elapsed, 0.0)


def check_for_update_sync(*, now: float | None = None) -> dict:
    """
    Проверяет наличие обновлений синхронно.

    Возвращает dict:
        has_update   : bool      — найдено ли новое обновление
        version      : str|None  — версия обновления (если has_update) или текущая
        release_notes: str       — заметки к выпуску
        error        : str|None  — текст ошибки (если проверка не удалась)
        release_info : dict|None — полные метаданные найденного выпуска
        skipped      : bool      — проверка не нужна (недавно уже была)
    """
    from config.build_info import APP_VERSION, CHANNEL

    from updater.release.resolver import lookup_latest_release
    from updater.versions import compare_versions

    current = float(now if now is not None else time.time())
    pause_left = _pause_left(current)
    if pause_left > 0:
        reason = f"обновления уже проверялись {int((AUTO_CHECK_PAUSE_SECONDS - pause_left) // 60)} мин назад"
        log(f"Автопроверка обновлений при запуске пропущена: {reason}", "🔁 UPDATE")
        return {
            "has_update": False,
            "version": APP_VERSION,
            "release_notes": "",
            "error": None,
            "release_info": None,
            "skipped": True,
            "skip_reason": reason,
            "checked_at": _last_successful_check_at(),
        }

    log("Проверка обновлений при запуске...", "🔁 UPDATE")
    lookup = lookup_latest_release(CHANNEL)
    if not lookup.ok:
        return {
            "has_update": False,
            "version": None,
            "release_notes": "",
            "error": lookup.error,
            "release_info": None,
        }

    release = dict(lookup.release or {})
    new_version = str(release.get("version") or "")
    try:
        has_update = compare_versions(APP_VERSION, new_version) < 0
    except ValueError as exc:
        return {
            "has_update": False,
            "version": None,
            "release_notes": "",
            "error": f"Некорректная версия выпуска: {exc}",
            "release_info": None,
        }

    if has_update:
        from updater.release.history import release_history_since, release_url_for

        log(f"Найдено обновление v{new_version} (текущая v{APP_VERSION})", "🔁 UPDATE")
        return {
            "has_update": True,
            "version": new_version,
            "release_notes": str(release.get("release_notes") or ""),
            "release_source": str(release.get("source") or ""),
            "release_history": release_history_since(release, current_version=APP_VERSION),
            "release_url": release_url_for(release),
            # «Пропустить версию»: при запуске окно само не открывается.
            "user_skipped": _is_skipped_by_user(new_version),
            "error": None,
            "release_info": release,
        }

    log(f"Обновлений нет (v{APP_VERSION})", "🔁 UPDATE")
    # Пауза только после проверки без находки: о найденном обновлении
    # программа напоминает при каждом запуске.
    _remember_successful_check(current)
    return {
        "has_update": False,
        "version": APP_VERSION,
        "release_notes": "",
        "error": None,
        "release_info": None,
    }
