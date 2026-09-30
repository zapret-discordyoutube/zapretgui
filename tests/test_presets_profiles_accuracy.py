"""Точность пресетов и профилей: мелкие ошибки, найденные аудитом.

Каждый тест описывает сценарий, который раньше давал неверный результат.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class LaunchValidationTests(unittest.TestCase):
    def test_filter_flag_inside_comment_does_not_make_preset_launchable(self) -> None:
        from profile.launch_validation import preset_has_required_filter_flags

        self.assertFalse(preset_has_required_filter_flags("zapret2_mode", "# --wf-tcp-out=443\n--lua-desync=pass\n"))
        self.assertTrue(preset_has_required_filter_flags("zapret2_mode", "--wf-tcp-out=443\n--lua-desync=pass\n"))


class LineSplittingTests(unittest.TestCase):
    def test_launch_args_split_only_on_newline_like_parser(self) -> None:
        from winws_runtime.runners.preset_runner_support import launch_args_from_preset_text

        # \u2028 внутри значения — одна строка для парсера; splitlines() резал её надвое.
        args = launch_args_from_preset_text("--wf-tcp-out=443\n--hostlist-domains=a\u2028b\n")
        self.assertEqual(args, ["--wf-tcp-out=443", "--hostlist-domains=a\u2028b"])


class StrategyFilterPreservationTests(unittest.TestCase):
    def test_last_of_consecutive_leading_payloads_is_kept(self) -> None:
        from profile.parser import parse_preset_text
        from profile.serializer import _preserve_missing_winws2_strategy_filters

        profile = parse_preset_text(
            "--filter-tcp=443\n--payload=tls_client_hello\n--payload=http_req\n--lua-desync=fake\n"
            "--payload=quic\n--lua-desync=split\n",
            engine="winws2",
            source_name="t.txt",
        ).profiles[0]

        # Из подряд идущих --payload действует последний; фильтр второй ветки
        # (--payload=quic) к началу новой стратегии не относится.
        self.assertEqual(
            _preserve_missing_winws2_strategy_filters("winws2", profile, ["--lua-desync=multisplit"]),
            ["--payload=http_req", "--lua-desync=multisplit"],
        )


class UserProfileFilesTests(unittest.TestCase):
    def test_new_profile_does_not_share_list_files_of_renamed_profile(self) -> None:
        from core.paths import AppPaths
        from profile.user_profiles import create_user_profile
        from profile.user_profiles.service import update_user_profile
        from settings.store import get_user_profiles_settings

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = AppPaths(user_root=root, local_root=root)
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                first = create_user_profile(paths, name="Alpha", protocol="tcp", ports="443")
                update_user_profile(paths, first, name="Beta", protocol="tcp", ports="443")
                second = create_user_profile(paths, name="Beta!", protocol="tcp", ports="443")
                profiles = get_user_profiles_settings()["profiles"]

        self.assertNotEqual(profiles[first]["hostlist"], profiles[second]["hostlist"])
        self.assertNotEqual(profiles[first]["ipset"], profiles[second]["ipset"])

    def test_corrupt_row_does_not_break_template_library(self) -> None:
        from core.paths import AppPaths
        from profile.user_profiles.service import load_user_profile_templates

        rows = {
            "profiles": {
                "good": {"name": "Good", "protocol": "tcp", "ports": "443", "hostlist": "lists/good.txt", "ipset": ""},
                "bad": {"name": "", "protocol": "nonsense", "ports": "x"},
            }
        }
        with patch("profile.user_profiles.service.get_user_profiles_settings", return_value=rows):
            templates = load_user_profile_templates(AppPaths(user_root=Path("."), local_root=Path(".")), "winws2")

        self.assertIn("user:good", templates)
        self.assertNotIn("user:bad", templates)


class DerivedCacheKeyTests(unittest.TestCase):
    def test_renamed_profile_gets_fresh_core_and_list_files_change_invalidates(self) -> None:
        from profile.derived_cache import ProfileDerivedCache

        cache = ProfileDerivedCache()
        profile_a = SimpleNamespace(engine="winws2", name="Другое", display_name="Другое", segments=[])
        profile_b = SimpleNamespace(engine="winws2", name="Исключения", display_name="Исключения", segments=[])
        built = []

        def _build(profile, **_kwargs):
            built.append(profile.name)
            return object()

        with tempfile.TemporaryDirectory() as temp_dir:
            app_paths = SimpleNamespace(user_root=temp_dir)
            (Path(temp_dir) / "lists").mkdir()
            with patch("profile.derived_cache.build_profile_derived_core", _build):
                cache.core_for(profile_a, catalogs={}, catalogs_signature=(), app_paths=app_paths)
                # Тот же текст, другое имя (роль «исключения» зависит от имени).
                cache.core_for(profile_b, catalogs={}, catalogs_signature=(), app_paths=app_paths)
                self.assertEqual(built, ["Другое", "Исключения"])

                (Path(temp_dir) / "lists" / "user").mkdir()
                cache._lists_signature_at = -1.0  # сброс полусекундной памяти
                cache.core_for(profile_a, catalogs={}, catalogs_signature=(), app_paths=app_paths)
                self.assertEqual(built, ["Другое", "Исключения", "Другое"])


class PresetStoreNamingTests(unittest.TestCase):
    def setUp(self) -> None:
        from core.paths import AppPaths
        from presets.file_store import PresetFileStore

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.store = PresetFileStore(AppPaths(user_root=root / "user", local_root=root / "local"))
        self.user_dir = self.store._engine_paths("winws2").user_presets_dir
        self.user_dir.mkdir(parents=True, exist_ok=True)

    def test_dotted_name_resolves_to_its_own_file_not_to_prefix(self) -> None:
        (self.user_dir / "v1.txt").write_text("# Preset: v1\n", encoding="utf-8")
        (self.user_dir / "v1.5.txt").write_text("# Preset: v1.5\n", encoding="utf-8")

        self.assertEqual(self.store.resolve_file_name("winws2", "v1.5"), "v1.5.txt")

    def test_resolve_returns_disk_spelling(self) -> None:
        (self.user_dir / "My Preset.txt").write_text("# Preset: My Preset\n", encoding="utf-8")

        self.assertEqual(self.store.resolve_file_name("winws2", "my preset.txt"), "My Preset.txt")

    def test_unique_name_is_free_in_list_and_on_disk(self) -> None:
        (self.user_dir / "X.txt").write_text("# Preset: X\n", encoding="utf-8")
        (self.user_dir / "Other.txt").write_text("# Preset: X (2)\n", encoding="utf-8")

        # «X» занят файлом, «X (2)» — отображаемым именем другого пресета.
        self.assertEqual(self.store.unique_preset_name("winws2", "X"), "X (3)")
        # Переименование пресета в своё же имя (смена регистра) не считается занятым.
        self.assertEqual(self.store.unique_preset_name("winws2", "x", exclude_file_name="X.txt"), "x")


class LongAndReservedNamesTests(unittest.TestCase):
    def test_unique_name_for_long_taken_name_terminates(self) -> None:
        from core.paths import AppPaths
        from presets.file_store import PresetFileStore, _sanitize_file_stem

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = PresetFileStore(AppPaths(user_root=root / "user", local_root=root / "local"))
            user_dir = store._engine_paths("winws2").user_presets_dir
            user_dir.mkdir(parents=True, exist_ok=True)
            long_name = "A" * 105
            (user_dir / f"{_sanitize_file_stem(long_name)}.txt").write_text("# Preset: x\n", encoding="utf-8")

            # Раньше «AAA…A (2)» обрезалось до того же имени файла — цикл без конца.
            unique = store.unique_preset_name("winws2", long_name)

        self.assertTrue(unique.endswith(" (2)"))
        self.assertLessEqual(len(_sanitize_file_stem(unique)), 100)

    def test_user_profile_named_like_windows_device_gets_valid_list_files(self) -> None:
        from core.paths import AppPaths
        from profile.user_profiles import create_user_profile
        from settings.store import get_user_profiles_settings

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                profile_id = create_user_profile(AppPaths(user_root=root, local_root=root), name="con", protocol="tcp", ports="443")
                row = get_user_profiles_settings()["profiles"][profile_id]

        self.assertNotEqual(row["hostlist"].lower(), "lists/con.txt")

    def test_list_invalidation_forgets_lists_signature_memo(self) -> None:
        from profile.derived_cache import ProfileDerivedCache
        from profile.service import ProfilePresetService

        service = ProfilePresetService.__new__(ProfilePresetService)
        service._profile_list_snapshot = None
        service._profile_list_snapshot_revision = None
        service._profile_list_snapshots_by_revision = {}
        service._profile_sources_cache = {}
        service._profile_derived_cache = ProfileDerivedCache()
        service._profile_derived_cache._lists_signature_at = 10**9

        service._invalidate_profile_list_snapshot()

        self.assertLess(service._profile_derived_cache._lists_signature_at, 0)


class SelectionFallbackGuardsTests(unittest.TestCase):
    def test_fallback_preset_in_use_cannot_be_deleted(self) -> None:
        from core.paths import AppPaths
        from presets.file_store import PresetFileStore
        from presets.selection_service import PresetSelectionService
        from settings import store as settings_store

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                store = PresetFileStore(AppPaths(user_root=root, local_root=root))
                user_dir = store._engine_paths("winws2").user_presets_dir
                user_dir.mkdir(parents=True, exist_ok=True)
                (user_dir / "Fallback.txt").write_text("# Preset: Fallback\n", encoding="utf-8")
                settings_store.set_selected_source_preset_file_name("winws2", "gone.txt")
                selection = PresetSelectionService(store)

                # Работает запасной «Fallback» (его интерфейс и показывает активным).
                self.assertEqual(selection.ensure_selected_manifest("winws2").file_name, "Fallback.txt")
                with self.assertRaises(ValueError):
                    selection.ensure_can_delete("winws2", "Fallback.txt")


class ProfileListSnapshotPlacementTests(unittest.TestCase):
    def test_strategy_change_keeps_row_folder_and_order(self) -> None:
        import threading

        from profile.service import ProfilePresetService

        service = ProfilePresetService.__new__(ProfilePresetService)
        service._profile_list_lock = threading.RLock()
        from profile.state import ProfileListItem
        from dataclasses import fields

        def _item(**overrides):
            base = {field.name: None for field in fields(ProfileListItem)}
            base.update(
                key="uid:1", persistent_key="uid:1", profile_index=0, display_name="YT", enabled=True,
                in_preset=True, strategy_id="s", strategy_name="S", match_lines=(), list_type="hostlist",
                rating="", favorite=False, group="common", group_name="", order=0,
            )
            base.update(overrides)
            return ProfileListItem(**{k: v for k, v in base.items() if v is not None or k in overrides})

        old_row = _item(strategy_id="old", group="youtube", group_name="YouTube", order=7, group_rank=3, group_collapsed=True)
        new_row = _item(strategy_id="new")
        from profile.state import ProfileListPayload

        service._profile_list_snapshot = ProfileListPayload(
            items=(old_row,), selected_preset_file_name="P.txt", selected_preset_name="P"
        )
        service._profile_list_snapshots_by_revision = {}
        service._current_profile_list_revision = lambda: "rev"
        service._remember_profile_list_snapshot = lambda *_a: None

        self.assertTrue(service._replace_profile_list_snapshot_item("uid:1", new_row))

        row = service._profile_list_snapshot.items[0]
        self.assertEqual(row.strategy_id, "new")
        self.assertEqual((row.group, row.group_name, row.order, row.group_rank, row.group_collapsed), ("youtube", "YouTube", 7, 3, True))

    def test_snapshot_of_other_revision_is_not_patched(self) -> None:
        import threading

        from profile.service import ProfilePresetService

        service = ProfilePresetService.__new__(ProfilePresetService)
        service._profile_list_lock = threading.RLock()
        service._profile_list_snapshot = None
        # Снимок ДРУГОЙ ревизии, в котором есть та же строка: раньше его
        # подставляли под текущую ревизию и считали свежим.
        stale = SimpleNamespace(items=(SimpleNamespace(key="uid:1", persistent_key="uid:1"),))
        service._profile_list_snapshots_by_revision = {"old": stale}
        service._current_profile_list_revision = lambda: "new"
        service._remember_profile_list_snapshot = Mock()

        # False -> вызывающий сбросит снимки и список пересоберётся честно.
        self.assertFalse(service._replace_profile_list_snapshot_item("uid:1", SimpleNamespace(key="uid:1")))
        service._remember_profile_list_snapshot.assert_not_called()


class PortableArchiveTests(unittest.TestCase):
    def test_inline_list_reference_is_rewritten(self) -> None:
        from presets.portable_archive import _rewrite_list_file_references

        text = "--hostlist=lists/a.txt --filter-tcp=443\n# --hostlist=lists/a.txt\n"
        self.assertEqual(
            _rewrite_list_file_references(text, {"a.txt": "a-imported-2.txt"}),
            "--hostlist=lists/a-imported-2.txt --filter-tcp=443\n# --hostlist=lists/a.txt\n",
        )

    def test_dangerous_list_file_names_are_rejected(self) -> None:
        from lists.core.layered_files import safe_list_file_name

        for name in ("a.txt:stream", "CON.txt", "nul", "COM1.lst", "bad?.txt", "trail.", "tab\t.txt"):
            with self.subTest(name=name):
                self.assertEqual(safe_list_file_name(name), "")
        self.assertEqual(safe_list_file_name("lists/comet.txt"), "comet.txt")


class UserPresetsPageTests(unittest.TestCase):
    def test_import_result_is_shown_even_when_more_actions_are_queued(self) -> None:
        from presets.ui.common.user_presets_page import UserPresetsPageBase

        page = UserPresetsPageBase.__new__(UserPresetsPageBase)
        page._preset_bulk_action_request_id = 4
        page._has_pending_preset_write_action = lambda: True
        page._runtime_service = SimpleNamespace(
            add_created_preset_locally=Mock(return_value=True),
            mark_presets_structure_changed=Mock(),
        )
        page.window = lambda: None
        result = SimpleNamespace(
            ok=False, log_message="Файл не похож на пресет", log_level="WARNING",
            infobar_level="warning", infobar_title="Импорт", infobar_content="Файл не похож на пресет",
        )

        with patch("presets.ui.common.user_presets_page.InfoBar") as info_bar, patch(
            "presets.ui.common.user_presets_page.log"
        ) as log_fn:
            UserPresetsPageBase._on_preset_bulk_action_finished(page, 4, "import", result, {})

        info_bar.warning.assert_called_once()
        log_fn.assert_called_once()

    def test_requeued_import_keeps_source_url_and_auto_update(self) -> None:
        from presets.ui.common.user_presets_page import UserPresetsPageBase

        page = UserPresetsPageBase.__new__(UserPresetsPageBase)
        page._queue_preset_write_action = Mock()

        UserPresetsPageBase._queue_preset_write_action_from_dict(
            page,
            {"kind": "bulk", "action": "import", "file_path": "C:/p.txt", "source_url": "https://x/p.txt", "auto_update": True},
        )

        kwargs = page._queue_preset_write_action.call_args.kwargs
        self.assertEqual(kwargs["source_url"], "https://x/p.txt")
        self.assertTrue(kwargs["auto_update"])

    def test_folder_collapse_is_shown_immediately(self) -> None:
        from presets.ui.common.user_presets_page import UserPresetsPageBase

        page = UserPresetsPageBase.__new__(UserPresetsPageBase)
        page._current_preset_folder_collapsed = lambda _key: False
        page._apply_preset_folder_state_locally = Mock(return_value=True)
        page._request_preset_folder_action = Mock()

        UserPresetsPageBase._on_toggle_folder(page, "games")

        page._apply_preset_folder_state_locally.assert_called_once_with(
            "set_collapsed", {"folder_key": "games", "collapsed": True}
        )
        page._request_preset_folder_action.assert_called_once_with("set_collapsed", folder_key="games", collapsed=True)


class ReviewFollowUpTests(unittest.TestCase):
    def test_list_action_is_refused_while_list_shows_previous_preset(self) -> None:
        from profile.ui.preset_setup_page import PresetSetupPageBase
        from profile.ui.preset_write_queue import PresetWriteQueue

        page = PresetSetupPageBase.__new__(PresetSetupPageBase)
        page._displayed_preset_file_name = "A.txt"
        page._ui_state_store = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(active_preset_file_name="B.txt", active_preset_launch_method="zapret2_mode")
        )
        page._profile_reference_for = lambda key: key
        queue = PresetWriteQueue(page)
        queue._start_profile_context_action_worker = Mock()
        queue._profile_preset_write_operation_running = lambda: False

        # Активный пресет уже B, строки ещё от A: удаление ушло бы в B.
        queue._request_profile_context_action("delete", "uid:1")

        queue._start_profile_context_action_worker.assert_not_called()

    def test_preset_of_other_mode_does_not_block_list_actions(self) -> None:
        from profile.ui.preset_setup_page import Zapret1PresetSetupPage
        from profile.ui.preset_write_queue import PresetWriteQueue

        # Store общий: последним переключали пресет zapret2, а открыт список
        # zapret1 со своим пресетом — это не «устаревший список».
        page = Zapret1PresetSetupPage.__new__(Zapret1PresetSetupPage)
        page._displayed_preset_file_name = "Default v1.txt"
        page._ui_state_store = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(active_preset_file_name="Default v5.txt", active_preset_launch_method="zapret2_mode")
        )
        page._profile_reference_for = lambda key: key
        queue = PresetWriteQueue(page)
        queue._start_profile_context_action_worker = Mock()
        queue._profile_preset_write_operation_running = lambda: False

        queue._request_profile_context_action("delete", "uid:1")

        queue._start_profile_context_action_worker.assert_called_once()

    def test_scan_with_locked_file_is_not_cached(self) -> None:
        from core.paths import AppPaths
        from presets import file_store as file_store_module
        from presets.file_store import PresetFileStore

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = PresetFileStore(AppPaths(user_root=root / "user", local_root=root / "local"))
            user_dir = store._engine_paths("winws2").user_presets_dir
            user_dir.mkdir(parents=True, exist_ok=True)
            (user_dir / "Locked.txt").write_text("# Preset: Настоящее имя\n", encoding="utf-8")
            real_read = file_store_module._read_header_text
            locked = {"on": True}

            def _read(path):
                if locked["on"]:
                    raise PermissionError(13, "locked")
                return real_read(path)

            clock = {"now": 1000.0}
            with patch.object(file_store_module, "_read_header_text", _read), patch.object(
                file_store_module.time, "monotonic", lambda: clock["now"]
            ):
                self.assertEqual(store.get_manifest("winws2", "Locked.txt").name, "Locked")
                locked["on"] = False
                # Файл разблокирован, папка не менялась: неполная запись живёт
                # недолго, а не до следующего изменения папки.
                clock["now"] += 5.0
                self.assertEqual(store.get_manifest("winws2", "Locked.txt").name, "Настоящее имя")

    def test_in_place_row_update_supersedes_running_filter(self) -> None:
        from profile.ui.profile_strategy_list_widget import ProfileStrategyListWidget

        widget = ProfileStrategyListWidget.__new__(ProfileStrategyListWidget)
        widget._strategy_filter_runtime = SimpleNamespace(is_running=lambda: True)
        widget._request_tree_rebuild = Mock()

        widget._supersede_running_strategy_filter()

        widget._request_tree_rebuild.assert_called_once_with()

    def test_click_on_fallback_preset_persists_selection(self) -> None:
        from app.feature_facades.presets import PresetsFeature

        feature = PresetsFeature.__new__(PresetsFeature)
        activate = Mock()
        captured = {}

        def _worker(_request_id, activate_fn, **_kwargs):
            captured["activate"] = activate_fn
            return None

        # Сохранён пропавший gone.txt, работает (и показан активным) Fallback.txt.
        with patch.object(PresetsFeature, "get_stored_source_preset_file_name", lambda _self, _m: "gone.txt"), patch.object(
            PresetsFeature, "get_selected_source_preset_file_name", lambda _self, _m: "Fallback.txt"
        ), patch.object(PresetsFeature, "activate_preset_file", lambda _self, *a: activate(*a)), patch(
            "presets.user_presets_action_workers.UserPresetActivateWorker", _worker
        ):
            feature.create_preset_activate_worker(
                1,
                launch_method="zapret2_mode",
                file_name="Fallback.txt",
                display_name="Fallback",
                activate_error_level="error",
                activate_error_mode="plain",
            )
            captured["activate"](file_name="Fallback.txt", display_name="Fallback")

        # Щелчок закрепляет выбор, а не считается «уже выбран».
        activate.assert_called_once_with("zapret2_mode", "Fallback.txt")


class ProfileFolderMoveStepsTests(unittest.TestCase):
    def test_repeated_move_steps_are_not_collapsed(self) -> None:
        from profile.ui.profile_folder_controller import ProfileFolderController
        from ui.queued_worker_state import QueuedWorkerState

        state = QueuedWorkerState(None)
        page = SimpleNamespace(_profile_folder_action_state_obj=lambda: state)
        controller = ProfileFolderController(page)
        step = {"action": "move", "folder_key": "games", "direction": -1}

        for _ in range(3):
            controller._queue_profile_folder_action(dict(step))

        self.assertEqual(len(state.pending), 3)


if __name__ == "__main__":
    unittest.main()
