from __future__ import annotations

"""Фоновая проверка целостности поставки после старта окна.

Дыру в установке нужно находить не в момент, когда пользователь нажал
«Запустить», а сразу после запуска программы. Первая проверка после
обновления считает и хэши: именно тогда антивирус чаще всего забирает
свежераспакованный движок.
"""

from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal

from log.log import log
from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import enqueue_subsystem_task, schedule_after


_INTEGRITY_CHECK_DELAY_MS = 6000
_INTEGRITY_LOG_LEVEL = "🩹 REPAIR"


class _IntegrityBridge(QObject):
    result_ready = pyqtSignal(object)


def _first_run_after_update() -> bool:
    """True, если версия приложения изменилась с прошлого запуска."""
    try:
        from config.build_info import APP_VERSION
        from settings.store import get_last_seen_version, set_last_seen_version
    except Exception:
        return False

    try:
        previous = str(get_last_seen_version() or "").strip()
        current = str(APP_VERSION or "").strip()
        if previous != current:
            set_last_seen_version(current)
            return bool(previous)
    except Exception:
        return False
    return False


def install_installation_integrity_check(
    startup_host,
    *,
    updater_feature,
    request_repair,
    log_startup_metric=None,
) -> None:
    bridge = _IntegrityBridge(QCoreApplication.instance())

    def _on_report(report: object) -> None:
        if not is_startup_host_alive(startup_host):
            return
        if report is None or getattr(report, "ok", True) or not getattr(report, "checked", False):
            return
        log(
            f"Проверка целостности нашла расхождение ({report.cause.value}): "
            f"нет {len(report.missing)}, повреждено {len(report.corrupted)}",
            _INTEGRITY_LOG_LEVEL,
        )
        try:
            request_repair(report)
        except Exception as exc:
            log(f"Не удалось запросить восстановление поставки: {exc}", "❌ ERROR")

    bridge.result_ready.connect(_on_report)

    def _worker() -> None:
        deep = _first_run_after_update()
        try:
            report = updater_feature.check_installation_integrity(deep=deep)
        except Exception as exc:
            log(f"Проверка целостности не выполнена: {exc}", "WARNING")
            bridge.result_ready.emit(None)
            return
        if callable(log_startup_metric):
            try:
                log_startup_metric(
                    "StartupInstallationIntegrity",
                    f"{report.checked_files} файлов, deep={deep}",
                )
            except Exception:
                pass
        bridge.result_ready.emit(report)

    def _schedule() -> None:
        if not is_startup_host_alive(startup_host):
            return
        schedule_after(
            _INTEGRITY_CHECK_DELAY_MS,
            lambda: is_startup_host_alive(startup_host)
            and enqueue_subsystem_task("update", "InstallationIntegrityWorker", _worker),
        )

    bind_startup_gate(
        startup_host.startup_post_init_ready,
        _schedule,
        is_ready=lambda: bool(startup_host.startup_state.post_init_ready),
    )


__all__ = ["install_installation_integrity_check"]
