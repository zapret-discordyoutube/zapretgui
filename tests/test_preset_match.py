"""Какой профиль движок берёт для сайта: пресет проходится по правилу dp_match из nfq2/desync.c."""

from __future__ import annotations

import os
import tempfile
import unittest

from blockcheck.preset_match import first_profile_for


def _lists(**files: str) -> str:
    root = tempfile.mkdtemp()
    for name, text in files.items():
        with open(os.path.join(root, name.replace("__", ".")), "w", encoding="utf-8") as target:
            target.write(text)
    return root


PRESET = """--wf-tcp-out=80,443
--new
--name=Исключения айпи (RU сайты)
--filter-tcp=80,443-65535
--ipset=lists/ipset-ru.txt
--lua-desync=pass
--new
--name=Общий
--filter-tcp=443
--hostlist=lists/general.txt
--lua-desync=fake:blob=fake_default_tls
--new
--filter-tcp=443
--hostlist-domains=castingcall.club
--lua-desync=multisplit
--new
--name=Все сайты (айпи)
--filter-tcp=80,443-65535
--ipset-exclude=lists/ipset-ru.txt
--lua-desync=fake
"""


class PresetMatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lists = _lists(**{"ipset-ru__txt": "77.88.0.0/18\n", "general__txt": "club\n!example.club\n"})

    def test_profile_above_that_catches_the_site_is_named_with_the_reason(self) -> None:
        hit = first_profile_for(PRESET, "www.castingcall.club", ["104.21.32.151"], lists_dir=self.lists)
        self.assertEqual((hit.number, hit.title, hit.own), (2, "Общий", False))
        self.assertIn("www.castingcall.club есть в его списке general.txt", hit.reason)

    def test_exception_in_the_list_lets_the_site_reach_its_own_profile(self) -> None:
        lists = _lists(**{"ipset-ru__txt": "77.88.0.0/18\n", "general__txt": "club\n!castingcall.club\n"})
        hit = first_profile_for(PRESET, "www.castingcall.club", ["104.21.32.151"], lists_dir=lists)
        self.assertEqual(hit.number, 3)
        self.assertTrue(hit.own)

    def test_address_lists_include_and_exclude(self) -> None:
        # Российский адрес — в первом профиле по списку адресов; «Все сайты (айпи)» его исключает.
        hit = first_profile_for(PRESET, "ya.ru", ["77.88.21.3"], lists_dir=self.lists)
        self.assertEqual((hit.number, hit.reason), (1, "адрес 77.88.21.3 есть в его списке адресов"))
        hit = first_profile_for(PRESET.replace("--ipset=lists/ipset-ru.txt", "--ipset=lists/none.txt"), "ya.ru", ["77.88.21.3"], lists_dir=self.lists)
        self.assertIsNone(hit)

    def test_profile_without_lists_takes_everything_except_its_exclusions(self) -> None:
        hit = first_profile_for(PRESET, "discord.com", ["162.159.137.232"], lists_dir=self.lists)
        self.assertEqual(hit.number, 4)
        self.assertIn("кроме адресов из своих исключений", hit.reason)

    def test_disabled_udp_and_l7_profiles_are_skipped(self) -> None:
        preset = """--new
--name=Выключен
--skip
--filter-tcp=443
--lua-desync=fake
--new
--name=QUIC
--filter-udp=443
--lua-desync=fake
--new
--name=Только QUIC по L7
--filter-l7=quic
--lua-desync=fake
--new
--name=Последний
--lua-desync=fake
"""
        hit = first_profile_for(preset, "x.com", ["1.1.1.1"], lists_dir=self.lists)
        self.assertEqual((hit.number, hit.title), (4, "Последний"))

    def test_auto_hostlist_takes_the_site_once_the_name_is_known(self) -> None:
        preset = "--new\n--name=Авто\n--filter-tcp=443\n--hostlist-auto=lists/autohostlist.txt\n--lua-desync=fake\n"
        hit = first_profile_for(preset, "x.com", [], lists_dir=self.lists)
        self.assertIn("автосписок", hit.reason)


if __name__ == "__main__":
    unittest.main()
