"""Замена копий встроенных пресетов, отставших по номеру версии.

Пункт 7 договора presets.preset_contract: когда пользователь меняет встроенный
пресет, его правка сохраняется копией в папке пользователя. Если после
обновления программы у встроенного пресета вырос номер # BuiltinVersion:,
копия заменяется им — всегда. Прежняя копия сохраняется в presets/replaced.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.paths import AppPaths
from presets.file_service import BuiltinOverrideRefreshResult, PresetFileService
from presets.file_store import PresetFileStore
from presets.preset_contract import (
    BUILTIN_PRESET_UPDATES,
    OUTDATED_BUILTIN_OVERRIDE_REFRESH,
    builtin_preset_version,
    normalize_preset_source_for_save,
)
from settings.mode import ENGINE_WINWS2, ZAPRET1_MODE, ZAPRET2_MODE


def _preset(name: str, *, version: str | None, profile_name: str, strategy: str = "fake:blob=fake_default_tls") -> str:
    header = [f"# Preset: {name}"]
    if version is not None:
        header.append(f"# BuiltinVersion: {version}")
    text = "\n".join(
        [
            *header,
            "--wf-tcp-out=443",
            "--new",
            f"--name={profile_name}",
            "--filter-tcp=443",
            "--hostlist=lists/googlevideo.txt",
            f"--lua-desync={strategy}",
            "",
        ]
    )
    return normalize_preset_source_for_save(text, ENGINE_WINWS2)


class _UiStore:
    def __init__(self) -> None:
        self.notify_preset_content_changed = Mock()
        self.notify_presets_changed = Mock()
        self.notify_preset_switched = Mock()
        self.notify_preset_identity_changed = Mock()


class BuiltinPresetVersionTests(unittest.TestCase):
    def test_version_is_read_from_the_header(self) -> None:
        self.assertEqual(builtin_preset_version("# Preset: X\n# BuiltinVersion: 2.44\n--wf-tcp-out=443\n"), (2, 44))
        self.assertEqual(builtin_preset_version("# BuiltinVersion: 1.4\n"), (1, 4))
        # 2.9 меньше 2.10: номера сравниваются как числа, а не как текст.
        self.assertLess(builtin_preset_version("# BuiltinVersion: 2.9"), builtin_preset_version("# BuiltinVersion: 2.10"))

    def test_no_version_when_header_has_none(self) -> None:
        self.assertIsNone(builtin_preset_version("# Preset: X\n--wf-tcp-out=443\n"))
        self.assertIsNone(builtin_preset_version(""))
        self.assertIsNone(builtin_preset_version("# BuiltinVersion: скоро\n"))
        # Строка с номером после параметров запуска — уже не шапка.
        self.assertIsNone(builtin_preset_version("--wf-tcp-out=443\n# BuiltinVersion: 2.44\n"))

    def test_contract_lists_the_update(self) -> None:
        self.assertIn(OUTDATED_BUILTIN_OVERRIDE_REFRESH, BUILTIN_PRESET_UPDATES)


class BuiltinOverrideRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.paths = AppPaths(user_root=self.root, local_root=self.root)
        self.store = PresetFileStore(self.paths)
        self.engine_paths = self.paths.engine_paths(ENGINE_WINWS2).ensure_directories()
        self.user_dir = self.engine_paths.user_presets_dir
        self.builtin_dir = self.engine_paths.builtin_presets_dir
        self.selected = ""
        self.refresh_selected = Mock()
        self.ui_store = _UiStore()
        self.bindings: dict[str, dict] = {}
        bindings = patch(
            "presets.remote_bindings.get_remote_preset_binding",
            side_effect=lambda _engine, file_name: self.bindings.get(file_name),
        )
        bindings.start()
        self.addCleanup(bindings.stop)

    def _service(self) -> PresetFileService:
        store = self.store
        test = self

        class _Coordinator:
            def get_selected_source_manifest(self, _launch_method):
                return store.get_manifest(ENGINE_WINWS2, test.selected) if test.selected else None

            def refresh_selected_launch_preset(self, launch_method):
                test.refresh_selected(launch_method)

        return PresetFileService(
            engine=ENGINE_WINWS2,
            launch_method=ZAPRET2_MODE,
            app_paths=self.paths,
            preset_mode_coordinator=_Coordinator(),
            preset_file_store=self.store,
            preset_selection_service=SimpleNamespace(),
            preset_store_winws2=self.ui_store,
            preset_store_winws1=self.ui_store,
        )

    def _write(self, directory: Path, file_name: str, text: str) -> Path:
        path = directory / file_name
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def _builtin(self, version: str | None = "2.44") -> str:
        text = _preset("Default", version=version, profile_name="YouTube · видео (googlevideo.com)")
        self._write(self.builtin_dir, "Default.txt", text)
        return text

    def _user_copy(self, version: str | None = "2.43") -> str:
        # Копия пользователя: прежнее имя профиля и своя стратегия.
        text = _preset(
            "Default",
            version=version,
            profile_name="googlevideo.com (CDN сервера)",
            strategy="multisplit:pos=2",
        )
        self._write(self.user_dir, "Default.txt", text)
        return text

    def test_copy_with_lower_version_is_replaced_by_the_builtin_preset(self) -> None:
        builtin_text = self._builtin("2.44")
        user_text = self._user_copy("2.43")
        service = self._service()

        result = service.refresh_outdated_builtin_overrides()

        self.assertEqual(result.refreshed, (("Default.txt", "2.43", "2.44"),))
        self.assertEqual(result.failed, ())
        self.assertFalse((self.user_dir / "Default.txt").exists())
        self.assertEqual(service.read_source_text_by_file_name("Default.txt"), builtin_text)
        # Прежняя копия пользователя не потеряна.
        backup = self.engine_paths.replaced_presets_dir / "Default.txt"
        self.assertEqual(backup.read_text(encoding="utf-8"), user_text)
        self.assertEqual(self.engine_paths.replaced_presets_dir, self.root / "presets" / "replaced" / self.user_dir.name)

    def test_copy_without_version_is_replaced_too(self) -> None:
        self._builtin("2.44")
        self._user_copy(None)

        result = self._service().refresh_outdated_builtin_overrides()

        self.assertEqual(result.refreshed, (("Default.txt", "", "2.44"),))
        self.assertFalse((self.user_dir / "Default.txt").exists())

    def test_copy_with_the_same_or_newer_version_is_kept(self) -> None:
        self._builtin("2.44")
        for version in ("2.44", "2.45", "3.0"):
            with self.subTest(version=version):
                user_text = self._user_copy(version)

                result = self._service().refresh_outdated_builtin_overrides()

                self.assertEqual(result, BuiltinOverrideRefreshResult())
                self.assertEqual((self.user_dir / "Default.txt").read_text(encoding="utf-8"), user_text)
        self.assertFalse(self.engine_paths.replaced_presets_dir.exists())

    def test_builtin_preset_without_version_never_replaces_the_copy(self) -> None:
        self._builtin(None)
        user_text = self._user_copy(None)

        result = self._service().refresh_outdated_builtin_overrides()

        self.assertEqual(result, BuiltinOverrideRefreshResult())
        self.assertEqual((self.user_dir / "Default.txt").read_text(encoding="utf-8"), user_text)

    def test_user_presets_with_other_file_names_are_not_touched(self) -> None:
        self._builtin("2.44")
        own_text = _preset("Мой", version="1.0", profile_name="googlevideo.com (CDN сервера)")
        own = self._write(self.user_dir, "Мой.txt", own_text)

        result = self._service().refresh_outdated_builtin_overrides()

        self.assertEqual(result, BuiltinOverrideRefreshResult())
        self.assertEqual(own.read_text(encoding="utf-8"), own_text)

    def test_copy_kept_in_sync_from_a_link_is_not_replaced(self) -> None:
        self._builtin("2.44")
        user_text = self._user_copy("2.43")
        self.bindings["Default.txt"] = {"url": "https://example.org/preset.txt", "auto": True}

        result = self._service().refresh_outdated_builtin_overrides()

        self.assertEqual(result, BuiltinOverrideRefreshResult())
        self.assertEqual((self.user_dir / "Default.txt").read_text(encoding="utf-8"), user_text)

        # Отвязанная ссылка (auto=False) копией больше не управляет.
        self.bindings["Default.txt"] = {"url": "https://example.org/preset.txt", "auto": False}
        result = self._service().refresh_outdated_builtin_overrides()
        self.assertEqual(result.refreshed, (("Default.txt", "2.43", "2.44"),))

    def test_program_learns_about_the_change_of_the_selected_preset(self) -> None:
        self._builtin("2.44")
        self._user_copy("2.43")
        self.selected = "Default.txt"

        self._service().refresh_outdated_builtin_overrides()

        self.refresh_selected.assert_called_once_with(ZAPRET2_MODE)
        self.ui_store.notify_preset_content_changed.assert_called_once()

    def test_second_run_changes_nothing_and_keeps_the_saved_copy(self) -> None:
        self._builtin("2.44")
        user_text = self._user_copy("2.43")
        service = self._service()
        service.refresh_outdated_builtin_overrides()

        result = service.refresh_outdated_builtin_overrides()

        self.assertEqual(result, BuiltinOverrideRefreshResult())
        backup = self.engine_paths.replaced_presets_dir / "Default.txt"
        self.assertEqual(backup.read_text(encoding="utf-8"), user_text)

    def test_saved_copies_are_not_listed_as_presets(self) -> None:
        self._builtin("2.44")
        self._user_copy("2.43")
        service = self._service()

        service.refresh_outdated_builtin_overrides()

        self.assertEqual([manifest.file_name for manifest in service.list_manifests()], ["Default.txt"])

    def test_broken_copy_is_reported_and_others_are_still_replaced(self) -> None:
        self._builtin("2.44")
        self._user_copy("2.43")
        other_builtin = _preset("Other", version="2.50", profile_name="Telegram")
        self._write(self.builtin_dir, "Other.txt", other_builtin)
        self._write(self.user_dir, "Other.txt", _preset("Other", version="2.42", profile_name="Telegram"))
        service = self._service()
        real_reset = PresetFileService.reset_to_builtin_by_file_name

        def _reset(instance, file_name):
            if file_name == "Default.txt":
                raise PermissionError("файл занят")
            return real_reset(instance, file_name)

        with patch.object(PresetFileService, "reset_to_builtin_by_file_name", _reset):
            result = service.refresh_outdated_builtin_overrides()

        self.assertEqual(result.refreshed, (("Other.txt", "2.42", "2.50"),))
        self.assertEqual(result.failed, (("Default.txt", "файл занят"),))
        self.assertTrue((self.user_dir / "Default.txt").exists())


class BuiltinOverrideRefreshStartupTests(unittest.TestCase):
    def _install(self, presets_feature, notify=None):
        from main import post_startup_builtin_preset_refresh as module

        gate_calls = []
        enqueued = []
        patches = [
            patch.object(
                module,
                "bind_startup_gate",
                side_effect=lambda _signal, callback, *, is_ready: gate_calls.append(callback),
            ),
            patch.object(
                module,
                "enqueue_subsystem_task",
                side_effect=lambda subsystem, name, task: enqueued.append((subsystem, name, task)),
            ),
            patch.object(module, "is_startup_host_alive", return_value=True),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        startup_host = SimpleNamespace(
            startup_interactive_ready=object(),
            startup_state=SimpleNamespace(interactive_logged=True),
        )
        module.install_builtin_preset_override_refresh(
            startup_host,
            presets_feature=presets_feature,
            log_startup_metric=Mock(),
            notify=notify,
        )
        return module, gate_calls, enqueued

    def test_runs_in_presets_queue_for_both_engines_and_logs_each_file(self) -> None:
        results = {
            ZAPRET2_MODE: BuiltinOverrideRefreshResult(
                refreshed=(("Default.txt", "2.43", "2.44"), ("Old.txt", "", "2.50")),
                failed=(("Busy.txt", "файл занят"),),
            ),
            ZAPRET1_MODE: BuiltinOverrideRefreshResult(),
        }
        feature = SimpleNamespace(refresh_outdated_builtin_overrides=Mock(side_effect=lambda method: results[method]))
        module, gate_calls, enqueued = self._install(feature)
        self.assertEqual(len(gate_calls), 1)
        self.assertEqual(enqueued, [])

        gate_calls[0]()
        self.assertEqual([subsystem for subsystem, _name, _task in enqueued], ["presets"])

        with patch.object(module, "log") as log:
            enqueued[0][2]()

        self.assertEqual(
            [call.args[0] for call in feature.refresh_outdated_builtin_overrides.call_args_list],
            [ZAPRET2_MODE, ZAPRET1_MODE],
        )
        messages = [call.args[0] for call in log.call_args_list]
        self.assertEqual(len(messages), 3)
        self.assertIn("«Default.txt» заменён встроенным версии 2.44", messages[0])
        self.assertIn("была 2.43", messages[0])
        self.assertIn("presets/replaced", messages[0])
        self.assertIn("без номера", messages[1])
        self.assertIn("«Busy.txt»", messages[2])
        self.assertEqual(log.call_args_list[2].args[1], "WARNING")

    def test_user_is_told_that_presets_were_replaced(self) -> None:
        results = {
            ZAPRET2_MODE: BuiltinOverrideRefreshResult(refreshed=(("Default v1 (game filter).txt", "2.43", "2.44"),)),
            ZAPRET1_MODE: BuiltinOverrideRefreshResult(),
        }
        feature = SimpleNamespace(refresh_outdated_builtin_overrides=Mock(side_effect=lambda method: results[method]))
        notify = Mock()
        module, gate_calls, enqueued = self._install(feature, notify)
        gate_calls[0]()

        with patch.object(module, "log"):
            enqueued[0][2]()

        notify.assert_called_once()
        payload = notify.call_args.args[0]
        self.assertEqual(payload["title"], "Встроенные пресеты обновлены")
        self.assertIn("«Default v1 (game filter)» заменён новой версией", payload["content"])
        self.assertIn("presets\\replaced", payload["content"])

    def test_several_replaced_presets_give_one_notice(self) -> None:
        results = {
            ZAPRET2_MODE: BuiltinOverrideRefreshResult(refreshed=(("A.txt", "2.43", "2.44"), ("B.txt", "", "2.44"))),
            ZAPRET1_MODE: BuiltinOverrideRefreshResult(refreshed=(("C.txt", "1.0", "1.1"),)),
        }
        feature = SimpleNamespace(refresh_outdated_builtin_overrides=Mock(side_effect=lambda method: results[method]))
        notify = Mock()
        module, gate_calls, enqueued = self._install(feature, notify)
        gate_calls[0]()

        with patch.object(module, "log"):
            enqueued[0][2]()

        notify.assert_called_once()
        self.assertIn("Пресетов заменено новыми версиями встроенных: 3.", notify.call_args.args[0]["content"])

    def test_nothing_replaced_means_no_notice(self) -> None:
        feature = SimpleNamespace(
            refresh_outdated_builtin_overrides=Mock(return_value=BuiltinOverrideRefreshResult())
        )
        notify = Mock()
        _module, gate_calls, enqueued = self._install(feature, notify)
        gate_calls[0]()

        enqueued[0][2]()

        notify.assert_not_called()

    def test_failure_of_one_engine_does_not_stop_the_other(self) -> None:
        def _refresh(method):
            if method == ZAPRET2_MODE:
                raise RuntimeError("нет папки")
            return BuiltinOverrideRefreshResult(refreshed=(("A.txt", "1.0", "1.1"),))

        feature = SimpleNamespace(refresh_outdated_builtin_overrides=Mock(side_effect=_refresh))
        module, gate_calls, enqueued = self._install(feature)
        gate_calls[0]()

        with patch.object(module, "log") as log:
            enqueued[0][2]()

        self.assertEqual(feature.refresh_outdated_builtin_overrides.call_count, 2)
        self.assertEqual([call.args[1] for call in log.call_args_list], ["WARNING", "INFO"])

    def test_refresh_is_not_started_at_program_startup_for_now(self) -> None:
        """В 21.1.7.19 замена шла сразу после запуска программы и подвесила окно:
        она пришлась на запуск обхода. Пока причина не устранена, задача не ставится."""
        import inspect

        from main import post_startup

        source = inspect.getsource(post_startup.install_post_startup_tasks)
        calls = [line for line in source.splitlines() if "install_builtin_preset_override_refresh(" in line]
        self.assertEqual([line for line in calls if not line.strip().startswith("#")], [])


if __name__ == "__main__":
    unittest.main()
