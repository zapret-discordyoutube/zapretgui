from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class ListsCoreFilesTests(unittest.TestCase):
    def test_write_text_file_keeps_existing_file_when_prepare_fails(self) -> None:
        from lists.core import files

        with TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "ipset-amazon.txt"
            target.write_text("old\n", encoding="utf-8")

            with patch("lists.core.files.normalize_newlines", side_effect=RuntimeError("boom")):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    files.write_text_file(str(target), "new\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")

    def test_write_text_file_skips_replace_when_content_is_unchanged(self) -> None:
        from lists.core import files

        with TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "cloudflare.txt"
            target.write_text("cloudflare.com\n", encoding="utf-8")

            with patch("lists.core.files.os.replace", side_effect=PermissionError("locked")) as replace:
                files.write_text_file(str(target), "cloudflare.com\n")

            replace.assert_not_called()
            self.assertEqual(target.read_text(encoding="utf-8"), "cloudflare.com\n")

    def test_read_text_file_drops_bom(self) -> None:
        from lists.core import files

        with TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "my.txt"
            target.write_bytes("\ufeffexample.com\nother.com\n".encode("utf-8"))

            self.assertEqual(files.read_text_file(str(target)), "example.com\nother.com\n")

    def test_read_text_file_keeps_cp1251_text_instead_of_empty(self) -> None:
        from lists.core import files

        with TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "my.txt"
            target.write_bytes("# мои сайты\nexample.com\n".encode("cp1251"))

            self.assertEqual(files.read_text_file_safe(str(target)), "# мои сайты\nexample.com\n")

    def test_write_text_file_rewrites_cp1251_file_as_utf8(self) -> None:
        from lists.core import files

        with TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "my.txt"
            target.write_bytes("# мои сайты\n".encode("cp1251"))

            files.write_text_file(str(target), "# мои сайты\n")

            self.assertEqual(target.read_bytes(), "# мои сайты\n".encode("utf-8"))

    def test_read_text_file_safe_returns_none_for_missing_file(self) -> None:
        from lists.core import files

        with TemporaryDirectory() as temp_dir:
            self.assertIsNone(files.read_text_file_safe(str(Path(temp_dir) / "none.txt")))


if __name__ == "__main__":
    unittest.main()
