"""«Место фильтра»: поиск по нескольким сайтам, владельцы узлов и вывод для стратегий."""

from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

from blockcheck.ui.result_cards_model import FILTER_MARK, build_cards
from diagnostics import filter_place as fp
from diagnostics import path_trace as pt
from utils.windows_icmp import HOP_ROUTER, HOP_SILENT, HOP_TARGET

OWN_ASN = "8359"
OWNERS = {"212.188.1.1": (OWN_ASN, "MTS"), "195.34.50.1": (OWN_ASN, "MTS"), "62.115.1.1": ("1299", "Arelion")}


def _trace(target: str, addresses) -> pt.RouteTrace:
    hops = tuple(
        pt.Hop(ttl, HOP_ROUTER if address else HOP_SILENT, address, 2.0 * ttl if address else None)
        for ttl, address in enumerate(addresses, 1)
    )
    return pt.RouteTrace(target, hops + (pt.Hop(len(hops) + 1, HOP_TARGET, target, 30.0),), reached=True)


ROAD = ("192.168.1.1", "100.64.0.1", "212.188.1.1", "195.34.50.1", "62.115.1.1", "")


def _found(hop: int, distance: int = 7) -> pt.FilterFacts:
    return pt.FilterFacts(True, True, hop, hop, neutral_passes=True, next_blocked=True, distance=distance)


def _site(host: str, ip: str, *found, road=ROAD) -> fp.SiteFacts:
    return fp.SiteFacts(host, ip, _trace(ip, road), tuple(found))


def _hops():
    return fp.describe_hops(_trace("5.5.5.5", ROAD), own_asn=OWN_ASN, owner_of=OWNERS.get)


class CandidateTests(unittest.TestCase):
    def test_only_sites_blocked_by_name_with_a_public_ipv4_address_are_used(self) -> None:
        picked = fp.pick_candidates(
            [
                ("open.example", "1.1.1.1", False, False),
                ("quic.example", "2.2.2.2", True, False),
                ("both.example", "3.3.3.3", True, True),
                ("v6.example", "2001:db8::1", True, True),
                ("lan.example", "192.168.1.5", True, True),
                ("noaddr.example", "", True, True),
            ]
        )

        self.assertEqual([(item.host, item.methods) for item in picked], [("both.example", ("quic", "tcp")), ("quic.example", ("quic",))])

    def test_different_networks_go_first_and_one_address_is_used_once(self) -> None:
        sites = [(f"s{n}.example", f"10.{n}.0.1".replace("10.", "45."), True, False) for n in range(3)]
        sites.insert(1, ("same-net.example", "45.0.9.9", True, False))
        sites.append(("same-ip.example", "45.0.0.1", True, False))
        picked = fp.pick_candidates(sites, limit=3)

        self.assertEqual([item.host for item in picked], ["s0.example", "s1.example", "s2.example"])
        self.assertEqual(len(fp.pick_candidates(sites, limit=9)), 4)


class SiteJudgeTests(unittest.TestCase):
    def test_two_methods_naming_one_hop_agree(self) -> None:
        verdict = fp.judge_site(_site("x.com", "5.5.5.5", ("quic", _found(4)), ("tcp", _found(4))))

        self.assertEqual((verdict.code, verdict.hop, verdict.agreeing, verdict.distance), (pt.FILTER_FOUND, 4, 2, 7))

    def test_two_methods_naming_different_hops_give_no_place(self) -> None:
        verdict = fp.judge_site(_site("x.com", "5.5.5.5", ("quic", _found(4)), ("tcp", _found(2))))

        self.assertEqual((verdict.code, verdict.hop), (pt.FILTER_UNSURE, None))
        self.assertIn("разные узлы", verdict.text)

    def test_one_method_failing_does_not_cancel_the_other(self) -> None:
        silent_filter = pt.FilterFacts(control_ok=True, stateful=False, distance=7)
        verdict = fp.judge_site(_site("x.com", "5.5.5.5", ("tcp", silent_filter), ("quic", _found(4))))

        self.assertEqual((verdict.code, verdict.hop, verdict.agreeing), (pt.FILTER_FOUND, 4, 1))

    def test_server_distance_is_the_smallest_of_what_was_measured(self) -> None:
        # Пинг дошёл за 7 узлов, по TCP сервер на 6-м: фильтр на 6-м назвать нельзя.
        verdict = fp.judge_site(_site("x.com", "5.5.5.5", ("quic", _found(6, distance=6))))

        self.assertEqual((verdict.code, verdict.hop, verdict.distance), (pt.FILTER_AT_TARGET, None, 6))

    def test_cancelled_search_gives_no_verdict(self) -> None:
        self.assertIsNone(fp.judge_site(_site("x.com", "5.5.5.5", ("quic", pt.FilterFacts(cancelled=True)))))


class HopOwnerTests(unittest.TestCase):
    def test_hops_are_named_by_whose_network_they_are(self) -> None:
        hops = _hops()

        self.assertEqual(
            [(hop.ttl, hop.owner_kind) for hop in hops],
            [(1, "router"), (2, "provider"), (3, "provider"), (4, "provider"), (5, "other"), (6, "silent"), (7, "unknown")],
        )
        self.assertEqual(hops[0].owner, "ваш роутер")
        self.assertIn("CGNAT", hops[1].owner)
        self.assertEqual(hops[2].owner, "ваш провайдер (MTS)")
        self.assertEqual(hops[4].owner, "Arelion (AS1299)")

    def test_private_address_beyond_the_first_hop_is_the_provider_not_the_router(self) -> None:
        hops = fp.describe_hops(_trace("5.5.5.5", ("192.168.1.1", "10.20.0.1")))

        self.assertEqual([hop.owner for hop in hops[:2]], ["ваш роутер", "внутренняя сеть провайдера"])

    def test_unsupported_trace_gives_no_hops(self) -> None:
        self.assertEqual(fp.describe_hops(pt.RouteTrace("5.5.5.5", supported=False)), ())
        self.assertEqual(fp.describe_hops(None), ())


class AggregateTests(unittest.TestCase):
    def _verdicts(self, *hops, distance=7, agreeing=1):
        return [
            fp.SiteVerdict(f"s{n}.example", f"45.{n}.0.1", pt.FILTER_FOUND, hop, distance, f"узел {hop}", agreeing=agreeing)
            for n, hop in enumerate(hops)
        ]

    def test_three_sites_on_one_hop_is_a_confident_place_in_the_provider_network(self) -> None:
        place = fp.aggregate(self._verdicts(4, 4, 4), _hops())

        self.assertEqual((place.state, place.hop, place.confidence, place.distance), ("found", 4, "high", 7))
        self.assertIn("между узлами 3 и 4 от вас — в сети вашего провайдера", place.sentence)
        self.assertIn("совпало по 3 сайтам", place.sentence)

    def test_confidence_follows_how_much_agreed(self) -> None:
        self.assertEqual(fp.aggregate(self._verdicts(4), _hops()).confidence, "low")
        self.assertEqual(fp.aggregate(self._verdicts(4, 4), _hops()).confidence, "medium")
        self.assertEqual(fp.aggregate(self._verdicts(4, agreeing=2), _hops()).confidence, "medium")
        self.assertEqual(fp.aggregate(self._verdicts(4, 4, agreeing=2), _hops()).confidence, "high")

    def test_ttl_range_for_fakes_is_from_the_filter_to_just_before_the_server(self) -> None:
        self.assertIn("от 4 до 6", fp.aggregate(self._verdicts(4), _hops()).ttl_advice)
        self.assertIn("ровно 4", fp.aggregate(self._verdicts(4, distance=5), _hops()).ttl_advice)
        self.assertIn("не меньше 4", fp.aggregate(self._verdicts(4, distance=0), _hops()).ttl_advice)
        # Берётся ближайший сервер: поддельный пакет не должен дойти ни до одного.
        mixed = self._verdicts(4) + [fp.SiteVerdict("near.example", "46.0.0.1", pt.FILTER_FOUND, 4, 6, "узел 4")]
        self.assertIn("от 4 до 5", fp.aggregate(mixed, _hops()).ttl_advice)

    def test_place_is_named_by_the_border_it_stands_on(self) -> None:
        self.assertIn("на стыке с Arelion (AS1299)", fp.aggregate(self._verdicts(5), _hops()).sentence)
        self.assertIn("в вашем роутере или на самом компьютере", fp.aggregate(self._verdicts(1), _hops()).sentence)
        # Узлы неизвестны — место названо числом, без догадок о владельце.
        self.assertNotIn("провайдер", fp.aggregate(self._verdicts(4), ()).sentence)

    def test_sites_disagreeing_give_no_single_place(self) -> None:
        place = fp.aggregate(self._verdicts(3, 5), _hops())

        self.assertEqual((place.state, place.hop, place.ttl_advice), ("disagree", None, ""))
        self.assertIn("узел 3, узел 5", place.sentence)

    def test_first_hop_with_a_bypass_tool_running_is_the_tool_not_the_provider(self) -> None:
        for kwargs in ({"zapret_running": True}, {"other_tools": ("Xray",)}):
            place = fp.aggregate(self._verdicts(1, 1), _hops(), **kwargs)
            self.assertEqual((place.state, place.hop), ("disturbed", None))
        self.assertEqual(fp.aggregate(self._verdicts(1, 1), _hops()).state, "found")

    def test_running_bypass_tool_lowers_the_confidence_of_a_farther_place(self) -> None:
        place = fp.aggregate(self._verdicts(4, 4, 4), _hops(), zapret_running=True)

        # Три сайта сошлись: уверенность была бы высокой, с работающим обходом — на ступень ниже.
        self.assertEqual((place.state, place.confidence), ("found", "medium"))
        self.assertEqual(fp.aggregate(self._verdicts(4, 4), _hops(), zapret_running=True).confidence, "low")
        self.assertIn("Zapret", place.reasons[-1])

    def test_nothing_found_and_nothing_to_search_are_different_answers(self) -> None:
        nothing = fp.aggregate([], _hops(), zapret_running=True)
        self.assertEqual(nothing.state, "none")
        self.assertIn("работает Zapret", nothing.sentence)
        at_target = fp.SiteVerdict("x.com", "5.5.5.5", pt.FILTER_AT_TARGET, None, 7, "гаснет только у сервера")
        missed = fp.aggregate([at_target], _hops())
        self.assertEqual(missed.state, "not_found")
        self.assertIn("гаснет только у сервера", missed.sentence)

    def test_nothing_found_while_a_bypass_tool_runs_is_blamed_on_the_tool(self) -> None:
        # Zapret переделывает пакеты с запрещённым именем: «фильтра нет» при нём ничего не значит.
        silent = fp.SiteVerdict("x.com", "5.5.5.5", pt.FILTER_NOT_STATEFUL, None, 7, "фильтр не запоминает соединение")
        for kwargs in ({"zapret_running": True}, {"other_tools": ("Xray",)}):
            place = fp.aggregate([silent], _hops(), **kwargs)
            self.assertEqual(place.state, "disturbed")
            self.assertIn("Остановите", place.sentence)
        self.assertEqual(fp.aggregate([silent], _hops(), zapret_running=False).state, "not_found")


class CollectAndReportTests(unittest.TestCase):
    def _run(self):
        candidates = [fp.Candidate("a.example", "45.1.0.1", ("quic", "tcp")), fp.Candidate("b.example", "45.2.0.1", ("quic",))]
        asked: list[tuple[str, str, str]] = []

        def locate(method, host, ip, traced):
            # Дорога к этому времени уже известна: по ней поиск знает расстояние до сервера.
            assert traced is not None and traced.reached
            asked.append((method, host, ip))
            return _found(4)

        with ThreadPoolExecutor(8) as pool:
            facts = fp.collect(candidates, locate=locate, trace=lambda ip: _trace(ip, ROAD), submit=pool.submit)
        return facts, asked

    def test_every_site_is_searched_by_each_of_its_methods(self) -> None:
        facts, asked = self._run()

        self.assertEqual(sorted(asked), [("quic", "a.example", "45.1.0.1"), ("quic", "b.example", "45.2.0.1"), ("tcp", "a.example", "45.1.0.1")])
        self.assertEqual([len(item.found) for item in facts], [2, 1])
        self.assertTrue(all(item.trace.reached for item in facts))

    def test_report_text_and_card_show_the_place_the_road_and_the_ttl(self) -> None:
        facts, _asked = self._run()
        verdicts = [fp.judge_site(item) for item in facts]
        hops = _hops()
        place = fp.report(fp.aggregate(verdicts, hops), verdicts, hops, verdicts[0])

        self.assertEqual((place["found"], place["hop"], place["confidence"], place["host"]), (True, 4, "high", "a.example"))
        self.assertEqual([site["hop"] for site in place["sites"]], [4, 4])
        text = "\n".join(fp.lines(place))
        self.assertIn("📍 Фильтр стоит между узлами 3 и 4 от вас", text)
        self.assertIn("👉 Поддельным пакетам стратегии подходит срок жизни (TTL) от 4 до 6", text)
        self.assertLess(text.index("── здесь стоит фильтр ──"), text.index(" 4. 195.34.50.1"))
        self.assertIn(" 3. 212.188.1.1 · 6 мс · ваш провайдер (MTS)", text)

        [card] = build_cards({"filter": place})
        self.assertEqual((card.level, card.status), ("warn", "Между узлами 3 и 4"))
        self.assertEqual(card.lines[1].name, "Для стратегий")
        titles = [section.title for section in card.sections]
        self.assertEqual(titles[:3], ["Вывод", "По каким сайтам искали", "На чём основан вывод"])
        road = card.sections[-1].lines
        mark = next(index for index, line in enumerate(road) if FILTER_MARK in line.name)
        self.assertEqual(road[mark + 1].name, "Узел 4")
        self.assertIn("ваш провайдер (MTS)", road[mark].text if False else road[mark - 1].text)

    def test_card_without_blocks_shows_the_road_as_plain_information(self) -> None:
        hops = _hops()
        place = fp.report(fp.aggregate([], hops), [], hops, None)
        place["host"], place["address"] = "www.youtube.com", "5.5.5.5"
        [card] = build_cards({"filter": place})

        self.assertEqual((card.level, card.status), ("info", "Блокировок по имени нет"))
        self.assertEqual(card.lines[-1].name, "Дорога показана до")
        self.assertFalse(any(FILTER_MARK in line.name for line in card.sections[-1].lines))


if __name__ == "__main__":
    unittest.main()
