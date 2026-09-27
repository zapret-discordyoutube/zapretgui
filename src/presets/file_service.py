from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from presets.models import PresetManifest
from presets.preset_file_ops import (
    create_preset as _create_preset,
    delete_by_file_name as _delete_by_file_name,
    duplicate_by_file_name as _duplicate_by_file_name,
    export_plain_text_by_file_name as _export_plain_text_by_file_name,
    import_from_file as _import_from_file,
    rename_by_file_name as _rename_by_file_name,
    reset_all_to_builtin as _reset_all_to_builtin,
    reset_to_builtin_by_file_name as _reset_to_builtin_by_file_name,
)
from presets.preset_contract import CONTRACT_MIGRATION_CHANGE_KIND, normalize_preset_source_for_save
from settings.mode import (
    ENGINE_WINWS1,
    ENGINE_WINWS2,
    PRESETS_SCOPE_WINWS1,
    PRESETS_SCOPE_WINWS2,
    engine_for_launch_method_or_none,
    normalize_launch_method,
)

if TYPE_CHECKING:
    from core.paths import AppPaths
    from presets.file_store import PresetFileStore
    from presets.ui_store import PresetUiStore
    from presets.selection_service import PresetSelectionService


_ENGINE_TO_HIERARCHY_SCOPE = {
    ENGINE_WINWS2: PRESETS_SCOPE_WINWS2,
    ENGINE_WINWS1: PRESETS_SCOPE_WINWS1,
}


def _with_final_newline(text: str) -> str:
    """Текст так, как его запишет хранилище: с переводом строки в конце."""
    return text if text.endswith("\n") else f"{text}\n"


@dataclass(frozen=True)
class PresetContractMigrationResult:
    """Итог разового перевода пресетов пользователя (preset_contract, пункт 6)."""

    migrated: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PresetFileService:
    """File-only service for preset mode management.

    Этот сервис работает только с физическими preset-файлами: создать,
    переименовать, импортировать, сбросить, сохранить текст. Он не строит
    список profile и не читает содержимое preset-а.
    """

    engine: str
    launch_method: str
    app_paths: "AppPaths"
    preset_mode_coordinator: object
    preset_file_store: "PresetFileStore"
    preset_selection_service: "PresetSelectionService"
    preset_store_winws2: "PresetUiStore"
    preset_store_winws1: "PresetUiStore"

    @classmethod
    def from_launch_method(cls, launch_method: str, *, preset_services) -> "PresetFileService":
        method = normalize_launch_method(launch_method, default="")
        engine = engine_for_launch_method_or_none(method)
        if engine is None:
            raise ValueError(f"Unsupported preset launch method: {launch_method}")
        return cls(
            engine=engine,
            launch_method=method,
            app_paths=preset_services.app_paths,
            preset_mode_coordinator=preset_services.preset_mode_coordinator,
            preset_file_store=preset_services.preset_file_store,
            preset_selection_service=preset_services.preset_selection_service,
            preset_store_winws2=preset_services.preset_store_winws2,
            preset_store_winws1=preset_services.preset_store_winws1,
        )

    def _ui_store(self):
        if self.engine == ENGINE_WINWS2:
            return self.preset_store_winws2
        if self.engine == ENGINE_WINWS1:
            return self.preset_store_winws1
        raise ValueError(f"Unsupported preset mode engine: {self.engine}")

    def _folder_scope_key(self) -> str:
        scope_key = _ENGINE_TO_HIERARCHY_SCOPE.get(self.engine)
        if scope_key is None:
            raise ValueError(f"Unsupported preset mode engine: {self.engine}")
        return scope_key

    def _rename_folder_item_meta(
        self,
        old_file_name: str,
        new_file_name: str,
    ) -> None:
        try:
            from presets.folders import rename_preset_item_meta

            rename_preset_item_meta(self._folder_scope_key(), old_file_name, new_file_name)
        except Exception:
            pass

    def _copy_folder_item_meta(
        self,
        source_file_name: str,
        new_file_name: str,
    ) -> None:
        try:
            from presets.folders import copy_preset_item_meta

            copy_preset_item_meta(self._folder_scope_key(), source_file_name, new_file_name)
        except Exception:
            pass

    def _delete_folder_item_meta(self, preset_file_name: str) -> None:
        try:
            from presets.folders import delete_preset_item_meta

            delete_preset_item_meta(self._folder_scope_key(), preset_file_name)
        except Exception:
            pass

    def _rename_remote_binding_meta(self, old_file_name: str, new_file_name: str) -> None:
        try:
            from presets.remote_bindings import rename_remote_preset_binding

            rename_remote_preset_binding(self.engine, old_file_name, new_file_name)
        except Exception:
            pass

    def _delete_remote_binding_meta(self, preset_file_name: str) -> None:
        try:
            from presets.remote_bindings import delete_preset_identity

            delete_preset_identity(self.engine, preset_file_name)
        except Exception:
            pass

    def _carry_remote_sync_hash(self, file_name: str, old_text: str, new_text: str) -> None:
        """Разовый перевод — действие программы, а не правка пользователя.

        Если пресет привязан к источнику и до перевода совпадал с последней
        синхронизацией, запомненный хэш переносится на новый текст. Иначе
        автосинк принял бы добавленные строки за локальную правку и отвязал
        пресет от источника.
        """
        try:
            from presets.remote_bindings import get_remote_preset_binding, update_remote_preset_binding
            from presets.remote_sync import comparison_hash

            binding = get_remote_preset_binding(self.engine, file_name)
            if not binding or str(binding.get("synced_hash") or "") != comparison_hash(old_text):
                return
            update_remote_preset_binding(self.engine, file_name, synced_hash=comparison_hash(new_text))
        except Exception:
            pass

    def _refresh_selected_source_preset(self) -> None:
        selected_file_name = self.get_selected_file_name()
        if not selected_file_name or self.get_manifest_by_file_name(selected_file_name) is None:
            return
        self.preset_mode_coordinator.refresh_selected_launch_preset(self.launch_method)

    def list_manifests(self) -> list[PresetManifest]:
        return self.preset_file_store.list_manifests(self.engine)

    def notify_preset_content_changed(self, file_name: str, *, content_change_kind: str = "") -> None:
        candidate = str(file_name or "").strip()
        if candidate:
            notify = self._ui_store().notify_preset_content_changed
            clean_kind = str(content_change_kind or "").strip()
            if clean_kind:
                try:
                    notify(candidate, content_change_kind=clean_kind)
                    return
                except TypeError as exc:
                    if "content_change_kind" not in str(exc):
                        raise
            notify(candidate)

    def notify_preset_switched(self, file_name: str) -> None:
        candidate = str(file_name or "").strip()
        if candidate:
            self._ui_store().notify_preset_switched(candidate)

    def notify_preset_identity_changed(self, file_name: str) -> None:
        candidate = str(file_name or "").strip()
        if candidate:
            self._ui_store().notify_preset_identity_changed(candidate)

    def notify_presets_changed(self) -> None:
        self._ui_store().notify_presets_changed()

    def activate_preset_file(self, file_name: str):
        return self.select_file_name(file_name)

    def select_file_name(self, file_name: str):
        previous_file_name = str(self.get_selected_file_name() or "").strip()
        profile = self.preset_mode_coordinator.select_preset_file_name(self.launch_method, file_name)
        next_file_name = str(getattr(profile, "preset_file_name", "") or "").strip()
        if previous_file_name and next_file_name and previous_file_name.lower() == next_file_name.lower():
            return profile
        self.notify_preset_switched(profile.preset_file_name)
        return profile

    def get_selected_manifest(self) -> PresetManifest | None:
        try:
            return self.preset_mode_coordinator.get_selected_source_manifest(self.launch_method)
        except Exception:
            return None

    def get_selected_file_name(self) -> str:
        preset = self.get_selected_manifest()
        return preset.file_name if preset is not None else ""

    def is_selected_file_name(self, file_name: str) -> bool:
        current = str(self.get_selected_file_name() or "").strip()
        candidate = str(self.preset_file_store.resolve_file_name(self.engine, file_name) or file_name or "").strip()
        return bool(current and candidate and current.lower() == candidate.lower())

    def get_manifest_by_file_name(self, file_name: str) -> PresetManifest | None:
        return self.preset_file_store.get_manifest(self.engine, file_name)

    def get_source_path_by_file_name(self, file_name: str) -> Path:
        return self.preset_file_store.get_source_path(self.engine, file_name)

    def read_source_text_by_file_name(self, file_name: str) -> str:
        manifest = self.get_manifest_by_file_name(file_name)
        if manifest is None:
            raise ValueError(f"Preset not found: {file_name}")
        return self.preset_file_store.read_source_text(self.engine, manifest.file_name)

    def read_selected_source_text(self) -> str:
        selected_file_name = self.get_selected_file_name()
        if not selected_file_name:
            return ""
        return self.read_source_text_by_file_name(selected_file_name)

    def normalize_source_text(self, source_text: str) -> str:
        return normalize_preset_source_for_save(source_text, self.engine)

    def publish_preset_content_changed_by_file_name(
        self,
        file_name: str,
        *,
        content_change_kind: str = "",
    ) -> PresetManifest:
        manifest = self.get_manifest_by_file_name(file_name)
        if manifest is None:
            raise ValueError(f"Preset not found: {file_name}")
        if self.is_selected_file_name(manifest.file_name):
            self.preset_mode_coordinator.refresh_selected_launch_preset(self.launch_method)
        self.notify_preset_content_changed(
            manifest.file_name,
            content_change_kind=content_change_kind,
        )
        return manifest

    def save_source_text_by_file_name(
        self,
        file_name: str,
        source_text: str,
        *,
        publish_content_changed: bool = True,
        content_change_kind: str = "",
    ) -> PresetManifest:
        manifest = self.get_manifest_by_file_name(file_name)
        if manifest is None:
            raise ValueError(f"Preset not found: {file_name}")
        normalized = self.normalize_source_text(source_text)
        current_text = self.read_source_text_by_file_name(manifest.file_name)
        if normalized == self.normalize_source_text(current_text):
            return manifest
        updated = self.preset_file_store.update_preset(self.engine, manifest.file_name, normalized, None)
        if publish_content_changed:
            self.publish_preset_content_changed_by_file_name(
                updated.file_name,
                content_change_kind=content_change_kind,
            )
        return updated

    def migrate_user_presets_to_save_contract(self) -> PresetContractMigrationResult:
        """Разовый перевод пресетов из папки пользователя в формат сохранения.

        Пункт 6 договора ``presets.preset_contract``: каждый пресет пользователя
        проходит ту же нормализацию, что и при сохранении (для winws2 — полный
        блок ``--lua-init``), и записывается, только если текст на диске от
        этого изменится. Встроенные пресеты не трогаются: запись в хранилище
        создала бы для них скрытую копию в папке пользователя.

        Обычное ``save_source_text_by_file_name`` здесь не подходит: оно
        сравнивает нормализованный текст с нормализованным текущим, и файл без
        блока с его точки зрения «не изменился».

        Если меняются аргументы запуска, программа узнаёт об изменении тем же
        путём, что и при обычном сохранении: у активного пресета при работающем
        winws2 это один перезапуск, после которого запущено ровно то, что
        записано в файле. Если аргументы те же (ушли только служебные строки
        шапки), файл пишется без оповещения, и winws2 не перезапускается.
        Привязка к источнику (автосинк) после перевода не отвязывается.
        Файл, который не удалось прочитать или записать, пропускается.
        """
        from winws_runtime.runners.preset_runner_support import launch_args_from_preset_text

        migrated: list[str] = []
        failed: list[tuple[str, str]] = []
        for manifest in self.list_manifests():
            if str(manifest.storage_scope or "").strip().lower() != "user":
                continue
            file_name = manifest.file_name
            try:
                current_text = self.preset_file_store.read_source_text(self.engine, file_name)
                normalized = self.normalize_source_text(current_text)
                if normalized == _with_final_newline(current_text):
                    continue
                self.preset_file_store.update_preset(self.engine, file_name, normalized, None)
            except Exception as exc:
                failed.append((file_name, str(exc) or type(exc).__name__))
                continue
            migrated.append(file_name)
            self._carry_remote_sync_hash(file_name, current_text, normalized)
            if launch_args_from_preset_text(normalized) == launch_args_from_preset_text(current_text):
                continue
            try:
                self.publish_preset_content_changed_by_file_name(
                    file_name,
                    content_change_kind=CONTRACT_MIGRATION_CHANGE_KIND,
                )
            except Exception as exc:
                failed.append((file_name, f"файл записан, но программа не узнала об изменении: {exc}"))
        return PresetContractMigrationResult(migrated=tuple(migrated), failed=tuple(failed))

    def save_selected_source_text(self, source_text: str, *, content_change_kind: str = "") -> PresetManifest:
        selected_file_name = self.get_selected_file_name()
        if not selected_file_name:
            raise ValueError("Selected preset is required")
        return self.save_source_text_by_file_name(
            selected_file_name,
            source_text,
            content_change_kind=content_change_kind,
        )

    def rename_by_file_name(self, file_name: str, new_name: str) -> PresetManifest:
        return _rename_by_file_name(self, file_name, new_name)

    def duplicate_by_file_name(self, file_name: str, new_name: str) -> PresetManifest:
        return _duplicate_by_file_name(self, file_name, new_name)

    def create(self, name: str, *, from_current: bool = True) -> PresetManifest:
        return _create_preset(self, name, from_current=from_current)

    def import_from_file(self, src_path: Path, name: str | None = None):
        return _import_from_file(self, src_path, name=name)

    def export_plain_text_by_file_name(self, file_name: str, dest_path: Path):
        return _export_plain_text_by_file_name(self, file_name, dest_path)

    def reset_to_builtin_by_file_name(self, file_name: str) -> PresetManifest:
        return _reset_to_builtin_by_file_name(self, file_name)

    def reset_all_to_builtin(self) -> tuple[int, int, list[str]]:
        return _reset_all_to_builtin(self)

    def delete_by_file_name(self, file_name: str) -> None:
        _delete_by_file_name(self, file_name)
