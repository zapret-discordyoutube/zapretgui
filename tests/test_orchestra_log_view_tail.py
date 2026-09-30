import tempfile
import unittest
from pathlib import Path

from orchestra.orchestra_runner import OrchestraRunner


class OrchestraLogViewTailTests(unittest.TestCase):
    def _runner(self, logs_path: str) -> OrchestraRunner:
        runner = OrchestraRunner.__new__(OrchestraRunner)
        runner.logs_path = logs_path
        return runner

    def test_big_log_is_read_only_from_the_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lines = [f"line {index:06d}\n" for index in range(20000)]
            Path(tmp, "orchestra_big.log").write_text("".join(lines), encoding="utf-8")

            content = self._runner(tmp).get_log_content("big", max_bytes=4096)

        self.assertLessEqual(len(content.encode("utf-8")), 4096)
        self.assertTrue(content.endswith("line 019999\n"))
        self.assertTrue(content.startswith("line "))

    def test_small_log_is_returned_whole(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "orchestra_small.log").write_text("один\nдва\n", encoding="utf-8")

            content = self._runner(tmp).get_log_content("small")

        self.assertEqual(content, "один\nдва\n")

    def test_missing_log_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(self._runner(tmp).get_log_content("missing"))


if __name__ == "__main__":
    unittest.main()
