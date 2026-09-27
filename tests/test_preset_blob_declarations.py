"""Фейки (--blob=) выбранной стратегии объявляются в пресете явно.

Пресет — точка истины: при запуске ничего не подставляется, поэтому явный
выбор стратегии дописывает в преамбулу недостающие строки ``--blob=`` из
реестра фейков. Имя, уже объявленное в пресете, повторно не добавляется
(winws2 завершается на дубле имени), встроенные и lua-фейки не объявляются.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.paths import AppPaths
from fakes.public import FakeEntry, FakesCatalog
from profile.parser import parse_preset_text
from profile.preset_blob_declarations import (
    LUA_DEFINED_BLOB_NAMES,
    declared_blob_names,
    required_blob_names,
    with_declared_blobs,
)
from profile.serializer import serialize_preset
from profile.winws2_preset_source import WINWS2_LUA_INIT_LINES


TLS_GOOGLE_LINE = "--blob=tls_google:@bin/tls_clienthello_www_google_com.bin"
TLS_MAX_LINE = "--blob=tls_max:@bin/tls_clienthello_max_ru.bin"
ZERO4_LINE = "--blob=zero4:0x00000000"


def _file_entry(name: str, file_name: str) -> FakeEntry:
    return FakeEntry(
        name=name,
        source_kind="file",
        file_name=file_name,
        hex_value=None,
        kind="tls",
        sni=None,
        description="",
        same_bytes_as=None,
    )


def _catalog() -> FakesCatalog:
    return FakesCatalog(
        entries={
            "tls_google": _file_entry("tls_google", "tls_clienthello_www_google_com.bin"),
            "tls_max": _file_entry("tls_max", "tls_clienthello_max_ru.bin"),
            "zero4": FakeEntry(
                name="zero4",
                source_kind="hex",
                file_name=None,
                hex_value="00000000",
                kind="zeros",
                sni=None,
                description="",
                same_bytes_as=None,
            ),
        }
    )


def _loader():
    return _catalog


def _preset(text: str):
    return parse_preset_text(text, engine="winws2", source_name="selected.txt")


_LUA_INIT = "\n".join(WINWS2_LUA_INIT_LINES)


class RequiredBlobNamesTests(unittest.TestCase):
    def test_every_reference_key_is_collected(self) -> None:
        names = required_blob_names(
            [
                "--lua-desync=fake:blob=b1:repeats=2",
                "--lua-desync=fake:fake_blob=b2",
                "--lua-desync=multisplit:pos=2:seqovl=5:seqovl_pattern=b3",
                "--lua-desync=syndata:pattern=b4",
                "--lua-desync=tls_client_hello_clone:blob=clone_out:fallback=b5",
            ]
        )
        self.assertEqual(names, {"b1", "b2", "b3", "b4", "b5"})

    def test_clone_output_slot_is_not_a_reference_but_fallback_is(self) -> None:
        names = required_blob_names(["--lua-desync=tls_client_hello_clone:blob=my_clone:fallback=tls_google"])
        self.assertEqual(names, {"tls_google"})

    def test_hex_literals_and_non_identifiers_are_skipped(self) -> None:
        names = required_blob_names(
            [
                "--lua-desync=fake:blob=0x00000000",
                "--lua-desync=fake:blob=0XABCD",
                "--lua-desync=fake:blob=1bad",
                "--lua-desync=fake:blob=",
                "--lua-desync=fake:blob=tls_google:tls_mod=rnd,dupsid",
            ]
        )
        self.assertEqual(names, {"tls_google"})

    def test_comments_and_other_options_are_ignored(self) -> None:
        names = required_blob_names(
            [
                "# --lua-desync=fake:blob=commented",
                "--payload=tls_client_hello",
                "--lua-desync=multisplit:pos=1",
                "--hostlist=lists/blob=x.txt",
            ]
        )
        self.assertEqual(names, set())

    def test_inline_options_are_split_like_launch(self) -> None:
        names = required_blob_names(["--payload=all --lua-desync=fake:blob=tls_max"])
        self.assertEqual(names, {"tls_max"})


class DeclaredBlobNamesTests(unittest.TestCase):
    def test_declarations_anywhere_in_text(self) -> None:
        text = "\n".join(
            (
                "# --blob=commented:@bin/x.bin",
                _LUA_INIT,
                "--blob=tls_google:@bin/other.bin",
                "--blob=hex1:0x0102",
                "--blob=ofs1:+10@bin/x.bin",
                "--new",
                "--name=A",
                "--blob=in_profile:@bin/y.bin",
                "--lua-desync=fake:blob=tls_google",
                "",
            )
        )
        self.assertEqual(declared_blob_names(text), {"tls_google", "hex1", "ofs1", "in_profile"})

    def test_name_is_case_sensitive(self) -> None:
        self.assertEqual(declared_blob_names("--blob=TLS_Google:@bin/x.bin\n"), {"TLS_Google"})


class WithDeclaredBlobsTests(unittest.TestCase):
    def _text(self, *body: str) -> str:
        return "\n".join(("# Preset: T", "", _LUA_INIT, "", *body, ""))

    def test_missing_registry_blob_added_after_lua_init_block(self) -> None:
        text = self._text("--wf-tcp-out=443", "", "--new", "--name=A", "--filter-tcp=443", "--lua-desync=fake:blob=tls_google")
        updated, report = with_declared_blobs(_preset(text), ["--lua-desync=fake:blob=tls_google"], _loader())

        saved = serialize_preset(updated)
        self.assertEqual(report.added, ("tls_google",))
        self.assertEqual(saved.count("--blob=tls_google:"), 1)
        lines = saved.splitlines()
        last_init = max(i for i, line in enumerate(lines) if line.startswith("--lua-init="))
        self.assertEqual(lines[last_init + 1 : last_init + 3], ["", TLS_GOOGLE_LINE])
        self.assertEqual(lines[last_init + 3], "")
        self.assertIn("--wf-tcp-out=443", lines)
        # profile-ы не тронуты.
        self.assertEqual(
            [s.text for s in updated.profiles[0].segments],
            [s.text for s in _preset(text).profiles[0].segments],
        )

    def test_added_after_last_existing_blob_line_in_sorted_order(self) -> None:
        text = self._text("--blob=aaa:@bin/a.bin", "--blob=bbb:@bin/b.bin", "", "--new", "--name=A", "--lua-desync=pass")
        updated, report = with_declared_blobs(
            _preset(text),
            ["--lua-desync=fake:blob=zero4", "--lua-desync=fake:blob=tls_max", "--lua-desync=fake:blob=tls_google"],
            _loader(),
        )
        lines = serialize_preset(updated).splitlines()
        start = lines.index("--blob=bbb:@bin/b.bin") + 1
        self.assertEqual(lines[start : start + 3], [TLS_GOOGLE_LINE, TLS_MAX_LINE, ZERO4_LINE])
        self.assertEqual(report.added, ("tls_google", "tls_max", "zero4"))

    def test_blob_line_inside_profile_does_not_attract_insertion(self) -> None:
        text = self._text("--new", "--name=A", "--blob=own:@bin/o.bin", "--lua-desync=fake:blob=tls_google")
        updated, _report = with_declared_blobs(_preset(text), ["--lua-desync=fake:blob=tls_google"], _loader())
        lines = serialize_preset(updated).splitlines()
        self.assertLess(lines.index(TLS_GOOGLE_LINE), lines.index("--new"))

    def test_end_of_preamble_when_no_lua_init_and_no_blob(self) -> None:
        text = "--wf-tcp-out=443\n\n--new\n--name=A\n--lua-desync=fake:blob=tls_google\n"
        updated, _report = with_declared_blobs(_preset(text), ["--lua-desync=fake:blob=tls_google"], _loader())
        self.assertEqual(updated.preamble_lines[:3], ["--wf-tcp-out=443", "", TLS_GOOGLE_LINE])
        self.assertLess(serialize_preset(updated).index(TLS_GOOGLE_LINE), serialize_preset(updated).index("--new"))

    def test_preset_without_preamble_gets_blob_before_first_profile(self) -> None:
        text = "--name=A\n--filter-tcp=443\n--lua-desync=fake:blob=tls_google\n"
        updated, _report = with_declared_blobs(_preset(text), ["--lua-desync=fake:blob=tls_google"], _loader())
        saved = serialize_preset(updated)
        self.assertEqual(saved, f"{TLS_GOOGLE_LINE}\n{text}")
        self.assertEqual(len(_preset(saved).profiles), 1)
        self.assertEqual(_preset(saved).preamble_lines, [TLS_GOOGLE_LINE])

    def test_idempotent(self) -> None:
        text = self._text("--new", "--name=A", "--lua-desync=fake:blob=tls_google")
        once, _ = with_declared_blobs(_preset(text), ["--lua-desync=fake:blob=tls_google"], _loader())
        twice, report = with_declared_blobs(once, ["--lua-desync=fake:blob=tls_google"], _loader())
        self.assertIs(twice, once)
        self.assertEqual(report.added, ())
        self.assertEqual(serialize_preset(twice).count("--blob=tls_google:"), 1)

    def test_already_declared_with_other_file_is_a_conflict_and_text_unchanged(self) -> None:
        text = self._text("--blob=tls_google:@bin/my_own.bin", "", "--new", "--name=A", "--lua-desync=pass")
        preset = _preset(text)
        updated, report = with_declared_blobs(preset, ["--lua-desync=fake:blob=tls_google"], _loader())
        self.assertIs(updated, preset)
        self.assertEqual(report.conflicts, ("tls_google",))
        self.assertEqual(report.added, ())
        self.assertEqual(serialize_preset(updated), serialize_preset(_preset(text)))
        self.assertEqual(report.user_warnings(), ())

    def test_declared_after_new_or_as_hex_or_with_offset_is_not_added_again(self) -> None:
        for declaration in (
            "--blob=tls_google:0x1603",
            "--blob=tls_google:+5@bin/tls_clienthello_www_google_com.bin",
        ):
            with self.subTest(declaration=declaration):
                text = self._text("--new", "--name=A", declaration, "--lua-desync=fake:blob=tls_google")
                preset = _preset(text)
                updated, report = with_declared_blobs(preset, ["--lua-desync=fake:blob=tls_google"], _loader())
                self.assertIs(updated, preset)
                self.assertEqual(report.added, ())

    def test_builtin_and_lua_defined_names_are_never_declared(self) -> None:
        text = self._text("--new", "--name=A", "--lua-desync=pass")
        lines = [
            "--lua-desync=fake:blob=fake_default_tls",
            "--lua-desync=fake:blob=fake_default_quic",
            *(f"--lua-desync=fake:blob={name}" for name in sorted(LUA_DEFINED_BLOB_NAMES)),
        ]
        calls: list[int] = []

        def _counting_loader():
            calls.append(1)
            return _catalog()

        preset = _preset(text)
        updated, report = with_declared_blobs(preset, lines, _counting_loader)
        self.assertIs(updated, preset)
        self.assertEqual((report.added, report.unknown), ((), ()))
        self.assertEqual(calls, [], "реестр не нужен, если стратегия не ссылается на обычные фейки")

    def test_unknown_names_reported_not_added(self) -> None:
        text = self._text("--new", "--name=A", "--lua-desync=pass")
        updated, report = with_declared_blobs(
            _preset(text),
            ["--lua-desync=fake:blob=tls_google", "--lua-desync=fake:blob=no_such_fake"],
            _loader(),
        )
        self.assertEqual(report.added, ("tls_google",))
        self.assertEqual(report.unknown, ("no_such_fake",))
        self.assertNotIn("no_such_fake:", serialize_preset(updated))
        self.assertTrue(any("no_such_fake" in warning for warning in report.user_warnings()))

    def test_registry_failure_applies_nothing_and_warns(self) -> None:
        text = self._text("--new", "--name=A", "--lua-desync=pass")
        preset = _preset(text)

        def _broken():
            raise RuntimeError("файл не найден")

        for loader in (_broken, None):
            with self.subTest(loader=loader):
                updated, report = with_declared_blobs(preset, ["--lua-desync=fake:blob=tls_google"], loader)
                self.assertIs(updated, preset)
                self.assertEqual(report.unknown, ("tls_google",))
                self.assertTrue(report.catalog_error)
                self.assertEqual(len(report.user_warnings()), 1)

    def test_noop_when_strategy_references_no_fakes(self) -> None:
        text = self._text("--new", "--name=A", "--lua-desync=pass")
        preset = _preset(text)
        updated, report = with_declared_blobs(preset, ["--lua-desync=multisplit:pos=1"], None)
        self.assertIs(updated, preset)
        self.assertEqual(report.user_warnings(), ())


class _PresetStore:
    def __init__(self, text: str) -> None:
        self.text = text
        self.save_count = 0

    def read_selected_preset_source(self, _launch_method: str):
        return self.text, SimpleNamespace(file_name="selected.txt", name="selected")

    def save_selected_preset_source(self, _launch_method: str, text: str, **_kwargs) -> None:
        self.save_count += 1
        self.text = text


class ServiceApplyStrategyBlobTests(unittest.TestCase):
    def _write_catalog(self, root: Path) -> None:
        catalogs_dir = root / "system" / "strategy_catalogs" / "winws2"
        catalogs_dir.mkdir(parents=True)
        (catalogs_dir / "tcp.txt").write_text(
            "\n".join(
                (
                    "[fake_google]",
                    "name = Fake Google",
                    "--lua-desync=fake:blob=tls_google:repeats=6",
                    "",
                    "[fake_google_split]",
                    "name = Fake Google + split",
                    "--lua-desync=fake:blob=tls_google:repeats=2",
                    "--lua-desync=multisplit:pos=1",
                    "",
                    "[site_composite]",
                    "name = Site composite",
                    "--payload=tls_client_hello",
                    "--lua-desync=fake:blob=tls_google:repeats=6",
                    "--payload=http_req",
                    "--lua-desync=multisplit:pos=2",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (root / "system" / "templates").mkdir(parents=True)
        (root / "system" / "templates" / "all_profiles.txt").write_text("", encoding="utf-8")

    def _service(self, store, root: Path, loader=_loader()):
        from profile.service import ProfilePresetService

        feature = SimpleNamespace(
            _presets_feature=store,
            _app_paths=AppPaths(user_root=root, local_root=root),
            load_fakes_catalog=loader,
        )
        return ProfilePresetService(feature, "zapret2_mode")

    def _run(self, text: str, applies, loader=_loader()):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_catalog(root)
            store = _PresetStore(text)
            results = []
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                service = self._service(store, root, loader)
                for strategy_id in applies:
                    results.append(service.apply_strategy("profile:0", strategy_id))
        return store, results

    def test_plain_apply_declares_missing_fake_once_across_repeated_applies(self) -> None:
        text = "\n".join((_LUA_INIT, "", "--name=Site", "--filter-tcp=443", "--hostlist=lists/site.txt", "--lua-desync=pass", ""))
        store, results = self._run(text, ["fake_google", "fake_google_split", "fake_google"])

        self.assertEqual([result.status for result in results], ["applied", "applied", "applied"])
        self.assertEqual(store.text.count("--blob=tls_google:"), 1)
        self.assertIn(TLS_GOOGLE_LINE, store.text)
        preset = _preset(store.text)
        self.assertIn(TLS_GOOGLE_LINE, preset.preamble_lines)
        self.assertEqual(results[0].blob_warnings, ())

    def test_apply_onto_multi_branch_profile_declares_missing_fake(self) -> None:
        # Обычная стратегия поверх составной: один --payload с объединением
        # типов прежних веток, фейк стратегии объявлен один раз.
        text = "\n".join(
            (
                _LUA_INIT,
                "",
                "--name=Site",
                "--filter-tcp=80,443",
                "--hostlist=lists/site.txt",
                "--payload=tls_client_hello",
                "--lua-desync=pass",
                "--payload=http_req",
                "--lua-desync=multisplit:pos=2",
                "",
            )
        )
        store, results = self._run(text, ["fake_google", "fake_google_split"])

        self.assertEqual([result.status for result in results], ["applied", "applied"])
        self.assertEqual(store.text.count("--blob=tls_google:"), 1)
        self.assertIn(TLS_GOOGLE_LINE, _preset(store.text).preamble_lines)
        self.assertIn(
            "--payload=tls_client_hello,http_req\n"
            "--lua-desync=fake:blob=tls_google:repeats=2\n"
            "--lua-desync=multisplit:pos=1\n",
            store.text,
        )
        self.assertEqual(store.text.count("--payload="), 1)

    def test_apply_without_registry_writes_strategy_and_warns(self) -> None:
        text = "--name=Site\n--filter-tcp=443\n--hostlist=lists/site.txt\n--lua-desync=pass\n"

        def _broken():
            raise RuntimeError("файл не найден")

        store, results = self._run(text, ["fake_google"], loader=_broken)

        self.assertEqual(results[0].status, "applied")
        self.assertNotIn("--blob=", store.text)
        self.assertIn("--lua-desync=fake:blob=tls_google:repeats=6", store.text)
        self.assertEqual(len(results[0].blob_warnings), 1)
        self.assertIn("tls_google", results[0].blob_warnings[0])


    def _run_with_store(self, text: str, applies, loader=_loader()):
        """Как _run, но возвращает стор с числом записей после каждого применения."""
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_catalog(root)
            store = _PresetStore(text)
            results = []
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                service = self._service(store, root, loader)
                for strategy_id in applies:
                    results.append(service.apply_strategy("profile:0", strategy_id))
        return store, results

    def test_reselecting_applied_strategy_declares_only_missing_fake(self) -> None:
        body = ("--name=Site", "--filter-tcp=443", "--hostlist=lists/site.txt", "--lua-desync=fake:blob=tls_google:repeats=6")
        text = "\n".join((_LUA_INIT, "", "--wf-tcp-out=443", "", *body, ""))
        store, results = self._run_with_store(text, ["fake_google"])

        self.assertEqual(results[0].status, "already_applied")
        self.assertEqual(store.save_count, 1)
        added = [line for line in store.text.splitlines() if line not in text.splitlines()]
        self.assertEqual(added, [TLS_GOOGLE_LINE])
        self.assertEqual(store.text, text.replace(_LUA_INIT + "\n", _LUA_INIT + "\n\n" + TLS_GOOGLE_LINE + "\n", 1))

    def test_reselecting_applied_composite_strategy_declares_missing_fake(self) -> None:
        text = "\n".join(
            (
                _LUA_INIT,
                "",
                "--name=Site",
                "--filter-tcp=80,443",
                "--hostlist=lists/site.txt",
                "--payload=tls_client_hello",
                "--lua-desync=fake:blob=tls_google:repeats=6",
                "--payload=http_req",
                "--lua-desync=multisplit:pos=2",
                "",
            )
        )
        store, results = self._run_with_store(text, ["site_composite"])

        self.assertEqual(results[0].status, "already_applied")
        self.assertEqual(store.save_count, 1)
        self.assertEqual(store.text.count("--blob="), 1)
        self.assertIn(TLS_GOOGLE_LINE, _preset(store.text).preamble_lines)

    def test_reselecting_applied_strategy_with_all_fakes_declared_does_not_write(self) -> None:
        text = "\n".join(
            (
                _LUA_INIT,
                "",
                TLS_GOOGLE_LINE,
                "",
                "--name=Site",
                "--filter-tcp=443",
                "--hostlist=lists/site.txt",
                "--lua-desync=fake:blob=tls_google:repeats=6",
                "",
            )
        )
        store, results = self._run_with_store(text, ["fake_google", "fake_google"])

        self.assertEqual([result.status for result in results], ["already_applied", "already_applied"])
        self.assertEqual(store.save_count, 0)
        self.assertEqual(store.text, text)


_PRIVATE_CUSTOM_FUNCS = Path(__file__).resolve().parents[1].parent / "private_zapretgui" / "dist" / "lua" / "custom_funcs.lua"


class LuaDefinedBlobNamesTests(unittest.TestCase):
    @unittest.skipUnless(_PRIVATE_CUSTOM_FUNCS.is_file(), "нет private_zapretgui/dist/lua/custom_funcs.lua")
    def test_lua_defined_names_match_custom_funcs_globals(self) -> None:
        import re

        text = _PRIVATE_CUSTOM_FUNCS.read_text(encoding="utf-8", errors="replace")
        # Верхнеуровневые фейки вида `x = x or ...` — их задаёт lua-код, --blob= не нужен.
        names = set(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\1\s+or\b", text, re.MULTILINE))
        self.assertEqual(names, set(LUA_DEFINED_BLOB_NAMES))


class StrategyScannerProbePresetBlobTests(unittest.TestCase):
    def _scanner(self, work_dir: str, catalog):
        from blockcheck.strategy_scanner import StrategyScanner

        scanner = object.__new__(StrategyScanner)
        scanner._work_dir = work_dir
        scanner._scan_protocol = "tcp_https"
        scanner._cb = SimpleNamespace(on_log=lambda _message: None)
        scanner._load_fakes_catalog = (lambda: catalog) if catalog is not None else None
        scanner._prepare_fakes_catalog()
        return scanner

    def test_probe_preset_declares_strategy_fakes_after_lua_init_block(self) -> None:
        with TemporaryDirectory() as temp_dir:
            scanner = self._scanner(temp_dir, _catalog())
            path = scanner._write_temp_preset(
                "--lua-desync=fake:blob=tls_google:repeats=2\n--lua-desync=fake:blob=fake_default_tls",
                "discord.com",
            )
            lines = Path(path).read_text(encoding="utf-8").splitlines()

        count = len(WINWS2_LUA_INIT_LINES)
        self.assertEqual(lines[:count], list(WINWS2_LUA_INIT_LINES))
        self.assertEqual(lines[count : count + 3], ["", TLS_GOOGLE_LINE, ""])
        self.assertEqual(sum(1 for line in lines if line.startswith("--blob=")), 1)

    def test_probe_preset_without_registry_has_no_blob_lines(self) -> None:
        with TemporaryDirectory() as temp_dir:
            scanner = self._scanner(temp_dir, None)
            path = scanner._write_temp_preset("--lua-desync=fake:blob=tls_google", "discord.com")
            text = Path(path).read_text(encoding="utf-8")

        self.assertNotIn("--blob=", text)
        self.assertIn("--lua-desync=fake:blob=tls_google", text)


if __name__ == "__main__":
    unittest.main()
