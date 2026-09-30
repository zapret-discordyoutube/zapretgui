"""Один источник «активного пресета»: PresetSelectionService.

Каждая смена выбора — включая те, что пользователь не делал (подмена
пропавшего файла, его возврат), — доходит до слушателей одним событием.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class _SelectionFixture:
    def __init__(self, root: Path) -> None:
        from core.paths import AppPaths
        from presets.file_store import PresetFileStore
        from presets.selection_service import PresetSelectionService

        self.store = PresetFileStore(AppPaths(user_root=root, local_root=root))
        self.user_dir = self.store._engine_paths("winws2").user_presets_dir
        self.user_dir.mkdir(parents=True, exist_ok=True)
        self.selection = PresetSelectionService(self.store)
        self.events: list[tuple[str, str, str, str]] = []
        self.selection.add_listener(lambda *event: self.events.append(event))

    def write(self, file_name: str) -> None:
        (self.user_dir / file_name).write_text(f"# Preset: {Path(file_name).stem}\n--wf-tcp-out=443\n", encoding="utf-8")
        self.store._invalidate_manifest_cache("winws2")


class SelectionEventsTests(unittest.TestCase):
    def test_user_selection_fallback_and_restore_are_single_events(self) -> None:
        from presets.selection_service import (
            SELECTION_REASON_FALLBACK,
            SELECTION_REASON_RESTORED,
            SELECTION_REASON_USER,
        )

        with tempfile.TemporaryDirectory() as temp_dir, patch("settings.store.MAIN_DIRECTORY", temp_dir):
            fx = _SelectionFixture(Path(temp_dir))
            fx.write("A.txt")
            fx.write("B.txt")

            fx.selection.select_preset_file_name_fast("winws2", "A.txt")
            fx.selection.select_preset_file_name_fast("winws2", "a.txt")  # тот же выбор — без события
            (fx.user_dir / "A.txt").unlink()
            fx.store._invalidate_manifest_cache("winws2")
            fx.selection.ensure_selected_manifest("winws2", preferred_file_name="B.txt")
            fx.selection.ensure_selected_manifest("winws2", preferred_file_name="B.txt")  # повтор — без события
            fx.write("A.txt")
            fx.selection.ensure_selected_manifest("winws2", preferred_file_name="B.txt")

        self.assertEqual(
            fx.events,
            [
                ("winws2", "A.txt", SELECTION_REASON_USER, ""),
                ("winws2", "B.txt", SELECTION_REASON_FALLBACK, "A.txt"),
                ("winws2", "A.txt", SELECTION_REASON_RESTORED, ""),
            ],
        )

    def test_rename_of_selected_preset_is_not_a_switch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("settings.store.MAIN_DIRECTORY", temp_dir):
            fx = _SelectionFixture(Path(temp_dir))
            fx.write("A.txt")
            fx.selection.select_preset_file_name_fast("winws2", "A.txt")
            fx.events.clear()
            fx.write("Renamed.txt")

            fx.selection.select_preset("winws2", "Renamed.txt", same_preset_renamed=True)

        # Переименование сообщает identity_changed; событие «смена пресета»
        # заставило бы runtime перезапустить тот же пресет.
        self.assertEqual(fx.events, [])

    def test_clicking_fallback_closes_substitution_and_switches_runtime(self) -> None:
        from presets.selection_service import SELECTION_REASON_USER

        with tempfile.TemporaryDirectory() as temp_dir, patch("settings.store.MAIN_DIRECTORY", temp_dir):
            fx = _SelectionFixture(Path(temp_dir))
            fx.write("B.txt")
            from settings import store as settings_store

            settings_store.set_selected_source_preset_file_name("winws2", "gone.txt")
            fx.selection.ensure_selected_manifest("winws2", preferred_file_name="B.txt")
            fx.events.clear()

            fx.selection.select_preset_file_name_fast("winws2", "B.txt")

        # Работающий DPI мог остаться на прежних настройках — щелчок по
        # запасному должен перевести его на запасной.
        self.assertEqual(fx.events, [("winws2", "B.txt", SELECTION_REASON_USER, "")])


class CoordinatorFallbackTests(unittest.TestCase):
    def test_fallback_is_announced_and_published_without_runtime_switch(self) -> None:
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator

        store = SimpleNamespace(set_last_status_message=Mock())
        coordinator = PresetRuntimeCoordinator.__new__(PresetRuntimeCoordinator)
        coordinator._ui_state_store = store
        coordinator._last_active_preset_key = ("zapret2_mode", "gone.txt")
        coordinator._is_current_preset_method = lambda _method: True
        coordinator._publish_active_preset_revision_deferred = Mock()
        coordinator.schedule_refresh_after_preset_switch = Mock()
        coordinator._request_selected_source_preset_apply = Mock()

        PresetRuntimeCoordinator.handle_selection_fallback(coordinator, "zapret2_mode", "gone.txt", "Default v5.txt")

        message = store.set_last_status_message.call_args.args[0]
        self.assertIn("gone", message)
        self.assertIn("Default v5", message)
        self.assertEqual(coordinator._active_preset_projection, ("zapret2_mode", "Default v5.txt"))
        coordinator._publish_active_preset_revision_deferred.assert_called_once_with()
        # Работающий DPI сам по себе не переключаем на запасной, и помним,
        # на чём он реально работает — иначе щелчок по запасному был бы «уже выбран».
        coordinator._request_selected_source_preset_apply.assert_not_called()
        self.assertEqual(coordinator._last_active_preset_key, ("zapret2_mode", "gone.txt"))

    def test_click_on_fallback_after_substitution_applies_it(self) -> None:
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator

        coordinator = PresetRuntimeCoordinator.__new__(PresetRuntimeCoordinator)
        coordinator._ui_state_store = SimpleNamespace(set_last_status_message=Mock())
        coordinator._last_active_preset_key = ("zapret2_mode", "gone.txt")
        coordinator._is_current_preset_method = lambda _method: True
        coordinator._publish_active_preset_revision_deferred = Mock()
        coordinator.schedule_refresh_after_preset_switch = Mock()
        coordinator._schedule_active_preset_file_watcher_setup = Mock()
        coordinator._request_selected_source_preset_apply = Mock()

        PresetRuntimeCoordinator.handle_selection_fallback(coordinator, "zapret2_mode", "gone.txt", "Default v5.txt")
        PresetRuntimeCoordinator.handle_preset_switched(coordinator, "zapret2_mode", "Default v5.txt")

        coordinator._request_selected_source_preset_apply.assert_called_once()

    def test_returned_file_updates_display_without_restart(self) -> None:
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator

        coordinator = PresetRuntimeCoordinator.__new__(PresetRuntimeCoordinator)
        coordinator._ui_state_store = SimpleNamespace(set_last_status_message=Mock())
        coordinator._last_active_preset_key = ("zapret2_mode", "gone.txt")
        coordinator._is_current_preset_method = lambda _method: True
        coordinator._publish_active_preset_revision_deferred = Mock()
        coordinator.schedule_refresh_after_preset_switch = Mock()
        coordinator._request_selected_source_preset_apply = Mock()

        PresetRuntimeCoordinator.handle_selection_fallback(coordinator, "zapret2_mode", "gone.txt", "Default v5.txt")
        coordinator._publish_active_preset_revision_deferred.reset_mock()
        # Файл вернулся: DPI и так работал на нём — только показ.
        PresetRuntimeCoordinator.handle_preset_switched(coordinator, "zapret2_mode", "gone.txt")

        coordinator._request_selected_source_preset_apply.assert_not_called()
        coordinator._publish_active_preset_revision_deferred.assert_called_once_with()
        self.assertEqual(coordinator._active_preset_projection, ("zapret2_mode", "gone.txt"))


class RestoredSelectionTests(unittest.TestCase):
    def test_restored_file_is_applied_even_if_last_switch_had_its_name(self) -> None:
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator

        coordinator = PresetRuntimeCoordinator.__new__(PresetRuntimeCoordinator)
        coordinator._ui_state_store = None
        # Последнее переключение было на X; пока X не было, DPI перезапустился
        # на запасном D — координатор этого не видел.
        coordinator._last_active_preset_key = ("zapret2_mode", "x.txt")
        coordinator._is_current_preset_method = lambda _method: True
        coordinator._publish_active_preset_revision_deferred = Mock()
        coordinator.schedule_refresh_after_preset_switch = Mock()
        coordinator._schedule_active_preset_file_watcher_setup = Mock()
        coordinator._request_selected_source_preset_apply = Mock()

        PresetRuntimeCoordinator.handle_selection_restored(coordinator, "zapret2_mode", "X.txt")

        coordinator._request_selected_source_preset_apply.assert_called_once()

    def test_store_announces_restore_before_switch(self) -> None:
        from presets.selection_service import SELECTION_REASON_RESTORED
        from presets.ui_store import PresetUiStore

        store = PresetUiStore("winws2", SimpleNamespace(), selection_service=SimpleNamespace())
        order: list[str] = []
        store.preset_selection_restored.connect(lambda name: order.append(f"restored:{name}"))
        store.preset_switched.connect(lambda name: order.append(f"switched:{name}"))

        store.notify_selection_changed("X.txt", SELECTION_REASON_RESTORED)

        # Сначала runtime применяет вернувшийся файл, потом обычное
        # «переключено» отсекается как дубль.
        self.assertEqual(order, ["restored:X.txt", "switched:X.txt"])


class RawEditorHeaderTests(unittest.TestCase):
    def test_header_follows_selection_made_elsewhere_in_same_mode_only(self) -> None:
        from presets.ui.common.preset_subpage_base import PresetRawEditorPage

        page = PresetRawEditorPage.__new__(PresetRawEditorPage)
        page._launch_method = "zapret2_mode"
        page._active_preset_file_name = "A.txt"
        page._active_preset_name = "A"
        page._refresh_header = Mock()

        other_mode = SimpleNamespace(active_preset_file_name="Z.txt", active_preset_launch_method="zapret1_mode")
        PresetRawEditorPage._apply_active_preset_projection(page, other_mode)
        self.assertEqual(page._active_preset_file_name, "A.txt")

        same_mode = SimpleNamespace(active_preset_file_name="B.txt", active_preset_launch_method="zapret2_mode")
        PresetRawEditorPage._apply_active_preset_projection(page, same_mode)
        self.assertEqual(page._active_preset_file_name, "B.txt")
        page._refresh_header.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
