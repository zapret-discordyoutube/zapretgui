"""Целостность каталогов готовых стратегий (src/system/strategy_catalogs).

Парсер каталога молча отбрасывает запись, если в ней есть строки профиля
(`--filter-*`, `--hostlist=` и т.п.), а при повторе `[id]` оставляет последнюю
копию. Оба случая раньше ничего не писали в лог, и стратегия просто пропадала
из списка. Здесь проверяется, что поставляемые каталоги от этого чисты, и что
парсер предупреждает о таких записях.

Уникальность id между разными каталогами одного движка сознательно НЕ
проверяется: оценки стратегий хранятся по паре (profile, id), и переименование
осиротило бы пользовательские данные.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from profile.strategy_catalog import _parse_catalog_file

CATALOGS_ROOT = Path(__file__).resolve().parents[1] / "src" / "system" / "strategy_catalogs"
ENGINES = ("winws1", "winws2")

_HEADER_RE = re.compile(r"^\s*\[(.+)\]\s*$")


def _catalog_files(engine: str) -> list[Path]:
    return sorted((CATALOGS_ROOT / engine).glob("*.txt"))


def _raw_header_ids(path: Path) -> list[str]:
    ids: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _HEADER_RE.match(raw)
        if match:
            ids.append(match.group(1).strip())
    return ids


def _warning_messages(mock_log) -> list[str]:
    return [
        str(call.args[0])
        for call in mock_log.call_args_list
        if len(call.args) > 1 and call.args[1] == "WARNING"
    ]


class ShippedStrategyCatalogIntegrityTest(unittest.TestCase):
    def test_every_engine_has_catalog_files(self) -> None:
        for engine in ENGINES:
            with self.subTest(engine=engine):
                self.assertTrue(_catalog_files(engine), f"нет каталогов для {engine}")

    def test_every_header_becomes_loaded_entry(self) -> None:
        for engine in ENGINES:
            for path in _catalog_files(engine):
                with self.subTest(engine=engine, catalog=path.name):
                    raw_ids = _raw_header_ids(path)
                    with patch("profile.strategy_catalog.log"):
                        loaded = _parse_catalog_file(path, path.stem.lower())
                    missing = sorted(set(raw_ids) - set(loaded))
                    self.assertEqual(
                        missing,
                        [],
                        f"{engine}/{path.name}: записи не загружаются (строки профиля в стратегии?)",
                    )
                    self.assertEqual(len(raw_ids), len(loaded), f"{engine}/{path.name}: повторяющиеся [id]")

    def test_headers_are_unique_within_catalog(self) -> None:
        for engine in ENGINES:
            for path in _catalog_files(engine):
                with self.subTest(engine=engine, catalog=path.name):
                    duplicates = sorted(
                        strategy_id
                        for strategy_id, count in Counter(_raw_header_ids(path)).items()
                        if count > 1
                    )
                    self.assertEqual(duplicates, [], f"{engine}/{path.name}: повторяющиеся [id]")

    def test_loaded_args_lines_are_options(self) -> None:
        # Пустые записи («Не применять ...») допустимы: у них просто нет строк.
        for engine in ENGINES:
            for path in _catalog_files(engine):
                with patch("profile.strategy_catalog.log"):
                    loaded = _parse_catalog_file(path, path.stem.lower())
                for strategy_id, entry in loaded.items():
                    with self.subTest(engine=engine, catalog=path.name, strategy_id=strategy_id):
                        bad_lines = [line for line in entry.args.splitlines() if not line.startswith("--")]
                        self.assertEqual(bad_lines, [])


class ParseCatalogWarningsTest(unittest.TestCase):
    def _parse(self, text: str):
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tcp.txt"
            path.write_text(text, encoding="utf-8")
            with patch("profile.strategy_catalog.log") as mock_log:
                entries = _parse_catalog_file(path, "tcp")
        return entries, mock_log

    def test_entry_with_profile_lines_is_dropped_with_one_warning(self) -> None:
        entries, mock_log = self._parse(
            "[good]\n"
            "--lua-desync=fake\n"
            "\n"
            "[scoped_entry]\n"
            "name = scoped\n"
            "--filter-l3=ipv4\n"
            "--lua-desync=multisplit\n"
        )

        self.assertEqual(list(entries), ["good"])
        warnings = _warning_messages(mock_log)
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("scoped_entry", warnings[0])
        self.assertIn("--filter-l3=ipv4", warnings[0])
        self.assertIn("tcp.txt", warnings[0])

    def test_duplicate_header_warns_and_last_copy_wins(self) -> None:
        entries, mock_log = self._parse(
            "[same]\n"
            "name = first\n"
            "--lua-desync=fake\n"
            "\n"
            "[same]\n"
            "name = second\n"
            "--lua-desync=multisplit\n"
        )

        self.assertEqual(list(entries), ["same"])
        self.assertEqual(entries["same"].name, "second")
        self.assertEqual(entries["same"].args, "--lua-desync=multisplit")
        warnings = _warning_messages(mock_log)
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("same", warnings[0])

    def test_duplicate_header_warns_even_when_first_copy_was_dropped(self) -> None:
        entries, mock_log = self._parse(
            "[same]\n"
            "--hostlist=lists/x.txt\n"
            "--lua-desync=fake\n"
            "\n"
            "[same]\n"
            "--lua-desync=multisplit\n"
        )

        self.assertEqual(entries["same"].args, "--lua-desync=multisplit")
        warnings = _warning_messages(mock_log)
        self.assertEqual(len(warnings), 2, warnings)
        self.assertTrue(any("повторяется" in message for message in warnings), warnings)

    def test_clean_catalog_does_not_log(self) -> None:
        entries, mock_log = self._parse(
            "# comment\n"
            "[one]\n"
            "name = One\n"
            "--lua-desync=fake\n"
            "\n"
            "[two]\n"
            "--lua-desync=multisplit:pos=1\n"
        )

        self.assertEqual(list(entries), ["one", "two"])
        mock_log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
