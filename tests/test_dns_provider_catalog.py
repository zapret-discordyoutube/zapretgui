import ast
import unittest
from pathlib import Path

from dns.dns_providers import DNS_PROVIDERS


def _load_doh_templates_from_source() -> dict[str, str]:
    source_path = Path(__file__).resolve().parents[1] / "src" / "dns" / "dns_core.py"
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "DOH_TEMPLATES":
                    value = ast.literal_eval(node.value)
                    if isinstance(value, dict):
                        return value
    raise AssertionError("DOH_TEMPLATES not found")


class DNSProviderCatalogTests(unittest.TestCase):
    def test_xbox_dns_profiles_are_ordered_and_preserved(self) -> None:
        providers = DNS_PROVIDERS["Для ИИ"]
        names = list(providers)

        self.assertLess(names.index("Xbox DNS"), names.index("Xbox DNS v2"))
        self.assertLess(names.index("Xbox DNS v2"), names.index("Xbox DNS (old)"))
        self.assertEqual(
            providers["Xbox DNS"]["ipv4"],
            ["111.88.96.54", "111.88.96.55"],
        )
        self.assertEqual(
            providers["Xbox DNS"]["ipv6"],
            ["2a00:ab00:1233:26::50", "2a00:ab00:1233:26::51"],
        )
        self.assertEqual(
            providers["Xbox DNS v2"]["ipv4"],
            ["87.228.47.200", "87.228.47.201"],
        )
        self.assertEqual(
            providers["Xbox DNS (old)"]["ipv4"],
            ["176.99.11.77", "80.78.247.254"],
        )

    def test_xbox_dns_addresses_have_doh_templates(self) -> None:
        doh_templates = _load_doh_templates_from_source()
        expected_template = "https://xbox-dns.ru/dns-query"
        for address in (
            "111.88.96.54",
            "111.88.96.55",
            "111.88.96.50",
            "111.88.96.51",
            "87.228.47.200",
            "87.228.47.201",
            "176.99.11.77",
            "80.78.247.254",
        ):
            self.assertEqual(doh_templates[address], expected_template)

    def test_malw_dns_uses_current_addresses_with_doh_template(self) -> None:
        provider = DNS_PROVIDERS["Для ИИ"]["dns.malw.link"]
        self.assertEqual(provider["ipv4"], ["95.216.204.218", "80.253.249.40"])
        self.assertEqual(
            provider["ipv6"],
            ["2a01:4f9:c014:6dac::1", "2a12:bec4:1460:5b7::2"],
        )
        doh_templates = _load_doh_templates_from_source()
        for address in provider["ipv4"]:
            self.assertEqual(doh_templates[address], provider["doh"])


if __name__ == "__main__":
    unittest.main()
