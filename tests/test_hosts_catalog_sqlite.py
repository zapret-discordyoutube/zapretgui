from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PUBLIC_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PUBLIC_ROOT.parent
PRIVATE_DATABASE = PROJECT_ROOT / "private_zapretgui" / "resources" / "data" / "hosts_catalog.sqlite3"
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
                    root / "private_zapretgui" / "resources" / "data" / "hosts_catalog.sqlite3",
                )

            install_root = root / "Zapret" / "Dev"
            with (
                patch.object(self.proxy_domains, "PACKAGED_RUNTIME", True),
                patch.object(self.proxy_domains, "MAIN_DIRECTORY", str(install_root)),
            ):
                self.assertEqual(
                    self.proxy_domains.get_hosts_catalog_path(),
                    install_root / "data" / "hosts_catalog.sqlite3",
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
        self.assertEqual(catalog.catalog_version, "2026.08.14.1")
        self.assertEqual(len(catalog.content_sha256), 64)
        self.assertEqual(len(catalog.service_order), 72)
        self.assertEqual(len(catalog.dns_profiles), 8)
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
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM domains").fetchone()[0], 818)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM dns_answers").fetchone()[0], 5723)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM hosts_entries").fetchone()[0], 427)
        finally:
            connection.close()

    def test_runtime_reads_dns_and_direct_rows_from_sqlite(self) -> None:
        self.assertEqual(
            len(self.proxy_domains.get_service_domain_ip_rows("ChatGPT & Sora (OpenAI)", "xbox_dns")),
            53,
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
                    "DELETE FROM dns_answers WHERE domain_id = ? AND profile_id = 'fin_dns'",
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
            self.assertNotIn("fin_dns", available)
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
                    "INSERT INTO dns_answers VALUES (?, 'xbox_dns', '87.228.47.205', 1)",
                    (domain_id,),
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
        self.assertEqual(index["icon_by_service"]["Discord"], ("fa5b.discord", "#5865f2"))

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
            "removed.future_service": "fin_dns",
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
                "removed.future_service": "fin_dns",
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
