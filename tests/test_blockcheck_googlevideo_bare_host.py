"""Голый googlevideo.com распознаётся во всех формах записи."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from blockcheck.googlevideo_discovery import is_bare_googlevideo_host  # noqa: E402


class BareGoogleVideoHostTests(unittest.TestCase):
    def test_bare_hosts_detected(self) -> None:
        for value in (
            "googlevideo.com",
            "www.googlevideo.com",
            "https://googlevideo.com",
            "https://googlevideo.com/watch?v=1",
            "GOOGLEVIDEO.COM.",
            "googlevideo.com:443",
        ):
            with self.subTest(value=value):
                self.assertTrue(is_bare_googlevideo_host(value))

    def test_real_video_and_service_hosts_pass(self) -> None:
        for value in (
            "rr5---sn-c0q7lnz7.googlevideo.com",
            "redirector.googlevideo.com",
            "youtube.com",
            "",
            None,
        ):
            with self.subTest(value=value):
                self.assertFalse(is_bare_googlevideo_host(value))


if __name__ == "__main__":
    unittest.main()
