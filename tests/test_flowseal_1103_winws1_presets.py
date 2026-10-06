"""Builtin winws1 presets imported from Flowseal zapret-discord-youtube 1.10.3.

The 1.10.3 files are derived from the exact 1.9.9a / EXP 1.10.0 ports and
carry only the upstream bat changes (ef19845/9503dc0 -> 865da4f). The old
ports must stay untouched next to them.
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import unittest

from folders.defaults import classify_preset_folder
from profile.parser import parse_preset_text


PUBLIC_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = PUBLIC_ROOT.parent / "private_zapretgui"
WINWS1_DIR = PUBLIC_ROOT / "src" / "presets" / "builtin" / "winws1"
SHIPPED_BIN_DIR = PRIVATE_ROOT / "dist" / "bin"

_V = "1.10.3"


def _name(variant: str) -> str:
    prefix = f"general {variant} " if variant else "general "
    return f"{prefix}{_V} (game filter).txt"


# variant -> fake-unknown lines expected in EVERY split game TCP block
# (--filter-tcp=1024-65535 + --ipset). Taken from the 1.10.3 bats (#15640).
GAME_TCP_FAKE_UNKNOWN: dict[str, tuple[str, ...]] = {
    "": (),
    "ALT1": ("bin/stun.bin", "bin/tls_clienthello_www_google_com.bin"),
    "ALT2": (),
    "ALT3": ("bin/tls_clienthello_max_ru.bin",),
    "ALT4": ("bin/stun.bin", "bin/tls_clienthello_www_google_com.bin"),
    "ALT5": (),
    "ALT6": (),
    "ALT7": (),
    "ALT8": ("bin/tls_clienthello_max_ru.bin",),
    "ALT9": (),
    "ALT10": ("bin/stun.bin", "bin/tls_clienthello_4pda_to.bin"),
    "ALT11": ("bin/stun2.bin", "bin/tls_clienthello_max_ru.bin"),
    "ALT12": ("bin/stun.bin", "bin/tls_clienthello_max_ru.bin"),
    "ALT13": ("bin/tls_clienthello_sochi_park.bin", "bin/stun2.bin"),
    "FAKE TLS AUTO": ("0x00000000", "bin/tls_clienthello_max_ru.bin"),
    "FAKE TLS AUTO ALT": ("bin/tls_clienthello_max_ru.bin",),
    "FAKE TLS AUTO ALT2": ("bin/tls_clienthello_max_ru.bin",),
    "FAKE TLS AUTO ALT3": ("bin/tls_clienthello_max_ru.bin",),
    "SIMPLE FAKE": ("bin/stun2.bin", "bin/tls_clienthello_www_google_com.bin"),
    "SIMPLE FAKE ALT": ("bin/stun2.bin", "bin/tls_clienthello_www_google_com.bin"),
    "SIMPLE FAKE ALT2": ("bin/stun2.bin", "bin/tls_clienthello_max_ru.bin"),
    "EXP": (),
}
VARIANTS = tuple(GAME_TCP_FAKE_UNKNOWN)
NEW_FILES = tuple(_name(variant) for variant in VARIANTS)
# 1.10.3 switched these fakes to stun2.bin; stun.bin must not remain anywhere.
STUN2_ONLY_VARIANTS = {"ALT11", "ALT13", "SIMPLE FAKE", "SIMPLE FAKE ALT", "SIMPLE FAKE ALT2"}

OLD_PORTS = tuple(
    f"general {variant + ' ' if variant else ''}1.9.9a (game filter).txt"
    for variant in VARIANTS
    if variant not in {"EXP", "ALT13"}
) + ("general EXP 1.10.0 (game filter).txt",)

_BATCH_SYNTAX = re.compile(r"%[A-Za-z_]+%|\^|\"|(?:^|\s)start(?:\s|$)|call service\.bat|winws\.exe", re.MULTILINE)


def _read(name: str) -> str:
    return (WINWS1_DIR / name).read_text(encoding="utf-8")


def _blocks(text: str) -> list[list[str]]:
    return [
        [line.strip() for line in block.splitlines() if line.strip() and not line.startswith("#")]
        for block in text.split("\n--new\n")
    ]


def _is_game_block(block: list[str], proto: str) -> bool:
    return f"--filter-{proto}=1024-65535" in block and any(line.startswith("--ipset=") for line in block)


class Flowseal1103Winws1PresetTests(unittest.TestCase):
    def test_all_22_presets_exist(self) -> None:
        self.assertEqual(len(NEW_FILES), 22)
        present = sorted(path.name for path in WINWS1_DIR.glob(f"*{_V}*.txt"))
        self.assertEqual(present, sorted(NEW_FILES))

    def test_headers(self) -> None:
        for name in NEW_FILES:
            with self.subTest(name=name):
                lines = _read(name).splitlines()
                header = [line for line in lines if line.startswith("# ")]
                self.assertEqual(lines[0], f"# Preset: {name[:-4]}")
                # Номер не ниже того, с которым набор добавлен: каждая правка
                # пресета поднимает его на шаг.
                version_line = next(line for line in header if line.startswith("# BuiltinVersion: "))
                version = re.fullmatch(r"# BuiltinVersion: (\d+)\.(\d+)", version_line)
                self.assertIsNotNone(version, version_line)
                self.assertGreaterEqual((int(version.group(1)), int(version.group(2))), (1, 0))
                self.assertTrue(any(line.startswith("# IconColor: #") for line in header))
                description = next(line for line in header if line.startswith("# Description: "))
                self.assertIn(f"zapret-discord-youtube {_V}", description)

    def test_no_batch_syntax_and_no_aggregate_or_user_lists(self) -> None:
        for name in NEW_FILES:
            with self.subTest(name=name):
                text = _read(name)
                self.assertIsNone(_BATCH_SYNTAX.search(text))
                self.assertNotIn("\r", text)
                self.assertNotIn("-user.txt", text)
                self.assertNotIn("list-general", text)
                self.assertNotIn("ipset-all.txt", text)
                self.assertNotIn("--name=", text)
                self.assertTrue(text.endswith("\n"))

    def test_presets_parse_as_winws1_with_one_profile_per_block(self) -> None:
        for name in NEW_FILES:
            with self.subTest(name=name):
                text = _read(name)
                preset = parse_preset_text(text, engine="winws1", source_name=name)
                self.assertEqual(len(preset.profiles), len(_blocks(text)))

    def test_discord_voice_udp_uses_active_discord_fake(self) -> None:
        for name in NEW_FILES:
            with self.subTest(name=name):
                text = _read(name)
                self.assertNotIn("quic_initial_dbankcloud_ru.bin", text)
                voice = [b for b in _blocks(text) if "--filter-udp=19294-19344,50000-50100" in b]
                self.assertEqual(len(voice), 1)
                self.assertIn("--dpi-desync-fake-discord=bin/ACTIVE_DISCORD_UDP.bin", voice[0])

    def test_game_udp_blocks_use_new_fakes(self) -> None:
        for variant, name in zip(VARIANTS, NEW_FILES):
            with self.subTest(name=name):
                game_udp = [b for b in _blocks(_read(name)) if _is_game_block(b, "udp")]
                self.assertGreaterEqual(len(game_udp), 5)
                for block in game_udp:
                    fakes = [line for line in block if line.startswith("--dpi-desync-fake-unknown-udp=")]
                    if variant == "EXP":
                        self.assertEqual(
                            fakes,
                            [
                                "--dpi-desync-fake-unknown-udp=bin/quic_initial_4pda_to.bin",
                                "--dpi-desync-fake-unknown-udp=bin/ACTIVE_GAME_UDP.bin",
                            ],
                        )
                    else:
                        self.assertEqual(fakes, ["--dpi-desync-fake-unknown-udp=bin/ACTIVE_GAME_UDP.bin"])

    def test_every_split_game_tcp_block_has_upstream_fake_unknown(self) -> None:
        for variant, name in zip(VARIANTS, NEW_FILES):
            with self.subTest(name=name):
                game_tcp = [b for b in _blocks(_read(name)) if _is_game_block(b, "tcp")]
                self.assertGreaterEqual(len(game_tcp), 5)
                expected = [f"--dpi-desync-fake-unknown={value}" for value in GAME_TCP_FAKE_UNKNOWN[variant]]
                for block in game_tcp:
                    self.assertEqual(
                        [line for line in block if line.startswith("--dpi-desync-fake-unknown=")],
                        expected,
                    )

    def test_stun2_replaces_stun_in_variants_changed_upstream(self) -> None:
        for variant in STUN2_ONLY_VARIANTS:
            with self.subTest(variant=variant):
                text = _read(_name(variant))
                self.assertNotIn("bin/stun.bin", text)
                self.assertIn("bin/stun2.bin", text)

    def test_simple_fake_google_block_uses_hostfakesplit(self) -> None:
        blocks = _blocks(_read(_name("SIMPLE FAKE")))
        google = [b for b in blocks if "--hostlist=lists/list-google.txt" in b]
        self.assertEqual(len(google), 1)
        self.assertEqual(
            [line for line in google[0] if line.startswith("--dpi-desync")],
            [
                "--dpi-desync=hostfakesplit",
                "--dpi-desync-fooling=ts",
                "--dpi-desync-hostfakesplit-mod=host=www.google.com",
            ],
        )
        # the duplicate fake-tls=stun.bin was removed from the general/ipset blocks
        self.assertNotIn("--dpi-desync-fake-tls=bin/stun.bin", _read(_name("SIMPLE FAKE")))

    def test_alt13_uses_sochi_park_hostfakesplit(self) -> None:
        text = _read(_name("ALT13"))
        self.assertIn("--dpi-desync=fake,hostfakesplit", text)
        self.assertIn("--dpi-desync-hostfakesplit-mod=host=mail.ru,altorder=1", text)
        self.assertIn("--dpi-desync-fake-tls=bin/tls_clienthello_sochi_park.bin", text)
        self.assertNotIn("--ip-id=zero", text)

    def test_exp_discord_udp_has_any_protocol_and_cutoff(self) -> None:
        text = _read(_name("EXP"))
        voice = [b for b in _blocks(text) if "--filter-udp=19294-19344,50000-50100" in b]
        self.assertEqual(len(voice), 1)
        self.assertIn("--filter-l7=discord,stun,unknown", voice[0])
        self.assertIn("--dpi-desync-any-protocol=1", voice[0])
        self.assertIn("--dpi-desync-cutoff=n4", voice[0])
        self.assertNotIn("quic_initial_4pda.to.bin", text)
        self.assertIn("bin/quic_initial_4pda_to.bin", text)
        # intentional adaptation kept from EXP 1.10.0: QUIC hostlist blocks narrowed to udp/443
        quic_hostlist = [
            b for b in _blocks(text)
            if "--filter-l7=quic" in b and any(line.startswith("--hostlist=") for line in b)
        ]
        self.assertEqual(len(quic_hostlist), 13)
        self.assertTrue(all("--filter-udp=443" in b for b in quic_hostlist))

    def test_referenced_bins_are_shipped(self) -> None:
        if not SHIPPED_BIN_DIR.is_dir():
            self.skipTest("private_zapretgui/dist/bin is not available")
        for name in NEW_FILES:
            for match in re.finditer(r"=(bin/[^\s,]+)", _read(name)):
                with self.subTest(name=name, bin=match.group(1)):
                    self.assertTrue((SHIPPED_BIN_DIR / match.group(1)[4:]).is_file())

    def test_presets_are_classified_into_1103_folder(self) -> None:
        for name in NEW_FILES:
            with self.subTest(name=name):
                self.assertEqual(classify_preset_folder(name, "winws1"), "1-10-3")

    def test_old_ports_still_exist_and_are_unchanged(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not available")
        for name in OLD_PORTS:
            with self.subTest(name=name):
                path = WINWS1_DIR / name
                self.assertTrue(path.is_file())
                rel = path.relative_to(PUBLIC_ROOT).as_posix()
                result = subprocess.run(
                    ["git", "-C", str(PUBLIC_ROOT), "show", f"HEAD:{rel}"],
                    capture_output=True,
                    check=False,
                )
                if result.returncode != 0:
                    self.skipTest("git HEAD is not available")
                self.assertEqual(path.read_bytes(), result.stdout)


if __name__ == "__main__":
    unittest.main()
