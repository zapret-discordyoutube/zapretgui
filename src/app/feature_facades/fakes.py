from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FakesFeature:
    """Вход в фейки winws2 для страницы «Фейки»: реестр и свои фейки.

    Всё, что читает диск или базу, выполняется в фоновых worker-ах.
    """

    _app_paths: Any

    @staticmethod
    def _application_paths():
        from config.runtime_layout import APPLICATION_PATHS

        return APPLICATION_PATHS

    def _load_winws2_strategy_catalogs(self):
        from profile.strategy_catalog import load_strategy_catalogs

        return load_strategy_catalogs(self._app_paths, "winws2")

    def build_snapshot(self):
        from fakes.public import build_fakes_page_snapshot

        return build_fakes_page_snapshot(
            application_paths=self._application_paths(),
            load_strategy_catalogs=self._load_winws2_strategy_catalogs,
        )

    def import_user_fake(self, *, source_path: str, name: str, description: str = ""):
        from fakes.public import current_fake_name_rules, import_user_fake

        paths = self._application_paths()
        return import_user_fake(
            source_path,
            name=name,
            description=description,
            user_fakes_dir=paths.user_fakes_dir,
            rules=current_fake_name_rules(paths),
        )

    def delete_user_fake(self, *, name: str) -> str:
        from fakes.public import delete_user_fake

        delete_user_fake(name, user_fakes_dir=self._application_paths().user_fakes_dir)
        return str(name)

    def open_user_fakes_folder(self) -> None:
        from fakes.public import open_user_fakes_folder

        open_user_fakes_folder(self._application_paths().user_fakes_dir)

    @staticmethod
    def _worker(request_id: int, *, task, task_name: str, parent=None):
        from fakes.workers import FakesTaskWorker

        return FakesTaskWorker(request_id, task=task, task_name=task_name, parent=parent)

    def create_snapshot_worker(self, request_id: int, *, parent=None):
        return self._worker(request_id, task=self.build_snapshot, task_name="чтение фейков", parent=parent)

    def create_import_worker(self, request_id: int, *, source_path: str, name: str, description: str = "", parent=None):
        return self._worker(
            request_id,
            task=lambda: self.import_user_fake(source_path=source_path, name=name, description=description),
            task_name="добавление фейка",
            parent=parent,
        )

    def create_delete_worker(self, request_id: int, *, name: str, parent=None):
        return self._worker(
            request_id,
            task=lambda: self.delete_user_fake(name=name),
            task_name="удаление фейка",
            parent=parent,
        )


def build_fakes_feature(paths: Any) -> FakesFeature:
    return FakesFeature(_app_paths=paths)


__all__ = ["FakesFeature", "build_fakes_feature"]
