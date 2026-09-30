from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

PROJECT_SRC = Path(__file__).resolve().parent.parent / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


class ActionResultAppliesWithoutReloadTests(unittest.TestCase):
    """AC1: rename/duplicate/reset применяют результат action-worker-а без перечитывания."""

    def _make_page(self):
        from presets.ui.common.preset_subpage_base import PresetRawEditorPage

        page = PresetRawEditorPage.__new__(PresetRawEditorPage)
        page._raw_action_request_id = 1
        page._pending_raw_preset_write_operations = []
        page._preset_name = "Old"
        page._preset_file_name = "Old.txt"
        page._preset_path = None
        page._preset_origin = "user"
        page._preset_can_reset_to_builtin = True
        page._notify_preset_structure_changed = Mock()
        page._load_file = Mock(side_effect=AssertionError("action result must apply without disk reload"))
        page._refresh_header = Mock()
        page._show_success = Mock()
        page._apply_raw_preset_action_result = Mock(
            wraps=lambda load_result: None if load_result is not None else page._load_file()
        )
        return page

    def test_rename_applies_worker_result_without_load_file(self) -> None:
        from presets.ui.common.preset_subpage_base import PresetRawEditorPage

        page = self._make_page()
        updated = SimpleNamespace(name="New", file_name="New.txt", kind="user")
        load_result = SimpleNamespace(text="--new\n--filter-tcp=443\n")

        PresetRawEditorPage._on_raw_preset_action_finished(
            page,
            1,
            "rename",
            (updated, "C:/presets/New.txt", load_result),
            {"new_name": "New"},
        )

        page._apply_raw_preset_action_result.assert_called_once_with(load_result)
        page._load_file.assert_not_called()
        self.assertEqual(page._preset_file_name, "New.txt")

    def test_reset_applies_worker_result_without_load_file(self) -> None:
        from presets.ui.common.preset_subpage_base import PresetRawEditorPage

        page = self._make_page()
        updated = SimpleNamespace(name="Builtin", file_name="Builtin.txt", kind="builtin")
        load_result = SimpleNamespace(text="--new\nbuiltin\n")

        PresetRawEditorPage._on_raw_preset_action_finished(
            page,
            1,
            "reset",
            (updated, "C:/presets/Builtin.txt", load_result),
            {},
        )

        page._apply_raw_preset_action_result.assert_called_once_with(load_result)
        page._load_file.assert_not_called()

    def test_apply_falls_back_to_load_file_when_worker_result_missing(self) -> None:
        from presets.ui.common.preset_subpage_base import PresetRawEditorPage

        page = PresetRawEditorPage.__new__(PresetRawEditorPage)
        page._load_file = Mock()

        PresetRawEditorPage._apply_raw_preset_action_result(page, None)

        page._load_file.assert_called_once_with()

    def test_action_worker_reads_result_in_worker_thread(self) -> None:
        from presets.raw_preset_loader import RawPresetActionWorker

        run_source = inspect.getsource(RawPresetActionWorker.run)

        self.assertEqual(run_source.count("_load_result_after_action"), 3)


class ActivationOnlyReloadSkipTests(unittest.TestCase):
    """AC2: активация уже отображаемого пресета не перегружает список профилей."""

    def test_activation_of_displayed_preset_skips_reload(self) -> None:
        from profile.ui.preset_setup_page import PresetSetupPageBase

        page = PresetSetupPageBase.__new__(PresetSetupPageBase)
        page._cleanup_in_progress = False
        page._displayed_preset_file_name = "Default V1.txt"
        page._schedule_profiles_payload_reload_after_preset_switch = Mock(
            side_effect=AssertionError("activation of displayed preset must not reload")
        )
        state = SimpleNamespace(active_preset_file_name="default v1.txt")

        PresetSetupPageBase._on_ui_state_changed(page, state, frozenset({"active_preset_revision"}))

        page._schedule_profiles_payload_reload_after_preset_switch.assert_not_called()

    def test_activation_of_other_preset_schedules_reload(self) -> None:
        from profile.ui.preset_setup_page import PresetSetupPageBase

        page = PresetSetupPageBase.__new__(PresetSetupPageBase)
        page._cleanup_in_progress = False
        page._displayed_preset_file_name = "Default V1.txt"
        page._deferred_profile_payload_apply = object()
        page._schedule_profiles_payload_reload_after_preset_switch = Mock()
        state = SimpleNamespace(active_preset_file_name="Other.txt")

        PresetSetupPageBase._on_ui_state_changed(page, state, frozenset({"active_preset_revision"}))

        page._schedule_profiles_payload_reload_after_preset_switch.assert_called_once_with()

    def test_unknown_active_name_falls_back_to_reload(self) -> None:
        from profile.ui.preset_setup_page import PresetSetupPageBase

        page = PresetSetupPageBase.__new__(PresetSetupPageBase)
        page._displayed_preset_file_name = "Default V1.txt"
        state = SimpleNamespace(active_preset_file_name="")

        self.assertFalse(PresetSetupPageBase._activation_targets_displayed_preset(page, state))

    def test_coordinator_publishes_active_preset_file_name(self) -> None:
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator

        store = SimpleNamespace(bump_active_preset_revision=Mock(return_value=True))
        coordinator = PresetRuntimeCoordinator.__new__(PresetRuntimeCoordinator)
        coordinator._ui_state_store = store
        coordinator._last_active_preset_key = ("zapret2_mode", "default v1.txt")
        coordinator._active_preset_projection = ("zapret2_mode", "Default v1.txt")

        PresetRuntimeCoordinator._publish_active_preset_revision_now(coordinator)

        # Имя в исходном регистре и режим: store общий для zapret1 и zapret2.
        self.assertEqual(
            store.bump_active_preset_revision.call_args.kwargs,
            {"file_name": "Default v1.txt", "launch_method": "zapret2_mode"},
        )


class ExternalReloadCoalescingTests(unittest.TestCase):
    """AC3: серия внешних изменений в окне коалесинга даёт одну перезагрузку."""

    def test_rapid_external_bumps_produce_single_reload(self) -> None:
        from presets.ui.common.preset_subpage_base import PresetRawEditorPage

        page = PresetRawEditorPage.__new__(PresetRawEditorPage)
        page._preset_file_name = "A.txt"
        page._raw_preset_content_loaded_once = True
        page._request_raw_preset_text = Mock()
        scheduled = []

        with patch("presets.ui.common.preset_subpage_base.QTimer") as qtimer:
            qtimer.singleShot = Mock(side_effect=lambda _ms, cb: scheduled.append(cb))
            PresetRawEditorPage._handle_external_raw_preset_content_changed(page)
            PresetRawEditorPage._handle_external_raw_preset_content_changed(page)
            PresetRawEditorPage._handle_external_raw_preset_content_changed(page)

        self.assertEqual(len(scheduled), 1)
        scheduled[0]()
        page._request_raw_preset_text.assert_called_once_with(reason="external")

    def test_reload_guards_run_at_fire_time(self) -> None:
        from presets.ui.common.preset_subpage_base import PresetRawEditorPage

        page = PresetRawEditorPage.__new__(PresetRawEditorPage)
        page._preset_file_name = "A.txt"
        page._request_raw_preset_text = Mock()
        editor = SimpleNamespace(
            has_local_unpublished_changes=Mock(return_value=True),
            report_external_update_skipped=Mock(),
            content_loaded_once=True,
        )
        page.__dict__["_raw_text_editor"] = editor

        PresetRawEditorPage._run_external_raw_preset_reload_now(page)

        editor.report_external_update_skipped.assert_called_once_with()
        page._request_raw_preset_text.assert_not_called()


class SingleSnapshotOwnerTests(unittest.TestCase):
    """AC4: после создания редактора legacy-копий состояния текста не существует."""

    def test_sync_removes_legacy_keys(self) -> None:
        from presets.ui.common.preset_subpage_base import PresetRawEditorPage

        page = PresetRawEditorPage.__new__(PresetRawEditorPage)
        page.__dict__["_raw_editor_text_snapshot"] = "text"
        page.__dict__["_raw_preset_content_loaded_once"] = True
        page.__dict__["_raw_preset_content_dirty"] = False
        editor = SimpleNamespace(
            text_snapshot=None,
            content_loaded_once=False,
            content_dirty=True,
            cache_update_suspended=False,
            show_scheduled=False,
            content_publish_pending=False,
        )
        page.__dict__["_raw_text_editor"] = editor

        PresetRawEditorPage._sync_raw_text_editor_state_from_legacy(page)

        for legacy_key, _attr, _default in PresetRawEditorPage._RAW_TEXT_STATE_LEGACY_KEYS:
            self.assertNotIn(legacy_key, page.__dict__, legacy_key)
        self.assertEqual(editor.text_snapshot, "text")
        self.assertTrue(editor.content_loaded_once)
        self.assertFalse(editor.content_dirty)

    def test_setter_writes_only_to_editor_when_present(self) -> None:
        from presets.ui.common.preset_subpage_base import PresetRawEditorPage

        page = PresetRawEditorPage.__new__(PresetRawEditorPage)
        editor = SimpleNamespace(
            text_snapshot=None,
            content_loaded_once=False,
            content_dirty=True,
            cache_update_suspended=False,
            show_scheduled=False,
            content_publish_pending=False,
        )
        page.__dict__["_raw_text_editor"] = editor

        page._raw_editor_text_snapshot = "new text"
        page._raw_preset_content_loaded_once = True

        self.assertEqual(editor.text_snapshot, "new text")
        self.assertTrue(editor.content_loaded_once)
        self.assertNotIn("_raw_editor_text_snapshot", page.__dict__)
        self.assertNotIn("_raw_preset_content_loaded_once", page.__dict__)


class AppEventFilterScopeTests(unittest.TestCase):
    """App-wide фильтр редактора живёт только пока страница видима."""

    def test_init_does_not_install_app_filter(self) -> None:
        from presets.ui.common.preset_subpage_base import PresetRawEditorPage

        init_source = inspect.getsource(PresetRawEditorPage.__init__)

        self.assertNotIn("app.installEventFilter(self)", init_source)

    def test_page_lifecycle_scopes_app_filter(self) -> None:
        from presets.ui.common.preset_subpage_base import PresetRawEditorPage

        activated_source = inspect.getsource(PresetRawEditorPage.on_page_activated)
        hidden_source = inspect.getsource(PresetRawEditorPage.on_page_hidden)

        self.assertIn("_install_app_event_filter", activated_source)
        self.assertIn("_remove_app_event_filter", hidden_source)

    def test_hidden_commits_pending_before_removing_filter(self) -> None:
        from presets.ui.common.preset_subpage_base import PresetRawEditorPage

        hidden_source = inspect.getsource(PresetRawEditorPage.on_page_hidden)

        self.assertLess(
            hidden_source.index("_commit_pending_content_change"),
            hidden_source.index("_remove_app_event_filter"),
        )

    def test_editor_mouse_commit_requires_pending_changes(self) -> None:
        from presets.ui.common.raw_preset_text_editor import RawPresetTextEditor

        handle_source = inspect.getsource(RawPresetTextEditor.handle_event)

        self.assertIn("self.content_publish_pending", handle_source)


if __name__ == "__main__":
    unittest.main()
