"""Блок «Кэши YouTube» в hosts: закрывает только группы узлов провайдера, чужие строки не трогает, убирается целиком."""

from __future__ import annotations

import unittest

from hosts import youtube_cache_block as block
from hosts.hosts_blocks import BLOCK_USER, BLOCK_YOUTUBE_CACHE, parse_hosts_blocks

_USER = "# мои записи\n127.0.0.1 localhost\n10.0.0.5 nas.home\n"


class _File:
    def __init__(self, text: str) -> None:
        self.text = text

    def read(self) -> str:
        return self.text

    def write(self, text: str) -> bool:
        self.text = text
        return True


class NamesTests(unittest.TestCase):
    def test_group_turns_into_every_machine_name(self) -> None:
        names = block.names_for_group("sn-xguxaxjvh-304e")
        self.assertIn("rr1---sn-xguxaxjvh-304e.googlevideo.com", names)
        self.assertIn("rr20---sn-xguxaxjvh-304e.googlevideo.com", names)
        self.assertIn("rr7---sn-xguxaxjvh-304e.c.youtube.com", names)
        self.assertIn("r2---sn-xguxaxjvh-304e.googlevideo.com", names)
        self.assertEqual(len(names), 46)

    def test_junk_is_never_a_group(self) -> None:
        for junk in ("", "googlevideo.com", "sn-", "sn-bad name", "x\n127.0.0.1 evil", "rr1---sn-a"):
            self.assertEqual(block.names_for_group(junk), (), junk)


class ContentTests(unittest.TestCase):
    def test_block_is_added_after_user_lines_and_they_stay(self) -> None:
        text = block.build_hosts_content(_USER, ["sn-hxb54vo-3046"])
        self.assertTrue(text.startswith(_USER.rstrip("\n")))
        self.assertIn(f"{block.BLOCK_IP} rr4---sn-hxb54vo-3046.googlevideo.com", text)
        self.assertEqual(block.blocked_groups(text), ("sn-hxb54vo-3046",))

    def test_rebuild_replaces_the_old_block_instead_of_adding_a_second(self) -> None:
        once = block.build_hosts_content(_USER, ["sn-a1"])
        twice = block.build_hosts_content(once, ["sn-a1", "sn-b2"])
        self.assertEqual(twice.count(block.BLOCK_BEGIN), 1)
        self.assertEqual(block.blocked_groups(twice), ("sn-a1", "sn-b2"))

    def test_empty_groups_remove_the_block_and_keep_everything_else(self) -> None:
        text = block.build_hosts_content(block.build_hosts_content(_USER, ["sn-a1"]), [])
        self.assertEqual(text, _USER)

    def test_lost_end_marker_does_not_eat_user_lines_after_a_blank_line(self) -> None:
        broken = block.BLOCK_BEGIN + "\n127.0.0.2 rr1---sn-a1.googlevideo.com\n\n10.0.0.5 nas.home\n"
        self.assertIn("10.0.0.5 nas.home", block.build_hosts_content(broken, []))

    def test_hosts_page_sees_the_block_as_its_own_owner(self) -> None:
        kinds = [item.kind for item in parse_hosts_blocks(block.build_hosts_content(_USER, ["sn-a1"]))]
        self.assertIn(BLOCK_YOUTUBE_CACHE, kinds)
        self.assertIn(BLOCK_USER, kinds)


class ChangeTests(unittest.TestCase):
    def test_block_adds_to_already_blocked_groups(self) -> None:
        file = _File(block.build_hosts_content(_USER, ["sn-a1"]))
        ok, message = block.block_groups(["sn-b2"], read_hosts_file=file.read, write_hosts_file=file.write)
        self.assertTrue(ok)
        self.assertIn("Перезапустите браузер", message)
        self.assertEqual(block.blocked_groups(file.text), ("sn-a1", "sn-b2"))

    def test_unblock_removes_only_our_block(self) -> None:
        file = _File(block.build_hosts_content(_USER, ["sn-a1", "sn-b2"]))
        ok, _message = block.unblock_all(read_hosts_file=file.read, write_hosts_file=file.write)
        self.assertTrue(ok)
        self.assertEqual(file.text, _USER)

    def test_nothing_valid_to_block_writes_nothing(self) -> None:
        file = _File(_USER)
        ok, _message = block.block_groups(["junk name"], read_hosts_file=file.read, write_hosts_file=file.write)
        self.assertFalse(ok)
        self.assertEqual(file.text, _USER)

    def test_read_and_write_failures_are_reported(self) -> None:
        self.assertEqual(block.block_groups(["sn-a1"], read_hosts_file=lambda: None, write_hosts_file=lambda _t: True)[0], False)
        ok, message = block.block_groups(["sn-a1"], read_hosts_file=lambda: _USER, write_hosts_file=lambda _t: False)
        self.assertFalse(ok)
        self.assertIn("администратора", message)


if __name__ == "__main__":
    unittest.main()
