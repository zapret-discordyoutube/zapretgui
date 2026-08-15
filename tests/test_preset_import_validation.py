"""Импорт пресетов не должен принимать произвольный TXT (лог, JSON и т.п.)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


VALID_PRESET_TEXT = (
    "# Preset: Example\n"
    "\n"
    "--wf-tcp-out=80,443,8080\n"
    "--new\n"
    "--name=revive\n"
    "--filter-tcp=80,8080\n"
    "--hostlist-domains=main.txrevive.com\n"
    "--out-range=-d8\n"
    "--lua-desync=hostfakesplit:host=ozon.ru:tcp_ts=-1000\n"
)

LOG_FILE_TEXT = (
    "=== Zapret 2 GUI Log - Started 2026-07-23 10:40:51 ===\n"
    "[10:40:51] [INFO] Запуск приложения\n"
    "[10:40:52] [SUCCESS] Всё работает\n"
)


class ValidatePresetSourceTextTests(unittest.TestCase):
    def _validate(self, text: str, engine: str = "winws2") -> str:
        from presets.preset_text_ops import validate_preset_source_text

        return validate_preset_source_text(text, engine=engine)

    def test_accepts_real_preset_with_comments_and_blank_lines(self) -> None:
        self.assertEqual(self._validate(VALID_PRESET_TEXT), "")

    def test_accepts_preset_with_bom_and_crlf(self) -> None:
        text = "﻿# Preset: X\r\n--wf-tcp-out=443\r\n--new\r\n--lua-desync=fake\r\n"
        self.assertEqual(self._validate(text), "")

    def test_rejects_preset_without_wf_filter(self) -> None:
        error = self._validate("--new\n--filter-tcp=443\n--lua-desync=fake\n")
        self.assertIn("--wf", error)
        self.assertIn("не перехватывает", error)

    def test_rejects_winws2_preset_without_lua_desync(self) -> None:
        error = self._validate("--wf-tcp-out=443\n--filter-tcp=443\n--hostlist=x.txt\n")
        self.assertIn("--lua-desync", error)

    def test_winws1_preset_does_not_require_lua_desync(self) -> None:
        text = "--wf-tcp=443\n--dpi-desync=fake,split2\n"
        self.assertEqual(self._validate(text, engine="winws1"), "")

    def test_rejects_log_file(self) -> None:
        error = self._validate(LOG_FILE_TEXT)
        self.assertIn("не является опцией winws", error)
        self.assertIn("строка 1", error)

    def test_rejects_empty_and_comment_only_text(self) -> None:
        self.assertIn("нет ни одной опции", self._validate(""))
        self.assertIn("нет ни одной опции", self._validate("# только комментарий\n\n"))

    def test_rejects_mixed_text_with_garbage_line(self) -> None:
        error = self._validate("--new\nвнезапный мусор\n--filter-tcp=443\n")
        self.assertIn("строка 2", error)
        self.assertIn("внезапный мусор", error)

    def test_long_garbage_line_is_truncated_in_error(self) -> None:
        error = self._validate("x" * 200 + "\n")
        self.assertIn("…", error)
        self.assertLess(len(error), 160)


class ImportFromFileValidationTests(unittest.TestCase):
    def _backend(self) -> SimpleNamespace:
        backend = SimpleNamespace(
            engine="winws2",
            normalize_source_text=lambda text: text,
            preset_file_store=SimpleNamespace(
                create_preset=Mock(
                    return_value=SimpleNamespace(file_name="Imported.txt", name="Imported")
                )
            ),
            _delete_folder_item_meta=Mock(),
            notify_presets_changed=Mock(),
        )
        return backend

    def test_import_rejects_log_file_without_creating_preset(self) -> None:
        from presets.preset_file_ops import import_from_file

        backend = self._backend()
        with tempfile.TemporaryDirectory() as tmp_dir:
            src = Path(tmp_dir) / "zapret_log_2026-07-23.txt"
            src.write_text(LOG_FILE_TEXT, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "не похож на пресет"):
                import_from_file(backend, src)

        backend.preset_file_store.create_preset.assert_not_called()
        backend.notify_presets_changed.assert_not_called()

    def test_import_accepts_real_preset(self) -> None:
        from presets.preset_file_ops import import_from_file

        backend = self._backend()
        with tempfile.TemporaryDirectory() as tmp_dir:
            src = Path(tmp_dir) / "Ростелеком.txt"
            src.write_text(VALID_PRESET_TEXT, encoding="utf-8")

            imported = import_from_file(backend, src)

        self.assertEqual(imported.file_name, "Imported.txt")
        backend.preset_file_store.create_preset.assert_called_once()
        created_text = backend.preset_file_store.create_preset.call_args.args[2]
        self.assertIn("--filter-tcp=80,8080", created_text)
        self.assertIn("# PresetKind: imported", created_text)


if __name__ == "__main__":
    unittest.main()


class DebugLogPathRelocationTests(unittest.TestCase):
    """Логи переехали в user\\logs — старый путь ломал запуск winws2."""

    def test_new_debug_line_points_to_user_logs(self) -> None:
        from presets.preset_text_ops import _rewrite_debug_log_setting

        result = _rewrite_debug_log_setting(VALID_PRESET_TEXT, "Мой пресет", True)
        self.assertIn("--debug=@user/logs/", result)
        self.assertNotIn("--debug=@logs/", result)

    def test_legacy_debug_line_is_relocated_on_normalize(self) -> None:
        from presets.preset_text_ops import normalize_preset_source_text_for_engine

        legacy = VALID_PRESET_TEXT + "--debug=@logs/Default_v1_game_filter_debug.log\n"
        result = normalize_preset_source_text_for_engine(legacy, "winws2")
        self.assertIn("--debug=@user/logs/Default_v1_game_filter_debug.log", result)
        self.assertNotIn("--debug=@logs/", result)

    def test_absolute_debug_path_is_left_untouched(self) -> None:
        from presets.preset_text_ops import normalize_preset_source_text_for_engine

        absolute = VALID_PRESET_TEXT + "--debug=@C:/Zapret/custom.log\n"
        result = normalize_preset_source_text_for_engine(absolute, "winws2")
        self.assertIn("--debug=@C:/Zapret/custom.log", result)

    def test_existing_user_logs_path_survives_toggle(self) -> None:
        from presets.preset_text_ops import _rewrite_debug_log_setting

        text = VALID_PRESET_TEXT + "--debug=@user/logs/Мой_debug.log\n"
        result = _rewrite_debug_log_setting(text, "Мой", True)
        self.assertEqual(result.count("--debug="), 1)
        self.assertIn("--debug=@user/logs/Мой_debug.log", result)
