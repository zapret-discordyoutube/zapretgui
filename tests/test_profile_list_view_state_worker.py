from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


class ProfileListViewStateWorkerTests(unittest.TestCase):
    def test_profile_list_worker_builds_view_state_off_gui_thread(self) -> None:
        from profile.profile_list_loader import ProfileListLoadWorker

        init_source = inspect.getsource(ProfileListLoadWorker.__init__)
        run_source = inspect.getsource(ProfileListLoadWorker.run)

        self.assertIn("build_view_state", init_source)
        self.assertIn("self._build_view_state", run_source)
        self.assertIn("ProfileListLoadResult", run_source)

    def test_profile_feature_builds_profile_list_state_without_ui_model_import(self) -> None:
        from app.feature_facades.profile import ProfileFeature

        warm_source = inspect.getsource(ProfileFeature.warm_profile_list)
        worker_source = inspect.getsource(ProfileFeature.create_profile_list_load_worker)

        self.assertIn("profile.list_view_state", warm_source)
        self.assertIn("profile.list_view_state", worker_source)
        self.assertNotIn("profile.ui.profile_list_model", warm_source)
        self.assertNotIn("profile.ui.profile_list_model", worker_source)

    def test_list_file_save_worker_tells_running_engine_to_reload_lists(self) -> None:
        # Файл списка записан в потоке сохранения; движок перечитывает его
        # сигналом, перезапуск не нужен. Сигнал подаётся не в потоке интерфейса.
        from app.feature_facades.profile import ProfileFeature

        calls: list[str] = []
        feature = ProfileFeature(SimpleNamespace(), SimpleNamespace())
        worker = None
        with (
            patch.object(ProfileFeature, "save_profile_list_file_text", lambda self, *a, **k: calls.append("save") or "state"),
            patch.object(ProfileFeature, "get_profile_setup", lambda self, *a, **k: calls.append("load") or "payload"),
            patch(
                "winws_runtime.runtime.system_ops.reload_own_engine_lists_runtime",
                side_effect=lambda: calls.append("reload") or 1,
            ),
        ):
            worker = feature.create_profile_list_file_save_worker(
                1, "zapret2", profile_key="p", text="example.com"
            )
            worker.run()

        self.assertEqual(calls, ["save", "reload", "load"])

    def test_list_file_save_failure_does_not_signal_engine(self) -> None:
        from app.feature_facades.profile import ProfileFeature

        feature = ProfileFeature(SimpleNamespace(), SimpleNamespace())
        with (
            patch.object(ProfileFeature, "save_profile_list_file_text", side_effect=ValueError("bad")),
            patch("winws_runtime.runtime.system_ops.reload_own_engine_lists_runtime") as reload,
        ):
            worker = feature.create_profile_list_file_save_worker(
                1, "zapret2", profile_key="p", text="x"
            )
            worker.run()

        reload.assert_not_called()

    def test_profile_feature_has_no_duplicate_result_cache(self) -> None:
        from app.feature_facades.profile import ProfileFeature

        feature = ProfileFeature(SimpleNamespace(), SimpleNamespace())
        class_source = inspect.getsource(ProfileFeature)

        self.assertFalse(hasattr(feature, "_profile_list_load_result_cache"))
        self.assertNotIn("_profile_list_load_result", class_source)
        self.assertNotIn("_remember_profile_list_load_result", class_source)

    def test_warm_profile_list_delegates_caching_to_service(self) -> None:
        from app.feature_facades.profile import ProfileFeature
        from settings.mode import ZAPRET2_MODE

        payload = SimpleNamespace(items=())
        service = SimpleNamespace(list_profiles=Mock(return_value=payload))
        feature = ProfileFeature(SimpleNamespace(), SimpleNamespace())

        with patch.object(ProfileFeature, "_commands") as commands:
            commands.return_value._profile_preset_service.return_value = service
            result = feature.warm_profile_list(ZAPRET2_MODE)

        service.list_profiles.assert_called_once_with()
        self.assertIs(result.payload, payload)
        self.assertIsNotNone(result.view_state)

        warm_source = inspect.getsource(ProfileFeature.warm_profile_list)
        self.assertNotIn("warm_profile_setups", warm_source)

    def test_profile_list_load_worker_reads_service_directly(self) -> None:
        from app.feature_facades.profile import ProfileFeature

        worker_source = inspect.getsource(ProfileFeature.create_profile_list_load_worker)

        self.assertIn("service.list_profiles()", worker_source)
        self.assertNotIn("_profile_list_load_result", worker_source)

    def test_preset_setup_page_applies_worker_view_state_to_profile_list(self) -> None:
        from profile.ui.preset_setup_page import PresetSetupPageBase

        apply_source = inspect.getsource(PresetSetupPageBase._apply_payload)

        self.assertIn("view_state", apply_source)
        self.assertIn("apply_view_state", apply_source)
        self.assertNotIn("profiles_list.build_profiles(tuple(payload.items))", apply_source)

    def test_preset_setup_page_does_not_read_cached_profile_payload_in_gui(self) -> None:
        from profile.ui.preset_setup_page import PresetSetupPageBase
        from ui.page_deps.presets import build_preset_setup_page_kwargs

        init_source = inspect.getsource(PresetSetupPageBase.__init__)
        request_source = inspect.getsource(PresetSetupPageBase._request_profiles_payload)
        page_source = inspect.getsource(PresetSetupPageBase)
        deps_source = inspect.getsource(build_preset_setup_page_kwargs)

        self.assertNotIn("get_cached_profile_list", init_source)
        self.assertNotIn("get_cached_profile_list", request_source)
        self.assertNotIn("_apply_cached_profile_payload", page_source)
        self.assertNotIn("get_cached_profile_list", deps_source)


if __name__ == "__main__":
    unittest.main()
