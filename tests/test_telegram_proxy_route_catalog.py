from __future__ import annotations

import unittest


class TelegramProxyRouteCatalogTests(unittest.TestCase):
    def test_stable_wss_routes_are_only_dc2_and_dc4(self) -> None:
        from telegram_proxy.proxy.route_catalog import (
            RouteStatus,
            stable_wss_domains_for_dc,
            wss_enabled_dcs,
        )

        self.assertEqual(wss_enabled_dcs(), (2, 4))
        self.assertEqual(
            stable_wss_domains_for_dc(2, is_media=False),
            ("kws2.web.telegram.org", "kws2-1.web.telegram.org"),
        )
        self.assertEqual(
            stable_wss_domains_for_dc(2, is_media=True),
            ("kws2-1.web.telegram.org", "kws2.web.telegram.org"),
        )
        self.assertEqual(
            stable_wss_domains_for_dc(4, is_media=False),
            ("kws4.web.telegram.org", "kws4-1.web.telegram.org"),
        )
        self.assertEqual(stable_wss_domains_for_dc(1, is_media=False), ())
        self.assertEqual(stable_wss_domains_for_dc(203, is_media=False), ())
        self.assertEqual(RouteStatus.STABLE.value, "stable")

    def test_candidate_zws_routes_are_recorded_but_not_used_as_stable(self) -> None:
        from telegram_proxy.proxy.route_catalog import (
            candidate_wss_domains_for_dc,
            stable_wss_domains_for_dc,
        )

        self.assertEqual(
            candidate_wss_domains_for_dc(2, is_media=False),
            ("zws2.web.telegram.org", "zws2-1.web.telegram.org"),
        )
        self.assertEqual(
            candidate_wss_domains_for_dc(4, is_media=True),
            ("zws4-1.web.telegram.org", "zws4.web.telegram.org"),
        )
        self.assertNotIn("zws2.web.telegram.org", stable_wss_domains_for_dc(2, is_media=False))
        self.assertNotIn("zws4.web.telegram.org", stable_wss_domains_for_dc(4, is_media=False))

    def test_fallback_only_routes_document_non_wss_dcs(self) -> None:
        from telegram_proxy.proxy.route_catalog import (
            fallback_only_reason,
            route_status_for_dc,
        )

        for dc in (1, 3, 5, 203):
            with self.subTest(dc=dc):
                self.assertEqual(route_status_for_dc(dc), "fallback_only")
                self.assertIn("101", fallback_only_reason(dc))

        self.assertEqual(route_status_for_dc(2), "stable")
        self.assertEqual(route_status_for_dc(4), "stable")

    def test_cdn_fronts_have_two_cloudflare_addresses_each(self) -> None:
        from telegram_proxy.proxy.route_catalog import CDN_FRONTS, TUNNEL_HOST

        self.assertEqual(len(CDN_FRONTS), 20)
        self.assertEqual(len({front.domain for front in CDN_FRONTS}), 20)
        for front in CDN_FRONTS:
            with self.subTest(front=front.domain):
                self.assertTrue(front.domain.endswith(".co.uk"))
                self.assertTrue(front.addresses[0].startswith("104.21."))
                self.assertTrue(front.addresses[1].startswith("172.67."))
        self.assertEqual(CDN_FRONTS[0].host_for(2), "kws2.pclead.co.uk")
        self.assertEqual(TUNNEL_HOST, "edge.amberwick.workers.dev")

if __name__ == "__main__":
    unittest.main()
