"""Разовый перевод пресетов пользователя в обязательный формат winws2.

Пункт 6 договора presets.preset_contract: после запуска каждый пресет winws2
из папки пользователя проходит нормализацию сохранения и записывается, только
если текст изменился. Встроенные пресеты не трогаются, сломанный файл
пропускается с записью в лог, повторный проход ничего не пишет.
"""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.paths import AppPaths
from presets.file_service import PresetContractMigrationResult, PresetFileService
from presets.file_store import PresetFileStore
from presets.preset_contract import (
    CONTRACT_MIGRATION_CHANGE_KIND,
    ONE_TIME_MIGRATIONS,
    USER_WINWS2_PRESETS_SAVE_FORMAT_MIGRATION,
    normalize_preset_source_for_save,
)
from profile.winws2_preset_source import WINWS2_LUA_INIT_LINES
from settings.mode import ENGINE_WINWS2, ZAPRET2_MODE


# Старый пресет: в блоке только два файла из восьми.
OLD_PRESET = (
    "# Preset: Old\n"
    "--lua-init=@lua/zapret-lib.lua\n"
    "--lua-init=@lua/zapret-antidpi.lua\n"
    "--wf-tcp-out=443\n"
    "--new\n"
    "--name=youtube\n"
    "--filter-tcp=443\n"
    "--lua-desync=fake:blob=fake_default_tls\n"
)


def _full_preset(name: str) -> str:
    return normalize_preset_source_for_save(OLD_PRESET.replace("# Preset: Old", f"# Preset: {name}"), ENGINE_WINWS2)


class _UiStore:
    def __init__(self) -> None:
        self.notify_preset_content_changed = Mock()
        self.notify_presets_changed = Mock()
        self.notify_preset_switched = Mock()
        self.notify_preset_identity_changed = Mock()


class UserPresetContractMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.paths = AppPaths(user_root=self.root, local_root=self.root)
        self.store = PresetFileStore(self.paths)
        engine_paths = self.paths.engine_paths(ENGINE_WINWS2).ensure_directories()
        self.user_dir = engine_paths.user_presets_dir
        self.builtin_dir = engine_paths.builtin_presets_dir
        self.selected = ""
        self.refresh_selected = Mock()
        self.ui_store = _UiStore()
        # Привязки к источникам живут в settings.sqlite3: в тестах без привязок
        # настоящую базу не читаем.
        no_bindings = patch("presets.remote_bindings.get_remote_preset_binding", return_value=None)
        no_bindings.start()
        self.addCleanup(no_bindings.stop)

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

    def _write_user(self, file_name: str, text: str) -> Path:
        path = self.user_dir / file_name
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def test_contract_lists_the_migration(self) -> None:
        self.assertIn(USER_WINWS2_PRESETS_SAVE_FORMAT_MIGRATION, ONE_TIME_MIGRATIONS)

    def test_user_preset_without_full_block_gets_it_once(self) -> None:
        path = self._write_user("Old.txt", OLD_PRESET)

        result = self._service().migrate_user_presets_to_save_contract()

        self.assertEqual(result.migrated, ("Old.txt",))
        self.assertEqual(result.failed, ())
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text, normalize_preset_source_for_save(OLD_PRESET, ENGINE_WINWS2))
        lines = text.split("\n")
        self.assertEqual(lines[1 : 1 + len(WINWS2_LUA_INIT_LINES)], list(WINWS2_LUA_INIT_LINES))
        for lua_line in WINWS2_LUA_INIT_LINES:
            self.assertEqual(lines.count(lua_line), 1)
        # Остальные строки пресета на месте и в том же порядке.
        self.assertIn("--name=youtube\n--filter-tcp=443\n--lua-desync=fake:blob=fake_default_tls\n", text)

    def test_second_run_writes_nothing(self) -> None:
        self._write_user("Old.txt", OLD_PRESET)
        self._write_user("Fine.txt", _full_preset("Fine"))
        service = self._service()
        service.migrate_user_presets_to_save_contract()

        with patch.object(self.store, "update_preset", wraps=self.store.update_preset) as update:
            result = service.migrate_user_presets_to_save_contract()

        self.assertEqual(result, PresetContractMigrationResult())
        update.assert_not_called()

    def test_preset_without_final_newline_is_not_migrated_every_run(self) -> None:
        # Хранилище само добавляет перевод строки в конце: такой файл не считается
        # переведённым, иначе он попадал бы в лог при каждом запуске.
        self._write_user("NoNewline.txt", _full_preset("NoNewline").rstrip("\n"))

        result = self._service().migrate_user_presets_to_save_contract()

        self.assertEqual(result.migrated, ())

    def test_builtin_presets_are_untouched(self) -> None:
        builtin_path = self.builtin_dir / "Builtin.txt"
        builtin_path.write_text(OLD_PRESET.replace("Old", "Builtin"), encoding="utf-8", newline="\n")
        before = builtin_path.read_bytes()

        result = self._service().migrate_user_presets_to_save_contract()

        self.assertEqual(result.migrated, ())
        self.assertEqual(builtin_path.read_bytes(), before)
        self.assertFalse((self.user_dir / "Builtin.txt").exists())

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root пишет и в файл только для чтения")
    def test_broken_file_is_skipped_and_others_still_migrate(self) -> None:
        broken = self._write_user("Broken.txt", OLD_PRESET.replace("Old", "Broken"))
        broken_before = broken.read_bytes()
        broken.chmod(stat.S_IRUSR)
        self.addCleanup(broken.chmod, stat.S_IRUSR | stat.S_IWUSR)
        self._write_user("Old.txt", OLD_PRESET)

        result = self._service().migrate_user_presets_to_save_contract()

        self.assertEqual(result.migrated, ("Old.txt",))
        self.assertEqual([name for name, _error in result.failed], ["Broken.txt"])
        self.assertEqual(broken.read_bytes(), broken_before)

    def test_active_preset_with_new_launch_args_is_published_once(self) -> None:
        self._write_user("Old.txt", OLD_PRESET)
        self.selected = "Old.txt"

        self._service().migrate_user_presets_to_save_contract()

        self.refresh_selected.assert_called_once_with(ZAPRET2_MODE)
        self.ui_store.notify_preset_content_changed.assert_called_once_with(
            "Old.txt",
            content_change_kind=CONTRACT_MIGRATION_CHANGE_KIND,
        )

    def test_header_only_change_is_written_without_publish(self) -> None:
        # Служебные строки шапки не попадают в запуск: переписать файл нужно,
        # а перезапускать winws2 — нет.
        full = _full_preset("Headers")
        path = self._write_user("Headers.txt", full.replace("# Preset: Headers\n", "# Preset: Headers\n# Modified: 2024-01-01\n"))
        self.selected = "Headers.txt"

        result = self._service().migrate_user_presets_to_save_contract()

        self.assertEqual(result.migrated, ("Headers.txt",))
        self.assertEqual(path.read_text(encoding="utf-8"), full)
        self.refresh_selected.assert_not_called()
        self.ui_store.notify_preset_content_changed.assert_not_called()

    def test_linked_preset_stays_attached_to_its_source(self) -> None:
        # Разовый перевод — не правка пользователя: следующий автосинк с тем же
        # источником не должен отвязывать пресет.
        from presets import remote_bindings
        from presets.remote_sync import (
            STATUS_NOT_MODIFIED,
            RemoteFetchResult,
            comparison_hash,
            sync_remote_preset,
        )

        path = self._write_user("Linked.txt", OLD_PRESET.replace("Old", "Linked"))
        bindings = {
            "Linked.txt": {
                "url": "https://example.com/linked.txt",
                "auto": True,
                "detached": False,
                "synced_hash": comparison_hash(path.read_text(encoding="utf-8")),
            }
        }

        def _update(_scope, file_name, **fields):
            bindings[file_name].update(fields)
            return bindings[file_name]

        with patch.object(remote_bindings, "get_remote_preset_binding", side_effect=lambda _scope, name: bindings.get(name)), \
                patch.object(remote_bindings, "update_remote_preset_binding", side_effect=_update):
            result = self._service().migrate_user_presets_to_save_contract()

        self.assertEqual(result.migrated, ("Linked.txt",))
        outcome = sync_remote_preset(
            bindings["Linked.txt"],
            engine=ENGINE_WINWS2,
            read_current_text=lambda: path.read_text(encoding="utf-8"),
            fetch=lambda _url, _etag, _last_modified: RemoteFetchResult(status_code=304),
            save_text=lambda _text: None,
            now_iso="2026-01-01T00:00:00Z",
        )
        self.assertEqual(outcome.status, STATUS_NOT_MODIFIED)


class UserPresetContractMigrationStartupTests(unittest.TestCase):
    def _install(self, presets_feature):
        from main import post_startup_preset_contract_migration as module

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
        module.install_user_preset_contract_migration(
            startup_host,
            presets_feature=presets_feature,
            log_startup_metric=Mock(),
        )
        return module, gate_calls, enqueued

    def test_runs_once_in_presets_queue_after_interactive_and_logs_each_file(self) -> None:
        feature = SimpleNamespace(
            migrate_user_presets_to_save_contract=Mock(
                return_value=PresetContractMigrationResult(
                    migrated=("A.txt", "B.txt"),
                    failed=(("C.txt", "Permission denied"),),
                )
            )
        )
        module, gate_calls, enqueued = self._install(feature)
        self.assertEqual(len(gate_calls), 1)
        self.assertEqual(enqueued, [])

        gate_calls[0]()
        self.assertEqual([subsystem for subsystem, _name, _task in enqueued], ["presets"])

        with patch.object(module, "log") as log:
            enqueued[0][2]()

        feature.migrate_user_presets_to_save_contract.assert_called_once_with(ZAPRET2_MODE)
        messages = [call.args[0] for call in log.call_args_list]
        self.assertEqual(len(messages), 3)
        self.assertIn("«A.txt»", messages[0])
        self.assertIn("«B.txt»", messages[1])
        self.assertIn("«C.txt»", messages[2])
        self.assertEqual(log.call_args_list[2].args[1], "WARNING")


if __name__ == "__main__":
    unittest.main()
