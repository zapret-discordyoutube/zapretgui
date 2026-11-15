"""Looking for a newer version where the program cannot replace itself (Linux): tell the person, install on request.

Once shortly after the start and then every few hours (the check itself pauses after a recent one) the shared release
lookup runs in the "update" queue. A newer version is announced by a notification with the buttons "download and
install" (the package is checked against the SHA-256 of the release and handed to the system's package manager), "what
is new" and "later". Nothing is downloaded or installed without that button.
"""

from __future__ import annotations

from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal

from app_notifications import advisory_notification, notification_action
from log.log import log
from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import enqueue_subsystem_task, schedule_after

FIRST_CHECK_DELAY_MS = 90 * 1000
CHECK_INTERVAL_MS = 6 * 3600 * 1000
RETRY_AFTER_ERROR_MS = 30 * 60 * 1000


class _Bridge(QObject):
    found = pyqtSignal(str, str)  # version, release page


def notification_for(version: str, release_url: str) -> dict:
    buttons = [notification_action("install_package_update", "Скачать и установить", value=version)]
    if release_url:
        buttons.append(notification_action("open_url", "Что нового", value=release_url))
    buttons.append(notification_action("dismiss", "Позже"))
    return advisory_notification(
        level="info",
        title=f"Вышла версия {version}",
        content=(
            "Пакет скачается, проверится по контрольной сумме выпуска и будет установлен через менеджер пакетов: "
            "система спросит пароль. После установки закройте Zapret и запустите снова."
        ),
        source="update.package",
        presentation="infobar",
        queue="immediate",
        duration=30000,
        dedupe_key=f"update.package:{version}",
        buttons=buttons,
    )


def install_package_update_check(startup_host, *, updater_feature, notify, set_status=None, idle_tasks=None, ui_state_store=None) -> None:
    from updater import package_update

    bridge = _Bridge(QCoreApplication.instance())
    announced: set[str] = set()

    def _announce(version: str, release_url: str) -> None:
        if not is_startup_host_alive(startup_host) or version in announced:
            return
        announced.add(version)
        if set_status is not None:
            try:
                set_status(f"Доступно обновление v{version}")
            except Exception:
                pass
        notify(notification_for(version, release_url))

    bridge.found.connect(_announce)

    def _check() -> int:
        """Returns the delay until the next check."""
        if not is_startup_host_alive(startup_host):
            return CHECK_INTERVAL_MS
        if not updater_feature.is_auto_update_enabled():
            return CHECK_INTERVAL_MS
        result = updater_feature.run_startup_update_check()
        if result.get("error"):
            log(f"Проверка обновлений пакета не удалась: {result['error']}", "🔁 UPDATE")
            return RETRY_AFTER_ERROR_MS
        if result.get("has_update") and not result.get("user_skipped") and isinstance(result.get("release_info"), dict):
            package_update.remember_found_release(result["release_info"])
            bridge.found.emit(str(result.get("version") or ""), str(result.get("release_url") or ""))
        return CHECK_INTERVAL_MS

    def _tick() -> None:
        if not is_startup_host_alive(startup_host):
            return
        state = {"delay": CHECK_INTERVAL_MS}

        def _work() -> None:
            try:
                state["delay"] = _check()
            except Exception as exc:  # the check must never disturb the program
                log(f"Проверка обновлений пакета: {type(exc).__name__}: {exc}", "WARNING")
                state["delay"] = RETRY_AFTER_ERROR_MS
            schedule_after(state["delay"], _tick)

        enqueue_subsystem_task("update", "PackageUpdateCheck", _work)

    def _start() -> None:
        schedule_after(FIRST_CHECK_DELAY_MS, _tick)

    bind_startup_gate(
        startup_host.startup_interactive_ready,
        _start,
        is_ready=lambda: bool(startup_host.startup_state.interactive_logged),
    )


__all__ = ["install_package_update_check", "notification_for"]
