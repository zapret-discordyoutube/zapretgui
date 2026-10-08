"""Отчёт из дата-классов сохраняется в JSON и возвращается тем же объектом."""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass

from utils.dataclass_json import from_plain, to_plain


@dataclass(frozen=True, slots=True)
class _Inner:
    name: str
    values: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class _Outer:
    title: str
    inner: _Inner | None = None
    items: tuple[_Inner, ...] = ()
    pair: tuple[str, int] = ("", 0)
    pairs: tuple[tuple[str, str], ...] = ()
    extra: dict | None = None
    score: float | None = None
    done: bool = False


class DataclassJsonTests(unittest.TestCase):
    def test_nested_report_survives_a_trip_through_json(self) -> None:
        report = _Outer(
            "отчёт",
            inner=_Inner("а", (1, 2)),
            items=(_Inner("б"), _Inner("в", (3,))),
            pair=("x", 7),
            pairs=(("k", "v"),),
            extra={"список": [1, 2]},
            score=1.5,
            done=True,
        )
        restored = from_plain(_Outer, json.loads(json.dumps(to_plain(report), ensure_ascii=False)))

        self.assertEqual(restored, report)
        self.assertIsInstance(restored.items[1].values, tuple)

    def test_old_and_newer_files_are_read_without_errors(self) -> None:
        # Поля, которых программа не знает, пропускаются; недостающие берутся по умолчанию.
        restored = from_plain(_Outer, {"title": "старый", "выдумано": 1, "inner": {"name": "а", "лишнее": True}})

        self.assertEqual(restored, _Outer("старый", inner=_Inner("а")))

    def test_broken_data_gives_nothing_instead_of_an_exception(self) -> None:
        self.assertIsNone(from_plain(_Outer, None))
        self.assertIsNone(from_plain(_Outer, ["не словарь"]))
        # Нет обязательного поля — отчёт не собрать.
        self.assertIsNone(from_plain(_Outer, {"done": True}))
        self.assertEqual(from_plain(_Outer, {"title": "т", "items": "не список"}).items, ())


if __name__ == "__main__":
    unittest.main()
