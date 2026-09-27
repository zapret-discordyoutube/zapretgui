"""Читатель реестра фейков winws2 (system/fakes_catalog.sqlite3).

База — готовый ресурс private-репозитория. Проверки порчи делаются на копии
настоящей базы во временной папке; без private-репозитория они пропускаются.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from config.runtime_layout import ApplicationPaths
from fakes.catalog_repository import (
    CATALOG_APPLICATION_ID,
    compute_content_sha256,
    read_fakes_catalog,
)
from fakes.public import (
    BLOB_REFERENCE_ARG_NAMES,
    NFQWS2_BUILTIN_BLOBS,
    FakeEntry,
    FakesCatalog,
    FakesCatalogError,
    invalidate_fakes_catalog_cache,
    load_fakes_catalog,
)

PUBLIC_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DATABASE = (
    PUBLIC_ROOT.parent / "private_zapretgui" / "resources" / "system" / "fakes_catalog.sqlite3"
)


def _entry(**overrides) -> FakeEntry:
    values = dict(
        name="tls_example",
        source_kind="file",
        file_name="tls_clienthello_example.bin",
        hex_value=None,
        kind="tls",
        sni="example.com",
        description="",
        same_bytes_as=None,
    )
    values.update(overrides)
    return FakeEntry(**values)


class FakeEntryTest(unittest.TestCase):
    def test_file_entry_renders_exact_blob_line(self) -> None:
        entry = _entry()
        self.assertEqual(entry.blob_value(), "@bin/tls_clienthello_example.bin")
        self.assertEqual(entry.blob_line(), "--blob=tls_example:@bin/tls_clienthello_example.bin")

    def test_hex_entry_keeps_digit_case(self) -> None:
        entry = _entry(name="hex_0e0e0f0e", source_kind="hex", file_name=None,
                       hex_value="0E0E0F0E", kind="other", sni=None)
        self.assertEqual(entry.blob_line(), "--blob=hex_0e0e0f0e:0x0E0E0F0E")

    def test_invalid_entries_are_rejected(self) -> None:
        cases = {
            "hex with 0x prefix": dict(source_kind="hex", file_name=None, hex_value="0x00"),
            "odd hex": dict(source_kind="hex", file_name=None, hex_value="000"),
            "both values": dict(hex_value="00"),
            "file with path": dict(file_name="bin/x.bin"),
            "bad name": dict(name="tls-example"),
            "unknown kind": dict(kind="video"),
            "unknown source": dict(source_kind="lua"),
        }
        for label, overrides in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                _entry(**overrides)

    def test_catalog_snapshot_is_read_only(self) -> None:
        entry = _entry()
        catalog = FakesCatalog(entries={entry.name: entry})
        with self.assertRaises(TypeError):
            catalog.entries["other"] = entry  # type: ignore[index]
        with self.assertRaises(ValueError):
            FakesCatalog(entries={"other": entry})

    def test_engine_name_constants(self) -> None:
        self.assertEqual(
            NFQWS2_BUILTIN_BLOBS,
            {"fake_default_tls", "fake_default_http", "fake_default_quic"},
        )
        self.assertEqual(
            BLOB_REFERENCE_ARG_NAMES,
            {"blob", "fake_blob", "pattern", "seqovl_pattern", "fallback"},
        )

    def test_installed_path_is_in_system_folder(self) -> None:
        root = Path("D:/Zapret").resolve()
        self.assertEqual(
            ApplicationPaths.from_root(root).fakes_catalog_database,
            root / "system" / "fakes_catalog.sqlite3",
        )


class FakesCatalogDatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        if not PRIVATE_DATABASE.is_file():
            self.skipTest(f"Нет private-репозитория: {PRIVATE_DATABASE}")
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.addCleanup(invalidate_fakes_catalog_cache)
        self.database = Path(self._temp.name) / "fakes_catalog.sqlite3"
        shutil.copyfile(PRIVATE_DATABASE, self.database)

    def _mutate(self, *statements: str) -> None:
        connection = sqlite3.connect(self.database)
        try:
            with connection:
                for statement in statements:
                    connection.execute(statement)
        finally:
            connection.close()

    def test_reads_valid_snapshot(self) -> None:
        catalog = read_fakes_catalog(self.database)
        self.assertTrue(catalog.entries)
        self.assertTrue(catalog.catalog_version)
        self.assertEqual(len(catalog.content_sha256), 64)
        for name, entry in catalog.entries.items():
            self.assertEqual(entry.name, name)
            self.assertTrue(entry.blob_line().startswith(f"--blob={name}:"))

    def test_reader_does_not_write_the_file(self) -> None:
        before = self.database.read_bytes()
        read_fakes_catalog(self.database)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.database.parent.iterdir()), [self.database.name])

    def test_missing_file_is_an_error(self) -> None:
        with self.assertRaises(FakesCatalogError):
            load_fakes_catalog(self.database.with_name("absent.sqlite3"))

    def test_garbage_file_is_an_error(self) -> None:
        self.database.write_bytes(b"not a sqlite database")
        with self.assertRaises(FakesCatalogError):
            read_fakes_catalog(self.database)

    def test_row_change_without_new_checksum_is_rejected(self) -> None:
        self._mutate("UPDATE fakes SET description = 'подмена'")
        with self.assertRaisesRegex(FakesCatalogError, "контрольная сумма"):
            read_fakes_catalog(self.database)

    def test_wrong_application_id_is_rejected(self) -> None:
        self._mutate(f"PRAGMA application_id = {CATALOG_APPLICATION_ID + 1}")
        with self.assertRaisesRegex(FakesCatalogError, "application_id"):
            read_fakes_catalog(self.database)

    def test_wrong_schema_version_is_rejected(self) -> None:
        self._mutate("PRAGMA user_version = 2")
        with self.assertRaisesRegex(FakesCatalogError, "схема"):
            read_fakes_catalog(self.database)

    def test_checksum_covers_every_row(self) -> None:
        connection = sqlite3.connect(self.database)
        try:
            before = compute_content_sha256(connection)
            with connection:
                connection.execute("UPDATE fakes SET sort_order = sort_order + 1")
            self.assertNotEqual(compute_content_sha256(connection), before)
        finally:
            connection.close()

    def test_cache_follows_file_content(self) -> None:
        first = load_fakes_catalog(self.database)
        self.assertIs(load_fakes_catalog(self.database), first)

        connection = sqlite3.connect(self.database)
        try:
            with connection:
                connection.execute("UPDATE fakes SET description = 'новое описание'")
                connection.execute(
                    "UPDATE catalog_meta SET value = ? WHERE key = 'content_sha256'",
                    (compute_content_sha256(connection),),
                )
        finally:
            connection.close()

        second = load_fakes_catalog(self.database)
        self.assertIsNot(second, first)
        self.assertEqual(
            {entry.description for entry in second.entries.values()},
            {"новое описание"},
        )


if __name__ == "__main__":
    unittest.main()
