from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BUILTIN = PROJECT_SRC / "presets" / "builtin"

SAMPLE = """# Preset: Пример
# BuiltinVersion: 1.0

--lua-init=@lua/zapret-lib.lua
--lua-init=@lua/zapret-antidpi.lua

--ctrack-disable=0

--wf-tcp-out=80,443
--wf-udp-out=443

--blob=tls_google:@bin/tls_clienthello_www_google_com.bin

--name=youtube
--filter-tcp=443
--hostlist=lists/youtube.txt
--out-range=-d8
--payload=tls_client_hello
--lua-desync=multisplit:pos=1

--new

--name=discord
--filter-udp=50000-50100
--ipset=lists/ipset-discord.txt
--lua-desync=fake:blob=tls_google
"""


def _lines(text: str, indexes) -> list[str]:
    source = text.split("\n")
    return [source[index] for index in indexes]


class PresetTextOutlineTests(unittest.TestCase):
    def test_every_part_is_found_in_the_right_lines(self) -> None:
        from profile.parser import preset_text_outline

        outline = preset_text_outline(SAMPLE, engine="winws2")
        self.assertEqual(_lines(SAMPLE, outline.header), ["# Preset: Пример", "# BuiltinVersion: 1.0"])
        self.assertEqual(len(outline.lua_init), 2)
        self.assertEqual(_lines(SAMPLE, outline.engine_options), ["--ctrack-disable=0"])
        self.assertEqual(_lines(SAMPLE, outline.interception), ["--wf-tcp-out=80,443", "--wf-udp-out=443"])
        self.assertEqual(len(outline.blobs), 1)
        self.assertEqual(len(outline.profiles), 2)
        first, second = outline.profiles
        self.assertIsNone(first.new_line)
        self.assertEqual(_lines(SAMPLE, first.name), ["--name=youtube"])
        self.assertEqual(_lines(SAMPLE, first.match), ["--filter-tcp=443", "--hostlist=lists/youtube.txt"])
        self.assertEqual(_lines(SAMPLE, first.packets), ["--out-range=-d8", "--payload=tls_client_hello"])
        self.assertEqual(_lines(SAMPLE, first.strategy), ["--lua-desync=multisplit:pos=1"])
        self.assertEqual(_lines(SAMPLE, [second.new_line]), ["--new"])
        self.assertEqual(second.packets, ())

    def test_outline_follows_the_text_not_remembered_line_numbers(self) -> None:
        from ui.onboarding.preset_sections import build_outline, section_lines, section_text_values

        edited = SAMPLE.replace(
            "--blob=tls_google:@bin/tls_clienthello_www_google_com.bin\n",
            "--blob=tls_vk:@bin/tls_clienthello_vk_com.bin\n--blob=quic1:@bin/quic_1.bin\n",
        ).replace("--lua-desync=multisplit:pos=1", "--lua-desync=fakedsplit:pos=2")
        before = build_outline(SAMPLE, zapret2=True)
        after = build_outline(edited, zapret2=True)
        self.assertEqual(section_text_values(before, "blobs"), {"count": "1", "example": "tls_google"})
        self.assertEqual(section_text_values(after, "blobs"), {"count": "2", "example": "tls_vk"})
        self.assertEqual(section_text_values(after, "profile_strategy")["technique"], "fakedsplit")
        self.assertEqual(
            [line + 1 for line in section_lines(after, "profile_strategy")],
            [line + 2 for line in section_lines(before, "profile_strategy")],
        )

    def test_missing_part_has_no_lines(self) -> None:
        from ui.onboarding.preset_sections import build_outline, section_lines

        text = "--wf-tcp-out=443\n\n--filter-tcp=443\n--lua-desync=fake\n"
        outline = build_outline(text, zapret2=True)
        for section in ("header", "lua_init", "blobs", "engine_options", "profile_name", "profile_packets", "profile_new"):
            self.assertEqual(section_lines(outline, section), (), section)
        self.assertEqual(len(section_lines(outline, "interception")), 1)

    def test_outline_agrees_with_parser_on_every_builtin_preset(self) -> None:
        from profile.parser import parse_preset_text, preset_text_outline

        checked = 0
        for engine in ("winws2", "winws1"):
            for path in sorted((BUILTIN / engine).glob("*.txt")):
                text = path.read_text(encoding="utf-8")
                preset = parse_preset_text(text, engine=engine)
                outline = preset_text_outline(text, engine=engine)
                with self.subTest(preset=path.name):
                    self.assertEqual(len(outline.profiles), len(preset.profiles))
                    self.assertEqual(
                        len(outline.header),
                        len([line for line in preset.header_lines if line.strip()]),
                    )
                    for profile_outline, profile in zip(outline.profiles, preset.profiles):
                        strategy_like = profile_outline.packets + profile_outline.strategy
                        kinds = [s for s in profile.segments if s.kind in {"strategy", "strategy_filter"}]
                        self.assertEqual(len(strategy_like), len(kinds))
                checked += 1
        self.assertGreater(checked, 100)


class PresetSectionEditorTests(unittest.TestCase):
    def setUp(self) -> None:
        from PyQt6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])

    def test_highlight_and_scroll_do_not_touch_the_text(self) -> None:
        from PyQt6.QtWidgets import QPlainTextEdit

        from ui.onboarding.preset_sections import (
            build_outline,
            editor_lines_rect,
            scroll_editor_to_line,
            section_lines,
        )

        long_text = SAMPLE + "\n".join(f"--new\n--filter-tcp={port}\n--lua-desync=fake" for port in range(1000, 1060))
        editor = QPlainTextEdit()
        editor.resize(600, 200)
        editor.setPlainText(long_text)
        editor.show()
        try:
            outline = build_outline(editor.toPlainText(), zapret2=True)
            lines = section_lines(outline, "profile_strategy")
            revision = editor.document().revision()
            cursor_position = editor.textCursor().position()
            scroll_editor_to_line(editor, min(lines))
            target = editor_lines_rect(editor, lines)
            self.assertIsNotNone(target)
            widget, rect = target
            self.assertIs(widget, editor)
            self.assertTrue(editor.rect().contains(rect))
            self.assertEqual(editor.document().revision(), revision)
            self.assertEqual(editor.textCursor().position(), cursor_position)
            self.assertEqual(editor.toPlainText(), long_text)
        finally:
            editor.close()
            editor.deleteLater()


if __name__ == "__main__":
    unittest.main()
