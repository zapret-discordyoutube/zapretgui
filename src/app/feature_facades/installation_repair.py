from __future__ import annotations

"""Порт восстановления поставки.

Runtime знает только то, что установка повреждена, и публикует событие.
Слой приложения решает, что с этим делать: запустить фоновую починку тем же
конвейером, которым программа обновляется, и один раз сказать об этом
пользователю. Так winws_runtime не получает зависимость на updater.
"""

from dataclasses import dataclass, field
from typing import Any

from app_notifications import advisory_notification
from log.log import log
from ui.one_shot_worker_runtime import OneShotWorkerRuntime


_REPAIR_LOG_LEVEL = "🩹 REPAIR"


@dataclass(slots=True)
class InstallationRepairPort:
    updater_feature: Any
    ui_port: Any = None
    _runtime: OneShotWorkerRuntime = field(default_factory=OneShotWorkerRuntime)
    _handled_signatures: set = field(default_factory=set)

    def request_repair(self, report) -> bool:
        """Запускает починку под отчёт целостности. Повторы гасятся."""
        if report is None:
            return False

        signature = self._signature(report)
        if signature in self._handled_signatures:
            return False
        if self._runtime.is_running():
            return False
        self._handled_signatures.add(signature)

        from install_integrity import describe_report

        message = describe_report(report, repair_started=True)
        log(f"Запуск восстановления поставки: {message.as_line()}", _REPAIR_LOG_LEVEL)
        self._notify(
            level="warning",
            title=message.title,
            content=message.content,
            dedupe_key=f"installation.repair:{signature}",
        )

        try:
            self._runtime.start_qthread_worker(
                worker_factory=lambda request_id: self.updater_feature.create_installation_repair_worker(
                    request_id,
                    report=report,
                ),
                on_loaded=self._on_repair_finished,
                on_failed=self._on_repair_failed,
            )
        except Exception as exc:
            log(f"Не удалось запустить восстановление поставки: {exc}", "❌ ERROR")
            return False
        return True

    def configure_ui_port(self, ui_port) -> None:
        self.ui_port = ui_port

    def shutdown(self) -> None:
        self._runtime.stop(blocking=False, log_fn=log, warning_prefix="installation_repair")
        self._runtime.cancel()

    def _signature(self, report) -> str:
        cause = str(getattr(getattr(report, "cause", None), "value", "") or "")
        paths = tuple(getattr(report, "missing", ()) or ()) + tuple(getattr(report, "corrupted", ()) or ())
        return f"{cause}|{'|'.join(sorted(paths))}"

    def _notify(self, *, level: str, title: str, content: str, dedupe_key: str) -> None:
        ui_port = self.ui_port
        notify = getattr(ui_port, "notify", None) if ui_port is not None else None
        if not callable(notify):
            return
        try:
            notify(
                advisory_notification(
                    level=level,
                    title=title,
                    content=content,
                    source="installation.repair",
                    presentation="infobar",
                    queue="immediate",
                    duration=10000,
                    dedupe_key=dedupe_key,
                )
            )
        except Exception:
            return

    def _on_repair_finished(self, request_id: int, started: bool, reason: str, source: str) -> None:
        if not self._runtime.is_current(request_id):
            return
        if started:
            log(
                f"Установщик восстановления запущен ({source or 'unknown'}); программа перезапустится",
                _REPAIR_LOG_LEVEL,
            )
            self._notify(
                level="success",
                title="Восстановление запущено",
                content="Программа переустановит свои файлы и перезапустится.",
                dedupe_key="installation.repair:started",
            )
            return

        log(f"Восстановление поставки не выполнено: {reason}", _REPAIR_LOG_LEVEL)
        self._notify(
            level="error",
            title="Не удалось восстановить файлы",
            content=str(reason or "Переустановите программу вручную."),
            dedupe_key="installation.repair:failed",
        )

    def _on_repair_failed(self, request_id: int, error: str) -> None:
        if not self._runtime.is_current(request_id):
            return
        log(f"Ошибка восстановления поставки: {error}", "❌ ERROR")
        self._notify(
            level="error",
            title="Не удалось восстановить файлы",
            content=str(error or "Переустановите программу вручную."),
            dedupe_key="installation.repair:failed",
        )


def build_installation_repair_port(*, updater_feature, ui_port=None) -> InstallationRepairPort:
    return InstallationRepairPort(updater_feature=updater_feature, ui_port=ui_port)


__all__ = ["InstallationRepairPort", "build_installation_repair_port"]
