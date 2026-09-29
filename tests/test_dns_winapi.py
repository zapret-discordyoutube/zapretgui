from __future__ import annotations

import ctypes
import sys
import unittest
from unittest.mock import patch

from dns import winapi


class _FakeIpHelper:
    """Записывает, что именно ушло в SetInterfaceDnsSettings."""

    def __init__(self, result: int = 0) -> None:
        self.result = result
        self.calls: list[dict] = []

    def SetInterfaceDnsSettings(self, guid, settings_ptr):  # noqa: N802
        base = ctypes.cast(settings_ptr, ctypes.POINTER(winapi.DNS_INTERFACE_SETTINGS)).contents
        call = {"guid": winapi.guid_to_string(guid), "version": base.Version, "flags": base.Flags, "name_server": base.NameServer}
        if base.Version == winapi.DNS_INTERFACE_SETTINGS_VERSION3:
            full = ctypes.cast(settings_ptr, ctypes.POINTER(winapi.DNS_INTERFACE_SETTINGS3)).contents
            call["properties"] = [
                (
                    full.ServerProperties[index].ServerIndex,
                    full.ServerProperties[index].Type,
                    full.ServerProperties[index].Property.DohSettings.contents.Template,
                    full.ServerProperties[index].Property.DohSettings.contents.Flags,
                )
                for index in range(full.cServerProperties)
            ]
        self.calls.append(call)
        return self.result


GUID_TEXT = "{553DA581-A1E2-4F9E-BDEF-E3DC2C1CE39F}"


class DnsWinApiTests(unittest.TestCase):
    def test_structures_match_windows_sdk_sizes(self) -> None:
        # Проверено на Windows x64: sizeof(MIB_IF_ROW2) == 1352 (wchar_t там 2 байта).
        if sys.platform == "win32" and ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(winapi.MIB_IF_ROW2), 1352)
        self.assertEqual(ctypes.sizeof(winapi.GUID), 16)

    def test_guid_round_trip(self) -> None:
        guid = winapi.guid_from_string(GUID_TEXT)

        self.assertEqual(winapi.guid_to_string(guid), GUID_TEXT)
        self.assertEqual(winapi.guid_to_string(winapi.guid_from_string(GUID_TEXT.strip("{}").lower())), GUID_TEXT)
        with self.assertRaises(ValueError):
            winapi.guid_from_string("Ethernet")

    def test_name_servers_are_split_without_duplicates(self) -> None:
        self.assertEqual(winapi.split_name_servers("1.1.1.1,1.0.0.1; 1.1.1.1"), ("1.1.1.1", "1.0.0.1"))
        self.assertEqual(winapi.split_name_servers("2606:4700::1111 2606:4700::1001"), ("2606:4700::1111", "2606:4700::1001"))
        self.assertEqual(winapi.split_name_servers(None), ())

    def test_plain_write_uses_version1_settings(self) -> None:
        fake = _FakeIpHelper()
        with patch.object(winapi, "_iphlpapi", return_value=fake):
            winapi.write_dns(GUID_TEXT, ["8.8.8.8", "8.8.4.4", "8.8.8.8"], ipv6=False)

        call = fake.calls[0]
        self.assertEqual(call["guid"], GUID_TEXT)
        self.assertEqual(call["version"], winapi.DNS_INTERFACE_SETTINGS_VERSION1)
        self.assertEqual(call["flags"], winapi.DNS_SETTING_NAMESERVER)
        self.assertEqual(call["name_server"], "8.8.8.8,8.8.4.4")

    def test_doh_write_attaches_template_to_known_servers(self) -> None:
        fake = _FakeIpHelper()
        with patch.object(winapi, "_iphlpapi", return_value=fake):
            winapi.write_dns(
                GUID_TEXT,
                ["2606:4700:4700::1111", "2001:db8::1"],
                ipv6=True,
                doh_templates={"2606:4700:4700::1111": "https://cloudflare-dns.com/dns-query"},
            )

        call = fake.calls[0]
        self.assertEqual(call["version"], winapi.DNS_INTERFACE_SETTINGS_VERSION3)
        self.assertEqual(call["flags"], winapi.DNS_SETTING_NAMESERVER | winapi.DNS_SETTING_IPV6 | winapi.DNS_SETTING_DOH)
        self.assertEqual(call["name_server"], "2606:4700:4700::1111 2001:db8::1")
        self.assertEqual(
            call["properties"],
            [
                (
                    0,
                    winapi.DNS_SERVER_DOH_PROPERTY,
                    "https://cloudflare-dns.com/dns-query",
                    winapi.DNS_DOH_SERVER_SETTINGS_ENABLE | winapi.DNS_DOH_SERVER_SETTINGS_FALLBACK_TO_UDP,
                )
            ],
        )

    def test_empty_write_returns_to_automatic_and_errors_are_readable(self) -> None:
        fake = _FakeIpHelper(result=5)
        with patch.object(winapi, "_iphlpapi", return_value=fake):
            with self.assertRaises(winapi.DnsWinApiError) as error:
                winapi.write_dns(GUID_TEXT, [], ipv6=False, doh_templates={})

        self.assertEqual(fake.calls[0]["name_server"], "")
        self.assertIn("SetInterfaceDnsSettings: ошибка Windows 5", str(error.exception))

    def test_interface_chain_skips_loopback_and_reads_dns_servers(self) -> None:
        def sockaddr_v4(address: str):
            raw = (ctypes.c_ubyte * 16)(2, 0, 0, 0, *[int(part) for part in address.split(".")])
            return raw

        keep: list[object] = []

        def dns_node(address: str, next_node=None):
            raw = sockaddr_v4(address)
            node = winapi.IP_ADAPTER_DNS_SERVER_ADDRESS()
            node.Address.lpSockaddr = ctypes.cast(raw, ctypes.c_void_p)
            node.Address.iSockaddrLength = 16
            if next_node is not None:
                node.Next = ctypes.pointer(next_node)
            keep.extend([raw, node])
            return node

        def adapter(index, if_type, name, guid, dns=None, next_node=None):
            item = winapi.IP_ADAPTER_ADDRESSES()
            item.IfIndex = index
            item.IfType = if_type
            item.OperStatus = winapi.IF_OPER_STATUS_UP
            item.FriendlyName = name
            item.Description = name + " desc"
            item.AdapterName = guid.encode("ascii")
            if dns is not None:
                item.FirstDnsServerAddress = ctypes.pointer(dns)
            if next_node is not None:
                item.Next = ctypes.pointer(next_node)
            keep.append(item)
            return item

        ethernet = adapter(9, winapi.IF_TYPE_ETHERNET_CSMACD, "Ethernet", GUID_TEXT, dns=dns_node("1.1.1.1", dns_node("8.8.8.8")))
        loopback = adapter(1, winapi.IF_TYPE_SOFTWARE_LOOPBACK, "Loopback", "{00000000-0000-0000-0000-000000000001}", next_node=ethernet)

        with patch.object(winapi, "_interface_flags", return_value=winapi.IF_FLAG_HARDWARE | winapi.IF_FLAG_CONNECTOR_PRESENT):
            interfaces = winapi._read_interfaces(ctypes.pointer(loopback))

        self.assertEqual(len(interfaces), 1)
        self.assertEqual(interfaces[0].name, "Ethernet")
        self.assertEqual(interfaces[0].guid, GUID_TEXT)
        self.assertEqual(interfaces[0].dns_ipv4, ("1.1.1.1", "8.8.8.8"))
        self.assertTrue(interfaces[0].hardware and interfaces[0].connector and interfaces[0].connected)

    def test_hidden_interfaces_are_not_requested(self) -> None:
        source = open(winapi.__file__, encoding="utf-8").read()

        self.assertNotIn("GAA_FLAG_INCLUDE_ALL_INTERFACES", source.split('"""', 2)[2])
        self.assertNotIn("winreg", source)

    def test_doh_needs_windows_11(self) -> None:
        with patch.object(winapi, "windows_build", return_value=19045):
            self.assertFalse(winapi.is_doh_supported())
        with patch.object(winapi, "windows_build", return_value=22631):
            self.assertTrue(winapi.is_doh_supported())


if __name__ == "__main__":
    unittest.main()
