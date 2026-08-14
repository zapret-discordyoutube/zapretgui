"""Импорт по уже привязанной ссылке обновляет существующий пресет, а не создаёт дубль."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

VALID_TEXT = (
    "# Preset: Example\n"
    "--wf-tcp-out=80,443\n"
    "--lua-desync=hostfakesplit:host=x.com:tcp_ts=-1000\n"
)

UPDATED_TEXT = (
    "# Preset: Example\n"
    "--wf-tcp-out=80,443,8080\n"
    "--lua-desync=hostfakesplit:host=y.com:tcp_ts=-500\n"
)

URL = "https://example.com/p.txt"


class UrlImportDedupTests(unittest.TestCase):
    def setUp(self):
        self._temp = TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        patcher = patch("settings.store.MAIN_DIRECTORY", str(Path(self._temp.name) / "settings"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _make_facade_stub(self, current_text: str):
        from app.feature_facades.presets import PresetsFeature

        stub = SimpleNamespace(
            read_preset_source_by_file_name=Mock(return_value=current_text),
            save_preset_source_by_file_name=Mock(),
            get_preset_manifest_by_file_name=Mock(return_value=SimpleNamespace(name="Мой пресет")),
            _remote_scope_for_launch_method=PresetsFeature._remote_scope_for_launch_method,
        )
        stub._update_url_bound_preset_from_file = (
            lambda launch_method, source_url, file_path, auto=True: PresetsFeature._update_url_bound_preset_from_file(
                stub, launch_method, source_url, file_path, auto=auto
            )
        )
        return stub

    def _bind(self, file_name: str = "Мой пресет.txt", synced_text: str = VALID_TEXT):
        from presets.remote_bindings import make_remote_preset_binding, set_remote_preset_binding
        from presets.remote_sync import comparison_hash

        set_remote_preset_binding(
            "winws2",
            file_name,
            make_remote_preset_binding(URL, synced_hash=comparison_hash(synced_text)),
        )

    def _downloaded_file(self, text: str) -> str:
        path = Path(self._temp.name) / "downloaded.txt"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_unbound_url_falls_through_to_normal_import(self):
        stub = self._make_facade_stub(VALID_TEXT)
        result = stub._update_url_bound_preset_from_file(
            "zapret2", URL, self._downloaded_file(UPDATED_TEXT)
        )
        self.assertIsNone(result)

    def test_update_preserves_local_preset_name_in_header(self):
        self._bind()
        stub = self._make_facade_stub(VALID_TEXT)
        remote_text = "# Preset: Имя Из Источника\n" + UPDATED_TEXT.split("\n", 1)[1]
        stub._update_url_bound_preset_from_file("zapret2", URL, self._downloaded_file(remote_text))
        saved_text = stub.save_preset_source_by_file_name.call_args[0][2]
        self.assertIn("# Preset: Мой пресет", saved_text)
        self.assertNotIn("Имя Из Источника", saved_text)

    def test_bound_url_updates_existing_preset_without_duplicate(self):
        self._bind()
        stub = self._make_facade_stub(VALID_TEXT)
        result = stub._update_url_bound_preset_from_file(
            "zapret2", URL, self._downloaded_file(UPDATED_TEXT)
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.ok)
        self.assertTrue(result.updated_existing)
        self.assertEqual(result.actual_file_name, "Мой пресет.txt")
        self.assertFalse(result.structure_changed)
        stub.save_preset_source_by_file_name.assert_called_once()
        _, kwargs = stub.save_preset_source_by_file_name.call_args
        self.assertTrue(kwargs["publish_content_changed"])
        self.assertEqual(kwargs["content_change_kind"], "remote_sync")

    def test_bound_url_with_identical_content_does_not_save(self):
        self._bind()
        stub = self._make_facade_stub(VALID_TEXT)
        result = stub._update_url_bound_preset_from_file(
            "zapret2", URL, self._downloaded_file(VALID_TEXT)
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.updated_existing)
        stub.save_preset_source_by_file_name.assert_not_called()

    def test_invalid_downloaded_content_raises(self):
        self._bind()
        stub = self._make_facade_stub(VALID_TEXT)
        with self.assertRaisesRegex(ValueError, "не похож на пресет"):
            stub._update_url_bound_preset_from_file(
                "zapret2", URL, self._downloaded_file("просто текст\n")
            )
        stub.save_preset_source_by_file_name.assert_not_called()

    def test_reimport_after_unlink_resumes_binding_without_duplicate(self):
        from app.feature_facades.presets import PresetsFeature
        from presets.remote_bindings import get_remote_preset_binding, update_remote_preset_binding

        self._bind()
        # «Отвязать» = пауза: URL остаётся в базе с auto=False.
        update_remote_preset_binding("winws2", "Мой пресет.txt", auto=False, detached=False)
        stub = self._make_facade_stub(VALID_TEXT)
        result = stub._update_url_bound_preset_from_file(
            "zapret2", URL, self._downloaded_file(UPDATED_TEXT)
        )
        self.assertIsNotNone(result)  # дубликат не создаётся
        self.assertTrue(result.updated_existing)
        binding = get_remote_preset_binding("winws2", "Мой пресет.txt")
        self.assertTrue(binding["auto"])  # привязка возобновлена

    def test_unbind_keeps_url_as_paused_binding(self):
        from app.feature_facades.presets import PresetsFeature
        from presets.remote_bindings import get_remote_preset_binding

        self._bind()
        stub = self._make_facade_stub(VALID_TEXT)
        stub.unbind_preset_remote_source = (
            lambda launch_method, file_name: PresetsFeature.unbind_preset_remote_source(
                stub, launch_method, file_name
            )
        )
        self.assertTrue(stub.unbind_preset_remote_source("zapret2", "Мой пресет.txt"))
        binding = get_remote_preset_binding("winws2", "Мой пресет.txt")
        self.assertIsNotNone(binding)  # URL сохранён
        self.assertFalse(binding["auto"])
        self.assertEqual(binding["url"], URL)

    def test_rename_migrates_binding_with_url(self):
        from presets.remote_bindings import get_remote_preset_binding, rename_remote_preset_binding

        self._bind()
        self.assertTrue(
            rename_remote_preset_binding("winws2", "Мой пресет.txt", "Новое имя.txt")
        )
        self.assertIsNone(get_remote_preset_binding("winws2", "Мой пресет.txt"))
        self.assertEqual(
            get_remote_preset_binding("winws2", "Новое имя.txt")["url"], URL
        )

    def test_binding_updates_after_successful_update(self):
        from presets.remote_bindings import get_remote_preset_binding
        from presets.remote_sync import comparison_hash

        self._bind()
        stub = self._make_facade_stub(VALID_TEXT)
        stub.read_preset_source_by_file_name = Mock(side_effect=[VALID_TEXT, UPDATED_TEXT])
        stub._update_url_bound_preset_from_file("zapret2", URL, self._downloaded_file(UPDATED_TEXT))
        binding = get_remote_preset_binding("winws2", "Мой пресет.txt")
        self.assertEqual(binding["synced_hash"], comparison_hash(UPDATED_TEXT))
        self.assertFalse(binding["detached"])
        self.assertEqual(binding["error"], "")


if __name__ == "__main__":
    unittest.main()
