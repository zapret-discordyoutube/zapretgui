"""Данные пресетов не теряются и не подменяются тихо.

Автосинк не воскрешает отвязанную привязку, один нечитаемый файл не роняет
список, упавшая проверка не останавливает работающий DPI, действия над
профилями не уходят в другой пресет после переключения.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class RemoteSyncBindingWriteTests(unittest.TestCase):
    def _run(self, *, latest_binding):
        from presets import remote_sync_workers
        from presets.remote_sync import RemoteSyncOutcome

        snapshot = {"url": "https://example.com/p.txt", "auto": True, "detached": False}
        bindings = iter([snapshot, latest_binding])
        written: list[dict] = []
        outcome = RemoteSyncOutcome(status="updated", binding_updates={"checked_at": "now", "synced_hash": "h"})
        with patch("presets.remote_bindings.get_remote_preset_binding", lambda *_a: next(bindings)), patch(
            "presets.remote_bindings.set_remote_preset_binding",
            lambda _scope, _file, binding: written.append(dict(binding)),
        ), patch("presets.remote_sync.sync_remote_preset", return_value=outcome):
            remote_sync_workers.sync_remote_preset_by_file_name(SimpleNamespace(), "zapret2_mode", "P.txt")
        return written

    def test_unlinked_during_sync_binding_is_not_recreated(self) -> None:
        self.assertEqual(self._run(latest_binding=None), [])

    def test_auto_update_switched_off_during_sync_stays_off(self) -> None:
        written = self._run(
            latest_binding={"url": "https://example.com/p.txt", "auto": False, "detached": False},
        )
        self.assertEqual(len(written), 1)
        self.assertFalse(written[0]["auto"])
        self.assertEqual(written[0]["synced_hash"], "h")

    def test_url_changed_during_sync_is_not_overwritten(self) -> None:
        written = self._run(latest_binding={"url": "https://other.example/q.txt", "auto": True})
        self.assertEqual(written, [])


class PresetListScanTests(unittest.TestCase):
    def test_unreadable_file_and_txt_folder_do_not_break_list(self) -> None:
        from core.paths import AppPaths
        from presets import file_store as file_store_module
        from presets.file_store import PresetFileStore

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = PresetFileStore(AppPaths(user_root=root / "user", local_root=root / "local"))
            user_dir = store._engine_paths("winws2").user_presets_dir
            user_dir.mkdir(parents=True, exist_ok=True)
            (user_dir / "Good.txt").write_text("# Preset: Good\n--filter-tcp=443\n", encoding="utf-8")
            (user_dir / "Locked.txt").write_text("# Preset: Locked\n--filter-tcp=80\n", encoding="utf-8")
            (user_dir / "Folder.txt").mkdir()
            real_read = file_store_module._read_header_text

            def _read(path):
                if path.name == "Locked.txt":
                    raise PermissionError(13, "locked by antivirus")
                return real_read(path)

            with patch.object(file_store_module, "_read_header_text", _read):
                names = [manifest.file_name for manifest in store.list_manifests("winws2")]

        self.assertIn("Good.txt", names)
        self.assertIn("Locked.txt", names)
        self.assertNotIn("Folder.txt", names)


class ValidateBeforeStopTests(unittest.TestCase):
    def test_validator_crash_keeps_running_dpi(self) -> None:
        from winws_runtime.runtime.preset_launch_service import PresetLaunchService

        service = PresetLaunchService.__new__(PresetLaunchService)
        service.launch_method = "zapret2_mode"
        service.last_error_message = ""
        service._progress = Mock()

        with tempfile.TemporaryDirectory() as temp_dir:
            preset = Path(temp_dir) / "P.txt"
            preset.write_text("--wf-tcp-out=443\n--filter-tcp=443\n--lua-desync=pass\n", encoding="utf-8")
            with patch(
                "winws_runtime.runners.runner_factory.get_strategy_runner",
                side_effect=RuntimeError("winws2.exe не найден"),
            ):
                allowed = service._validate_preset_before_stop(
                    is_preset_file=True,
                    preset_path=str(preset),
                    skip_stop=False,
                )

        # Раньше любое исключение давало True: работающий DPI останавливался,
        # а новый не стартовал — пользователь оставался вовсе без обхода.
        self.assertFalse(allowed)
        self.assertIn("winws2.exe не найден", service.last_error_message)


class ProfileListQueuePresetBindingTests(unittest.TestCase):
    def _page(self, displayed: str):
        from profile.ui.preset_setup_page import PresetSetupPageBase

        page = PresetSetupPageBase.__new__(PresetSetupPageBase)
        page._displayed_preset_file_name = displayed
        page._cleanup_in_progress = False
        page._profile_context_action_runtime = SimpleNamespace(is_running=lambda: True)
        return page

    def test_queued_action_remembers_preset_and_is_skipped_in_other_preset(self) -> None:
        from profile.ui.preset_write_queue import PresetWriteQueue

        page = self._page("A.txt")
        queue = PresetWriteQueue(page)
        queue._queue_profile_preset_write_operation("context", action="delete", profile_key="uid:1")
        operation = page._profile_preset_write_state_obj().pop_next()
        self.assertEqual(operation["preset_file_name"], "A.txt")

        page._displayed_preset_file_name = "B.txt"
        queue._profile_preset_write_operation_running = lambda: False
        queue._start_profile_context_action_worker = Mock()
        queue._run_profile_preset_write_operation(operation)

        # Удаление профиля пресета A не должно выполниться в пресете B.
        queue._start_profile_context_action_worker.assert_not_called()

    def test_preset_switch_drops_pending_profile_actions_but_keeps_user_profiles(self) -> None:
        from profile.ui.preset_write_queue import PresetWriteQueue

        page = self._page("A.txt")
        queue = PresetWriteQueue(page)
        queue._queue_profile_preset_write_operation("context", action="delete", profile_key="uid:1")
        queue._queue_profile_preset_write_operation("user_profile", action="delete", profile_id="my")

        queue._drop_preset_bound_operations()

        self.assertEqual(
            [op["kind"] for op in page._profile_preset_write_state_obj().pending],
            ["user_profile"],
        )


class DynamicWidgetWorkersHaveNoParentTests(unittest.TestCase):
    def test_list_and_filter_workers_are_not_owned_by_recreated_widgets(self) -> None:
        import inspect

        from profile.ui import profile_strategy_list_widget, profiles_list

        # Виджет удаляется вместе с работающим QThread-ребёнком — Qt роняет
        # программу. Живым воркер держит гейт, затем finished -> deleteLater.
        self.assertNotIn("parent=self", inspect.getsource(profiles_list.ProfilesList._start_view_state_worker))
        filter_factory = profile_strategy_list_widget.ProfileStrategyListWidget.create_strategy_filter_worker
        self.assertNotIn("parent=self", inspect.getsource(filter_factory))


if __name__ == "__main__":
    unittest.main()
