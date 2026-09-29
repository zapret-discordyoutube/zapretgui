"""Отложенные записи страницы profile помнят профиль, открытый при щелчке.

Раньше очередь хранила только «что сделать» (стратегия, переключатель), а
«для кого» бралось из страницы в момент выполнения: если пользователь успевал
открыть другой профиль, стратегия или включение уходили в него.
"""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from profile.ui.profile_setup_page import ProfileSetupPageBase


class _Signal:
    def connect(self, _callback) -> None:
        return None


class _Worker:
    saved = _Signal()
    applied = _Signal()
    failed = _Signal()
    finished = _Signal()

    def __init__(self) -> None:
        self.start = Mock()
        self.run = Mock()
        self.deleteLater = Mock()


class _Runtime:
    def __init__(self, *, running: bool = False) -> None:
        self.running = running

    def is_running(self) -> bool:
        return self.running

    def start_qthread_worker(self, *, worker_factory, **_kwargs):
        worker = worker_factory(0)
        worker.start()
        return 0, worker


def _payload(key: str, strategy_id: str = "none"):
    return SimpleNamespace(item=SimpleNamespace(key=key, persistent_key=key, strategy_id=strategy_id, enabled=True))


def _page_with_running_write(profile_key: str, strategy_id: str = "none"):
    page = ProfileSetupPageBase.__new__(ProfileSetupPageBase)
    page._loading = False
    page._profile_key = profile_key
    page._payload = _payload(profile_key, strategy_id)
    page._raw_profile_save_runtime = _Runtime(running=True)
    page._strategy_apply_runtime = _Runtime(running=False)
    page._enabled_save_runtime = _Runtime(running=False)
    page._strategy_apply_request_id = 0
    page._enabled_save_request_id = 0
    page._strategy_apply_runtime_strategy_id = ""
    page._pending_strategy_apply = None
    page._pending_enabled_save = None
    page._pending_profile_setup_write_operations = []
    page._enabled_checkbox = None
    page.create_profile_strategy_apply_worker = Mock(return_value=_Worker())
    page.create_profile_enabled_save_worker = Mock(return_value=_Worker())
    return page


def _open_other_profile(page, profile_key: str) -> None:
    page._profile_key = profile_key
    page._payload = _payload(profile_key)


class ProfileSetupWriteIdentityTests(unittest.TestCase):
    def test_queued_strategy_is_applied_to_the_profile_it_was_clicked_for(self) -> None:
        page = _page_with_running_write("uid:A")

        ProfileSetupPageBase._request_strategy_apply(page, "tls_fake")
        _open_other_profile(page, "uid:B")
        page._raw_profile_save_runtime.running = False
        operation = page._profile_setup_write_state_obj().pop_next()
        ProfileSetupPageBase._run_profile_setup_write_operation(page, operation)

        page.create_profile_strategy_apply_worker.assert_called_once()
        self.assertEqual(
            page.create_profile_strategy_apply_worker.call_args.kwargs["profile_key"],
            "uid:A",
        )

    def test_queued_enabled_toggle_is_saved_to_the_profile_it_was_clicked_for(self) -> None:
        page = _page_with_running_write("uid:A")
        page._current_filter_kind = lambda: "hostlist"
        page._current_filter_value = lambda: "lists/a.txt"

        ProfileSetupPageBase._on_enabled_changed(page, 0)
        _open_other_profile(page, "uid:B")
        page._current_filter_value = lambda: "lists/b.txt"
        page._raw_profile_save_runtime.running = False
        operation = page._profile_setup_write_state_obj().pop_next()
        ProfileSetupPageBase._run_profile_setup_write_operation(page, operation)

        kwargs = page.create_profile_enabled_save_worker.call_args.kwargs
        self.assertEqual(kwargs["profile_key"], "uid:A")
        self.assertEqual(kwargs["filter_value"], "lists/a.txt")
        self.assertFalse(kwargs["enabled"])

    def test_queued_writes_of_different_profiles_do_not_replace_each_other(self) -> None:
        page = _page_with_running_write("uid:A")

        ProfileSetupPageBase._request_strategy_apply(page, "tls_fake")
        _open_other_profile(page, "uid:B")
        ProfileSetupPageBase._request_strategy_apply(page, "tls_split")

        self.assertEqual(
            [(op["profile_key"], op["strategy_id"]) for op in page._profile_setup_write_state_obj().pending],
            [("uid:A", "tls_fake"), ("uid:B", "tls_split")],
        )

    def test_returning_to_strategy_being_written_cancels_the_pending_one(self) -> None:
        # B пишется, щёлкнули C (ожидает), вернулись к B: итог — B, а не C.
        page = _page_with_running_write("uid:A", strategy_id="strategy_a")
        page._strategy_apply_runtime.running = True
        page._strategy_apply_runtime_strategy_id = "strategy_b"
        page._strategy_apply_runtime_profile_key = "uid:A"
        page._mark_strategy_selection_pending = Mock()

        ProfileSetupPageBase._on_strategy_list_activated(page, "strategy_c")
        self.assertEqual(page._pending_strategy_apply, "strategy_c")

        ProfileSetupPageBase._on_strategy_list_activated(page, "strategy_b")

        self.assertIsNone(page._pending_strategy_apply)
        self.assertEqual(page._profile_setup_write_state_obj().pending, [])

    def test_click_on_payload_strategy_while_other_is_written_is_not_dropped(self) -> None:
        # В payload ещё A, пишется B: щелчок по A раньше молча отбрасывался.
        page = _page_with_running_write("uid:A", strategy_id="strategy_a")
        page._strategy_apply_runtime.running = True
        page._strategy_apply_runtime_strategy_id = "strategy_b"
        page._strategy_apply_runtime_profile_key = "uid:A"
        page._mark_strategy_selection_pending = Mock()

        ProfileSetupPageBase._on_strategy_list_activated(page, "strategy_a")

        self.assertEqual(page._pending_strategy_apply, "strategy_a")
        page._mark_strategy_selection_pending.assert_called_once_with("strategy_a")

    def test_switching_profile_saves_pending_field_edits_under_the_old_key(self) -> None:
        page = ProfileSetupPageBase.__new__(ProfileSetupPageBase)
        page._profile_key = "uid:A"
        page._payload = _payload("uid:A")
        timer = SimpleNamespace(isActive=Mock(return_value=True), stop=Mock())
        page._settings_save_timer = timer
        requests: list[str] = []
        page._flush_list_file_autosave_before_switch = Mock()
        page.reload_current_profile = Mock()

        with patch(
            "profile.ui.profile_setup_save_controllers.ProfileSetupSaveController._autosave_editable_settings",
            lambda controller: requests.append(controller._page._profile_key),
        ):
            ProfileSetupPageBase.show_profile(page, "uid:B")

        timer.stop.assert_called_once_with()
        self.assertEqual(requests, ["uid:A"])
        self.assertEqual(page._profile_key, "uid:B")

    def test_reopening_same_profile_reloads_after_preset_changed_elsewhere(self) -> None:
        page = ProfileSetupPageBase.__new__(ProfileSetupPageBase)
        page._profile_key = "uid:A"
        page._payload = _payload("uid:A", "strategy_a")
        page.reload_current_profile = Mock()

        ProfileSetupPageBase.show_profile(page, "uid:A")
        page.reload_current_profile.assert_not_called()

        # Пресет поправили в редакторе текста / автосинке: при возврате
        # на тот же профиль нужен файл, а не прежний payload.
        ProfileSetupPageBase._on_preset_revision_changed(page, None, frozenset({"preset_content_revision"}))
        ProfileSetupPageBase.show_profile(page, "uid:A")
        page.reload_current_profile.assert_called_once_with()

        ProfileSetupPageBase.show_profile(page, "uid:A")
        page.reload_current_profile.assert_called_once_with()

    def test_strategy_result_is_applied_when_pending_strategy_belongs_to_other_profile(self) -> None:
        page = _page_with_running_write("uid:A", strategy_id="s0")
        page._strategy_apply_request_id = 5
        page._pending_strategy_apply = "Y"
        page._strategy_apply_pending_profile_key = "uid:B"
        page._schedule_profile_setup_payload_apply = Mock()
        page._on_profile_changed_callback = Mock()
        page.reload_current_profile = Mock()
        new_payload = _payload("uid:A", "W")
        applied = SimpleNamespace(status="applied", should_reload=False, blob_warnings=())

        with patch(
            "profile.ui.profile_setup_page._profile_setup_apply_result_from_worker_result",
            return_value=applied,
        ), patch(
            "profile.ui.profile_setup_page._profile_setup_payload_and_apply_signature",
            return_value=(new_payload, None),
        ):
            ProfileSetupPageBase._on_strategy_apply_finished(page, 5, "uid:A", "uid:A", "W", object())

        # Ожидающая стратегия профиля B не делает результат для A устаревшим.
        self.assertIs(page._payload, new_payload)
        page._on_profile_changed_callback.assert_called_once_with("uid:A", "strategy", new_payload.item)

    def test_strategy_written_to_other_profile_notifies_profile_list(self) -> None:
        page = _page_with_running_write("uid:B")
        page._strategy_apply_request_id = 5
        page._on_profile_changed_callback = Mock()
        written_payload = _payload("uid:A", "W")

        with patch(
            "profile.ui.profile_setup_page._profile_setup_payload_and_apply_signature",
            return_value=(written_payload, None),
        ):
            ProfileSetupPageBase._on_strategy_apply_finished(page, 5, "uid:A", "uid:A", "W", object())

        # Страница показывает B и не трогает его, а строка A в списке обновится.
        self.assertEqual(page._profile_key, "uid:B")
        page._on_profile_changed_callback.assert_called_once_with("uid:A", "strategy", written_payload.item)

    def test_settings_result_for_left_profile_does_not_switch_page_back(self) -> None:
        page = _page_with_running_write("uid:B")
        page._settings_save_request_id = 3
        page._settings_save_runtime_profile_key = "uid:A"
        page._pending_settings_save = None
        page._schedule_profile_setup_payload_apply = Mock()
        page._on_profile_changed_callback = Mock()
        written_payload = _payload("uid:A")

        with patch(
            "profile.ui.profile_setup_page._profile_setup_payload_and_apply_signature",
            return_value=(written_payload, None),
        ):
            ProfileSetupPageBase._on_settings_save_finished(page, 3, ("uid:A", "uid:A"), object())

        self.assertEqual(page._profile_key, "uid:B")
        page._schedule_profile_setup_payload_apply.assert_not_called()
        page._on_profile_changed_callback.assert_called_once_with("uid:A", "settings", written_payload.item)

    def test_enabled_toggle_of_other_profile_is_queued_not_merged_into_one_slot(self) -> None:
        page = _page_with_running_write("uid:B")
        page._raw_profile_save_runtime.running = False
        page._enabled_save_runtime.running = True
        page._enabled_save_runtime_enabled = False
        page._enabled_save_runtime_profile_key = "uid:A"
        page._current_filter_kind = lambda: "hostlist"
        page._current_filter_value = lambda: "lists/b.txt"
        page._payload.item.enabled = True

        ProfileSetupPageBase._on_enabled_changed(page, 0)

        self.assertIsNone(page._pending_enabled_save)
        self.assertEqual(
            page._profile_setup_write_state_obj().pending,
            [{"kind": "enabled_save", "enabled": False, "profile_key": "uid:B", "filter_kind": "hostlist", "filter_value": "lists/b.txt"}],
        )

    def test_close_keeps_chronological_order_of_pending_writes(self) -> None:
        page = _page_with_running_write("uid:A")
        page._profile_setup_write_state_obj().pending.extend(
            [
                {"kind": "strategy_apply", "strategy_id": "S", "profile_key": "uid:A"},
                {"kind": "raw_profile_save", "profile_key": "uid:A", "text": "--lua-desync=fake"},
            ]
        )
        page._pending_raw_profile_save = ("uid:A", "--lua-desync=fake")
        page._pending_strategy_apply = "S"
        page._strategy_apply_pending_profile_key = "uid:A"

        operations = ProfileSetupPageBase._save_controller_obj(page)._profile_writes_to_save_on_close()

        # Текст, набранный после щелчка по стратегии, ложится последним.
        self.assertEqual([op["kind"] for op in operations], ["strategy_apply", "raw_profile_save"])

    def test_close_writes_queued_strategy_synchronously(self) -> None:
        page = _page_with_running_write("uid:A")
        ProfileSetupPageBase._request_strategy_apply(page, "tls_fake")

        operations = ProfileSetupPageBase._save_controller_obj(page)._profile_writes_to_save_on_close()
        for operation in operations:
            ProfileSetupPageBase._save_controller_obj(page)._save_profile_write_on_close(operation)

        worker = page.create_profile_strategy_apply_worker.return_value
        self.assertEqual(
            page.create_profile_strategy_apply_worker.call_args.kwargs,
            {"profile_key": "uid:A", "strategy_id": "tls_fake", "parent": None},
        )
        worker.run.assert_called_once_with()
        worker.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
