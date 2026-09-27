"""Ветки ``--payload`` одного profile-а — это ОДНА готовая стратегия.

Profile winws2, где стратегия разбита на ветки для разных типов пакетов
(``--payload=tls_client_hello`` / ``--payload=http_req`` / ...), узнаётся как
одна составная запись каталога: одно имя, одна оценка, выбирается и
применяется целиком (см. ``profile.strategy_shape``).
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.paths import AppPaths
from profile.derived_cache import (
    basic_strategy_entries,
    profile_strategy_shape,
    resolve_strategy,
)
from profile.parser import parse_preset_text
from profile.serializer import serialize_preset, with_profile_whole_strategy
from profile.service import ProfilePresetService
from profile.strategy_catalog import _parse_catalog_file, load_strategy_catalogs
from profile.strategy_shape import payload_badge_text, strategy_shape, union_payload


PUBLIC_ROOT = Path(__file__).resolve().parents[1]
BUILTIN_WINWS2 = PUBLIC_ROOT / "src" / "presets" / "builtin" / "winws2"

_CATALOG = "\n".join(
    (
        "[tls_ozon]",
        "name = TLS Ozon",
        "--lua-desync=hostfakesplit:host=ozon.ru:tcp_ts=-1000:tcp_md5:repeats=4",
        "",
        "[http_vk]",
        "name = HTTP VK",
        "--lua-desync=hostfakesplit:host=vk.com:tcp_ts=-3000:tcp_md5:repeats=2",
        "",
        "[ozon_both]",
        "name = Ozon TLS + HTTP",
        "author = test",
        "--payload=tls_client_hello",
        "--lua-desync=hostfakesplit:host=ozon.ru:tcp_ts=-1000:tcp_md5:repeats=4",
        "--payload=http_req",
        "--lua-desync=hostfakesplit:host=ozon.ru:tcp_ts=-2000:tcp_md5:repeats=4",
        "",
    )
)

_COMPOSITE_LINES = (
    "--payload=tls_client_hello",
    "--lua-desync=hostfakesplit:host=ozon.ru:tcp_ts=-1000:tcp_md5:repeats=4",
    "--payload=http_req",
    "--lua-desync=hostfakesplit:host=ozon.ru:tcp_ts=-2000:tcp_md5:repeats=4",
)

_PROFILE_HEAD = (
    "--name=youtube.com (интерфейс)",
    "--filter-tcp=80,443",
    "--hostlist=lists/youtube.txt",
)


class _PresetStore:
    def __init__(self, text: str) -> None:
        self.text = text
        self.save_count = 0

    def read_selected_preset_source(self, _launch_method: str):
        return self.text, SimpleNamespace(file_name="selected.txt", name="selected")

    def save_selected_preset_source(self, _launch_method: str, text: str, **_kwargs) -> None:
        self.save_count += 1
        self.text = text


def _text(*strategy_lines: str) -> str:
    return "\n".join((*_PROFILE_HEAD, *strategy_lines, ""))


class _ServiceCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.root = Path(self._temp.name)
        catalogs_dir = self.root / "system" / "strategy_catalogs" / "winws2"
        catalogs_dir.mkdir(parents=True)
        (catalogs_dir / "tcp.txt").write_text(_CATALOG, encoding="utf-8")
        (self.root / "system" / "templates").mkdir(parents=True)
        (self.root / "system" / "templates" / "all_profiles.txt").write_text("", encoding="utf-8")
        self._settings_patch = patch("settings.store.MAIN_DIRECTORY", str(self.root))
        self._settings_patch.start()

    def tearDown(self) -> None:
        self._settings_patch.stop()
        self._temp.cleanup()

    def _service(self, text: str) -> tuple[ProfilePresetService, _PresetStore]:
        store = _PresetStore(text)
        feature = SimpleNamespace(
            _presets_feature=store,
            _app_paths=AppPaths(user_root=self.root, local_root=self.root),
        )
        return ProfilePresetService(feature, "zapret2_mode"), store


class CompositeStrategyServiceTests(_ServiceCase):
    def test_payload_branches_are_one_ready_strategy_with_badge(self) -> None:
        service, _store = self._service(_text("--out-range=-d8", *_COMPOSITE_LINES))

        setup = service.get_profile_setup("profile:0")

        self.assertEqual((setup.item.strategy_id, setup.item.strategy_name), ("ozon_both", "Ozon TLS + HTTP"))
        self.assertEqual(setup.item.strategy_payload_scopes, ("tls_client_hello", "http_req"))
        self.assertEqual(payload_badge_text(setup.item.strategy_payload_scopes), "TLS · HTTP")
        self.assertIn("Ozon TLS + HTTP", setup.match_tab_text)
        self.assertEqual(setup.out_range, "-d8")

    def test_unknown_payload_branches_are_one_custom_strategy(self) -> None:
        service, _store = self._service(
            _text(
                "--payload=tls_client_hello",
                "--lua-desync=hostfakesplit:host=ozon.ru:tcp_ts=-1000:tcp_md5:repeats=4",
                "--payload=http_req",
                "--lua-desync=hostfakesplit:host=vk.com:tcp_ts=-3000:tcp_md5:repeats=2",
            )
        )

        item = service.list_profiles().items[0]

        # Раньше тут было «2 стратегии: TLS Ozon, HTTP VK».
        self.assertEqual((item.strategy_id, item.strategy_name), ("custom", "Своя стратегия"))

    def test_ordinary_strategy_on_multi_branch_profile_writes_one_union_payload(self) -> None:
        service, store = self._service(_text("--out-range=-d8", *_COMPOSITE_LINES))

        result = service.apply_strategy("profile:0", "http_vk")

        self.assertEqual(result.status, "applied")
        self.assertEqual(
            store.text,
            _text(
                "--out-range=-d8",
                "--payload=tls_client_hello,http_req",
                "--lua-desync=hostfakesplit:host=vk.com:tcp_ts=-3000:tcp_md5:repeats=2",
            ),
        )
        self.assertEqual(service.get_profile_setup("profile:0").item.strategy_id, "http_vk")

    def test_ordinary_strategy_on_branches_with_all_writes_payload_all(self) -> None:
        service, store = self._service(
            _text(
                "--lua-desync=hostfakesplit:host=ozon.ru:tcp_ts=-1000:tcp_md5:repeats=4",
                "--payload=http_req",
                "--lua-desync=hostfakesplit:host=ozon.ru:tcp_ts=-2000:tcp_md5:repeats=4",
            )
        )

        result = service.apply_strategy("profile:0", "tls_ozon")

        self.assertEqual(result.status, "applied")
        self.assertEqual(
            store.text,
            _text("--payload=all", "--lua-desync=hostfakesplit:host=ozon.ru:tcp_ts=-1000:tcp_md5:repeats=4"),
        )

    def test_composite_strategy_on_single_branch_profile_has_no_double_payload(self) -> None:
        service, store = self._service(
            _text("--out-range=-d8", "--payload=tls_client_hello", "--lua-desync=hostfakesplit:host=vk.com:tcp_ts=-3000:tcp_md5:repeats=2")
        )

        result = service.apply_strategy("profile:0", "ozon_both")

        self.assertEqual(result.status, "applied")
        self.assertEqual(store.text, _text("--out-range=-d8", *_COMPOSITE_LINES))
        self.assertEqual(service.get_profile_setup("profile:0").item.strategy_id, "ozon_both")

    def test_reselecting_applied_composite_strategy_does_not_write(self) -> None:
        text = _text("--out-range=-d8", *_COMPOSITE_LINES)
        service, store = self._service(text)

        result = service.apply_strategy("profile:0", "ozon_both")

        self.assertEqual(result.status, "already_applied")
        self.assertEqual(store.save_count, 0)
        self.assertEqual(store.text, text)

    def test_rating_and_favorite_persist_for_composite_strategy(self) -> None:
        service, _store = self._service(_text(*_COMPOSITE_LINES))

        state = service.set_current_strategy_state("profile:0", rating="work", favorite=True)
        item = service.list_profiles().items[0]
        setup = service.get_profile_setup("profile:0")

        self.assertIsNotNone(state)
        self.assertEqual((item.strategy_id, item.rating, item.favorite), ("ozon_both", "work", True))
        self.assertEqual(setup.current_strategy_state.rating, "work")
        self.assertTrue(setup.strategy_states["ozon_both"].favorite)


class CompositeCatalogLoaderTests(unittest.TestCase):
    def _parse(self, text: str):
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tcp.txt"
            path.write_text(text, encoding="utf-8")
            with patch("profile.strategy_catalog.log") as mock_log:
                entries = _parse_catalog_file(path, "tcp")
        warnings = [call.args[0] for call in mock_log.call_args_list if len(call.args) > 1 and call.args[1] == "WARNING"]
        return entries, warnings

    def test_composite_entry_is_loaded_with_payload_scopes(self) -> None:
        entries, warnings = self._parse(_CATALOG)

        self.assertEqual(warnings, [])
        self.assertTrue(entries["ozon_both"].is_composite)
        self.assertEqual(entries["ozon_both"].payload_scopes, ("tls_client_hello", "http_req"))
        self.assertFalse(entries["tls_ozon"].is_composite)
        self.assertEqual(entries["tls_ozon"].payload_scopes, ())

    def test_range_after_first_lua_desync_is_a_branch_separator(self) -> None:
        entries, warnings = self._parse(
            "[split_by_range]\n--lua-desync=syndata\n--out-range=-d10\n--lua-desync=multisplit\n"
        )

        self.assertEqual(warnings, [])
        self.assertTrue(entries["split_by_range"].is_composite)
        self.assertEqual(entries["split_by_range"].payload_scopes, ("all", "all"))

    def test_range_before_first_lua_desync_drops_entry_with_warning(self) -> None:
        entries, warnings = self._parse(
            "[good]\n--lua-desync=fake\n\n"
            "[early_range]\n--out-range=-d8\n--payload=tls_client_hello\n--lua-desync=fake\n"
            "--payload=http_req\n--lua-desync=multisplit\n"
        )

        self.assertEqual(list(entries), ["good"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("early_range", warnings[0])
        self.assertIn("--out-range=-d8", warnings[0])

    def test_payload_without_second_branch_drops_entry_with_warning(self) -> None:
        entries, warnings = self._parse("[one_branch]\n--payload=tls_client_hello\n--lua-desync=fake\n")

        self.assertEqual(entries, {})
        self.assertEqual(len(warnings), 1)
        self.assertIn("one_branch", warnings[0])


class PayloadBadgeTests(unittest.TestCase):
    def test_badge_texts(self) -> None:
        self.assertEqual(
            payload_badge_text(("tls_client_hello", "http_req", "tls_client_hello,http_req")),
            "TLS · HTTP · TLS+HTTP",
        )
        self.assertEqual(
            payload_badge_text(("quic_initial", "wireguard_initiation,dht,discord_ip_discovery,stun", "all")),
            "QUIC · игры · прочее",
        )
        self.assertEqual(payload_badge_text(("all", "all")), "всё")
        self.assertEqual(payload_badge_text(("tls_client_hello",)), "")

    def test_union_payload(self) -> None:
        self.assertEqual(union_payload(("tls_client_hello", "http_req", "tls_client_hello,http_req")), "tls_client_hello,http_req")
        self.assertEqual(union_payload(("quic_initial", "all")), "all")


class WholeStrategySerializerTests(unittest.TestCase):
    def test_profile_range_stays_in_place_before_match_line(self) -> None:
        text = "\n".join(
            (
                "--name=discord.media (voice RTC)",
                "--filter-tcp=443",
                "--out-range=-d10",
                "--hostlist=lists/discord-media.txt",
                "--lua-desync=fake:blob=tls_google",
                "--payload=http_req",
                "--lua-desync=fake:blob=fake_default_http",
                "",
            )
        )
        preset = parse_preset_text(text, engine="winws2")

        updated = with_profile_whole_strategy(preset, 0, ["--lua-desync=multisplit", "--payload=http_req", "--lua-desync=fake"])

        self.assertEqual(
            serialize_preset(updated),
            "\n".join(
                (
                    "--name=discord.media (voice RTC)",
                    "--filter-tcp=443",
                    "--out-range=-d10",
                    "--hostlist=lists/discord-media.txt",
                    "--lua-desync=multisplit",
                    "--payload=http_req",
                    "--lua-desync=fake",
                    "",
                )
            ),
        )


def _builtin_composite_profiles():
    paths = AppPaths(user_root=(PUBLIC_ROOT / "src").resolve(), local_root=(PUBLIC_ROOT / "src").resolve())
    catalogs = load_strategy_catalogs(paths, "winws2")
    for path in sorted(BUILTIN_WINWS2.glob("*.txt")):
        if "(circular)" in path.stem.lower():
            continue
        preset = parse_preset_text(path.read_text(encoding="utf-8"), engine="winws2", source_name=path.name)
        for profile in preset.profiles:
            shape = profile_strategy_shape(profile)
            if not shape.composite or any("circular:" in line.lower() for line in shape.body_lines):
                continue
            yield path.name, profile, basic_strategy_entries(profile, catalogs)


def _single_profile_preset(profile):
    return parse_preset_text("\n".join(segment.text for segment in profile.segments), engine="winws2")


def _reorders_on_whole_replace(profile) -> bool:
    """Форма, которую замена стратегии целиком переставляет (смысл тот же).

    Диапазон profile-а стоит после ``--payload`` или match-строка стоит между
    строками стратегии: новые строки встают одним блоком после диапазонов.
    """
    kinds = [segment for segment in profile.segments if segment.kind not in {"blank", "comment"}]
    strategy_positions = [index for index, segment in enumerate(kinds) if segment.kind in {"strategy", "strategy_filter"}]
    inner = kinds[strategy_positions[0] : strategy_positions[-1] + 1]
    first_lua = next(index for index, segment in enumerate(inner) if segment.kind == "strategy")
    seen_payload = False
    for index, segment in enumerate(inner):
        name = str(segment.name or "").lower()
        if segment.kind == "strategy_filter" and name == "--payload" and index < first_lua:
            seen_payload = True
        elif segment.kind == "strategy_filter" and index < first_lua and seen_payload:
            return True
        elif segment.kind not in {"strategy", "strategy_filter"} and index > first_lua:
            return True
    return False


class BuiltinCompositeCoverageTests(unittest.TestCase):
    def test_every_multi_branch_builtin_profile_is_exactly_one_composite_entry(self) -> None:
        checked = 0
        for preset_name, profile, entries in _builtin_composite_profiles():
            checked += 1
            with self.subTest(preset=preset_name, profile=profile.display_name):
                strategy_id, _name = resolve_strategy(profile, entries)
                self.assertNotEqual(strategy_id, "custom")
                self.assertTrue(entries[strategy_id].is_composite)
                body = strategy_shape(entries[strategy_id].args.splitlines()).body_lines
                self.assertEqual(
                    [entry_id for entry_id, entry in entries.items() if entry.is_composite and strategy_shape(entry.args.splitlines()).body_lines == body],
                    [strategy_id],
                )
        self.assertGreater(checked, 1000)

    def test_applying_resolved_composite_entry_keeps_profile_text(self) -> None:
        reordered = 0
        for preset_name, profile, entries in _builtin_composite_profiles():
            strategy_id, _name = resolve_strategy(profile, entries)
            preset = _single_profile_preset(profile)
            updated = with_profile_whole_strategy(preset, 0, entries[strategy_id].args.splitlines())
            with self.subTest(preset=preset_name, profile=profile.display_name):
                if _reorders_on_whole_replace(profile):
                    reordered += 1
                    before = profile_strategy_shape(preset.profiles[0])
                    after = profile_strategy_shape(updated.profiles[0])
                    self.assertEqual(sorted(after.profile_range_lines), sorted(before.profile_range_lines))
                    self.assertEqual(after.body_lines, before.body_lines)
                    self.assertEqual(sorted(updated.profiles[0].match.all_lines()), sorted(preset.profiles[0].match.all_lines()))
                    continue
                self.assertEqual(serialize_preset(updated), serialize_preset(preset))
        # 9 profile-ов Ростелекома (--filter-tcp между строками стратегии) и
        # «Все сайты (айпи)» в general SIMPLE FAKE ALT2 (game filter)
        # (диапазон после --payload) — список не должен расти молча.
        self.assertEqual(reordered, 10)


if __name__ == "__main__":
    unittest.main()
