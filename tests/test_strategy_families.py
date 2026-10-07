from __future__ import annotations

import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from profile.strategy_families import (
    STRATEGY_FAMILIES,
    strategy_count_text,
    strategy_family,
    strategy_family_key,
    strategy_family_keys,
)
from profile.strategy_state import ProfileStrategyState
from profile.strategy_visuals import strategy_technique_keys


CATALOGS = Path(__file__).resolve().parents[1] / "src" / "system" / "strategy_catalogs"


def _entry(name: str, *desync: str):
    return SimpleNamespace(name=name, args="\n".join(f"--lua-desync={value}" for value in desync))


def _catalog_entries(engine: str, name: str) -> dict:
    text = (CATALOGS / engine / f"{name}.txt").read_text(encoding="utf-8")
    entries = {}
    for match in re.finditer(r"^\[(.*?)\]\s*$(.*?)(?=^\[|\Z)", text, flags=re.M | re.S):
        body = match.group(2)
        title = re.search(r"^name\s*=\s*(.*)$", body, flags=re.M)
        entries[match.group(1)] = SimpleNamespace(
            name=title.group(1).strip() if title else match.group(1),
            args="\n".join(line for line in body.splitlines() if line.startswith("--")),
        )
    return entries


class StrategyFamilyKeyTests(unittest.TestCase):
    def test_single_techniques(self) -> None:
        self.assertEqual(strategy_family_key(("fake",)), "fake")
        self.assertEqual(strategy_family_key(("multisplit",)), "split")
        self.assertEqual(strategy_family_key(("split",)), "split")
        self.assertEqual(strategy_family_key(("multidisorder",)), "disorder")
        self.assertEqual(strategy_family_key(("hostfakesplit",)), "host")
        self.assertEqual(strategy_family_key(()), "other")
        self.assertEqual(strategy_family_key(("pass",)), "other")

    def test_fake_combined_with_split_or_disorder(self) -> None:
        self.assertEqual(strategy_family_key(("fake", "multisplit")), "fake_split")
        self.assertEqual(strategy_family_key(("fake", "multidisorder")), "fake_disorder")
        # Порядок строк в стратегии не важен: подмена остаётся подменой.
        self.assertEqual(strategy_family_key(("multisplit", "fake")), "fake_split")
        # Способы с поддельными частями сами относятся к «подмене и …».
        self.assertEqual(strategy_family_key(("fakedsplit",)), "fake_split")
        self.assertEqual(strategy_family_key(("fakemultidisorder",)), "fake_disorder")
        # Изменение окна — добавка, группу она не меняет.
        self.assertEqual(strategy_family_key(("wssize", "fake", "multisplit")), "fake_split")

    def test_prelude_and_host_name_win_over_the_rest(self) -> None:
        self.assertEqual(strategy_family_key(("send", "syndata", "multisplit")), "send")
        self.assertEqual(strategy_family_key(("send", "syndata", "hostfakesplit")), "send")
        self.assertEqual(strategy_family_key(("fake", "hostfakesplit")), "host")

    def test_udp_length_change_is_its_own_family(self) -> None:
        self.assertEqual(strategy_family_key(("fake", "udplen")), "fake_udplen")
        self.assertEqual(strategy_family_key(("fake", "tamper")), "fake")

    def test_every_family_has_russian_title_and_explanation(self) -> None:
        keys = [family.key for family in STRATEGY_FAMILIES]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(keys[-1], "other")
        for family in STRATEGY_FAMILIES:
            self.assertTrue(re.search("[а-яё]", family.title.lower()), family.key)
            self.assertTrue(re.search("[а-яё]", family.description), family.key)
            self.assertTrue(family.icon_name and family.color, family.key)
        self.assertEqual(strategy_family("no-such-family").key, "other")

    def test_count_text_uses_russian_plural_forms(self) -> None:
        self.assertEqual(strategy_count_text(1), "1 стратегия")
        self.assertEqual(strategy_count_text(3), "3 стратегии")
        self.assertEqual(strategy_count_text(11), "11 стратегий")
        self.assertEqual(strategy_count_text(97), "97 стратегий")


class StrategyFamilyCatalogTests(unittest.TestCase):
    def test_rare_technique_folds_into_other(self) -> None:
        entries = {f"fake-{n}": _entry(f"Fake {n}", "fake") for n in range(3)}
        entries["split-0"] = _entry("Split 0", "multisplit")
        entries["split-1"] = _entry("Split 1", "multisplit")

        keys = strategy_family_keys(entries)

        self.assertEqual({keys[f"fake-{n}"] for n in range(3)}, {"fake"})
        self.assertEqual(keys["split-0"], "other")
        self.assertEqual(keys["split-1"], "other")

    def test_real_tcp_catalog_splits_into_readable_groups(self) -> None:
        entries = _catalog_entries("winws2", "tcp")
        keys = strategy_family_keys(entries)
        counts: dict[str, int] = {}
        for key in keys.values():
            counts[key] = counts.get(key, 0) + 1

        self.assertEqual(sum(counts.values()), len(entries))
        self.assertGreaterEqual(len(counts), 6)
        self.assertTrue(all(count >= 3 for key, count in counts.items() if key != "other"))
        # Ни одна группа не забирает больше трети каталога: иначе деление
        # ничего не упрощает.
        self.assertLess(max(counts.values()), len(entries) / 3)
        self.assertLess(counts.get("other", 0), len(entries) / 10)





if __name__ == "__main__":
    unittest.main()
