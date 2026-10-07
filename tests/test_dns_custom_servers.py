"""Правила своих DNS-серверов: разбор полей, список, плитки и команды сохранения."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from dns import commands, custom_servers
from dns.custom_servers import (
    CUSTOM_DNS_CATEGORY,
    FIELD_ADDRESSES,
    FIELD_DOH,
    FIELD_NAME,
    build_dns_providers_with_custom,
    custom_doh_templates,
    parse_doh_template,
    read_form,
    split_addresses,
)
from dns.doh_lookup import DohLookup

HOME = {"id": "home", "name": "Дом", "ipv4": ["192.168.1.1"], "ipv6": [], "doh": ""}
SECURE = {
    "id": "secure",
    "name": "dns.example.com",
    "ipv4": ["203.0.113.5", "203.0.113.6"],
    "ipv6": ["2001:db8::5"],
    "doh": "https://dns.example.com/dns-query",
}


class DohTemplateTests(unittest.TestCase):
    def test_address_is_parsed_into_host_port_and_path(self) -> None:
        template = parse_doh_template(" https://DNS.Example.com/dns-query ")

        self.assertEqual(
            (template.url, template.host, template.port, template.path),
            ("https://dns.example.com/dns-query", "dns.example.com", 443, "/dns-query"),
        )
        self.assertFalse(template.host_is_address)

    def test_scheme_and_path_may_be_omitted(self) -> None:
        self.assertEqual(parse_doh_template("dns.example.com/abc123").url, "https://dns.example.com/abc123")
        self.assertEqual(parse_doh_template("dns.example.com").url, "https://dns.example.com/dns-query")

    def test_template_suffix_and_custom_port_are_understood(self) -> None:
        self.assertEqual(
            parse_doh_template("https://dns.example.com/dns-query{?dns}").url,
            "https://dns.example.com/dns-query",
        )
        template = parse_doh_template("https://dns.example.com:444/dns-query")
        self.assertEqual((template.port, template.url), (444, "https://dns.example.com:444/dns-query"))

    def test_server_may_be_given_by_address(self) -> None:
        self.assertTrue(parse_doh_template("https://9.9.9.9/dns-query").host_is_address)
        template = parse_doh_template("https://[2620:fe::fe]/dns-query")
        self.assertEqual((template.host, template.url), ("2620:fe::fe", "https://[2620:fe::fe]/dns-query"))

    def test_wrong_addresses_are_rejected(self) -> None:
        for text in (
            "",
            "http://dns.example.com/dns-query",
            "https://dns example.com/dns-query",
            "localhost",
            "https://dns.example.com:99999/dns-query",
            "https://user@dns.example.com/dns-query",
            "https://dns.example.com/dns-query?dns=abc",
            # Недописанный IP-адрес — не имя сервера.
            "149.112.",
            "149.112",
        ):
            with self.subTest(text=text):
                self.assertIsNone(parse_doh_template(text))


class FormTests(unittest.TestCase):
    def test_addresses_are_split_by_family_in_written_order(self) -> None:
        ipv4, ipv6, bad = split_addresses("8.8.8.8, 8.8.4.4;2001:4860:4860::8888  [2001:4860:4860::8844] кот 8.8.8.8")

        self.assertEqual(ipv4, ["8.8.8.8", "8.8.4.4"])
        self.assertEqual(ipv6, ["2001:4860:4860::8888", "2001:4860:4860::8844"])
        self.assertEqual(bad, ["кот"])

    def test_one_doh_line_is_enough_and_names_the_server(self) -> None:
        form = read_form(doh="https://dns.example.com/dns-query")

        self.assertEqual(form.error, "")
        self.assertTrue(form.needs_lookup)
        self.assertEqual(form.server["name"], "dns.example.com")
        self.assertEqual(form.server["doh"], "https://dns.example.com/dns-query")
        self.assertEqual((form.server["ipv4"], form.server["ipv6"]), ([], []))
        self.assertTrue(form.server["id"].startswith("custom-"))

    def test_addresses_alone_make_a_plain_server_named_by_first_address(self) -> None:
        form = read_form(addresses="9.9.9.9 149.112.112.112 2620:fe::fe")

        self.assertFalse(form.needs_lookup)
        self.assertEqual(
            form.server | {"id": ""},
            {"id": "", "name": "9.9.9.9", "ipv4": ["9.9.9.9", "149.112.112.112"], "ipv6": ["2620:fe::fe"], "doh": ""},
        )

    def test_editing_keeps_the_server_id_and_cleans_the_name(self) -> None:
        form = read_form(server_id="home", name="  Мой   DNS ", addresses="10.0.0.1")

        self.assertEqual((form.server["id"], form.server["name"]), ("home", "Мой DNS"))

    def test_errors_name_the_field(self) -> None:
        self.assertEqual(read_form().field, FIELD_DOH)
        self.assertEqual(read_form(doh="http://dns.example.com").field, FIELD_DOH)
        wrong = read_form(doh="https://dns.example.com/dns-query", addresses="9.9.9.9 кот")
        self.assertEqual(wrong.field, FIELD_ADDRESSES)
        self.assertIn("кот", wrong.error)
        self.assertIsNone(wrong.server)


class PastedTextTests(unittest.TestCase):
    def test_doh_with_addresses_is_split_into_two_fields(self) -> None:
        self.assertEqual(
            custom_servers.split_pasted("https://dns.example.com/dns-query, 203.0.113.5, 2001:db8::5"),
            ("https://dns.example.com/dns-query", "203.0.113.5 2001:db8::5"),
        )
        # Именно так кладёт сервер в буфер «Копировать DNS».
        self.assertEqual(
            custom_servers.split_pasted(custom_servers.clipboard_text(SECURE)),
            ("https://dns.example.com/dns-query", "203.0.113.5 203.0.113.6 2001:db8::5"),
        )

    def test_unclear_text_is_left_alone(self) -> None:
        for text in (
            "9.9.9.9 149.112.112.112",
            "https://dns.example.com/dns-query",
            "кот 9.9.9.9",
            "a.example/q b.example/q 9.9.9.9",
            "9.9.9.9 149.112.",
            "",
        ):
            with self.subTest(text=text):
                self.assertIsNone(custom_servers.split_pasted(text))


class ServerListTests(unittest.TestCase):
    def test_new_server_is_appended_and_known_id_is_replaced(self) -> None:
        servers, error = custom_servers.upsert_server([HOME], SECURE)
        self.assertEqual((error, [item["id"] for item in servers]), ("", ["home", "secure"]))

        renamed = dict(HOME, name="Дача", ipv4=["10.0.0.1"])
        servers, error = custom_servers.upsert_server(servers, renamed)
        self.assertEqual(error, "")
        self.assertEqual([(item["id"], item["name"]) for item in servers], [("home", "Дача"), ("secure", "dns.example.com")])

    def test_name_cannot_repeat_another_server_or_a_builtin_one(self) -> None:
        clash = dict(SECURE, name="дом")
        servers, error = custom_servers.upsert_server([HOME], clash)
        self.assertIn("«дом»", error)
        self.assertEqual(servers, [HOME])

        _servers, error = custom_servers.upsert_server([], dict(SECURE, name="Quad9"), reserved_names=["Quad9"])
        self.assertIn("Quad9", error)
        # Свой же сервер под прежним названием сохранить можно.
        self.assertEqual(custom_servers.upsert_server([HOME], dict(HOME, ipv4=["10.0.0.1"]))[1], "")

    def test_duplicate_gets_new_id_and_free_name(self) -> None:
        servers = custom_servers.duplicate_server([HOME, dict(SECURE, name="Дом копия")], "home")

        self.assertEqual(len(servers), 3)
        self.assertEqual(servers[2]["name"], "Дом копия 2")
        self.assertNotEqual(servers[2]["id"], "home")
        self.assertEqual(servers[2]["ipv4"], ["192.168.1.1"])
        self.assertEqual(custom_servers.duplicate_server([HOME], "missing"), [HOME])

    def test_remove_drops_only_the_named_server(self) -> None:
        self.assertEqual(custom_servers.remove_server([HOME, SECURE], "home"), [SECURE])

    def test_clipboard_text_carries_doh_and_addresses(self) -> None:
        self.assertEqual(custom_servers.clipboard_text(HOME), "192.168.1.1")
        self.assertEqual(
            custom_servers.clipboard_text(SECURE),
            "https://dns.example.com/dns-query, 203.0.113.5, 203.0.113.6, 2001:db8::5",
        )
        self.assertEqual(custom_servers.addresses_text(SECURE), "203.0.113.5 203.0.113.6 2001:db8::5")


class ProviderTests(unittest.TestCase):
    def test_custom_servers_become_a_group_with_doh(self) -> None:
        providers = build_dns_providers_with_custom({"Основные": {"Quad9": {"ipv4": ["9.9.9.9"]}}}, [HOME, SECURE])

        group = providers[CUSTOM_DNS_CATEGORY]
        self.assertEqual(list(group), ["Дом", "dns.example.com"])
        self.assertNotIn("doh", group["Дом"])
        self.assertEqual(group["dns.example.com"]["doh"], "https://dns.example.com/dns-query")
        self.assertEqual(group["dns.example.com"]["custom_id"], "secure")
        self.assertIn("Quad9", providers["Основные"])

    def test_doh_templates_cover_every_address_of_servers_with_doh(self) -> None:
        self.assertEqual(
            custom_doh_templates([HOME, SECURE]),
            {
                "203.0.113.5": "https://dns.example.com/dns-query",
                "203.0.113.6": "https://dns.example.com/dns-query",
                "2001:db8::5": "https://dns.example.com/dns-query",
            },
        )


class _Store:
    """Список своих DNS в памяти вместо settings.sqlite3."""

    def __init__(self, servers=()):
        self.servers = [dict(item) for item in servers]

    def update(self, mutator):
        self.servers = [dict(item) for item in mutator([dict(item) for item in self.servers])]
        return [dict(item) for item in self.servers]

    def read(self):
        return [dict(item) for item in self.servers]


class CommandTests(unittest.TestCase):
    def _store(self, servers=()):
        store = _Store(servers)
        for target, replacement in (
            ("settings.store.update_custom_dns_servers", store.update),
            ("settings.store.get_custom_dns_servers", store.read),
        ):
            patcher = patch(target, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        return store

    def _lookup(self, result):
        winapi = patch.multiple(
            "dns.winapi",
            internet_route=lambda: type("Route", (), {"has_ipv6": True})(),
            is_doh_supported=lambda: True,
        )
        winapi.start()
        self.addCleanup(winapi.stop)
        patcher = patch("dns.doh_lookup.find_doh_addresses", return_value=result)
        found = patcher.start()
        self.addCleanup(patcher.stop)
        return found

    def test_server_with_addresses_is_saved_without_network(self) -> None:
        store = self._store()
        lookup = self._lookup(DohLookup(host="x"))

        result = commands.save_custom_server(dict(HOME))

        self.assertTrue(result.success)
        self.assertEqual(result.server["name"], "Дом")
        self.assertEqual([item["id"] for item in store.servers], ["home"])
        lookup.assert_not_called()

    def test_doh_only_server_gets_found_addresses(self) -> None:
        store = self._store([HOME])
        lookup = self._lookup(
            DohLookup(host="dns.example.com", ipv4=("203.0.113.5",), ipv6=("2001:db8::5",), notice="оговорка")
        )
        cancel = object()

        result = commands.save_custom_server(dict(SECURE, ipv4=[], ipv6=[]), cancel=cancel)

        self.assertTrue(result.success)
        self.assertEqual(result.notice, "оговорка")
        self.assertEqual((result.server["ipv4"], result.server["ipv6"]), (["203.0.113.5"], ["2001:db8::5"]))
        self.assertEqual(store.servers[1]["ipv4"], ["203.0.113.5"])
        template = lookup.call_args.args[0]
        self.assertEqual(template.host, "dns.example.com")
        self.assertEqual(lookup.call_args.kwargs, {"ipv6": True, "doh_supported": True, "cancel": cancel})

    def test_failed_lookup_saves_nothing_and_points_at_doh_field(self) -> None:
        store = self._store([HOME])
        self._lookup(DohLookup(host="dns.example.com", error="сервер молчит"))

        result = commands.save_custom_server(dict(SECURE, ipv4=[], ipv6=[]))

        self.assertFalse(result.success)
        self.assertEqual((result.error, result.field), ("сервер молчит", FIELD_DOH))
        self.assertEqual(store.servers, [HOME])

    def test_taken_name_is_reported_for_the_name_field(self) -> None:
        store = self._store([HOME])
        self._lookup(DohLookup(host="x"))

        clash = commands.save_custom_server(dict(SECURE, name="Дом"))
        builtin = commands.save_custom_server(dict(SECURE, name="Quad9"))

        self.assertEqual((clash.success, clash.field), (False, FIELD_NAME))
        self.assertEqual((builtin.success, builtin.field), (False, FIELD_NAME))
        self.assertEqual(clash.servers, (HOME,))
        self.assertEqual(store.servers, [HOME])

    def test_delete_and_duplicate_return_the_new_list(self) -> None:
        store = self._store([HOME, SECURE])

        copied = commands.duplicate_custom_server("home")
        self.assertEqual([item["name"] for item in copied.servers], ["Дом", "dns.example.com", "Дом копия"])

        deleted = commands.delete_custom_server("home")
        self.assertTrue(deleted.success)
        self.assertEqual([item["name"] for item in store.servers], ["dns.example.com", "Дом копия"])


if __name__ == "__main__":
    unittest.main()
