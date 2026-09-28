"""Редактор делит текст пресета на аргументы ровно так же, как запуск.

Если деление расходится, редактор покажет «всё верно» на пресете, который
winws2 прочитает иначе. Эталон — ``launch_args_from_preset_text``.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

BUILTIN_WINWS2 = PROJECT_SRC / "presets" / "builtin" / "winws2"


class Winws2TokensTests(unittest.TestCase):
    def assert_same_as_launch(self, text: str) -> None:
        from profile.winws2_language.tokens import document_lines, tokenize
        from winws_runtime.runners.preset_runner_support import launch_args_from_preset_text

        tokens = tokenize(text)
        self.assertEqual([token.text for token in tokens], launch_args_from_preset_text(text))
        lines = document_lines(text)
        for token in tokens:
            self.assertEqual(lines[token.line][token.start:token.end], token.text)

    def test_inline_options_bom_comments_and_crlf(self) -> None:
        self.assert_same_as_launch(
            "﻿--wf-tcp-out=443\r\n# comment --new\r\n  --filter-tcp=443   --hostlist=lists/a b.txt\r\n"
            "\r\n--name=Имя с пробелами (CDN)\r\nstray value\r\n--lua-desync=fake:blob=x --lua-desync=pass\r\n"
        )

    def test_all_builtin_presets(self) -> None:
        paths = sorted(BUILTIN_WINWS2.glob("*.txt"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(preset=path.name):
                self.assert_same_as_launch(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
