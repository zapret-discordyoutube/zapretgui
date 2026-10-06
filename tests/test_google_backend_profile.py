"""Профиль «Бэкенд Google» (fonts/gstatic/ajax.googleapis) во встроенных winws2 preset-ах.

Сайты YouTube тянут шрифты, скрипты и статику с общих доменов Google. Эти
домены вынесены в отдельный список lists/google-backend.txt и отдельный
profile «Бэкенд Google», который в каждом встроенном winws2 preset-е с
«YouTube · сайт и приложение» повторяет его стратегию и стоит до RU-исключений.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from profile.parser import parse_preset_text


PUBLIC_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = PUBLIC_ROOT.parent / "private_zapretgui"
ALL_PROFILES_PATH = PRIVATE_ROOT / "resources" / "profile" / "templates" / "all_profiles.txt"
GOOGLE_BACKEND_LIST_PATH = PRIVATE_ROOT / "dist" / "lists" / "google-backend.txt"
WINWS2_BUILTIN_DIR = PUBLIC_ROOT / "src" / "presets" / "builtin" / "winws2"

BACKEND_NAME = "Бэкенд Google"
YOUTUBE_INTERFACE_NAME = "YouTube · сайт и приложение"
RU_EXCLUSION_NAMES = frozenset({"Исключения домены (RU сайты)", "Исключения айпи (RU сайты)"})
BACKEND_MATCH_LINES = ["--filter-tcp=80,443", "--hostlist=lists/google-backend.txt"]
TLS_GOOGLE_BLOB_LINE = "--blob=tls_google:@bin/tls_clienthello_www_google_com.bin"


def _parse(path: Path):
    return parse_preset_text(path.read_text(encoding="utf-8"), engine="winws2", source_name=path.name)


def _match_lines(profile) -> list[str]:
    return [segment.text for segment in profile.segments if segment.kind == "match"]


def _named(preset, name: str) -> list:
    return [profile for profile in preset.profiles if str(profile.name or "").strip() == name]


def _builtin_presets() -> list[Path]:
    paths = sorted(WINWS2_BUILTIN_DIR.glob("*.txt"))
    if not paths:
        raise AssertionError(f"Нет встроенных winws2 preset-ов в {WINWS2_BUILTIN_DIR}")
    return paths


class GoogleBackendPrivateResourcesTests(unittest.TestCase):
    def setUp(self) -> None:
        if not ALL_PROFILES_PATH.is_file():
            self.skipTest(f"Нет private-репозитория: {ALL_PROFILES_PATH}")

    def test_shipped_list_has_exact_backend_domains(self) -> None:
        self.assertTrue(GOOGLE_BACKEND_LIST_PATH.is_file(), GOOGLE_BACKEND_LIST_PATH)
        entries = [
            line.strip()
            for line in GOOGLE_BACKEND_LIST_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

        self.assertEqual(
            entries,
            ["fonts.googleapis.com", "fonts.gstatic.com", "www.gstatic.com", "ajax.googleapis.com"],
        )

    def test_all_profiles_has_backend_template_without_strategy(self) -> None:
        catalog = _parse(ALL_PROFILES_PATH)
        profiles = _named(catalog, BACKEND_NAME)

        self.assertEqual(len(profiles), 1)
        self.assertTrue(profiles[0].enabled)
        self.assertEqual(_match_lines(profiles[0]), BACKEND_MATCH_LINES)
        self.assertEqual(profiles[0].strategy.strategy_lines, [])


class GoogleBackendBuiltinPresetTests(unittest.TestCase):
    def test_youtube_presets_have_single_backend_profile_with_youtube_strategy(self) -> None:
        offenders: list[str] = []
        checked = 0

        for path in _builtin_presets():
            preset = _parse(path)
            youtube_profiles = _named(preset, YOUTUBE_INTERFACE_NAME)
            if not youtube_profiles:
                continue
            checked += 1
            youtube = youtube_profiles[0]
            backend_profiles = _named(preset, BACKEND_NAME)
            if len(backend_profiles) != 1:
                offenders.append(f"{path.name}: {len(backend_profiles)} profile «{BACKEND_NAME}» вместо 1")
                continue
            backend = backend_profiles[0]

            if not backend.enabled:
                offenders.append(f"{path.name}: «{BACKEND_NAME}» выключен")
            if _match_lines(backend) != BACKEND_MATCH_LINES:
                offenders.append(f"{path.name}: условия «{BACKEND_NAME}» {_match_lines(backend)}")
            if not backend.strategy.strategy_lines:
                offenders.append(f"{path.name}: у «{BACKEND_NAME}» нет стратегии")
            if backend.strategy.strategy_lines != youtube.strategy.strategy_lines:
                offenders.append(f"{path.name}: стратегия «{BACKEND_NAME}» не совпадает с {YOUTUBE_INTERFACE_NAME}")
            if backend.index <= youtube.index:
                offenders.append(f"{path.name}: «{BACKEND_NAME}» стоит раньше {YOUTUBE_INTERFACE_NAME}")

            ru_indexes = [p.index for p in preset.profiles if str(p.name or "").strip() in RU_EXCLUSION_NAMES]
            if not ru_indexes:
                offenders.append(f"{path.name}: нет RU-исключений для проверки порядка")
            elif backend.index >= min(ru_indexes):
                offenders.append(f"{path.name}: «{BACKEND_NAME}» стоит после RU-исключений")

        self.assertGreater(checked, 0)
        self.assertEqual(offenders, [])

    def test_presets_without_youtube_interface_have_no_backend_profile(self) -> None:
        offenders: list[str] = []
        checked = 0
        for path in _builtin_presets():
            preset = _parse(path)
            if _named(preset, YOUTUBE_INTERFACE_NAME):
                continue
            checked += 1
            if _named(preset, BACKEND_NAME):
                offenders.append(path.name)

        self.assertGreater(checked, 0)
        self.assertEqual(offenders, [])

    def test_every_builtin_preset_declares_tls_google_blob(self) -> None:
        # Готовые стратегии каталога ссылаются на blob tls_google: без объявления
        # в шапке preset-а выбор такой стратегии ломает запуск winws2.
        offenders = [
            path.name
            for path in _builtin_presets()
            if TLS_GOOGLE_BLOB_LINE not in path.read_text(encoding="utf-8").splitlines()
        ]

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
