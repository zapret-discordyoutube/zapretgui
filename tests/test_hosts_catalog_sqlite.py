from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


PUBLIC_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PUBLIC_ROOT.parent
PRIVATE_DATABASE = PROJECT_ROOT / "private_zapretgui" / "resources" / "system" / "hosts_catalog.sqlite3"
SRC_ROOT = PUBLIC_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config import zapret_dns  # noqa: E402


class HostsCatalogSqliteTests(unittest.TestCase):
    def setUp(self) -> None:
        from hosts import proxy_domains

        proxy_domains.invalidate_hosts_catalog_cache()
        self.proxy_domains = proxy_domains

    def tearDown(self) -> None:
        self.proxy_domains.invalidate_hosts_catalog_cache()

    def _copy_database(self, root: Path) -> Path:
        destination = root / "hosts_catalog.sqlite3"
        shutil.copy2(PRIVATE_DATABASE, destination)
        return destination

    @staticmethod
    def _rehash_database(path: Path) -> None:
        from hosts.catalog_repository import compute_content_sha256

        connection = sqlite3.connect(path)
        try:
            digest = compute_content_sha256(connection)
            connection.execute(
                "UPDATE catalog_meta SET value = ? WHERE key = 'content_sha256'",
                (digest,),
            )
            connection.commit()
        finally:
            connection.close()

    def test_source_and_packaged_paths_point_only_to_data_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_module = root / "public_zapretgui" / "src" / "hosts" / "proxy_domains.py"
            fake_module.parent.mkdir(parents=True)
            fake_module.touch()
            with patch.object(self.proxy_domains, "__file__", str(fake_module)):
                self.assertEqual(
                    self.proxy_domains.get_hosts_catalog_path(),
                    root / "private_zapretgui" / "resources" / "system" / "hosts_catalog.sqlite3",
                )

            install_root = root / "Zapret" / "Dev"
            with (
                patch.object(self.proxy_domains, "PACKAGED_RUNTIME", True),
                patch.object(self.proxy_domains, "MAIN_DIRECTORY", str(install_root)),
            ):
                self.assertEqual(
                    self.proxy_domains.get_hosts_catalog_path(),
                    install_root / "system" / "hosts_catalog.sqlite3",
                )

    def test_tracked_catalog_is_complete_and_integral(self) -> None:
        from hosts.catalog_repository import (
            CATALOG_APPLICATION_ID,
            CATALOG_SCHEMA_VERSION,
            load_catalog,
        )

        catalog = load_catalog(PRIVATE_DATABASE)
        self.assertFalse(
            (PROJECT_ROOT / "private_zapretgui" / "resources" / "json" / "hosts_catalog").exists()
        )
        self.assertEqual(catalog.catalog_version, "2026.11.01.1")
        # У каждого сервиса свой значок, а не запасной глобус.
        self.assertEqual(
            [name for name, (icon, _color) in catalog.service_icons.items() if icon == "fa5s.globe"],
            [],
        )
        # Фирменные логотипы ("simple:<имя>:<буквы>") лежат в бандле; иначе вместо
        # логотипа нарисуется квадрат с буквами. После правки значков в каталоге:
        # PYTHONPATH=src python tools/generate_profile_icon_bundle.py
        from profile.ui.simple_icons_bundle import SIMPLE_ICON_SVGS

        simple_slugs = {
            icon.removeprefix("simple:").partition(":")[0]
            for icon, _color in catalog.service_icons.values()
            if icon.startswith("simple:")
        }
        self.assertIn("discord", simple_slugs)
        self.assertEqual(sorted(simple_slugs - set(SIMPLE_ICON_SVGS)), [])
        # Свои SVG ("own:<имя>:<буквы>") — для логотипов, которых нет в Simple Icons.
        from profile.ui.own_icons import OWN_ICON_SVGS

        own_slugs = {
            icon.removeprefix("own:").partition(":")[0]
            for icon, _color in catalog.service_icons.values()
            if icon.startswith("own:")
        }
        self.assertIn("openai", own_slugs)
        self.assertEqual(sorted(own_slugs - set(OWN_ICON_SVGS)), [])
        self.assertEqual(catalog.service_icons["ChatGPT & Sora (OpenAI)"], ("own:openai:AI", "#10a37f"))
        self.assertEqual(catalog.service_icons["Grok"], ("own:grok:GR", None))
        self.assertEqual(len(catalog.content_sha256), 64)
        self.assertEqual(len(catalog.service_order), 106)
        self.assertEqual(len(catalog.dns_profiles), 7)
        for removed in ("xbox_dns_old", "malw_dns", "malw_dns_v2"):
            self.assertNotIn(removed, catalog.dns_profiles)
        self.assertNotIn("fin_dns", catalog.dns_profiles)
        self.assertNotIn("play2go_cloud_dns", catalog.dns_profiles)
        self.assertEqual(catalog.service_id_by_name["Discord"], "hosts.discord")
        self.assertEqual(
            catalog.service_id_by_name["ChatGPT & Sora (OpenAI)"],
            "dns.chatgpt_and_sora_openai",
        )

        connection = sqlite3.connect(PRIVATE_DATABASE)
        try:
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("PRAGMA application_id").fetchone()[0], CATALOG_APPLICATION_ID)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], CATALOG_SCHEMA_VERSION)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM domains").fetchone()[0], 954)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM dns_answers").fetchone()[0], 6090)  # +58: Zapret DNS у Gemini AI и Fitbit (2026.11.01.1)
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM dns_profiles WHERE profile_id = 'fin_dns'"
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dns_answers WHERE ip_address = '31.77.140.129'"
                ).fetchone()[0],
                0,
            )
            # Посредники, которые перестали отвечать; вместе с последними тремя из
            # каталога ушли профили xbox_dns_old, malw_dns и malw_dns_v2 (2026-10-08).
            for dead_relay in (
                "95.182.120.241",
                "185.246.223.127",
                "144.31.14.104",
                "45.155.204.190",
                "62.133.62.97",
                "45.95.233.23",
                "217.60.245.219",
                # Посредники XBOX DNS: владелец погасил сервисы 2026-10-08.
                "188.68.214.130",
                "188.68.214.131",
                "188.68.214.132",
                "188.68.214.143",
                "188.68.214.144",
                "188.68.214.145",
                "87.228.47.198",
                "87.228.47.199",
            ):
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM dns_answers WHERE ip_address = ?",
                        (dead_relay,),
                    ).fetchone()[0],
                    0,
                    dead_relay,
                )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM hosts_entries").fetchone()[0], 549)
            # У XBOX DNS остался один живой посредник — вход в Xbox Live: профиль
            # предлагается только сервису Microsoft.
            self.assertEqual(
                connection.execute(
                    "SELECT DISTINCT d.service_id FROM dns_answers a"
                    " JOIN domains d USING(domain_id) WHERE a.profile_id = 'xbox_dns'"
                ).fetchall(),
                [("dns.microsoft_copilot_designer_xbox",)],
            )
        finally:
            connection.close()

    def test_every_offered_profile_leads_through_a_relay(self) -> None:
        """Профиль из одних обычных адресов ничего не открывает — таких в каталоге нет.

        У 33 сервисов Comss DNS отдавал обычные адреса (проверено с Windows и из
        домашних сетей России 2026-10-08): эти профили убраны. Сервисы, у которых
        после этого не осталось рабочего профиля, выключены, а не удалены.
        """
        relays = {
            "comss_dns": ("91.132.224.167", "103.137.248.145", "80.74.30.162"),
            "geohide": ("159.194.200.33", "193.233.112.67", "193.233.112.68", "193.233.112.88"),
            "astracat": ("217.60.179.6",),
        }
        connection = sqlite3.connect(PRIVATE_DATABASE)
        try:
            for profile_id in ("geohide", "astracat"):
                marks = ",".join("?" * len(relays[profile_id]))
                self.assertEqual(
                    connection.execute(
                        "SELECT d.service_id FROM dns_answers a JOIN domains d USING(domain_id)"
                        f" WHERE a.profile_id = ? GROUP BY d.service_id HAVING SUM(a.ip_address IN ({marks})) = 0",
                        (profile_id, *relays[profile_id]),
                    ).fetchall(),
                    [],
                    profile_id,
                )
            for service_id in ("dns.spotify", "dns.deezer", "dns.badoo", "dns.amd", "dns.web_archive"):
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM dns_answers a JOIN domains d USING(domain_id)"
                        " WHERE d.service_id = ? AND a.profile_id = 'comss_dns'",
                        (service_id,),
                    ).fetchone(),
                    service_id,
                )
            disabled = [row[0] for row in connection.execute("SELECT name FROM services WHERE enabled = 0 ORDER BY name")]
            self.assertEqual(disabled, ["Dell", "FMHY", "Patreon", "TikTok", "Tuta"])
            # У включённого сервиса с DNS-профилями есть хотя бы один профиль.
            self.assertEqual(
                connection.execute(
                    "SELECT s.name FROM services s WHERE s.kind = 'dns' AND s.enabled = 1 AND NOT EXISTS"
                    " (SELECT 1 FROM dns_answers a JOIN domains d USING(domain_id) WHERE d.service_id = s.service_id)"
                ).fetchall(),
                [],
            )
        finally:
            connection.close()
        index = self.proxy_domains.get_services_profile_index()
        self.assertNotIn("Tuta", index["services"])
        for added in ("Cursor", "Groq", "MongoDB", "Brave", "Arduino", "Crunchyroll", "LEGO", "IKEA", "Chess"):
            self.assertIn(added, index["services"], added)
            self.assertIn("geohide", self.proxy_domains.get_service_available_dns_profiles(added), added)

    def test_zapret_dns_profile_leads_through_the_project_server(self) -> None:
        """Свой сервер проекта: профиль стоит первым и ведёт главные имена через метку адреса сервера.

        Каталог не хранит адрес Zapret DNS: в строках профиля стоит метка, а программа
        подставляет нынешний адрес (config.zapret_dns, dns.zapret_dns_server).
        """
        marker = zapret_dns.CATALOG_ADDRESS_MARKER
        connection = sqlite3.connect(PRIVATE_DATABASE)
        try:
            first = dict(
                connection.execute(
                    "SELECT d.hostname, a.ip_address FROM dns_answers a JOIN domains d USING(domain_id)"
                    " WHERE a.profile_id = 'zapret_dns' AND a.priority = 0"
                ).fetchall()
            )
            services = connection.execute(
                "SELECT COUNT(DISTINCT d.service_id) FROM dns_answers a JOIN domains d USING(domain_id)"
                " WHERE a.profile_id = 'zapret_dns'"
            ).fetchone()[0]
            order = [row[0] for row in connection.execute("SELECT profile_id FROM dns_profiles ORDER BY sort_order")]
        finally:
            connection.close()
        self.assertEqual(order[0], "zapret_dns")
        self.assertGreaterEqual(services, 80)
        for hostname in ("chatgpt.com", "claude.ai", "open.spotify.com", "www.notion.so"):
            self.assertEqual(first[hostname], marker, hostname)
        # Имена, которые сервер ведёт сам, не должны уходить на чужие посредники: адреса Comss,
        # AstraCat и GeoHide из России закрыты, и из-за них профиль не записывался (21.1.7.117).
        foreign = {"103.137.248.145", "89.150.59.128", "217.60.179.6", "35.228.34.253", "159.194.200.33"}
        self.assertEqual({host for host, ip in first.items() if ip in foreign}, set())
        # Comss DNS сменил посредников (сверено с его сервером 2026-10-09): старых адресов в каталоге нет.
        # 103.137.248.145 вернулся в его выдачу 2026-10-21 и снова открывает сайты, поэтому его здесь нет.
        connection = sqlite3.connect(PRIVATE_DATABASE)
        try:
            stale = connection.execute(
                "SELECT COUNT(*) FROM dns_answers WHERE ip_address IN ('89.150.59.128')"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(stale, 0)
        for hostname in ("dam.strava.com", "api.claude.ai", "login.x.ai"):
            self.assertEqual(first[hostname], marker, hostname)
        # Раздача Spotify идёт напрямую: у неё нет гео-ограничения.
        self.assertNotEqual(first["image-cdn-fa.spotifycdn.com"], marker)
        # Ни одного адреса своего сервера в каталоге: ни нынешнего, ни прежних.
        connection = sqlite3.connect(PRIVATE_DATABASE)
        try:
            literal = connection.execute(
                "SELECT COUNT(*) FROM dns_answers WHERE ip_address IN (%s)"
                % ",".join("?" * len(zapret_dns.IPV4 + zapret_dns.FORMER_IPV4)),
                zapret_dns.IPV4 + zapret_dns.FORMER_IPV4,
            ).fetchone()[0]
            elsewhere = connection.execute(
                "SELECT COUNT(*) FROM dns_answers WHERE ip_address = ? AND profile_id != ?",
                (marker, zapret_dns.CATALOG_PROFILE_ID),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual((literal, elsewhere), (0, 0))
        for service in ("ChatGPT & Sora (OpenAI)", "Claude", "Spotify", "Twitch", "Web Archive", "Weather.com"):
            self.assertEqual(self.proxy_domains.get_service_available_dns_profiles(service)[0], "zapret_dns", service)
        # Canva и Intel через сервер не открываются (проверено 2026-10-09): профиля у них нет.
        for service in ("Canva", "Intel"):
            self.assertNotIn("zapret_dns", self.proxy_domains.get_service_available_dns_profiles(service), service)

    def test_google_services_skip_relays_that_google_counts_as_russia(self) -> None:
        """Gemini для вошедших в аккаунт закрыт, если Google видит адрес посредника российским.

        Замер 2026-10-15 (YouTube через посредника отвечает "GL"): прежний сервер 83.217.211.149
        и Comss 157.22.177.90 — RU, GeoHide — US, Comss 132.243.112.221 и 157.22.178.170 — NL,
        AstraCat 217.60.179.6 — DE. Сервер переехал в США (2026-11-01, "GL":"US", проба
        Gemini «open»), и профиль Zapret DNS вернулся к Gemini AI и Fitbit: все их имена из
        списка сервера идут через него (проверено TLS с проверкой сертификата), o.pki.goog —
        напрямую.
        """
        self.assertEqual(
            self.proxy_domains.get_service_available_dns_profiles("Gemini AI"), ["zapret_dns", "comss_dns", "geohide"]
        )
        self.assertEqual(
            self.proxy_domains.get_service_available_dns_profiles("Fitbit"), ["zapret_dns", "astracat", "geohide"]
        )
        connection = sqlite3.connect(PRIVATE_DATABASE)
        try:
            russian = connection.execute(
                "SELECT COUNT(*) FROM dns_answers a JOIN domains d USING(domain_id)"
                " WHERE d.service_id IN ('dns.gemini_ai', 'dns.fitbit')"
                " AND a.ip_address IN ('83.217.211.149', '157.22.177.90')"  # прежний сервер и Comss — RU у Google
            ).fetchone()[0]
            gemini = [
                row[0]
                for row in connection.execute(
                    "SELECT a.ip_address FROM dns_answers a JOIN domains d USING(domain_id)"
                    " WHERE d.hostname = 'gemini.google.com' AND a.profile_id = 'comss_dns' ORDER BY a.priority"
                )
            ]
        finally:
            connection.close()
        self.assertEqual(russian, 0)
        self.assertEqual(gemini, ["91.132.224.167", "103.137.248.145"])

    def test_geohide_opens_spotify_through_its_relays(self) -> None:
        """Имена Spotify с гео-ограничением идут через посредников GeoHide, раздача — напрямую."""
        relays = {"159.194.200.33", "193.233.112.67", "193.233.112.68", "193.233.112.88"}
        connection = sqlite3.connect(PRIVATE_DATABASE)
        try:
            first = dict(
                connection.execute(
                    "SELECT d.hostname, a.ip_address FROM dns_answers a JOIN domains d USING(domain_id)"
                    " WHERE d.service_id = 'dns.spotify' AND a.profile_id = 'geohide' AND a.priority = 0"
                ).fetchall()
            )
            total = connection.execute("SELECT COUNT(*) FROM domains WHERE service_id = 'dns.spotify'").fetchone()[0]
        finally:
            connection.close()
        # Профиль предлагается, только когда покрывает каждое имя сервиса.
        self.assertEqual(len(first), total)
        for hostname in ("open.spotify.com", "accounts.spotify.com", "api.spotify.com", "spclient.wg.spotify.com"):
            self.assertIn(first[hostname], relays, hostname)
        # Раздачу картинок и звука посредник не пропускает: у неё настоящие адреса.
        self.assertNotIn(first["image-cdn-fa.spotifycdn.com"], relays)
        self.assertIn("geohide", self.proxy_domains.get_service_available_dns_profiles("Spotify"))
        self.assertNotIn("xbox_dns", self.proxy_domains.get_service_available_dns_profiles("Spotify"))

    def test_sni_proxy_profiles_cover_every_name_of_their_services(self) -> None:
        """AstraCat и GeoHide стоят у сервисов, которые их посредник открывает из России.

        Профиль предлагается, только когда покрывает каждое имя сервиса, поэтому
        у имён, которые посредник не пропускает, записан настоящий адрес.
        """
        connection = sqlite3.connect(PRIVATE_DATABASE)
        try:
            for profile_id, proxy_ip, minimum in (("astracat", "217.60.179.6", 20), ("geohide", "159.194.200.33", 40)):
                services = [
                    row[0]
                    for row in connection.execute(
                        "SELECT DISTINCT d.service_id FROM dns_answers a"
                        " JOIN domains d USING(domain_id) WHERE a.profile_id = ?",
                        (profile_id,),
                    )
                ]
                self.assertIn("dns.chatgpt_and_sora_openai", services, profile_id)
                self.assertGreaterEqual(len(services), minimum, profile_id)
                uncovered = connection.execute(
                    "SELECT COUNT(*) FROM domains d WHERE d.service_id IN"
                    " (SELECT DISTINCT d2.service_id FROM dns_answers a2 JOIN domains d2 USING(domain_id)"
                    "  WHERE a2.profile_id = ?)"
                    " AND NOT EXISTS (SELECT 1 FROM dns_answers a"
                    " WHERE a.domain_id = d.domain_id AND a.profile_id = ?)",
                    (profile_id, profile_id),
                ).fetchone()[0]
                self.assertEqual(uncovered, 0, profile_id)
                # У каждого сервиса профиля хотя бы одно имя идёт через посредника:
                # профиль из одних настоящих адресов ничего бы не открывал.
                relays = ("217.60.179.6", "159.194.200.33", "193.233.112.67", "193.233.112.68", "193.233.112.88")
                without_relay = connection.execute(
                    "SELECT d.service_id FROM dns_answers a JOIN domains d USING(domain_id)"
                    " WHERE a.profile_id = ? GROUP BY d.service_id"
                    f" HAVING SUM(a.ip_address IN ({','.join('?' * len(relays))})) = 0",
                    (profile_id, *relays),
                ).fetchall()
                self.assertEqual(without_relay, [], profile_id)
                chatgpt_ip = connection.execute(
                    "SELECT a.ip_address FROM dns_answers a JOIN domains d USING(domain_id)"
                    " WHERE d.hostname = 'chatgpt.com' AND a.profile_id = ?"
                    " ORDER BY a.priority, a.ip_address",
                    (profile_id,),
                ).fetchall()
                # В hosts работает первая строка имени; у GeoHide за ней идут запасные узлы.
                self.assertEqual(chatgpt_ip[0], (proxy_ip,), profile_id)
            # Имена, которые в России режутся по имени сайта: посредник их не открывает
            # (проверено из сетей Ростелекома и МТС 2026-10-08), профилей у них нет.
            for service_id in ("dns.patreon", "dns.twitch", "dns.canva", "dns.dell"):
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM dns_answers a JOIN domains d USING(domain_id)"
                        " WHERE d.service_id = ? AND a.profile_id IN ('astracat', 'geohide')",
                        (service_id,),
                    ).fetchone()[0],
                    0,
                    service_id,
                )
        finally:
            connection.close()
        for service in ("Gemini AI", "Claude", "Notion", "DeepL"):
            self.assertIn("geohide", self.proxy_domains.get_service_available_dns_profiles(service), service)
        self.assertIn("astracat", self.proxy_domains.get_service_available_dns_profiles("Spotify"))

    def test_dns_ai_profile_covers_only_services_its_node_opens(self) -> None:
        """DNS-AI ведёт через свой узел 27 имён; узел для Google из России не открывался."""
        connection = sqlite3.connect(PRIVATE_DATABASE)
        try:
            services = {
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT d.service_id FROM dns_answers a"
                    " JOIN domains d USING(domain_id) WHERE a.profile_id = 'dns_ai'"
                )
            }
            self.assertEqual(
                services,
                {
                    "dns.chatgpt_and_sora_openai",
                    "dns.claude",
                    "dns.grok",
                    "dns.microsoft_copilot_designer_xbox",
                    "dns.manus",
                },
            )
            first = dict(
                connection.execute(
                    "SELECT d.hostname, a.ip_address FROM dns_answers a JOIN domains d USING(domain_id)"
                    " WHERE a.profile_id = 'dns_ai' AND a.priority = 0"
                )
            )
            for host in ("chatgpt.com", "api.openai.com", "claude.ai", "grok.com", "copilot.microsoft.com", "manus.im"):
                self.assertEqual(first[host], "62.60.230.61", host)
            # Имя, которое DNS-AI через узел не ведёт, держит настоящий адрес сайта.
            self.assertNotEqual(first["console.anthropic.com"], "62.60.230.61")
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dns_answers WHERE ip_address = '191.44.41.215'"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

        self.proxy_domains.invalidate_hosts_catalog_cache()
        self.assertIn("dns_ai", self.proxy_domains.get_service_available_dns_profiles("Claude"))
        self.assertNotIn("dns_ai", self.proxy_domains.get_service_available_dns_profiles("Gemini AI"))

    def test_youtube_service_covers_the_names_dns_denies_with_ipv4_and_ipv6(self) -> None:
        # С 2026-10 на обычный запрос DNS про youtube.com, www.youtube.com и m.youtube.com по дороге
        # приходит подставной «такого домена нет». Записи в hosts снимают зависимость от DNS целиком.
        import ipaddress

        connection = sqlite3.connect(PRIVATE_DATABASE)
        try:
            rows = connection.execute(
                "SELECT hostname, ip_address FROM hosts_entries WHERE service_id = 'hosts.youtube' ORDER BY priority"
            ).fetchall()
        finally:
            connection.close()

        by_host: dict[str, list] = {}
        for hostname, ip in rows:
            by_host.setdefault(hostname, []).append(ipaddress.ip_address(ip))
        self.assertEqual(list(by_host), ["www.youtube.com", "youtube.com", "m.youtube.com"])
        for hostname, addresses in by_host.items():
            with self.subTest(hostname=hostname):
                # Адреса IPv4 идут первыми: у кого IPv6 нет, тому программа пишет только их.
                self.assertEqual([address.version for address in addresses], [4] * 6 + [6] * 6)
                self.assertEqual(addresses, by_host["www.youtube.com"])
        # Этот адрес 2026-10-13 не отвечал ни со стенда, ни из шести российских сетей.
        self.assertNotIn("216.58.206.78", {ip for _hostname, ip in rows})

    def test_youtube_tile_no_longer_warns_that_it_may_not_work(self) -> None:
        # Оговорка «иногда может не работать» стояла в самом названии сервиса. С умным движком
        # (hosts.address_engine) программа сама проверяет адреса и ставит рабочий первым.
        from hosts.catalog_repository import load_catalog

        catalog = load_catalog(PRIVATE_DATABASE)
        self.assertEqual(catalog.service_name_by_id["hosts.youtube"], "YouTube")
        self.assertNotIn("не работать", " ".join(catalog.service_order))

    def test_every_service_with_a_passport_exists_and_writes_real_site_addresses(self) -> None:
        # Паспорт разрешает брать свежие адреса у честного DNS. Это верно только там, где в hosts
        # стоит настоящий адрес сайта; у сервисов через посредника честный DNS всё сломал бы.
        from hosts.address_engine import PASSPORTS
        from hosts.catalog_repository import HOSTS_PROFILE_ID, load_catalog

        catalog = load_catalog(PRIVATE_DATABASE)
        relays = {"hosts.supercell", "hosts.ntc_party_ipv4", "hosts.flowseal_discord_voice", "hosts.discord", "hosts.rutor", "hosts.x_twitter"}
        self.assertFalse(relays & set(PASSPORTS))
        column = catalog.dns_profiles.index(HOSTS_PROFILE_ID)
        for service_id in PASSPORTS:
            with self.subTest(service_id=service_id):
                name = catalog.service_name_by_id[service_id]
                self.assertTrue([host for host, values in catalog.service_entries[name] if values[column]])

    def test_checked_addresses_replace_catalog_rows_everywhere_at_once(self) -> None:
        """Набор проверенных адресов видят и страница, и запись hosts: источник строк один."""
        from hosts.catalog_repository import HOSTS_PROFILE_ID, load_catalog

        shipped = load_catalog(PRIVATE_DATABASE)
        stored: dict = {}
        with (
            patch.object(self.proxy_domains.settings_store, "get_hosts_engine", lambda: dict(stored)),
            patch.object(self.proxy_domains.settings_store, "set_hosts_engine", lambda value: stored.update(value) or True),
        ):
            self.proxy_domains.invalidate_hosts_catalog_cache()
            before = self.proxy_domains.get_service_domain_ip_rows("YouTube", HOSTS_PROFILE_ID)
            signature = self.proxy_domains.get_hosts_catalog_signature()
            self.assertEqual(len(before), 36)

            rows = [[host, "64.233.165.198"] for host in ("www.youtube.com", "youtube.com", "m.youtube.com")]
            overlay = {"catalog": shipped.content_sha256, "checked_at": "2026-10-13T14:20:05", "services": {"hosts.youtube": {HOSTS_PROFILE_ID: rows}}}
            self.assertTrue(self.proxy_domains.set_engine_overlay(overlay))

            self.assertEqual(
                self.proxy_domains.get_service_domain_ip_rows("YouTube", HOSTS_PROFILE_ID),
                [(host, address) for host, address in rows],
            )
            # Снимок страницы пересобирается по этой подписи.
            self.assertNotEqual(self.proxy_domains.get_hosts_catalog_signature(), signature)
            # Соседний сервис не тронут, а поставленный каталог остаётся как был — из него берётся запас.
            self.assertEqual(
                self.proxy_domains.get_service_domain_ip_rows("GitHub", HOSTS_PROFILE_ID),
                [(h, v[shipped.dns_profiles.index(HOSTS_PROFILE_ID)]) for h, v in shipped.service_entries["GitHub"] if v[shipped.dns_profiles.index(HOSTS_PROFILE_ID)]],
            )
            self.assertEqual(len(self.proxy_domains.load_shipped_catalog().service_entries["YouTube"]), 36)

            # Набор от другой версии каталога не действует: вышел новый каталог — проверка начнётся с него.
            stored["catalog"] = "старый каталог"
            self.proxy_domains.invalidate_hosts_catalog_cache()
            self.assertEqual(self.proxy_domains.get_service_domain_ip_rows("YouTube", HOSTS_PROFILE_ID), before)

    def test_direct_services_hold_only_addresses_that_answered_on_2026_10_14(self) -> None:
        """Сверка прямых сервисов 2026-10-14 (стенд win10 и российские домашние сети).

        Supercell: посредники 144.31.14.104, 185.246.223.127 и 185.36.125.240–.245 не принимали
        соединение, 45.95.233.23 отвечал чужим сертификатом. Игровые серверы и магазин (он из России
        отдаёт отказ) идут через живых посредников; остальные имена открываются из России напрямую,
        в hosts они вели на мёртвый адрес и только мешали — убраны.
        Rutor: адреса Cloudflare отдавали «error code: 1034» вместо сайта — стоят настоящие адреса.
        """
        connection = sqlite3.connect(PRIVATE_DATABASE)
        try:
            rows = connection.execute("SELECT service_id, hostname, ip_address FROM hosts_entries").fetchall()
        finally:
            connection.close()
        addresses = {ip for _service, _host, ip in rows}
        for dead in (
            "144.31.14.104", "185.246.223.127", "45.95.233.23",
            "185.36.125.240", "185.36.125.241", "185.36.125.244", "185.36.125.245",
            "173.245.59.219", "172.64.33.155",  # Cloudflare: «error code: 1034»
            "185.199.110.133",  # отдавал camo.githubassets.com сертификат *.github.io
        ):
            self.assertNotIn(dead, addresses, dead)
        hosts = {host for _service, host, _ip in rows}
        # Имён больше нет в DNS (1.1.1.1, 8.8.8.8, 9.9.9.9 и 77.88.8.8 ответили «домена нет»).
        self.assertNotIn("static.instagram.com", hosts)
        self.assertNotIn("d.rutor.is", hosts)

        def of(service_id: str) -> dict[str, list[str]]:
            found: dict[str, list[str]] = {}
            for service, host, ip in rows:
                if service == service_id:
                    found.setdefault(host, []).append(ip)
            return found

        supercell = of("hosts.supercell")
        self.assertEqual(
            set(supercell),
            {
                "game.brawlstarsgame.com", "game.clashroyaleapp.com", "game.mocogame.com",
                "game.squadbustersgame.com", "gamea.clashofclans.com", "store.supercell.com",
            },
        )
        # У магазина два посредника разных владельцев: умный движок ставит первым ответивший.
        self.assertEqual(supercell["store.supercell.com"], ["143.20.64.192", "217.60.179.6"])
        self.assertEqual(
            of("hosts.rutor"),
            {
                "rutor.info": ["193.46.255.29"],
                "rutor.is": ["193.46.255.26"],
                "d.rutor.info": ["193.46.255.29"],
                "tracker.openbittorrent.com": ["52.223.13.41"],
            },
        )
        github = of("hosts.github")
        self.assertEqual(github["github.com"], ["140.82.121.3", "140.82.121.4"])
        self.assertEqual(github["camo.githubassets.com"], ["185.199.108.154", "185.199.109.154"])
        self.assertEqual(of("hosts.render"), {"render.com": ["216.24.57.1"]})

    def test_direct_services_carry_ipv6_wherever_the_site_itself_has_it(self) -> None:
        # Решение владельца 2026-10-13: один тумблер с IPv4 и IPv6 сразу, а не отдельные «…IPv6».
        # IPv6 есть не у всех: у Discord, x.com, github.com, Rutor и Render его нет у самих сайтов,
        # а Supercell, ntc.party и голос Discord идут через посредника по IPv4.
        import ipaddress

        expected = {
            "hosts.instagram": {
                "b.i.instagram.com", "help.instagram.com", "instagram.com", "www.instagram.com",
                "scontent.cdninstagram.com", "scontent-hel3-1.cdninstagram.com", "z-p42-chat-e2ee-ig.facebook.com",
            },
            "hosts.github": {"github.githubassets.com"},
            "hosts.whatsapp_ipv6": {"www.whatsapp.com", "web.whatsapp.com", "static.whatsapp.net", "scontent.whatsapp.net"},
            "hosts.youtube": {"www.youtube.com", "youtube.com", "m.youtube.com"},
        }
        connection = sqlite3.connect(PRIVATE_DATABASE)
        try:
            for service_id, hostnames in expected.items():
                rows = connection.execute(
                    "SELECT hostname, ip_address FROM hosts_entries WHERE service_id = ?", (service_id,)
                ).fetchall()
                with_ipv6 = {hostname for hostname, ip in rows if ipaddress.ip_address(ip).version == 6}
                with_ipv4 = {hostname for hostname, ip in rows if ipaddress.ip_address(ip).version == 4}
                with self.subTest(service_id=service_id):
                    self.assertEqual(with_ipv6, hostnames)
                    # У каждого имени с IPv6 есть и IPv4: без IPv6 на компьютере тумблер продолжает работать.
                    self.assertLessEqual(with_ipv6, with_ipv4)
        finally:
            connection.close()

    def test_githubusercontent_service_has_ipv6_and_ipv4_for_every_host(self) -> None:
        connection = sqlite3.connect(PRIVATE_DATABASE)
        try:
            service = connection.execute(
                "SELECT category, kind, sort_order FROM services"
                " WHERE service_id = 'hosts.githubusercontent_ipv6'"
            ).fetchone()
            self.assertIsNotNone(service)
            category, kind, sort_order = service
            self.assertEqual((category, kind), ("direct", "hosts"))
            github_order = connection.execute(
                "SELECT sort_order FROM services WHERE service_id = 'hosts.github'"
            ).fetchone()[0]
            self.assertEqual(sort_order, github_order - 1)
            rows = connection.execute(
                "SELECT hostname, ip_address FROM hosts_entries"
                " WHERE service_id = 'hosts.githubusercontent_ipv6'"
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(len(rows), 90)
        hostnames = {hostname for hostname, _ in rows}
        self.assertEqual(len(hostnames), 18)
        self.assertIn("raw.githubusercontent.com", hostnames)
        self.assertIn("objects.githubusercontent.com", hostnames)
        # Без IPv4 сервис считается «только IPv6» и выключается у тех, у кого IPv6 нет.
        ipv4_hostnames = {hostname for hostname, ip in rows if ip == "146.75.22.132"}
        self.assertEqual(ipv4_hostnames, hostnames)
        for _, ip in rows:
            self.assertTrue(ip == "146.75.22.132" or ip.startswith("2606:50c0:800"), ip)
        from hosts.catalog_repository import load_catalog

        # Название больше не обещает «только IPv6», а service_id прежний: по нему
        # в настройках хранится выбор пользователя.
        self.assertEqual(
            load_catalog(PRIVATE_DATABASE).service_id_by_name["GitHub загрузки/картинки"],
            "hosts.githubusercontent_ipv6",
        )

    def test_runtime_reads_dns_and_direct_rows_from_sqlite(self) -> None:
        self.assertEqual(
            len(self.proxy_domains.get_service_domain_ip_rows("ChatGPT & Sora (OpenAI)", "comss_dns")),
            147,
        )
        self.assertEqual(
            self.proxy_domains.get_service_domain_ip_rows("Discord", "hosts")[:2],
            [
                ("discord.com", "23.227.38.74"),
                ("gateway.discord.gg", "23.227.38.74"),
            ],
        )
        self.assertTrue(self.proxy_domains.service_has_proxy_profiles("ChatGPT & Sora (OpenAI)"))
        self.assertFalse(self.proxy_domains.service_has_proxy_profiles("Discord"))

    def test_catalog_open_is_read_only_and_does_not_change_file(self) -> None:
        from hosts.catalog_repository import file_content_signature, load_catalog

        before = file_content_signature(PRIVATE_DATABASE)
        load_catalog(PRIVATE_DATABASE)
        after = file_content_signature(PRIVATE_DATABASE)
        self.assertEqual(after, before)

    def test_windows_unc_catalog_uses_plain_path_with_query_only(self) -> None:
        from hosts.catalog_repository import _connect_read_only

        unc_path = Path("//10.20.0.1/zapretgui/candidate/hosts_catalog.sqlite3")
        connection = MagicMock(spec=sqlite3.Connection)
        with (
            patch("hosts.catalog_repository.os.name", "nt"),
            patch.object(Path, "resolve", return_value=unc_path),
            patch("hosts.catalog_repository.sqlite3.connect", return_value=connection) as connect,
        ):
            actual = _connect_read_only(Path("ignored.sqlite3"))

        self.assertIs(actual, connection)
        connect.assert_called_once_with(unc_path, timeout=5.0)
        connection.execute.assert_any_call("PRAGMA query_only = ON")

    def test_missing_database_is_not_created(self) -> None:
        from hosts.catalog_repository import HostsCatalogError, load_catalog

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "hosts_catalog.sqlite3"
            with self.assertRaises(HostsCatalogError):
                load_catalog(missing)
            self.assertFalse(missing.exists())

    def test_tampered_logical_content_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = self._copy_database(Path(tmp))
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "UPDATE services SET name = 'Tampered' WHERE service_id = 'hosts.discord'"
                )
                connection.commit()
            finally:
                connection.close()

            with patch.object(self.proxy_domains, "_get_hosts_catalog_path", return_value=database):
                self.proxy_domains.invalidate_hosts_catalog_cache()
                self.assertEqual(self.proxy_domains.get_all_services(), [])

    def test_unsupported_schema_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = self._copy_database(Path(tmp))
            connection = sqlite3.connect(database)
            try:
                connection.execute("PRAGMA user_version = 2")
                connection.commit()
            finally:
                connection.close()
            with patch.object(self.proxy_domains, "_get_hosts_catalog_path", return_value=database):
                self.proxy_domains.invalidate_hosts_catalog_cache()
                self.assertEqual(self.proxy_domains.get_dns_profiles(), [])

    def test_content_signature_ignores_timestamp_only_touch(self) -> None:
        from hosts.catalog_repository import file_content_signature

        with tempfile.TemporaryDirectory() as tmp:
            database = self._copy_database(Path(tmp))
            before = file_content_signature(database)
            stat = database.stat()
            os.utime(database, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
            self.assertEqual(file_content_signature(database), before)

    def test_content_signature_tracks_same_size_change(self) -> None:
        from hosts.catalog_repository import file_content_signature

        with tempfile.TemporaryDirectory() as tmp:
            database = self._copy_database(Path(tmp))
            before = file_content_signature(database)
            payload = bytearray(database.read_bytes())
            payload[-1] ^= 1
            database.write_bytes(payload)
            after = file_content_signature(database)
            self.assertEqual(after[1], before[1])
            self.assertNotEqual(after[0], before[0])

    def test_profile_with_missing_domain_answer_is_not_offered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = self._copy_database(Path(tmp))
            connection = sqlite3.connect(database)
            try:
                domain_id = connection.execute(
                    """
                    SELECT d.domain_id FROM domains AS d
                    WHERE d.service_id = 'dns.chatgpt_and_sora_openai'
                    ORDER BY d.sort_order LIMIT 1
                    """
                ).fetchone()[0]
                connection.execute(
                    "DELETE FROM dns_answers WHERE domain_id = ? AND profile_id = 'comss_dns'",
                    (domain_id,),
                )
                connection.commit()
            finally:
                connection.close()
            self._rehash_database(database)

            with patch.object(self.proxy_domains, "_get_hosts_catalog_path", return_value=database):
                self.proxy_domains.invalidate_hosts_catalog_cache()
                available = self.proxy_domains.get_service_available_dns_profiles(
                    "ChatGPT & Sora (OpenAI)"
                )
            self.assertNotIn("comss_dns", available)
            self.assertIn("geohide", available)

    def test_multiple_answers_preserve_priority_and_top_ip_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = self._copy_database(Path(tmp))
            connection = sqlite3.connect(database)
            try:
                domain_id = connection.execute(
                    """
                    SELECT domain_id FROM domains
                    WHERE service_id = 'dns.chatgpt_and_sora_openai'
                    ORDER BY sort_order LIMIT 1
                    """
                ).fetchone()[0]
                connection.execute(
                    "DELETE FROM dns_answers WHERE domain_id = ? AND profile_id = 'geohide'",
                    (domain_id,),
                )
                connection.executemany(
                    "INSERT INTO dns_answers VALUES (?, 'geohide', ?, ?)",
                    [(domain_id, "87.228.47.205", 1), (domain_id, "87.228.47.204", 0)],
                )
                connection.commit()
            finally:
                connection.close()
            self._rehash_database(database)

            with patch.object(self.proxy_domains, "_get_hosts_catalog_path", return_value=database):
                self.proxy_domains.invalidate_hosts_catalog_cache()
                rows = self.proxy_domains.get_service_domain_ip_rows(
                    "ChatGPT & Sora (OpenAI)", "geohide"
                )
                domain_map = self.proxy_domains.get_service_domain_ip_map(
                    "ChatGPT & Sora (OpenAI)", "geohide"
                )
            self.assertEqual(rows[:2], [("ab.chatgpt.com", "87.228.47.204"), ("ab.chatgpt.com", "87.228.47.205")])
            self.assertEqual(domain_map["ab.chatgpt.com"], "87.228.47.204")

    def test_profile_index_uses_database_category_icon_and_order(self) -> None:
        index = self.proxy_domains.get_services_profile_index()
        self.assertEqual(index["services"][0], "ChatGPT & Sora (OpenAI)")
        self.assertEqual(index["category_by_service"]["ChatGPT & Sora (OpenAI)"], "ai")
        self.assertEqual(index["category_by_service"]["Discord"], "direct")
        self.assertEqual(index["icon_by_service"]["Discord"], ("simple:discord:DI", "#5865f2"))

    def test_profile_index_is_cached_until_explicit_invalidation(self) -> None:
        with patch.object(
            self.proxy_domains,
            "_build_services_profile_index",
            wraps=self.proxy_domains._build_services_profile_index,
        ) as build:
            first = self.proxy_domains.get_services_profile_index()
            second = self.proxy_domains.get_services_profile_index()
            self.assertIs(first, second)
            self.assertEqual(build.call_count, 1)
            self.proxy_domains.invalidate_hosts_catalog_cache()
            self.proxy_domains.get_services_profile_index()
            self.assertEqual(build.call_count, 2)

    def test_user_selection_is_stored_by_stable_id_and_orphans_are_retained(self) -> None:
        stored = {
            "dns.chatgpt_and_sora_openai": "comss_dns",
            "removed.future_service": "future_dns",
        }
        written: list[dict[str, str]] = []
        with (
            patch.object(self.proxy_domains.settings_store, "get_hosts_selection", return_value=stored),
            patch.object(
                self.proxy_domains.settings_store,
                "set_hosts_selection",
                side_effect=lambda value: written.append(dict(value)) or True,
            ),
        ):
            self.assertEqual(
                self.proxy_domains.load_user_hosts_selection(),
                {"ChatGPT & Sora (OpenAI)": "comss_dns"},
            )
            self.assertTrue(
                self.proxy_domains.save_user_hosts_selection(
                    {"ChatGPT & Sora (OpenAI)": "geohide"}
                )
            )

        self.assertEqual(
            written,
            [{
                "removed.future_service": "future_dns",
                "dns.chatgpt_and_sora_openai": "geohide",
            }],
        )

    def test_selection_of_a_removed_profile_moves_to_the_first_available(self) -> None:
        """Профиль убрали у сервиса — сервис не выключается молча, а берёт первый доступный.

        Случай 2026-10-15: у Gemini AI убирали Zapret DNS (Google считал прежний сервер
        российским); у кого он был выбран, Gemini должен был остаться в hosts. Здесь —
        Comss DNS у Fitbit: профиль в каталоге есть, а у этого сервиса его нет.
        """
        stored = {
            "dns.chatgpt_and_sora_openai": "malw_dns",  # профиль убран из каталога целиком
            "dns.fitbit": "comss_dns",  # профиль есть, но у этого сервиса его нет
            "dns.claude": "comss_dns",
        }
        available = self.proxy_domains.get_service_available_dns_profiles
        with patch.object(self.proxy_domains.settings_store, "get_hosts_selection", return_value=stored):
            self.assertEqual(
                self.proxy_domains.load_user_hosts_selection(),
                {
                    "ChatGPT & Sora (OpenAI)": available("ChatGPT & Sora (OpenAI)")[0],
                    "Fitbit": available("Fitbit")[0],
                    "Claude": "comss_dns",
                },
            )

    def test_legacy_display_name_selection_is_converted_on_next_save(self) -> None:
        written: list[dict[str, str]] = []
        with (
            patch.object(
                self.proxy_domains.settings_store,
                "get_hosts_selection",
                return_value={"Discord": "hosts"},
            ),
            patch.object(
                self.proxy_domains.settings_store,
                "set_hosts_selection",
                side_effect=lambda value: written.append(dict(value)) or True,
            ),
        ):
            self.assertEqual(
                self.proxy_domains.load_user_hosts_selection(),
                {"Discord": "hosts"},
            )
            self.assertTrue(self.proxy_domains.save_user_hosts_selection({"Discord": "hosts"}))
        self.assertEqual(written, [{"hosts.discord": "hosts"}])


if __name__ == "__main__":
    unittest.main()
