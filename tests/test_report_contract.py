"""Отчёт BlockCheck и экран не расходятся: у каждого поля отчёта есть место.

Список полей — в ``diagnostics.report_contract``. Здесь две проверки:

- движок не кладёт в отчёт поле мимо списка (новое поле нельзя добавить молча);
- каждое показываемое поле действительно меняет экран: без него карточки
  получаются другими, а поля панели итога названы в её коде.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from test_diagnostics_verdict import _Net

from blockcheck.ui.result_cards_model import build_cards
from diagnostics import engine
from diagnostics.report_contract import CARD_FIELDS, SERVICE_FIELDS, SUMMARY_FIELDS, declared_fields

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tests" / "fixtures" / "blockcheck_report_sample.json"
# Что читает панель итога и страница.
SUMMARY_SOURCES = ("src/blockcheck/ui/check_results.py", "src/blockcheck/ui/page.py", "src/diagnostics/history.py")
# Что рабочий поток проверки дописывает к отчёту движка.
WORKER_SOURCE = "src/blockcheck/worker.py"


def _sample() -> dict:
    return json.loads(SAMPLE.read_text("utf-8"))


class ReportContractTests(unittest.TestCase):
    def test_every_field_has_exactly_one_place(self) -> None:
        places = [*CARD_FIELDS, *SUMMARY_FIELDS, *SERVICE_FIELDS]
        self.assertEqual(sorted(places), sorted(set(places)), "поле названо в двух местах")
        self.assertTrue(all(reason.strip() for reason in SERVICE_FIELDS.values()), "у служебного поля нет объяснения")

    def test_engine_report_has_no_field_outside_the_contract(self) -> None:
        for scope in ("main", "full"):
            partials: list[dict] = []
            report = _Net().run(engine.run_blockcheck, scope, emit=lambda _line: None, partial=partials.append)
            for item in (report, *partials):
                self.assertEqual(set(item) - declared_fields(), set(), f"{scope}: поле отчёта не внесено в договор")

    def test_fields_added_by_the_worker_are_in_the_contract(self) -> None:
        import re

        source = (ROOT / WORKER_SOURCE).read_text("utf-8")
        added = set(re.findall(r"""report\[["'](\w+)["']\]\s*=""", source))
        self.assertTrue(added, "не нашли, что рабочий поток дописывает к отчёту")
        self.assertEqual(added - declared_fields(), set())

    def test_sample_report_covers_every_shown_field(self) -> None:
        sample = _sample()
        self.assertEqual(set(sample) - declared_fields(), set())
        empty = [name for name in (*CARD_FIELDS, *SUMMARY_FIELDS) if name not in sample]
        self.assertEqual(empty, [], "в образце отчёта нет показываемого поля")
        # Пустое значение ничего не доказывает: карточка обязана быть построена из данных.
        self.assertEqual([name for name in CARD_FIELDS if not sample[name]], [])

    def test_each_card_field_really_changes_the_cards(self) -> None:
        sample = _sample()
        full = build_cards(sample)
        for name in CARD_FIELDS:
            with self.subTest(field=name):
                without = dict(sample)
                without[name] = None
                self.assertNotEqual(build_cards(without), full, f"поле {name} есть в отчёте, но на карточках его не видно")

    def test_each_summary_field_is_read_by_the_summary_code(self) -> None:
        source = "\n".join((ROOT / path).read_text("utf-8") for path in SUMMARY_SOURCES)
        for name in SUMMARY_FIELDS:
            with self.subTest(field=name):
                self.assertTrue(
                    f'"{name}"' in source or f"'{name}'" in source,
                    f"поле {name} объявлено показываемым, но панель итога его не читает",
                )


if __name__ == "__main__":
    unittest.main()
