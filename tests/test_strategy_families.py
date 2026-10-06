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
from profile.strategy_list_filter import build_profile_strategy_list_plan, strategy_list_groups
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

    def test_zapret1_catalog_has_no_lua_desync_and_stays_one_flat_list(self) -> None:
        entries = _catalog_entries("winws1", "tcp")
        self.assertTrue(entries)
        self.assertEqual(
            {strategy_technique_keys(entry.args) for entry in entries.values()},
            {()},
        )

        plan = build_profile_strategy_list_plan(entries=entries, states={}, current_strategy_id="none", search_text="")

        self.assertEqual(plan.groups, ())
        self.assertEqual(len(plan.rows), len(entries))
        self.assertEqual(
            [row.name.lower() for row in plan.rows],
            sorted(entry.name.lower() for entry in entries.values()),
        )


class StrategyListPlanGroupTests(unittest.TestCase):
    def _entries(self) -> dict:
        entries = {}
        for n in range(4):
            entries[f"split-{n}"] = _entry(f"Split {n}", "multisplit")
            entries[f"fake-{n}"] = _entry(f"Fake {n}", "fake")
            entries[f"host-{n}"] = _entry(f"Host {n}", "hostfakesplit")
        return entries

    def test_rows_go_by_family_then_favorite_then_name(self) -> None:
        states = {"fake-3": ProfileStrategyState(rating="", favorite=True)}

        plan = build_profile_strategy_list_plan(
            entries=self._entries(),
            states=states,
            current_strategy_id="none",
            search_text="",
        )

        self.assertEqual([group.key for group in plan.groups], ["fake", "split", "host"])
        self.assertEqual([group.count for group in plan.groups], [4, 4, 4])
        self.assertEqual(
            [row.strategy_id for row in plan.rows[:5]],
            ["fake-3", "fake-0", "fake-1", "fake-2", "split-0"],
        )
        self.assertEqual({row.family_key for row in plan.rows[:4]}, {"fake"})
        self.assertTrue(plan.rows[0].favorite)
        self.assertEqual(plan.groups[0].title, "Подмена пакета")

    def test_search_keeps_the_family_and_counts_only_matches(self) -> None:
        plan = build_profile_strategy_list_plan(
            entries=self._entries(),
            states={},
            current_strategy_id="none",
            search_text="2",
        )

        self.assertEqual([row.strategy_id for row in plan.rows], ["fake-2", "split-2", "host-2"])
        self.assertEqual([(group.key, group.count) for group in plan.groups], [("fake", 1), ("split", 1), ("host", 1)])
        self.assertEqual(plan.visible_count, 3)
        self.assertEqual(plan.total_count, 12)

    def test_single_group_gets_no_header(self) -> None:
        self.assertEqual(strategy_list_groups({"fake": 7}), ())
        self.assertEqual(strategy_list_groups({}), ())
        self.assertEqual(len(strategy_list_groups({"fake": 7, "split": 3})), 2)

    def test_group_header_reads_state_and_explanation(self) -> None:
        group = strategy_list_groups({"fake": 97, "split": 3})[0]

        self.assertEqual(
            group.accessible_text(expanded=False),
            "Группа Подмена пакета, 97 стратегий, свернута. "
            "Способ обхода: перед настоящими данными уходит поддельный пакет. "
            "Нажмите Enter или Пробел, чтобы свернуть или развернуть группу.",
        )
        self.assertIn("развернута", group.accessible_text(expanded=True))


if __name__ == "__main__":
    unittest.main()
