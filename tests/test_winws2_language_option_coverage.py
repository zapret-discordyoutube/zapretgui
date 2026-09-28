"""Справочник опций редактора покрывает ВСЕ опции оригинального winws2.

Список сверяется с массивом ``long_options[]`` из исходников zapret2
(``nfq2/nfqws.c``) вместе с блоками ``#ifdef``: опции под ``__CYGWIN__``
есть только в Windows-сборке, под ``__linux__``/``BSD`` — только в других.
Если исходников zapret2 рядом нет (например, в CI), сверка пропускается,
а остальные проверки справочника выполняются всегда.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys
import unittest

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

ZAPRET2_NFQWS = Path("/home/codex-pve/zapret2/nfq2/nfqws.c")

_OPTION_RE = re.compile(r'\{"([a-z0-9-]+)",\s*(no_argument|required_argument|optional_argument)')
_ARG_KIND = {"no_argument": "none", "required_argument": "required", "optional_argument": "optional"}


def _platform_for(condition: str) -> str:
    text = condition.strip()
    if "__CYGWIN__" in text:
        return "unix" if text.startswith("#ifndef") else "windows"
    if "__linux__" in text or "HAS_FILTER_SSID" in text:
        return "linux"
    if "BSD" in text or "SO_USER_COOKIE" in text:
        return "bsd"
    return "all"


def parse_long_options(source: str) -> dict[str, tuple[str, str]]:
    """{имя: (вид значения, платформа)} из массива long_options[]."""
    start = source.index("static const struct option long_options[]")
    end = source.index("};", start)
    options: dict[str, tuple[str, str]] = {}
    stack: list[str] = []
    for raw in source[start:end].splitlines():
        line = raw.strip()
        if line.startswith(("#ifdef", "#ifndef", "#if ")):
            stack.append(_platform_for(line))
            continue
        if line.startswith("#elif"):
            stack[-1] = _platform_for(line)
            continue
        if line.startswith("#endif"):
            stack.pop()
            continue
        match = _OPTION_RE.search(line)
        if match:
            platform = next((item for item in reversed(stack) if item != "all"), "all")
            options[match.group(1)] = (_ARG_KIND[match.group(2)], platform)
    return options


class Winws2OptionCatalogTests(unittest.TestCase):
    def test_every_option_has_description_and_valid_fields(self) -> None:
        from profile.winws2_language.options import WINWS2_OPTIONS

        for spec in WINWS2_OPTIONS:
            with self.subTest(option=spec.name):
                self.assertTrue(spec.summary.strip())
                self.assertIn(spec.arg, {"none", "required", "optional"})
                self.assertIn(spec.scope, {"global", "profile", "any"})
                self.assertIn(spec.platform, {"all", "windows", "linux", "bsd", "unix"})
                self.assertIn(spec.repeat, {"accumulate", "last", "once"})

    def test_prefix_resolution_matches_getopt(self) -> None:
        from profile.winws2_language.options import resolve_windows_option

        spec, _ = resolve_windows_option("hostlist-exclude-d")
        self.assertEqual(spec.name, "hostlist-exclude-domains")
        spec, candidates = resolve_windows_option("hostlist-e")
        self.assertIsNone(spec)
        self.assertEqual(
            {c.name for c in candidates}, {"hostlist-exclude", "hostlist-exclude-domains"}
        )
        spec, _ = resolve_windows_option("hostlist")
        self.assertEqual(spec.name, "hostlist")
        spec, candidates = resolve_windows_option("qnum")
        self.assertIsNone(spec)
        self.assertEqual(candidates, ())

    @unittest.skipUnless(ZAPRET2_NFQWS.is_file(), "нет исходников zapret2")
    def test_catalog_matches_original_long_options(self) -> None:
        from profile.winws2_language.options import OPTIONS_BY_NAME

        original = parse_long_options(ZAPRET2_NFQWS.read_text(encoding="utf-8", errors="replace"))
        self.assertGreater(len(original), 80)
        self.assertEqual(set(OPTIONS_BY_NAME), set(original))
        for name, (arg, platform) in original.items():
            spec = OPTIONS_BY_NAME[name]
            with self.subTest(option=name):
                self.assertEqual(spec.arg, arg)
                available = platform in {"all", "windows"}
                self.assertEqual(spec.available_on_windows, available)
                if platform == "windows":
                    self.assertEqual(spec.platform, "windows")


if __name__ == "__main__":
    unittest.main()
