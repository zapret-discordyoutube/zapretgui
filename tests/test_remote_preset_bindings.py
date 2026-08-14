"""CRUD привязок удалённых пресетов поверх settings store."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class RemotePresetBindingsTests(unittest.TestCase):
    def setUp(self):
        self._temp = TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        patcher = patch("settings.store.MAIN_DIRECTORY", str(Path(self._temp.name)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_defaults_and_missing(self):
        from presets import remote_bindings as rb

        self.assertEqual(rb.load_remote_preset_bindings("winws2"), {})
        self.assertIsNone(rb.get_remote_preset_binding("winws2", "nope.txt"))
        self.assertFalse(rb.delete_remote_preset_binding("winws2", "nope.txt"))
        self.assertFalse(rb.rename_remote_preset_binding("winws2", "a.txt", "b.txt"))
        self.assertIsNone(rb.find_remote_preset_by_url("https://example.com/x.txt"))

    def test_set_get_roundtrip_with_normalization(self):
        from presets import remote_bindings as rb

        binding = rb.make_remote_preset_binding(
            "https://example.com/p.txt", synced_hash="abc", now_iso="2026-08-14T00:00:00Z"
        )
        saved = rb.set_remote_preset_binding("winws2", "My.txt", binding)
        self.assertIsNotNone(saved)
        loaded = rb.get_remote_preset_binding("winws2", "My.txt")
        self.assertEqual(loaded["url"], "https://example.com/p.txt")
        self.assertEqual(loaded["synced_hash"], "abc")
        self.assertTrue(loaded["auto"])
        self.assertFalse(loaded["detached"])

    def test_binding_without_url_rejected(self):
        from presets import remote_bindings as rb

        self.assertIsNone(rb.set_remote_preset_binding("winws2", "My.txt", {"url": ""}))
        self.assertIsNone(rb.get_remote_preset_binding("winws2", "My.txt"))

    def test_scope_isolation(self):
        from presets import remote_bindings as rb

        rb.set_remote_preset_binding(
            "winws2", "A.txt", rb.make_remote_preset_binding("https://example.com/a.txt")
        )
        self.assertIsNone(rb.get_remote_preset_binding("winws1", "A.txt"))
        self.assertIsNotNone(rb.get_remote_preset_binding("winws2", "A.txt"))

    def test_update_fields(self):
        from presets import remote_bindings as rb

        rb.set_remote_preset_binding(
            "winws1", "B.txt", rb.make_remote_preset_binding("https://example.com/b.txt")
        )
        updated = rb.update_remote_preset_binding("winws1", "B.txt", detached=True, error="oops")
        self.assertTrue(updated["detached"])
        self.assertEqual(updated["error"], "oops")
        self.assertIsNone(rb.update_remote_preset_binding("winws1", "missing.txt", error="x"))

    def test_rename_migrates_binding(self):
        from presets import remote_bindings as rb

        rb.set_remote_preset_binding(
            "winws2", "Old.txt", rb.make_remote_preset_binding("https://example.com/o.txt")
        )
        self.assertTrue(rb.rename_remote_preset_binding("winws2", "Old.txt", "New.txt"))
        self.assertIsNone(rb.get_remote_preset_binding("winws2", "Old.txt"))
        self.assertEqual(
            rb.get_remote_preset_binding("winws2", "New.txt")["url"],
            "https://example.com/o.txt",
        )

    def test_delete(self):
        from presets import remote_bindings as rb

        rb.set_remote_preset_binding(
            "winws2", "Del.txt", rb.make_remote_preset_binding("https://example.com/d.txt")
        )
        self.assertTrue(rb.delete_remote_preset_binding("winws2", "Del.txt"))
        self.assertIsNone(rb.get_remote_preset_binding("winws2", "Del.txt"))

    def test_find_by_url_across_scopes(self):
        from presets import remote_bindings as rb

        rb.set_remote_preset_binding(
            "winws1", "C.txt", rb.make_remote_preset_binding("https://example.com/c.txt")
        )
        found = rb.find_remote_preset_by_url("https://example.com/c.txt")
        self.assertIsNotNone(found)
        scope, file_name, binding = found
        self.assertEqual(scope, "winws1")
        self.assertEqual(file_name, "C.txt")
        self.assertEqual(binding["url"], "https://example.com/c.txt")

    def test_normalization_survives_settings_roundtrip(self):
        from settings.normalize import normalize_settings

        normalized = normalize_settings(
            {
                "remote_presets": {
                    "winws2": {
                        "Ok.txt": {"url": "https://example.com/ok.txt", "auto": False},
                        "NoUrl.txt": {"etag": "x"},
                        "": {"url": "https://example.com/empty-key.txt"},
                    },
                    "junk": {"A.txt": {"url": "https://example.com/a.txt"}},
                }
            }
        )
        section = normalized["remote_presets"]
        self.assertEqual(set(section.keys()), {"winws2", "winws1"})
        self.assertEqual(set(section["winws2"].keys()), {"Ok.txt"})
        self.assertFalse(section["winws2"]["Ok.txt"]["auto"])
        self.assertTrue(section["winws2"]["Ok.txt"]["detached"] is False)


if __name__ == "__main__":
    unittest.main()
