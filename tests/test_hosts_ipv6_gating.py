import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PUBLIC_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PUBLIC_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class Ipv6DetectionPredicateTests(unittest.TestCase):
    def setUp(self) -> None:
        from hosts import ipv6_detection

        self.ipv6_detection = ipv6_detection
        ipv6_detection.reset_ipv6_detection_cache()
        self.addCleanup(ipv6_detection.reset_ipv6_detection_cache)

    def test_gateway_predicate_accepts_link_local_router(self) -> None:
        # Шлюз из Router Advertisement — почти всегда link-local: это норма.
        self.assertTrue(self.ipv6_detection._is_usable_gateway_ipv6("fe80::1"))
        self.assertTrue(
            self.ipv6_detection._is_usable_gateway_ipv6("fe80::60f0:7326:977e:9215")
        )
        self.assertTrue(self.ipv6_detection._is_usable_gateway_ipv6("2a00:1450::1"))

    def test_gateway_predicate_rejects_unusable_addresses(self) -> None:
        for value in ("", "::", "::1", "ff02::1", "not-an-ip", "192.168.1.1"):
            with self.subTest(value=value):
                self.assertFalse(self.ipv6_detection._is_usable_gateway_ipv6(value))

    def test_local_predicate_requires_global_address(self) -> None:
        self.assertTrue(self.ipv6_detection._is_usable_local_ipv6("2a00:1450::1"))
        self.assertTrue(
            self.ipv6_detection._is_usable_local_ipv6("2606:50c0:8000::154")
        )

    def test_local_predicate_rejects_non_global_addresses(self) -> None:
        cases = {
            "ULA": "fd00::1",
            "link-local": "fe80::1",
            "loopback": "::1",
            "unspecified": "::",
            "multicast": "ff02::1",
            "teredo": "2001::1",
            "6to4": "2002:0101:0101::1",
            "documentation": "2001:db8::1",
            "ipv4-mapped": "::ffff:8.8.8.8",
            "ipv4": "8.8.8.8",
            "invalid": "junk",
        }
        for label, value in cases.items():
            with self.subTest(label=label):
                self.assertFalse(self.ipv6_detection._is_usable_local_ipv6(value))

    def test_availability_result_is_cached_until_reset(self) -> None:
        with (
            patch.object(self.ipv6_detection.os, "name", "posix"),
            patch.object(
                self.ipv6_detection, "_is_ipv6_available_socket_probe", return_value=True
            ) as probe,
        ):
            self.assertTrue(self.ipv6_detection.is_ipv6_available())
            self.assertTrue(self.ipv6_detection.is_ipv6_available())
            self.assertEqual(probe.call_count, 1)

            self.ipv6_detection.reset_ipv6_detection_cache()
            self.assertTrue(self.ipv6_detection.is_ipv6_available())
            self.assertEqual(probe.call_count, 2)


def _make_profile_index(service_name: str, candidate_ips: list[str]) -> dict[str, object]:
    return {
        "dns_profiles": ["hosts"],
        "dns_profile_names": {"hosts": "Вкл. (активировать hosts)"},
        "services": [service_name],
        "available_by_service": {service_name: ["hosts"]},
        "has_proxy_by_service": {service_name: False},
        "category_by_service": {service_name: "direct"},
        "icon_by_service": {service_name: ("fa5b.github", "#181717")},
        "service_id_by_name": {service_name: "hosts.test_service"},
        "direct_profile": "hosts",
        "domain_names_by_service": {service_name: ["raw.githubusercontent.com"]},
        "profile_domain_maps_by_service": {
            service_name: {"hosts": {"raw.githubusercontent.com": candidate_ips[0]}}
        },
        "profile_domain_ip_candidates_by_service": {
            service_name: {"hosts": {"raw.githubusercontent.com": list(candidate_ips)}}
        },
    }


class ServicesCatalogPlanIpv6GateTests(unittest.TestCase):
    SERVICE = "GitHub IPv6"

    def _build_plan(self, *, candidate_ips, ipv6_available, current_selection):
        from hosts import page_plans, proxy_domains

        with patch.object(
            proxy_domains,
            "get_services_profile_index",
            return_value=_make_profile_index(self.SERVICE, candidate_ips),
        ):
            return page_plans.build_services_catalog_plan(
                current_selection=current_selection,
                active_domains_map={},
                direct_title="Напрямую из hosts",
                ai_title="ИИ",
                other_title="Остальные",
                ipv6_available=ipv6_available,
            )

    def _single_row(self, plan):
        self.assertEqual(len(plan.groups), 1)
        self.assertEqual(len(plan.groups[0].rows), 1)
        return plan.groups[0].rows[0]

    def test_ipv6_only_service_is_disabled_without_ipv6(self) -> None:
        plan = self._build_plan(
            candidate_ips=["2606:50c0:8000::154", "2606:50c0:8001::154"],
            ipv6_available=False,
            current_selection={self.SERVICE: "hosts"},
        )
        row = self._single_row(plan)
        self.assertFalse(row.toggle_enabled)
        self.assertFalse(row.toggle_checked)
        self.assertIsNotNone(row.unavailable_reason)

    def test_disabled_service_keeps_saved_selection(self) -> None:
        plan = self._build_plan(
            candidate_ips=["2606:50c0:8000::154"],
            ipv6_available=False,
            current_selection={self.SERVICE: "hosts"},
        )
        self.assertEqual(plan.new_selection, {self.SERVICE: "hosts"})
        self.assertFalse(plan.selection_changed)

    def test_ipv6_only_service_is_enabled_with_ipv6(self) -> None:
        plan = self._build_plan(
            candidate_ips=["2606:50c0:8000::154"],
            ipv6_available=True,
            current_selection={self.SERVICE: "hosts"},
        )
        row = self._single_row(plan)
        self.assertTrue(row.toggle_enabled)
        self.assertTrue(row.toggle_checked)
        self.assertIsNone(row.unavailable_reason)
        self.assertFalse(plan.selection_changed)

    def test_mixed_service_is_not_gated_without_ipv6(self) -> None:
        plan = self._build_plan(
            candidate_ips=["185.199.108.133", "2606:50c0:8000::154"],
            ipv6_available=False,
            current_selection={self.SERVICE: "hosts"},
        )
        row = self._single_row(plan)
        self.assertTrue(row.toggle_enabled)
        self.assertTrue(row.toggle_checked)
        self.assertIsNone(row.unavailable_reason)


if __name__ == "__main__":
    unittest.main()
