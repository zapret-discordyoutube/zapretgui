from __future__ import annotations

import unittest


class LineOpsTests(unittest.TestCase):
    def test_duplicate_lines_copies_block_below_and_moves_cursor(self) -> None:
        from ui.code_editor.line_ops import duplicate_lines

        plan = duplicate_lines(["a", "b", "c"], 1, 1)

        self.assertIsNotNone(plan)
        self.assertEqual((plan.start_line, plan.end_line), (1, 1))
        self.assertEqual(plan.lines, ("b", "b"))
        self.assertEqual((plan.anchor_line, plan.cursor_line), (2, 2))

    def test_duplicate_lines_keeps_multiline_selection(self) -> None:
        from ui.code_editor.line_ops import duplicate_lines

        plan = duplicate_lines(["a", "b", "c"], 0, 1)

        self.assertEqual(plan.lines, ("a", "b", "a", "b"))
        self.assertEqual((plan.anchor_line, plan.cursor_line), (2, 3))

    def test_delete_lines_removes_block(self) -> None:
        from ui.code_editor.line_ops import delete_lines

        plan = delete_lines(["a", "b", "c"], 1, 2)

        self.assertEqual(plan.lines, ())
        self.assertEqual((plan.start_line, plan.end_line), (1, 2))
        self.assertEqual(plan.cursor_line, 0)

    def test_move_lines_up_swaps_with_previous_line(self) -> None:
        from ui.code_editor.line_ops import move_lines

        plan = move_lines(["a", "b", "c"], 1, 1, delta=-1)

        self.assertEqual((plan.start_line, plan.end_line), (0, 1))
        self.assertEqual(plan.lines, ("b", "a"))
        self.assertEqual((plan.anchor_line, plan.cursor_line), (0, 0))

    def test_move_lines_down_swaps_with_next_line(self) -> None:
        from ui.code_editor.line_ops import move_lines

        plan = move_lines(["a", "b", "c"], 0, 0, delta=1)

        self.assertEqual((plan.start_line, plan.end_line), (0, 1))
        self.assertEqual(plan.lines, ("b", "a"))
        self.assertEqual((plan.anchor_line, plan.cursor_line), (1, 1))

    def test_move_lines_at_document_edges_is_rejected(self) -> None:
        from ui.code_editor.line_ops import move_lines

        self.assertIsNone(move_lines(["a", "b"], 0, 0, delta=-1))
        self.assertIsNone(move_lines(["a", "b"], 1, 1, delta=1))
        self.assertIsNone(move_lines(["a", "b"], 0, 0, delta=0))

    def test_toggle_comment_adds_prefix_at_shared_indent(self) -> None:
        from ui.code_editor.line_ops import toggle_comment

        plan = toggle_comment(["  --new", "    --filter-tcp=443"], 0, 1)

        self.assertEqual(plan.lines, ("  # --new", "  #   --filter-tcp=443"))
        self.assertEqual(plan.cursor_column_delta, 2)

    def test_toggle_comment_removes_prefix_when_whole_block_commented(self) -> None:
        from ui.code_editor.line_ops import toggle_comment

        plan = toggle_comment(["# a", "#b", ""], 0, 2)

        self.assertEqual(plan.lines, ("a", "b", ""))

    def test_toggle_comment_comments_mixed_block(self) -> None:
        from ui.code_editor.line_ops import toggle_comment

        plan = toggle_comment(["# a", "b"], 0, 1)

        self.assertEqual(plan.lines, ("# # a", "# b"))

    def test_toggle_comment_ignores_blank_block(self) -> None:
        from ui.code_editor.line_ops import toggle_comment

        self.assertIsNone(toggle_comment(["", "   "], 0, 1))

    def test_indent_and_unindent_round_trip(self) -> None:
        from ui.code_editor.line_ops import indent_lines, unindent_lines

        indented = indent_lines(["a", "", "b"], 0, 2)
        self.assertEqual(indented.lines, ("    a", "", "    b"))
        self.assertEqual(indented.anchor_column_delta, 4)

        restored = unindent_lines(list(indented.lines), 0, 2)
        self.assertEqual(restored.lines, ("a", "", "b"))
        self.assertEqual(restored.cursor_column_delta, -4)

    def test_unindent_handles_partial_indent_and_tabs(self) -> None:
        from ui.code_editor.line_ops import unindent_lines

        plan = unindent_lines([" a", "\tb"], 0, 1)

        self.assertEqual(plan.lines, ("a", "b"))

    def test_unindent_without_indent_is_rejected(self) -> None:
        from ui.code_editor.line_ops import unindent_lines

        self.assertIsNone(unindent_lines(["a", "b"], 0, 1))

    def test_range_is_normalized_and_clamped(self) -> None:
        from ui.code_editor.line_ops import duplicate_lines

        plan = duplicate_lines(["a", "b"], 5, -3)

        self.assertEqual((plan.start_line, plan.end_line), (0, 1))
        self.assertIsNone(duplicate_lines([], 0, 0))


if __name__ == "__main__":
    unittest.main()
