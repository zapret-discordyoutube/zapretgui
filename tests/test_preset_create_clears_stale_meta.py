"""Новый пресет начинает без прошлого файла с тем же именем (удалённого вне программы)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from presets.preset_file_ops import create_preset


class CreatePresetStaleMetaTests(unittest.TestCase):
    def test_pin_rating_folder_and_remote_binding_of_an_old_file_do_not_carry_over(self) -> None:
        store = Mock()
        store.create_preset.return_value = SimpleNamespace(file_name="A.txt", name="A")
        store.unique_preset_name.side_effect = lambda _engine, name, **_kw: name
        backend = SimpleNamespace(
            engine="winws2",
            preset_file_store=store,
            read_selected_source_text=lambda: "# Preset: Old\n--filter-tcp=443\n--lua-desync=pass\n",
            normalize_source_text=lambda text: text,
            _delete_folder_item_meta=Mock(),
            _delete_remote_binding_meta=Mock(),
            notify_presets_changed=Mock(),
        )

        created = create_preset(backend, "A")

        self.assertEqual(created.file_name, "A.txt")
        backend._delete_folder_item_meta.assert_called_once_with("A.txt")
        backend._delete_remote_binding_meta.assert_called_once_with("A.txt")


if __name__ == "__main__":
    unittest.main()
