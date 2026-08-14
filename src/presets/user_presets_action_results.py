from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class UserPresetActionResult:
    ok: bool
    log_level: str
    log_message: str
    infobar_level: str | None
    infobar_title: str
    infobar_content: str
    structure_changed: bool
    switched_file_name: str | None = None
    error_code: str | None = None
    preset_file_name: str | None = None
    preset_display_name: str | None = None


@dataclass(slots=True)
class UserPresetImportResult:
    ok: bool
    actual_name: str
    actual_file_name: str
    requested_name: str
    log_level: str
    log_message: str
    infobar_level: str
    infobar_title: str
    infobar_content: str
    structure_changed: bool
    # Импорт по уже привязанной ссылке обновил существующий пресет,
    # нового файла не появилось — страница не должна добавлять строку.
    updated_existing: bool = False


@dataclass(slots=True)
class UserPresetResetAllResult:
    ok: bool
    success_count: int
    total_count: int
    failed_count: int
    log_level: str
    log_message: str
    structure_changed: bool
    switched_file_name: str | None


@dataclass(slots=True)
class UserPresetActivationResult:
    ok: bool
    log_level: str
    log_message: str
    infobar_level: str | None
    infobar_title: str
    infobar_content: str
    activated_file_name: str | None
