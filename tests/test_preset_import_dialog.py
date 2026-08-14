"""Fluent-диалог импорта пресета: состояния, drop-контракт, accessibility."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget


class PresetImportDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._host = QWidget()
        self._host.resize(800, 600)
        self.addCleanup(self._host.deleteLater)

    def _make_dialog(self, dialog_cls=None, language: str = "ru"):
        if dialog_cls is None:
            from presets.ui.common.preset_import_dialog import ImportPresetDialog as dialog_cls
        dlg = dialog_cls(self._host, language=language)
        self.addCleanup(dlg.deleteLater)
        return dlg

    def test_engine_subclasses_have_expected_prefixes(self) -> None:
        from presets.ui.zapret1.user_presets_dialogs import ImportPresetDialog as Z1
        from presets.ui.zapret2.user_presets_dialogs import ImportPresetDialog as Z2

        self.assertEqual(Z1.tr_prefix, "page.winws1_user_presets")
        self.assertEqual(Z2.tr_prefix, "page.winws2_user_presets")
        for dialog_cls in (Z1, Z2):
            dlg = self._make_dialog(dialog_cls)
            self.assertTrue(dlg.titleLabel.text())
            self.assertTrue(dlg.yesButton.text())
            self.assertTrue(dlg.cancelButton.text())

    def test_pages_register_import_dialog_cls(self) -> None:
        from presets.ui.zapret1.user_presets_page import Zapret1UserPresetsPage
        from presets.ui.zapret2.user_presets_page import Zapret2UserPresetsPage

        self.assertIsNotNone(Zapret1UserPresetsPage.page_config.import_dialog_cls)
        self.assertIsNotNone(Zapret2UserPresetsPage.page_config.import_dialog_cls)

    def test_empty_validate_shows_warning(self) -> None:
        dlg = self._make_dialog()
        self.assertFalse(dlg.validate())
        self.assertTrue(dlg.warningLabel.isVisibleTo(dlg))
        self.assertFalse(dlg._downloading)

    def test_invalid_url_shows_warning_without_download(self) -> None:
        dlg = self._make_dialog()
        dlg.urlEdit.setText("ftp://example.com/p.txt")
        with patch(
            "presets.ui.common.preset_import_download_worker.PresetImportDownloadWorker"
        ) as worker_cls:
            self.assertFalse(dlg.validate())
        worker_cls.assert_not_called()
        self.assertTrue(dlg.warningLabel.isVisibleTo(dlg))

    def test_valid_url_starts_download_and_disables_inputs(self) -> None:
        dlg = self._make_dialog()
        dlg.urlEdit.setText("https://example.com/p.txt")
        started = {}

        class _FakeWorker:
            def __init__(self, request_id, url, parent=None):
                started["request_id"] = request_id
                started["url"] = url
                self.succeeded = SimpleNamespace(connect=lambda *_a: None)
                self.failed = SimpleNamespace(connect=lambda *_a: None)
                self.finished = SimpleNamespace(connect=lambda *_a: None)

            def start(self):
                started["started"] = True

            def request_cancel(self):
                started["cancelled"] = True

            def deleteLater(self):  # noqa: N802 (Qt-совместимое имя)
                started["deleted"] = True

        with patch(
            "presets.ui.common.preset_import_download_worker.PresetImportDownloadWorker",
            _FakeWorker,
        ):
            self.assertFalse(dlg.validate())
        self.assertTrue(started.get("started"))
        self.assertEqual(started.get("url"), "https://example.com/p.txt")
        self.assertTrue(dlg._downloading)
        self.assertFalse(dlg.yesButton.isEnabled())
        self.assertFalse(dlg.urlEdit.isEnabled())
        self.assertTrue(dlg.progressBar.isVisibleTo(dlg))

        # Закрытие во время скачивания просит отмену.
        dlg.done(0)
        self.assertTrue(started.get("cancelled"))

    def test_download_success_accepts_with_result(self) -> None:
        dlg = self._make_dialog()
        dlg.urlEdit.setText("https://example.com/p.txt")
        dlg._downloading = True
        dlg._download_request_id = 7
        result = SimpleNamespace(
            file_path="C:/Temp/dl/Preset.txt",
            source_url="https://example.com/p.txt",
            suffix=".txt",
        )
        with patch.object(dlg, "accept") as accept:
            dlg._on_download_succeeded(7, result)
        accept.assert_called_once()
        self.assertEqual(dlg.result_file_path, "C:/Temp/dl/Preset.txt")
        self.assertEqual(dlg.result_source_url, "https://example.com/p.txt")
        self.assertTrue(dlg.result_auto_update)
        self.assertFalse(dlg._downloading)

    def test_download_success_zip_disables_auto_update(self) -> None:
        dlg = self._make_dialog()
        dlg._downloading = True
        dlg._download_request_id = 3
        result = SimpleNamespace(
            file_path="C:/Temp/dl/Bundle.zip",
            source_url="https://example.com/bundle.zip",
            suffix=".zip",
        )
        with patch.object(dlg, "accept"):
            dlg._on_download_succeeded(3, result)
        self.assertFalse(dlg.result_auto_update)

    def test_download_failure_returns_to_idle_with_warning(self) -> None:
        dlg = self._make_dialog()
        dlg._downloading = True
        dlg._download_request_id = 5
        dlg._set_inputs_enabled(False)
        dlg._on_download_failed(5, "timeout", "stalled")
        self.assertFalse(dlg._downloading)
        self.assertTrue(dlg.yesButton.isEnabled())
        self.assertTrue(dlg.warningLabel.isVisibleTo(dlg))

    def test_stale_download_signals_ignored(self) -> None:
        dlg = self._make_dialog()
        dlg._download_request_id = 9
        with patch.object(dlg, "accept") as accept:
            dlg._on_download_succeeded(8, SimpleNamespace(file_path="x", source_url="y", suffix=".txt"))
        accept.assert_not_called()

    def test_auto_update_checkbox_follows_url_scheme(self) -> None:
        dlg = self._make_dialog()
        self.assertFalse(dlg.autoUpdateCheck.isEnabled())
        dlg.urlEdit.setText("https://example.com/p.txt")
        self.assertTrue(dlg.autoUpdateCheck.isEnabled())
        dlg.urlEdit.setText("http://example.com/p.txt")
        self.assertFalse(dlg.autoUpdateCheck.isEnabled())

    def test_file_selection_accepts_immediately(self) -> None:
        dlg = self._make_dialog()
        with tempfile.TemporaryDirectory() as tmp:
            preset_path = Path(tmp) / "Local.txt"
            preset_path.write_text("--wf-tcp-out=443\n", encoding="utf-8")
            with patch.object(dlg, "accept") as accept:
                self.assertTrue(dlg.handle_dropped_preset_files([str(preset_path)]))
            accept.assert_called_once()
            self.assertEqual(dlg.result_file_path, str(preset_path))
            self.assertEqual(dlg.result_source_url, "")
            self.assertFalse(dlg.result_auto_update)

    def test_dropped_non_preset_files_rejected(self) -> None:
        dlg = self._make_dialog()
        with tempfile.TemporaryDirectory() as tmp:
            other_path = Path(tmp) / "notes.md"
            other_path.write_text("hello", encoding="utf-8")
            with patch.object(dlg, "accept") as accept:
                self.assertFalse(dlg.handle_dropped_preset_files([str(other_path)]))
            accept.assert_not_called()

    def test_drop_ignored_while_downloading(self) -> None:
        dlg = self._make_dialog()
        dlg._downloading = True
        with tempfile.TemporaryDirectory() as tmp:
            preset_path = Path(tmp) / "Busy.txt"
            preset_path.write_text("x", encoding="utf-8")
            self.assertFalse(dlg.handle_dropped_preset_files([str(preset_path)]))

    def test_accessibility_metadata_present(self) -> None:
        dlg = self._make_dialog()
        for control in (dlg.dropZone, dlg.browseButton, dlg.urlEdit, dlg.autoUpdateCheck, dlg.yesButton, dlg.cancelButton):
            self.assertTrue(control.accessibleName(), msg=str(control))
            self.assertTrue(control.accessibleDescription(), msg=str(control))

    def test_browse_uses_file_dialog(self) -> None:
        dlg = self._make_dialog()
        with patch(
            "presets.ui.common.preset_import_dialog.QFileDialog.getOpenFileName",
            return_value=("C:/Temp/Chosen.txt", ""),
        ):
            with patch.object(dlg, "accept") as accept:
                dlg._on_browse_clicked()
        accept.assert_called_once()
        self.assertEqual(dlg.result_file_path, "C:/Temp/Chosen.txt")


if __name__ == "__main__":
    unittest.main()
