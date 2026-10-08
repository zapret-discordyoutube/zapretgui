from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from dns.dns_providers import DNS_PROVIDERS
from dns.ui.now_panel import DnsBadge
from dns.ui.provider_grid import DnsProviderGrid, DnsTile
from hosts.ui.profile_icons import profile_icon
from profile.ui import profile_icon as profile_icon_module
from profile.ui.own_icons import OWN_ICON_SVGS
from profile.ui.simple_icons_bundle import SIMPLE_ICON_SVGS


# Сервер со страницы «Настройка DNS» -> его DNS-профиль в «Редакторе hosts».
_SAME_SERVER_IN_HOSTS = {
    "Xbox DNS": "xbox_dns",
    "Comss DNS": "comss_dns",
    "AstraCat": "astracat",
    "GeoHide": "geohide",
    "DNS-AI": "dns_ai",
}


def _providers() -> dict[str, dict]:
    return {name: data for providers in DNS_PROVIDERS.values() for name, data in providers.items()}


class DnsProviderIconTests(unittest.TestCase):
    """Значки серверов на странице «Настройка DNS»."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        profile_icon_module._PROFILE_PIXMAP_CACHE.clear()

    def test_every_logo_of_the_list_can_be_drawn(self) -> None:
        """После правки значков: PYTHONPATH=src python tools/generate_profile_icon_bundle.py"""
        for name, data in _providers().items():
            icon = str(data["icon"])
            with self.subTest(provider=name):
                slug = icon.partition(":")[2].partition(":")[0]
                if icon.startswith("simple:"):
                    self.assertIn(slug, SIMPLE_ICON_SVGS)
                elif icon.startswith("own:"):
                    self.assertIn(slug, OWN_ICON_SVGS)
                else:
                    self.assertRegex(icon, r"^fa5[sb]\.[a-z0-9-]+$")

    def test_known_services_show_their_own_logo(self) -> None:
        providers = _providers()

        self.assertEqual(providers["Cloudflare"]["icon"], "simple:cloudflare:CF")
        self.assertEqual(providers["Quad9"]["icon"], "simple:quad9:Q9")
        self.assertEqual(providers["AdGuard"]["icon"], "simple:adguard:AG")
        self.assertEqual(providers["OpenDNS"]["icon"], "own:opendns:OD")
        self.assertEqual(providers["Google DNS"]["icon"], "simple:google:G")
        self.assertEqual(providers["Gcore"]["icon"], "simple:gcore:GC")
        self.assertEqual(providers["DNS4EU"]["icon"], "simple:europeanunion:EU")
        self.assertEqual(providers["Яндекс DNS"]["icon"], "fa5b.yandex")

    def test_no_two_servers_look_the_same(self) -> None:
        looks = [(data["icon"], str(data["color"]).lower()) for data in _providers().values()]

        self.assertEqual(len(looks), len(set(looks)))

    def test_server_has_the_same_icon_as_its_hosts_profile(self) -> None:
        providers = _providers()

        for position, (name, profile_id) in enumerate(_SAME_SERVER_IN_HOSTS.items()):
            with self.subTest(provider=name):
                icon_name, color = profile_icon(profile_id, position)
                self.assertEqual(providers[name]["icon"], icon_name)
                self.assertEqual(str(providers[name]["color"]).lower(), color.lower())

    def test_tile_draws_the_logo_and_not_a_letter_stub(self) -> None:
        grid = DnsProviderGrid()
        self.addCleanup(grid.deleteLater)
        grid.resize(560, 100)
        grid.set_tiles(
            [
                DnsTile(
                    kind="provider",
                    key="Cloudflare",
                    title="Cloudflare",
                    address="1.1.1.1",
                    icon_name="simple:cloudflare:CF",
                    color="#f48120",
                )
            ]
        )

        self.assertFalse(grid.grab().isNull())

        kinds = {key[0] for key in profile_icon_module._PROFILE_PIXMAP_CACHE}
        self.assertIn("simple", kinds)
        self.assertNotIn("initials", kinds)

    def test_now_badge_draws_the_logo_and_not_a_letter_stub(self) -> None:
        # Значок не показан на экране, поэтому «переворота монетки» нет:
        # сразу рисуется новый значок.
        badge = DnsBadge()
        self.addCleanup(badge.deleteLater)
        badge.set_icon("own:opendns:OD", "#fe7702")

        self.assertFalse(badge.grab().isNull())

        kinds = {key[0] for key in profile_icon_module._PROFILE_PIXMAP_CACHE}
        self.assertIn("own", kinds)
        self.assertNotIn("initials", kinds)


if __name__ == "__main__":
    unittest.main()
