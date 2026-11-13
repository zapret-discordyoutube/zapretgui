"""Обход всех видеосерверов YouTube: код каждого узла и слова строки отчёта."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from diagnostics import block_cause, video_sweep
from diagnostics import service_view as view
from diagnostics.block_cause import HelloResult
from diagnostics.video_sweep import SweepFacts


def _facts(hello: str, small: str | None = None, *, family: str = video_sweep.FAMILY_V4, host: str = "rr1---sn-a.googlevideo.com") -> SweepFacts:
    ip = "2a00:1450::4" if family == video_sweep.FAMILY_V6 else "203.0.113.5"
    return SweepFacts(host, ip, family, HelloResult(hello), None if small is None else HelloResult(small))


def _node(code: str, *, family: str = video_sweep.FAMILY_V4, host: str = "rr1---sn-a.googlevideo.com") -> dict:
    return {"host": host, "ip": "203.0.113.5", "family": family, "code": code}


class JudgeNodeTests(unittest.TestCase):
    def test_answer_means_ok(self) -> None:
        self.assertEqual(video_sweep.judge_node(_facts(block_cause.HELLO_OK)), video_sweep.SWEEP_OK)
        # Отказ сервера тоже ответ: дорога открыта.
        self.assertEqual(video_sweep.judge_node(_facts(block_cause.HELLO_ALERT)), video_sweep.SWEEP_OK)

    def test_silence_after_connect_is_cut_unless_small_window_answers(self) -> None:
        self.assertEqual(video_sweep.judge_node(_facts(block_cause.HELLO_TIMEOUT, block_cause.HELLO_TIMEOUT)), video_sweep.SWEEP_SILENT)
        self.assertEqual(video_sweep.judge_node(_facts(block_cause.HELLO_TIMEOUT)), video_sweep.SWEEP_SILENT)
        self.assertEqual(video_sweep.judge_node(_facts(block_cause.HELLO_TIMEOUT, block_cause.HELLO_OK)), video_sweep.SWEEP_CUT_WINDOW)

    def test_reset_and_no_route_are_told_apart(self) -> None:
        self.assertEqual(video_sweep.judge_node(_facts(block_cause.HELLO_RESET, block_cause.HELLO_RESET)), video_sweep.SWEEP_RESET)
        self.assertEqual(video_sweep.judge_node(_facts(block_cause.HELLO_RESET, block_cause.HELLO_OK)), video_sweep.SWEEP_CUT_WINDOW)
        self.assertEqual(video_sweep.judge_node(_facts(block_cause.HELLO_CONNECT)), video_sweep.SWEEP_NO_ROUTE)

    def test_unknown_answer_is_unclear(self) -> None:
        self.assertEqual(video_sweep.judge_node(_facts(block_cause.HELLO_GARBAGE)), video_sweep.SWEEP_UNCLEAR)


class JudgeTests(unittest.TestCase):
    def test_ipv6_is_dropped_when_none_of_it_connects(self) -> None:
        """На машине без IPv6 «нет дороги» не значит «узел закрыт»: такие строки не показываем."""
        facts = (
            _facts(block_cause.HELLO_OK),
            _facts(block_cause.HELLO_CONNECT, family=video_sweep.FAMILY_V6),
            _facts(block_cause.HELLO_CONNECT, family=video_sweep.FAMILY_V6, host="rr4---sn-b.googlevideo.com"),
        )
        nodes = video_sweep.judge(facts)
        self.assertEqual([node.family for node in nodes], [video_sweep.FAMILY_V4])

    def test_ipv6_is_kept_when_some_of_it_answers(self) -> None:
        facts = (
            _facts(block_cause.HELLO_OK),
            _facts(block_cause.HELLO_OK, family=video_sweep.FAMILY_V6),
            _facts(block_cause.HELLO_CONNECT, family=video_sweep.FAMILY_V6, host="rr4---sn-b.googlevideo.com"),
        )
        self.assertEqual(len(video_sweep.judge(facts)), 3)


class CollectTests(unittest.TestCase):
    def _run(self) -> SimpleNamespace:
        # submit выполняет задачу сразу: пул настоящей проверки здесь не нужен.
        class _Future:
            def __init__(self, value) -> None:
                self._value = value

            def result(self):
                return self._value

        return SimpleNamespace(
            probe_cancel=object(),
            dns_cancelled=lambda: False,
            submit=lambda fn, *args: _Future(fn(*args)),
        )

    def test_small_window_is_tried_only_after_a_silent_hello(self) -> None:
        run = self._run()
        calls: list[str] = []

        def tls_hello(ip, name, **_kw):
            calls.append("hello")
            return HelloResult(block_cause.HELLO_TIMEOUT if name.startswith("rr1") else block_cause.HELLO_OK)

        def window_hello(ip, name, **_kw):
            calls.append("window")
            return HelloResult(block_cause.HELLO_OK)

        with (
            mock.patch.object(block_cause, "tls_hello", tls_hello),
            mock.patch.object(block_cause, "window_hello", window_hello),
            mock.patch.object(video_sweep, "_ipv6_address", return_value=""),
            mock.patch("diagnostics.video_nodes.node_owner", return_value=None),
            mock.patch("diagnostics.net_access.browser_ipv4", return_value=("203.0.113.5",)),
        ):
            result = video_sweep.collect(run, ("rr1---sn-a.googlevideo.com", "rr4---sn-b.googlevideo.com"))
        self.assertEqual(calls, ["hello", "window", "hello"])
        self.assertEqual([item.small is not None for item in result], [True, False])
        self.assertEqual([video_sweep.judge_node(item) for item in result], [video_sweep.SWEEP_CUT_WINDOW, video_sweep.SWEEP_OK])

    def test_not_more_than_the_limit_of_nodes(self) -> None:
        run = self._run()
        hosts = tuple(f"rr{n}---sn-x.googlevideo.com" for n in range(1, 9))
        with (
            mock.patch.object(block_cause, "tls_hello", return_value=HelloResult(block_cause.HELLO_OK)),
            mock.patch.object(video_sweep, "_ipv6_address", return_value=""),
            mock.patch("diagnostics.video_nodes.node_owner", return_value=None),
            mock.patch("diagnostics.net_access.browser_ipv4", return_value=("203.0.113.5",)),
        ):
            result = video_sweep.collect(run, hosts)
        self.assertEqual(len(result), video_sweep.SWEEP_MAX_NODES)


class NodeGroupTests(unittest.TestCase):
    def test_group_from_every_name_form(self) -> None:
        self.assertEqual(video_sweep.node_group("rr4---sn-xguxaxjvh-304e.googlevideo.com"), "sn-xguxaxjvh-304e")
        self.assertEqual(video_sweep.node_group("r2---sn-hxb54vo-3046.googlevideo.com"), "sn-hxb54vo-3046")
        self.assertEqual(video_sweep.node_group("RR12---SN-AJ5GOC-5J.c.youtube.com."), "sn-aj5goc-5j")

    def test_other_names_have_no_group(self) -> None:
        for host in ("googlevideo.com", "redirector.googlevideo.com", "www.youtube.com", "rr4---sn-x.example.com", ""):
            self.assertEqual(video_sweep.node_group(host), "", host)


class OwnerTests(unittest.TestCase):
    def test_provider_flag_only_for_known_non_google_owner(self) -> None:
        from utils.ip_owner import IpOwner

        facts = (
            SweepFacts("rr1---sn-xguxaxjvh-304e.googlevideo.com", "85.113.32.140", video_sweep.FAMILY_V4, HelloResult(block_cause.HELLO_TIMEOUT), owner=IpOwner(asn="34533", owner="ER-Telecom")),
            SweepFacts("rr2---sn-aj5go5-5i.googlevideo.com", "173.194.160.161", video_sweep.FAMILY_V4, HelloResult(block_cause.HELLO_OK), owner=IpOwner(asn="15169", owner="GOOGLE")),
            SweepFacts("rr3---sn-q4fl6nsd.googlevideo.com", "203.0.113.7", video_sweep.FAMILY_V4, HelloResult(block_cause.HELLO_OK)),
        )
        nodes = video_sweep.judge(facts)
        self.assertEqual([node.provider for node in nodes], [True, False, False])
        self.assertEqual(nodes[0].group, "sn-xguxaxjvh-304e")
        self.assertIn("провайдера", nodes[0].owner)
        self.assertIn("Google", nodes[1].owner)
        self.assertEqual(nodes[2].owner, "")


class ViewTests(unittest.TestCase):
    def test_one_node_adds_no_rows(self) -> None:
        self.assertEqual(view.video_sweep_rows([_node("ok")]), ())
        self.assertEqual(view.video_sweep_rows(None), ())

    def test_summary_level_follows_how_many_answer(self) -> None:
        both = view.video_sweep_rows([_node("ok"), _node("ok", host="rr4---sn-b.googlevideo.com")])
        self.assertEqual((both[0].level, both[0].text), (view.OK, "отвечают 2 из 2 проверенных адресов"))
        half = view.video_sweep_rows([_node("ok"), _node("silent", host="rr4---sn-b.googlevideo.com")])
        self.assertEqual(half[0].level, view.WARN)
        none = view.video_sweep_rows([_node("silent"), _node("no_route", host="rr4---sn-b.googlevideo.com")])
        self.assertEqual(none[0].level, view.FAIL)

    def test_cut_window_row_names_the_cure(self) -> None:
        rows = view.video_sweep_rows([_node("ok"), _node("cut_window", family=video_sweep.FAMILY_V6, host="rr4---sn-b.googlevideo.com")])
        self.assertIn("(IPv6)", rows[2].title)
        self.assertIn("wssize", rows[2].text)

    def test_target_rows_carry_the_sweep(self) -> None:
        item = {
            "host": "rr1---sn-a.googlevideo.com",
            "purpose": "видео",
            "main": True,
            "ok": True,
            "state": "ok",
            "text": "открывается",
            "video_sweep": [_node("ok"), _node("silent", host="rr4---sn-b.googlevideo.com")],
        }
        titles = [row.title for row in view.target_rows(item)]
        self.assertIn("Видеосерверы YouTube", titles)


if __name__ == "__main__":
    unittest.main()
