from __future__ import annotations

from pathlib import Path

from settings.mode import DEFAULT_PRESET_FILE_NAME_BY_ENGINE
from presets.builtin_catalog import list_builtin_presets
from presets.builtin_reset_support import (
    reset_all_builtin_overrides as _reset_all_builtin_overrides,
)
from presets.preset_text_ops import (
    _header_preset_kind,
    _rewrite_preset_headers,
    validate_preset_source_text,
)
from presets.portable_archive import (
    PresetImportResult,
    export_preset_with_lists,
    import_portable_preset,
)


def _read_standard_builtin_preset(backend) -> str:
    file_name = DEFAULT_PRESET_FILE_NAME_BY_ENGINE.get(backend.engine, "")
    if not file_name:
        raise ValueError(f"Default preset is not configured for engine: {backend.engine}")

    engine_paths = backend.app_paths.engine_paths(backend.engine).ensure_directories()
    source_path = engine_paths.builtin_presets_dir / file_name
    if not source_path.is_file():
        raise ValueError(f"Default built-in preset not found: {source_path}")
    return source_path.read_text(encoding="utf-8", errors="replace")


def _remove_active_remote_binding_after_reset(backend, file_name: str) -> None:
    """Удаляет активную URL-привязку после удаления user-копии.

    ``auto=False`` означает не удалённую привязку, а запомненный URL после
    действия «Отвязать». Её нельзя удалять при сбросе: повторный импорт той
    же ссылки должен найти прежнюю запись и не создать дубликат. Активная
    привязка, наоборот, больше не должна относиться к восстановленному
    builtin-файлу — иначе автосинхронизация снова создаст user-копию.
    """
    key = str(file_name or "").strip()
    if not key:
        return
    try:
        delete_identity = getattr(backend, "_delete_remote_binding_meta", None)
        if not callable(delete_identity):
            return
        from presets.remote_bindings import get_remote_preset_binding

        binding = get_remote_preset_binding(str(getattr(backend, "engine", "") or ""), key)
        if binding is None or not bool(binding.get("auto", True)):
            return
        delete_identity(key)
    except Exception:
        # Сброс файла не должен превращаться в ошибку из-за недоступного
        # хранилища привязок. Сам wrapper backend-а также изолирует такие
        # ошибки, если он доступен.
        return


def _existing_builtin_override_paths(backend) -> dict[str, Path]:
    """Возвращает user-файлы, которые соответствуют текущим builtin-файлам."""
    try:
        engine = str(getattr(backend, "engine", "") or "").strip()
        engine_paths = backend.app_paths.engine_paths(engine).ensure_directories()
        user_dir = engine_paths.user_presets_dir
        return {
            builtin_path.name: user_dir / builtin_path.name
            for builtin_path in list_builtin_presets(engine_paths.builtin_presets_dir)
            if (user_dir / builtin_path.name).exists()
        }
    except Exception:
        return {}


def _user_preset_path(backend, file_name: str) -> Path | None:
    try:
        engine = str(getattr(backend, "engine", "") or "").strip()
        engine_paths = backend.app_paths.engine_paths(engine).ensure_directories()
        return engine_paths.user_presets_dir / str(file_name or "").strip()
    except Exception:
        return None


def rename_by_file_name(backend, file_name: str, new_name: str):
    manifest = backend.get_manifest_by_file_name(file_name)
    if manifest is None:
        raise ValueError(f"Preset not found: {file_name}")
    if str(manifest.kind or "").strip().lower() == "builtin":
        raise ValueError(f"Built-in preset cannot be renamed: {manifest.name}")
    was_selected = backend.is_selected_file_name(manifest.file_name)
    source_text = backend.read_source_text_by_file_name(manifest.file_name)
    renamed = backend.preset_file_store.rename_preset(backend.engine, manifest.file_name, new_name)
    rewritten = _rewrite_preset_headers(
        source_text,
        new_name,
        preset_kind=_header_preset_kind(manifest.kind),
    )
    rewritten = backend.normalize_source_text(rewritten)
    updated = backend.preset_file_store.update_preset(backend.engine, renamed.file_name, rewritten, None)
    backend._rename_folder_item_meta(
        manifest.file_name,
        updated.file_name,
    )
    backend._rename_remote_binding_meta(
        manifest.file_name,
        updated.file_name,
    )
    if was_selected:
        backend.preset_selection_service.select_preset(backend.engine, updated.file_name)
        backend.notify_preset_identity_changed(updated.file_name)
        backend._refresh_selected_source_preset()
    return updated


def duplicate_by_file_name(backend, file_name: str, new_name: str):
    manifest = backend.get_manifest_by_file_name(file_name)
    if manifest is None:
        raise ValueError(f"Preset not found: {file_name}")
    source_text = backend.read_source_text_by_file_name(manifest.file_name)
    rewritten = _rewrite_preset_headers(
        source_text,
        new_name,
        preset_kind=_header_preset_kind(manifest.kind),
    )
    rewritten = backend.normalize_source_text(rewritten)
    duplicated = backend.preset_file_store.create_preset(backend.engine, new_name, rewritten)
    backend._copy_folder_item_meta(
        manifest.file_name,
        duplicated.file_name,
    )
    backend.notify_presets_changed()
    return duplicated


def create_preset(backend, name: str, *, from_current: bool = True):
    source_text = backend.read_selected_source_text() if from_current else _read_standard_builtin_preset(backend)
    rewritten = _rewrite_preset_headers(source_text, name)
    rewritten = backend.normalize_source_text(rewritten)
    created = backend.preset_file_store.create_preset(backend.engine, name, rewritten)
    backend.notify_presets_changed()
    return created


def import_from_file(backend, src_path: Path, name: str | None = None):
    src = Path(src_path)
    if not src.exists():
        raise ValueError(f"Import source not found: {src}")
    preset_name = str(name or src.stem or "Imported").strip() or "Imported"
    if src.suffix.lower() == ".zip":
        return import_portable_preset(backend, src, name=preset_name)
    source_text = src.read_text(encoding="utf-8", errors="replace")
    validation_error = validate_preset_source_text(source_text, engine=backend.engine)
    if validation_error:
        raise ValueError(f"Файл не похож на пресет: {validation_error}")
    rewritten = _rewrite_preset_headers(
        source_text,
        preset_name,
        preset_kind="imported",
    )
    rewritten = backend.normalize_source_text(rewritten)
    imported = backend.preset_file_store.create_preset(backend.engine, preset_name, rewritten, kind="imported")
    backend._delete_folder_item_meta(imported.file_name)
    backend.notify_presets_changed()
    return PresetImportResult(manifest=imported)


def export_plain_text_by_file_name(backend, file_name: str, dest_path: Path):
    return export_preset_with_lists(backend, file_name, Path(dest_path))


def reset_to_builtin_by_file_name(backend, file_name: str):
    manifest = backend.get_manifest_by_file_name(file_name)
    if manifest is None:
        raise ValueError(f"Preset not found: {file_name}")
    builtin_path = backend.app_paths.engine_paths(backend.engine).ensure_directories().builtin_presets_dir / manifest.file_name
    if str(manifest.kind or "").strip().lower() == "builtin":
        return manifest
    if builtin_path.exists():
        user_path = _user_preset_path(backend, manifest.file_name)
        user_existed = bool(user_path is not None and user_path.exists())
        backend.preset_file_store.delete_preset(backend.engine, manifest.file_name)
        updated = backend.get_manifest_by_file_name(manifest.file_name)
        if updated is None:
            raise ValueError("Built-in preset not found after reset")
        if user_existed and user_path is not None and not user_path.exists():
            _remove_active_remote_binding_after_reset(backend, manifest.file_name)
        if backend.is_selected_file_name(manifest.file_name):
            backend._refresh_selected_source_preset()
        backend.notify_preset_content_changed(updated.file_name)
        return updated
    raise ValueError(
        "Сброс невозможен: для этого пользовательского preset-а нет встроенного preset-а с таким же именем файла."
    )


def reset_all_to_builtin(backend) -> tuple[int, int, list[str]]:
    override_paths = _existing_builtin_override_paths(backend)
    result = _reset_all_builtin_overrides(backend.engine, backend.app_paths)
    for file_name, user_path in override_paths.items():
        if not user_path.exists():
            _remove_active_remote_binding_after_reset(backend, file_name)
    backend.notify_presets_changed()
    selected_file_name = backend.get_selected_file_name()
    if selected_file_name and backend.get_manifest_by_file_name(selected_file_name) is not None:
        backend._refresh_selected_source_preset()
        backend.notify_preset_content_changed(selected_file_name)
    return result


def delete_by_file_name(backend, file_name: str) -> None:
    manifest = backend.get_manifest_by_file_name(file_name)
    if manifest is None:
        raise ValueError(f"Preset not found: {file_name}")
    if str(manifest.kind or "").strip().lower() == "builtin":
        raise ValueError(f"Built-in preset cannot be deleted: {manifest.name}")
    backend.preset_selection_service.ensure_can_delete(backend.engine, manifest.file_name)
    backend.preset_file_store.delete_preset(backend.engine, manifest.file_name)
    backend._delete_folder_item_meta(manifest.file_name)
    backend._delete_remote_binding_meta(manifest.file_name)
    backend.notify_presets_changed()
