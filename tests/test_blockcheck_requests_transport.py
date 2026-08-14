"""BlockCheck использует общий HTTP-механизм приложения без отдельного httpx."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_SRC = PROJECT_ROOT / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from blockcheck.config import ISP_REDIRECT_MARKERS  # noqa: E402
from blockcheck.dns_integrity import resolve_doh  # noqa: E402
from blockcheck.googlevideo_discovery import _fetch_watch_page  # noqa: E402
from blockcheck.isp_page_detector import detect_isp_page  # noqa: E402
from blockcheck.models import TestStatus as BlockcheckStatus  # noqa: E402
from blockcheck.tcp_test import check_tcp_16_20_single  # noqa: E402


class _Response:
    def __init__(
        self,
        *,
        chunks: tuple[bytes, ...] = (),
        json_data: dict | None = None,
        text: str = "",
        status_code: int = 200,
        history: tuple[object, ...] = (),
    ) -> None:
        self._chunks = chunks
        self._json_data = json_data or {}
        self.text = text
        self.status_code = status_code
        self.history = history

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        self.chunk_size = chunk_size
        yield from self._chunks

    def json(self) -> dict:
        return self._json_data


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.max_redirects = None
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class RequestsDependencyBoundaryTests(unittest.TestCase):
    def test_retired_httpx_is_absent_from_runtime_and_blockcheck(self) -> None:
        runtime = (PROJECT_ROOT / "requirements-runtime.txt").read_text("utf-8")
        sources = "\n".join(
            (PROJECT_SRC / "blockcheck" / name).read_text("utf-8")
            for name in (
                "dns_integrity.py",
                "googlevideo_discovery.py",
                "isp_page_detector.py",
                "tcp_test.py",
            )
        )

        self.assertNotIn("httpx", runtime.lower())
        self.assertNotIn("httpx", sources.lower())

    def test_doh_keeps_json_a_record_filter(self) -> None:
        response = _Response(json_data={
            "Answer": [
                {"type": 1, "data": "1.2.3.4"},
                {"type": 28, "data": "2001:db8::1"},
            ]
        })
        session = _Session(response)

        with patch("requests.Session", return_value=session):
            result = resolve_doh("example.org", "https://dns.example/query", timeout=3)

        self.assertEqual(result, ["1.2.3.4"])
        self.assertEqual(session.calls[0][1]["timeout"], 3)
        self.assertTrue(session.calls[0][1]["allow_redirects"])

    def test_googlevideo_download_stays_streamed_and_limited(self) -> None:
        host = b"https://rr7---sn-user.googlevideo.com/videoplayback"
        session = _Session(_Response(chunks=(b"prefix", host, b"unused")))

        with patch("requests.Session", return_value=session):
            page = _fetch_watch_page("https://youtube.example/watch", 4, lambda: False)

        self.assertIn("rr7---sn-user.googlevideo.com", page)
        self.assertTrue(session.calls[0][1]["stream"])
        self.assertEqual(session.max_redirects, 5)

    def test_isp_redirect_is_still_detected(self) -> None:
        marker = ISP_REDIRECT_MARKERS[0]
        redirect = type("Redirect", (), {"headers": {"location": f"https://{marker}/"}})()
        session = _Session(_Response(history=(redirect,)))

        with patch("requests.Session", return_value=session):
            result = detect_isp_page("blocked.example", timeout=5)

        self.assertEqual(result.status, BlockcheckStatus.FAIL)
        self.assertEqual(result.error_code, "ISP_PAGE")
        self.assertFalse(session.calls[0][1]["verify"])
        self.assertEqual(session.max_redirects, 5)

    def test_tcp_boundary_is_counted_from_streamed_chunks(self) -> None:
        session = _Session(_Response(chunks=(b"a" * 9000, b"b" * 9000)))

        with patch("requests.Session", return_value=session):
            result = check_tcp_16_20_single("https://large.example/file", timeout=6)

        self.assertEqual(result.status, BlockcheckStatus.FAIL)
        self.assertEqual(result.error_code, "TCP_16_20")
        self.assertEqual(result.raw_data["bytes_received"], 18_000)
        self.assertTrue(session.calls[0][1]["stream"])


if __name__ == "__main__":
    unittest.main()
