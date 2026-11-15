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
        # Молчащему узлу — три попытки, потом малое окно; ответившему — все пять (вдруг «через раз»).
        self.assertEqual(calls, ["hello"] * video_sweep.SWEEP_GIVE_UP + ["window"] + ["hello"] * video_sweep.SWEEP_TRIES)
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

    def test_open_video_server_through_wssize_shows_the_speed_cap(self) -> None:
        """Видеосервер открылся (wssize для того и нужен), а скорость душит: потолок виден и у открытого."""
        item = {
            "host": "rr1---sn-a.googlevideo.com", "purpose": "видео", "main": True, "ok": True, "state": "ok", "text": "открывается",
            "coverage": None,
            "video_strategy": {"strategy_name": "MultiDisorder Complex + WSSize Ramp · из googlevideo.com", "strategy_text": ""},
            "video_sweep": [{**_node("ok"), "rtt_ms": 20}, {**_node("ok", host="rr4---sn-b.googlevideo.com"), "rtt_ms": 30}],
        }
        self.assertIn("Потолок скорости с wssize", [row.title for row in view.target_rows(item)])

    def test_report_carries_the_video_strategy_to_the_rows(self) -> None:
        from diagnostics import engine, report_text

        probe = engine._Probe(target=engine.Target("rr1---sn-a.googlevideo.com", "видео"), service="youtube", host="rr1---sn-a.googlevideo.com")
        self.assertIsNone(report_text.target_report(probe)["video_strategy"])
        probe.video_coverage = SimpleNamespace(strategy_name="MultiDisorder Complex + WSSize Ramp · из googlevideo.com", strategy_text="--x")
        self.assertEqual(
            report_text.target_report(probe)["video_strategy"],
            {"strategy_name": "MultiDisorder Complex + WSSize Ramp · из googlevideo.com", "strategy_text": "--x"},
        )

    def test_annotate_records_the_profile_of_an_open_video_server(self) -> None:
        from diagnostics import preset_coverage
        from diagnostics.verdict import ReachState

        cover = SimpleNamespace(strategy_name="MultiDisorder Complex + WSSize Ramp · из googlevideo.com", strategy_text="")
        running = SimpleNamespace(covering=lambda host, ip: cover)
        video = SimpleNamespace(
            host="rr1---sn-a.googlevideo.com", reach=SimpleNamespace(ip="203.0.113.5"), reach_state=ReachState.OK,
            target=SimpleNamespace(discover_googlevideo=True), video_sweep=(_facts(block_cause.HELLO_OK),),
            coverage=None, video_coverage=None,
        )
        site = SimpleNamespace(host="www.youtube.com", reach=None, reach_state=ReachState.OK,
                               target=SimpleNamespace(discover_googlevideo=False), video_sweep=(), coverage=None, video_coverage=None)
        with mock.patch.object(preset_coverage, "running_preset", return_value=running):
            preset_coverage.annotate([video, site], True)
        self.assertIs(video.video_coverage, cover)
        # «Профиль пресета» по-прежнему только для закрытых адресов.
        self.assertIsNone(video.coverage)
        self.assertIsNone(site.video_coverage)
        video.video_coverage = None
        with mock.patch.object(preset_coverage, "running_preset", return_value=running):
            preset_coverage.annotate([video], False)
        self.assertIsNone(video.video_coverage)  # Zapret не запущен — профиля нет


if __name__ == "__main__":
    unittest.main()


def _seq(*kinds: str, connect_ms: float | None = None):
    """tls_hello, который отвечает по очереди заданными исходами."""
    queue = list(kinds)
    calls: list[str] = []

    def tls_hello(ip, name, **_kw):
        calls.append(ip)
        return HelloResult(queue.pop(0) if queue else kinds[-1], connect_ms=connect_ms)

    return tls_hello, calls


class RepeatTests(unittest.TestCase):
    """Узлы открываются «то с первой попытки, то сто раз подряд нет»: одна попытка — подброшенная монета."""

    _run = CollectTests._run

    def _collect(self, tls_hello, *, window=block_cause.HELLO_TIMEOUT):
        with (
            mock.patch.object(block_cause, "tls_hello", tls_hello),
            mock.patch.object(block_cause, "window_hello", return_value=HelloResult(window)),
            mock.patch.object(video_sweep, "_ipv6_address", return_value=""),
            mock.patch("diagnostics.video_nodes.node_owner", return_value=None),
            mock.patch("diagnostics.net_access.browser_ipv4", return_value=("203.0.113.5",)),
        ):
            return video_sweep.collect(self._run(), ("rr1---sn-a.googlevideo.com", "rr2---sn-a.googlevideo.com"))[0]

    def test_some_attempts_through_means_flaky_with_the_count(self) -> None:
        ok, cut = block_cause.HELLO_OK, block_cause.HELLO_TIMEOUT
        hello, calls = _seq(cut, ok, cut, ok, ok)
        facts = self._collect(hello)
        node = video_sweep.judge((facts,))[0]
        self.assertEqual((node.code, node.passes, node.tries), (video_sweep.SWEEP_FLAKY, 3, 5))
        self.assertIsNone(facts.small)  # узел отвечает — малое окно не нужно
        self.assertEqual(node.as_dict()["passes"], 3)

    def test_first_answer_then_failures_is_flaky_not_ok(self) -> None:
        facts = SweepFacts("h", "203.0.113.5", video_sweep.FAMILY_V4, HelloResult(block_cause.HELLO_OK), more=(HelloResult(block_cause.HELLO_RESET),))
        self.assertEqual(video_sweep.judge_node(facts), video_sweep.SWEEP_FLAKY)

    def test_silent_node_gives_up_after_three_attempts(self) -> None:
        hello, calls = _seq(block_cause.HELLO_TIMEOUT)
        facts = self._collect(hello)
        self.assertEqual(len(facts.attempts), video_sweep.SWEEP_GIVE_UP)
        self.assertEqual(video_sweep.judge_node(facts), video_sweep.SWEEP_SILENT)

    def test_no_route_is_not_repeated(self) -> None:
        hello, calls = _seq(block_cause.HELLO_CONNECT)
        self.assertEqual(len(self._collect(hello).attempts), 1)


class DistanceTests(unittest.TestCase):
    def test_round_trip_is_the_fastest_connect(self) -> None:
        facts = SweepFacts(
            "h", "203.0.113.5", video_sweep.FAMILY_V4, HelloResult(block_cause.HELLO_OK, connect_ms=48.0),
            more=(HelloResult(block_cause.HELLO_OK, connect_ms=31.0), HelloResult(block_cause.HELLO_TIMEOUT, connect_ms=40.0)),
        )
        node = video_sweep.judge((facts,))[0]
        self.assertEqual(node.rtt_ms, 31.0)
        self.assertEqual(node.as_dict()["rtt_ms"], 31)

    def _nodes(self, *rtts: float) -> list[dict]:
        return [{**_node("ok", host=f"rr{n}---sn-a.googlevideo.com"), "rtt_ms": rtt} for n, rtt in enumerate(rtts, 1)]

    def test_far_node_is_named_and_explained_as_distance_not_block(self) -> None:
        rows = view.video_sweep_rows(self._nodes(14, 240))
        far = [row for row in rows if row.title == "Далёкие видеосерверы"]
        self.assertEqual(len(far), 1)
        self.assertIn("rr2---sn-a.googlevideo.com (240 мс)", far[0].text)
        self.assertIn("без всякой блокировки", far[0].text)
        self.assertIn("до узла 240 мс — далеко", next(row.text for row in rows if "rr2---" in row.title))
        self.assertFalse(any(row.title == "Далёкие видеосерверы" for row in view.video_sweep_rows(self._nodes(14, 40))))

    def test_speed_cap_only_when_video_goes_through_wssize(self) -> None:
        nodes = self._nodes(14, 240)
        self.assertFalse(any("Потолок" in row.title for row in view.video_sweep_rows(nodes, {"strategy_name": "General ALT"})))
        ramp = [row for row in view.video_sweep_rows(nodes, {"strategy_name": "MultiDisorder Complex + WSSize Ramp · из googlevideo.com"}) if "Потолок" in row.title]
        self.assertEqual(len(ramp), 1)
        # 128 КБ за круг: 14 мс — около 75 Мбит/с, 240 мс — около 4.
        self.assertIn("при 14 мс до узла — до 75 Мбит/с, при 240 мс — до 4 Мбит/с", ramp[0].text)
        self.assertEqual(ramp[0].level, view.INFO)
        stock = [row for row in view.video_sweep_rows(nodes, {"strategy_name": "MultiDisorder Complex + WSSize 1:1 · из googlevideo.com"}) if "Потолок" in row.title]
        self.assertEqual(stock[0].level, view.WARN)
        self.assertIn("wssize_ramp", stock[0].text)

    def test_flaky_row_says_how_often(self) -> None:
        rows = view.video_sweep_rows([_node("ok"), {**_node("flaky", host="rr2---sn-a.googlevideo.com"), "passes": 2, "tries": 5}])
        self.assertIn("ещё 1 — через раз", rows[0].text)
        self.assertIn("через раз: 2 из 5 попыток", rows[2].text)


class ClosedCacheTests(unittest.TestCase):
    """Кэш, закрытый в hosts, проверяется по настоящему адресу, а не по 127.0.0.2 на своём же компьютере."""

    _run = CollectTests._run

    def test_closed_groups_missing_from_the_player_list_are_added(self) -> None:
        hosts = video_sweep.sweep_hosts(("rr1---sn-a.googlevideo.com",), ("sn-a", "sn-z", "sn-y", "sn-x"))
        self.assertEqual(hosts[0], ("rr1---sn-a.googlevideo.com", True))
        self.assertEqual([host for host, _closed in hosts[1:]], ["rr1---sn-x.googlevideo.com", "rr1---sn-y.googlevideo.com"])
        self.assertTrue(all(closed for _host, closed in hosts))

    def test_closed_node_is_resolved_past_hosts(self) -> None:
        seen: list[str] = []

        def tls_hello(ip, name, **_kw):
            seen.append(ip)
            return HelloResult(block_cause.HELLO_OK)

        with (
            mock.patch.object(block_cause, "tls_hello", tls_hello),
            mock.patch.object(video_sweep, "_ipv6_address", return_value="2a00:1450::4"),
            mock.patch("diagnostics.video_nodes.node_owner", return_value=None),
            mock.patch("diagnostics.net_access.browser_ipv4", return_value=("127.0.0.2",)),
            mock.patch("diagnostics.net_access.system_ipv4", return_value=SimpleNamespace(ips=("198.51.100.7",))),
        ):
            result = video_sweep.collect(self._run(), (), ("sn-c",))
        self.assertEqual([(item.ip, item.family, item.closed) for item in result], [("198.51.100.7", video_sweep.FAMILY_V4, True)])
        self.assertNotIn("127.0.0.2", seen)
        node = video_sweep.judge(result)[0]
        self.assertTrue(node.as_dict()["closed"])

    def test_closed_row_and_summary_leave_the_closed_node_out_of_the_count(self) -> None:
        nodes = [_node("ok"), _node("silent", host="rr2---sn-b.googlevideo.com"), {**_node("ok", host="rr1---sn-c.googlevideo.com"), "closed": True}]
        rows = view.video_sweep_rows(nodes)
        self.assertEqual(rows[0].text, "отвечают 1 из 2 проверенных адресов")
        self.assertEqual(rows[-1].title, "Закрытый в hosts кэш rr1---sn-c.googlevideo.com")
        self.assertIn("по настоящему адресу", rows[-1].text)
