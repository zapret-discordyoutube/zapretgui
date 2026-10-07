import unittest

from blockcheck.ui.result_cards_model import build_cards
from diagnostics import my_network as net
from utils.ip_owner import IpOwner

TRACE = b"fl=1\nh=one.one.one.one\nip=203.0.113.7\nts=1\nloc=RU\ntls=TLSv1.3\n"
OWNER = IpOwner(asn="12389", prefix="203.0.113.0/24", country="RU", owner="ROSTELECOM-AS")


class TraceTests(unittest.TestCase):
    def test_address_and_country_are_read_from_trace(self) -> None:
        self.assertEqual(net.parse_trace(TRACE), ("203.0.113.7", "RU"))
        self.assertEqual(net.parse_trace("ip=2a00:1450::1\nloc=de"), ("2a00:1450::1", "DE"))

    def test_garbage_gives_no_address(self) -> None:
        for body in (b"", b"<html>blocked</html>", b"ip=not-an-address\nloc=RU"):
            with self.subTest(body=body):
                self.assertEqual(net.parse_trace(body)[0], "")


class CollectTests(unittest.TestCase):
    def test_second_server_is_asked_when_the_first_is_silent(self) -> None:
        asked = []

        def fetch(server):
            asked.append(server)
            return None if server == net.TRACE_SERVERS[0] else TRACE

        facts = net.collect(fetch=fetch, owner_of=lambda ip: OWNER, local=lambda: "192.168.1.5")

        self.assertEqual(asked, list(net.TRACE_SERVERS))
        self.assertEqual((facts.external_ip, facts.country, facts.owner, facts.local_ip), ("203.0.113.7", "RU", OWNER, "192.168.1.5"))

    def test_owner_is_not_asked_without_an_address(self) -> None:
        owners = []
        facts = net.collect(fetch=lambda _server: None, owner_of=owners.append, local=lambda: "")

        self.assertEqual(owners, [])
        self.assertEqual(facts.external_ip, "")


class JudgeTests(unittest.TestCase):
    def _lines(self, **facts):
        return {line.name: line for line in net.judge(net.NetworkFacts(**facts))}

    def test_provider_network_and_router(self) -> None:
        lines = self._lines(external_ip="203.0.113.7", country="RU", owner=OWNER, local_ip="192.168.1.5")

        self.assertEqual(lines["Внешний адрес"].text, "203.0.113.7 (RU)")
        self.assertEqual(lines["Провайдер"].text, "ROSTELECOM-AS · AS12389, RU")
        self.assertEqual(lines["Сеть"].text, "203.0.113.0/24")
        self.assertIn("за роутером", lines["Адрес компьютера"].text)

    def test_carrier_address_means_shared_address(self) -> None:
        lines = self._lines(external_ip="203.0.113.7", owner=OWNER, local_ip="100.72.4.9")

        self.assertIn("общим адресом", lines["Адрес компьютера"].text)
        self.assertEqual(lines["Адрес компьютера"].state, "info")

    def test_direct_connection(self) -> None:
        lines = self._lines(external_ip="203.0.113.7", owner=OWNER, local_ip="203.0.113.7")

        self.assertIn("прямое подключение", lines["Адрес компьютера"].text)

    def test_public_local_address_that_differs_from_external_means_proxy_or_vpn(self) -> None:
        lines = self._lines(external_ip="203.0.113.7", owner=OWNER, local_ip="144.31.107.88")

        self.assertIn("прокси, VPN или общий адрес провайдера", lines["Адрес компьютера"].text)

    def test_unknown_is_said_plainly(self) -> None:
        lines = self._lines()
        self.assertEqual(lines["Внешний адрес"].state, "unknown")
        self.assertNotIn("Провайдер", lines)

        no_owner = self._lines(external_ip="203.0.113.7")
        self.assertEqual((no_owner["Провайдер"].state, no_owner["Провайдер"].text), ("unknown", "узнать не удалось"))

    def test_other_bypass_tools_are_a_warning_not_a_vpn_verdict(self) -> None:
        lines = {line.name: line for line in net.judge(net.NetworkFacts(external_ip="203.0.113.7"), bypass_tools=("WARP",))}

        self.assertEqual(lines["Другие программы обхода и VPN"].state, "warn")
        self.assertIn("WARP", lines["Другие программы обхода и VPN"].text)


class NetworkCardTests(unittest.TestCase):
    def _card(self, network):
        [card] = build_cards({"network": network})
        return card

    def test_card_names_the_provider(self) -> None:
        card = self._card({
            "external_ip": "203.0.113.7",
            "provider": "ROSTELECOM-AS · AS12389",
            "lines": [{"state": "info", "name": "Внешний адрес", "text": "203.0.113.7 (RU)"}],
        })

        self.assertEqual((card.key, card.title, card.status, card.level), ("network", "Ваша сеть", "ROSTELECOM-AS · AS12389", "info"))
        self.assertEqual(card.lines[0].text, "203.0.113.7 (RU)")
        self.assertFalse(card.site)

    def test_card_without_answer_and_with_warning(self) -> None:
        self.assertEqual(self._card({"lines": []}).status, "Не удалось узнать")
        self.assertEqual(self._card({"lines": []}).level, "unknown")
        self.assertEqual(self._card({"external_ip": "1.2.3.4", "lines": []}).status, "Провайдер не определён")
        warned = self._card({"external_ip": "1.2.3.4", "lines": [{"state": "warn", "name": "VPN", "text": "WARP"}]})
        self.assertEqual(warned.level, "warn")


if __name__ == "__main__":
    unittest.main()
