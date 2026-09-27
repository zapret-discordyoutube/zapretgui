from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from telegram_proxy.proxy.cloudflare import AUTO_CLOUDFLARE_DOMAINS, CloudflareFallbackConfig
from telegram_proxy.proxy.health import RouteHealth, address_key, cdn_key
from telegram_proxy.proxy.route_catalog import CDN_FRONTS, TUNNEL_HOST
from telegram_proxy.proxy.routes import (
    KIND_DIRECT,
    KIND_FRONT,
    KIND_RELAY,
    KIND_TUNNEL,
    KIND_UPSTREAM,
    KIND_USER_DOMAIN,
    KIND_USER_WORKER,
    PlanInput,
    build_plan,
)
from telegram_proxy.proxy.routing import UpstreamProxyConfig


PRESET = UpstreamProxyConfig(enabled=True, host="1.2.3.4", port=1080, mode="fallback", preset_id="ee")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def kinds(plan) -> list[str]:
    return [route.kind for route in plan]


class RoutePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.health = RouteHealth(clock=self.clock, front_count=len(CDN_FRONTS))

    def test_dc2_goes_relay_fronts_tunnel_then_country_socks(self) -> None:
        plan = build_plan(PlanInput(dc=2, is_media=False, target_host="149.154.167.51", upstream=PRESET), self.health)
        self.assertEqual(kinds(plan), [KIND_RELAY, KIND_FRONT, KIND_FRONT, KIND_FRONT, KIND_TUNNEL, KIND_UPSTREAM])
        relay = plan[0]
        self.assertEqual((relay.connect_host, relay.sni), ("149.154.167.220", "kws2.web.telegram.org"))
        self.assertTrue(plan[1].sni.startswith("kws2."))
        self.assertEqual(plan[4].path, "/apiws?dst=149.154.167.51&dc=2")

    def test_media_uses_minus_one_relay_domain(self) -> None:
        plan = build_plan(PlanInput(dc=4, is_media=True, target_host="149.154.164.250"), self.health)
        self.assertEqual(plan[0].sni, "kws4-1.web.telegram.org")

    def test_dc1_has_no_relay_and_starts_with_fronts(self) -> None:
        plan = build_plan(PlanInput(dc=1, is_media=False, target_host="149.154.175.50"), self.health)
        self.assertEqual(kinds(plan), [KIND_FRONT, KIND_FRONT, KIND_FRONT, KIND_TUNNEL, KIND_DIRECT])

    def test_dc203_uses_only_tunnel_before_fallbacks(self) -> None:
        plan = build_plan(PlanInput(dc=203, is_media=False, target_host="91.105.192.100", upstream=PRESET), self.health)
        self.assertEqual(kinds(plan), [KIND_TUNNEL, KIND_UPSTREAM])
        self.assertEqual(plan[0].sni, TUNNEL_HOST)

    def test_manual_always_server_takes_all_traffic(self) -> None:
        manual = UpstreamProxyConfig(enabled=True, host="5.6.7.8", port=1080, mode="always")
        plan = build_plan(PlanInput(dc=2, is_media=False, target_host="149.154.167.51", upstream=manual), self.health)
        self.assertEqual(kinds(plan), [KIND_UPSTREAM])

    def test_country_preset_toggle_all_tcp_through_socks_is_honoured(self) -> None:
        preset_always = UpstreamProxyConfig(enabled=True, host="1.2.3.4", port=1080, mode="always", preset_id="ee")
        plan = build_plan(PlanInput(dc=2, is_media=False, target_host="149.154.167.51", upstream=preset_always), self.health)
        self.assertEqual(kinds(plan), [KIND_UPSTREAM])

    def test_user_domain_and_worker_come_before_builtin_fronts(self) -> None:
        cloudflare = CloudflareFallbackConfig(
            enabled=True,
            domains=("my.example.com", *AUTO_CLOUDFLARE_DOMAINS),
            worker_enabled=True,
            worker_domains=("w.example.dev",),
        )
        plan = build_plan(PlanInput(dc=1, is_media=False, target_host="149.154.175.50", cloudflare=cloudflare), self.health)
        self.assertEqual(kinds(plan)[:3], [KIND_USER_DOMAIN, KIND_USER_WORKER, KIND_FRONT])
        self.assertEqual(plan[0].sni, "kws1.my.example.com")

    def test_suppressed_relay_address_switches_to_dns_name(self) -> None:
        self._suppress(address_key("149.154.167.220"))
        plan = build_plan(PlanInput(dc=2, is_media=False, target_host="149.154.167.51"), self.health)
        self.assertEqual(plan[0].connect_host, "kws2.web.telegram.org")

    def test_all_wss_suppressed_goes_socks_then_fronts_anyway(self) -> None:
        self._suppress(cdn_key(1))
        self._suppress(TUNNEL_HOST)
        plan = build_plan(PlanInput(dc=1, is_media=False, target_host="149.154.175.50", upstream=PRESET), self.health)
        self.assertEqual(kinds(plan), [KIND_UPSTREAM, KIND_FRONT, KIND_FRONT, KIND_FRONT])

    def _suppress(self, key: str) -> None:
        for _ in range(6):
            self.health.note_failure(key, threshold=6)
            self.clock.now += 3
        self.assertTrue(self.health.is_suppressed(key))


class RouteHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.health = RouteHealth(clock=self.clock, front_count=len(CDN_FRONTS))

    def _fail(self, key: str, times: int, **kwargs) -> None:
        for _ in range(times):
            self.health.note_failure(key, **kwargs)
            self.clock.now += 3

    def test_three_failures_suppress_and_duration_doubles(self) -> None:
        self._fail("kws2.web.telegram.org", 2)
        self.assertFalse(self.health.is_suppressed("kws2.web.telegram.org"))
        self._fail("kws2.web.telegram.org", 1)
        self.assertAlmostEqual(self.health.suppressed_for("kws2.web.telegram.org"), 117, delta=1)
        self.clock.now += 200
        self._fail("kws2.web.telegram.org", 3)
        self.assertAlmostEqual(self.health.suppressed_for("kws2.web.telegram.org"), 237, delta=1)

    def test_failures_in_one_burst_count_once(self) -> None:
        for _ in range(5):
            self.health.note_failure("k")
            self.clock.now += 0.5
        self.assertFalse(self.health.is_suppressed("k"))

    def test_lost_syn_ignored_when_address_recently_connected(self) -> None:
        self.health.note_tcp_connected("149.154.167.220")
        self._fail("k", 5, tcp_reached=False, address="149.154.167.220")
        self.assertFalse(self.health.is_suppressed("k"))

    def test_answer_breaks_series_but_only_proof_clears_suppressions(self) -> None:
        self._fail("k", 2)
        self.health.note_answer("k")
        self._fail("k", 2)
        self.assertFalse(self.health.is_suppressed("k"))
        self._fail("k", 1)
        self.assertTrue(self.health.is_suppressed("k"))
        self.health.note_proven("k")
        self.assertFalse(self.health.is_suppressed("k"))

    def test_suppression_survives_restart_but_capped_to_ten_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            wall = [5000.0]
            health = RouteHealth(path=path, clock=self.clock, wall_clock=lambda: wall[0], front_count=20)
            for _ in range(4):  # 2 + 4 + 8 + 16 минут
                for _ in range(3):
                    health.note_failure("k")
                    self.clock.now += 3
            self.assertIn("k", json.loads(path.read_text())["routes"])
            restored = RouteHealth(path=path, clock=self.clock, wall_clock=lambda: wall[0], front_count=20)
            self.assertTrue(restored.is_suppressed("k"))
            self.assertLessEqual(restored.suppressed_for("k"), 600)

    def test_working_front_address_family_is_kept(self) -> None:
        index, family = self.health.next_front_slots(1)[0]
        self.health.note_front_result(index, family, tcp_ok=True, answered=False)
        slots = self.health.next_front_slots(3)
        self.assertEqual({slot_family for _i, slot_family in slots}, {family})
        self.assertEqual(len({i for i, _f in slots}), 3)
        self.assertNotEqual(slots[0][0], index)


if __name__ == "__main__":
    unittest.main()
