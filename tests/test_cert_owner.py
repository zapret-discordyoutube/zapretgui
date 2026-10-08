"""Чужой сертификат: чей он и что с этим делать."""

from __future__ import annotations

import base64
import unittest

from diagnostics import cert_owner as co
from utils.cert_reader import CertNames, read_names

# Образцы сделаны для тестов (openssl), ключей от них нет.
# Выдан «Kaspersky Anti-Virus Personal Root Certificate» сайту chatgpt.com.
ANTIVIRUS = base64.b64decode("MIIB1DCCAXugAwIBAgIUQ/5UIZsucZp0d41CJkPG9cCmZAswCgYIKoZIzj0EAwIwVDEZMBcGA1UECgwQQU8gS2FzcGVyc2t5IExhYjE3MDUGA1UEAwwuS2FzcGVyc2t5IEFudGktVmlydXMgUGVyc29uYWwgUm9vdCBDZXJ0aWZpY2F0ZTAeFw0yNjEwMDgyMTQ3MDlaFw0zNjEwMDUyMTQ3MDlaMBYxFDASBgNVBAMMC2NoYXRncHQuY29tMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEW2EOUMQ6e9HWX5skDmSvTxNj1IxJC8Cwh/tvMf8xoJ/3YvpYj9X5ZPFX/wLg+k0+9LimaWzggCqclcRYou9o4qNpMGcwJQYDVR0RBB4wHIILY2hhdGdwdC5jb22CDSouY2hhdGdwdC5jb20wHQYDVR0OBBYEFF0AFm6sPoushogpGzudDdgsPaF2MB8GA1UdIwQYMBaAFMxWILFS30jkeDIf0SQUfoB8xu58MAoGCCqGSM49BAMCA0cAMEQCIBpHCcupTHrIACV6E+K3CWsZXnu2aBpZVQeqQ52zyLbJAiBBAYlGwMJx6dyBBVLd5HsY/7napSGP/pSGcUhtsnbyoA==")
# Выдан сам себе: proxy.local.
SELF_SIGNED = base64.b64decode("MIIBmTCCAT+gAwIBAgIURjLS8KsMIjnBbbNEkXKXe+4zCEswCgYIKoZIzj0EAwIwFjEUMBIGA1UEAwwLcHJveHkubG9jYWwwHhcNMjYxMDA4MjE0NzA5WhcNMzYxMDA1MjE0NzA5WjAWMRQwEgYDVQQDDAtwcm94eS5sb2NhbDBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABIsDXgSvwK3QngEHULtD95Vc83tkMV5YVg6Chj7FFGuBK9KDWt5m9wGIukIL3XNan/bYLGLFHrOnNO+P48SsWgqjazBpMB0GA1UdDgQWBBR1OEpcJZ5VXN+88DJz1T1GlzrMpzAfBgNVHSMEGDAWgBR1OEpcJZ5VXN+88DJz1T1GlzrMpzAPBgNVHRMBAf8EBTADAQH/MBYGA1UdEQQPMA2CC3Byb3h5LmxvY2FsMAoGCCqGSM49BAMCA0gAMEUCIQDzlk+F46bRuR0A/rfT1rF8iKaq+b2mqH8xxaq+Sa525QIgArAkdoAq6JB094y4NQH1emyhuWcwvSCwzm7T4gaKnSs=")
# Настоящий по виду сертификат другого сайта: other-site.example.
OTHER_SITE = base64.b64decode("MIIBvjCCAWSgAwIBAgIUAaPvTvkOc4F9bopVDP+2sn89BSgwCgYIKoZIzj0EAwIwJjEWMBQGA1UECgwNTGV0J3MgRW5jcnlwdDEMMAoGA1UEAwwDUjExMB4XDTI2MTAwODIxNDcwOVoXDTM2MTAwNTIxNDcwOVowHTEbMBkGA1UEAwwSb3RoZXItc2l0ZS5leGFtcGxlMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE3dV1tzgjq9NY0bsPcOFCi+nOtC3Z3IX57Y9vrDZe7gYlVa2O/Sdnba90VCcRntkiBSDUJ4tJGq56g6qU7so8W6N5MHcwNQYDVR0RBC4wLIISb3RoZXItc2l0ZS5leGFtcGxlghZ3d3cub3RoZXItc2l0ZS5leGFtcGxlMB0GA1UdDgQWBBSQejmp8FhSukOuzxSxwEFsoZ1zuzAfBgNVHSMEGDAWgBSyKVdLnDom+qTygTou1FNrC4TVOzAKBggqhkjOPQQDAgNIADBFAiB4UsbM+fzHkgxdVoo/rWPuX7oSii4JpiQXZF1HX5vjlgIhAJCDc7eC3FahlAoVDEJ4fOQI6OPuLnCfTs2OYsn7I3Hv")


class ReaderTests(unittest.TestCase):
    def test_reads_owner_issuer_and_site_names(self) -> None:
        names = read_names(ANTIVIRUS)

        self.assertEqual(names.subject, "chatgpt.com")
        self.assertEqual((names.issuer, names.issuer_org), ("Kaspersky Anti-Virus Personal Root Certificate", "AO Kaspersky Lab"))
        self.assertEqual(names.names, ("chatgpt.com", "*.chatgpt.com"))
        self.assertFalse(names.self_signed)

    def test_self_signed_is_noticed(self) -> None:
        names = read_names(SELF_SIGNED)

        self.assertTrue(names.self_signed)
        self.assertEqual((names.subject, names.names), ("proxy.local", ("proxy.local",)))

    def test_garbage_gives_nothing_instead_of_an_error(self) -> None:
        for broken in (None, b"", b"garbage", ANTIVIRUS[:40], b"\x30\x84\xff\xff\xff\xff", bytes(200)):
            self.assertIsNone(read_names(broken))


class JudgeTests(unittest.TestCase):
    def _judge(self, der, host="chatgpt.com", **kwargs) -> co.CertVerdict:
        return co.judge(read_names(der), host, **kwargs)

    def test_antivirus_is_named_and_is_not_called_a_block(self) -> None:
        verdict = self._judge(ANTIVIRUS)

        self.assertEqual(verdict.code, co.CERT_ANTIVIRUS)
        self.assertIn("Kaspersky", verdict.text)
        self.assertIn("не блокировка провайдера", verdict.advice)
        self.assertIn("Kaspersky Anti-Virus Personal Root Certificate", verdict.issuer)

    def test_hosts_record_leading_to_another_site_is_blamed_on_hosts(self) -> None:
        # Живой случай: посредник для ChatGPT из файла hosts сменился, по адресу теперь чужой сайт.
        verdict = self._judge(OTHER_SITE, from_hosts=True)

        self.assertEqual(verdict.code, co.CERT_HOSTS)
        self.assertIn("запись в файле hosts ведёт на чужой сервер", verdict.text)
        self.assertIn("other-site.example", verdict.text)
        self.assertIn("Редактор hosts", verdict.advice)

    def test_same_certificate_without_hosts_is_someone_on_the_road(self) -> None:
        verdict = self._judge(OTHER_SITE)

        self.assertEqual(verdict.code, co.CERT_OTHER_SITE)
        self.assertNotIn("hosts", verdict.text)
        self.assertIn("заглушка провайдера", verdict.advice)

    def test_self_signed_certificate(self) -> None:
        self.assertEqual(self._judge(SELF_SIGNED).code, co.CERT_SELF_SIGNED)
        self.assertIn("hosts", self._judge(SELF_SIGNED, from_hosts=True).text)

    def test_certificate_for_this_site_from_unknown_issuer_points_at_the_clock(self) -> None:
        # Имя подходит, издатель не антивирус: чаще всего это неверные часы Windows.
        names = CertNames(subject="chatgpt.com", issuer="Some CA", names=("chatgpt.com",))
        verdict = co.judge(names, "chatgpt.com", problem="сертификат просрочен")

        self.assertEqual(verdict.code, co.CERT_UNKNOWN_ISSUER)
        self.assertIn("сертификат просрочен", verdict.text)
        self.assertIn("дату и время", verdict.advice)

    def test_wildcard_covers_one_level_only(self) -> None:
        names = CertNames(subject="x", issuer="Some CA", names=("*.example.com",))

        self.assertEqual(co.judge(names, "www.example.com").code, co.CERT_UNKNOWN_ISSUER)
        self.assertEqual(co.judge(names, "a.b.example.com").code, co.CERT_OTHER_SITE)
        self.assertEqual(co.judge(names, "example.com").code, co.CERT_OTHER_SITE)

    def test_state_ca_and_debug_proxy_are_told_apart(self) -> None:
        state = CertNames(subject="sber.ru", issuer="Russian Trusted Sub CA", names=("sber.ru",))
        fiddler = CertNames(subject="chatgpt.com", issuer="DO_NOT_TRUST_FiddlerRoot", names=("chatgpt.com",))

        self.assertEqual(co.judge(state, "sber.ru").code, co.CERT_STATE)
        self.assertIn("не перехват", co.judge(state, "sber.ru").advice)
        self.assertEqual(co.judge(fiddler, "chatgpt.com").code, co.CERT_DEBUG_PROXY)

    def test_unreadable_certificate_still_says_someone_else_answered(self) -> None:
        verdict = co.judge(None, "chatgpt.com", problem="сертификат выдан другому сайту")

        self.assertEqual(verdict.code, co.CERT_UNKNOWN)
        self.assertIn("сертификат выдан другому сайту", verdict.text)


if __name__ == "__main__":
    unittest.main()
