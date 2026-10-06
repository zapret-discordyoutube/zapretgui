from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PresetServicesBundle:
    app_paths: object
    preset_mode_coordinator: object
    preset_file_store: object
    preset_selection_service: object
    preset_store_winws2: object
    preset_store_winws1: object


def create_preset_services(app_paths) -> PresetServicesBundle:
    from presets.file_store import PresetFileStore
    from presets.mode_coordinator import PresetModeCoordinator
    from presets.selection_service import PresetSelectionService
    from presets.ui_store import PresetUiStore
    from settings.mode import ENGINE_WINWS1, ENGINE_WINWS2

    preset_file_store = PresetFileStore(app_paths)
    preset_selection_service = PresetSelectionService(preset_file_store)
    stores = {
        ENGINE_WINWS2: PresetUiStore(ENGINE_WINWS2, preset_file_store, preset_selection_service),
        ENGINE_WINWS1: PresetUiStore(ENGINE_WINWS1, preset_file_store, preset_selection_service),
    }

    # Сервисы собираются по первому требованию, и первым может прийти фоновый
    # поток. Шина сигналов при этом обязана жить в потоке окна.
    from app.ui_thread_marshaller import ensure_window_thread_affinity

    for engine_key, store in stores.items():
        ensure_window_thread_affinity(store, f"Шина событий пресетов {engine_key}")

    def _on_selection_changed(engine: str, file_name: str, reason: str, detail: str) -> None:
        store = stores.get(str(engine or "").strip().lower())
        if store is not None:
            store.notify_selection_changed(file_name, reason, detail)

    # Любая смена выбора (в т.ч. подмена пропавшего файла и его возврат)
    # доходит до сигналов — копии «активного пресета» не отстают от настроек.
    preset_selection_service.add_listener(_on_selection_changed)
    return PresetServicesBundle(
        app_paths=app_paths,
        preset_mode_coordinator=PresetModeCoordinator(app_paths, preset_selection_service, preset_file_store),
        preset_file_store=preset_file_store,
        preset_selection_service=preset_selection_service,
        preset_store_winws2=stores[ENGINE_WINWS2],
        preset_store_winws1=stores[ENGINE_WINWS1],
    )


__all__ = ["PresetServicesBundle", "create_preset_services"]
