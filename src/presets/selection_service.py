from __future__ import annotations

import threading
from typing import Callable

from settings.mode import DEFAULT_PRESET_FILE_NAME_BY_ENGINE

from .models import PresetManifest
from .file_store import PresetFileStore

# Причины события смены выбора (слушатель получает одну из них).
SELECTION_REASON_USER = "user"
SELECTION_REASON_FALLBACK = "fallback"
SELECTION_REASON_RESTORED = "restored"

# listener(engine, file_name, reason, detail): detail — пропавший файл для
# SELECTION_REASON_FALLBACK, иначе "".
SelectionListener = Callable[[str, str, str, str], None]


class PresetSelectionService:
    """Выбранный source preset: единственный писатель и единственный источник
    событий о его смене.

    Все, кто держит копию «активного пресета» (хранилища сигналов, runtime,
    страницы), узнают о смене отсюда — включая смены, которых пользователь не
    делал: подмену пропавшего файла запасным и возврат файла."""

    def __init__(self, preset_file_store: PresetFileStore):
        self._preset_file_store = preset_file_store
        self._listeners: list[SelectionListener] = []
        self._state_lock = threading.Lock()
        # engine -> (пропавший файл, запасной файл), пока работает подмена.
        self._active_fallbacks: dict[str, tuple[str, str]] = {}

    def add_listener(self, listener: SelectionListener) -> None:
        if callable(listener):
            self._listeners.append(listener)

    def _notify(self, engine: str, file_name: str, reason: str, detail: str = "") -> None:
        for listener in tuple(self._listeners):
            try:
                listener(str(engine or ""), str(file_name or ""), reason, str(detail or ""))
            except Exception as exc:
                try:
                    from log.log import log

                    log(f"PresetSelectionService: слушатель смены выбора упал: {exc}", "WARNING")
                except Exception:
                    pass

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

    def select_preset(self, engine: str, file_name: str, *, same_preset_renamed: bool = False) -> PresetManifest:
        """same_preset_renamed=True — выбранный пресет лишь переименован: это
        не смена пресета (о ней сообщает событие identity_changed), и runtime
        не должен ничего перезапускать."""
        from settings.store import set_selected_source_preset_file_name

        preset = self._preset_file_store.get_manifest(engine, file_name)
        if preset is None:
            raise ValueError(f"Preset not found: {file_name}")
        previous = self._stored_selection(engine)
        set_selected_source_preset_file_name(engine, preset.file_name)
        if not same_preset_renamed:
            self._after_explicit_selection(engine, previous, preset.file_name)
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

        previous = self._stored_selection(engine)
        set_selected_source_preset_file_name(engine, candidate)
        self._after_explicit_selection(engine, previous, candidate)
        return candidate

    def ensure_can_delete(self, engine: str, file_name: str) -> None:
        candidate = str(self._preset_file_store.resolve_file_name(engine, file_name) or file_name or "").strip()
        # Нельзя удалить ни сохранённый выбор, ни запасной пресет, который
        # сейчас работает вместо пропавшего файла (его интерфейс и показывает
        # активным).
        protected = {str(self.get_selected_file_name(engine) or "").strip().lower()}
        if self.get_selected_manifest(engine) is None:
            fallback = self._fallback_manifest(engine, DEFAULT_PRESET_FILE_NAME_BY_ENGINE.get(engine, ""))
            if fallback is not None:
                protected.add(str(fallback.file_name or "").strip().lower())
        protected.discard("")
        if candidate.lower() in protected:
            raise ValueError("Cannot delete the selected source preset")

    def ensure_selected_manifest(self, engine: str, preferred_file_name: str | None = None) -> PresetManifest | None:
        """Пресет для запуска: выбранный, а если его файла нет — запасной.

        Выбора ещё не было (первый запуск) — запасной становится выбором.
        Выбор есть, но файла нет (удалён в Проводнике, автосинк или редактор
        пересоздаёт его) — запасной берётся только для этого запуска, выбор в
        настройках остаётся: вернётся файл — вернётся и он. Об обоих случаях
        слушатели узнают событием (SELECTION_REASON_FALLBACK / _RESTORED)."""
        current = self.get_selected_manifest(engine)
        if current is not None:
            self._report_restored_if_needed(engine, current)
            return current

        fallback = self._fallback_manifest(engine, preferred_file_name)
        if fallback is None:
            return None

        missing_file_name = self._stored_selection(engine)
        if not missing_file_name:
            return self.select_preset(engine, fallback.file_name)
        self._report_selection_fallback(engine, missing_file_name, fallback)
        return fallback

    def _stored_selection(self, engine: str) -> str:
        from settings.store import get_selected_source_preset_file_name

        return str(get_selected_source_preset_file_name(engine) or "").strip()

    def _after_explicit_selection(self, engine: str, previous: str, selected: str) -> None:
        key = str(engine or "")
        with self._state_lock:
            fallback = self._active_fallbacks.pop(key, None)
        changed = previous.casefold() != str(selected or "").casefold()
        # Явный выбор закрывает подмену. Даже если выбран сам запасной пресет,
        # событие нужно: работающий DPI мог остаться на прежних настройках, и
        # щелчок по запасному должен его на него перевести.
        if changed or fallback is not None:
            self._notify(key, selected, SELECTION_REASON_USER)

    def _fallback_manifest(self, engine: str, preferred_file_name: str | None) -> PresetManifest | None:
        """Запасной пресет без побочных эффектов: предпочитаемый, иначе первый."""
        preferred_key = str(preferred_file_name or "").strip()
        if preferred_key:
            preferred = self._preset_file_store.get_manifest(engine, preferred_key)
            if preferred is not None:
                return preferred
        manifests = self._preset_file_store.list_manifests(engine)
        return manifests[0] if manifests else None

    def _report_selection_fallback(self, engine: str, missing_file_name: str, fallback: PresetManifest) -> None:
        key = str(engine or "")
        entry = (missing_file_name, str(fallback.file_name or ""))
        with self._state_lock:
            if self._active_fallbacks.get(key) == entry:
                return
            self._active_fallbacks[key] = entry
        try:
            from log.log import log

            log(
                f"Выбранный пресет {missing_file_name} не найден — для запуска используется "
                f"{fallback.name or fallback.file_name}. Выбор не изменён: вернётся файл — вернётся и он",
                "WARNING",
            )
        except Exception:
            pass
        self._notify(key, entry[1], SELECTION_REASON_FALLBACK, missing_file_name)

    def _report_restored_if_needed(self, engine: str, current: PresetManifest) -> None:
        key = str(engine or "")
        with self._state_lock:
            fallback = self._active_fallbacks.pop(key, None)
        if fallback is None:
            return
        # Файл вернулся: действующий пресет снова выбранный — runtime и
        # страницы должны переключиться обратно.
        self._notify(key, str(current.file_name or ""), SELECTION_REASON_RESTORED)
