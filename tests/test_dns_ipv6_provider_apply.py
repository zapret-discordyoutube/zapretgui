from __future__ import annotations

import unittest


class DnsIpv6ProviderApplyTests(unittest.TestCase):
    def test_provider_dns_plan_accepts_ipv6_only_when_ipv6_is_available(self) -> None:
        from dns.page_plans import build_provider_dns_plan

        plan = build_provider_dns_plan(
            name="Мой IPv6 DNS",
            data={"ipv4": [], "ipv6": ["2001:4860:4860::8888"]},
            ipv6_available=True,
        )

        self.assertTrue(plan.valid)
        self.assertEqual(plan.ipv4, [])
        self.assertEqual(plan.ipv6, ["2001:4860:4860::8888"])

    def test_provider_dns_plan_rejects_ipv6_only_when_ipv6_is_unavailable(self) -> None:
        from dns.page_plans import build_provider_dns_plan

        plan = build_provider_dns_plan(
            name="Мой IPv6 DNS",
            data={"ipv4": [], "ipv6": ["2001:4860:4860::8888"]},
            ipv6_available=False,
        )

        self.assertFalse(plan.valid)
        self.assertIn("нет DNS", plan.log_message)

class DnsProviderPlanTests(unittest.TestCase):
    def test_ipv6_addresses_are_kept_even_without_ipv6_route(self) -> None:
        from dns.page_plans import build_provider_dns_plan

        plan = build_provider_dns_plan(
            name="Cloudflare",
            data={"ipv4": ["1.1.1.1"], "ipv6": ["2606:4700:4700::1111"]},
            ipv6_available=False,
        )

        self.assertTrue(plan.valid)
        self.assertEqual(plan.ipv6, ["2606:4700:4700::1111"])


if __name__ == "__main__":
    unittest.main()
