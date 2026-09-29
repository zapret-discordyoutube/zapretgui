"""Workflow сырого редактора preset-файла."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class RawPresetLoadResult:
    text: str
    footer_text: str
    file_name: str = ""
    display_name: str = ""
    path: Path | None = None
    origin: str = "user"
    can_reset_to_builtin: bool = False
    active_file_name: str = ""
    active_name: str = ""


@dataclass(frozen=True)
class RawPresetSaveResult:
    updated: object
    path: Path
    footer_text: str
    can_reset_to_builtin: bool = False
    # Текст, который редактор отдал на сохранение, и текст файла после
    # сохранения. Они различаются, когда сохранение нормализовало текст
    # (presets.preset_contract) — тогда редактор показывает сохранённый текст.
    requested_text: str = ""
    saved_text: str = ""


def load_raw_preset_text(path: Path | None) -> RawPresetLoadResult:
    """Читает текст preset-файла для редактора."""
    from presets.commands import read_raw_preset_text

    text, found = read_raw_preset_text(path)
    if not found:
        return RawPresetLoadResult(text="", footer_text="Файл не найден", path=path)
    return RawPresetLoadResult(
        text=text,
        footer_text="Загружено",
        path=path,
    )


def save_raw_preset_text(
    *,
    presets_feature,
    launch_method: str | None,
    file_name: str,
    source_text: str,
    publish_content_changed: bool = True,
) -> RawPresetSaveResult:
    """Сохраняет текст preset-файла через presets feature.

    Автосохранение (``publish_content_changed=False``) только пишет файл.
    Фиксация правки (``publish_content_changed=True``) пишет файл и всегда
    сообщает запуску об изменении: текст мог быть записан ещё автосохранением,
    и тогда само сохранение уже ничего не меняет и ничего не публикует.
    """
    if not file_name:
        raise ValueError("Не удалось определить имя файла пресета для сохранения.")
    requested_text = str(source_text or "")
    updated = presets_feature.save_preset_source_by_file_name(
        launch_method,
        file_name,
        requested_text,
        publish_content_changed=False,
        content_change_kind="editor_save",
    )
    if publish_content_changed:
        publish_raw_preset_content_changed(
            presets_feature=presets_feature,
            launch_method=launch_method,
            file_name=updated.file_name,
        )
    path = presets_feature.get_preset_source_path_by_file_name(
        launch_method,
        updated.file_name,
    )
    saved_text = presets_feature.read_preset_source_by_file_name(launch_method, updated.file_name)
    return RawPresetSaveResult(
        updated=updated,
        path=path,
        footer_text=f"Сохранено {datetime.now().strftime('%H:%M:%S')}",
        can_reset_to_builtin=preset_differs_from_builtin(
            presets_feature=presets_feature,
            launch_method=launch_method,
            file_name=updated.file_name,
        ),
        requested_text=requested_text,
        saved_text=str(saved_text or ""),
    )


def get_raw_preset_source_path(*, presets_feature, launch_method: str | None, file_name: str) -> Path:
    return presets_feature.get_preset_source_path_by_file_name(
        launch_method,
        str(file_name or "").strip(),
    )


def get_raw_preset_manifest(*, presets_feature, launch_method: str | None, file_name: str):
    if not file_name:
        return None
    return presets_feature.get_preset_manifest_by_file_name(
        launch_method,
        file_name,
    )


def load_raw_preset_for_file(*, presets_feature, launch_method: str | None, file_name: str) -> RawPresetLoadResult:
    candidate = str(file_name or "").strip()
    if not candidate:
        return load_raw_preset_text(None)

    manifest = get_raw_preset_manifest(
        presets_feature=presets_feature,
        launch_method=launch_method,
        file_name=candidate,
    )
    resolved_file_name = candidate
    display_name = Path(candidate).stem
    origin = "user"
    if manifest is not None:
        resolved_file_name = str(manifest.file_name or candidate).strip() or candidate
        display_name = str(manifest.name or display_name).strip() or display_name
        origin = str(manifest.kind or "user").strip().lower() or "user"

    path = get_raw_preset_source_path(
        presets_feature=presets_feature,
        launch_method=launch_method,
        file_name=resolved_file_name,
    )
    text_result = load_raw_preset_text(path)
    active_file_name = get_selected_raw_preset_file_name(
        presets_feature=presets_feature,
        launch_method=launch_method,
    )
    active_name = get_selected_raw_preset_name(
        presets_feature=presets_feature,
        launch_method=launch_method,
    )
    return RawPresetLoadResult(
        text=text_result.text,
        footer_text=text_result.footer_text,
        file_name=resolved_file_name,
        display_name=display_name,
        path=path,
        origin=origin,
        can_reset_to_builtin=preset_differs_from_builtin(
            presets_feature=presets_feature,
            launch_method=launch_method,
            file_name=resolved_file_name,
        ),
        active_file_name=active_file_name,
        active_name=active_name,
    )


def preset_differs_from_builtin(*, presets_feature, launch_method: str | None, file_name: str) -> bool:
    checker = getattr(presets_feature, "preset_differs_from_builtin_by_file_name", None)
    if not callable(checker):
        return False
    try:
        return bool(checker(launch_method, file_name))
    except Exception:
        return False


def open_raw_preset_source_file(*, presets_feature, path: Path | None) -> None:
    if path is None:
        return
    presets_feature.open_preset_source_file(path)


def rename_raw_preset(*, presets_feature, launch_method: str | None, file_name: str, new_name: str):
    if not file_name:
        raise ValueError("Не удалось определить имя файла пресета для переименования.")
    return presets_feature.rename_preset_by_file_name(
        launch_method,
        file_name,
        new_name,
    )


def duplicate_raw_preset(*, presets_feature, launch_method: str | None, file_name: str, new_name: str):
    if not file_name:
        raise ValueError("Не удалось определить имя файла пресета для дублирования.")
    return presets_feature.duplicate_preset_by_file_name(
        launch_method,
        file_name,
        new_name,
    )


def export_raw_preset(*, presets_feature, launch_method: str | None, file_name: str, target_path: str):
    if not file_name:
        raise ValueError("Не удалось определить имя файла пресета для экспорта.")
    return presets_feature.export_preset_plain_text(
        launch_method,
        file_name,
        target_path,
    )


def reset_raw_preset_to_builtin(*, presets_feature, launch_method: str | None, file_name: str):
    if not file_name:
        raise ValueError("Не удалось определить имя файла пресета для сброса.")
    return presets_feature.reset_preset_to_builtin_by_file_name(
        launch_method,
        file_name,
    )


def delete_raw_preset(*, presets_feature, launch_method: str | None, file_name: str) -> None:
    if not file_name:
        raise ValueError("Не удалось определить имя файла пресета для удаления.")
    presets_feature.delete_preset_by_file_name(
        launch_method,
        file_name,
    )


def get_selected_raw_preset_name(*, presets_feature, launch_method: str | None) -> str:
    selected = presets_feature.get_selected_source_preset_manifest(launch_method)
    return (selected.name if selected is not None else "").strip()


def get_selected_raw_preset_file_name(*, presets_feature, launch_method: str | None) -> str:
    return (presets_feature.get_selected_source_preset_file_name(launch_method) or "").strip()


def activate_raw_preset(*, presets_feature, launch_method: str | None, file_name: str) -> bool:
    if not file_name:
        return False
    presets_feature.activate_preset_file(
        launch_method,
        file_name,
    )
    return True


def publish_raw_preset_content_changed(*, presets_feature, launch_method: str | None, file_name: str) -> None:
    if not file_name:
        return
    presets_feature.publish_preset_content_changed(launch_method, file_name, content_change_kind="editor_save")
