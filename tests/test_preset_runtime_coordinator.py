from __future__ import annotations

import inspect
import time
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6.QtWidgets import QApplication


class PresetRuntimeCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        # Координаторы в тестах живут меньше, чем их watch-QThread'ы. Если
        # Python соберёт координатора, пока поток ещё работает, Qt делает
        # fail-fast (0xC0000409). Отслеживаем каждый реальный worker и в
        # tearDown дожидаемся его завершения до сборки мусора.
        from core.runtime import preset_runtime_coordinator as prc

        self._live_watch_workers: list = []
        original_worker = prc.PresetWatchPathResolveWorker
        test_case = self

        class _TrackedWatchWorker(original_worker):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                test_case._live_watch_workers.append(self)

        self._watch_worker_patch = patch.object(
            prc, "PresetWatchPathResolveWorker", _TrackedWatchWorker
        )
        self._watch_worker_patch.start()

    def tearDown(self) -> None:
        self._watch_worker_patch.stop()
        for worker in self._live_watch_workers:
            try:
                worker.wait(2000)
            except (RuntimeError, AttributeError):
                pass
        for _ in range(3):
            self._app.processEvents()
        self._live_watch_workers.clear()

    def _process_events_until(self, predicate, *, timeout_s: float = 0.5) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._app.processEvents()
            if predicate():
                return True
            time.sleep(0.001)
        self._app.processEvents()
        return bool(predicate())

    def test_runtime_command_passes_preset_source_resolver_to_coordinator(self) -> None:
        from winws_runtime.runtime import commands as runtime_commands

        resolver = lambda method, file_name: f"{method}/{file_name}"
        runtime_feature = SimpleNamespace(
            dependencies=SimpleNamespace(presets_feature=object()),
        )

        with patch(
            "core.runtime.preset_runtime_coordinator.PresetRuntimeCoordinator",
            side_effect=lambda *args, **kwargs: SimpleNamespace(args=args, kwargs=kwargs),
        ):
            coordinator = runtime_commands.create_preset_runtime_coordinator(
                object(),
                runtime_feature=runtime_feature,
                ui_state_store=object(),
                get_launch_method=lambda: "zapret2",
                get_active_preset_path=lambda: "",
                get_preset_source_path_by_file_name=resolver,
                refresh_after_switch=lambda: None,
            )

        self.assertIs(coordinator.kwargs["get_preset_source_path_by_file_name"], resolver)

    def test_saving_active_preset_uses_content_apply_not_preset_switch(self) -> None:
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator
        from settings.mode import ZAPRET2_MODE

        switch_calls: list[tuple[str, str, str]] = []
        content_calls: list[tuple[str, str, str]] = []
        active_path = "C:/Zapret/Dev/presets/winws2/Default v5.txt"
        presets_feature = SimpleNamespace(
            is_selected_source_preset_file=lambda method, file_name: (
                method == ZAPRET2_MODE and file_name == "Default v5.txt"
            )
        )
        ui_state = SimpleNamespace(
            content_revision=0,
            bump_preset_content_revision=lambda: setattr(
                ui_state,
                "content_revision",
                ui_state.content_revision + 1,
            ),
        )
        coordinator = PresetRuntimeCoordinator(
            presets_feature=presets_feature,
            ui_state_store=ui_state,
            get_launch_method=lambda: ZAPRET2_MODE,
            get_active_preset_path=lambda: active_path,
            refresh_after_switch=lambda: None,
            request_selected_source_preset_apply=lambda method, reason, file_name: switch_calls.append(
                (method, reason, file_name)
            )
            or True,
            request_preset_content_apply=lambda method, reason, file_name: content_calls.append(
                (method, reason, file_name)
            )
            or True,
        )
        coordinator._active_preset_file_path = active_path
        coordinator.setup_active_preset_file_watcher = lambda: None

        coordinator.handle_preset_content_changed(ZAPRET2_MODE, "Default v5.txt")
        self._app.processEvents()

        self.assertEqual(content_calls, [(ZAPRET2_MODE, "preset_content_changed", "Default v5.txt")])
        self.assertEqual(switch_calls, [])
        self.assertEqual(ui_state.content_revision, 1)

    def test_active_preset_content_change_publishes_reason_to_ui_state(self) -> None:
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator
        from settings.mode import ZAPRET2_MODE

        active_path = "C:/Zapret/Dev/presets/winws2/Default v5.txt"
        presets_feature = SimpleNamespace(
            is_selected_source_preset_file=lambda method, file_name: (
                method == ZAPRET2_MODE and file_name == "Default v5.txt"
            )
        )
        content_calls: list[tuple[str, str, str]] = []
        refresh_calls: list[str] = []
        ui_state = SimpleNamespace(content_revision=0, content_change_kind="")

        def bump_preset_content_revision(*, content_change_kind: str = "") -> None:
            ui_state.content_revision += 1
            ui_state.content_change_kind = content_change_kind

        ui_state.bump_preset_content_revision = bump_preset_content_revision
        coordinator = PresetRuntimeCoordinator(
            presets_feature=presets_feature,
            ui_state_store=ui_state,
            get_launch_method=lambda: ZAPRET2_MODE,
            get_active_preset_path=lambda: active_path,
            refresh_after_switch=lambda *, reason="": refresh_calls.append(reason),
            request_selected_source_preset_apply=lambda *_args: True,
            request_preset_content_apply=lambda method, reason, file_name: content_calls.append(
                (method, reason, file_name)
            )
            or True,
        )
        coordinator._active_preset_file_path = active_path
        coordinator.setup_active_preset_file_watcher = lambda: None

        coordinator.handle_preset_content_changed(ZAPRET2_MODE, "Default v5.txt", reason="strategy_only")
        self._app.processEvents()

        self.assertEqual(content_calls, [(ZAPRET2_MODE, "strategy_only", "Default v5.txt")])
        self.assertEqual(ui_state.content_revision, 1)
        self.assertEqual(ui_state.content_change_kind, "strategy_only")
        self.assertEqual(refresh_calls, ["strategy_only"])

        coordinator._on_active_preset_file_changed(active_path)

        self.assertEqual(content_calls, [(ZAPRET2_MODE, "strategy_only", "Default v5.txt")])
        self.assertEqual(ui_state.content_revision, 1)
        self.assertEqual(refresh_calls, ["strategy_only"])

    def _existing_preset_path(self, file_name: str) -> str:
        """Watcher проверяет, что файл на месте: пропавший пресет не применяется."""
        tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        path = Path(tmp_dir.name) / file_name
        path.write_text("--filter-tcp=443\n", encoding="utf-8")
        return str(path)

    def _make_watch_coordinator(self, active_path: str, content_calls: list):
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator
        from settings.mode import ZAPRET2_MODE

        ui_state = SimpleNamespace(content_revision=0)

        def bump_preset_content_revision(*, content_change_kind: str = "") -> None:
            ui_state.content_revision += 1

        ui_state.bump_preset_content_revision = bump_preset_content_revision
        presets_feature = SimpleNamespace(
            is_selected_source_preset_file=lambda method, file_name: True,
        )
        coordinator = PresetRuntimeCoordinator(
            presets_feature=presets_feature,
            ui_state_store=ui_state,
            get_launch_method=lambda: ZAPRET2_MODE,
            get_active_preset_path=lambda: active_path,
            refresh_after_switch=lambda *, reason="": None,
            request_selected_source_preset_apply=lambda *_args: True,
            request_preset_content_apply=lambda method, reason, file_name: content_calls.append(
                (method, reason, file_name)
            )
            or True,
        )
        coordinator._active_preset_file_path = active_path
        coordinator.setup_active_preset_file_watcher = lambda: None
        coordinator._ui_state = ui_state
        return coordinator

    def test_external_preset_file_change_triggers_runtime_apply(self) -> None:
        from settings.mode import ZAPRET2_MODE

        active_path = self._existing_preset_path("Default v5.txt")
        content_calls: list[tuple[str, str, str]] = []
        coordinator = self._make_watch_coordinator(active_path, content_calls)

        # Никакого собственного сохранения не было: изменение файла снаружи
        # обязано дойти до runtime, а не только обновить UI.
        coordinator._on_active_preset_file_changed(active_path)

        self.assertEqual(
            content_calls,
            [(ZAPRET2_MODE, "preset_file_external_change", "default v5.txt")],
        )
        self.assertEqual(coordinator._ui_state.content_revision, 1)

    def test_own_save_fs_events_are_suppressed_by_time_window(self) -> None:
        from settings.mode import ZAPRET2_MODE

        active_path = self._existing_preset_path("Default v5.txt")
        content_calls: list[tuple[str, str, str]] = []
        coordinator = self._make_watch_coordinator(active_path, content_calls)

        coordinator.handle_preset_content_changed(ZAPRET2_MODE, "Default v5.txt")
        self._app.processEvents()
        self.assertEqual(
            content_calls,
            [(ZAPRET2_MODE, "preset_content_changed", "Default v5.txt")],
        )

        # Atomic save породил два fs-события — оба внутри окна, оба свои.
        coordinator._on_active_preset_file_changed(active_path)
        coordinator._on_active_preset_file_changed(active_path)
        self.assertEqual(len(content_calls), 1)

        # Окно истекло: следующее событие — уже внешняя правка.
        coordinator._own_preset_content_suppress_deadline = time.monotonic() - 1.0
        coordinator._on_active_preset_file_changed(active_path)
        self.assertEqual(
            content_calls[-1],
            (ZAPRET2_MODE, "preset_file_external_change", "default v5.txt"),
        )

    def test_vanished_active_preset_is_not_applied_until_it_returns(self) -> None:
        from settings.mode import ZAPRET2_MODE

        active_path = self._existing_preset_path("Vanished preset.txt")
        content_calls: list[tuple[str, str, str]] = []
        coordinator = self._make_watch_coordinator(active_path, content_calls)
        coordinator._active_preset_file_watcher = SimpleNamespace(files=lambda: [], addPath=lambda _path: False)
        coordinator._active_preset_watch_rearm_timer = SimpleNamespace(start=lambda _ms: None)

        # Файл удалён/переименован в Проводнике: работающий пресет не
        # подменяется ни пустым, ни пресетом по умолчанию.
        Path(active_path).unlink()
        coordinator._on_active_preset_file_changed(active_path)
        self.assertEqual(content_calls, [])
        self.assertEqual(coordinator._ui_state.content_revision, 0)

        # Редактор сохранил «удалить и создать заново»: файл вернулся —
        # правка доходит до runtime как обычная внешняя.
        Path(active_path).write_text("--filter-tcp=80\n", encoding="utf-8")
        coordinator._active_preset_file_watcher = SimpleNamespace(files=lambda: [], addPath=lambda _path: True)
        coordinator._ensure_active_preset_watch_armed()
        self.assertEqual(
            content_calls,
            [(ZAPRET2_MODE, "preset_file_external_change", "vanished preset.txt")],
        )
        self.assertEqual(coordinator._ui_state.content_revision, 1)

    def test_unpublished_own_save_does_not_trigger_external_apply(self) -> None:
        from presets.own_write_registry import mark_own_preset_write

        active_path = self._existing_preset_path("Default v5.txt")
        content_calls: list[tuple[str, str, str]] = []
        coordinator = self._make_watch_coordinator(active_path, content_calls)

        # Автосохранение редактора: файл записан приложением без publish —
        # watcher не должен применять его к runtime как внешнюю правку.
        mark_own_preset_write(active_path)
        coordinator._on_active_preset_file_changed(active_path)

        self.assertEqual(content_calls, [])
        self.assertEqual(coordinator._ui_state.content_revision, 0)

    def test_preset_file_store_write_marks_own_write(self) -> None:
        from presets.file_store import PresetFileStore
        from presets.own_write_registry import was_recent_own_preset_write

        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "Own Write Probe.txt"
            PresetFileStore._write_source(target, "--new\n")

            self.assertTrue(was_recent_own_preset_write(str(target)))
            self.assertTrue(was_recent_own_preset_write("own write probe.txt"))
            self.assertFalse(was_recent_own_preset_write("other.txt"))

    def test_active_preset_watch_rearm_retries_after_atomic_save(self) -> None:
        active_path = self._existing_preset_path("Default v5.txt")
        content_calls: list[tuple[str, str, str]] = []
        coordinator = self._make_watch_coordinator(active_path, content_calls)

        watcher = SimpleNamespace(files=lambda: [], addPath=Mock(return_value=False))
        coordinator._active_preset_file_watcher = watcher

        # Файл на мгновение отсутствует (temp+rename): addPath проваливается,
        # но слежение должно восстановиться повтором, а не умереть молча.
        coordinator._ensure_active_preset_watch_armed()
        self.assertEqual(coordinator._active_preset_watch_rearm_attempts, 1)
        self.assertIsNotNone(coordinator._active_preset_watch_rearm_timer)
        self.assertTrue(coordinator._active_preset_watch_rearm_timer.isActive())

        watcher.addPath = Mock(return_value=True)
        coordinator._ensure_active_preset_watch_armed()
        self.assertEqual(coordinator._active_preset_watch_rearm_attempts, 0)
        coordinator._active_preset_watch_rearm_timer.stop()

    def test_editor_save_publishes_editor_save_content_change_kind(self) -> None:
        from presets.raw_preset_editor_workflow import save_raw_preset_text

        captured: dict = {}
        updated = SimpleNamespace(file_name="Default v5.txt", name="Default v5")

        def save_preset_source_by_file_name(
            method,
            file_name,
            source_text,
            *,
            publish_content_changed=True,
            content_change_kind="",
        ):
            captured["kind"] = content_change_kind
            captured["publish"] = publish_content_changed
            return updated

        def publish_preset_content_changed(method, file_name, *, content_change_kind=""):
            captured["published"] = (file_name, content_change_kind)

        presets_feature = SimpleNamespace(
            save_preset_source_by_file_name=save_preset_source_by_file_name,
            publish_preset_content_changed=publish_preset_content_changed,
            read_preset_source_by_file_name=lambda method, file_name: "--new\n--filter-tcp=443\n",
            get_preset_source_path_by_file_name=lambda method, file_name: Path(
                "C:/Zapret/presets"
            )
            / file_name,
        )

        save_raw_preset_text(
            presets_feature=presets_feature,
            launch_method="zapret2_mode",
            file_name="Default v5.txt",
            source_text="--new\n--filter-tcp=443\n",
            publish_content_changed=True,
        )

        self.assertEqual(captured["kind"], "editor_save")
        # Запись и публикация разделены: фиксация правки публикует всегда, даже
        # если текст уже записало автосохранение и сама запись ничего не меняет.
        self.assertFalse(captured["publish"])
        self.assertEqual(captured["published"], ("Default v5.txt", "editor_save"))

    def test_rapid_active_preset_content_changes_coalesce_to_one_apply(self) -> None:
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator
        from settings.mode import ZAPRET2_MODE

        content_calls: list[tuple[str, str, str]] = []
        ui_state = SimpleNamespace(
            content_revision=0,
            bump_preset_content_revision=lambda: setattr(
                ui_state,
                "content_revision",
                ui_state.content_revision + 1,
            ),
        )
        presets_feature = SimpleNamespace(
            is_selected_source_preset_file=lambda method, file_name: (
                method == ZAPRET2_MODE and file_name == "Default v5.txt"
            )
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            active_path = Path(tmp_dir) / "Default v5.txt"
            active_path.write_text("--new\n--filter-tcp=443\n", encoding="utf-8")
            coordinator = PresetRuntimeCoordinator(
                presets_feature=presets_feature,
                ui_state_store=ui_state,
                get_launch_method=lambda: ZAPRET2_MODE,
                get_active_preset_path=lambda: str(active_path),
                refresh_after_switch=lambda: None,
                request_selected_source_preset_apply=lambda *_args: True,
                request_preset_content_apply=lambda method, reason, file_name: content_calls.append(
                    (method, reason, file_name)
                )
                or True,
            )
            coordinator._active_preset_file_path = str(active_path)
            coordinator.setup_active_preset_file_watcher = lambda: None

            coordinator.handle_preset_content_changed(ZAPRET2_MODE, "Default v5.txt")
            coordinator.handle_preset_content_changed(ZAPRET2_MODE, "Default v5.txt")
            active_path.write_text("--new\n--filter-tcp=80\n", encoding="utf-8")
            coordinator.handle_preset_content_changed(ZAPRET2_MODE, "Default v5.txt")
            self._app.processEvents()

        self.assertEqual(
            content_calls,
            [
                (ZAPRET2_MODE, "preset_content_changed", "Default v5.txt"),
            ],
        )
        self.assertEqual(ui_state.content_revision, 1)

    def test_preset_content_change_handler_does_not_stat_source_file(self) -> None:
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator

        handle_source = inspect.getsource(PresetRuntimeCoordinator.handle_preset_content_changed)
        apply_source = inspect.getsource(PresetRuntimeCoordinator._apply_pending_preset_content_change)

        self.assertFalse(hasattr(PresetRuntimeCoordinator, "_build_preset_content_apply_fingerprint"))
        self.assertNotIn("os.stat", handle_source)
        self.assertNotIn("os.stat", apply_source)
        self.assertNotIn("open(", handle_source + apply_source)
        self.assertNotIn(".read(", handle_source + apply_source)
        self.assertNotIn("setup_active_preset_file_watcher()", apply_source)

    def test_preset_content_change_defers_watcher_and_apply_after_signal(self) -> None:
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator
        from settings.mode import ZAPRET2_MODE

        watched_paths: list[str] = []
        content_calls: list[tuple[str, str, str]] = []
        ui_state = SimpleNamespace(
            content_revision=0,
            bump_preset_content_revision=lambda: setattr(
                ui_state,
                "content_revision",
                ui_state.content_revision + 1,
            ),
        )
        coordinator = PresetRuntimeCoordinator(
            presets_feature=SimpleNamespace(
                is_selected_source_preset_file=lambda method, file_name: (
                    method == ZAPRET2_MODE and file_name == "Default v5.txt"
                )
            ),
            ui_state_store=ui_state,
            get_launch_method=lambda: ZAPRET2_MODE,
            get_active_preset_path=lambda: "C:/Zapret/Dev/presets/winws2/Default v5.txt",
            refresh_after_switch=lambda: None,
            request_selected_source_preset_apply=lambda *_args: True,
            request_preset_content_apply=lambda method, reason, file_name: content_calls.append(
                (method, reason, file_name)
            )
            or True,
        )
        coordinator._apply_active_preset_watch_path = lambda path: watched_paths.append(path)

        coordinator.handle_preset_content_changed(ZAPRET2_MODE, "Default v5.txt")

        self.assertEqual(watched_paths, [])
        self.assertEqual(content_calls, [])
        self.assertEqual(ui_state.content_revision, 0)

        self.assertTrue(self._process_events_until(lambda: bool(watched_paths)))

        self.assertEqual(watched_paths, ["C:/Zapret/Dev/presets/winws2/Default v5.txt"])
        self.assertEqual(
            content_calls,
            [(ZAPRET2_MODE, "preset_content_changed", "Default v5.txt")],
        )
        self.assertEqual(ui_state.content_revision, 1)

    def test_active_preset_revision_is_published_after_click_event(self) -> None:
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator
        from settings.mode import ZAPRET2_MODE

        class _UiState:
            def __init__(self) -> None:
                self.active_revision = 0

            def bump_active_preset_revision(self) -> None:
                self.active_revision += 1

        ui_state = _UiState()
        coordinator = PresetRuntimeCoordinator(
            presets_feature=SimpleNamespace(),
            ui_state_store=ui_state,
            get_launch_method=lambda: ZAPRET2_MODE,
            get_active_preset_path=lambda: "",
            refresh_after_switch=lambda: None,
            request_selected_source_preset_apply=lambda *_args: True,
            request_preset_content_apply=lambda *_args: True,
        )
        coordinator.setup_active_preset_file_watcher = lambda: None

        coordinator.handle_preset_switched(ZAPRET2_MODE, "Default v5.txt")

        self.assertEqual(ui_state.active_revision, 0)
        self._app.processEvents()
        self.assertEqual(ui_state.active_revision, 1)

    def test_preset_switch_defers_active_file_watcher_setup_after_click_event(self) -> None:
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator
        from settings.mode import ZAPRET2_MODE

        watched_paths: list[str] = []
        coordinator = PresetRuntimeCoordinator(
            presets_feature=SimpleNamespace(),
            ui_state_store=None,
            get_launch_method=lambda: ZAPRET2_MODE,
            get_active_preset_path=lambda: "C:/Zapret/Dev/presets/winws2/Default v5.txt",
            refresh_after_switch=lambda: None,
            request_selected_source_preset_apply=lambda *_args: True,
            request_preset_content_apply=lambda *_args: True,
        )
        coordinator._apply_active_preset_watch_path = lambda path: watched_paths.append(path)

        coordinator.handle_preset_switched(ZAPRET2_MODE, "Default v5.txt")

        self.assertEqual(watched_paths, [])
        self.assertTrue(self._process_events_until(lambda: bool(watched_paths)))
        self.assertEqual(watched_paths, ["C:/Zapret/Dev/presets/winws2/Default v5.txt"])

    def test_preset_switch_watcher_uses_switched_file_name_not_selected_lookup(self) -> None:
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator
        from settings.mode import ZAPRET2_MODE

        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "Default v5.txt"
            preset_path.write_text("--new\n", encoding="utf-8")
            path_calls: list[tuple[str, str]] = []

            coordinator = PresetRuntimeCoordinator(
                presets_feature=SimpleNamespace(),
                ui_state_store=None,
                get_launch_method=lambda: ZAPRET2_MODE,
                get_active_preset_path=lambda: (_ for _ in ()).throw(
                    AssertionError("watcher setup must not re-read selected source preset")
                ),
                get_preset_source_path_by_file_name=lambda method, file_name: path_calls.append(
                    (method, file_name)
                )
                or str(preset_path),
                refresh_after_switch=lambda: None,
                request_selected_source_preset_apply=lambda *_args: True,
                request_preset_content_apply=lambda *_args: True,
            )

            coordinator.handle_preset_switched(ZAPRET2_MODE, "Default v5.txt")
            # Резолвер вызывается в фоновом QThread — ждём его, а не гадаем
            # по одному processEvents().
            self.assertTrue(self._process_events_until(lambda: bool(path_calls)))

        self.assertEqual(path_calls, [(ZAPRET2_MODE, "Default v5.txt")])

    def test_startup_watcher_setup_resolves_path_off_the_gui_thread(self) -> None:
        import threading

        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator

        gui_thread = threading.get_ident()
        resolver_threads: list[int] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            preset_path = Path(temp_dir) / "Default.txt"
            preset_path.write_text("--new\n", encoding="utf-8")

            def _active_path() -> str:
                # Первый поиск пути читает заголовки всех пресетов в папке:
                # в GUI-потоке это задерживало кадр сразу после появления окна.
                resolver_threads.append(threading.get_ident())
                return str(preset_path)

            coordinator = PresetRuntimeCoordinator(
                presets_feature=SimpleNamespace(),
                ui_state_store=None,
                get_launch_method=lambda: "zapret2_mode",
                get_active_preset_path=_active_path,
                get_preset_source_path_by_file_name=lambda *_args: str(preset_path),
                refresh_after_switch=lambda: None,
                request_selected_source_preset_apply=lambda *_args: True,
                request_preset_content_apply=lambda *_args: True,
            )

            coordinator.setup_active_preset_file_watcher()

            # Сам вызов путь не ищет — этим занят фоновый поток.
            self.assertEqual(resolver_threads, [])
            self.assertTrue(
                self._process_events_until(
                    lambda: coordinator._active_preset_file_watcher is not None,
                    timeout_s=3.0,
                )
            )

        self.assertEqual(len(resolver_threads), 1)
        self.assertNotEqual(resolver_threads[0], gui_thread)
        self.assertEqual(
            [Path(path).name for path in coordinator._active_preset_file_watcher.files()]
            or [Path(coordinator._active_preset_file_path).name],
            ["Default.txt"],
        )

    def test_preset_switch_resolves_watcher_path_in_worker(self) -> None:
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator

        schedule_source = inspect.getsource(PresetRuntimeCoordinator._schedule_active_preset_file_watcher_setup)
        start_source = inspect.getsource(PresetRuntimeCoordinator._start_active_preset_watch_worker)
        loaded_source = inspect.getsource(PresetRuntimeCoordinator._on_active_preset_watch_path_loaded)

        self.assertIn("PresetWatchPathResolveWorker", start_source)
        self.assertIn("start_qthread_worker", start_source)
        self.assertNotIn("setup_active_preset_file_watcher()", schedule_source)
        self.assertNotIn("setup_active_preset_file_watcher()", start_source)
        self.assertNotIn("_active_preset_watch_runtime_worker", start_source)
        self.assertIn("_apply_active_preset_watch_path", loaded_source)

    def test_preset_watch_path_worker_uses_switched_file_name(self) -> None:
        from core.runtime.preset_runtime_coordinator import PendingPresetWatch, PresetWatchPathResolveWorker
        from settings.mode import ZAPRET2_MODE

        path_calls: list[tuple[str, str]] = []
        loaded: list[tuple[int, str]] = []
        worker = PresetWatchPathResolveWorker(
            7,
            pending=PendingPresetWatch(ZAPRET2_MODE, "Default v5.txt"),
            get_preset_source_path_by_file_name=lambda method, file_name: path_calls.append(
                (method, file_name)
            )
            or f"C:/Zapret/Dev/presets/winws2/{file_name}",
            get_active_preset_path=lambda: (_ for _ in ()).throw(
                AssertionError("worker must use switched file name first")
            ),
        )
        worker.loaded.connect(lambda request_id, path: loaded.append((request_id, path)))

        worker.run()

        self.assertEqual(path_calls, [(ZAPRET2_MODE, "Default v5.txt")])
        self.assertEqual(loaded, [(7, "C:/Zapret/Dev/presets/winws2/Default v5.txt")])

    def test_preset_watch_worker_start_failure_does_not_resolve_path_on_gui_thread(self) -> None:
        from core.runtime.preset_runtime_coordinator import PendingPresetWatch, PresetRuntimeCoordinator
        from settings.mode import ZAPRET2_MODE

        path_calls: list[tuple[str, str]] = []
        fallback_calls: list[str] = []
        coordinator = PresetRuntimeCoordinator(
            presets_feature=SimpleNamespace(),
            ui_state_store=None,
            get_launch_method=lambda: ZAPRET2_MODE,
            get_active_preset_path=lambda: fallback_calls.append("fallback") or "fallback.txt",
            get_preset_source_path_by_file_name=lambda method, file_name: path_calls.append(
                (method, file_name)
            )
            or f"C:/Zapret/Dev/presets/winws2/{file_name}",
            refresh_after_switch=lambda: None,
            request_selected_source_preset_apply=lambda *_args: True,
            request_preset_content_apply=lambda *_args: True,
        )
        coordinator._apply_active_preset_watch_path = lambda _path: None
        coordinator._active_preset_watch_runtime = SimpleNamespace(
            is_running=lambda: False,
            start_qthread_worker=Mock(side_effect=RuntimeError("worker failed")),
        )

        coordinator.handle_preset_switched(ZAPRET2_MODE, "Default v5.txt")

        self.assertEqual(path_calls, [])
        self.assertEqual(fallback_calls, [])
        self.assertEqual(
            coordinator._pending_active_preset_watch,
            PendingPresetWatch(ZAPRET2_MODE, "Default v5.txt"),
        )

    def test_rapid_preset_switches_coalesce_deferred_ui_work(self) -> None:
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator
        from settings.mode import ZAPRET2_MODE

        class _UiState:
            def __init__(self) -> None:
                self.active_revision = 0

            def bump_active_preset_revision(self) -> None:
                self.active_revision += 1

        ui_state = _UiState()
        watched_paths: list[str] = []
        switch_calls: list[tuple[str, str, str]] = []
        refresh_calls: list[str] = []
        coordinator = PresetRuntimeCoordinator(
            presets_feature=SimpleNamespace(),
            ui_state_store=ui_state,
            get_launch_method=lambda: ZAPRET2_MODE,
            get_active_preset_path=lambda: "C:/Zapret/Dev/presets/winws2/fallback.txt",
            get_preset_source_path_by_file_name=lambda _method, file_name: (
                f"C:/Zapret/Dev/presets/winws2/{file_name}"
            ),
            refresh_after_switch=lambda: refresh_calls.append("refresh"),
            request_selected_source_preset_apply=lambda method, reason, file_name: switch_calls.append(
                (method, reason, file_name)
            )
            or True,
            request_preset_content_apply=lambda *_args: True,
        )
        coordinator._apply_active_preset_watch_path = lambda path: watched_paths.append(path)

        coordinator.handle_preset_switched(ZAPRET2_MODE, "Default v1.txt")
        coordinator.handle_preset_switched(ZAPRET2_MODE, "Default v2.txt")
        coordinator.handle_preset_switched(ZAPRET2_MODE, "Default v3.txt")

        self.assertEqual(watched_paths, [])
        self.assertEqual(ui_state.active_revision, 0)

        self.assertTrue(self._process_events_until(lambda: bool(watched_paths)))

        self.assertEqual(watched_paths, ["C:/Zapret/Dev/presets/winws2/Default v3.txt"])
        self.assertEqual(ui_state.active_revision, 1)
        # Запросы уходят сразу; склеивает их switch pipeline (restart_flow).
        self.assertEqual(
            switch_calls,
            [
                (ZAPRET2_MODE, "preset_switched", "Default v1.txt"),
                (ZAPRET2_MODE, "preset_switched", "Default v2.txt"),
                (ZAPRET2_MODE, "preset_switched", "Default v3.txt"),
            ],
        )
        self.assertEqual(refresh_calls, [])
        self.assertEqual(coordinator._preset_switch_refresh_timer.interval(), 180)

    def test_get_selected_source_path_does_not_build_launch_snapshot(self) -> None:
        import inspect

        from presets import commands as preset_commands

        source = inspect.getsource(preset_commands.get_selected_source_path)

        self.assertNotIn("get_launch_snapshot", source)
        self.assertIn("preset_mode_coordinator.get_selected_source_path", source)

    def test_preset_switch_apply_is_requested_without_second_debounce(self) -> None:
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator
        from settings.mode import ZAPRET2_MODE

        switch_calls: list[tuple[str, str, str]] = []
        coordinator = PresetRuntimeCoordinator(
            presets_feature=SimpleNamespace(),
            ui_state_store=None,
            get_launch_method=lambda: ZAPRET2_MODE,
            get_active_preset_path=lambda: "",
            refresh_after_switch=lambda: None,
            request_selected_source_preset_apply=lambda method, reason, file_name: switch_calls.append(
                (method, reason, file_name)
            )
            or True,
            request_preset_content_apply=lambda *_args: True,
        )
        coordinator.setup_active_preset_file_watcher = lambda: None

        coordinator.handle_preset_switched(ZAPRET2_MODE, "Default v5.txt")

        # Раньше здесь стоял свой таймер на 500 мс поверх debounce switch
        # pipeline (700 мс): каждое переключение ждало ~1,2 с.
        self.assertEqual(switch_calls, [(ZAPRET2_MODE, "preset_switched", "Default v5.txt")])
        self.assertFalse(hasattr(coordinator, "_preset_switch_apply_timer"))

    def test_reapplying_same_preset_skips_ui_refresh_and_runtime_apply(self) -> None:
        from core.runtime.preset_runtime_coordinator import PresetRuntimeCoordinator
        from settings.mode import ZAPRET2_MODE

        switch_calls: list[tuple[str, str, str]] = []
        refresh_calls: list[str] = []

        ui_state = SimpleNamespace(
            active_revision=0,
            bump_active_preset_revision=lambda: setattr(
                ui_state,
                "active_revision",
                ui_state.active_revision + 1,
            ),
        )
        coordinator = PresetRuntimeCoordinator(
            presets_feature=SimpleNamespace(),
            ui_state_store=ui_state,
            get_launch_method=lambda: ZAPRET2_MODE,
            get_active_preset_path=lambda: "",
            refresh_after_switch=lambda: refresh_calls.append("refresh"),
            request_selected_source_preset_apply=lambda method, reason, file_name: switch_calls.append(
                (method, reason, file_name)
            )
            or True,
            request_preset_content_apply=lambda *_args: True,
        )
        coordinator.setup_active_preset_file_watcher = lambda: None

        coordinator.handle_preset_switched(ZAPRET2_MODE, "Default v5.txt")
        self._app.processEvents()
        coordinator.handle_preset_switched(ZAPRET2_MODE, "Default v5.txt")
        self._app.processEvents()

        self.assertEqual(refresh_calls, [])
        self.assertEqual(coordinator._preset_switch_refresh_timer.interval(), 180)
        coordinator._preset_switch_refresh_timer.timeout.emit()
        self.assertEqual(refresh_calls, ["refresh"])
        self.assertEqual(
            switch_calls,
            [
                (ZAPRET2_MODE, "preset_switched", "Default v5.txt"),
            ],
        )
        self.assertEqual(ui_state.active_revision, 1)

    def test_selecting_same_preset_file_does_not_emit_switch_signal(self) -> None:
        from presets.file_service import PresetFileService
        from presets.models import PresetManifest
        from settings.mode import ENGINE_WINWS2, ZAPRET2_MODE

        manifest = PresetManifest(
            file_name="Default v5.txt",
            name="Default v5",
            updated_at="",
            kind="user",
        )
        notified: list[str] = []

        class _PresetModeCoordinator:
            def get_selected_source_manifest(self, _launch_method):
                return manifest

            def select_preset_file_name(self, _launch_method, _file_name):
                return SimpleNamespace(preset_file_name=manifest.file_name)

        class _PresetFileStore:
            def resolve_file_name(self, _engine, file_name):
                return file_name

        class _PresetUiStore:
            def notify_preset_switched(self, file_name):
                notified.append(file_name)

        store = _PresetUiStore()
        service = PresetFileService(
            engine=ENGINE_WINWS2,
            launch_method=ZAPRET2_MODE,
            app_paths=object(),
            preset_mode_coordinator=_PresetModeCoordinator(),
            preset_file_store=_PresetFileStore(),
            preset_selection_service=object(),
            preset_store_winws2=store,
            preset_store_winws1=store,
        )

        service.select_file_name("Default v5.txt")

        self.assertEqual(notified, [])

    def test_raw_editor_can_save_active_preset_without_publishing_until_commit(self) -> None:
        from presets.raw_preset_editor_workflow import (
            publish_raw_preset_content_changed,
            save_raw_preset_text,
        )
        from settings.mode import ZAPRET2_MODE

        save_calls: list[tuple[str, str, str, bool, str]] = []
        publish_calls: list[tuple[str, str, str]] = []

        class _PresetsFeature:
            def save_preset_source_by_file_name(
                self,
                launch_method,
                file_name,
                source_text,
                *,
                publish_content_changed=True,
                content_change_kind="",
            ):
                save_calls.append(
                    (launch_method, file_name, source_text, publish_content_changed, content_change_kind)
                )
                return type("Manifest", (), {"name": "Default v5", "file_name": file_name})()

            def get_preset_source_path_by_file_name(self, _launch_method, file_name):
                from pathlib import Path

                return Path("C:/Zapret/Dev/presets/winws2") / file_name

            def publish_preset_content_changed(self, launch_method, file_name, *, content_change_kind=""):
                publish_calls.append((launch_method, file_name, content_change_kind))

            def read_preset_source_by_file_name(self, _launch_method, _file_name):
                return save_calls[-1][2]

        feature = _PresetsFeature()

        save_raw_preset_text(
            presets_feature=feature,
            launch_method=ZAPRET2_MODE,
            file_name="Default v5.txt",
            source_text="--new\n--filter-tcp=80\n",
            publish_content_changed=False,
        )
        publish_raw_preset_content_changed(
            presets_feature=feature,
            launch_method=ZAPRET2_MODE,
            file_name="Default v5.txt",
        )

        self.assertEqual(
            save_calls,
            [(ZAPRET2_MODE, "Default v5.txt", "--new\n--filter-tcp=80\n", False, "editor_save")],
        )
        self.assertEqual(publish_calls, [(ZAPRET2_MODE, "Default v5.txt", "editor_save")])

    def test_preset_content_apply_switches_running_preset_once(self) -> None:
        from pathlib import Path
        import tempfile
        from unittest.mock import Mock

        from winws_runtime.flow.apply_policy import (
            PRESET_CONTENT_APPLY_DEBOUNCE_MS,
            request_preset_runtime_content_apply,
        )
        from settings.mode import ZAPRET2_MODE

        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "selected.txt"
            preset_path.write_text(
                "--wf-tcp-out=80,443\n--filter-tcp=80\n--hostlist=list.txt\n--lua-desync=fake\n",
                encoding="utf-8",
            )

            launch_runtime = SimpleNamespace(
                is_running=Mock(return_value=True),
                switch_presets_async=Mock(),
                stop_dpi_async=Mock(),
            )
            presets_feature = SimpleNamespace(
                get_launch_snapshot=Mock(return_value=SimpleNamespace(preset_path=str(preset_path)))
            )
            runtime_feature = SimpleNamespace(
                objects=SimpleNamespace(launch_runtime=launch_runtime),
                dependencies=SimpleNamespace(presets_feature=presets_feature),
            )

            self.assertTrue(
                request_preset_runtime_content_apply(
                    runtime_feature=runtime_feature,
                    launch_method=ZAPRET2_MODE,
                    reason="preset_content_changed",
                )
            )

            launch_runtime.switch_presets_async.assert_called_once_with(
                ZAPRET2_MODE,
                delay_ms=PRESET_CONTENT_APPLY_DEBOUNCE_MS,
            )
            launch_runtime.stop_dpi_async.assert_not_called()

    def test_strategy_only_preset_content_apply_uses_longer_debounce(self) -> None:
        from pathlib import Path
        import tempfile
        from unittest.mock import Mock

        from winws_runtime.flow.apply_policy import (
            PRESET_CONTENT_APPLY_DEBOUNCE_MS,
            PRESET_STRATEGY_ONLY_APPLY_DEBOUNCE_MS,
            request_preset_runtime_content_apply,
        )
        from settings.mode import ZAPRET2_MODE

        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "selected.txt"
            preset_path.write_text(
                "--wf-tcp-out=80,443\n--filter-tcp=80\n--hostlist=list.txt\n--lua-desync=fake\n",
                encoding="utf-8",
            )

            launch_runtime = SimpleNamespace(
                is_running=Mock(return_value=True),
                switch_presets_async=Mock(),
                stop_dpi_async=Mock(),
            )
            presets_feature = SimpleNamespace(
                get_launch_snapshot=Mock(return_value=SimpleNamespace(preset_path=str(preset_path)))
            )
            runtime_feature = SimpleNamespace(
                objects=SimpleNamespace(launch_runtime=launch_runtime),
                dependencies=SimpleNamespace(presets_feature=presets_feature),
            )

            self.assertGreater(PRESET_STRATEGY_ONLY_APPLY_DEBOUNCE_MS, PRESET_CONTENT_APPLY_DEBOUNCE_MS)
            self.assertTrue(
                request_preset_runtime_content_apply(
                    runtime_feature=runtime_feature,
                    launch_method=ZAPRET2_MODE,
                    reason="strategy_only",
                )
            )

            launch_runtime.switch_presets_async.assert_called_once_with(
                ZAPRET2_MODE,
                delay_ms=PRESET_STRATEGY_ONLY_APPLY_DEBOUNCE_MS,
            )
            launch_runtime.stop_dpi_async.assert_not_called()

    def test_editor_save_preset_content_apply_uses_short_debounce(self) -> None:
        from unittest.mock import Mock

        from winws_runtime.flow.apply_policy import (
            PRESET_CONTENT_APPLY_DEBOUNCE_MS,
            PRESET_EDITOR_SAVE_APPLY_DEBOUNCE_MS,
            request_preset_runtime_content_apply,
        )
        from settings.mode import ZAPRET2_MODE

        launch_runtime = SimpleNamespace(
            is_running=Mock(return_value=True),
            switch_presets_async=Mock(),
            stop_dpi_async=Mock(),
        )
        runtime_feature = SimpleNamespace(
            objects=SimpleNamespace(launch_runtime=launch_runtime),
        )

        self.assertLess(PRESET_EDITOR_SAVE_APPLY_DEBOUNCE_MS, PRESET_CONTENT_APPLY_DEBOUNCE_MS)
        self.assertTrue(
            request_preset_runtime_content_apply(
                runtime_feature=runtime_feature,
                launch_method=ZAPRET2_MODE,
                reason="editor_save",
            )
        )

        launch_runtime.switch_presets_async.assert_called_once_with(
            ZAPRET2_MODE,
            delay_ms=PRESET_EDITOR_SAVE_APPLY_DEBOUNCE_MS,
        )
        launch_runtime.stop_dpi_async.assert_not_called()

    def test_selected_source_preset_apply_is_debounced_before_runtime_switch(self) -> None:
        from unittest.mock import Mock

        from settings.mode import ZAPRET2_MODE
        from winws_runtime.flow.preset_switch_policy import (
            SELECTED_SOURCE_PRESET_APPLY_DEBOUNCE_MS,
            request_selected_source_preset_apply,
        )

        launch_runtime = SimpleNamespace(
            is_running=Mock(return_value=True),
            switch_presets_async=Mock(),
            restart_dpi_async=Mock(),
        )
        runtime_feature = SimpleNamespace(
            objects=SimpleNamespace(launch_runtime=launch_runtime),
        )

        self.assertTrue(
            request_selected_source_preset_apply(
                runtime_feature=runtime_feature,
                launch_method=ZAPRET2_MODE,
                reason="preset_switched",
                preset_file_name="Default v5.txt",
            )
        )

        launch_runtime.switch_presets_async.assert_called_once_with(
            ZAPRET2_MODE,
            delay_ms=SELECTED_SOURCE_PRESET_APPLY_DEBOUNCE_MS,
        )
        launch_runtime.restart_dpi_async.assert_not_called()

    def test_preset_switch_during_dpi_start_is_forwarded_not_dropped(self) -> None:
        from unittest.mock import Mock

        from settings.mode import ZAPRET2_MODE
        from winws_runtime.flow.apply_policy import request_preset_runtime_content_apply
        from winws_runtime.flow.preset_switch_policy import request_selected_source_preset_apply

        def _runtime_feature(phase: str):
            launch_runtime = SimpleNamespace(
                is_running=Mock(return_value=False),
                switch_presets_async=Mock(),
                restart_dpi_async=Mock(),
            )
            return launch_runtime, SimpleNamespace(
                objects=SimpleNamespace(
                    launch_runtime=launch_runtime,
                    snapshot=lambda: SimpleNamespace(phase=phase, running=False),
                ),
            )

        # «Старт» нажат, worker уже прочитал прежний пресет: смена пресета и
        # правка стратегии обязаны дойти до switch pipeline, который дождётся
        # конца запуска, а не выброситься как «DPI не запущен».
        launch_runtime, runtime_feature = _runtime_feature("starting")
        self.assertTrue(
            request_selected_source_preset_apply(
                runtime_feature=runtime_feature,
                launch_method=ZAPRET2_MODE,
                reason="preset_switched",
                preset_file_name="B.txt",
            )
        )
        self.assertTrue(
            request_preset_runtime_content_apply(
                runtime_feature=runtime_feature,
                launch_method=ZAPRET2_MODE,
                reason="strategy_only",
            )
        )
        self.assertEqual(launch_runtime.switch_presets_async.call_count, 2)

        launch_runtime, runtime_feature = _runtime_feature("stopped")
        self.assertFalse(
            request_selected_source_preset_apply(
                runtime_feature=runtime_feature,
                launch_method=ZAPRET2_MODE,
                reason="preset_switched",
                preset_file_name="B.txt",
            )
        )
        launch_runtime.switch_presets_async.assert_not_called()

    def test_pending_preset_switch_waits_while_dpi_is_starting(self) -> None:
        from settings.mode import ZAPRET2_MODE
        from winws_runtime.runtime import restart_flow

        snapshot = SimpleNamespace(phase="starting", launch_method=ZAPRET2_MODE)
        owner = SimpleNamespace(
            _presets_switch_requested_generation=3,
            _presets_switch_completed_generation=2,
            _presets_switch_method=ZAPRET2_MODE,
            _presets_switch_thread=None,
            _dpi_start_thread=None,
            _dpi_stop_thread=None,
            is_running=lambda: False,
            _runtime_service=lambda: SimpleNamespace(snapshot=lambda: snapshot, set_busy=Mock()),
        )
        retries: list[object] = []

        with patch.object(restart_flow, "_schedule_pending_presets_switch_retry", retries.append):
            restart_flow.process_pending_presets_switch(owner)
        # Поколение не закрыто — после запуска переключение применится.
        self.assertEqual(retries, [owner])
        self.assertEqual(owner._presets_switch_completed_generation, 2)

        snapshot.phase = "stopped"
        with patch.object(restart_flow, "_schedule_pending_presets_switch_retry", retries.append):
            restart_flow.process_pending_presets_switch(owner)
        self.assertEqual(retries, [owner])
        self.assertEqual(owner._presets_switch_completed_generation, 3)

    def test_owner_redirect_happens_after_debounce(self) -> None:
        from settings.mode import ZAPRET2_MODE
        from winws_runtime.runtime import restart_flow

        owner = SimpleNamespace(
            _runtime_service=lambda: SimpleNamespace(snapshot=lambda: SimpleNamespace(launch_method=ZAPRET2_MODE)),
            restart_dpi_async=Mock(),
        )
        redirect = Mock(return_value=True)
        scheduled: list[tuple[str, int]] = []

        with patch.object(restart_flow, "_redirect_preset_switch_if_owner_differs", redirect), patch.object(
            restart_flow,
            "_schedule_debounced_presets_switch",
            lambda _owner, method, delay: scheduled.append((method, delay)),
        ):
            restart_flow.switch_presets_async(owner, ZAPRET2_MODE, delay_ms=700)

        # Быстрые щелчки при чужом владельце склеиваются, а не запускают
        # полный stop+start на каждом щелчке.
        self.assertEqual(scheduled, [(ZAPRET2_MODE, 700)])
        redirect.assert_not_called()

    def test_preset_content_apply_does_not_validate_preset_on_ui_request_path(self) -> None:
        from pathlib import Path
        import tempfile
        from unittest.mock import Mock

        from winws_runtime.flow.apply_policy import (
            PRESET_CONTENT_APPLY_DEBOUNCE_MS,
            request_preset_runtime_content_apply,
        )
        from settings.mode import ZAPRET2_MODE

        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "selected.txt"
            preset_path.write_text(
                "--wf-tcp-out=80,443\n--filter-tcp=80\n--hostlist=list.txt\n--lua-desync=fake\n",
                encoding="utf-8",
            )

            launch_runtime = SimpleNamespace(
                is_running=Mock(return_value=True),
                switch_presets_async=Mock(),
                stop_dpi_async=Mock(),
            )
            presets_feature = SimpleNamespace(
                get_launch_snapshot=Mock(return_value=SimpleNamespace(preset_path=str(preset_path)))
            )
            runtime_feature = SimpleNamespace(
                objects=SimpleNamespace(launch_runtime=launch_runtime),
                dependencies=SimpleNamespace(presets_feature=presets_feature),
            )

            with patch.object(
                Path,
                "read_text",
                side_effect=AssertionError("preset reading must run in the preset switch worker"),
            ):
                self.assertTrue(
                    request_preset_runtime_content_apply(
                        runtime_feature=runtime_feature,
                        launch_method=ZAPRET2_MODE,
                        reason="preset_content_changed",
                    )
                )

            launch_runtime.switch_presets_async.assert_called_once_with(
                ZAPRET2_MODE,
                delay_ms=PRESET_CONTENT_APPLY_DEBOUNCE_MS,
            )
            launch_runtime.stop_dpi_async.assert_not_called()

    def test_preset_content_publish_refreshes_launch_snapshot_before_signal(self) -> None:
        from presets.file_service import PresetFileService
        from presets.models import PresetManifest
        from settings.mode import ENGINE_WINWS2, ZAPRET2_MODE

        manifest = PresetManifest(
            file_name="Default v5.txt",
            name="Default v5",
            updated_at="",
        )
        launch_snapshot_refreshed = False
        events: list[tuple[str, bool]] = []

        class _PresetModeCoordinator:
            def get_selected_source_manifest(self, _launch_method):
                return manifest

            def refresh_selected_launch_preset(self, _launch_method):
                nonlocal launch_snapshot_refreshed
                launch_snapshot_refreshed = True
                events.append(("refresh", launch_snapshot_refreshed))
                return object()

        class _PresetFileStore:
            def get_manifest(self, _engine, _file_name):
                return manifest

            def resolve_file_name(self, _engine, file_name):
                return file_name

        class _PresetUiStore:
            def notify_preset_content_changed(self, _file_name):
                events.append(("notify", launch_snapshot_refreshed))

        store = _PresetUiStore()
        service = PresetFileService(
            engine=ENGINE_WINWS2,
            launch_method=ZAPRET2_MODE,
            app_paths=object(),
            preset_mode_coordinator=_PresetModeCoordinator(),
            preset_file_store=_PresetFileStore(),
            preset_selection_service=object(),
            preset_store_winws2=store,
            preset_store_winws1=store,
        )

        service.publish_preset_content_changed_by_file_name("Default v5.txt")

        self.assertEqual(events, [("refresh", True), ("notify", True)])

    def test_preset_content_publish_passes_change_kind_to_signal(self) -> None:
        from presets.file_service import PresetFileService
        from presets.models import PresetManifest
        from settings.mode import ENGINE_WINWS2, ZAPRET2_MODE

        manifest = PresetManifest(
            file_name="Default v5.txt",
            name="Default v5",
            updated_at="",
        )
        events: list[tuple[str, str]] = []

        class _PresetModeCoordinator:
            def get_selected_source_manifest(self, _launch_method):
                return manifest

            def refresh_selected_launch_preset(self, _launch_method):
                return object()

        class _PresetFileStore:
            def get_manifest(self, _engine, _file_name):
                return manifest

            def resolve_file_name(self, _engine, file_name):
                return file_name

        class _PresetUiStore:
            def notify_preset_content_changed(self, file_name, *, content_change_kind: str = ""):
                events.append((file_name, content_change_kind))

        store = _PresetUiStore()
        service = PresetFileService(
            engine=ENGINE_WINWS2,
            launch_method=ZAPRET2_MODE,
            app_paths=object(),
            preset_mode_coordinator=_PresetModeCoordinator(),
            preset_file_store=_PresetFileStore(),
            preset_selection_service=object(),
            preset_store_winws2=store,
            preset_store_winws1=store,
        )

        service.publish_preset_content_changed_by_file_name(
            "Default v5.txt",
            content_change_kind="strategy_only",
        )

        self.assertEqual(events, [("Default v5.txt", "strategy_only")])

    def test_reset_selected_preset_refreshes_launch_snapshot_before_signal(self) -> None:
        from pathlib import Path
        import tempfile

        from presets.models import PresetManifest
        from presets.preset_file_ops import reset_to_builtin_by_file_name
        from settings.mode import ENGINE_WINWS2

        manifest = PresetManifest(
            file_name="Default v5.txt",
            name="Default v5",
            updated_at="",
            kind="user",
        )
        updated = PresetManifest(
            file_name="Default v5.txt",
            name="Default v5",
            updated_at="",
            kind="builtin",
        )
        launch_snapshot_refreshed = False
        deleted = False
        events: list[tuple[str, bool]] = []

        class _EnginePaths:
            def __init__(self, builtin_dir: Path) -> None:
                self.builtin_presets_dir = builtin_dir

            def ensure_directories(self):
                return self

        class _AppPaths:
            def __init__(self, builtin_dir: Path) -> None:
                self._builtin_dir = builtin_dir

            def engine_paths(self, _engine):
                return _EnginePaths(self._builtin_dir)

        class _PresetFileStore:
            def delete_preset(self, _engine, _file_name):
                nonlocal deleted
                deleted = True

        class _Backend:
            engine = ENGINE_WINWS2
            preset_file_store = _PresetFileStore()

            def __init__(self, builtin_dir: Path) -> None:
                self.app_paths = _AppPaths(builtin_dir)

            def get_manifest_by_file_name(self, _file_name):
                return updated if deleted else manifest

            def is_selected_file_name(self, _file_name):
                return True

            def _refresh_selected_source_preset(self):
                nonlocal launch_snapshot_refreshed
                launch_snapshot_refreshed = True
                events.append(("refresh", launch_snapshot_refreshed))

            def notify_preset_content_changed(self, _file_name):
                events.append(("notify", launch_snapshot_refreshed))

        with tempfile.TemporaryDirectory() as tmp_dir:
            builtin_dir = Path(tmp_dir)
            (builtin_dir / manifest.file_name).write_text("--new\n", encoding="utf-8")

            reset_to_builtin_by_file_name(_Backend(builtin_dir), manifest.file_name)

        self.assertEqual(events, [("refresh", True), ("notify", True)])


if __name__ == "__main__":
    unittest.main()
