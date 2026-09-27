"""Выбор каталога готовых стратегий по фильтрам profile с учётом --filter-l7.

Раньше любой `--filter-l7` отправлял profile в UDP-каталог, даже когда там
только TCP-протоколы (`tls`, `http`). Теперь транспорт L7 решает один общий
помощник `l7_transport`, которым пользуются и каталоги, и пользовательские
L7-profile.
"""

from __future__ import annotations

from pathlib import Path
import unittest

from profile.match_filters import (
    _parse_ports,
    filter_values,
    is_http80_match,
    is_voice_match,
    l7_transport,
    strategy_catalog_from_match_lines,
)
from profile.parser import parse_preset_text

PUBLIC_ROOT = Path(__file__).resolve().parents[1]
BUILTIN_PRESETS_ROOT = PUBLIC_ROOT / "src" / "presets" / "builtin"
ALL_PROFILES_PATH = (
    PUBLIC_ROOT.parent / "private_zapretgui" / "resources" / "profile" / "templates" / "all_profiles.txt"
)


def _legacy_strategy_catalog(match_lines: tuple[str, ...]) -> str:
    """Старое правило (до l7_transport): любой --filter-l7 -> udp."""

    def legacy_http80() -> bool:
        tcp_values = filter_values(match_lines, "--filter-tcp")
        if not tcp_values or filter_values(match_lines, "--filter-udp") or filter_values(match_lines, "--filter-l7"):
            return False
        ports = _parse_ports(",".join(tcp_values))
        return bool(ports) and ports == {80}

    if is_voice_match(match_lines):
        return "voice"
    if filter_values(match_lines, "--filter-l7"):
        return "udp"
    if filter_values(match_lines, "--filter-udp") and not filter_values(match_lines, "--filter-tcp"):
        return "udp"
    if legacy_http80():
        return "http80"
    return "tcp"


def _shipped_profiles():
    sources: list[tuple[Path, str]] = []
    for engine in ("winws1", "winws2"):
        sources.extend((path, engine) for path in sorted((BUILTIN_PRESETS_ROOT / engine).glob("*.txt")))
    if ALL_PROFILES_PATH.is_file():
        sources.append((ALL_PROFILES_PATH, "winws2"))
    for path, engine in sources:
        preset = parse_preset_text(path.read_text(encoding="utf-8"), engine=engine, source_name=path.name)
        for profile in preset.profiles:
            yield f"{engine}/{path.name}:{profile.display_name}", tuple(profile.match.all_lines())


class L7TransportTest(unittest.TestCase):
    def test_transport_by_l7_names(self) -> None:
        cases = [
            ((), None),
            (("",), None),
            (("tls",), "tcp"),
            (("http,tls",), "tcp"),
            (("HTTP", " tls "), "tcp"),
            (("xmpp,mtproto,bt",), "tcp"),
            (("quic",), "udp"),
            (("tls,quic",), "udp"),
            (("stun,discord",), "udp"),
            (("unknown",), "udp"),
        ]
        for values, expected in cases:
            with self.subTest(values=values):
                self.assertEqual(l7_transport(values), expected)


class StrategyCatalogFromMatchLinesTest(unittest.TestCase):
    def test_catalog_table(self) -> None:
        cases = [
            (("--filter-tcp=443", "--filter-l7=tls"), "tcp"),
            (("--filter-tcp=80", "--filter-l7=http"), "http80"),
            (("--filter-tcp=80,443", "--filter-l7=http,tls"), "tcp"),
            (("--filter-l7=tls",), "tcp"),
            (("--filter-l7=http",), "tcp"),
            (("--filter-l7=quic",), "udp"),
            # Только TCP-порты: L7 не переводит profile в UDP-каталог.
            (("--filter-tcp=443", "--filter-l7=quic"), "tcp"),
            (("--filter-udp=443", "--filter-l7=quic"), "udp"),
            (("--filter-tcp=443", "--filter-udp=443", "--filter-l7=quic"), "udp"),
            (("--filter-tcp=443", "--filter-udp=443", "--filter-l7=tls,quic"), "udp"),
            (("--filter-tcp=443", "--filter-udp=443", "--filter-l7=tls"), "tcp"),
            (("--filter-l7=unknown",), "udp"),
            (("--filter-l7=stun,discord",), "voice"),
            (("--filter-udp=443",), "udp"),
            (("--filter-tcp=80",), "http80"),
            (("--filter-tcp=80,443",), "tcp"),
            (("--filter-tcp=443", "--filter-udp=443"), "tcp"),
            (("--hostlist=lists/x.txt",), "tcp"),
        ]
        for match_lines, expected in cases:
            with self.subTest(match_lines=match_lines):
                self.assertEqual(strategy_catalog_from_match_lines(match_lines), expected)

    def test_http80_accepts_only_tcp_l7(self) -> None:
        self.assertTrue(is_http80_match(("--filter-tcp=80",)))
        self.assertTrue(is_http80_match(("--filter-tcp=80", "--filter-l7=http")))
        self.assertFalse(is_http80_match(("--filter-tcp=80", "--filter-l7=quic")))
        self.assertFalse(is_http80_match(("--filter-tcp=80", "--filter-l7=http,quic")))
        self.assertFalse(is_http80_match(("--filter-tcp=80", "--filter-udp=80")))
        self.assertFalse(is_http80_match(("--filter-tcp=80,443", "--filter-l7=http")))

    def test_shipped_profiles_keep_their_catalog(self) -> None:
        """Ни один поставляемый profile не должен сменить каталог из-за нового правила."""
        checked = 0
        changed: list[str] = []
        for source, match_lines in _shipped_profiles():
            checked += 1
            old = _legacy_strategy_catalog(match_lines)
            new = strategy_catalog_from_match_lines(match_lines)
            if old != new:
                changed.append(f"{source}: {old} -> {new}")
        self.assertGreater(checked, 0)
        self.assertEqual(changed, [])


if __name__ == "__main__":
    unittest.main()
