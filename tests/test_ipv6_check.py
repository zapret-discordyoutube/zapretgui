from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

from diagnostics import ipv6_check as v6
from diagnostics.tls_probe import KIND_CANCELLED, KIND_CONNECT, KIND_OK, KIND_TIMEOUT, ProbeResult

ADDRESS = "2001:db8::1"


def _ok(ms: float = 25.0) -> ProbeResult:
    return ProbeResult(ip=ADDRESS, kind=KIND_OK, elapsed_ms=ms)


def _fail(kind: str = KIND_TIMEOUT) -> ProbeResult:
    return ProbeResult(ip=ADDRESS, kind=kind)


def _facts(*results, has_route=True) -> v6.Ipv6Facts:
    probes = tuple((f"site{index}", ADDRESS if result is not None else "", result) for index, result in enumerate(results))
    return v6.Ipv6Facts(has_route=has_route, probes=probes)


class JudgeTests(unittest.TestCase):
    def test_one_answer_is_enough_and_fastest_time_is_shown(self) -> None:
        verdict = v6.judge(_facts(_fail(), _ok(40.0), _ok(12.4)))

        self.assertEqual((verdict.code, verdict.text), (v6.IPV6_OK, "работает (ответ за 12 мс)"))

    def test_answer_wins_even_if_system_reports_no_route(self) -> None:
        self.assertEqual(v6.judge(_facts(_ok(), has_route=False)).code, v6.IPV6_OK)

    def test_no_route_is_normal_absence_not_a_fault(self) -> None:
        verdict = v6.judge(_facts(_fail(KIND_CONNECT), _fail(KIND_CONNECT), has_route=False))

        self.assertEqual(verdict.code, v6.IPV6_ABSENT)
        self.assertIn("нормально", verdict.text)

    def test_route_present_but_nothing_answers_is_broken(self) -> None:
        verdict = v6.judge(_facts(_fail(), _fail(KIND_CONNECT), has_route=True))

        self.assertEqual(verdict.code, v6.IPV6_BROKEN)
        self.assertIn("настроен, но не работает", verdict.text)

    def test_unknown_when_there_is_not_enough_to_judge(self) -> None:
        unknown = (
            # Проверку сняли по времени.
            _facts(_fail(KIND_CANCELLED), _fail(), has_route=True),
            # Эталонные серверы не дали адресов.
            _facts(None, None, has_route=True),
            # Сайты молчат, а про дорогу в системе узнать не удалось.
            _facts(_fail(), has_route=None),
        )
        for facts in unknown:
            with self.subTest(facts=facts):
                self.assertEqual(v6.judge(facts).code, v6.IPV6_UNKNOWN)

    def test_no_route_wins_over_missing_addresses(self) -> None:
        self.assertEqual(v6.judge(_facts(None, None, has_route=False)).code, v6.IPV6_ABSENT)


class CollectTests(unittest.TestCase):
    def test_each_site_is_asked_by_its_ipv6_address(self) -> None:
        asked: list[tuple[str, str]] = []
        addresses = {"www.google.com": ("2001:db8::10", "2001:db8::11"), "www.cloudflare.com": ()}

        def get(host, ip):
            asked.append((host, ip))
            return _ok()

        with ThreadPoolExecutor(4) as pool:
            facts = v6.collect(has_route=lambda: True, lookup=addresses.__getitem__, get=get, submit=pool.submit)

        # Сайт без IPv6-адреса не запрашивается; у остальных берётся первый адрес.
        self.assertEqual(asked, [("www.google.com", "2001:db8::10")])
        self.assertTrue(facts.has_route)
        self.assertEqual([(host, ip) for host, ip, _result in facts.probes], [("www.google.com", "2001:db8::10"), ("www.cloudflare.com", "")])
        self.assertIsNone(facts.probes[1][2])

    def test_failed_route_lookup_is_unknown_not_crash(self) -> None:
        def broken() -> bool:
            raise OSError("нет доступа")

        with ThreadPoolExecutor(2) as pool:
            facts = v6.collect(has_route=broken, lookup=lambda _host: (), get=lambda *_a: _ok(), submit=pool.submit)

        self.assertIsNone(facts.has_route)


if __name__ == "__main__":
    unittest.main()
