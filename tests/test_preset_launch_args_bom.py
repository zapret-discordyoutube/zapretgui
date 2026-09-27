"""Ведущий BOM в файле пресета не должен попадать в аргументы winws.

Блокнот сохраняет «UTF-8 с BOM», файл читается как utf-8 и первая строка
начинается с U+FEFF. str.strip() его не снимает, поэтому раньше первый
аргумент @config выглядел как «\\ufeff--wf-tcp-out=...». Проверка пресета
(validate_preset_source_text) этот символ снимает — запуск должен вести себя
так же.
"""

from __future__ import annotations

from pathlib import Path
import shlex
import sys
import tempfile
import unittest


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


BOM = "﻿"


class LaunchArgsBomTests(unittest.TestCase):
    def test_bom_before_first_option_is_removed(self) -> None:
        from winws_runtime.runners.preset_runner_support import launch_args_from_preset_text

        args = launch_args_from_preset_text(BOM + "--wf-tcp-out=443\n--lua-desync=fake:blob=0x00\n")
        self.assertEqual(args, ["--wf-tcp-out=443", "--lua-desync=fake:blob=0x00"])

    def test_bom_before_first_comment_does_not_become_an_argument(self) -> None:
        from winws_runtime.runners.preset_runner_support import launch_args_from_preset_text

        args = launch_args_from_preset_text(BOM + "# Preset: test\n--wf-tcp-out=443\n")
        self.assertEqual(args, ["--wf-tcp-out=443"])

    def test_bom_only_stripped_at_start_of_text(self) -> None:
        from winws_runtime.runners.preset_runner_support import launch_args_from_preset_text

        args = launch_args_from_preset_text(BOM + "--wf-tcp-out=443\n--comment=a" + BOM + "b\n")
        self.assertEqual(args, ["--wf-tcp-out=443", "--comment=a" + BOM + "b"])

    def test_winws2_at_config_has_no_bom(self) -> None:
        from winws_runtime.runners.zapret2_runner import Winws2StrategyRunner

        runner = object.__new__(Winws2StrategyRunner)
        source = BOM + "# Preset: test\n--wf-tcp-out=443\n--filter-tcp=443\n--lua-desync=fake:blob=0x00\n"
        prepared = runner._prepare_preset_text_for_launch(source)
        config_text = runner._build_winws2_at_config_text(prepared)

        self.assertNotIn(BOM, config_text)
        self.assertEqual(shlex.split(config_text)[0], "--wf-tcp-out=443")

    def test_winws1_at_config_has_no_bom(self) -> None:
        from winws_runtime.runners.zapret1_runner import Winws1StrategyRunner

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = object.__new__(Winws1StrategyRunner)
            runner.work_dir = str(root)
            runner.lists_dir = str(root / "lists")
            runner.bin_dir = str(root / "bin")

            config_text = runner._build_winws1_at_config_text(
                BOM + "--wf-tcp=443\n--filter-tcp=443\n--dpi-desync=fake\n"
            )

        self.assertNotIn(BOM, config_text)
        self.assertEqual(shlex.split(config_text)[0], "--wf-tcp=443")


if __name__ == "__main__":
    unittest.main()
