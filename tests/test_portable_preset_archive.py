from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from core.paths import AppPaths
from lists.core.layered_files import write_profile_user_list_text
from presets.models import PresetManifest
from presets.portable_archive import export_preset_with_lists, import_portable_preset


PRESET_TEXT = (
    "# Preset: Portable\n"
    "--wf-tcp-out=80,443\n"
    "--new\n"
    "--name=Mega\n"
    "--filter-tcp=80,443\n"
    "--hostlist=lists/mega.txt\n"
    "--lua-desync=pass\n"
)


class _PresetStore:
    def __init__(self) -> None:
        self.created_text = ""

    def create_preset(self, _engine: str, name: str, source_text: str, *, kind: str = "user") -> PresetManifest:
        self.created_text = source_text
        return PresetManifest(
            file_name=f"{name}.txt",
            name=name,
            updated_at="now",
            kind=kind,
        )


class _Backend:
    engine = "winws2"

    def __init__(self, root: Path, source_text: str = PRESET_TEXT) -> None:
        self.app_paths = AppPaths(user_root=root, local_root=root)
        self.source_text = source_text
        self.preset_file_store = _PresetStore()
        self.notifications = 0

    def read_source_text_by_file_name(self, _file_name: str) -> str:
        return self.source_text

    def normalize_source_text(self, text: str) -> str:
        return text

    def _delete_folder_item_meta(self, _file_name: str) -> None:
        return None

    def notify_presets_changed(self) -> None:
        self.notifications += 1


class PortablePresetArchiveTests(unittest.TestCase):
    def test_system_lists_keep_plain_txt_export(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lists_root = root / "lists"
            (lists_root / "base").mkdir(parents=True)
            (lists_root / "base" / "mega.txt").write_text("mega.nz\n", encoding="utf-8")
            backend = _Backend(root)
            destination = root / "shared.txt"

            result = export_preset_with_lists(backend, "Portable.txt", destination)

            self.assertFalse(result.is_archive)
            self.assertEqual(result.path, destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), PRESET_TEXT)
            self.assertFalse((root / "shared.zip").exists())

    def test_custom_list_turns_export_into_zip_and_imports_with_preset(self) -> None:
        with TemporaryDirectory() as source_dir, TemporaryDirectory() as target_dir:
            source_root = Path(source_dir)
            write_profile_user_list_text(source_root / "lists", "mega.txt", "mega.nz\nmega.io\n")
            exported = export_preset_with_lists(
                _Backend(source_root),
                "Portable.txt",
                source_root / "shared.txt",
            )

            self.assertTrue(exported.is_archive)
            self.assertEqual(exported.path.suffix, ".zip")
            self.assertFalse((source_root / "shared.txt").exists())

            target_root = Path(target_dir)
            target_backend = _Backend(target_root)
            imported = import_portable_preset(target_backend, exported.path, name="From friend")

            self.assertEqual(imported.file_name, "From friend.txt")
            self.assertEqual(imported.imported_list_files, ("mega.txt",))
            self.assertEqual(
                (target_root / "lists" / "user" / "mega.txt").read_text(encoding="utf-8"),
                "mega.nz\nmega.io\n",
            )
            self.assertIn("--hostlist=lists/mega.txt", target_backend.preset_file_store.created_text)
            self.assertIn("# Preset: From friend", target_backend.preset_file_store.created_text)
            self.assertIn("# PresetKind: imported", target_backend.preset_file_store.created_text)
            self.assertEqual(target_backend.notifications, 1)

    def test_standalone_name_collision_is_renamed_without_overwrite(self) -> None:
        with TemporaryDirectory() as source_dir, TemporaryDirectory() as target_dir:
            source_root = Path(source_dir)
            write_profile_user_list_text(source_root / "lists", "mega.txt", "sender.example\n")
            exported = export_preset_with_lists(
                _Backend(source_root),
                "Portable.txt",
                source_root / "shared.txt",
            )

            target_root = Path(target_dir)
            write_profile_user_list_text(target_root / "lists", "mega.txt", "receiver.example\n")
            target_backend = _Backend(target_root)
            imported = import_portable_preset(target_backend, exported.path, name="Collision")

            self.assertEqual(imported.renamed_list_files, (("mega.txt", "mega-imported-2.txt"),))
            self.assertEqual(
                (target_root / "lists" / "user" / "mega.txt").read_text(encoding="utf-8"),
                "receiver.example\n",
            )
            self.assertEqual(
                (target_root / "lists" / "user" / "mega-imported-2.txt").read_text(encoding="utf-8"),
                "sender.example\n",
            )
            self.assertIn("--hostlist=lists/mega-imported-2.txt", target_backend.preset_file_store.created_text)

    def test_system_list_user_additions_are_merged_on_import(self) -> None:
        with TemporaryDirectory() as source_dir, TemporaryDirectory() as target_dir:
            source_root = Path(source_dir)
            source_lists = source_root / "lists"
            (source_lists / "base").mkdir(parents=True)
            (source_lists / "base" / "mega.txt").write_text("system.example\n", encoding="utf-8")
            write_profile_user_list_text(source_lists, "mega.txt", "sender.example\n")
            exported = export_preset_with_lists(
                _Backend(source_root),
                "Portable.txt",
                source_root / "shared.txt",
            )

            target_root = Path(target_dir)
            target_lists = target_root / "lists"
            (target_lists / "base").mkdir(parents=True)
            (target_lists / "base" / "mega.txt").write_text("system.example\n", encoding="utf-8")
            write_profile_user_list_text(target_lists, "mega.txt", "receiver.example\n")
            target_backend = _Backend(target_root)

            imported = import_portable_preset(target_backend, exported.path, name="Merged")

            self.assertEqual(imported.renamed_list_files, ())
            self.assertEqual(
                (target_lists / "user" / "mega.txt").read_text(encoding="utf-8"),
                "receiver.example\nsender.example\n",
            )
            self.assertIn("--hostlist=lists/mega.txt", target_backend.preset_file_store.created_text)

    def test_import_rejects_list_not_referenced_by_preset(self) -> None:
        with TemporaryDirectory() as source_dir, TemporaryDirectory() as target_dir:
            source_root = Path(source_dir)
            write_profile_user_list_text(source_root / "lists", "mega.txt", "sender.example\n")
            exported = export_preset_with_lists(
                _Backend(source_root),
                "Portable.txt",
                source_root / "shared.txt",
            )
            with zipfile.ZipFile(exported.path, "r") as source_zip:
                manifest = json.loads(source_zip.read("manifest.json").decode("utf-8"))
                list_text = source_zip.read("lists/mega.txt")
            foreign_preset = PRESET_TEXT.replace("lists/mega.txt", "lists/system.txt")
            with zipfile.ZipFile(exported.path, "w", compression=zipfile.ZIP_DEFLATED) as changed_zip:
                changed_zip.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
                changed_zip.writestr("preset.txt", foreign_preset)
                changed_zip.writestr("lists/mega.txt", list_text)

            target_backend = _Backend(Path(target_dir))
            with self.assertRaisesRegex(ValueError, "не ссылается"):
                import_portable_preset(target_backend, exported.path, name="Unsafe")

            self.assertFalse((Path(target_dir) / "lists" / "user" / "mega.txt").exists())
            self.assertEqual(target_backend.preset_file_store.created_text, "")


if __name__ == "__main__":
    unittest.main()
