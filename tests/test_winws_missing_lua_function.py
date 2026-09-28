"""Ошибка запуска «desync function 'X' does not exist» объясняется по делу.

Отсутствующий на диске lua-файл winws2 сообщает раньше другим текстом, поэтому
здесь причина — опечатка в имени функции (с подсказкой похожего имени) или
функция из lua-файла, который пресет не подключил. Совет «переустановите
программу» для опечатки вводил в заблуждение.
"""

from __future__ import annotations

import unittest

from winws_runtime.health.winws_exit_diagnosis import diagnose_winws_exit


def _diagnose(name: str):
    return diagnose_winws_exit(87, f"github version v1.0.3\ndesync function '{name}' does not exist\n")


class MissingLuaFunctionDiagnosisTests(unittest.TestCase):
    def test_typo_suggests_the_intended_function(self) -> None:
        diagnosis = _diagnose("hostfakespli")
        self.assertIn("опечатка", diagnosis.cause)
        self.assertIn("«hostfakesplit»", diagnosis.cause)
        self.assertIn("«hostfakesplit»", diagnosis.solution)
        self.assertNotIn("Переустановите", diagnosis.solution)

    def test_function_from_unloaded_file_names_the_lua_init_line(self) -> None:
        diagnosis = _diagnose("rst_flood")
        self.assertIn("lua/zapret-rst-flood.lua", diagnosis.cause)
        self.assertIn("--lua-init=@lua/zapret-rst-flood.lua", diagnosis.solution)

    def test_unknown_name_without_close_match(self) -> None:
        diagnosis = _diagnose("zzzqqq")
        self.assertIn("нет ни в одном lua-файле", diagnosis.cause)
        self.assertIn("--lua-desync", diagnosis.solution)


if __name__ == "__main__":
    unittest.main()
