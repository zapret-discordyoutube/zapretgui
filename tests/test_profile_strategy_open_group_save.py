from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from profile.state import ProfileListItem, ProfileSetupPayload
from profile.ui.profile_setup_page import Zapret2ProfileSetupPage


def _worker_stub(*_args, **_kwargs):
    return None


class _Runtime:
    """Вместо настоящего потока: запоминает работника и сам ничего не запускает."""

    def __init__(self) -> None:
        self.running = False
        self.on_finished = None

    def is_running(self) -> bool:
        return self.running

    def start_qthread_worker(self, *, worker_factory, on_finished=None, **_kwargs):
        worker = worker_factory(1)
        self.running = True
        self.on_finished = on_finished
        return 1, worker

    def finish(self) -> None:
        self.running = False
        self.on_finished(None)


def _entries() -> dict:
    """Три способа обхода по 12 стратегий: длинный список, раскрыта одна группа."""
    entries = {}
    for n in range(12):
        for key, desync in (("fake", "fake"), ("split", "multisplit"), ("host", "hostfakesplit")):
            entries[f"{key}-{n:02d}"] = SimpleNamespace(name=f"{key} {n:02d}", args=f"--lua-desync={desync}")
    return entries


class ProfileStrategyOpenGroupSaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        self.app.closeAllWindows()
        self.app.processEvents()

    def _page(self, create_worker=None) -> Zapret2ProfileSetupPage:
        page = Zapret2ProfileSetupPage(
            create_profile_setup_load_worker=_worker_stub,
            create_profile_list_file_load_worker=_worker_stub,
            create_profile_list_file_save_worker=_worker_stub,
            create_profile_list_file_validation_worker=_worker_stub,
            create_profile_settings_save_worker=_worker_stub,
            create_profile_raw_text_save_worker=_worker_stub,
            create_profile_enabled_save_worker=_worker_stub,
            create_profile_user_update_worker=_worker_stub,
            create_profile_user_delete_worker=_worker_stub,
            create_profile_strategy_apply_worker=_worker_stub,
            create_profile_strategy_feedback_save_worker=_worker_stub,
            create_profile_strategy_open_group_save_worker=create_worker,
            open_profiles=lambda: None,
            open_root=lambda: None,
            on_profile_changed=lambda *_args, **_kwargs: None,
        )
        self.addCleanup(page.deleteLater)
        page._strategy_open_group_save_runtime = _Runtime()
        page._strategy_list._strategy_filter_runtime = None
        return page

    def _open_groups(self, page) -> list[str]:
        strategy_list = page._strategy_list
        return [
            key
            for key, header in strategy_list._group_header_items.items()
            if header.data(strategy_list._ROLE_GROUP_EXPANDED)
        ]

    def _payload(self, *, persistent_key: str, open_group: str | None) -> ProfileSetupPayload:
        item = ProfileListItem(
            key="profile:0",
            persistent_key=persistent_key,
            profile_index=0,
            display_name="YouTube",
            enabled=True,
            in_preset=True,
            strategy_id="host-05",
            strategy_name="host 05",
            match_lines=("--filter-tcp=443", "--hostlist=lists/youtube.txt"),
            list_type="hostlist",
            rating="",
            favorite=False,
            group="youtube",
            group_name="YouTube",
            order=0,
        )
        return ProfileSetupPayload(
            item=item,
            strategy_entries=_entries(),
            strategy_states={},
            raw_profile_text="--filter-tcp=443",
            raw_strategy_text="--lua-desync=hostfakesplit",
            match_summary="TCP 443",
            strategy_open_group=open_group,
        )

    def test_remembered_group_from_settings_is_opened_for_the_profile(self) -> None:
        page = self._page()
        page._profile_key = "profile:0"

        page._apply_payload(self._payload(persistent_key="uid:youtube", open_group="split"))

        self.assertEqual(self._open_groups(page), ["split"])
        self.assertEqual(page._strategy_list._open_group_token, "uid:youtube")

    def test_without_saved_group_the_group_of_selected_strategy_is_open(self) -> None:
        page = self._page()
        page._profile_key = "profile:0"

        page._apply_payload(self._payload(persistent_key="uid:youtube", open_group=None))

        self.assertEqual(self._open_groups(page), ["host"])

    def test_opening_a_group_saves_it_in_background_for_the_current_profile(self) -> None:
        worker = Mock()
        create_worker = Mock(return_value=worker)
        page = self._page(create_worker)
        page._profile_key = "profile:0"
        page._apply_payload(self._payload(persistent_key="uid:youtube", open_group=None))
        strategy_list = page._strategy_list

        strategy_list._toggle_group_item(strategy_list._group_header_items["fake"])

        create_worker.assert_called_once_with(
            1,
            page.launch_method,
            profile_key="profile:0",
            group_key="fake",
            parent=page,
        )

    def test_only_the_last_group_is_saved_after_the_running_save(self) -> None:
        create_worker = Mock(return_value=Mock())
        page = self._page(create_worker)
        page._profile_key = "profile:0"

        page._on_strategy_open_group_changed("uid:youtube", "fake")
        page._on_strategy_open_group_changed("uid:youtube", "split")
        page._on_strategy_open_group_changed("uid:youtube", "")
        self.assertEqual(create_worker.call_count, 1)

        page._strategy_open_group_save_runtime.finish()

        self.assertEqual(create_worker.call_count, 2)
        self.assertEqual(create_worker.call_args.kwargs["group_key"], "")
        page._strategy_open_group_save_runtime.finish()
        self.assertEqual(create_worker.call_count, 2)

    def test_save_of_previous_profile_is_not_lost_when_another_is_opened(self) -> None:
        create_worker = Mock(return_value=Mock())
        page = self._page(create_worker)
        page._profile_key = "profile:0"
        page._on_strategy_open_group_changed("uid:youtube", "fake")
        page._on_strategy_open_group_changed("uid:youtube", "split")

        page._profile_key = "profile:1"
        page._on_strategy_open_group_changed("uid:discord", "host")
        page._strategy_open_group_save_runtime.finish()
        page._strategy_open_group_save_runtime.finish()

        saved = [(call.kwargs["profile_key"], call.kwargs["group_key"]) for call in create_worker.call_args_list]
        self.assertEqual(saved, [("profile:0", "fake"), ("profile:0", "split"), ("profile:1", "host")])

    def test_without_save_worker_the_group_is_kept_only_for_the_session(self) -> None:
        page = self._page(None)
        page._profile_key = "profile:0"
        page._apply_payload(self._payload(persistent_key="uid:youtube", open_group=None))
        strategy_list = page._strategy_list

        strategy_list._toggle_group_item(strategy_list._group_header_items["fake"])

        self.assertEqual(self._open_groups(page), ["fake"])
        self.assertEqual(page._pending_strategy_open_group_saves, {})


class ProfileStrategyOpenGroupWorkerTests(unittest.TestCase):
    def test_worker_passes_profile_and_group_to_the_feature(self) -> None:
        from app.feature_facades.profile import ProfileFeature

        # Фасад — замороженный класс, поэтому вместо него подставлен простой объект.
        feature = SimpleNamespace(set_strategy_open_group=Mock(return_value=True))

        worker = ProfileFeature.create_profile_strategy_open_group_save_worker(
            feature, 7, "direct_zapret2", profile_key="profile:0", group_key="fake"
        )
        saved: list[int] = []
        worker.saved.connect(saved.append)
        worker.run()

        feature.set_strategy_open_group.assert_called_once_with("direct_zapret2", "profile:0", "fake")
        self.assertEqual(saved, [7])

    def test_worker_reports_failure_instead_of_raising(self) -> None:
        from profile.profile_setup_loader import ProfileStrategyOpenGroupSaveWorker

        worker = ProfileStrategyOpenGroupSaveWorker(
            3, Mock(side_effect=ValueError("unknown strategy group key")), profile_key="profile:0", group_key="fake"
        )
        failed: list[tuple[int, str]] = []
        worker.failed.connect(lambda request_id, error: failed.append((request_id, error)))

        worker.run()

        self.assertEqual(failed, [(3, "unknown strategy group key")])

    def test_service_saves_by_the_permanent_profile_key(self) -> None:
        from profile.service import ProfilePresetService

        service = ProfilePresetService.__new__(ProfilePresetService)
        service._state_store = Mock()
        service._state_store.set_open_group.return_value = True
        service._resolve_profile = Mock(return_value=SimpleNamespace(persistent_key="uid:youtube"))

        self.assertTrue(ProfilePresetService.set_strategy_open_group(service, "profile:0", "fake"))
        service._state_store.set_open_group.assert_called_once_with("uid:youtube", "fake")

        service._resolve_profile = Mock(return_value=None)
        self.assertFalse(ProfilePresetService.set_strategy_open_group(service, "profile:9", "fake"))


if __name__ == "__main__":
    unittest.main()
