from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True, init=False)
class UpdaterFeature:
    _check_coordinator: Any | None = field(default=None, repr=False, compare=False)

    def __init__(self, check_coordinator: Any | None = None) -> None:
        object.__setattr__(self, "_check_coordinator", check_coordinator)

    @staticmethod
    def _commands():
        import updater.public as updater_commands

        return updater_commands

    @staticmethod
    def _create_check_coordinator():
        from core.runtime.update_check_coordinator import UpdateCheckCoordinator

        from app.ui_thread_marshaller import shared_ui_thread_marshaller

        return UpdateCheckCoordinator(ui_thread_marshaller_provider=shared_ui_thread_marshaller)

    @property
    def check_coordinator(self):
        coordinator = self._check_coordinator
        if coordinator is None:
            coordinator = self._create_check_coordinator()
            object.__setattr__(self, "_check_coordinator", coordinator)
        return coordinator

    def begin_update_check(self, *, source: str) -> int | None:
        return self.check_coordinator.begin(source=source)

    def finish_update_check(self, result: dict, *, source: str, token: int) -> bool:
        return bool(
            self.check_coordinator.finish(
                dict(result or {}),
                source=source,
                token=int(token),
            )
        )

    def current_update_check_snapshot(self):
        return self.check_coordinator.snapshot()

    def subscribe_update_check(self, callback, *, emit_initial: bool = False):
        return self.check_coordinator.subscribe(callback, emit_initial=bool(emit_initial))

    def is_auto_update_enabled(self) -> bool:
        return bool(self._commands().is_auto_update_enabled())

    def set_auto_update_enabled(self, enabled: bool) -> None:
        self._commands().set_auto_update_enabled(bool(enabled))

    # Все методы ниже ходят в settings.sqlite3 или в сеть — только из фона.
    def get_update_skipped_version(self) -> str:
        return str(self._commands().get_update_skipped_version() or "")

    def set_update_skipped_version(self, version: str) -> None:
        self._commands().set_update_skipped_version(str(version or ""))

    def note_auto_install_attempt(self, version: str) -> int:
        """Программа сама взялась ставить версию: попытка идёт в счёт лимита."""
        return int(self._commands().note_auto_install_attempt(str(version or "")))

    def remember_whats_new(self, version: str, history) -> None:
        self._commands().remember_whats_new(str(version or ""), tuple(history or ()))

    def startup_whats_new(self, app_version: str) -> tuple:
        return tuple(self._commands().startup_whats_new(str(app_version or "")))

    def mark_whats_new_seen(self, version: str) -> None:
        self._commands().mark_whats_new_seen(str(version or ""))

    def mark_update_app_ready(self, version: str) -> bool:
        """Новая версия открылась: окно-продолжение обновления может гаснуть."""
        return bool(self._commands().mark_update_app_ready(str(version or "")))

    def load_release_history(self, version: str) -> tuple:
        return tuple(self._commands().load_release_history(str(version or "")))

    def create_installation_repair_worker(
        self,
        request_id: int,
        *,
        report=None,
        allow_download: bool = True,
        parent=None,
    ):
        from updater.repair_workers import InstallationRepairWorker

        return InstallationRepairWorker(
            request_id,
            repair_installation=self.repair_installation,
            report=report,
            allow_download=bool(allow_download),
            parent=parent,
        )

    def check_installation_integrity(self, *, deep: bool = False):
        return self._commands().check_installation_integrity(deep=bool(deep))

    def repair_installation(self, report=None, *, allow_download: bool = True):
        return self._commands().repair_installation(report, allow_download=bool(allow_download))

    def run_startup_update_check(self, *, signalled: bool = False) -> dict:
        return self._commands().run_startup_update_check(signalled=bool(signalled))

    def create_release_watcher(self, *, on_release, on_queued):
        """Слушатель очереди обновлений на сервере; оба вызова приходят из фона."""
        return self._commands().create_release_watcher(on_release=on_release, on_queued=on_queued)

    def open_update_channel(self, channel: str):
        return self._commands().open_update_channel(channel)


def build_updater_feature() -> UpdaterFeature:
    return UpdaterFeature()
