"""Сырой редактор показывает то, что реально записано в файл пресета.

Сохранение нормализует текст (presets.preset_contract), например дописывает
обязательный блок --lua-init. Раньше редактор оставлял у себя текст до
нормализации, и то, что видел пользователь, расходилось с файлом.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from presets.preset_contract import normalize_preset_source_for_save
from presets.raw_preset_editor_workflow import save_raw_preset_text
from presets.ui.common.raw_preset_text_editor import map_line_after_rewrite
from profile.winws2_preset_source import WINWS2_LUA_INIT_LINES


USER_TEXT = (
    "# Preset: Mine\n"
    "--wf-tcp-out=443\n"
    "--new\n"
    "--name=youtube\n"
    "--filter-tcp=443\n"
    "--lua-desync=fake:blob=tls_google\n"
)
SAVED_TEXT = normalize_preset_source_for_save(USER_TEXT, "winws2")


class MapLineAfterRewriteTests(unittest.TestCase):
    def test_line_moves_down_by_inserted_block(self) -> None:
        self.assertEqual(map_line_after_rewrite(USER_TEXT, SAVED_TEXT, 0), 0)
        self.assertEqual(map_line_after_rewrite(USER_TEXT, SAVED_TEXT, 3), 3 + len(WINWS2_LUA_INIT_LINES))

    def test_replaced_line_maps_to_start_of_replacement(self) -> None:
        self.assertEqual(map_line_after_rewrite("a\nb\nc", "a\nX\nY\nc", 1), 1)
        self.assertEqual(map_line_after_rewrite("a\nb\nc", "a\nX\nY\nc", 2), 3)

    def test_line_past_end_is_clamped(self) -> None:
        self.assertEqual(map_line_after_rewrite("a\nb", "a", 5), 0)


class RawPresetTextEditorShowsSavedTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from presets.ui.common.raw_preset_text_editor import RawPresetTextEditor

        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.save_requests: list[bool] = []
        self.text_editor = RawPresetTextEditor(
            self.parent,
            request_save=lambda *, publish_content_changed=False: self.save_requests.append(publish_content_changed) or True,
            set_footer=lambda _text: None,
            cleanup_in_progress=lambda: False,
        )
        self.addCleanup(self.text_editor.cleanup)
        self.text_editor.apply_loaded_text(USER_TEXT)

    def _place_cursor(self, line: int, column: int) -> None:
        from PyQt6.QtGui import QTextCursor

        cursor = QTextCursor(self.text_editor.editor.document().findBlockByNumber(line))
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, column)
        self.text_editor.editor.setTextCursor(cursor)

    def test_saved_normalized_text_replaces_editor_text_and_keeps_cursor_line(self) -> None:
        self._place_cursor(4, 3)
        self.text_editor.content_publish_pending = True

        self.assertTrue(self.text_editor.show_saved_text(USER_TEXT, SAVED_TEXT))

        self.assertEqual(self.text_editor.editor.toPlainText(), SAVED_TEXT)
        self.assertEqual(self.text_editor.current_text(), SAVED_TEXT)
        cursor = self.text_editor.editor.textCursor()
        self.assertEqual(cursor.block().text(), "--filter-tcp=443")
        self.assertEqual(cursor.positionInBlock(), 3)
        # Подмена текста — не правка пользователя: нет нового автосохранения,
        # а ещё не опубликованная правка по-прежнему ждёт фиксации.
        self.app.processEvents()
        self.assertFalse(self.text_editor.save_timer.isActive())
        self.assertEqual(self.save_requests, [])
        self.assertTrue(self.text_editor.content_publish_pending)

    def test_newer_typing_is_not_overwritten(self) -> None:
        self.text_editor.editor.appendPlainText("--new")
        self.assertFalse(self.text_editor.show_saved_text(USER_TEXT, SAVED_TEXT))
        self.assertNotEqual(self.text_editor.editor.toPlainText(), SAVED_TEXT)

    def test_pending_save_is_not_overwritten(self) -> None:
        self.text_editor.save_timer.start(10_000)
        self.addCleanup(self.text_editor.save_timer.stop)
        self.assertFalse(self.text_editor.show_saved_text(USER_TEXT, SAVED_TEXT))
        self.assertEqual(self.text_editor.editor.toPlainText(), USER_TEXT)

    def test_final_newline_difference_does_not_redraw(self) -> None:
        self.text_editor.apply_loaded_text(SAVED_TEXT.rstrip("\n"))
        self.assertFalse(self.text_editor.show_saved_text(SAVED_TEXT.rstrip("\n"), SAVED_TEXT))


class _FeatureWithNormalizingSave:
    """Presets feature поверх «файла» в памяти с настоящей нормализацией."""

    def __init__(self, disk_text: str) -> None:
        self.disk_text = disk_text
        self.published: list[tuple[str, str]] = []
        self.save_publish_flags: list[bool] = []

    def save_preset_source_by_file_name(self, _method, file_name, source_text, *, publish_content_changed=True, content_change_kind=""):
        self.save_publish_flags.append(publish_content_changed)
        self.disk_text = normalize_preset_source_for_save(source_text, "winws2")
        return SimpleNamespace(file_name=file_name, name="Mine", kind="user")

    def read_preset_source_by_file_name(self, _method, _file_name):
        return self.disk_text

    def publish_preset_content_changed(self, _method, file_name, *, content_change_kind=""):
        self.published.append((file_name, content_change_kind))

    def get_preset_source_path_by_file_name(self, _method, file_name):
        return f"C:/Zapret/presets/{file_name}"


class SaveRawPresetTextTests(unittest.TestCase):
    def test_result_carries_requested_and_saved_text(self) -> None:
        feature = _FeatureWithNormalizingSave("")
        result = save_raw_preset_text(
            presets_feature=feature,
            launch_method="zapret2_mode",
            file_name="Mine.txt",
            source_text=USER_TEXT,
            publish_content_changed=False,
        )
        self.assertEqual(result.requested_text, USER_TEXT)
        self.assertEqual(result.saved_text, SAVED_TEXT)
        self.assertEqual(feature.published, [])

    def test_commit_publishes_even_when_autosave_already_wrote_the_text(self) -> None:
        # Автосохранение уже записало текст; фиксация правки ничего не меняет
        # в файле, но запуск всё равно должен узнать об изменении.
        feature = _FeatureWithNormalizingSave(SAVED_TEXT)
        save_raw_preset_text(
            presets_feature=feature,
            launch_method="zapret2_mode",
            file_name="Mine.txt",
            source_text=USER_TEXT,
            publish_content_changed=True,
        )
        self.assertEqual(feature.published, [("Mine.txt", "editor_save")])
        self.assertEqual(feature.save_publish_flags, [False])


class RawEditorPageShowsSavedTextTests(unittest.TestCase):
    def test_save_finished_hands_saved_text_to_the_editor(self) -> None:
        from presets.ui.common.preset_subpage_base import PresetRawEditorPage

        result = SimpleNamespace(
            updated=SimpleNamespace(name="Mine", file_name="Mine.txt", kind="user"),
            path="C:/Zapret/presets/Mine.txt",
            footer_text="Сохранено",
            can_reset_to_builtin=False,
            requested_text=USER_TEXT,
            saved_text=SAVED_TEXT,
        )
        page = PresetRawEditorPage.__new__(PresetRawEditorPage)
        page._cleanup_in_progress = False
        page._raw_save_request_id = 1
        page._preset_file_name = "Mine.txt"
        page._preset_origin = "user"
        page._set_footer = Mock()
        page._raw_text_editor = Mock()

        PresetRawEditorPage._on_raw_preset_save_finished(page, 1, "Mine.txt", result, False)

        page._raw_text_editor.show_saved_text.assert_called_once_with(USER_TEXT, SAVED_TEXT)


if __name__ == "__main__":
    unittest.main()
