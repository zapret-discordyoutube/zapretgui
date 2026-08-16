from __future__ import annotations

import ctypes
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class _FakeWindowsApi:
    def __getattr__(self, _name: str):
        return self

    def __call__(self, *_args, **_kwargs):
        return 0


def _load_dns_core():
    added_winreg_stub = False
    try:
        import winreg  # noqa: F401
    except ModuleNotFoundError:
        if "winreg" not in sys.modules:
            sys.modules["winreg"] = types.SimpleNamespace()
            added_winreg_stub = True

    added_windll_stub = not hasattr(ctypes, "windll")
    if added_windll_stub:
        ctypes.windll = _FakeWindowsApi()

    try:
        sys.modules.pop("dns.dns_core", None)
        return importlib.import_module("dns.dns_core")
    finally:
        if added_windll_stub:
            del ctypes.windll
        if added_winreg_stub:
            sys.modules.pop("winreg", None)


def _adapter(
    dns_core,
    *,
    name: str,
    description: str,
    guid: bytes,
    adapter_type: int,
    status: int,
):
    value = dns_core.IP_ADAPTER_ADDRESSES()
    value.AdapterName = guid
    value.FriendlyName = name
    value.Description = description
    value.IfType = adapter_type
    value.OperStatus = status
    value.IfIndex = 7
    return value


class DnsAdapterFilteringTests(unittest.TestCase):
    def test_native_chain_preserves_friendly_name_guid_and_status(self) -> None:
        dns_core = _load_dns_core()
        ethernet = _adapter(
            dns_core,
            name="Ethernet",
            description="Realtek PCIe GbE Family Controller",
            guid=b"{11111111-1111-1111-1111-111111111111}",
            adapter_type=dns_core.MIB_IF_TYPE_ETHERNET,
            status=dns_core.IF_OPER_STATUS_UP,
        )
        wifi = _adapter(
            dns_core,
            name="Wi-Fi",
            description="Intel(R) Wi-Fi 6 AX200",
            guid=b"22222222-2222-2222-2222-222222222222",
            adapter_type=dns_core.MIB_IF_TYPE_IEEE80211,
            status=2,
        )
        ethernet.Next = ctypes.pointer(wifi)

        adapters = dns_core._read_network_adapter_chain(ctypes.pointer(ethernet))

        self.assertEqual([item["name"] for item in adapters], ["Ethernet", "Wi-Fi"])
        self.assertEqual(
            adapters[0]["guid"],
            "{11111111-1111-1111-1111-111111111111}",
        )
        self.assertEqual(
            adapters[1]["guid"],
            "{22222222-2222-2222-2222-222222222222}",
        )
        self.assertTrue(adapters[0]["connected"])
        self.assertFalse(adapters[1]["connected"])

    def test_dns_changes_target_only_wifi_and_ethernet_adapters(self) -> None:
        dns_core = _load_dns_core()
        manager = dns_core.DNSManager()
        native_adapters = [
            {
                "name": "Ethernet",
                "description": "Realtek PCIe GbE Family Controller",
                "guid": "{11111111-1111-1111-1111-111111111111}",
                "type": dns_core.MIB_IF_TYPE_ETHERNET,
                "connected": False,
            },
            {
                "name": "Wi-Fi",
                "description": "Intel(R) Wi-Fi 6 AX200",
                "guid": "{22222222-2222-2222-2222-222222222222}",
                "type": dns_core.MIB_IF_TYPE_IEEE80211,
                "connected": True,
            },
            {
                "name": "Пидорашка",
                "description": "WAN Miniport (PPPOE)",
                "guid": "{33333333-3333-3333-3333-333333333333}",
                "type": dns_core.MIB_IF_TYPE_PPP,
                "connected": True,
            },
            {
                "name": "Сетевое подключение Bluetooth",
                "description": "Bluetooth Device (Personal Area Network)",
                "guid": "{44444444-4444-4444-4444-444444444444}",
                "type": dns_core.MIB_IF_TYPE_ETHERNET,
                "connected": False,
            },
            {
                "name": "vEthernet (WSL)",
                "description": "Hyper-V Virtual Ethernet Adapter",
                "guid": "{55555555-5555-5555-5555-555555555555}",
                "type": dns_core.MIB_IF_TYPE_ETHERNET,
                "connected": True,
            },
        ]

        with patch.object(
            dns_core,
            "get_network_adapters_native",
            return_value=native_adapters,
        ):
            adapters = manager.get_network_adapters_fast(
                include_ignored=False,
                include_disconnected=True,
            )

        self.assertEqual(
            [name for name, _description in adapters],
            ["Ethernet", "Wi-Fi"],
        )
        self.assertEqual(
            manager.get_adapter_guid("Ethernet"),
            "{11111111-1111-1111-1111-111111111111}",
        )

    def test_disconnected_filter_uses_native_operational_status(self) -> None:
        dns_core = _load_dns_core()
        manager = dns_core.DNSManager()
        native_adapters = [
            {
                "name": "Ethernet",
                "description": "Realtek",
                "guid": "{11111111-1111-1111-1111-111111111111}",
                "type": dns_core.MIB_IF_TYPE_ETHERNET,
                "connected": False,
            },
            {
                "name": "Wi-Fi",
                "description": "Intel Wi-Fi",
                "guid": "{22222222-2222-2222-2222-222222222222}",
                "type": dns_core.MIB_IF_TYPE_IEEE80211,
                "connected": True,
            },
        ]
        with patch.object(
            dns_core,
            "get_network_adapters_native",
            return_value=native_adapters,
        ):
            adapters = manager.get_network_adapters_fast(
                include_ignored=False,
                include_disconnected=False,
            )
        self.assertEqual(adapters, [("Wi-Fi", "Intel Wi-Fi")])

    def test_dns_runtime_has_no_wmi_or_legacy_adapter_api(self) -> None:
        dns_core = _load_dns_core()
        source = Path(dns_core.__file__).read_text(encoding="utf-8")

        self.assertIn("GetAdaptersAddresses", source)
        self.assertNotIn("import wmi", source)
        self.assertNotIn("GetAdaptersInfo", source)
        self.assertNotIn("_wmi_conn", source)


if __name__ == "__main__":
    unittest.main()
