from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from presets.models import PresetManifest
from presets.preset_file_ops import reset_all_to_builtin, reset_to_builtin_by_file_name
from settings.mode import ENGINE_WINWS2


class _EnginePaths:
    def __init__(self, root: Path) -> None:
        self.user_presets_dir = root / "presets" / "winws2"
        self.builtin_presets_dir = root / "presets" / "winws2_builtin"

    def ensure_directories(self):
        self.user_presets_dir.mkdir(parents=True, exist_ok=True)
        self.builtin_presets_dir.mkdir(parents=True, exist_ok=True)
        return self


class _AppPaths:
    def __init__(self, root: Path) -> None:
        self._engine_paths = _EnginePaths(root)

    def engine_paths(self, _engine: str) -> _EnginePaths:
        return self._engine_paths


class _PresetFileStore:
    def __init__(self, engine_paths: _EnginePaths) -> None:
        self._engine_paths = engine_paths

    def delete_preset(self, _engine: str, file_name: str) -> None:
        (self._engine_paths.user_presets_dir / file_name).unlink()


class _Backend:
    engine = ENGINE_WINWS2

    def __init__(self, root: Path) -> None:
        self.app_paths = _AppPaths(root)
        engine_paths = self.app_paths.engine_paths(self.engine).ensure_directories()
        self.preset_file_store = _PresetFileStore(engine_paths)
        self.deleted_remote_identities: list[str] = []
        self.content_notifications: list[str] = []
        self.structure_notifications = 0

    def get_manifest_by_file_name(self, file_name: str) -> PresetManifest | None:
        engine_paths = self.app_paths.engine_paths(self.engine).ensure_directories()
        user_path = engine_paths.user_presets_dir / file_name
        builtin_path = engine_paths.builtin_presets_dir / file_name
        if user_path.is_file():
            return PresetManifest(
                file_name=file_name,
                name=Path(file_name).stem,
                updated_at="",
                kind="user",
                storage_scope="user",
            )
        if builtin_path.is_file():
            return PresetManifest(
                file_name=file_name,
                name=Path(file_name).stem,
                updated_at="",
                kind="builtin",
                storage_scope="builtin",
            )
        return None

    def is_selected_file_name(self, _file_name: str) -> bool:
        return False

    def _delete_remote_binding_meta(self, file_name: str) -> None:
        self.deleted_remote_identities.append(str(file_name))

    def notify_preset_content_changed(self, file_name: str) -> None:
        self.content_notifications.append(str(file_name))

    def notify_presets_changed(self) -> None:
        self.structure_notifications += 1

    def get_selected_file_name(self) -> str:
        return ""

    def _refresh_selected_source_preset(self) -> None:
        raise AssertionError("selected preset is not used in these tests")


class PresetResetRemoteBindingTests(unittest.TestCase):
    def _write_preset(self, path: Path, text: str = "--new\n") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_single_reset_removes_active_identity(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backend = _Backend(root)
            engine_paths = backend.app_paths.engine_paths(ENGINE_WINWS2).ensure_directories()
            self._write_preset(engine_paths.user_presets_dir / "A.txt", "old\n")
            self._write_preset(engine_paths.builtin_presets_dir / "A.txt", "new\n")

            with patch(
                "presets.remote_bindings.get_remote_preset_binding",
                return_value={"url": "https://example.com/a.txt", "auto": True},
            ):
                reset_to_builtin_by_file_name(backend, "A.txt")

            self.assertFalse((engine_paths.user_presets_dir / "A.txt").exists())
            self.assertEqual(backend.deleted_remote_identities, ["A.txt"])

    def test_single_reset_keeps_remembered_paused_url(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backend = _Backend(root)
            engine_paths = backend.app_paths.engine_paths(ENGINE_WINWS2).ensure_directories()
            self._write_preset(engine_paths.user_presets_dir / "A.txt", "old\n")
            self._write_preset(engine_paths.builtin_presets_dir / "A.txt", "new\n")

            with patch(
                "presets.remote_bindings.get_remote_preset_binding",
                return_value={"url": "https://example.com/a.txt", "auto": False},
            ):
                reset_to_builtin_by_file_name(backend, "A.txt")

            self.assertEqual(backend.deleted_remote_identities, [])

    def test_bulk_reset_removes_active_identity_only_for_removed_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backend = _Backend(root)
            engine_paths = backend.app_paths.engine_paths(ENGINE_WINWS2).ensure_directories()
            self._write_preset(engine_paths.builtin_presets_dir / "A.txt")
            self._write_preset(engine_paths.builtin_presets_dir / "B.txt")
            self._write_preset(engine_paths.user_presets_dir / "A.txt", "custom\n")
            # A directory with a preset suffix cannot be unlinked by the reset
            # helper; its binding must remain untouched because removal failed.
            (engine_paths.user_presets_dir / "B.txt").mkdir()

            def binding_for(_scope: str, file_name: str):
                return {"url": f"https://example.com/{file_name}", "auto": True}

            with patch(
                "presets.remote_bindings.get_remote_preset_binding",
                side_effect=binding_for,
            ):
                result = reset_all_to_builtin(backend)

            self.assertEqual(result[0], 1)
            self.assertEqual(result[2], ["B"])
            self.assertEqual(backend.deleted_remote_identities, ["A.txt"])
            self.assertFalse((engine_paths.user_presets_dir / "A.txt").exists())
            self.assertTrue((engine_paths.user_presets_dir / "B.txt").is_dir())


if __name__ == "__main__":
    unittest.main()
