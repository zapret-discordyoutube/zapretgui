"""Строка hostlist и поиск домена — как в движке winws2 (zapret2 nfq2/hostlist.c).

Примеры взяты из описания автора движка (коммит 48ec126, 31.10.2026):
«ru» + «!torrents.ru» — все сайты .ru, кроме torrents.ru.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from lists.core.builders import build_combined_content  # noqa: E402
from lists.core.hostlist_syntax import (  # noqa: E402
    HostlistIndex,
    format_hostlist_entry,
    hostlist_line_problem,
    parse_hostlist_line,
)


def index(*lines: str) -> HostlistIndex:
    return HostlistIndex.from_texts(("list", "\n".join(lines)))


class ParseLineTests(unittest.TestCase):
    def test_prefixes_in_any_order(self) -> None:
        for raw in ("!^a.ru", "^!a.ru", "  !^A.RU", "\t^!a.ru  tail"):
            with self.subTest(raw=raw):
                entry = parse_hostlist_line(raw)
                self.assertTrue(entry.is_entry)
                self.assertEqual((entry.domain, entry.exact, entry.exception), ("a.ru", True, True))
                self.assertEqual(entry.prefix, "!^")

    def test_comments_like_engine(self) -> None:
        for raw in ("# x", ";x", "//x", "/x", "   # x"):
            with self.subTest(raw=raw):
                self.assertEqual(parse_hostlist_line(raw).kind, "comment")
        self.assertEqual(parse_hostlist_line("   ").kind, "blank")

    def test_columns_point_at_domain(self) -> None:
        entry = parse_hostlist_line("  !^site.com")
        self.assertEqual((entry.start, entry.domain_start, entry.domain_end), (2, 4, 12))

    def test_format_entry(self) -> None:
        self.assertEqual(format_hostlist_entry("A.ru", exact=True, exception=True), "!^a.ru")


class LookupTests(unittest.TestCase):
    def test_local_exception_inside_zone(self) -> None:
        hostlist = index("ru", "!torrents.ru")
        self.assertTrue(hostlist.contains("mail.ru"))
        self.assertFalse(hostlist.contains("torrents.ru"))
        self.assertFalse(hostlist.contains("www.torrents.ru"))
        self.assertTrue(hostlist.lookup("torrents.ru").entry.exception)
        self.assertIsNone(hostlist.lookup("example.com"))

    def test_exact_exception_keeps_subdomains(self) -> None:
        hostlist = index("ru", "^!a.ru")
        self.assertFalse(hostlist.contains("a.ru"))
        self.assertTrue(hostlist.contains("www.a.ru"))

    def test_exact_entry_skipped_on_parent_level(self) -> None:
        hostlist = index("^ru")
        self.assertTrue(hostlist.contains("ru"))
        self.assertIsNone(hostlist.lookup("x.ru"))

    def test_first_record_of_domain_wins(self) -> None:
        self.assertTrue(index("ru", "!ru").contains("x.ru"))
        self.assertFalse(index("!ru", "ru").contains("x.ru"))
        self.assertEqual(len(index("ru", "!ru", "^ru")), 1)

    def test_most_specific_record_decides(self) -> None:
        hostlist = index("!youtube.com", "music.youtube.com")
        self.assertTrue(hostlist.contains("music.youtube.com"))
        self.assertFalse(hostlist.contains("www.youtube.com"))

    def test_covering_parent(self) -> None:
        hostlist = index("ru", "!torrents.ru", "^com")
        self.assertEqual(hostlist.covering_parent("torrents.ru").entry.domain, "ru")
        self.assertIsNone(hostlist.covering_parent("vk.com"))

    def test_fast_path_matches_full_parser(self) -> None:
        text = "Example.COM\n!a.example.com\n  b.ru\n^c.ru\n# x\n;y\n/z\nd.ru tail\n"
        fast = HostlistIndex.from_texts(("list", text))
        for host in ("example.com", "a.example.com", "x.a.example.com", "b.ru", "c.ru", "w.c.ru", "d.ru", "tail"):
            with self.subTest(host=host):
                expected = None
                for raw in text.splitlines():
                    entry = parse_hostlist_line(raw)
                    if entry.is_entry and entry.domain == host:
                        expected = entry
                        break
                found = fast.get(host)
                self.assertEqual(found is None, expected is None)
                if expected is not None:
                    self.assertEqual((found.entry.exact, found.entry.exception),
                                     (expected.exact, expected.exception))


class LineProblemTests(unittest.TestCase):
    def test_valid_entries(self) -> None:
        for raw in ("ru", "!torrents.ru", "^!a.ru", "!^a.ru", "^Example.com", "# comment", "; comment"):
            with self.subTest(raw=raw):
                self.assertEqual(hostlist_line_problem(raw), "")

    def test_lines_that_never_match(self) -> None:
        for raw in ("*.example.com", "!*.example.com", ".example.com", "!", "^!",
                    "https://example.com", "1.2.3.4", "bad domain"):
            with self.subTest(raw=raw):
                self.assertNotEqual(hostlist_line_problem(raw), "")


class FinalFileOrderTests(unittest.TestCase):
    def test_user_exception_overrides_builtin_record(self) -> None:
        final = build_combined_content(["youtube.com", "ru"], ["!youtube.com"])
        self.assertEqual(final, "!youtube.com\nyoutube.com\nru\n")
        self.assertFalse(index(*final.splitlines()).contains("www.youtube.com"))


class ListRegistrationFailureTests(unittest.TestCase):
    def test_engine_messages_become_plain_reasons(self) -> None:
        from winws_runtime.health.winws_output import explain_list_registration_failure

        duplicate = "github version v1.0.6\nfailed to register hostlist 'lists/a.txt'\n"
        self.assertIn("дважды", explain_list_registration_failure(duplicate))
        missing = "cannot access ipset file 'lists/ipset-x.txt'\nfailed to register ipset 'lists/ipset-x.txt'\n"
        self.assertIn("не найден файл списка", explain_list_registration_failure(missing))
        self.assertEqual(explain_list_registration_failure("windivert: error opening filter"), "")


if __name__ == "__main__":
    unittest.main()
