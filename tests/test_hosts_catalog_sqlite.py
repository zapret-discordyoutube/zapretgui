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
        self.assertEqual(catalog.catalog_version, "2026.10.08.2")
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
        self.assertEqual(len(catalog.service_order), 73)
        self.assertEqual(len(catalog.dns_profiles), 8)
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
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM domains").fetchone()[0], 850)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM dns_answers").fetchone()[0], 6815)
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
            for dead_relay in ("95.182.120.241", "185.246.223.127", "144.31.14.104"):
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM dns_answers WHERE ip_address = ?",
                        (dead_relay,),
                    ).fetchone()[0],
                    0,
                    dead_relay,
                )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM hosts_entries").fetchone()[0], 517)
        finally:
            connection.close()

    def test_sni_proxy_profiles_cover_only_chatgpt(self) -> None:
        connection = sqlite3.connect(PRIVATE_DATABASE)
        try:
            for profile_id, proxy_ip in (("astracat", "217.60.179.6"), ("geohide", "217.60.245.219")):
                services = connection.execute(
                    "SELECT DISTINCT d.service_id FROM dns_answers a"
                    " JOIN domains d USING(domain_id) WHERE a.profile_id = ?",
                    (profile_id,),
                ).fetchall()
                self.assertEqual(services, [("dns.chatgpt_and_sora_openai",)], profile_id)
                uncovered = connection.execute(
                    "SELECT COUNT(*) FROM domains d WHERE d.service_id = 'dns.chatgpt_and_sora_openai'"
                    " AND NOT EXISTS (SELECT 1 FROM dns_answers a"
                    " WHERE a.domain_id = d.domain_id AND a.profile_id = ?)",
                    (profile_id,),
                ).fetchone()[0]
                self.assertEqual(uncovered, 0, profile_id)
                chatgpt_ip = connection.execute(
                    "SELECT a.ip_address FROM dns_answers a JOIN domains d USING(domain_id)"
                    " WHERE d.hostname = 'chatgpt.com' AND a.profile_id = ?",
                    (profile_id,),
                ).fetchall()
                self.assertEqual(chatgpt_ip, [(proxy_ip,)], profile_id)
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
            len(self.proxy_domains.get_service_domain_ip_rows("ChatGPT & Sora (OpenAI)", "xbox_dns")),
            100,
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
                    "DELETE FROM dns_answers WHERE domain_id = ? AND profile_id = 'xbox_dns_old'",
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
            self.assertNotIn("xbox_dns_old", available)
            self.assertIn("xbox_dns", available)

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
                    "DELETE FROM dns_answers WHERE domain_id = ? AND profile_id = 'xbox_dns'",
                    (domain_id,),
                )
                connection.executemany(
                    "INSERT INTO dns_answers VALUES (?, 'xbox_dns', ?, ?)",
                    [(domain_id, "87.228.47.205", 1), (domain_id, "87.228.47.204", 0)],
                )
                connection.commit()
            finally:
                connection.close()
            self._rehash_database(database)

            with patch.object(self.proxy_domains, "_get_hosts_catalog_path", return_value=database):
                self.proxy_domains.invalidate_hosts_catalog_cache()
                rows = self.proxy_domains.get_service_domain_ip_rows(
                    "ChatGPT & Sora (OpenAI)", "xbox_dns"
                )
                domain_map = self.proxy_domains.get_service_domain_ip_map(
                    "ChatGPT & Sora (OpenAI)", "xbox_dns"
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
            "dns.chatgpt_and_sora_openai": "xbox_dns_old",
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
                {"ChatGPT & Sora (OpenAI)": "xbox_dns_old"},
            )
            self.assertTrue(
                self.proxy_domains.save_user_hosts_selection(
                    {"ChatGPT & Sora (OpenAI)": "xbox_dns"}
                )
            )

        self.assertEqual(
            written,
            [{
                "removed.future_service": "future_dns",
                "dns.chatgpt_and_sora_openai": "xbox_dns",
            }],
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
