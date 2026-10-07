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

from blockcheck.googlevideo_discovery import _fetch_watch_page  # noqa: E402


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
        sources = (PROJECT_SRC / "blockcheck" / "googlevideo_discovery.py").read_text("utf-8")

        self.assertNotIn("httpx", runtime.lower())
        self.assertNotIn("httpx", sources.lower())

    def test_googlevideo_download_stays_streamed_and_limited(self) -> None:
        host = b"https://rr7---sn-user.googlevideo.com/videoplayback"
        session = _Session(_Response(chunks=(b"prefix", host, b"unused")))

        with patch("requests.Session", return_value=session):
            page = _fetch_watch_page("https://youtube.example/watch", 4, lambda: False)

        self.assertIn("rr7---sn-user.googlevideo.com", page)
        self.assertTrue(session.calls[0][1]["stream"])
        self.assertEqual(session.max_redirects, 5)


if __name__ == "__main__":
    unittest.main()
