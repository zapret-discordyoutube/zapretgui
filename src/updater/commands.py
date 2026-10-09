from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class UpdateChannelActionResult:
    ok: bool
    message: str


def is_auto_update_enabled() -> bool:
    from settings.store import get_auto_update_enabled

    return bool(get_auto_update_enabled())


def set_auto_update_enabled(enabled: bool) -> None:
    from settings.store import set_auto_update_enabled

    set_auto_update_enabled(bool(enabled))


def get_update_skipped_version() -> str:
    from settings.store import get_update_skipped_version

    return str(get_update_skipped_version() or "")


def set_update_skipped_version(version: str) -> None:
    from settings.store import set_update_skipped_version

    set_update_skipped_version(str(version or ""))


def note_auto_install_attempt(version: str) -> int:
    from config.build_info import APP_VERSION
    from settings.store import add_auto_install_attempt
    from updater.release.outcome import granted_at

    target = str(version or "")
    # С какой версии и по какому разрешению идёт обновление: новая версия
    # сообщит серверу, что оно дошло.
    return int(add_auto_install_attempt(target, from_version=APP_VERSION, granted_at=granted_at(target)))


def update_busy_reason() -> str:
    """Чем занят человек (игра на весь экран, проверка сети) либо пустая строка."""
    from updater.busy import busy_reason

    return str(busy_reason() or "")


def note_auto_install_failed(version: str) -> None:
    from updater.release.outcome import note_attempt_failed

    note_attempt_failed(str(version or ""))


def remember_whats_new(version: str, history) -> None:
    from updater.whats_new import remember_pending

    remember_pending(version, history)


def startup_whats_new(app_version: str) -> tuple:
    from updater.whats_new import startup_history

    return startup_history(app_version)


def mark_whats_new_seen(version: str) -> None:
    from updater.whats_new import mark_seen

    mark_seen(version)


def load_release_history(version: str) -> tuple:
    from updater.whats_new import load_release_history as _load

    return _load(version)


def mark_update_app_ready(version: str) -> bool:
    from updater.install.splash import mark_update_app_ready as _mark

    return bool(_mark(version))


def run_startup_update_check(*, signalled: bool = False) -> dict:
    from updater.startup_update_check import check_for_update_sync

    return check_for_update_sync(signalled=bool(signalled))


def create_release_watcher(*, on_release, on_queued, is_bypass_running=None):
    """Слушатель сервера, который ведёт очередь обновлений своего канала.

    ``is_bypass_running`` — включён ли сейчас обход (читается из любого
    потока): программа называет это серверу вместе со своим занятием.
    """
    from config.build_info import APP_VERSION, CHANNEL

    from updater.busy import activity, busy_reason
    from updater.release import outcome
    from updater.release.watch import ReleaseWatcher

    def _granted(version: str) -> None:
        # Отсюда считается «от разрешения до запуска новой версии».
        outcome.note_granted(version)
        on_release(version)

    return ReleaseWatcher(
        channel=CHANNEL,
        current_version=APP_VERSION,
        on_release=_granted,
        on_queued=on_queued,
        is_enabled=is_auto_update_enabled,
        is_busy=lambda: bool(busy_reason()),
        activity=lambda: _told_activity(activity, is_bypass_running),
        pending_report=outcome.pending_report,
        report_delivered=outcome.report_delivered,
    )


def _told_activity(activity, is_bypass_running) -> dict:
    from updater.busy import screen_state

    # scr — сырой ответ Windows о занятости экрана: по общему счёту видно,
    # насколько верно правило «человек занят».
    told = {"act": activity(), "scr": screen_state()}
    if is_bypass_running is not None:
        told["run"] = "1" if is_bypass_running() else "0"
    return told


def check_installation_integrity(*, deep: bool = False):
    from install_integrity import verify_installation

    return verify_installation(deep=bool(deep))


def repair_installation(report=None, *, allow_download: bool = True):
    from updater.install.repair import repair_installation as _repair_installation

    return _repair_installation(report, allow_download=bool(allow_download))


def open_update_channel(channel: str) -> UpdateChannelActionResult:
    from config.telegram_links import open_telegram_link
    from updater.channel_utils import is_dev_update_channel

    try:
        domain = "zapretguidev" if is_dev_update_channel(channel) else "zapretnetdiscordyoutube"
        open_telegram_link(domain)
        return UpdateChannelActionResult(True, domain)
    except Exception as exc:
        return UpdateChannelActionResult(False, str(exc))
