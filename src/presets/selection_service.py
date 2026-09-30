from __future__ import annotations

from .models import PresetManifest
from .file_store import PresetFileStore


class PresetSelectionService:
    def __init__(self, preset_file_store: PresetFileStore):
        self._preset_file_store = preset_file_store

    def get_selected_file_name(self, engine: str) -> str | None:
        """Только чтение: сохранённый выбор, приведённый к имени файла на диске.

        Раньше чтение само переписывало настройки (другой регистр имени) —
        молча и из фоновых потоков, без события для интерфейса. Выбор меняют
        только явные действия: select_preset / select_preset_file_name_fast."""
        from settings.store import get_selected_source_preset_file_name

        raw_value = str(get_selected_source_preset_file_name(engine) or "").strip()
        if not raw_value:
            return None

        resolved = str(self._preset_file_store.resolve_file_name(engine, raw_value) or "").strip()
        return resolved or raw_value or None

    def get_selected_manifest(self, engine: str) -> PresetManifest | None:
        file_name = self.get_selected_file_name(engine)
        if not file_name:
            return None
        return self._preset_file_store.get_manifest(engine, file_name)

    def select_preset(self, engine: str, file_name: str) -> PresetManifest:
        from settings.store import set_selected_source_preset_file_name

        preset = self._preset_file_store.get_manifest(engine, file_name)
        if preset is None:
            raise ValueError(f"Preset not found: {file_name}")
        set_selected_source_preset_file_name(engine, preset.file_name)
        return preset

    def select_preset_file_name_fast(self, engine: str, file_name: str) -> str:
        """Preset selection path that does not depend on preset index.json."""
        from settings.store import set_selected_source_preset_file_name

        candidate = str(self._preset_file_store.resolve_file_name(engine, file_name) or "").strip()
        if not candidate:
            raise ValueError("Preset file name is required")

        try:
            preset_path = self._preset_file_store.get_source_path(engine, candidate)
        except Exception:
            preset_path = None
        if preset_path is None or not preset_path.exists():
            raise ValueError(f"Preset not found: {file_name}")

        set_selected_source_preset_file_name(engine, candidate)
        return candidate

    def ensure_can_delete(self, engine: str, file_name: str) -> None:
        selected_file_name = self.get_selected_file_name(engine)
        candidate = str(self._preset_file_store.resolve_file_name(engine, file_name) or file_name or "").strip()
        if selected_file_name and selected_file_name.strip().lower() == candidate.lower():
            raise ValueError("Cannot delete the selected source preset")

    def ensure_selected_manifest(self, engine: str, preferred_file_name: str | None = None) -> PresetManifest | None:
        """Пресет для запуска: выбранный, а если его файла нет — запасной.

        Выбора ещё не было (первый запуск) — запасной становится выбором.
        Выбор есть, но файла нет (удалён в Проводнике, автосинк или редактор
        пересоздаёт его) — запасной берётся только для этого запуска, выбор в
        настройках остаётся: вернётся файл — вернётся и он."""
        from settings.store import get_selected_source_preset_file_name

        current = self.get_selected_manifest(engine)
        if current is not None:
            return current

        fallback = None
        preferred_key = str(preferred_file_name or "").strip()
        if preferred_key:
            fallback = self._preset_file_store.get_manifest(engine, preferred_key)
        if fallback is None:
            manifests = self._preset_file_store.list_manifests(engine)
            if not manifests:
                return None
            fallback = manifests[0]

        missing_file_name = str(get_selected_source_preset_file_name(engine) or "").strip()
        if not missing_file_name:
            return self.select_preset(engine, fallback.file_name)
        self._report_selection_fallback(engine, missing_file_name, fallback)
        return fallback

    def _report_selection_fallback(self, engine: str, missing_file_name: str, fallback: PresetManifest) -> None:
        key = (str(engine or ""), missing_file_name.lower(), str(fallback.file_name or "").lower())
        if key == getattr(self, "_last_reported_fallback", None):
            return
        self._last_reported_fallback = key
        try:
            from log.log import log

            log(
                f"Выбранный пресет {missing_file_name} не найден — для запуска используется "
                f"{fallback.name or fallback.file_name}. Выбор не изменён: вернётся файл — вернётся и он",
                "WARNING",
            )
        except Exception:
            pass
