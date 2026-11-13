"""Что можно сделать с видео: исправления выбираются по фактам обхода видеосерверов, у каждого свои."""

from __future__ import annotations

import unittest

from diagnostics import service_view as view
from diagnostics import video_fixes as fixes


def _node(code: str, *, group: str = "sn-xguxaxjvh-304e", provider: bool = True, family: str = "IPv4", owner: str = "узел в сети провайдера") -> dict:
    return {"host": f"rr1---{group}.googlevideo.com", "ip": "203.0.113.5", "family": family, "code": code, "group": group, "provider": provider, "owner": owner}


def _google(code: str, *, family: str = "IPv4") -> dict:
    return _node(code, group="sn-aj5go5-5i", provider=False, family=family, owner="узел в сети Google")


def _keys(found: list[dict]) -> list[str]:
    return [fix["key"] for fix in found]


class RecommendTests(unittest.TestCase):
    def test_nothing_to_offer_without_a_sweep_or_when_all_answer(self) -> None:
        self.assertEqual(fixes.recommend(None), [])
        self.assertEqual(fixes.recommend([_google("ok"), _node("ok")]), [])

    def test_silent_provider_cache_with_answering_google_offers_the_hosts_block(self) -> None:
        found = fixes.recommend([_node("silent"), _google("ok")])
        self.assertEqual(_keys(found), [fixes.FIX_BLOCK_CACHES, fixes.FIX_WAIT_RELOAD])
        self.assertEqual(found[0]["groups"], ["sn-xguxaxjvh-304e"])

    def test_google_nodes_are_never_offered_for_blocking(self) -> None:
        found = fixes.recommend([_google("silent"), _google("ok", family="IPv6")])
        self.assertNotIn(fixes.FIX_BLOCK_CACHES, _keys(found))

    def test_no_hosts_block_when_nothing_from_google_answers(self) -> None:
        """Закрыть кэш, когда уйти некуда, — оставить человека совсем без видео."""
        found = fixes.recommend([_node("silent"), _google("silent")])
        self.assertNotIn(fixes.FIX_BLOCK_CACHES, _keys(found))

    def test_group_with_one_answering_address_is_not_blocked(self) -> None:
        found = fixes.recommend([_node("silent"), _node("ok", family="IPv6"), _google("ok")])
        self.assertNotIn(fixes.FIX_BLOCK_CACHES, _keys(found))

    def test_cut_reply_offers_quic_off_before_the_wssize_preset(self) -> None:
        found = fixes.recommend([_google("cut_window"), _google("ok")])
        self.assertEqual(_keys(found)[:2], [fixes.FIX_QUIC_OFF, fixes.FIX_WSSIZE_PRESET])

    def test_quic_cut_by_name_alone_offers_quic_off(self) -> None:
        self.assertEqual(_keys(fixes.recommend([_google("ok"), _google("silent")], quic="blocked_by_name")), [fixes.FIX_QUIC_OFF])

    def test_ipv6_answers_ipv4_does_not(self) -> None:
        found = fixes.recommend([_google("silent"), _google("ok", family="IPv6")])
        self.assertIn(fixes.FIX_PREFER_IPV6, _keys(found))
        self.assertNotIn(fixes.FIX_PREFER_IPV6, _keys(fixes.recommend([_google("ok"), _google("silent", family="IPv6")])))


class ViewTests(unittest.TestCase):
    def test_every_fix_has_words_and_carries_its_data_for_the_button(self) -> None:
        all_keys = [fixes.FIX_BLOCK_CACHES, fixes.FIX_QUIC_OFF, fixes.FIX_WSSIZE_PRESET, fixes.FIX_PREFER_IPV6, fixes.FIX_WAIT_RELOAD]
        rows = view.video_fix_rows([{"key": key, "groups": ["sn-a", "sn-b"]} for key in all_keys])
        self.assertEqual(len(rows), len(all_keys))
        self.assertIn("sn-a, sn-b", rows[0].text)
        self.assertEqual([row.extra["key"] for row in rows], all_keys)

    def test_unknown_fix_is_skipped(self) -> None:
        self.assertEqual(view.video_fix_rows([{"key": "nope"}, "junk"]), ())

    def test_fix_rows_follow_the_sweep_in_target_rows(self) -> None:
        item = {"host": "rr1---sn-a.googlevideo.com", "ok": True, "state": "ok", "text": "открывается",
                "video_sweep": [_node("silent"), _google("ok")], "video_fixes": fixes.recommend([_node("silent"), _google("ok")])}
        titles = [row.title for row in view.target_rows(item)]
        self.assertLess(titles.index("Видеосерверы YouTube"), titles.index("Можно сделать: закрыть молчащие кэши провайдера"))


if __name__ == "__main__":
    unittest.main()
