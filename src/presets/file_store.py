from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import time

from core.paths import AppPaths
from utils.atomic_text import atomic_write_text, decode_preset_bytes, read_preset_file_text

from .models import PresetManifest


_PRESET_HEADER_RE = re.compile(r"^\s*#\s*Preset:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_PRESET_KIND_RE = re.compile(r"^\s*#\s*PresetKind:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sanitize_file_stem(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "Preset"
    sanitized = re.sub(r'[\\/:*?"<>|\x00]+', "_", text)
    sanitized = re.sub(r"\s+", " ", sanitized).strip().rstrip(".")
    return sanitized[:100] or "Preset"


# Метка BOM — часть кодировки файла, а не текста пресета; старые файлы в
# cp1251 читаются как cp1251 (см. utils.atomic_text.decode_preset_bytes).
def _read_text(path: Path) -> str:
    return read_preset_file_text(path)


def _read_header_text(path: Path) -> str:
    lines: list[bytes] = []
    with path.open("rb") as handle:
        for raw in handle:
            stripped = raw.strip()
            if stripped and not stripped.removeprefix(b"\xef\xbb\xbf").startswith(b"#"):
                break
            lines.append(raw.rstrip(b"\r\n"))
    return decode_preset_bytes(b"\n".join(lines))


_DEGRADED_SCAN_TTL_SEC = 2.0
_LOGGED_UNREADABLE_PRESETS: set[str] = set()


def _log_unreadable_preset(path: Path, exc: Exception) -> None:
    # Один раз на файл: постоянно недоступный файл иначе писал бы в лог при
    # каждом пересканировании.
    key = str(path).lower()
    if key in _LOGGED_UNREADABLE_PRESETS:
        return
    _LOGGED_UNREADABLE_PRESETS.add(key)
    try:
        from log.log import log

        log(f"Не удалось прочитать шапку пресета {path.name}: {exc}", "WARNING")
    except Exception:
        pass


def _normalize_preset_file_name_candidate(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    # Пресеты — только *.txt: «v1.5» — это имя «v1.5.txt», а не файл с
    # расширением «.5» (иначе поиск по основе имени находил чужой v1.txt).
    return text if text.lower().endswith(".txt") else f"{text}.txt"


class PresetFileStore:
    def __init__(self, paths: AppPaths):
        self._paths = paths
        self._manifest_cache: dict[str, tuple[tuple[object, ...], list[PresetManifest]]] = {}

    def list_manifests(self, engine: str) -> list[PresetManifest]:
        manifests = self._load_manifests(engine)
        return sorted(manifests, key=lambda item: (item.name.lower(), item.file_name.lower()))

    def get_manifest(self, engine: str, file_name: str) -> PresetManifest | None:
        resolved_file_name = self.resolve_file_name(engine, file_name).lower()
        if not resolved_file_name:
            return None
        for manifest in self._load_manifests(engine):
            if manifest.file_name.strip().lower() == resolved_file_name:
                return manifest
        return None

    def resolve_file_name(self, engine: str, file_name: str) -> str:
        candidate = str(file_name or "").strip()
        if not candidate:
            return ""

        normalized_candidate = _normalize_preset_file_name_candidate(candidate)
        lowered_names = {candidate.lower(), normalized_candidate.lower()}

        # Сначала — имя так, как оно записано на диске (из сканирования папки):
        # на Windows exists() верен при любом регистре, и в настройки/мету
        # уходило написание вызывающего, а не файла.
        for manifest in self._load_manifests(engine):
            manifest_name = str(manifest.file_name or "").strip()
            if manifest_name and manifest_name.lower() in lowered_names:
                return manifest_name

        engine_paths = self._engine_paths(engine)
        for presets_dir in (engine_paths.user_presets_dir, engine_paths.builtin_presets_dir):
            candidate_path = presets_dir / normalized_candidate
            if candidate_path.exists():
                return candidate_path.name

        return normalized_candidate or candidate

    def unique_preset_name(self, engine: str, name: str, *, exclude_file_name: str | None = None) -> str:
        """Имя, свободное и среди отображаемых имён, и среди имён файлов.

        Раньше файл получал «X (2).txt», а шапка оставалась «# Preset: X» —
        в списке стояли два одинаковых «X». Теперь «X (2)» и там и там."""
        base = str(name or "").strip()
        if not base:
            return base
        engine_paths = self._engine_paths(engine)
        presets_dirs = (engine_paths.user_presets_dir, engine_paths.builtin_presets_dir)
        excluded = str(exclude_file_name or "").strip().lower()
        taken_names = {
            str(manifest.name or "").strip().casefold()
            for manifest in self._load_manifests(engine)
            if str(manifest.file_name or "").strip().lower() != excluded
        }

        def _file_taken(candidate_name: str) -> bool:
            file_name = f"{_sanitize_file_stem(candidate_name)}.txt"
            if file_name.lower() == excluded:
                return False
            return any((presets_dir / file_name).exists() for presets_dir in presets_dirs)

        candidate = base
        counter = 2
        while candidate.casefold() in taken_names or _file_taken(candidate):
            suffix = f" ({counter})"
            # Имя файла обрезается до 100 символов (_sanitize_file_stem): без
            # укорачивания основы длинное «имя (2)» давало тот же файл, что и
            # «имя», и цикл никогда не заканчивался.
            candidate = f"{base[: max(1, 100 - len(suffix))].rstrip()}{suffix}"
            counter += 1
            if counter > 10_000:
                raise ValueError(f"Не удалось подобрать свободное имя для пресета: {base}")
        return candidate

    def read_source_text(self, engine: str, file_name: str) -> str:
        manifest = self.get_manifest(engine, file_name)
        if manifest is None:
            raise ValueError(f"Preset not found: {file_name}")
        return _read_text(self._manifest_path(engine, manifest))

    def get_source_path(self, engine: str, file_name: str) -> Path:
        manifest = self.get_manifest(engine, file_name)
        if manifest is None:
            raise ValueError(f"Preset not found: {file_name}")
        return self._manifest_path(engine, manifest)

    def create_preset(
        self,
        engine: str,
        name: str,
        source_text: str,
        *,
        kind: str = "user",
    ) -> PresetManifest:
        engine_paths = self._engine_paths(engine)
        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise ValueError("Preset name is required")

        file_name = self._unique_file_name(
            (engine_paths.user_presets_dir, engine_paths.builtin_presets_dir),
            normalized_name,
        )
        display_name = self._extract_name(source_text, Path(file_name).stem)
        updated_at = _now_iso()
        normalized_kind = self._infer_kind(
            engine,
            current_kind=kind,
            storage_scope="user",
        )

        manifest = PresetManifest(
            file_name=file_name,
            name=display_name,
            updated_at=updated_at,
            kind=normalized_kind,
            storage_scope="user",
        )
        self._write_source(engine_paths.user_presets_dir / file_name, source_text)
        self._invalidate_manifest_cache(engine)
        return manifest

    def update_preset(
        self,
        engine: str,
        file_name: str,
        source_text: str,
        name: str | None,
    ) -> PresetManifest:
        _ = name
        manifests = self._load_manifests(engine)
        idx = self._find_index(manifests, file_name)
        current = manifests[idx]
        storage_scope = "user"
        destination_path = self._engine_paths(engine).user_presets_dir / current.file_name
        normalized_source_text = self._normalize_source_for_write(source_text)
        try:
            current_text = _read_text(self._manifest_path(engine, current))
        except Exception:
            current_text = ""
        if normalized_source_text == self._normalize_source_for_write(current_text):
            return current

        updated = PresetManifest(
            file_name=current.file_name,
            name=self._extract_name(source_text, Path(current.file_name).stem),
            updated_at=_now_iso(),
            kind=self._infer_kind(
                engine,
                current_kind=current.kind,
                storage_scope=storage_scope,
            ),
            storage_scope=storage_scope,
        )
        self._write_source(destination_path, source_text)
        self._invalidate_manifest_cache(engine)
        return updated

    def rename_preset(self, engine: str, file_name: str, new_name: str) -> PresetManifest:
        manifests = self._load_manifests(engine)
        idx = self._find_index(manifests, file_name)
        current = manifests[idx]
        if str(current.storage_scope or "").strip().lower() != "user":
            raise ValueError(f"Built-in preset cannot be renamed: {current.name}")
        normalized_name = str(new_name or "").strip()
        if not normalized_name:
            raise ValueError("Preset name is required")

        engine_paths = self._engine_paths(engine)
        src_path = self._manifest_path(engine, current)
        destination_file_name = self._unique_file_name(
            (engine_paths.user_presets_dir, engine_paths.builtin_presets_dir),
            normalized_name,
            exclude_file_name=current.file_name,
        )
        destination_path = engine_paths.user_presets_dir / destination_file_name
        if src_path.exists() and src_path != destination_path:
            # Оба пути — своя операция: исчезновение старого файла не должно
            # выглядеть для watcher-а активного пресета как внешняя правка.
            try:
                from .own_write_registry import mark_own_preset_write

                mark_own_preset_write(str(src_path))
                mark_own_preset_write(str(destination_path))
            except Exception:
                pass
            src_path.rename(destination_path)

        source_text = _read_text(destination_path) if destination_path.exists() else ""
        updated = PresetManifest(
            file_name=destination_file_name,
            name=self._extract_name(source_text, Path(destination_file_name).stem),
            updated_at=_now_iso(),
            kind=self._infer_kind(
                engine,
                current_kind=current.kind,
                storage_scope="user",
            ),
            storage_scope="user",
        )
        self._invalidate_manifest_cache(engine)
        return updated

    def delete_preset(self, engine: str, file_name: str) -> None:
        manifests = self._load_manifests(engine)
        idx = self._find_index(manifests, file_name)
        manifest = manifests[idx]
        if str(manifest.storage_scope or "").strip().lower() != "user":
            raise ValueError(f"Built-in preset cannot be deleted: {manifest.name}")
        preset_path = self._manifest_path(engine, manifest)
        try:
            preset_path.unlink()
        except FileNotFoundError:
            pass
        self._invalidate_manifest_cache(engine)

    def _engine_paths(self, engine: str):
        return self._paths.engine_paths(engine).ensure_directories()

    def _load_manifests(self, engine: str) -> list[PresetManifest]:
        normalized_engine = str(engine or "").strip().lower()
        cache_key = self._current_manifest_cache_key(normalized_engine)
        cached_entry = self._manifest_cache.get(normalized_engine)
        if cache_key is not None and cached_entry is not None:
            if cached_entry[0] == cache_key or cached_entry[0] == (
                *cache_key,
                ("degraded", int(time.monotonic() // _DEGRADED_SCAN_TTL_SEC)),
            ):
                return list(cached_entry[1])

        self._scan_degraded = False
        manifests = self._scan_manifests_from_files(engine)
        if self.__dict__.get("_scan_degraded"):
            # Какой-то файл был заблокирован: его запись неполная (имя из
            # имени файла). Кэшируем ненадолго — чтобы горячие пути не
            # перечитывали папку на каждый вызов, но разблокированный файл
            # скоро получил настоящее имя.
            if cache_key is not None:
                self._manifest_cache[normalized_engine] = (
                    (*cache_key, ("degraded", int(time.monotonic() // _DEGRADED_SCAN_TTL_SEC))),
                    list(manifests),
                )
            return manifests
        # Ключ — снятый ДО сканирования: файл, созданный во время сканирования,
        # сменит ключ, и следующий вызов пересканирует. Ключ, снятый после,
        # закрепил бы в кэше список без этого файла.
        if cache_key is not None:
            self._manifest_cache[normalized_engine] = (cache_key, list(manifests))
        return manifests

    def _scan_manifests_from_files(self, engine: str) -> list[PresetManifest]:
        engine_paths = self._engine_paths(engine)
        manifests_by_file_name: dict[str, PresetManifest] = {}
        for storage_scope, presets_dir in (
            ("builtin", engine_paths.builtin_presets_dir),
            ("user", engine_paths.user_presets_dir),
        ):
            for preset_path in sorted(presets_dir.glob("*.txt"), key=lambda p: p.name.lower()):
                if not preset_path.is_file():
                    continue
                try:
                    header_text = _read_header_text(preset_path)
                except OSError as exc:
                    # Файл заблокирован антивирусом/редактором: один такой
                    # файл не должен ронять весь список пресетов — показываем
                    # его по имени файла.
                    _log_unreadable_preset(preset_path, exc)
                    self._scan_degraded = True
                    header_text = ""
                display_name = self._extract_name(header_text, preset_path.stem)
                updated_at = self._file_time_to_iso(preset_path) or _now_iso()
                preset_kind = self._extract_preset_kind(header_text)
                kind = self._infer_kind(
                    engine,
                    current_kind=preset_kind,
                    storage_scope=storage_scope,
                )
                manifests_by_file_name[preset_path.name.lower()] = PresetManifest(
                    file_name=preset_path.name,
                    name=display_name,
                    updated_at=updated_at,
                    kind=kind,
                    storage_scope=storage_scope,
                )
        return list(manifests_by_file_name.values())

    @staticmethod
    def _extract_name(source_text: str, default_name: str) -> str:
        match = _PRESET_HEADER_RE.search(source_text or "")
        if match:
            value = match.group(1).strip()
            if value:
                return value
        return str(default_name or "Preset").strip() or "Preset"

    @staticmethod
    def _extract_preset_kind(source_text: str) -> str | None:
        match = _PRESET_KIND_RE.search(source_text or "")
        if not match:
            return None
        value = str(match.group(1) or "").strip().lower()
        return value or None

    @classmethod
    def _infer_kind(
        cls,
        engine: str,
        *,
        current_kind: str | None = None,
        storage_scope: str,
    ) -> str:
        _ = engine
        normalized_storage_scope = str(storage_scope or "").strip().lower()
        if normalized_storage_scope == "builtin":
            return "builtin"
        normalized_current_kind = str(current_kind or "").strip().lower()
        if normalized_current_kind == "imported":
            return "imported"
        return "user"

    def _current_manifest_cache_key(self, engine: str) -> tuple[object, ...] | None:
        try:
            engine_paths = self._engine_paths(engine)
        except Exception:
            return None
        return (
            *self._path_signature(engine_paths.user_presets_dir),
            *self._path_signature(engine_paths.builtin_presets_dir),
        )

    def _invalidate_manifest_cache(self, engine: str) -> None:
        self._manifest_cache.pop(str(engine or "").strip().lower(), None)

    @staticmethod
    def _path_signature(path: Path) -> tuple[object, ...]:
        try:
            stat = path.stat()
            return (
                True,
                int(getattr(stat, "st_mtime_ns", 0) or 0),
                int(getattr(stat, "st_size", 0) or 0),
            )
        except Exception:
            return (False, 0, 0)

    def _find_index(self, manifests: list[PresetManifest], file_name: str) -> int:
        candidate = str(file_name or "").strip().lower()
        for idx, manifest in enumerate(manifests):
            if manifest.file_name.strip().lower() == candidate:
                return idx
        raise ValueError(f"Preset not found: {file_name}")

    @staticmethod
    def _file_time_to_iso(path: Path) -> str:
        try:
            value = float(path.stat().st_mtime)
        except Exception:
            value = 0.0
        if value <= 0:
            return ""
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _normalize_source_for_write(source_text: str) -> str:
        text = (source_text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not text.endswith("\n"):
            text += "\n"
        return text

    @staticmethod
    def _write_source(path: Path, source_text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = PresetFileStore._normalize_source_for_write(source_text)
        # Пометка «своя запись» — вспомогательный механизм подавления watcher-а;
        # её отказ (например, модуль отсутствует в неполной сборке) не должен
        # ломать саму запись пресета.
        try:
            from .own_write_registry import mark_own_preset_write

            mark_own_preset_write(str(path))
        except Exception:
            pass
        atomic_write_text(path, text)

    def _manifest_path(self, engine: str, manifest: PresetManifest) -> Path:
        engine_paths = self._engine_paths(engine)
        if str(manifest.storage_scope or "").strip().lower() == "builtin":
            return engine_paths.builtin_presets_dir / manifest.file_name
        return engine_paths.user_presets_dir / manifest.file_name

    @staticmethod
    def _unique_file_name(
        presets_dirs: tuple[Path, ...],
        name: str,
        *,
        exclude_file_name: str | None = None,
    ) -> str:
        base = _sanitize_file_stem(name)
        candidate = f"{base}.txt"
        counter = 2
        excluded = (exclude_file_name or "").strip().lower()
        while any((presets_dir / candidate).exists() for presets_dir in presets_dirs) and candidate.lower() != excluded:
            candidate = f"{base} ({counter}).txt"
            counter += 1
        return candidate
