"""Редактор hostlist: предупреждения, подсказки и быстрые действия для «^» и «!»."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from lists.core.hostlist_syntax import HostlistIndex  # noqa: E402
from profile.list_file_language import (  # noqa: E402
    HostlistEditorLanguage,
    complete_hostlist,
    describe_hostlist_line,
    diagnose_hostlist_text,
    quick_actions_hostlist,
)


def base(*lines: str) -> HostlistIndex:
    return HostlistIndex.from_texts(("base", "\n".join(lines)))


def apply(text: str, fix) -> str:
    lines = text.split("\n")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    result = text
    for edit in sorted(fix.edits, key=lambda e: (e.start_line, e.start_column), reverse=True):
        result = result[:offsets[edit.start_line] + edit.start_column] + edit.text + result[offsets[edit.end_line] + edit.end_column:]
    return result


class HostlistDiagnosticsTests(unittest.TestCase):
    def messages(self, text: str, **kwargs):
        return [(d.line, d.severity, d.message) for d in diagnose_hostlist_text(text, **kwargs)]

    def test_useful_exception_is_quiet(self) -> None:
        self.assertEqual(self.messages("ru\n!torrents.ru"), [])
        self.assertEqual(self.messages("!torrents.ru", base_index=base("ru")), [])

    def test_exception_without_covering_record(self) -> None:
        found = self.messages("!torrents.ru\nexample.com")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][:2], (0, "warning"))
        self.assertIn("нечего исключать", found[0][2])

    def test_second_record_of_domain_is_ignored(self) -> None:
        found = self.messages("ru\n!ru")
        self.assertEqual([(line, severity) for line, severity, _ in found], [(1, "warning")])
        self.assertIn("только первую запись", found[0][2])

    def test_user_record_overrides_builtin(self) -> None:
        found = self.messages("!youtube.com", base_index=base("youtube.com"))
        self.assertEqual([(line, severity) for line, severity, _ in found], [(0, "hint")])
        self.assertIn("Перебивает встроенную", found[0][2])

    def test_record_already_in_builtin_part(self) -> None:
        found = self.messages("youtube.com\nwww.google.com", base_index=base("youtube.com", "google.com"))
        self.assertEqual([(line, severity) for line, severity, _ in found], [(0, "hint"), (1, "hint")])
        self.assertIn("уже есть во встроенных", found[0][2])
        self.assertIn("через «google.com»", found[1][2])

    def test_wildcard_is_error_with_fix(self) -> None:
        text = "!*.example.com"
        problem = diagnose_hostlist_text(text)[0]
        self.assertEqual(problem.severity, "error")
        self.assertEqual(apply(text, problem.fixes[0]), "!example.com")


class HostlistHoverAndActionsTests(unittest.TestCase):
    def test_hover_explains_exception_with_its_parent(self) -> None:
        text = describe_hostlist_line("!torrents.ru", 0, base_index=base("ru"))
        self.assertIn("исключение", text)
        self.assertIn("«ru»", text)
        self.assertIn("--hostlist-exclude", text)

    def test_hover_for_exact_and_plain(self) -> None:
        self.assertIn("только сам a.ru", describe_hostlist_line("^a.ru", 0))
        self.assertIn("все его поддомены", describe_hostlist_line("a.ru", 0))
        self.assertEqual(describe_hostlist_line("# x", 0), "")

    def test_quick_actions_toggle_prefixes(self) -> None:
        text = "  ^a.ru"
        titles = {fix.title: fix for fix in quick_actions_hostlist(text, 0)}
        self.assertEqual(apply(text, titles["Сделать исключением: «!a.ru»"]), "  !^a.ru")
        self.assertEqual(apply(text, titles["Вместе с поддоменами: убрать «^»"]), "  a.ru")
        text = "!a.ru"
        titles = {fix.title: fix for fix in quick_actions_hostlist(text, 0)}
        self.assertEqual(apply(text, titles["Убрать исключение «!»"]), "a.ru")

    def test_prefix_completion_only_on_request_at_line_start(self) -> None:
        self.assertIsNone(complete_hostlist("a.ru", 0, 0, explicit=False))
        labels = [item.label for item in complete_hostlist("a.ru", 0, 0, explicit=True).items]
        self.assertEqual(labels, ["!", "^"])
        labels = [item.label for item in complete_hostlist("!a.ru", 0, 1, explicit=True).items]
        self.assertEqual(labels, ["^"])
        self.assertIsNone(complete_hostlist("a.ru", 0, 3, explicit=True))

    def test_zapret1_mode_warns_that_exception_does_not_work(self) -> None:
        # winws (Zapret 1) знает «^», но не «!»: zapret nfq/hostlist.c.
        language = HostlistEditorLanguage(exceptions=False)
        language.set_list(kind="hostlist", base_index=None)
        problems = language.diagnose("ru\n!torrents.ru\n^a.ru")
        self.assertEqual([(d.line, d.severity) for d in problems], [(1, "warning")])
        self.assertIn("Zapret 1", problems[0].message)
        self.assertIn("Zapret 1", language.describe("!torrents.ru", 0, 0))
        titles = [fix.title for fix in language.quick_actions("a.ru", 0, 0)]
        self.assertFalse(any("исключением" in title for title in titles))
        labels = [item.label for item in language.complete("a.ru", 0, 0, explicit=True).items]
        self.assertEqual(labels, ["^"])

    def test_language_is_silent_for_ipset(self) -> None:
        language = HostlistEditorLanguage()
        language.set_list(kind="ipset", base_index=None)
        self.assertEqual(language.diagnose("!1.2.3.4"), ())
        language.set_list(kind="hostlist", base_index=None)
        self.assertTrue(language.diagnose("!a.ru"))


class ListPlaceholderTests(unittest.TestCase):
    """Подсказка внутри пустого поля «Ваши записи»: примеры можно вставить как есть."""

    def test_examples_are_valid_list_text(self) -> None:
        from profile.list_file_editor import validate_profile_list_file_text
        from profile.list_file_language import (
            HOSTLIST_PLACEHOLDER,
            HOSTLIST_PLACEHOLDER_ZAPRET1,
            IPSET_PLACEHOLDER,
        )

        for text in (HOSTLIST_PLACEHOLDER, HOSTLIST_PLACEHOLDER_ZAPRET1):
            with self.subTest(text=text[:20]):
                self.assertEqual(validate_profile_list_file_text("hostlist", text), ())
                problems = [d for d in diagnose_hostlist_text(text) if d.severity != "hint"]
                self.assertEqual(problems, [])
        self.assertEqual(validate_profile_list_file_text("ipset", IPSET_PLACEHOLDER), ())
        for needle in ("^vk.com", "!torrents.ru", "\nru\n"):
            self.assertIn(needle, HOSTLIST_PLACEHOLDER)
        self.assertNotIn("\n!", HOSTLIST_PLACEHOLDER_ZAPRET1)
        self.assertIn("10.0.0.0/8", IPSET_PLACEHOLDER)

    def test_placeholder_follows_kind_and_mode(self) -> None:
        from profile.list_file_language import HOSTLIST_PLACEHOLDER_ZAPRET1, IPSET_PLACEHOLDER, list_placeholder

        self.assertEqual(list_placeholder("ipset"), IPSET_PLACEHOLDER)
        self.assertEqual(list_placeholder("hostlist", exceptions=False), HOSTLIST_PLACEHOLDER_ZAPRET1)


if __name__ == "__main__":
    unittest.main()
