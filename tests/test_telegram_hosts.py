from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from telegram_proxy.telegram_hosts import (
    TELEGRAM_DOMAINS,
    TELEGRAM_HOSTS_MARKER,
    TELEGRAM_RELAY_IP,
    TelegramHostsError,
    add_telegram_hosts,
    add_telegram_hosts_to_text,
    get_telegram_hosts_status,
    remove_telegram_hosts,
    remove_telegram_hosts_from_text,
    telegram_hosts_status_from_text,
)

_USER_LINES = (
    "# Copyright (c) 1993-2009 Microsoft Corp.\n"
    "127.0.0.1 localhost\n"
    "10.0.0.5 nas.home\n"
)


def _block() -> str:
    return TELEGRAM_HOSTS_MARKER + "\n" + "".join(f"{TELEGRAM_RELAY_IP} {d}\n" for d in TELEGRAM_DOMAINS)


class TelegramHostsTextTests(unittest.TestCase):
    def test_status_counts_none_partial_and_all(self) -> None:
        total = len(TELEGRAM_DOMAINS)

        none = telegram_hosts_status_from_text(_USER_LINES)
        self.assertEqual((none.present, none.total, none.block_present), (0, total, False))
        self.assertTrue(none.is_empty)

        partial_text = _USER_LINES + f"{TELEGRAM_RELAY_IP} t.me\n{TELEGRAM_RELAY_IP} web.telegram.org\n"
        partial = telegram_hosts_status_from_text(partial_text)
        self.assertEqual((partial.present, partial.total, partial.block_present), (2, total, False))
        self.assertFalse(partial.is_empty)
        self.assertFalse(partial.is_complete)

        full = telegram_hosts_status_from_text(add_telegram_hosts_to_text(_USER_LINES))
        self.assertEqual((full.present, full.total, full.block_present), (total, total, True))
        self.assertTrue(full.is_complete)

    def test_status_ignores_comments_and_other_ip(self) -> None:
        text = f"# {TELEGRAM_RELAY_IP} t.me\n1.2.3.4 web.telegram.org\n"

        status = telegram_hosts_status_from_text(text)

        self.assertEqual(status.present, 0)

    def test_status_uses_first_entry_like_windows(self) -> None:
        text = f"1.2.3.4 t.me\n{TELEGRAM_RELAY_IP} t.me\n"

        self.assertEqual(telegram_hosts_status_from_text(text).present, 0)

    def test_add_writes_telegram_cdn_and_download_domains(self) -> None:
        text = add_telegram_hosts_to_text(_USER_LINES)

        self.assertTrue(text.startswith(_USER_LINES))
        self.assertIn(TELEGRAM_HOSTS_MARKER, text)
        for domain in (
            "cdn1.telesco.pe",
            "cdn4.telesco.pe",
            "desktop.telegram.org",
            "macos.telegram.org",
            "web.telegram.org",
            "t.me",
        ):
            self.assertIn(f"{TELEGRAM_RELAY_IP} {domain}\n", text)

    def test_add_is_idempotent(self) -> None:
        once = add_telegram_hosts_to_text(_USER_LINES)
        twice = add_telegram_hosts_to_text(once)

        self.assertEqual(once, twice)
        self.assertEqual(twice.count(TELEGRAM_HOSTS_MARKER), 1)

    def test_add_replaces_telegram_domain_on_other_ip(self) -> None:
        text = add_telegram_hosts_to_text(_USER_LINES + "1.2.3.4 t.me\n")

        self.assertNotIn("1.2.3.4 t.me", text)
        self.assertEqual(text.count(f"{TELEGRAM_RELAY_IP} t.me\n"), 1)

    def test_remove_keeps_foreign_lines_and_telegram_domain_on_other_ip(self) -> None:
        foreign = _USER_LINES + "1.2.3.4 t.me\n"
        text = foreign + "\n" + _block()

        cleaned = remove_telegram_hosts_from_text(text)

        self.assertEqual(cleaned, foreign)
        self.assertNotIn(TELEGRAM_HOSTS_MARKER, cleaned)
        self.assertEqual(telegram_hosts_status_from_text(cleaned).present, 0)

    def test_remove_after_add_restores_original(self) -> None:
        self.assertEqual(remove_telegram_hosts_from_text(add_telegram_hosts_to_text(_USER_LINES)), _USER_LINES)

    def test_remove_without_telegram_entries_returns_same_text(self) -> None:
        text = _USER_LINES + "\n\n1.2.3.4 t.me\n"

        self.assertIs(remove_telegram_hosts_from_text(text), text)


class TelegramHostsFileTests(unittest.TestCase):
    def test_status_only_reads(self) -> None:
        write = Mock(return_value=True)
        with (
            patch("hosts.public.read_hosts_file", return_value=_USER_LINES),
            patch("hosts.public.write_hosts_file", write),
        ):
            status = get_telegram_hosts_status()

        self.assertEqual(status.present, 0)
        write.assert_not_called()

    def test_add_writes_block_and_second_add_does_not_write(self) -> None:
        written: list[str] = []
        with (
            patch("hosts.public.read_hosts_file", return_value=_USER_LINES),
            patch("hosts.public.write_hosts_file", side_effect=lambda text: written.append(text) or True),
        ):
            changed, _message = add_telegram_hosts()
        self.assertTrue(changed)
        self.assertEqual(len(written), 1)

        write = Mock(return_value=True)
        with (
            patch("hosts.public.read_hosts_file", return_value=written[0]),
            patch("hosts.public.write_hosts_file", write),
        ):
            changed, _message = add_telegram_hosts()
        self.assertFalse(changed)
        write.assert_not_called()

    def test_remove_without_changes_does_not_write(self) -> None:
        write = Mock(return_value=True)
        with (
            patch("hosts.public.read_hosts_file", return_value=_USER_LINES + "1.2.3.4 t.me\n"),
            patch("hosts.public.write_hosts_file", write),
        ):
            changed, _message = remove_telegram_hosts()

        self.assertFalse(changed)
        write.assert_not_called()

    def test_remove_writes_cleaned_text(self) -> None:
        write = Mock(return_value=True)
        with (
            patch("hosts.public.read_hosts_file", return_value=_USER_LINES + "\n" + _block()),
            patch("hosts.public.write_hosts_file", write),
        ):
            changed, _message = remove_telegram_hosts()

        self.assertTrue(changed)
        write.assert_called_once_with(_USER_LINES)

    def test_write_failure_raises_readable_error(self) -> None:
        for write in (Mock(return_value=False), Mock(side_effect=PermissionError("read-only"))):
            with self.subTest(write=write):
                with (
                    patch("hosts.public.read_hosts_file", return_value=_USER_LINES),
                    patch("hosts.public.write_hosts_file", write),
                ):
                    with self.assertRaises(TelegramHostsError) as ctx:
                        add_telegram_hosts()
                self.assertIn("только для чтения", str(ctx.exception))

    def test_unreadable_hosts_raises(self) -> None:
        with patch("hosts.public.read_hosts_file", return_value=None):
            with self.assertRaises(TelegramHostsError):
                get_telegram_hosts_status()


if __name__ == "__main__":
    unittest.main()
