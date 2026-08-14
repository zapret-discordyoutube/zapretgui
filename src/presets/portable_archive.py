"""Переносимый ZIP-архив preset-а и его пользовательских списков."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import zipfile

from lists.core.layered_files import (
    delete_profile_user_list_file,
    layered_list_file,
    safe_list_file_name,
    write_profile_user_list_text,
)
from profile.list_file_editor import validate_profile_list_file_text
from profile.parser import parse_preset_text
from presets.models import PresetManifest
from presets.preset_text_ops import _rewrite_preset_headers, validate_preset_source_text


ARCHIVE_FORMAT = "zapretgui-portable-preset"
ARCHIVE_VERSION = 1
_MANIFEST_PATH = "manifest.json"
_PRESET_PATH = "preset.txt"
_MAX_ARCHIVE_MEMBER_SIZE = 32 * 1024 * 1024
_MAX_ARCHIVE_TOTAL_SIZE = 128 * 1024 * 1024
_LIST_OPTION_KINDS = {
    "--hostlist": "hostlist",
    "--hostlist-exclude": "hostlist",
    "--ipset": "ipset",
    "--ipset-exclude": "ipset",
}
_LIST_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<option>--(?:hostlist|hostlist-exclude|ipset|ipset-exclude))"
    r"\s*=\s*(?P<value>.*?)(?P<trailing>\s*)$",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PortableListFile:
    file_name: str
    kind: str
    text: str
    storage: str


@dataclass(frozen=True, slots=True)
class PresetExportResult:
    path: Path
    archived_list_files: tuple[str, ...] = ()

    @property
    def is_archive(self) -> bool:
        return bool(self.archived_list_files)


@dataclass(frozen=True, slots=True)
class PresetImportResult:
    manifest: PresetManifest
    imported_list_files: tuple[str, ...] = ()
    renamed_list_files: tuple[tuple[str, str], ...] = ()

    @property
    def file_name(self) -> str:
        return self.manifest.file_name

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def updated_at(self) -> str:
        return self.manifest.updated_at

    @property
    def kind(self) -> str:
        return self.manifest.kind

    @property
    def storage_scope(self) -> str:
        return self.manifest.storage_scope


def export_preset_with_lists(backend, file_name: str, dest_path: Path) -> PresetExportResult:
    source_text = backend.read_source_text_by_file_name(file_name)
    lists_root = Path(backend.app_paths.user_root) / "lists"
    list_files = collect_portable_list_files(source_text, engine=backend.engine, lists_root=lists_root)
    if not list_files:
        destination = Path(dest_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(_with_final_newline(source_text), encoding="utf-8")
        return PresetExportResult(path=destination)

    destination = Path(dest_path)
    if destination.suffix.lower() != ".zip":
        destination = destination.with_suffix(".zip")
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "format": ARCHIVE_FORMAT,
        "version": ARCHIVE_VERSION,
        "engine": str(backend.engine or "").strip().lower(),
        "preset_file_name": str(file_name or "").strip(),
        "lists": [
            {
                "file_name": item.file_name,
                "kind": item.kind,
                "storage": item.storage,
                "archive_path": f"lists/{item.file_name}",
                "sha256": _sha256_text(item.text),
            }
            for item in list_files
        ],
    }
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_MANIFEST_PATH, json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr(_PRESET_PATH, _with_final_newline(source_text))
        for item in list_files:
            archive.writestr(f"lists/{item.file_name}", _with_final_newline(item.text))
    return PresetExportResult(
        path=destination,
        archived_list_files=tuple(item.file_name for item in list_files),
    )


def import_portable_preset(backend, src_path: Path, *, name: str) -> PresetImportResult:
    source_text, list_files = _read_portable_archive(Path(src_path), expected_engine=backend.engine)
    referenced_kinds = _referenced_list_kinds(source_text, engine=backend.engine)
    for item in list_files:
        if referenced_kinds.get(item.file_name.casefold()) != item.kind:
            raise ValueError(
                f"ZIP содержит lists/{item.file_name}, но пресет не ссылается на него как на {item.kind}."
            )
    preset_name = str(name or Path(src_path).stem or "Imported").strip() or "Imported"
    lists_root = Path(backend.app_paths.user_root) / "lists"
    replacements, writes = _plan_list_imports(lists_root, list_files)
    rewritten = _rewrite_list_file_references(source_text, replacements)
    validation_error = validate_preset_source_text(rewritten, engine=backend.engine)
    if validation_error:
        raise ValueError(f"Архив содержит неверный пресет: {validation_error}")
    rewritten = _rewrite_preset_headers(
        rewritten,
        preset_name,
        preset_kind="imported",
    )

    backups: list[tuple[str, bool, str]] = []
    try:
        for file_name, text in writes:
            user_path = layered_list_file(lists_root, file_name).user_path
            existed = user_path.is_file()
            previous = user_path.read_text(encoding="utf-8", errors="replace") if existed else ""
            backups.append((file_name, existed, previous))
            write_profile_user_list_text(lists_root, file_name, text)

        rewritten = backend.normalize_source_text(rewritten)
        imported = backend.preset_file_store.create_preset(
            backend.engine,
            preset_name,
            rewritten,
            kind="imported",
        )
    except Exception:
        _restore_user_lists(lists_root, backups)
        raise

    backend._delete_folder_item_meta(imported.file_name)
    backend.notify_presets_changed()
    return PresetImportResult(
        manifest=imported,
        imported_list_files=tuple(item.file_name for item in list_files),
        renamed_list_files=tuple(
            (old_name, new_name)
            for old_name, new_name in replacements.items()
            if old_name.casefold() != new_name.casefold()
        ),
    )


def collect_portable_list_files(source_text: str, *, engine: str, lists_root: Path) -> tuple[PortableListFile, ...]:
    references = _referenced_list_kinds(source_text, engine=engine)
    result: list[PortableListFile] = []
    for key, kind in references.items():
        file_name = _reference_file_name(source_text, key) or key
        paths = layered_list_file(lists_root, file_name)
        if paths.user_path.is_file():
            text = paths.user_path.read_text(encoding="utf-8", errors="replace")
            if text.strip():
                _validate_exported_list_file(file_name, kind, text)
                result.append(
                    PortableListFile(
                        file_name=file_name,
                        kind=kind,
                        text=_with_final_newline(text),
                        storage="overlay" if paths.base_path.is_file() else "standalone",
                    )
                )
                continue
        if paths.base_path.is_file():
            continue
        if paths.final_path.is_file():
            text = paths.final_path.read_text(encoding="utf-8", errors="replace")
            if text.strip():
                _validate_exported_list_file(file_name, kind, text)
                result.append(
                    PortableListFile(
                        file_name=file_name,
                        kind=kind,
                        text=_with_final_newline(text),
                        storage="standalone",
                    )
                )
                continue
        raise ValueError(
            f"Пресет ссылается на lists/{file_name}, но этого файла нет. "
            "Исправьте список перед экспортом."
        )
    return tuple(result)


def _referenced_list_kinds(source_text: str, *, engine: str) -> dict[str, str]:
    preset = parse_preset_text(source_text, engine=engine, source_name="portable_export")
    references: dict[str, str] = {}
    for profile in preset.profiles:
        for segment in profile.segments:
            option = str(segment.name or "").strip().lower()
            kind = _LIST_OPTION_KINDS.get(option)
            if not kind:
                continue
            for file_name in _list_file_names_from_value(segment.value):
                key = file_name.casefold()
                previous = references.get(key)
                if previous and previous != kind:
                    raise ValueError(f"Файл lists/{file_name} одновременно используется как hostlist и ipset.")
                references[key] = kind
    return references


def _validate_exported_list_file(file_name: str, kind: str, text: str) -> None:
    invalid = validate_profile_list_file_text(kind, text)
    if not invalid:
        return
    line, value = invalid[0]
    raise ValueError(f"В lists/{file_name}, строка {line}, неверная запись `{value}`.")


def _read_portable_archive(path: Path, *, expected_engine: str) -> tuple[str, tuple[PortableListFile, ...]]:
    if not zipfile.is_zipfile(path):
        raise ValueError("Файл ZIP повреждён или не является архивом пресета ZapretGUI.")
    with zipfile.ZipFile(path, "r") as archive:
        file_members = [item for item in archive.infolist() if not item.is_dir()]
        members = {item.filename: item for item in file_members}
        if len(members) != len(file_members):
            raise ValueError("В ZIP есть повторяющиеся имена файлов.")
        total_size = sum(item.file_size for item in members.values())
        if total_size > _MAX_ARCHIVE_TOTAL_SIZE or any(
            item.file_size > _MAX_ARCHIVE_MEMBER_SIZE for item in members.values()
        ):
            raise ValueError("Архив пресета слишком большой.")
        try:
            manifest = json.loads(archive.read(_MANIFEST_PATH).decode("utf-8"))
            source_text = archive.read(_PRESET_PATH).decode("utf-8")
        except KeyError as exc:
            raise ValueError("В ZIP нет manifest.json или preset.txt.") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Не удалось прочитать описание ZIP-архива пресета.") from exc

        if not isinstance(manifest, dict):
            raise ValueError("В ZIP повреждён manifest.json.")
        try:
            archive_version = int(manifest.get("version") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("В ZIP неверная версия формата пресета.") from exc
        if manifest.get("format") != ARCHIVE_FORMAT or archive_version != ARCHIVE_VERSION:
            raise ValueError("Этот ZIP имеет неизвестный формат пресета ZapretGUI.")
        archive_engine = str(manifest.get("engine") or "").strip().lower()
        if archive_engine != str(expected_engine or "").strip().lower():
            raise ValueError("Архив создан для другого режима Zapret.")

        raw_lists = manifest.get("lists") or []
        if not isinstance(raw_lists, list):
            raise ValueError("В описании ZIP повреждён список файлов.")
        list_files: list[PortableListFile] = []
        seen: set[str] = set()
        for raw in raw_lists:
            if not isinstance(raw, dict):
                raise ValueError("В описании ZIP повреждён список файлов.")
            file_name = safe_list_file_name(str(raw.get("file_name") or ""))
            kind = str(raw.get("kind") or "").strip().lower()
            storage = str(raw.get("storage") or "").strip().lower()
            archive_path = str(raw.get("archive_path") or "").replace("\\", "/").strip()
            if not file_name or archive_path != f"lists/{file_name}" or kind not in {"hostlist", "ipset"}:
                raise ValueError("В ZIP найдено небезопасное имя файла списка.")
            if storage not in {"overlay", "standalone"} or file_name.casefold() in seen:
                raise ValueError("В ZIP повреждено описание файла списка.")
            seen.add(file_name.casefold())
            try:
                text = archive.read(archive_path).decode("utf-8")
            except (KeyError, UnicodeDecodeError) as exc:
                raise ValueError(f"Не удалось прочитать {archive_path} из ZIP.") from exc
            if _sha256_text(text) != str(raw.get("sha256") or "").strip().lower():
                raise ValueError(f"Контрольная сумма {archive_path} не совпадает.")
            invalid = validate_profile_list_file_text(kind, text)
            if invalid:
                line, value = invalid[0]
                raise ValueError(f"В {archive_path}, строка {line}, неверная запись `{value}`.")
            list_files.append(PortableListFile(file_name, kind, _with_final_newline(text), storage))
    return source_text, tuple(list_files)


def _plan_list_imports(
    lists_root: Path,
    list_files: tuple[PortableListFile, ...],
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    replacements: dict[str, str] = {}
    writes: list[tuple[str, str]] = []
    reserved: set[str] = set()
    for item in list_files:
        target_name = item.file_name
        paths = layered_list_file(lists_root, target_name)
        if item.storage == "overlay" and paths.base_path.is_file():
            existing = paths.user_path.read_text(encoding="utf-8", errors="replace") if paths.user_path.is_file() else ""
            merged = _merge_list_text(existing, item.text)
            if _normalized_text(existing) != _normalized_text(merged):
                writes.append((target_name, merged))
        elif _list_file_exists(paths):
            existing = _visible_list_text(paths)
            if _normalized_text(existing) != _normalized_text(item.text):
                target_name = _unique_imported_list_name(lists_root, item.file_name, reserved)
                writes.append((target_name, item.text))
        else:
            writes.append((target_name, item.text))
        reserved.add(target_name.casefold())
        replacements[item.file_name] = target_name
    return replacements, writes


def _restore_user_lists(lists_root: Path, backups: list[tuple[str, bool, str]]) -> None:
    for file_name, existed, previous in reversed(backups):
        try:
            if existed:
                write_profile_user_list_text(lists_root, file_name, previous)
            else:
                delete_profile_user_list_file(lists_root, file_name)
        except Exception:
            pass


def _rewrite_list_file_references(source_text: str, replacements: dict[str, str]) -> str:
    if not replacements:
        return source_text
    replacement_keys = {key.casefold(): value for key, value in replacements.items()}
    output: list[str] = []
    for raw in str(source_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = _LIST_LINE_RE.match(raw)
        if match is None:
            output.append(raw)
            continue
        values = []
        changed = False
        for value in _split_list_values(match.group("value")):
            file_name = safe_list_file_name(value.lstrip("@"))
            replacement = replacement_keys.get(file_name.casefold()) if file_name else None
            if replacement and replacement.casefold() != file_name.casefold():
                values.append(f"lists/{replacement}")
                changed = True
            else:
                values.append(value)
        if changed:
            output.append(
                f"{match.group('indent')}{match.group('option')}={','.join(values)}{match.group('trailing')}"
            )
        else:
            output.append(raw)
    return "\n".join(output)


def _list_file_names_from_value(value: str) -> tuple[str, ...]:
    result: list[str] = []
    for item in _split_list_values(value):
        file_name = safe_list_file_name(item.lstrip("@"))
        if file_name:
            result.append(file_name)
    return tuple(result)


def _split_list_values(value: str) -> tuple[str, ...]:
    text = str(value or "").strip().strip('"').strip("'")
    return tuple(item.strip().strip('"').strip("'") for item in text.split(",") if item.strip())


def _reference_file_name(source_text: str, wanted_key: str) -> str:
    for raw in str(source_text or "").splitlines():
        match = _LIST_LINE_RE.match(raw)
        if match is None:
            continue
        for value in _split_list_values(match.group("value")):
            file_name = safe_list_file_name(value.lstrip("@"))
            if file_name and file_name.casefold() == wanted_key:
                return file_name
    return ""


def _unique_imported_list_name(lists_root: Path, file_name: str, reserved: set[str]) -> str:
    path = Path(file_name)
    stem = path.stem or "list"
    suffix = path.suffix or ".txt"
    counter = 2
    while True:
        candidate = f"{stem}-imported-{counter}{suffix}"
        if candidate.casefold() not in reserved and not _list_file_exists(layered_list_file(lists_root, candidate)):
            return candidate
        counter += 1


def _list_file_exists(paths) -> bool:
    return paths.base_path.is_file() or paths.user_path.is_file() or paths.final_path.is_file()


def _visible_list_text(paths) -> str:
    if paths.user_path.is_file() and not paths.base_path.is_file():
        return paths.user_path.read_text(encoding="utf-8", errors="replace")
    if paths.final_path.is_file():
        return paths.final_path.read_text(encoding="utf-8", errors="replace")
    if paths.user_path.is_file():
        return paths.user_path.read_text(encoding="utf-8", errors="replace")
    if paths.base_path.is_file():
        return paths.base_path.read_text(encoding="utf-8", errors="replace")
    return ""


def _merge_list_text(current: str, incoming: str) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for raw in (*str(current or "").splitlines(), *str(incoming or "").splitlines()):
        line = raw.strip()
        if not line:
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(_with_final_newline(text).encode("utf-8")).hexdigest()


def _normalized_text(text: str) -> str:
    return _with_final_newline(text).replace("\r\n", "\n").replace("\r", "\n")


def _with_final_newline(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"


__all__ = [
    "PresetExportResult",
    "PresetImportResult",
    "collect_portable_list_files",
    "export_preset_with_lists",
    "import_portable_preset",
]
