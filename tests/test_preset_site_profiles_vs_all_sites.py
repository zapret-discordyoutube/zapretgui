"""Профили отдельных сайтов не нужны в пресетах, где уже есть профиль на все сайты TCP.

Решение владельца от 2026-11-04 (повторено 2026-11-21, когда профиль vencord.dev попал в 43 таких пресета):
новый профиль сайта кладут во все встроенные пресеты, КРОМЕ тех, где есть профиль на все сайты
(«Все сайты (айпи)», «Все сайты (хостлисты)» или любой другой профиль TCP на порт 443 без ``--hostlist=`` и
``--ipset=``). Там отдельный профиль сайта только мешает профилю на все сайты и кругу подбора.

Когда добавляете профиль сайта по просьбе владельца, впишите его имя в ``NEW_SITE_PROFILES`` ниже: тогда этот
тест сам не даст положить его в пресет с профилем на все сайты. Правило подробно: AGENTS.md («New site profiles»)
и ``.codex/skills/zapretgui-profile-preset-rules/SKILL.md``.
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest

from profile.parser import parse_preset_text

# Имена профилей, добавленных «везде, кроме пресетов с профилем на все сайты». Новое имя дописывайте сюда.
NEW_SITE_PROFILES = ("zapret.moe", "Signal", "Matrix", "SimpleX", "vencord.dev")

_SITE_LIST_LINE = re.compile(r"^--(hostlist=|ipset=|hostlist-domains=)")

_HOW_TO_FIX = (
    "Профиль сайта не кладут в пресет, где уже есть профиль на все сайты TCP (порт 443 без --hostlist=/--ipset=): "
    "он только мешает ему и кругу подбора. Уберите профиль из этих пресетов и поднимите у них "
    "# BuiltinVersion (см. AGENTS.md, «New site profiles»)."
)


def _covers_port_443(filter_line: str) -> bool:
    if not filter_line.startswith("--filter-tcp="):
        return False
    for part in filter_line.split("=", 1)[1].split(","):
        found = re.fullmatch(r"(\d+)(?:-(\d+))?", part.strip().lstrip("~"))
        if found and int(found.group(1)) <= 443 <= int(found.group(2) or found.group(1)):
            return True
    return False


def has_all_sites_tcp_profile(preset) -> bool:
    for profile in preset.profiles:
        lines = [str(line).strip() for line in profile.match.all_lines()]
        if any(_SITE_LIST_LINE.match(line) for line in lines):
            continue
        if any(_covers_port_443(line) for line in lines):
            return True
    return False


class SiteProfilesVersusAllSitesTests(unittest.TestCase):
    def test_site_profiles_are_absent_where_an_all_sites_tcp_profile_exists(self) -> None:
        offenders: dict[str, list[str]] = {}
        checked = 0
        for path in sorted(Path("src/presets/builtin/winws2").glob("*.txt")):
            preset = parse_preset_text(path.read_text(encoding="utf-8"), engine="winws2", source_name=path.name)
            if not has_all_sites_tcp_profile(preset):
                continue
            checked += 1
            present = [
                name
                for name in NEW_SITE_PROFILES
                if any(profile.display_name == name for profile in preset.profiles)
            ]
            if present:
                offenders[path.name] = present

        self.assertGreater(checked, 0, "не нашлось ни одного пресета с профилем на все сайты: разбор сломан?")
        self.assertEqual(offenders, {}, _HOW_TO_FIX)

    def test_all_sites_detection_ignores_profiles_with_lists(self) -> None:
        text = "\n".join(
            [
                "# Preset: probe",
                "# BuiltinVersion: 1.0",
                "",
                "--name=Сайт",
                "--filter-tcp=80,443",
                "--hostlist=lists/probe.txt",
                "--lua-desync=pass",
                "",
                "--new",
                "",
                "--name=Порт 80",
                "--filter-tcp=80",
                "--lua-desync=pass",
            ]
        )
        preset = parse_preset_text(text, engine="winws2", source_name="probe.txt")
        self.assertFalse(has_all_sites_tcp_profile(preset))

        text += "\n\n--new\n\n--name=Все сайты (айпи)\n--filter-tcp=80,443-65535\n--lua-desync=pass\n"
        preset = parse_preset_text(text, engine="winws2", source_name="probe.txt")
        self.assertTrue(has_all_sites_tcp_profile(preset))


if __name__ == "__main__":
    unittest.main()
