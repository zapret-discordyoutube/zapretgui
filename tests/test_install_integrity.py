from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from install_integrity import (
    IntegrityCause,
    MANIFEST_FILE_NAME,
    build_manifest,
    describe_report,
    load_manifest,
    missing_critical_groups,
    verify_fast,
    verify_full,
    write_manifest,
)
from settings.mode import ENGINE_WINWS1, ENGINE_WINWS2


def _build_install_tree(root: Path, *, with_manifest: bool = True) -> Path:
    (root / "exe").mkdir(parents=True, exist_ok=True)
    (root / "lua").mkdir(parents=True, exist_ok=True)
    (root / "exe" / "winws.exe").write_bytes(b"winws1-binary")
    (root / "exe" / "winws2.exe").write_bytes(b"winws2-binary")
    (root / "exe" / "WinDivert.dll").write_bytes(b"windivert-dll")
    (root / "exe" / "Monkey64.sys").write_bytes(b"windivert-driver")
    (root / "lua" / "main.lua").write_text("-- lua", encoding="utf-8")

    runtime_dir = root / "_internal"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    if with_manifest:
        manifest = build_manifest(root, version="21.1.1.4", channel="stable")
        write_manifest(manifest, runtime_dir)
    return runtime_dir


class ManifestBuildTests(unittest.TestCase):
    def test_manifest_marks_engines_and_driver_as_critical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_install_tree(root, with_manifest=False)

            manifest = build_manifest(root, version="21.1.1.4", channel="stable")
            by_path = {entry.path: entry for entry in manifest.entries}

            self.assertEqual(manifest.version, "21.1.1.4")
            self.assertEqual(manifest.channel, "stable")
            self.assertTrue(by_path["exe/winws2.exe"].is_critical)
            self.assertEqual(by_path["exe/winws2.exe"].engines, (ENGINE_WINWS2,))
            self.assertEqual(by_path["exe/winws.exe"].engines, (ENGINE_WINWS1,))
            self.assertTrue(by_path["exe/WinDivert.dll"].is_critical)
            self.assertTrue(by_path["exe/Monkey64.sys"].is_critical)
            # Файлы поставки, не входящие в критичные группы, остаются required.
            self.assertFalse(by_path["lua/main.lua"].is_critical)

    def test_manifest_roundtrip_keeps_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = _build_install_tree(root)

            loaded = load_manifest(runtime_dir)

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.version, "21.1.1.4")
            self.assertEqual(
                sorted(entry.path for entry in loaded.entries),
                sorted(
                    [
                        "exe/Monkey64.sys",
                        "exe/WinDivert.dll",
                        "exe/winws.exe",
                        "exe/winws2.exe",
                        "lua/main.lua",
                    ]
                ),
            )

    def test_manifest_with_foreign_schema_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            (runtime_dir / MANIFEST_FILE_NAME).write_text(
                json.dumps({"schema": 999, "files": [{"path": "exe/winws2.exe", "size": 1}]}),
                encoding="utf-8",
            )

            self.assertIsNone(load_manifest(runtime_dir))

    def test_missing_critical_groups_reports_absent_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_install_tree(root, with_manifest=False)

            self.assertEqual(missing_critical_groups(root), ())

            (root / "exe" / "winws2.exe").unlink()
            missing = missing_critical_groups(root)

            self.assertEqual(len(missing), 1)
            self.assertIn("winws2.exe", missing[0])


class VerifyInstallationTests(unittest.TestCase):
    def test_intact_installation_has_no_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = _build_install_tree(root)

            report = verify_fast(root=root, manifest_dir=runtime_dir)

            self.assertTrue(report.ok)
            self.assertTrue(report.checked)
            self.assertIs(report.cause, IntegrityCause.OK)
            self.assertFalse(report.blocks_launch(ENGINE_WINWS2))

    def test_missing_engine_blocks_only_its_own_launch_method(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = _build_install_tree(root)
            (root / "exe" / "winws2.exe").unlink()

            report = verify_fast(root=root, manifest_dir=runtime_dir)

            self.assertIs(report.cause, IntegrityCause.REMOVED_AFTER_INSTALL)
            self.assertEqual(report.missing, ("exe/winws2.exe",))
            self.assertTrue(report.blocks_launch(ENGINE_WINWS2))
            self.assertFalse(report.blocks_launch(ENGINE_WINWS1))

    def test_absent_delivery_directory_is_incomplete_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = _build_install_tree(root)
            for path in (root / "exe").iterdir():
                path.unlink()
            (root / "exe").rmdir()

            report = verify_fast(root=root, manifest_dir=runtime_dir)

            self.assertIs(report.cause, IntegrityCause.INCOMPLETE_INSTALL)
            self.assertEqual(len(report.missing), 4)

    def test_fast_check_catches_size_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = _build_install_tree(root)
            (root / "exe" / "WinDivert.dll").write_bytes(b"windivert-dll-but-longer")

            report = verify_fast(root=root, manifest_dir=runtime_dir)

            self.assertIs(report.cause, IntegrityCause.CORRUPTED)
            self.assertEqual(report.corrupted, ("exe/WinDivert.dll",))

    def test_only_deep_check_catches_same_size_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = _build_install_tree(root)
            (root / "exe" / "WinDivert.dll").write_bytes(b"WINDIVERT-DLL")

            self.assertTrue(verify_fast(root=root, manifest_dir=runtime_dir).ok)

            deep_report = verify_full(root=root, manifest_dir=runtime_dir)

            self.assertIs(deep_report.cause, IntegrityCause.CORRUPTED)
            self.assertEqual(deep_report.corrupted, ("exe/WinDivert.dll",))

    def test_missing_manifest_never_blocks_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_install_tree(root, with_manifest=False)

            report = verify_fast(root=root, manifest_dir=root / "_internal")

            self.assertIs(report.cause, IntegrityCause.MANIFEST_ABSENT)
            self.assertFalse(report.checked)
            self.assertTrue(report.ok)
            self.assertFalse(report.blocks_launch(ENGINE_WINWS2))


class IntegrityMessageTests(unittest.TestCase):
    def test_removed_after_install_names_antivirus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = _build_install_tree(root)
            (root / "exe" / "winws2.exe").unlink()

            report = verify_fast(root=root, manifest_dir=runtime_dir)
            message = describe_report(report, repair_started=True)

            self.assertIn("winws2.exe", message.content)
            self.assertIn("карантин", message.content)
            self.assertIn("восстанавливает", message.content)

    def test_message_without_repair_tells_user_what_to_do(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = _build_install_tree(root)
            (root / "exe" / "winws2.exe").unlink()

            message = describe_report(verify_fast(root=root, manifest_dir=runtime_dir))

            self.assertIn("Восстановите программу", message.content)


class InstallationPreflightTests(unittest.TestCase):
    def _report_for(self, root: Path, runtime_dir: Path):
        return verify_fast(root=root, manifest_dir=runtime_dir)

    def test_preflight_passes_when_engine_present(self) -> None:
        from winws_runtime.health.installation_preflight import check_installation_before_launch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = _build_install_tree(root)
            report = self._report_for(root, runtime_dir)

            with patch("install_integrity.verify_fast", return_value=report):
                result = check_installation_before_launch("zapret2_mode")

            self.assertTrue(result.ok)

    def test_preflight_blocks_zapret2_but_not_zapret1(self) -> None:
        from winws_runtime.health.installation_preflight import check_installation_before_launch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = _build_install_tree(root)
            (root / "exe" / "winws2.exe").unlink()
            report = self._report_for(root, runtime_dir)

            with patch("install_integrity.verify_fast", return_value=report):
                blocked = check_installation_before_launch("zapret2_mode")
                allowed = check_installation_before_launch("zapret1_mode")

            self.assertFalse(blocked.ok)
            self.assertEqual(blocked.missing, ("exe/winws2.exe",))
            self.assertEqual(blocked.cause, IntegrityCause.REMOVED_AFTER_INSTALL.value)
            self.assertIn("winws2.exe", blocked.message)
            self.assertTrue(allowed.ok)

    def test_orchestra_requires_the_same_engine_as_zapret2(self) -> None:
        from winws_runtime.health.installation_preflight import check_installation_before_launch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = _build_install_tree(root)
            (root / "exe" / "winws2.exe").unlink()
            report = self._report_for(root, runtime_dir)

            with patch("install_integrity.verify_fast", return_value=report):
                result = check_installation_before_launch("orchestra")

            self.assertFalse(result.ok)

    def test_preflight_never_blocks_on_verification_failure(self) -> None:
        from winws_runtime.health.installation_preflight import check_installation_before_launch

        with patch("install_integrity.verify_fast", side_effect=OSError("no disk")):
            result = check_installation_before_launch("zapret2_mode")

        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
