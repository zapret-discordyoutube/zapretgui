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
    BADGE_WARNING,
    FILTER_ALL,
    FILTER_FAVORITE,
    FILTER_RECOMMENDED,
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
    try_stage,
    visible_rows,
)
from profile.strategy_list.plan import strategy_badge, usage_caption, usage_sentence
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
    def test_recommended_tab_is_one_flat_list_sorted_by_frequency(self) -> None:
        usage = {
            "split-03": StrategyUsage(same_service=2, services=2),
            "fake-05": StrategyUsage(same_service=9, services=9),
            "fake-01": StrategyUsage(same_service=2, services=30),
            "fake-02": StrategyUsage(same_service=0, services=40),
        }
        plan = _plan(usage=usage, quick_filter=FILTER_RECOMMENDED)

        # Одна группа на весь список: заголовков групп на вкладке нет.
        self.assertEqual([group.key for group in plan.groups], [RECOMMENDED_GROUP])
        self.assertEqual(_ids(plan, RECOMMENDED_GROUP), ["fake-05", "fake-01", "split-03"])
        self.assertEqual({row.kind for row in visible_rows(plan, open_groups=set())}, {ROW_STRATEGY})
        self.assertEqual(plan.queue[:3], ("fake-05", "fake-01", "split-03"))
        self.assertEqual(plan.recommended_count, 3)

    def test_whole_catalog_keeps_recommended_strategy_in_its_method_group(self) -> None:
        """Во «Все» советуемых отдельной группой нет: каждая стратегия лежит в группе своего способа."""
        usage = {"fake-05": StrategyUsage(same_service=9, services=9)}
        plan = _plan(usage=usage)

        self.assertNotIn(RECOMMENDED_GROUP, [group.key for group in plan.groups])
        self.assertEqual(_ids(plan, "fake")[0], "fake-05")
        self.assertEqual(plan.visible_count, plan.total_count)
        # Очередь перебора от вкладки не зависит.
        self.assertEqual(plan.queue[0], "fake-05")
        self.assertEqual(plan.recommended_count, 1)

    def test_tab_counts_describe_the_catalog_not_the_search(self) -> None:
        states = {
            "fake-01": ProfileStrategyState(rating="work"),
            "fake-02": ProfileStrategyState(rating="notwork", favorite=True),
        }
        usage = {"split-01": StrategyUsage(same_service=1, services=1), "fake-09": StrategyUsage(services=5)}
        plan = _plan(states=states, usage=usage, query="gamma")

        self.assertEqual(
            dict(plan.tab_counts),
            {FILTER_RECOMMENDED: 1, FILTER_WORKS: 1, FILTER_FAVORITE: 1, FILTER_ALL: len(_entries())},
        )
        self.assertEqual(plan.tab_count(FILTER_WORKS), 1)
        # Первой стоит вкладка, с которой начинает новичок, весь каталог — последним.
        self.assertEqual([key for key, _count in plan.tab_counts], [FILTER_RECOMMENDED, FILTER_WORKS, FILTER_FAVORITE, FILTER_ALL])

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
        self.assertEqual(_plan().tab_count(FILTER_RECOMMENDED), 0)
        everything = {strategy_id: StrategyUsage(same_service=1, services=1) for strategy_id in _entries()}
        plan = _plan(usage=everything)

        self.assertNotIn(RECOMMENDED_GROUP, [group.key for group in plan.groups])
        self.assertEqual(plan.recommended_count, 0)
        # Советовать весь каталог бессмысленно: вкладка пуста, значит её не будет.
        self.assertEqual(plan.tab_count(FILTER_RECOMMENDED), 0)
        self.assertEqual(_plan(usage=everything, quick_filter=FILTER_RECOMMENDED).visible_count, 0)

    def test_zapret1_catalog_without_lua_desync_is_one_flat_list(self) -> None:
        entries = {f"s{number}": _entry(f"Strategy {number}", "--dpi-desync=fake") for number in range(40)}
        plan = build_plan(PlanRequest(facts=build_strategy_facts(entries)))
        rows = visible_rows(plan, open_groups=set())

        self.assertEqual(len(plan.groups), 1)
        self.assertEqual({row.kind for row in rows}, {ROW_STRATEGY})
        self.assertEqual(len(rows), 40)


class BadgeTests(unittest.TestCase):
    def test_badge_only_warns_frequency_is_written_in_the_caption(self) -> None:
        # Стратегию ставят на этот же сервис: пометка каталога уже не нужна.
        self.assertEqual(strategy_badge(StrategyUsage(same_service=13, services=13), ""), ("", ""))
        self.assertEqual(strategy_badge(StrategyUsage(same_service=1, services=1), "caution"), ("", ""))
        self.assertEqual(strategy_badge(StrategyUsage(services=28), "caution"), ("осторожно", BADGE_WARNING))
        self.assertEqual(strategy_badge(StrategyUsage(services=28), ""), ("", ""))
        self.assertEqual(strategy_badge(StrategyUsage(services=28), "experimental"), ("", ""))
        self.assertEqual(strategy_badge(StrategyUsage(services=1), "experimental"), ("опытная", BADGE_NEUTRAL))
        self.assertEqual(strategy_badge(None, "stock"), ("", ""))

    def test_usage_caption_says_where_ready_presets_use_the_strategy(self) -> None:
        self.assertEqual(usage_caption(StrategyUsage(same_service=13, services=13)), "на этом сервисе в 13 пресетах")
        self.assertEqual(usage_caption(StrategyUsage(same_service=1, services=1)), "на этом сервисе в 1 пресете")
        self.assertEqual(usage_caption(StrategyUsage(services=28)), "на 28 сервисах")
        self.assertEqual(usage_caption(StrategyUsage(services=1)), "")
        self.assertEqual(usage_caption(None), "")

    def test_caption_drops_the_method_where_the_group_header_already_names_it(self) -> None:
        usage = {"fake-05": StrategyUsage(same_service=9, services=9), "twin-steam": StrategyUsage(services=4)}

        by_method = _plan(usage=usage)
        self.assertEqual(by_method.item("fake-05").caption, "на этом сервисе в 9 пресетах")
        self.assertEqual(by_method.item("twin-steam").caption, "из Steam · на 4 сервисах")
        self.assertEqual(by_method.item("fake-06").caption, "")
        # На вкладке «Советуемые» и при другой группировке способа в заголовке нет.
        recommended = _plan(usage=usage, quick_filter=FILTER_RECOMMENDED)
        self.assertEqual(recommended.item("fake-05").caption, "подделка · на этом сервисе в 9 пресетах")
        self.assertEqual(_plan(usage=usage, grouping=GROUPING_SERIES).item("fake-06").caption, "подделка")
        self.assertIn("на этом сервисе в 9 пресетах", by_method.item("fake-05").accessible_text)

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
        self.assertTrue(plan.narrowed_by_query)
        self.assertEqual([section.key for group in plan.groups for section in group.sections], [""])
        self.assertTrue(all(not item.twin_key for group in plan.groups for item in group.items))

    def test_tab_folds_same_named_strategies_like_the_whole_catalog(self) -> None:
        """Вкладка — не поиск: одноимённые стратегии на ней сложены в одну строку."""
        states = {key: ProfileStrategyState(favorite=True) for key in ("twin-steam", "twin-telegram")}
        plan = _plan(states=states, quick_filter=FILTER_FAVORITE)

        self.assertTrue(plan.narrowed)
        self.assertFalse(plan.narrowed_by_query)
        self.assertEqual(len({item.twin_key for group in plan.groups for item in group.items}), 1)
        self.assertEqual(len([row for row in visible_rows(plan) if row.kind == ROW_STRATEGY]), 1)

    def test_quick_filters(self) -> None:
        states = {
            "fake-01": ProfileStrategyState(rating="work"),
            "fake-02": ProfileStrategyState(rating="notwork", favorite=True),
        }
        usage = {"split-01": StrategyUsage(same_service=1, services=1)}

        self.assertEqual(_plan(states=states, quick_filter=FILTER_WORKS).visible_count, 1)
        self.assertEqual(_ids(_plan(states=states, quick_filter=FILTER_FAVORITE), "fake"), ["fake-02"])
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
        # Стратегия не выбрана — открывается первая группа каталога.
        self.assertEqual(default_open_group(_plan(usage={"fake-01": StrategyUsage(same_service=1, services=1)}), None), "fake")


class FullQueueTests(unittest.TestCase):
    """Перебор не кончается на советуемых: очередь проходит весь каталог."""

    def test_queue_covers_whole_catalog_once_and_skips_pass(self) -> None:
        entries = _entries()
        entries["pass"] = _entry("Pass · ничего не делает", "--lua-desync=pass")
        plan = build_plan(
            PlanRequest(facts=build_strategy_facts(entries), usage={"fake-05": StrategyUsage(same_service=3, services=3)})
        )

        self.assertEqual(len(plan.queue), len(set(plan.queue)))
        self.assertEqual(set(plan.queue), set(entries) - {"pass"})

    def test_after_recommended_come_used_elsewhere_then_methods_take_turns(self) -> None:
        usage = {
            "fake-05": StrategyUsage(same_service=3, services=3),
            "split-07": StrategyUsage(services=4),
            "fake-02": StrategyUsage(services=9),
        }
        plan = _plan(usage=usage)
        facts = build_strategy_facts(_entries())

        self.assertEqual(plan.queue[:3], ("fake-05", "fake-02", "split-07"))
        # Дальше способы чередуются: подряд не идут две стратегии одного способа,
        # пока есть другие способы.
        families = [facts[strategy_id].family_key for strategy_id in plan.queue[3:9]]
        self.assertEqual(families, ["fake", "fake_split", "split", "fake", "fake_split", "split"])

    def test_stage_switches_to_whole_catalog_when_recommended_are_rated(self) -> None:
        plan = _plan(usage={"fake-05": StrategyUsage(same_service=3, services=3), "fake-01": StrategyUsage(same_service=1, services=1)})

        self.assertEqual(try_stage(plan, {}), ("recommended", 0, 2))
        rated = {key: ProfileStrategyState(rating="notwork") for key in ("fake-05", "fake-01")}
        self.assertEqual(try_stage(plan, rated), ("all", 2, len(_entries())))
        self.assertNotIn(next_to_try(plan.queue, rated, "fake-05"), ("", "fake-05", "fake-01"))


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

    def test_fast_catalog_lookup_agrees_with_profile_strategy_resolution(self) -> None:
        """Подсчёт ищет стратегию по словарю; ответ обязан совпадать с тем, как
        программа определяет выбранную стратегию профиля."""
        from profile.derived_cache import catalog_name_for_profile, resolve_strategy_lines
        from profile.strategy_usage import _CatalogIndex

        catalogs = {
            path.stem.lower(): _parse_catalog_file(path, path.stem.lower())
            for path in (SRC / "system/strategy_catalogs/winws2").glob("*.txt")
        }
        indexes = {name: _CatalogIndex("winws2", entries) for name, entries in catalogs.items()}
        checked = 0
        for path in sorted((SRC / "presets/builtin/winws2").glob("*.txt"))[::7]:
            for profile in parse_preset_text(path.read_text(encoding="utf-8"), engine="winws2", source_name=path.name).profiles:
                catalog = catalog_name_for_profile(profile)
                lines = profile.strategy.strategy_lines
                expected, _name = resolve_strategy_lines(profile, catalogs.get(catalog, {}), lines)
                found = indexes[catalog].strategy_id(lines) if catalog in indexes else ""
                self.assertEqual(found, "" if expected in ("none", "custom") else expected, (path.name, profile.name))
                checked += 1
        self.assertGreater(checked, 300)

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


class LearningOrderTests(unittest.TestCase):
    """Программа учится на отметках человека с других профилей."""

    def test_queue_starts_with_proven_and_ends_with_failed_elsewhere(self) -> None:
        usage = {"fake-05": StrategyUsage(same_service=3, services=3)}
        plan = _plan(usage=usage, experience={"split-04": (1, 0), "split-09": (3, 1), "fake-08": (0, 2), "mix-1": (1, 4)})

        self.assertEqual(plan.queue[:3], ("split-09", "split-04", "fake-05"))
        self.assertEqual(plan.queue[-2:], ("fake-08", "mix-1"))
        self.assertEqual(len(plan.queue), len(set(plan.queue)))

    def test_method_that_keeps_failing_is_tried_after_other_methods(self) -> None:
        from profile.strategy_list.plan import family_scores

        facts = build_strategy_facts(_entries())
        states = {key: ProfileStrategyState(rating="notwork") for key in ("fake-00", "fake-01", "fake-02")}
        plan = _plan(states=states)
        families = [facts[strategy_id].family_key for strategy_id in plan.queue[:6]]

        self.assertEqual(family_scores(facts, states, {}), {"fake": -3})
        # «Подделка» трижды не помогла: в каждом круге она теперь последняя.
        self.assertEqual(families, ["fake_split", "split", "fake", "fake_split", "split", "fake"])

    def test_personal_badge_needs_more_successes_than_failures(self) -> None:
        self.assertEqual(strategy_badge(StrategyUsage(same_service=5, services=5), "", (2, 1)), ("у вас работает · 2", "personal"))
        self.assertEqual(strategy_badge(StrategyUsage(same_service=5, services=5), "", (1, 3)), ("", ""))


class KnowledgeTests(unittest.TestCase):
    def test_steps_follow_strategy_lines_in_order(self) -> None:
        from profile.strategy_list.knowledge import explain_strategy

        steps = explain_strategy(
            "--lua-desync=fake:blob=fake_default_tls:tcp_md5:repeats=6:tls_mod=rnd,dupsid,sni=www.google.com\n"
            "--out-range=-d8\n"
            "--lua-desync=multisplit:pos=1,midsld,sniext+1:seqovl=5"
        )
        notes = [{note.kind: (note.label, note.value) for note in step.notes} for step in steps]

        self.assertEqual([(step.function, step.family) for step in steps], [("fake", "fake"), ("multisplit", "split")])
        self.assertEqual(steps[0].title, "Подделка")
        self.assertEqual(notes[0]["content"], ("Что в подделке", "стандартный запрос защищённого соединения к www.microsoft.com"))
        self.assertEqual(notes[0]["repeats"], ("Повторы", "6 раз подряд"))
        self.assertEqual(notes[0]["protection"], ("Защита подделки от сайта", "Лишняя подпись"))
        self.assertIn("имя сайта www.google.com", notes[0]["tweak"][1])
        # Подделка защищена подписью — предупреждения о незащищённой подделке нет.
        self.assertEqual(steps[0].cautions, ())
        self.assertEqual(
            notes[1]["cut"],
            ("Где режется запрос", "после 1-го байта; посередине основной части имени сайта; у поля с именем сайта (sniext+1)"),
        )
        self.assertEqual(notes[1]["overlap"][1], "Заглушка перед первой частью")

    def test_every_step_says_what_happens_and_why_in_plain_words(self) -> None:
        from profile.strategy_list.knowledge import _FUNCTIONS, explain_strategy

        for function in ("fake", "multisplit", "multidisorder", "fakedsplit", "hostfakesplit", "oob", "wssize"):
            step = explain_strategy(f"--lua-desync={function}")[0]
            self.assertTrue(step.text.endswith("."), function)
            self.assertTrue(step.why, function)
            # Объяснение самодостаточно: не ссылается на другой шаг и не называет функций движка.
            self.assertNotIn("Как ", step.text[:4], function)
            self.assertFalse(any(name in f"{step.text} {step.why}" for name in _FUNCTIONS), function)

    def test_warnings_are_separate_and_only_where_needed(self) -> None:
        from profile.strategy_list.knowledge import explain_strategy

        plain = explain_strategy("--lua-desync=fake:blob=x")[0]
        ttl = explain_strategy("--lua-desync=fake:blob=x:ip_ttl=4")[0]
        both = explain_strategy("--lua-desync=hostfakesplit:host=a.ru:tcp_ts=-1000:ip_ttl=3")[0]
        self.assertEqual(
            next(note for note in both.notes if note.kind == "names").text,
            "Образец поддельного имени: a.ru — Перед ним подставляется случайная часть.",
        )

        self.assertEqual(len(plain.cautions), 1)
        self.assertIn("нет защиты от сайта", plain.cautions[0])
        self.assertEqual(len(ttl.cautions), 1)
        self.assertIn("У другого подделка может дойти до сайта", ttl.cautions[0])
        self.assertEqual(len(both.cautions), 3)
        self.assertEqual(both.caution, " ".join(both.cautions))

    def test_texts_follow_zapret2_sources(self) -> None:
        """Поправки по сверке с lua/zapret-antidpi.lua, lua/zapret-lib.lua и docs/manual.md."""
        from profile.strategy_list.knowledge import explain_strategy

        def step(line: str):
            return explain_strategy(f"--lua-desync={line}")[0]

        def note(line: str, kind: str):
            return next(item for item in step(line).notes if item.kind == kind)

        # Данные в первом пакете — «скрытая подделка»: защита ей не нужна.
        self.assertEqual(step("syndata:blob=x").cautions, ())
        # В UDP (QUIC, звонки) автор Zapret шлёт подделки без защиты.
        self.assertEqual(step("fake:blob=fake_default_quic:repeats=6").cautions, ())
        self.assertEqual(explain_strategy("--lua-desync=fake:blob=x", udp=True)[0].cautions, ())
        self.assertEqual(len(step("fake:blob=fake_default_tls").cautions), 1)
        # Особые заголовки IPv6 и своя функция порчи — тоже защита.
        self.assertEqual(step("fake:blob=x:ip6_hopbyhop").cautions, ())
        self.assertEqual(step("fake:blob=x:fool=my_fool").cautions, ())
        # У нарезки подделок нет: те же параметры меняют её собственные пакеты.
        self.assertEqual(note("multisplit:pos=2:tcp_md5", "protection").label, "Изменение пакетов шага")
        self.assertEqual(note("fake:blob=x:tcp_md5", "protection").label, "Защита подделки от сайта")
        # Перекрытие у перестановки устроено иначе, чем у нарезки.
        self.assertEqual(note("multisplit:pos=2:seqovl=5", "overlap").value, "Заглушка перед первой частью")
        self.assertEqual(note("multidisorder:pos=2:seqovl=1", "overlap").value, "Ложные байты перед второй частью")
        self.assertIn("не работает с сайтами на Windows", step("multidisorder:pos=2:seqovl=1").caution)
        # Метка времени, сдвинутая вперёд, устаревшей не выглядит.
        self.assertEqual(note("fake:blob=x:tcp_ts=-1000", "protection").value, "Старая метка времени")
        self.assertEqual(note("fake:blob=x:tcp_ts=1000", "protection").value, "Метка времени сдвинута вперёд")
        # Места разреза.
        self.assertEqual(note("multisplit:pos=method+2", "cut").value, "в начале названия запроса (GET, POST) (method+2)")
        self.assertEqual(note("multisplit:pos=-4", "cut").value, "за 4 байт до конца")
        self.assertEqual(note("tcpseg:pos=0,-1", "cut").label, "Какой кусок")
        # Отдельный кусок исходный пакет не заменяет; срочный байт — только для Linux.
        self.assertIn("Исходный пакет этот шаг не заменяет", step("tcpseg:pos=0,-1").caution)
        self.assertIn("только с сайтами на Linux", step("oob").caution)
        self.assertIn("ваш компьютер сам", step("wsize:wsize=1").text)
        self.assertIn("конец данных обрезается", step("udplen:increment=-2").caution)

    def test_oob_scene_puts_urgent_byte_at_the_start(self) -> None:
        from ui.onboarding.illustrations import SCENES

        self.assertEqual(SCENES["oob"].packets[0].text, "#youtube.com")

    def test_own_and_unknown_functions_are_named_honestly(self) -> None:
        from profile.strategy_list.knowledge import explain_strategy

        own, unknown = explain_strategy("--lua-desync=hostfakesplit_multi:hosts=a.ru,b.ru\n--lua-desync=tls_weird_thing")
        origin = next(note for note in own.notes if note.kind == "origin")

        self.assertEqual((origin.value, origin.detail), ("Добавлен в ZapretGUI", "В оригинальном Zapret 2 его нет."))
        self.assertIn("берутся по очереди из списка", own.text)
        self.assertTrue(own.why)
        self.assertEqual(next(note for note in own.notes if note.kind == "names").value, "a.ru, b.ru")
        self.assertEqual(unknown.title, "tls_weird_thing")
        self.assertIn("описания для неё нет", unknown.text)

    def test_every_catalog_function_gets_a_step(self) -> None:
        from profile.strategy_list.knowledge import explain_strategy

        catalog = _parse_catalog_file(SRC / "system/strategy_catalogs/winws2/tcp.txt", "tcp")
        for strategy_id, entry in catalog.items():
            lines = [line for line in entry.args.splitlines() if line.startswith("--lua-desync=")]
            self.assertEqual(len(explain_strategy(entry.args)), len(lines), strategy_id)


class PlacesTests(unittest.TestCase):
    def test_places_list_services_with_preset_counts(self) -> None:
        usage, (youtube, _discord) = BuiltinUsageTests()._usage(
            [("--lua-desync=fake:blob=x", "--lua-desync=fake:blob=x"), ("--lua-desync=fake:blob=x", "--lua-desync=multisplit:pos=1")]
        )

        places = usage.places("tcp", "fake")
        self.assertEqual([(name, presets) for _service, name, presets in places], [("YouTube", 2), ("Discord", 1)])
        self.assertEqual(usage.places("tcp", "missing"), ())
        # По строкам условий сервиса подбирается его значок.
        self.assertIn("--hostlist=lists/youtube.txt", usage.service_match_lines[places[0][0]])

    def test_steps_name_their_animated_scene(self) -> None:
        from profile.strategy_list.knowledge import explain_strategy
        from ui.onboarding.illustrations import SCENES

        steps = explain_strategy(
            "--lua-desync=fake:blob=x\n--lua-desync=fakeddisorder:pos=2\n--lua-desync=hostfakesplit_multi:hosts=a\n--lua-desync=wssize:wsize=1"
        )

        self.assertEqual([step.scene for step in steps], ["fake", "fakeddisorder", "hostfakesplit", ""])
        self.assertTrue(all(step.scene in SCENES for step in steps if step.scene))

    def test_fakedsplit_scene_follows_engine_packet_order(self) -> None:
        """По lua/zapret-antidpi.lua: каждая настоящая часть идёт между двумя своими подделками."""
        from ui.onboarding.illustrations import SCENES

        kinds = [packet.kind for packet in SCENES["fakedsplit"].packets]
        parts = [packet.part for packet in SCENES["fakedsplit"].packets if packet.kind == "real"]
        reverse_parts = [packet.part for packet in SCENES["fakeddisorder"].packets if packet.kind == "real"]

        self.assertEqual(kinds, ["fake", "real", "fake", "fake", "real", "fake"])
        self.assertEqual((parts, reverse_parts), (["1", "2"], ["2", "1"]))


class AnalyzerSceneTests(unittest.TestCase):
    """Схема приёма оформлена как анализатор трафика: под пакетом — его номер и длина."""

    @classmethod
    def setUpClass(cls) -> None:
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def _frames(self, scene: str, phase: float, width: int = 620):
        from ui.onboarding.illustrations import TechniqueIllustration

        widget = TechniqueIllustration(tr_fn=lambda _key, default: default)
        self.addCleanup(widget.deleteLater)
        widget.resize(width, widget.height())
        widget.set_scene(scene)
        widget.set_paused(True)
        widget.set_phase(phase)
        return widget, widget.chip_frames(phase)

    def _rows(self, scene: str, phase: float):
        widget, _frames = self._frames(scene, phase)
        return widget, {row.number: row for row in widget.log_rows(phase)}

    def test_log_row_appears_when_packet_leaves_and_fills_in_as_it_travels(self) -> None:
        from ui.onboarding.illustrations import STATIC_PHASE

        widget, early = self._rows("fake", 0.02)
        self.assertEqual(list(early), [1])
        self.assertEqual((early[1].label, early[1].kind, early[1].length), ("google.com", "fake", 10))
        self.assertEqual((early[1].gate[1], early[1].site[1]), ("wait", "wait"))

        _widget, done = self._rows("fake", STATIC_PHASE)
        # «Не принят» верно для любой защиты: подделка либо не дошла, либо отброшена сайтом.
        self.assertEqual((done[1].gate, done[1].site), (("принял за настоящий", "ok"), ("не принят", "drop")))
        self.assertEqual((done[2].label, done[2].gate, done[2].site), ("youtube.com", ("пропустил", "pass"), ("принят", "ok")))

    def test_blocked_packet_never_reaches_the_site(self) -> None:
        from ui.onboarding.illustrations import STATIC_PHASE

        _widget, rows = self._rows("blocked", STATIC_PHASE)

        self.assertEqual((rows[1].gate, rows[1].site), (("узнал имя — блок", "block"), ("—", "none")))

    def test_scheme_height_follows_number_of_packets(self) -> None:
        from ui.onboarding.illustrations import LOG_ROW_HEIGHT, TechniqueIllustration

        two = TechniqueIllustration.scene_height("fake")
        six = TechniqueIllustration.scene_height("fakedsplit")
        widget, _rows = self._rows("fakedsplit", 0.5)

        self.assertEqual(six - two, 4 * LOG_ROW_HEIGHT)
        self.assertEqual(widget.height(), six)

    def test_scene_runs_slowly_enough_to_read_captions(self) -> None:
        from ui.onboarding.illustrations import PERIOD_MS

        self.assertGreaterEqual(PERIOD_MS, 8000)
