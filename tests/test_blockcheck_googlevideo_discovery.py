"""Динамическое обнаружение GoogleVideo CDN через сеть пользователя."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from blockcheck.googlevideo_discovery import (  # noqa: E402
    _extract_googlevideo_hosts,
    discover_googlevideo_host,
)


class GoogleVideoDiscoveryTests(unittest.TestCase):
    def test_extracts_encoded_rr_hosts_and_rejects_service_hosts(self) -> None:
        page = (
            "https%3A%2F%2Frr8---sn-user-a.googlevideo.com%2Fvideoplayback "
            "https://redirector.googlevideo.com/report_mapping "
            "https://rr3---sn-user-b.googlevideo.com/videoplayback"
        )

        self.assertEqual(
            _extract_googlevideo_hosts(page),
            (
                "rr8---sn-user-a.googlevideo.com",
                "rr3---sn-user-b.googlevideo.com",
            ),
        )

    def test_uses_third_control_video_when_first_two_have_no_stream_host(self) -> None:
        pages = [
            "YouTube page without streams",
            "Another YouTube page without streams",
            "https://rr4---sn-fresh-user.googlevideo.com/videoplayback",
        ]

        with patch(
            "blockcheck.googlevideo_discovery._fetch_watch_page",
            side_effect=pages,
        ) as fetch:
            result = discover_googlevideo_host()

        self.assertEqual(result.host, "rr4---sn-fresh-user.googlevideo.com")
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(
            fetch.call_args_list[-1].args[0],
            "https://www.youtube.com/watch?v=Qr1zDbHATw0&t=8s&hl=en",
        )

    def test_each_run_discovers_again_instead_of_reusing_old_host(self) -> None:
        pages = [
            "https://rr1---sn-first-user.googlevideo.com/videoplayback",
            "https://rr9---sn-second-user.googlevideo.com/videoplayback",
        ]

        with patch(
            "blockcheck.googlevideo_discovery._fetch_watch_page",
            side_effect=pages,
        ) as fetch:
            first = discover_googlevideo_host()
            second = discover_googlevideo_host()

        self.assertEqual(first.host, "rr1---sn-first-user.googlevideo.com")
        self.assertEqual(second.host, "rr9---sn-second-user.googlevideo.com")
        self.assertEqual(fetch.call_count, 2)

    def test_cancelled_run_does_not_start_discovery_request(self) -> None:
        with patch("blockcheck.googlevideo_discovery._fetch_watch_page") as fetch:
            result = discover_googlevideo_host(cancelled=lambda: True)

        self.assertIsNone(result.host)
        self.assertEqual(result.detail, "проверка остановлена")
        fetch.assert_not_called()

    def test_two_control_videos_share_one_timeout_budget(self) -> None:
        def slow_empty_page(*_args) -> str:
            time.sleep(0.12)
            return "YouTube page without streams"

        with patch(
            "blockcheck.googlevideo_discovery._fetch_watch_page",
            side_effect=slow_empty_page,
        ) as fetch:
            result = discover_googlevideo_host(timeout=0.1)

        self.assertIn("лимит времени", result.detail)
        self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
