"""Видеосервер YouTube не открылся: на какой ступени он закрыт, чей он и поможет ли стратегия.

Случаи взяты из жизни: абонент МТС (октябрь 2026) — адрес 212.188.37.15 сбрасывал любое
шифрованное соединение, и перебор 390 стратегий не помог; замер из Нидерландов у кэшей ТТК —
строчное имя googlevideo.com молчало, а то же имя с заглавными COM проходило.
"""

from __future__ import annotations

import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import patch

from diagnostics import block_cause as bc
from diagnostics import service_view
from diagnostics import video_node_check as vc
from diagnostics.services import Target
from diagnostics.tls_probe import KIND_CONNECT, KIND_RESET, STAGE_CONNECT, STAGE_TLS, ProbeResult
from diagnostics.verdict import DnsState, ReachState, TargetOutcome, summarize_service
from utils.ip_owner import IpOwner

HOST = "rr4---sn-jvhnu5g-c35d.googlevideo.com"
NODE_IP = "212.188.37.15"
MTS = IpOwner(asn="8359", prefix="212.188.0.0/16", country="RU", owner="MTS")
GOOGLE = IpOwner(asn="15169", prefix="", country="US", owner="GOOGLE")

ANSWER = bc.HelloResult(bc.HELLO_OK)
RESET = bc.HelloResult(bc.HELLO_RESET)


def _reset() -> ProbeResult:
    return ProbeResult(ip=NODE_IP, kind=KIND_RESET, stage=STAGE_TLS)


def _cause(**hellos) -> bc.CauseFacts:
    values = dict(neutral=RESET, nameless=RESET, allowed=RESET, recased=RESET, real_again=RESET)
    values.update(hellos)
    return bc.CauseFacts(
        host=HOST,
        result=_reset(),
        http=bc.HttpFacts(status=400),
        recased_name=bc.case_variant(HOST),
        any_name=True,
        **values,
    )


def _facts(cause=None, *, result=None, owner=MTS, via=()) -> vc.NodeFacts:
    return vc.NodeFacts(host=HOST, result=result or _reset(), cause=cause if cause is not None else _cause(), owner=owner, via=via)


class ClosedAddressTests(unittest.TestCase):
    """Случай МТС: соединение есть, шифрование не проходит ни с каким именем, даже без имени."""

    def test_every_name_reset_is_a_closed_address_and_no_strategy_helps(self) -> None:
        verdict = vc.judge(_facts())

        self.assertEqual(verdict.code, vc.NODE_ADDRESS)
        self.assertFalse(verdict.strategy_helps)
        self.assertIn("даже без имени", verdict.text)
        self.assertIn("по обычному HTTP он отвечает", verdict.text)
        self.assertIn("«Подбор стратегии» его не откроет", verdict.advice[0])

    def test_advice_names_the_provider_cache_and_does_not_promise_a_dns_fix(self) -> None:
        verdict = vc.judge(_facts())

        self.assertIn("AS8359 (MTS)", verdict.owner)
        self.assertIn("кэш Google в сети вашего провайдера", verdict.advice[-1])
        self.assertIn("другой DNS его не меняет", verdict.advice[-1])
        self.assertIn("прокси или VPN", verdict.advice[-1])

    def test_ladder_shows_each_step_in_order(self) -> None:
        keys = [step.key for step in vc.judge(_facts()).ladder]

        self.assertEqual(keys, ["tcp", "http", "nameless", "foreign", "allowed", "recased", "real"])
        steps = {step.key: step for step in vc.judge(_facts()).ladder}
        self.assertEqual(steps["tcp"].level, "ok")
        self.assertEqual(steps["http"].text, "отвечает (код 400)")
        self.assertEqual(steps["real"].text, "соединение сброшено — и при повторе")


class NameFilterTests(unittest.TestCase):
    def test_other_letters_passing_proves_a_letter_by_letter_name_filter(self) -> None:
        verdict = vc.judge(_facts(_cause(recased=ANSWER)))

        self.assertEqual(verdict.code, vc.NODE_NAME)
        self.assertTrue(verdict.strategy_helps)
        self.assertEqual(verdict.advice, ())
        self.assertIn("googlevideo.COM", verdict.text)

    def test_foreign_name_passing_is_a_name_filter(self) -> None:
        verdict = vc.judge(_facts(_cause(neutral=ANSWER, nameless=ANSWER)))

        self.assertEqual((verdict.code, verdict.strategy_helps), (vc.NODE_NAME, True))

    def test_real_name_passing_on_the_second_try_decides_nothing(self) -> None:
        verdict = vc.judge(_facts(_cause(real_again=ANSWER)))

        self.assertEqual((verdict.code, verdict.strategy_helps), (vc.NODE_UNCLEAR, True))


class NoRouteAndDownTests(unittest.TestCase):
    def test_no_tcp_connection_is_no_route_and_no_strategy_helps(self) -> None:
        result = ProbeResult(ip=NODE_IP, kind=KIND_CONNECT, stage=STAGE_CONNECT)
        cause = bc.CauseFacts(host=HOST, result=result, http=bc.HttpFacts(status=None), any_name=True)

        verdict = vc.judge(_facts(cause, result=result))

        self.assertEqual((verdict.code, verdict.strategy_helps), (vc.NODE_NO_ROUTE, False))
        self.assertEqual([step.key for step in verdict.ladder], ["tcp", "http"])
        self.assertIn("не открывает дорогу к адресу", verdict.advice[0])

    def test_node_dead_from_abroad_too_is_down(self) -> None:
        verdict = vc.judge(_facts(), outside="down")

        self.assertEqual((verdict.code, verdict.strategy_helps), (vc.NODE_DOWN, False))
        self.assertIn("не работает сам узел", verdict.text)

    def test_googles_own_node_is_not_called_a_provider_cache(self) -> None:
        verdict = vc.judge(_facts(owner=GOOGLE))

        self.assertIn("узел в сети Google", verdict.owner)
        self.assertNotIn("кэш Google в сети вашего провайдера", verdict.advice[-1])

    def test_no_facts_no_verdict(self) -> None:
        self.assertIsNone(vc.judge(None))


class ViaAddressTests(unittest.TestCase):
    """Тот же запрос узла через другой адрес Google: отдаёт ли он видео мимо закрытого адреса."""

    def test_data_through_another_google_address_becomes_the_first_fix(self) -> None:
        via = (vc.ViaAddress("i.ytimg.com", "192.178.40.6", ANSWER, fetched=True, status=200, size=48_000),)

        verdict = vc.judge(_facts(via=via))

        self.assertEqual(verdict.via[0][2], "ok")
        self.assertIn("отдал данные (46 КБ)", verdict.via[0][3])
        self.assertIn(f"«192.178.40.6 {HOST}» в файле hosts", verdict.advice[1])

    def test_refusing_address_is_reported_and_gives_no_hosts_advice(self) -> None:
        via = (
            vc.ViaAddress("www.youtube.com", "142.251.154.4", RESET),
            vc.ViaAddress("youtubei.googleapis.com", "172.217.115.4", ANSWER, fetched=True, status=403),
        )

        verdict = vc.judge(_facts(via=via))

        self.assertIn("не отвечает", verdict.via[0][3])
        self.assertIn("кодом 403", verdict.via[1][3])
        self.assertFalse(any("hosts" in line for line in verdict.advice))

    def test_answer_without_a_signed_request_says_data_was_not_checked(self) -> None:
        verdict = vc.judge(_facts(via=(vc.ViaAddress("i.ytimg.com", "192.178.40.6", ANSWER),)))

        self.assertIn("не проверялось", verdict.via[0][3])


class CollectTests(unittest.TestCase):
    def _run(self, goodput):
        def submit(fn, *args, **kwargs):
            future: Future = Future()
            future.set_result(fn(*args, **kwargs))
            return future

        return SimpleNamespace(video_goodput=goodput, probe_cancel=None, submit=submit, dns_cancelled=lambda: False)

    def _collect(self, goodput, fetched):
        addresses = {"i.ytimg.com": ("192.178.40.6",), "youtubei.googleapis.com": ("192.178.40.6",), "www.youtube.com": (NODE_IP,)}
        with (
            patch("diagnostics.video_nodes.node_owner", return_value=MTS),
            patch("diagnostics.net_access.browser_ipv4", lambda _run, name: addresses[name]),
            patch.object(bc, "tls_hello", lambda *_a, **_k: ANSWER),
            patch("diagnostics.net_access.fetch", side_effect=fetched) as fetch,
        ):
            facts = vc.collect(self._run(goodput), HOST, _reset(), _cause())
        return facts, fetch

    def test_signed_request_of_this_node_goes_through_each_other_address_once(self) -> None:
        answer = ProbeResult(ip="192.178.40.6", kind="ok", status=200, body_size=5000)

        facts, fetch = self._collect((HOST, "/videogoodput?id=1"), lambda *_a, **_k: answer)

        # Один и тот же адрес у двух имён проверяется один раз; адрес самого узла не проверяется.
        self.assertEqual([(item.name, item.ip) for item in facts.via], [("i.ytimg.com", "192.178.40.6")])
        self.assertEqual(fetch.call_args.args[1:4], (HOST, "192.178.40.6", "/videogoodput?id=1"))
        self.assertEqual((facts.via[0].status, facts.owner), (200, MTS))

    def test_request_of_another_node_is_not_used(self) -> None:
        facts, fetch = self._collect(("rr1---sn-other.googlevideo.com", "/videogoodput?id=1"), lambda *_a, **_k: None)

        fetch.assert_not_called()
        self.assertFalse(facts.via[0].fetched)


class SummaryTests(unittest.TestCase):
    """Совет по видеосерверу заменяет «подберите стратегию» в итоге сервиса."""

    def test_closed_video_node_replaces_the_strategy_advice(self) -> None:
        cure = vc.judge(_facts()).advice
        targets = [
            TargetOutcome("www.youtube.com", "сайт", ReachState.OK, DnsState.OK, main=True),
            TargetOutcome(HOST, "видео", ReachState.DPI, DnsState.OK, kind="ip", cure=cure),
        ]

        verdict = summarize_service("YouTube", targets, zapret_running=True)

        self.assertEqual(verdict.advice, cure)
        self.assertFalse(any("Подберите другую стратегию" in line for line in verdict.advice))

    def test_problem_has_no_strategy_button_for_a_closed_video_node(self) -> None:
        from diagnostics import problems
        from diagnostics.run_context import Probe
        from diagnostics.services import Service

        target = Target(HOST, "видео", discover_googlevideo=True)
        probe = Probe(target=target, service="youtube", host=HOST)
        probe.reach = _reset()
        probe.reach_state = ReachState.DPI
        probe.cause = bc.judge(_cause())
        probe.video_facts = _facts()
        main = Probe(target=Target("www.youtube.com", "сайт", main=True), service="youtube", host="www.youtube.com")
        main.reach_state = ReachState.OK
        services = {"youtube": Service("youtube", "YouTube", (main.target, target))}
        verdict = summarize_service(
            "YouTube",
            [
                TargetOutcome("www.youtube.com", "сайт", ReachState.OK, DnsState.OK, main=True),
                TargetOutcome(HOST, "видео", ReachState.DPI, DnsState.OK, kind=probe.kind, cure=probe.video_node.advice),
            ],
            zapret_running=True,
        )

        found, _working, _spoofed = problems.collect_problems(
            services, {"youtube": verdict}, {"youtube": [main, probe]}, voice=None, freeze=None, zapret_running=True
        )

        video = [item for item in found if item.get("target") == HOST]
        self.assertEqual(len(video), 1)
        self.assertEqual(video[0].get("action"), "")
        self.assertTrue(probe.strategy_futile)


class ViewTests(unittest.TestCase):
    def test_card_and_text_show_owner_ladder_via_and_conclusion(self) -> None:
        via = (vc.ViaAddress("i.ytimg.com", "192.178.40.6", ANSWER, fetched=True, status=200, size=48_000),)
        node = vc.judge(_facts(via=via)).as_dict()

        rows = service_view.video_node_rows(node)
        titles = [row.title for row in rows]

        self.assertEqual(titles[0], "Чей видеосервер")
        self.assertIn("Видеосервер: Шифрование без имени", titles)
        self.assertIn("Через адрес Google 192.178.40.6 (i.ytimg.com)", titles)
        self.assertEqual(titles[-1], "Вывод по видеосерверу")
        self.assertEqual(rows[-1].level, "fail")

    def test_report_without_a_video_node_draws_nothing(self) -> None:
        self.assertEqual(service_view.video_node_rows(None), ())


if __name__ == "__main__":
    unittest.main()
