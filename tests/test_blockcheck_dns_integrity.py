"""DNS-проверка BlockCheck: подмена определяется сертификатом, а не сравнением IP.

Регрессия, ради которой написаны тесты: Facebook и x.com помечались как «DNS
подмена» на сети без подмены — их anycast/geo-DNS штатно отдаёт системному
резолверу и DoH разные адреса.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from blockcheck.dns_integrity import _detect_stub_ips, judge_domain  # noqa: E402
from blockcheck.models import DnsVerdict  # noqa: E402


class _Verifier:
    """Подставной сертификатный пробник: сеть в тестах не трогаем."""

    def __init__(self, answers: dict[str, bool | None]):
        self.answers = answers
        self.calls: list[tuple[str, str]] = []

    def __call__(self, domain: str, ip: str) -> bool | None:
        self.calls.append((domain, ip))
        return self.answers.get(ip)


class JudgeDomainTests(unittest.TestCase):
    def test_geo_dns_mismatch_with_valid_cert_is_not_spoofing(self) -> None:
        verify = _Verifier({"1.2.3.4": True})

        result = judge_domain(
            "www.facebook.com", ["1.2.3.4"], ["5.6.7.8"], set(), verify=verify,
        )

        self.assertEqual(result.verdict, DnsVerdict.OK)
        self.assertTrue(result.is_consistent)
        self.assertIn("сертификат валиден", result.evidence)

    def test_matching_ips_skip_the_certificate_probe(self) -> None:
        verify = _Verifier({})

        result = judge_domain(
            "discord.com", ["1.2.3.4"], ["1.2.3.4", "9.9.9.9"], set(), verify=verify,
        )

        self.assertEqual(result.verdict, DnsVerdict.OK)
        self.assertEqual(verify.calls, [])

    def test_stub_ip_is_spoofing_without_a_probe(self) -> None:
        verify = _Verifier({})

        result = judge_domain(
            "rutracker.org", ["10.10.10.10"], ["1.2.3.4"], {"10.10.10.10"}, verify=verify,
        )

        self.assertEqual(result.verdict, DnsVerdict.FAKE)
        self.assertTrue(result.is_stub)
        self.assertEqual(result.stub_ip, "10.10.10.10")
        self.assertEqual(verify.calls, [])

    def test_wrong_certificate_on_isp_address_is_spoofing(self) -> None:
        verify = _Verifier({"1.1.1.1": False, "5.6.7.8": True})

        result = judge_domain(
            "rutracker.org", ["1.1.1.1"], ["5.6.7.8"], set(), verify=verify,
        )

        self.assertEqual(result.verdict, DnsVerdict.FAKE)
        self.assertFalse(result.is_consistent)

    def test_wrong_certificate_everywhere_is_channel_interception(self) -> None:
        """Если сертификат невалиден и на адресе от DoH — виноват не DNS."""
        verify = _Verifier({"1.1.1.1": False, "5.6.7.8": False})

        result = judge_domain(
            "rutracker.org", ["1.1.1.1"], ["5.6.7.8"], set(), verify=verify,
        )

        self.assertEqual(result.verdict, DnsVerdict.INCONCLUSIVE)

    def test_unreachable_address_gives_no_dns_conclusion(self) -> None:
        verify = _Verifier({"1.1.1.1": None})

        result = judge_domain(
            "blocked.example", ["1.1.1.1"], ["5.6.7.8"], set(), verify=verify,
        )

        self.assertEqual(result.verdict, DnsVerdict.INCONCLUSIVE)

    def test_missing_doh_still_allows_a_verdict_via_certificate(self) -> None:
        verify = _Verifier({"1.1.1.1": True})

        result = judge_domain("site.example", ["1.1.1.1"], [], set(), verify=verify)

        self.assertEqual(result.verdict, DnsVerdict.OK)
        self.assertFalse(result.is_comparable)

    def test_no_udp_answer_is_inconclusive(self) -> None:
        verify = _Verifier({})

        result = judge_domain("site.example", [], ["5.6.7.8"], set(), verify=verify)

        self.assertEqual(result.verdict, DnsVerdict.INCONCLUSIVE)
        self.assertEqual(verify.calls, [])


class StubDetectionTests(unittest.TestCase):
    def test_one_address_across_unrelated_domains_is_a_stub(self) -> None:
        udp = {
            "rutracker.org": ["203.0.113.9"],
            "linkedin.com": ["203.0.113.9"],
            "meduza.io": ["203.0.113.9"],
        }

        self.assertIn("203.0.113.9", _detect_stub_ips(udp, len(udp)))

    def test_shared_address_of_sibling_domains_is_legitimate(self) -> None:
        udp = {
            "telegram.org": ["149.154.167.99"],
            "web.telegram.org": ["149.154.167.99"],
            "discord.com": ["162.159.128.233"],
        }

        self.assertEqual(_detect_stub_ips(udp, len(udp)), set())

    def test_known_block_address_is_a_stub_on_its_own(self) -> None:
        udp = {"rutracker.org": ["62.33.207.196"]}

        self.assertIn("62.33.207.196", _detect_stub_ips(udp, 1))


if __name__ == "__main__":
    unittest.main()
