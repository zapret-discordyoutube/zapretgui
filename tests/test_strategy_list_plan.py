"""Список готовых стратегий без окон: сведения, раскладка, видимые строки, частота."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from profile.parser import parse_preset_text
from profile.strategy_catalog import _parse_catalog_file
from profile.strategy_list import (
    BADGE_NEUTRAL,
    BADGE_RECOMMENDED,
    BADGE_WARNING,
    FILTER_FAVORITE,
    FILTER_RECOMMENDED,
    FILTER_UNTRIED,
    FILTER_WORKS,
    GROUPING_SERIES,
    GROUPING_SOURCE,
    RECOMMENDED_GROUP,
    ROW_GROUP,
    ROW_SECTION,
    ROW_STRATEGY,
    PlanRequest,
    build_plan,
    build_strategy_facts,
    default_open_group,
    next_to_try,
    try_progress,
    visible_rows,
)
from profile.strategy_list.plan import strategy_badge, usage_sentence
from profile.strategy_state import ProfileStrategyState
from profile.strategy_usage import StrategyUsage, count_builtin_strategy_usage

SRC = Path(__file__).resolve().parents[1] / "src"


def _entry(name: str, args: str, **extra):
    return SimpleNamespace(name=name, args=args, visual=None, payload_scopes=(), old_name="", **extra)


def _entries() -> dict:
    entries = {}
    for number in range(12):
        entries[f"fake-{number:02d}"] = _entry(f"Alpha v{number}", "--lua-desync=fake:blob=x")
    for number in range(12):
        entries[f"split-{number:02d}"] = _entry(f"Beta v{number}", "--lua-desync=multisplit:pos=1")
    for source in ("Steam", "Telegram", "Discord"):
        entries[f"twin-{source.lower()}"] = _entry(
            f"Gamma 1.9 · из {source}", "--lua-desync=fake:blob=x\n--lua-desync=multisplit:pos=2"
        )
    for number in range(4):
        entries[f"mix-{number}"] = _entry(
            f"Delta v{number}", "--lua-desync=fake:blob=x\n--lua-desync=multisplit:pos=2"
        )
    return entries


def _plan(**kwargs):
    facts = kwargs.pop("facts", None) or build_strategy_facts(_entries())
    return build_plan(PlanRequest(facts=facts, **kwargs))


def _ids(plan, group_key: str) -> list[str]:
    return [item.strategy_id for group in plan.groups if group.key == group_key for item in group.items]


class FactsTests(unittest.TestCase):
    def test_name_is_split_into_title_detail_series_and_source(self) -> None:
        facts = build_strategy_facts(_entries())["twin-steam"]

        self.assertEqual((facts.title, facts.detail, facts.series, facts.source), ("Gamma 1.9", "из Steam", "Gamma", "Steam"))
        self.assertEqual(facts.family_key, "fake_split")

    def test_method_is_written_in_plain_words(self) -> None:
        facts = build_strategy_facts(_entries())

        self.assertEqual(facts["twin-steam"].plain_label, "подделка + нарезка")
        self.assertEqual(facts["twin-steam"].technical_label, "Fake + MultiSplit")
        self.assertEqual(facts["split-00"].plain_label, "нарезка")

    def test_unknown_method_keeps_technical_label(self) -> None:
        facts = build_strategy_facts({"x": _entry("Custom", "--lua-desync=my_own_function")})

        self.assertTrue(facts["x"].technical_label)
        self.assertEqual(facts["x"].plain_label, facts["x"].technical_label)

    def test_placeholder_descriptions_and_builtin_author_are_dropped(self) -> None:
        facts = build_strategy_facts(
            {
                "a": _entry("A", "--lua-desync=fake", description="Автодобавлено для распознавания пресета", author="builtin"),
                "b": _entry("B", "--lua-desync=fake", description="Режет вокруг имени сайта", author="hz", label="Caution"),
            }
        )

        self.assertEqual((facts["a"].description, facts["a"].author), ("", ""))
        self.assertEqual((facts["b"].description, facts["b"].author, facts["b"].label), ("Режет вокруг имени сайта", "hz", "caution"))
        self.assertIn("режет вокруг имени сайта", facts["b"].search_text)

    def test_real_catalog_reads_label_author_and_description(self) -> None:
        catalog = _parse_catalog_file(SRC / "system/strategy_catalogs/winws2/tcp.txt", "tcp")

        entry = catalog["fakemultisplit_simple"]
        self.assertEqual(entry.author, "Custom")
        self.assertTrue(entry.description.startswith("Базовый fakemultisplit"))
        self.assertIn("recommended", {item.label for item in catalog.values()})


class OrderTests(unittest.TestCase):
    def test_recommended_group_is_first_and_sorted_by_frequency(self) -> None:
        usage = {
            "split-03": StrategyUsage(same_service=2, services=2),
            "fake-05": StrategyUsage(same_service=9, services=9),
            "fake-01": StrategyUsage(same_service=2, services=30),
            "fake-02": StrategyUsage(same_service=0, services=40),
        }
        plan = _plan(usage=usage)

        self.assertEqual(plan.groups[0].key, RECOMMENDED_GROUP)
        self.assertEqual(_ids(plan, RECOMMENDED_GROUP), ["fake-05", "fake-01", "split-03"])
        self.assertEqual(plan.queue, ("fake-05", "fake-01", "split-03"))
        # Советуемая стратегия ушла из своей группы по способу, а не продублирована.
        self.assertNotIn("fake-05", _ids(plan, "fake"))
        self.assertEqual(plan.visible_count, plan.total_count)

    def test_inside_group_working_first_then_favorites_then_usage_and_failed_last(self) -> None:
        states = {
            "fake-07": ProfileStrategyState(rating="work"),
            "fake-03": ProfileStrategyState(favorite=True),
            "fake-00": ProfileStrategyState(rating="notwork"),
        }
        plan = _plan(states=states, usage={"fake-09": StrategyUsage(services=5)})

        order = _ids(plan, "fake")
        self.assertEqual(order[:3], ["fake-07", "fake-03", "fake-09"])
        self.assertEqual(order[-1], "fake-00")

    def test_no_recommended_group_without_usage_or_when_everything_is_used(self) -> None:
        self.assertNotIn(RECOMMENDED_GROUP, [group.key for group in _plan().groups])
        everything = {strategy_id: StrategyUsage(same_service=1, services=1) for strategy_id in _entries()}
        plan = _plan(usage=everything)

        self.assertNotIn(RECOMMENDED_GROUP, [group.key for group in plan.groups])
        self.assertEqual(plan.queue, ())

    def test_zapret1_catalog_without_lua_desync_is_one_flat_list(self) -> None:
        entries = {f"s{number}": _entry(f"Strategy {number}", "--dpi-desync=fake") for number in range(40)}
        plan = build_plan(PlanRequest(facts=build_strategy_facts(entries)))
        rows = visible_rows(plan, open_groups=set())

        self.assertEqual(len(plan.groups), 1)
        self.assertEqual({row.kind for row in rows}, {ROW_STRATEGY})
        self.assertEqual(len(rows), 40)


class BadgeTests(unittest.TestCase):
    def test_badge_prefers_same_service_then_caution_then_other_services(self) -> None:
        self.assertEqual(strategy_badge(StrategyUsage(same_service=13, services=13), ""), ("в 13 пресетах", BADGE_RECOMMENDED))
        self.assertEqual(strategy_badge(StrategyUsage(same_service=1, services=1), "caution"), ("в 1 пресете", BADGE_RECOMMENDED))
        self.assertEqual(strategy_badge(StrategyUsage(services=28), "caution"), ("осторожно", BADGE_WARNING))
        self.assertEqual(strategy_badge(StrategyUsage(services=28), ""), ("на 28 сервисах", BADGE_NEUTRAL))
        self.assertEqual(strategy_badge(StrategyUsage(services=1), "experimental"), ("опытная", BADGE_NEUTRAL))
        self.assertEqual(strategy_badge(None, "stock"), ("", ""))

    def test_usage_sentence_separates_this_service_from_others(self) -> None:
        self.assertEqual(
            usage_sentence(StrategyUsage(same_service=2, services=5)),
            "Стоит на этом сервисе в 2 готовых пресетах. В готовых пресетах встречается ещё на 4 сервисах.",
        )
        self.assertEqual(usage_sentence(StrategyUsage()), "")


class NarrowingTests(unittest.TestCase):
    def test_search_looks_at_plain_words_args_and_author(self) -> None:
        entries = _entries()
        entries["by-author"] = _entry("Epsilon", "--lua-desync=fake:blob=tls_google", author="Dronatar")
        facts = build_strategy_facts(entries)

        self.assertEqual(_ids(_plan(facts=facts, query="dronatar"), "fake"), ["by-author"])
        self.assertEqual(_ids(_plan(facts=facts, query="tls_google"), "fake"), ["by-author"])
        self.assertEqual(_plan(facts=facts, query="нарезка").visible_count, 12 + 3 + 4)

    def test_narrowed_plan_has_no_sections_and_no_twins(self) -> None:
        plan = _plan(query="gamma")

        self.assertTrue(plan.narrowed)
        self.assertEqual([section.key for group in plan.groups for section in group.sections], [""])
        self.assertTrue(all(not item.twin_key for group in plan.groups for item in group.items))

    def test_quick_filters(self) -> None:
        states = {
            "fake-01": ProfileStrategyState(rating="work"),
            "fake-02": ProfileStrategyState(rating="notwork", favorite=True),
        }
        usage = {"split-01": StrategyUsage(same_service=1, services=1)}

        self.assertEqual(_plan(states=states, quick_filter=FILTER_WORKS).visible_count, 1)
        self.assertEqual(_ids(_plan(states=states, quick_filter=FILTER_FAVORITE), "fake"), ["fake-02"])
        self.assertEqual(_plan(states=states, quick_filter=FILTER_UNTRIED).visible_count, len(_entries()) - 2)
        self.assertEqual(_ids(_plan(usage=usage, quick_filter=FILTER_RECOMMENDED), RECOMMENDED_GROUP), ["split-01"])

    def test_groups_do_not_move_while_searching(self) -> None:
        """Раскладка считается по всему каталогу: поиск не переносит стратегию в другую группу."""
        full = _plan()
        found = _plan(query="alpha v1")

        self.assertEqual(found.group_of("fake-01"), full.group_of("fake-01"))


class GroupingTests(unittest.TestCase):
    def test_series_grouping_puts_big_series_first_and_small_ones_together(self) -> None:
        plan = _plan(grouping=GROUPING_SERIES)

        self.assertEqual([group.title for group in plan.groups], ["Alpha", "Beta", "Delta", "Gamma"])

    def test_source_grouping_drops_source_from_detail(self) -> None:
        entries = _entries()
        entries["twin-steam-2"] = _entry("Other 2.0 · из Steam", "--lua-desync=fake")
        plan = build_plan(PlanRequest(facts=build_strategy_facts(entries), grouping=GROUPING_SOURCE))
        steam = next(group for group in plan.groups if group.title == "Steam")

        self.assertEqual({item.detail for item in steam.items}, {""})
        self.assertEqual(plan.groups[-1].title, "Без источника")


class VisibleRowsTests(unittest.TestCase):
    def test_collapsed_group_has_no_rows_at_all(self) -> None:
        plan = _plan()
        rows = visible_rows(plan, open_groups={"split"})

        self.assertEqual([row.group_key for row in rows if row.kind == ROW_GROUP], ["fake", "fake_split", "split"])
        self.assertEqual({row.group_key for row in rows if row.kind == ROW_STRATEGY}, {"split"})
        self.assertEqual([row.expanded for row in rows if row.kind == ROW_GROUP], [False, False, True])

    def test_all_groups_are_open_when_open_groups_is_none_or_plan_is_narrowed(self) -> None:
        self.assertEqual(sum(row.kind == ROW_STRATEGY for row in visible_rows(_plan(), open_groups=None)), 12 + 12 + 1 + 4)
        narrowed = _plan(query="v1")
        self.assertEqual(
            sum(row.kind == ROW_STRATEGY for row in visible_rows(narrowed, open_groups=set())), narrowed.visible_count
        )

    def test_twins_collapse_into_one_row_and_expand_on_demand(self) -> None:
        plan = _plan()
        collapsed = [row for row in visible_rows(plan, open_groups={"fake_split"}) if row.kind == ROW_STRATEGY]
        head = next(row for row in collapsed if row.item.title == "Gamma 1.9")

        self.assertEqual(sum(row.item.title == "Gamma 1.9" for row in collapsed), 1)
        self.assertEqual((head.twin_count, head.twin_open), (3, False))

        opened = [
            row
            for row in visible_rows(plan, open_groups={"fake_split"}, open_twins={head.item.twin_key})
            if row.kind == ROW_STRATEGY and row.item.title == "Gamma 1.9"
        ]
        self.assertEqual([row.twin_count for row in opened], [3, 0, 0])
        self.assertTrue(opened[0].twin_open)

    def test_collapsed_twins_show_the_selected_variant(self) -> None:
        plan = _plan(current_strategy_id="twin-telegram")
        shown = [
            row.strategy_id
            for row in visible_rows(plan, open_groups={"fake_split"})
            if row.kind == ROW_STRATEGY and row.item.title == "Gamma 1.9"
        ]

        self.assertEqual(shown, ["twin-telegram"])

    def test_section_rows_are_not_selectable_and_keys_are_unique(self) -> None:
        rows = visible_rows(_plan(), open_groups=None)

        self.assertFalse(any(row.selectable for row in rows if row.kind == ROW_SECTION))
        self.assertEqual(len({row.key for row in rows}), len(rows))

    def test_default_open_group(self) -> None:
        plan = _plan(current_strategy_id="split-04", usage={"fake-01": StrategyUsage(same_service=1, services=1)})

        self.assertEqual(default_open_group(plan, None), "split")
        self.assertEqual(default_open_group(plan, "fake"), "fake")
        self.assertEqual(default_open_group(plan, ""), "")
        # Сохранённой группы больше нет в списке — открывается группа выбранной стратегии.
        self.assertEqual(default_open_group(plan, "s_gone"), "split")
        self.assertEqual(default_open_group(_plan(usage={"fake-01": StrategyUsage(same_service=1, services=1)}), None), RECOMMENDED_GROUP)


class TryQueueTests(unittest.TestCase):
    def test_next_to_try_skips_current_and_rated(self) -> None:
        queue = ("a", "b", "c", "d")
        states = {"b": ProfileStrategyState(rating="notwork")}

        self.assertEqual(next_to_try(queue, states, "a"), "c")
        self.assertEqual(next_to_try(queue, {key: ProfileStrategyState(rating="work") for key in queue}, "a"), "")
        self.assertEqual(try_progress(queue, states), (1, 4))


class BuiltinUsageTests(unittest.TestCase):
    PRESET = """--lua-init=@lua/zapret-lib.lua

--new
--name=YouTube
--filter-tcp=443
--hostlist=lists/youtube.txt
{youtube}

--new
--name=Discord
--filter-tcp=443
--hostlist=lists/discord.txt
{discord}
"""

    def _usage(self, presets):
        catalogs = {
            "tcp": {
                "split": SimpleNamespace(strategy_id="split", name="Split", args="--lua-desync=multisplit:pos=1", is_composite=False),
                "fake": SimpleNamespace(strategy_id="fake", name="Fake", args="--lua-desync=fake:blob=x", is_composite=False),
            }
        }
        texts = [(f"preset{number}.txt", self.PRESET.format(youtube=youtube, discord=discord)) for number, (youtube, discord) in enumerate(presets)]
        usage = count_builtin_strategy_usage(texts, engine="winws2", catalogs=catalogs)
        profiles = parse_preset_text(texts[0][1], engine="winws2", source_name="x").profiles
        return usage, profiles

    def test_counts_presets_per_service_and_number_of_services(self) -> None:
        split, fake = "--lua-desync=multisplit:pos=1", "--lua-desync=fake:blob=x"
        usage, (youtube, discord) = self._usage([(split, fake), (split, fake), (fake, fake)])

        on_youtube = usage.for_profile(youtube, "tcp")
        self.assertEqual(on_youtube["split"], StrategyUsage(same_service=2, services=1))
        self.assertEqual(on_youtube["fake"], StrategyUsage(same_service=1, services=2))
        on_discord = usage.for_profile(discord, "tcp")
        self.assertEqual(on_discord["fake"], StrategyUsage(same_service=3, services=2))
        self.assertEqual(on_discord["split"], StrategyUsage(same_service=0, services=1))

    def test_disabled_profile_does_not_vote(self) -> None:
        split, fake = "--lua-desync=multisplit:pos=1", "--lua-desync=fake:blob=x"
        usage, (youtube, _discord) = self._usage([(f"--skip\n{split}", fake), (fake, fake)])

        self.assertFalse(youtube.enabled)
        self.assertNotIn("split", usage.for_profile(youtube, "tcp"))
        self.assertEqual(usage.for_profile(youtube, "tcp")["fake"].same_service, 1)

    def test_unknown_strategy_lines_are_not_counted(self) -> None:
        usage, (youtube, _discord) = self._usage([("--lua-desync=something_else", "--lua-desync=fake:blob=x")])

        self.assertNotIn("split", usage.for_profile(youtube, "tcp"))
        self.assertEqual(usage.for_profile(youtube, "tcp")["fake"].same_service, 0)

    def test_real_builtin_presets_recommend_a_part_of_the_catalog(self) -> None:
        catalogs = {
            path.stem.lower(): _parse_catalog_file(path, path.stem.lower())
            for path in (SRC / "system/strategy_catalogs/winws2").glob("*.txt")
        }
        texts = [(path.name, path.read_text(encoding="utf-8")) for path in sorted((SRC / "presets/builtin/winws2").glob("*.txt"))]
        usage = count_builtin_strategy_usage(texts, engine="winws2", catalogs=catalogs)
        profile = next(
            profile
            for _name, text in texts
            for profile in parse_preset_text(text, engine="winws2", source_name="x").profiles
            if "googlevideo" in profile.name.lower()
        )
        recommended = [value for value in usage.for_profile(profile).values() if value.recommended]

        self.assertGreater(len(recommended), 5)
        self.assertLess(len(recommended), len(catalogs["tcp"]) // 2)


if __name__ == "__main__":
    unittest.main()
