"""Builtin winws2 ports of Flowseal zapret-discord-youtube 1.10.3 (all 22 bats).

The ports keep the canonical 99-profile layout of `general EXP 1.10.0 (game filter)`
and translate every bat block with payload-scoped branches (exact port, not the
`--payload=all` generalisation of the 1.9.9 set). The old ports stay untouched.
"""

from __future__ import annotations

from pathlib import Path
import re
import hashlib
import unittest

from core.paths import AppPaths
from folders.defaults import classify_preset_folder
from profile.derived_cache import (
    basic_strategy_entries,
    normalize_lines,
    profile_strategy_shape,
    resolve_strategy,
)
from profile.models import build_profile_logical_key
from profile.parser import parse_preset_text
from profile.strategy_catalog import load_strategy_catalogs
from profile.winws2_preset_source import WINWS2_LUA_INIT_LINES, canonical_winws2_lua_init_path


PUBLIC_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = PUBLIC_ROOT.parent / "private_zapretgui"
WINWS2_DIR = PUBLIC_ROOT / "src" / "presets" / "builtin" / "winws2"
SHIPPED_BIN_DIR = PRIVATE_ROOT / "dist" / "bin"
ALL_PROFILES_PATH = PRIVATE_ROOT / "resources" / "profile" / "templates" / "all_profiles.txt"

_V = "1.10.3"

# variant -> (game TCP cutoff, game UDP cutoff, game TCP fake-unknown blobs,
#             game UDP fake-unknown-udp blobs, game UDP repeats), taken from the 1.10.3 bats.
GAME: dict[str, tuple[str, str, tuple[str, ...], tuple[str, ...], int]] = {
    "": ("n3", "n2", (), ("game_active",), 12),
    "ALT1": ("n4", "n3", ("stun_pat", "tls_google"), ("game_active",), 12),
    "ALT2": ("n3", "n2", (), ("game_active",), 12),
    "ALT3": ("n4", "n4", ("tls_max",), ("game_active",), 10),
    "ALT4": ("n3", "n2", ("stun_pat", "tls_google"), ("game_active",), 10),
    "ALT5": ("n4", "n3", (), ("game_active",), 14),
    "ALT6": ("n3", "n2", (), ("game_active",), 12),
    "ALT7": ("n4", "n2", (), ("game_active",), 12),
    "ALT8": ("n3", "n2", ("tls_max",), ("game_active",), 12),
    "ALT9": ("n3", "n2", (), ("game_active",), 12),
    "ALT10": ("n3", "n2", ("stun_pat", "tls_4pda"), ("game_active",), 12),
    "ALT11": ("n4", "n4", ("stun2", "tls_max"), ("game_active",), 10),
    "ALT12": ("n4", "n4", ("stun_pat", "tls_max"), ("game_active",), 10),
    "ALT13": ("n4", "n4", ("tls_sochi", "stun2"), ("game_active",), 10),
    "FAKE TLS AUTO": ("n4", "n2", ("0x00000000", "tls_max"), ("game_active",), 10),
    "FAKE TLS AUTO ALT": ("n3", "n2", ("tls_max",), ("game_active",), 10),
    "FAKE TLS AUTO ALT2": ("n3", "n2", ("tls_max",), ("game_active",), 10),
    "FAKE TLS AUTO ALT3": ("n4", "n3", ("tls_max",), ("game_active",), 10),
    "SIMPLE FAKE": ("n4", "n3", ("stun2", "tls_google"), ("game_active",), 12),
    "SIMPLE FAKE ALT": ("n3", "n2", ("stun2", "tls_google"), ("game_active",), 10),
    "SIMPLE FAKE ALT2": ("n5", "n3", ("stun2", "tls_max"), ("game_active",), 12),
    "EXP": ("n4", "n4", ("fake_unknown_256",), ("quic_4pda", "game_active"), 5),
}
VARIANTS = tuple(GAME)
UDP_KNOWN = "quic_initial,wireguard_initiation,dht,discord_ip_discovery,stun"
BUILTIN_BLOBS = {"fake_default_tls", "fake_default_http", "fake_default_quic", "fake_unknown_256", "fake_zero64"}
BLOB_FILES = {
    "quic_google": "quic_initial_www_google_com.bin",
    "discord_active": "ACTIVE_DISCORD_UDP.bin",
    "tls_google": "tls_clienthello_www_google_com.bin",
    "tls_max": "tls_clienthello_max_ru.bin",
    "stun2": "stun2.bin",
    "stun_pat": "stun.bin",
    "tls_4pda": "tls_clienthello_4pda_to.bin",
    "tls_sochi": "tls_clienthello_sochi_park.bin",
    "quic_4pda": "quic_initial_4pda_to.bin",
    "game_active": "ACTIVE_GAME_UDP.bin",
}
IPSET_TCP_HYBRID = "OVH TCP"
IPSET_UDP_HYBRID = "Cloudflare UDP"

# sha256 of the old Flowseal ports as committed before the 1.10.3 set was added; the new
# ports must be added next to them, never by editing them. The only allowed edits of the old
# ports are format-wide ones that every builtin winws2 preset gets: the mandatory lua-init block
# (presets.preset_contract, WINWS2_LUA_INIT_LINES) and the `# BuiltinVersion:` line. The hash is
# therefore taken over the text with exactly those lines masked out (_masked_sha256), so any other
# change to an old port still fails this pin.
# 2026-10-06: the pins were refreshed once for another format-wide edit — every builtin preset
# got the «Сервис · роль» profile names (`--name=YouTube · видео (googlevideo.com)` instead of
# `--name=googlevideo.com (CDN сервера)`); strategy lines were not touched.
# 2026-10-27: refreshed again for two more format-wide edits: profile #1 git.zapret.moe became
# zapret.moe with lists/zapret-moe.txt (2026-10-16), and every builtin preset got the Signal
# profile as #2. Undoing exactly those two edits reproduces the previous pins for all 20 files.
# 2026-10-28: refreshed for the Matrix and SimpleX profiles added as #3–#4 to every builtin preset.
# Removing exactly those two blocks reproduces the previous pins for all 20 files.
# 2026-11-21: refreshed for the vencord.dev profile added after "Discord · сайт и приложение" (a copy of that
# profile's strategy with lists/vencord.txt); removing that one block reproduces the previous pins.
OLD_PORT_SHA256 = {
    "general 1.9.9 (game filter).txt": "0cbd0d42157bc46a66290c7a41fe49e50c7e07eb71847c1f50283e618bd0c3b1",
    "general ALT10 1.9.9 (game filter).txt": "078a7bbaafef32ef991a6a0f255dcade7e7592bee7ca58171f0cbe3215d8c4f5",
    "general ALT11 1.9.9 (game filter).txt": "8572dbfdd89701a62e8ffa309cef1309c08b4ba84a04d4f7607df54b38371f30",
    "general ALT1 1.9.9 (game filter).txt": "64d15fe91a9ed5345a5ec9773de6886975c06e64aa75bba5db44a46255835532",
    "general ALT12 1.9.9 (game filter).txt": "afa03a1abc39518c0807ebe6015bcac41e147c3edceaa191a60741162826a08a",
    "general ALT2 1.9.9 (game filter).txt": "5b96405653fe9e4e88d5d3a62c03ef475bdc25ffdbcdc6075f5cf3ef0fd4391d",
    "general ALT3 1.9.9 (game filter).txt": "6c65a15df12aa7c18bdf15d51329cc0879e24c041a761927f835e69b51b090c4",
    "general ALT4 1.9.9 (game filter).txt": "777d5c2654d4dad6f5a2e01dd10490654bab819de1b924a57da486e633922a02",
    "general ALT6 1.9.9 (game filter).txt": "b55c539bd96b591bfa2c766cc520b533262214037e10de4c6da88f44c688c121",
    "general ALT7 1.9.9 (game filter).txt": "945819cda4940b4a8af7463f84787ba002d5a539113d005184069b4f904824e3",
    "general ALT8 1.9.9 (game filter).txt": "83a27b9ffeb42b50a714a16b55289b6a04bf5b7850049966e38edba934914750",
    "general ALT9 1.9.9 (game filter).txt": "ae3e3327d23e77ba7fede8db6ddb24c5be603feb4224ab44869c4af252f6f2a1",
    "general EXP 1.10.0 (game filter).txt": "73d1a76653a627fb01088d6487ebb9ea1e1ae2593f1ed6e9d124e204bc5756f2",
    "general FAKE TLS AUTO 1.9.9 (game filter).txt": "c29d31d98093b22fcfc1d8544dc04637dc7e6499263edf895e5f481f304f1fee",
    "general FAKE TLS AUTO ALT 1.9.9 (game filter).txt": "6b4d0ac457668ba28837a454170b151606b67fdd22e098fb1ac70e81242669c0",
    "general FAKE TLS AUTO ALT2 1.9.9 (game filter).txt": "67ed31a1b148ca31f23902bea7db073ea12191e42e163349901cbf9f63293832",
    "general FAKE TLS AUTO ALT3 1.9.9 (game filter).txt": "9105525b0acf9bbf8821f91591079683220c2c8e3219df9c7ee69872f4293e50",
    "general SIMPLE FAKE 1.9.9 (game filter).txt": "53376dc64ce017209103d372d1cb9b5d78ab5800ecc22c01d9a509489f9fdfc0",
    "general SIMPLE FAKE ALT 1.9.9 (game filter).txt": "c536c407a4a33f0a2f733f9264be09fc1a755f396409e099d746acf9c8cd03bb",
    "general SIMPLE FAKE ALT2 1.9.9 (game filter).txt": "c9b22272b454f8b3e6c6fabe6db82eea5cda3b8c337350732f382d92ca0fa13a",
}
_BATCH_SYNTAX = re.compile(r"%[A-Za-z_]+%|\^|\"|(?:^|\s)start(?:\s|$)|call service\.bat|winws\.exe", re.MULTILINE)


def _masked_sha256(data: bytes) -> str:
    """sha256 of a preset without its canonical lua-init block lines and BuiltinVersion line."""
    lines = [
        line
        for line in data.decode("utf-8").split("\n")
        if canonical_winws2_lua_init_path(line) is None and not line.startswith("# BuiltinVersion: ")
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _name(variant: str) -> str:
    return f"general {variant + ' ' if variant else ''}{_V} (game filter).txt"


NEW_FILES = tuple(_name(variant) for variant in VARIANTS)


def _read(name: str) -> str:
    return (WINWS2_DIR / name).read_text(encoding="utf-8")


def _preset(name: str):
    return parse_preset_text(_read(name), engine="winws2", source_name=name)


def _profile(preset, display_name: str):
    matches = [profile for profile in preset.profiles if profile.display_name == display_name]
    assert len(matches) == 1, display_name
    return matches[0]


def _strategy(profile) -> tuple[str, ...]:
    return normalize_lines(profile.strategy.strategy_lines)


def _lua(profile) -> list[str]:
    return [line for line in _strategy(profile) if line.startswith("--lua-desync=")]


def _blobs_in(lines) -> list[str]:
    return [m.group(1) for line in lines for m in re.finditer(r"(?:blob|seqovl_pattern)=([A-Za-z0-9_]+)", line)]


def _named_blobs_in(lines) -> set[str]:
    """Blob names that need a declaration (inline 0x... hex blobs do not)."""
    return {name for name in _blobs_in(lines) if not name.startswith("0x")}


class Flowseal1103Winws2PresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        paths = AppPaths(user_root=(PUBLIC_ROOT / "src").resolve(), local_root=(PUBLIC_ROOT / "src").resolve())
        cls.catalogs = load_strategy_catalogs(paths, "winws2")
        cls.presets = {name: _preset(name) for name in NEW_FILES}

    def test_all_22_presets_exist(self) -> None:
        self.assertEqual(len(NEW_FILES), 22)
        present = sorted(path.name for path in WINWS2_DIR.glob(f"*{_V}*.txt"))
        self.assertEqual(present, sorted(NEW_FILES))

    def test_headers(self) -> None:
        for name in NEW_FILES:
            with self.subTest(name=name):
                lines = _read(name).splitlines()
                self.assertEqual(lines[0], f"# Preset: {name[:-4]}")
                # Номер не ниже того, с которым набор добавлен: каждая правка
                # пресета поднимает его на шаг.
                version = re.fullmatch(r"# BuiltinVersion: (\d+)\.(\d+)", lines[1])
                self.assertIsNotNone(version, lines[1])
                self.assertGreaterEqual((int(version.group(1)), int(version.group(2))), (2, 42))
                self.assertRegex(lines[2], r"^# IconColor: #[0-9a-f]{6}([0-9a-f]{2})?$")
                self.assertTrue(lines[3].startswith(f"# Description: Flowseal {_V} "))
                self.assertTrue(lines[3].endswith(", adapted to ZapretGUI profiles"))

    def test_no_batch_or_winws1_syntax(self) -> None:
        for name in NEW_FILES:
            with self.subTest(name=name):
                text = _read(name)
                self.assertIsNone(_BATCH_SYNTAX.search(text))
                self.assertNotIn("\r", text)
                self.assertNotIn("--dpi-desync", text)
                self.assertNotIn("-user.txt", text)
                self.assertNotIn("--hostlist=lists/list-general-user.txt", text)
                self.assertNotIn("--wf-tcp=", text)
                self.assertNotIn("\n\n\n", text)
                self.assertTrue(text.endswith("\n"))

    def test_profiles_are_the_exact_canonical_exp_1100_layout(self) -> None:
        skeleton = _preset("general EXP 1.10.0 (game filter).txt")
        expected = [(p.display_name, tuple(p.match.all_lines())) for p in skeleton.profiles]
        catalog_pairs = None
        if ALL_PROFILES_PATH.is_file():
            all_profiles = parse_preset_text(
                ALL_PROFILES_PATH.read_text(encoding="utf-8"), engine="winws2", source_name=ALL_PROFILES_PATH.name
            )
            catalog_pairs = {
                (build_profile_logical_key(p.match_signature), str(p.name or "").strip())
                for p in all_profiles.profiles
            }
        for name, preset in self.presets.items():
            with self.subTest(name=name):
                self.assertEqual(len(preset.profiles), 102)
                self.assertEqual([(p.display_name, tuple(p.match.all_lines())) for p in preset.profiles], expected)
                if catalog_pairs is not None:
                    offenders = [
                        p.display_name
                        for p in preset.profiles
                        if (build_profile_logical_key(p.match_signature), str(p.name or "").strip())
                        not in catalog_pairs
                    ]
                    self.assertEqual(offenders, [])

    def test_gui_conventions(self) -> None:
        for name, preset in self.presets.items():
            with self.subTest(name=name):
                first = preset.profiles[0]
                self.assertEqual(first.display_name, "zapret.moe")
                self.assertEqual(
                    _strategy(first),
                    ("--out-range=-d8", "--lua-desync=hostfakesplit:host=ozon.ru:tcp_ts=-1000:tcp_md5:repeats=4"),
                )
                messengers = preset.profiles[1:4]
                self.assertEqual(
                    [p.display_name for p in messengers], ["Signal", "Matrix", "SimpleX"]
                )
                for messenger in messengers:
                    self.assertEqual(_strategy(messenger), _strategy(first))
                backend = _profile(preset, "Бэкенд Google")
                youtube = _profile(preset, "YouTube · сайт и приложение")
                self.assertEqual(_strategy(backend), _strategy(youtube))
                self.assertIn("\n\n--new\n\n", _read(name))

    def test_blobs_are_declared_used_and_shipped(self) -> None:
        for name in NEW_FILES:
            with self.subTest(name=name):
                text = _read(name)
                declared = {}
                for line in text.splitlines():
                    if line.startswith("--blob="):
                        blob_name, _, source = line[len("--blob="):].partition(":")
                        declared[blob_name] = source
                used = _named_blobs_in(line for line in text.splitlines() if line.startswith("--lua-desync="))
                self.assertEqual(used - set(declared) - BUILTIN_BLOBS, set())
                self.assertEqual(set(declared) - used - {"tls_google"}, set())
                self.assertIn("tls_google", declared)
                for blob_name, source in declared.items():
                    self.assertEqual(source, f"@bin/{BLOB_FILES[blob_name]}")
                # fake_unknown_256 / fake_zero64 come from the core lua custom_funcs.lua;
                # the mandatory block is the only lua-init content of these ports.
                self.assertEqual(
                    [line for line in text.splitlines() if line.startswith("--lua-init=")],
                    list(WINWS2_LUA_INIT_LINES),
                )

    def test_declared_bins_are_shipped(self) -> None:
        if not SHIPPED_BIN_DIR.is_dir():
            self.skipTest("private_zapretgui/dist/bin is not available")
        for name in NEW_FILES:
            for match in re.finditer(r"^--blob=[A-Za-z0-9_]+:@bin/(\S+)$", _read(name), re.MULTILINE):
                with self.subTest(name=name, bin=match.group(1)):
                    self.assertTrue((SHIPPED_BIN_DIR / match.group(1)).is_file())

    def test_every_profile_resolves_to_one_ready_strategy(self) -> None:
        # Ветки --payload одного bat-блока — одна готовая стратегия: profile
        # целиком узнаётся как составная запись каталога.
        for name, preset in self.presets.items():
            for profile in preset.profiles:
                with self.subTest(name=name, profile=profile.display_name):
                    entries = basic_strategy_entries(profile, self.catalogs)
                    strategy_id, _name = resolve_strategy(profile, entries)
                    self.assertNotEqual(strategy_id, "custom")
                    if profile_strategy_shape(profile).composite:
                        self.assertTrue(entries[strategy_id].is_composite)

    def test_strategies_are_payload_scoped_not_generalised(self) -> None:
        for name, preset in self.presets.items():
            with self.subTest(name=name):
                discord = _strategy(_profile(preset, "Discord · сайт и приложение"))
                if "--payload=all" in discord:  # ALT5: syndata must see the empty SYN
                    self.assertEqual(discord[: 2], ("--payload=all", "--lua-desync=syndata"))
                    discord = discord[2:]
                self.assertNotIn("--payload=all", discord)
                self.assertTrue(any(line.startswith("--payload=tls_client_hello") for line in discord))

    def test_game_tcp_branches_follow_the_bat(self) -> None:
        for variant in VARIANTS:
            tcp_cutoff, _udp_cutoff, unknown, _udp_unknown, _reps = GAME[variant]
            name = _name(variant)
            with self.subTest(name=name):
                steam = _strategy(_profile(self.presets[name], "Steam"))
                self.assertEqual(steam[0], f"--out-range=<{tcp_cutoff}")
                unknown_fakes = [
                    line for line in steam
                    if line.startswith("--lua-desync=fake:") and line.endswith(":payload=~empty,tls_client_hello,http_req")
                ]
                self.assertEqual(tuple(_blobs_in(unknown_fakes)), unknown)

    def test_game_tcp_full_branches_for_alt11_and_fake_tls_auto_alt(self) -> None:
        ts = "tcp_ts=-600000:tcp_ts_up"
        other = "payload=~empty,tls_client_hello,http_req"
        self.assertEqual(
            _strategy(_profile(self.presets[_name("ALT11")], "Steam")),
            (
                "--out-range=<n4",
                "--payload=tls_client_hello",
                f"--lua-desync=fake:blob=stun2:{ts}:repeats=8",
                f"--lua-desync=fake:blob=tls_max:{ts}:repeats=8",
                "--payload=http_req",
                f"--lua-desync=fake:blob=tls_max:{ts}:repeats=8",
                "--payload=all",
                f"--lua-desync=fake:blob=stun2:{ts}:repeats=8:{other}",
                f"--lua-desync=fake:blob=tls_max:{ts}:repeats=8:{other}",
                "--lua-desync=multisplit:pos=1:seqovl=664:seqovl_pattern=tls_max:payload=~empty",
            ),
        )
        seq = "tcp_seq=2:tcp_ack=-66000:tcp_ts_up"
        self.assertEqual(
            _strategy(_profile(self.presets[_name("FAKE TLS AUTO ALT")], "Steam")),
            (
                "--out-range=<n3",
                "--payload=tls_client_hello",
                f"--lua-desync=fake:blob=fake_default_tls:tls_mod=rnd,dupsid,sni=www.google.com:{seq}:repeats=8",
                "--payload=http_req",
                f"--lua-desync=fake:blob=tls_max:{seq}:repeats=8",
                "--payload=all",
                f"--lua-desync=fake:blob=tls_max:{seq}:repeats=8:{other}",
                f"--lua-desync=fakedsplit:pos=1:{seq}:repeats=8:payload=~empty",
            ),
        )

    def test_badseq_increments_follow_the_bat(self) -> None:
        expected = {
            "ALT4": 1000, "ALT8": 2, "FAKE TLS AUTO ALT": 2, "SIMPLE FAKE ALT": 2,
            "FAKE TLS AUTO ALT2": 10000000, "FAKE TLS AUTO": -10000,
        }
        for variant in VARIANTS:
            text = _read(_name(variant))
            seqs = set(re.findall(r":tcp_seq=(-?\d+):tcp_ack=-66000:", text))
            with self.subTest(variant=variant):
                if variant in expected:
                    self.assertEqual(seqs, {str(expected[variant])})
                else:
                    self.assertEqual(seqs, set())

    def test_alt9_md5sig_only_on_list_general_profiles(self) -> None:
        preset = self.presets[_name("ALT9")]
        self.assertEqual(
            _lua(_profile(preset, "Discord · сайт и приложение")),
            ["--lua-desync=hostfakesplit:host=ozon.ru:tcp_ts=-600000:tcp_ts_up:tcp_md5:repeats=4"],
        )
        for profile_name in ("OVH TCP", "Steam", "YouTube · сайт и приложение", "Discord · голос и видео (discord.media)"):
            with self.subTest(profile=profile_name):
                self.assertFalse(any("tcp_md5" in line for line in _lua(_profile(preset, profile_name))))
        self.assertEqual(
            _lua(_profile(preset, "OVH TCP")),
            ["--lua-desync=hostfakesplit:host=ozon.ru:tcp_ts=-600000:tcp_ts_up:repeats=4"],
        )

    def test_alt7_syndata_on_ipset_and_game_profiles(self) -> None:
        name = _name("ALT7")
        preset = self.presets[name]
        self.assertIn("--ipcache-hostname=1", _read(name).splitlines())
        self.assertEqual(_strategy(_profile(preset, "OVH TCP")), ("--payload=all", "--lua-desync=syndata"))
        self.assertEqual(
            _strategy(_profile(preset, "Steam")), ("--out-range=<n4", "--payload=all", "--lua-desync=syndata")
        )
        self.assertNotIn("--lua-desync=syndata", _strategy(_profile(preset, "Discord · сайт и приложение")))
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                has_ipcache = "--ipcache-hostname=1" in _read(_name(variant)).splitlines()
                self.assertEqual(has_ipcache, variant in {"ALT5", "ALT7"})

    def test_fooling_and_repeats_only_on_fake_sending_instances(self) -> None:
        for name, preset in self.presets.items():
            for profile in preset.profiles[4:]:  # #1 zapret.moe and #2–#4 messengers are fixed GUI conventions
                for line in _lua(profile):
                    with self.subTest(name=name, profile=profile.display_name, line=line):
                        if re.search(r":(tcp_seq|tcp_ts|tcp_md5)[=:]|:tcp_md5$", line):
                            self.assertIn(":tcp_ts_up", line)
                        if line.startswith(("--lua-desync=multisplit", "--lua-desync=multidisorder_legacy")):
                            self.assertNotRegex(line, r":(repeats|tcp_seq|tcp_ack|tcp_ts|tcp_md5)")

    def test_game_udp_branches_follow_the_bat(self) -> None:
        for variant in VARIANTS:
            _tcp_cutoff, udp_cutoff, _unknown, udp_unknown, reps = GAME[variant]
            name = _name(variant)
            with self.subTest(name=name):
                game = _strategy(_profile(self.presets[name], "Игровые UDP порты"))
                self.assertEqual(
                    game,
                    (
                        f"--out-range=<{udp_cutoff}",
                        "--payload=quic_initial",
                        f"--lua-desync=fake:blob=fake_default_quic:repeats={reps}",
                        "--payload=wireguard_initiation,dht,discord_ip_discovery,stun",
                        f"--lua-desync=fake:blob=fake_zero64:repeats={reps}",
                        "--payload=all",
                        *(
                            f"--lua-desync=fake:blob={blob}:repeats={reps}:payload=~empty,{UDP_KNOWN}"
                            for blob in udp_unknown
                        ),
                    ),
                )

    def test_ipset_profiles_use_regular_block_for_tcp_and_combine_blocks_for_udp(self) -> None:
        for variant in VARIANTS:
            name = _name(variant)
            preset = self.presets[name]
            _tcp_cutoff, udp_cutoff, *_ = GAME[variant]
            with self.subTest(name=name):
                # ipset UDP = ipset-all UDP 443 block (== list-general QUIC block) for QUIC,
                # then the game UDP block with its cutoff for every other payload
                udp = _strategy(_profile(preset, IPSET_UDP_HYBRID))
                quic = _strategy(_profile(preset, "YouTube · быстрый протокол QUIC"))
                self.assertEqual(udp[: len(quic)], quic)
                self.assertEqual(udp[len(quic)], f"--out-range=<{udp_cutoff}")
                # ipset TCP = only the ipset-all TCP 80,443,8443 block: no cutoff, no any-protocol
                for profile_name in (IPSET_TCP_HYBRID, "Telegram", "Мои сайты"):
                    for profile in (p for p in preset.profiles if p.display_name == profile_name):
                        if not any(line.startswith("--ipset=") for line in profile.match.all_lines()):
                            continue
                        tcp = _strategy(profile)
                        self.assertFalse(any(line.startswith("--out-range=") for line in tcp))
                        self.assertFalse(any("payload=~empty" in line for line in tcp))

    def test_alt11_uses_stun2_and_max_ru(self) -> None:
        preset = self.presets[_name("ALT11")]
        text = _read(_name("ALT11"))
        self.assertNotIn("stun_pat", text)
        self.assertEqual(
            _strategy(_profile(preset, "Discord · сайт и приложение")),
            (
                "--payload=tls_client_hello",
                "--lua-desync=fake:blob=stun2:tcp_ts=-600000:tcp_ts_up:repeats=8",
                "--lua-desync=fake:blob=tls_max:tcp_ts=-600000:tcp_ts_up:repeats=8",
                "--payload=http_req",
                "--lua-desync=fake:blob=tls_max:tcp_ts=-600000:tcp_ts_up:repeats=8",
                "--payload=tls_client_hello,http_req",
                "--lua-desync=multisplit:pos=1:seqovl=664:seqovl_pattern=tls_max",
            ),
        )
        self.assertEqual(
            _lua(_profile(preset, "YouTube · сайт и приложение")),
            [
                "--lua-desync=fake:blob=tls_google:tcp_ts=-600000:tcp_ts_up:ip_id=zero:repeats=8",
                "--lua-desync=fake:blob=fake_default_http:tcp_ts=-600000:tcp_ts_up:ip_id=zero:repeats=8",
                "--lua-desync=multisplit:pos=1:seqovl=681:seqovl_pattern=tls_google:ip_id=zero",
            ],
        )

    def test_simple_fake_google_uses_hostfakesplit(self) -> None:
        preset = self.presets[_name("SIMPLE FAKE")]
        for profile_name in ("YouTube · видео (googlevideo.com)", "YouTube · сайт и приложение", "Бэкенд Google"):
            self.assertEqual(
                _strategy(_profile(preset, profile_name)),
                (
                    "--payload=tls_client_hello,http_req",
                    "--lua-desync=hostfakesplit:host=www.google.com:tcp_ts=-600000:tcp_ts_up:ip_id=zero",
                ),
            )
        # 1.10.3 dropped the duplicate stun.bin TLS fake from the general blocks
        self.assertNotIn("stun_pat", _read(_name("SIMPLE FAKE")))
        self.assertEqual(
            _lua(_profile(preset, "Discord · сайт и приложение")),
            [
                "--lua-desync=fake:blob=tls_google:tcp_ts=-600000:tcp_ts_up:repeats=6",
                "--lua-desync=fake:blob=tls_max:tcp_ts=-600000:tcp_ts_up:repeats=6",
            ],
        )

    def test_alt13_uses_sochi_park_and_mail_ru_hostfakesplit(self) -> None:
        name = _name("ALT13")
        preset = self.presets[name]
        text = _read(name)
        self.assertIn("--blob=tls_sochi:@bin/tls_clienthello_sochi_park.bin", text.splitlines())
        self.assertIn("altorder=1", text.splitlines()[4])  # documented mismatch
        self.assertEqual(
            _strategy(_profile(preset, "Discord · сайт и приложение")),
            (
                "--payload=tls_client_hello",
                "--lua-desync=fake:blob=tls_sochi:tcp_ts=-600000:tcp_ts_up:repeats=5",
                "--lua-desync=fake:blob=stun2:tcp_ts=-600000:tcp_ts_up:repeats=5",
                "--payload=http_req",
                "--lua-desync=fake:blob=tls_sochi:tcp_ts=-600000:tcp_ts_up:repeats=5",
                "--payload=tls_client_hello,http_req",
                "--lua-desync=hostfakesplit:host=mail.ru:nofake2:tcp_ts=-600000:tcp_ts_up:repeats=5",
            ),
        )
        # the ALT13 list-google block has no --ip-id=zero upstream
        self.assertEqual(
            _lua(_profile(preset, "YouTube · сайт и приложение")),
            ["--lua-desync=hostfakesplit:host=www.google.com:tcp_ts=-600000:tcp_ts_up"],
        )

    def test_alt3_hostfakesplit_altorder_is_documented(self) -> None:
        text = _read(_name("ALT3"))
        self.assertIn("altorder=1", text.splitlines()[4])
        self.assertIn("--lua-desync=hostfakesplit:host=ya.ru:nofake2:tcp_ts=-600000:tcp_ts_up", text.splitlines())

    def test_exp_discord_voice_has_cutoff_and_unknown_fakes(self) -> None:
        preset = self.presets[_name("EXP")]
        expected = (
            "--out-range=<n4",
            "--payload=discord_ip_discovery",
            "--lua-desync=fake:blob=quic_google:repeats=4",
            "--lua-desync=fake:blob=discord_active:repeats=4",
            "--payload=stun",
            "--lua-desync=fake:blob=discord_active:repeats=4",
            "--payload=unknown",
            "--lua-desync=fake:blob=quic_google:repeats=4:payload=all",
            "--lua-desync=fake:blob=discord_active:repeats=4:payload=all",
        )
        for profile_name in ("Discord · UDP (обычно не нужно)", "Голосовые звонки/чаты"):
            self.assertEqual(_strategy(_profile(preset, profile_name)), expected)
        # non-EXP voice blocks have neither cutoff nor unknown payload
        self.assertEqual(
            _strategy(_profile(self.presets[_name("")], "Голосовые звонки/чаты")),
            ("--payload=discord_ip_discovery,stun", "--lua-desync=fake:blob=discord_active:repeats=6"),
        )

    def test_fake_tls_auto_keeps_both_tls_fakes_and_badseq(self) -> None:
        preset = self.presets[_name("FAKE TLS AUTO")]
        self.assertNotIn("zero4", _read(_name("FAKE TLS AUTO")))
        self.assertEqual(
            _lua(_profile(preset, "Discord · сайт и приложение")),
            [
                "--lua-desync=fake:blob=0x00000000:tcp_seq=-10000:tcp_ack=-66000:tcp_ts_up:repeats=11",
                "--lua-desync=fake:blob=fake_default_tls:tls_mod=rnd,dupsid,sni=www.google.com:"
                "tcp_seq=-10000:tcp_ack=-66000:tcp_ts_up:repeats=11",
                "--lua-desync=fake:blob=tls_max:tcp_seq=-10000:tcp_ack=-66000:tcp_ts_up:repeats=11",
                "--lua-desync=multidisorder_legacy:pos=1,midsld",
            ],
        )

    def test_alt5_syndata_multidisorder(self) -> None:
        name = _name("ALT5")
        preset = self.presets[name]
        self.assertIn("--ipcache-hostname=1", _read(name).splitlines())
        self.assertEqual(
            _strategy(_profile(preset, "Discord · сайт и приложение")),
            (
                "--payload=all",
                "--lua-desync=syndata",
                "--payload=tls_client_hello,http_req",
                "--lua-desync=multidisorder_legacy:pos=2",
            ),
        )
        self.assertEqual(
            _strategy(_profile(preset, "Steam")),
            (
                "--out-range=<n4",
                "--payload=all",
                "--lua-desync=syndata",
                "--lua-desync=multidisorder_legacy:pos=2:payload=~empty",
            ),
        )

    def test_presets_are_classified_into_1103_folder(self) -> None:
        for name in NEW_FILES:
            with self.subTest(name=name):
                self.assertEqual(classify_preset_folder(name, "winws2"), "1-10-3")

    def test_old_ports_still_exist_and_are_unchanged(self) -> None:
        self.assertEqual(len(OLD_PORT_SHA256), 20)
        for name, digest in OLD_PORT_SHA256.items():
            with self.subTest(name=name):
                path = WINWS2_DIR / name
                self.assertTrue(path.is_file())
                self.assertEqual(_masked_sha256(path.read_bytes()), digest)
                self.assertEqual(
                    [line for line in path.read_text(encoding="utf-8").splitlines() if canonical_winws2_lua_init_path(line)],
                    list(WINWS2_LUA_INIT_LINES),
                )


if __name__ == "__main__":
    unittest.main()
