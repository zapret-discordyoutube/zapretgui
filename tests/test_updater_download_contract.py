from __future__ import annotations

"""Скачивание установщика на настоящем локальном HTTP-сервере.

Проверяется то, что ломалось или могло сломаться: куски пишутся на свои
места в одном файле, испорченное зеркало не мешает следующему, размер на
сервере сверяется до скачивания, ошибка записи на диск не гоняет загрузку по
всем зеркалам.
"""

import hashlib
import http.server
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from updater.download import downloader
from updater.download.downloader import (
    CancellationToken,
    DownloadSource,
    LocalWriteError,
    ThrottledProgress,
    UpdateArtifact,
    UpdateCancelled,
    UpdateIntegrityError,
    UpdatePipelineError,
    download_artifact,
    verify_file,
)
from updater.release_contract import normalize_sha256

PAYLOAD = os.urandom(5 * 1024 * 1024 + 12345)


class _Handler(http.server.BaseHTTPRequestHandler):
    files: dict[str, bytes] = {}
    requests_log: list[str] = []

    def log_message(self, *_args) -> None:
        pass

    def _body(self) -> bytes | None:
        return self.files.get(self.path)

    def do_HEAD(self) -> None:  # noqa: N802
        body = self._body()
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self.requests_log.append(self.path)
        body = self._body()
        if body is None:
            self.send_error(404)
            return
        header = self.headers.get("Range")
        if header:
            start_text, end_text = header.split("=", 1)[1].split("-", 1)
            start, end = int(start_text), int(end_text)
            chunk = body[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
        else:
            chunk = body
            self.send_response(200)
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)


class DownloaderOnLocalServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _Handler.files = {
            "/good.exe": PAYLOAD,
            "/corrupt.exe": bytes(len(PAYLOAD)),
            "/short.exe": PAYLOAD[:-1],
        }
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        _Handler.requests_log = []
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.target = Path(self._temp.name) / "Zapret2Setup.exe.part"
        self.target.write_bytes(b"")

    def _artifact(self, *paths: str) -> UpdateArtifact:
        return UpdateArtifact(
            version="21.1.5.80",
            file_name="Zapret2Setup_DEV_21_1_5_80.exe",
            expected_size=len(PAYLOAD),
            expected_sha256=hashlib.sha256(PAYLOAD).hexdigest(),
            sources=tuple(DownloadSource(f"{self.base}{path}", False) for path in paths),
        )

    def test_segments_land_in_place_without_merge(self) -> None:
        progress: list[tuple[int, int, int]] = []

        download_artifact(
            self._artifact("/good.exe"),
            self.target,
            token=CancellationToken(),
            on_progress=lambda *values: progress.append(values),
        )

        self.assertEqual(self.target.read_bytes(), PAYLOAD)
        self.assertEqual(progress[-1], (100, len(PAYLOAD), len(PAYLOAD)))
        self.assertEqual(sorted(os.listdir(self._temp.name)), ["Zapret2Setup.exe.part"])

    def test_corrupt_mirror_is_skipped_for_the_next_source(self) -> None:
        download_artifact(self._artifact("/corrupt.exe", "/good.exe"), self.target, token=CancellationToken())

        self.assertEqual(self.target.read_bytes(), PAYLOAD)

    def test_wrong_size_on_server_is_rejected_before_download(self) -> None:
        with self.assertRaisesRegex(UpdatePipelineError, "другого размера"):
            download_artifact(self._artifact("/short.exe"), self.target, token=CancellationToken())

        self.assertEqual(_Handler.requests_log, [])

    def test_all_sources_failing_reports_every_reason(self) -> None:
        with self.assertRaisesRegex(UpdatePipelineError, "SHA-256"):
            download_artifact(self._artifact("/missing.exe", "/corrupt.exe"), self.target, token=CancellationToken())

    def test_failed_segment_stops_its_neighbours(self) -> None:
        """Раньше три остальных куска докачивались до конца впустую."""
        import time

        cancelled_neighbours: list[int] = []

        def flaky(source, path, *, start, end, token, on_chunk):
            if start == 0:
                time.sleep(0.05)
                raise ConnectionError("обрыв первого куска")
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    token.checkpoint()
                except UpdateCancelled:
                    cancelled_neighbours.append(start)
                    raise
                time.sleep(0.01)

        started = time.monotonic()
        with patch.object(downloader, "_download_segment", side_effect=flaky):
            with self.assertRaisesRegex(UpdatePipelineError, "обрыв первого куска"):
                download_artifact(self._artifact("/good.exe"), self.target, token=CancellationToken())

        self.assertEqual(len(cancelled_neighbours), downloader.NUM_SEGMENTS - 1)
        self.assertLess(time.monotonic() - started, 3.0)

    def test_local_write_error_does_not_walk_other_mirrors(self) -> None:
        def broken_write(*_args, **_kwargs):
            raise OSError(28, "No space left on device")

        with patch.object(downloader, "_download_segmented", side_effect=broken_write):
            with self.assertRaises(LocalWriteError):
                download_artifact(self._artifact("/good.exe", "/good.exe?second"), self.target, token=CancellationToken())

    def test_network_error_is_not_mistaken_for_disk_error(self) -> None:
        """Сетевые ошибки requests тоже наследуют OSError."""
        import requests

        self.assertFalse(downloader._is_local_io_error(requests.ConnectionError("net")))
        self.assertTrue(downloader._is_local_io_error(OSError(28, "No space left on device")))

    def test_cancellation_stops_download(self) -> None:
        token = CancellationToken()
        token.cancel()

        with self.assertRaises(UpdateCancelled):
            download_artifact(self._artifact("/good.exe"), self.target, token=token)


class UpdaterDownloadContractTests(unittest.TestCase):
    def test_progress_is_limited_by_time_and_flushes_completion(self) -> None:
        now = [10.0]
        emitted: list[tuple[int, int, int]] = []
        progress = ThrottledProgress(
            lambda percent, done, total: emitted.append((percent, done, total)),
            interval_seconds=0.25,
            clock=lambda: now[0],
        )

        progress.update(1, 100)
        progress.update(2, 100)
        now[0] += 0.24
        progress.update(3, 100)
        now[0] += 0.01
        progress.update(4, 100)
        progress.update(100, 100)

        self.assertEqual(emitted, [(1, 1, 100), (4, 4, 100), (100, 100, 100)])

    def test_sha256_requires_plain_64_character_hex(self) -> None:
        digest = "a" * 64
        self.assertEqual(normalize_sha256(f"sha256:{digest}"), "")
        self.assertEqual(normalize_sha256(digest.upper()), digest)
        self.assertEqual(normalize_sha256("md5:abcd"), "")

    def test_verification_checks_size_and_sha256(self) -> None:
        payload = b"verified installer"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "setup.exe"
            path.write_bytes(payload)
            artifact = UpdateArtifact(
                version="21.1.5.1",
                file_name="setup.exe",
                expected_size=len(payload),
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                sources=(),
            )

            verify_file(path, artifact, CancellationToken())

            wrong = UpdateArtifact(
                version=artifact.version,
                file_name=artifact.file_name,
                expected_size=artifact.expected_size,
                expected_sha256="0" * 64,
                sources=(),
            )
            with self.assertRaises(UpdateIntegrityError):
                verify_file(path, wrong, CancellationToken())

    def test_child_token_sees_parent_cancellation(self) -> None:
        parent = CancellationToken()
        child = CancellationToken(parent=parent)
        parent.cancel()
        with self.assertRaises(UpdateCancelled):
            child.checkpoint()


if __name__ == "__main__":
    unittest.main()
