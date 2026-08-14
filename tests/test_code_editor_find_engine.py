from __future__ import annotations

import unittest


class FindEngineTests(unittest.TestCase):
    def test_literal_search_is_case_insensitive_by_default(self) -> None:
        from ui.code_editor.find_engine import SearchOptions, find_matches

        result = find_matches("Alpha alpha ALPHA", "alpha")

        self.assertTrue(result.ok)
        self.assertEqual(result.count, 3)
        self.assertEqual(result.matches[0].start, 0)

        strict = find_matches("Alpha alpha ALPHA", "alpha", SearchOptions(case_sensitive=True))
        self.assertEqual(strict.count, 1)
        self.assertEqual(strict.matches[0].start, 6)

    def test_whole_word_ignores_partial_occurrences(self) -> None:
        from ui.code_editor.find_engine import SearchOptions, find_matches

        text = "cat category cat\n"
        loose = find_matches(text, "cat")
        strict = find_matches(text, "cat", SearchOptions(whole_word=True))

        self.assertEqual(loose.count, 3)
        self.assertEqual(strict.count, 2)
        self.assertEqual([match.start for match in strict.matches], [0, 13])

    def test_whole_word_keeps_flag_queries_searchable(self) -> None:
        from ui.code_editor.find_engine import SearchOptions, find_matches

        # Граница слова перед «-» никогда не совпадает, поэтому для запросов,
        # начинающихся не с буквы, \b не добавляется.
        result = find_matches("--new\n--filter-tcp=443\n", "--new", SearchOptions(whole_word=True))

        self.assertEqual(result.count, 1)

    def test_regex_search_and_invalid_pattern_report_error(self) -> None:
        from ui.code_editor.find_engine import SearchOptions, find_matches

        options = SearchOptions(regex=True)
        result = find_matches("--filter-tcp=443\n--filter-udp=443\n", r"--filter-\w+", options)
        self.assertEqual(result.count, 2)

        broken = find_matches("text", "(unclosed", options)
        self.assertFalse(broken.ok)
        self.assertEqual(broken.count, 0)
        self.assertTrue(broken.error)

    def test_zero_length_regex_matches_are_skipped(self) -> None:
        from ui.code_editor.find_engine import SearchOptions, find_matches

        result = find_matches("aaa\nbbb\n", "a*", SearchOptions(regex=True))

        self.assertTrue(result.ok)
        self.assertEqual(result.count, 1)
        self.assertEqual((result.matches[0].start, result.matches[0].end), (0, 3))

    def test_match_limit_marks_result_as_truncated(self) -> None:
        from ui.code_editor.find_engine import find_matches

        result = find_matches("x" * 50, "x", limit=10)

        self.assertEqual(result.count, 10)
        self.assertTrue(result.truncated)

    def test_locate_match_walks_forward_and_wraps(self) -> None:
        from ui.code_editor.find_engine import find_matches, locate_match

        result = find_matches("alpha beta alpha beta alpha", "alpha")
        matches = result.matches

        self.assertEqual(locate_match(matches, 0, include_position=True), 0)
        self.assertEqual(locate_match(matches, matches[0].end), 1)
        self.assertEqual(locate_match(matches, matches[2].end), 0)

    def test_locate_match_walks_backward_and_wraps(self) -> None:
        from ui.code_editor.find_engine import find_matches, locate_match

        result = find_matches("alpha beta alpha beta alpha", "alpha")
        matches = result.matches

        self.assertEqual(locate_match(matches, matches[2].start, reverse=True), 1)
        self.assertEqual(locate_match(matches, 0, reverse=True), len(matches) - 1)

    def test_counter_text_reports_position_and_absence(self) -> None:
        from ui.code_editor.find_engine import SearchResult, build_counter_text, find_matches

        result = find_matches("alpha alpha", "alpha")
        self.assertEqual(build_counter_text(result, 1, query="alpha"), "2 / 2")
        self.assertEqual(build_counter_text(result, None, query="alpha"), "2 совп.")
        self.assertEqual(build_counter_text(SearchResult(), 0, query="zzz"), "Нет совпадений")
        self.assertEqual(build_counter_text(SearchResult(error="bad"), None, query="("), "Ошибка")
        self.assertEqual(build_counter_text(result, 0, query=""), "")

    def test_counter_text_marks_truncated_total(self) -> None:
        from ui.code_editor.find_engine import build_counter_text, find_matches

        result = find_matches("x" * 20, "x", limit=5)

        self.assertEqual(build_counter_text(result, 0, query="x"), "1 / 5+")

    def test_replace_all_replaces_every_match(self) -> None:
        from ui.code_editor.find_engine import replace_all

        result = replace_all("alpha beta alpha", "alpha", "gamma")

        self.assertTrue(result.ok)
        self.assertEqual(result.count, 2)
        self.assertTrue(result.changed)
        self.assertEqual(result.text, "gamma beta gamma")

    def test_replace_all_expands_regex_groups(self) -> None:
        from ui.code_editor.find_engine import SearchOptions, replace_all

        result = replace_all(
            "--filter-tcp=80\n--filter-udp=443\n",
            r"--filter-(\w+)=(\d+)",
            r"--filter-\1=\2\2",
            SearchOptions(regex=True),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.count, 2)
        self.assertEqual(result.text, "--filter-tcp=8080\n--filter-udp=443443\n")

    def test_replace_all_reports_invalid_replacement(self) -> None:
        from ui.code_editor.find_engine import SearchOptions, replace_all

        result = replace_all("alpha", "alpha", r"\9", SearchOptions(regex=True))

        self.assertFalse(result.ok)
        self.assertEqual(result.count, 0)
        self.assertEqual(result.text, "alpha")

    def test_replace_all_on_missing_query_keeps_text(self) -> None:
        from ui.code_editor.find_engine import replace_all

        result = replace_all("alpha", "zzz", "x")

        self.assertTrue(result.ok)
        self.assertEqual(result.count, 0)
        self.assertFalse(result.changed)
        self.assertEqual(result.text, "alpha")


if __name__ == "__main__":
    unittest.main()
