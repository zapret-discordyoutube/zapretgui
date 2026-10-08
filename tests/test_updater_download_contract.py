from __future__ import annotations

"""Скачивание установщика на настоящих локальных HTTP-серверах.

Проверяется то, ради чего загрузчик качает со всех источников сразу:
медленный источник не тормозит быстрый, обрыв не теряет уже полученное,
застрявший кусок перехватывается, мёртвый сервер никого не ждёт,
испорченное зеркало не мешает исправному, ошибка записи на диск не гоняет
загрузку по зеркалам.
"""

import hashlib
import http.server
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from updater.download import engine
from updater.download.downloader import (
    CancellationToken,
    DownloadSource,
    LocalWriteError,
    UpdateArtifact,
    UpdateCancelled,
    UpdateIntegrityError,
    UpdatePipelineError,
    build_download_sources,
    download_artifact,
    verify_file,
)
from updater.release import mirrors
from updater.release_contract import ReleaseArtifactMetadata, normalize_sha256

PAYLOAD = os.urandom(5 * 1024 * 1024 + 12345)
SEND_STEP = 16 * 1024


class _Server:
    """Локальный сервер с одним файлом и настраиваемым «характером»."""

    def __init__(
        self,
        body: bytes | None = PAYLOAD,
        *,
        delay_per_step: float = 0.0,
        cut_after: int | None = None,
        stall_after: int | None = None,
        ranges: bool = True,
    ) -> None:
        self.body = body
        self.delay_per_step = delay_per_step
        # Обрывать каждое соединение после стольких байт.
        self.cut_after = cut_after
        # Замирать после стольких байт, не закрывая соединение.
        self.stall_after = stall_after
        self.ranges = ranges
        self.sent = 0
        self.requests = 0
        self.released = threading.Event()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                owner.requests += 1
                if owner.body is None:
                    self.send_error(404)
                    return
                header = self.headers.get("Range") if owner.ranges else None
                if header:
                    start_text, end_text = header.split("=", 1)[1].split("-", 1)
                    start, end = int(start_text), int(end_text)
                    chunk = owner.body[start : end + 1]
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(owner.body)}")
                else:
                    chunk = owner.body
                    self.send_response(200)
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                try:
                    for offset in range(0, len(chunk), SEND_STEP):
                        if owner.cut_after is not None and offset >= owner.cut_after:
                            self.connection.shutdown(socket.SHUT_RDWR)
                            return
                        if owner.stall_after is not None and offset >= owner.stall_after:
                            owner.released.wait(20)
                            return
                        if owner.delay_per_step:
                            time.sleep(owner.delay_per_step)
                        part = chunk[offset : offset + SEND_STEP]
                        self.wfile.write(part)
                        self.wfile.flush()
                        owner.sent += len(part)
                except OSError:
                    pass

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}/setup.exe"

    def source(self, name: str) -> DownloadSource:
        return DownloadSource(self.url, False, name=name)

    def close(self) -> None:
        self.released.set()
        self._httpd.shutdown()
        self._httpd.server_close()


def _closed_port_url() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}/setup.exe"


class DownloaderOnLocalServersTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.target = Path(self._temp.name) / "Zapret2Setup.exe.part"
        self.target.write_bytes(b"")
        # Мелкие куски — чтобы в небольшом файле их было несколько; повторы
        # после пропажи сети в тестах не должны длиться секунды.
        for name, value in (("PIECE_SIZE", 1024 * 1024), ("REVIVAL_PAUSES_SECONDS", (0.05,))):
            patcher = patch.object(engine, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _server(self, *args, **kwargs) -> _Server:
        server = _Server(*args, **kwargs)
        self.addCleanup(server.close)
        return server

    def _artifact(self, *sources: DownloadSource) -> UpdateArtifact:
        return UpdateArtifact(
            version="21.1.5.80",
            file_name="Zapret2Setup_DEV_21_1_5_80.exe",
            expected_size=len(PAYLOAD),
            expected_sha256=hashlib.sha256(PAYLOAD).hexdigest(),
            sources=tuple(sources),
        )

    def _download(self, *sources: DownloadSource, on_progress=None) -> None:
        download_artifact(self._artifact(*sources), self.target, token=CancellationToken(), on_progress=on_progress)

    def test_pieces_land_in_place_in_one_file(self) -> None:
        progress: list[tuple[int, int, int]] = []

        self._download(self._server().source("A"), on_progress=lambda *values: progress.append(values))

        self.assertEqual(self.target.read_bytes(), PAYLOAD)
        self.assertEqual(progress[-1], (100, len(PAYLOAD), len(PAYLOAD)))
        self.assertEqual(sorted(os.listdir(self._temp.name)), ["Zapret2Setup.exe.part"])

    def test_slow_source_does_not_hold_back_a_fast_one(self) -> None:
        """Раньше загрузка шла с первого источника до конца, как бы медленно он ни отдавал."""
        slow = self._server(delay_per_step=0.05)  # ~320 КБ/с: весь файл занял бы ~16 с
        fast = self._server()

        started = time.monotonic()
        self._download(slow.source("медленный"), fast.source("быстрый"))
        elapsed = time.monotonic() - started

        self.assertEqual(self.target.read_bytes(), PAYLOAD)
        self.assertLess(elapsed, 8.0)
        self.assertGreater(fast.sent, slow.sent * 3)

    def test_stalled_piece_is_taken_over_by_a_free_connection(self) -> None:
        """Соединение замерло, не оборвавшись: его кусок докачивает другой источник."""
        stalled = self._server(stall_after=64 * 1024)
        fast = self._server()

        with patch.object(engine, "HEDGE_MIN_AGE_SECONDS", 0.3):
            started = time.monotonic()
            self._download(stalled.source("замерший"), fast.source("быстрый"))
            elapsed = time.monotonic() - started

        self.assertEqual(self.target.read_bytes(), PAYLOAD)
        # Без перехвата пришлось бы ждать тайм-аут чтения — 15 секунд.
        self.assertLess(elapsed, 6.0)

    def test_broken_connection_keeps_the_bytes_already_received(self) -> None:
        """Источник рвёт каждое соединение на середине: полученное не качается заново."""
        flaky = self._server(cut_after=256 * 1024)
        seen: list[int] = []

        self._download(flaky.source("рвущийся"), self._server().source("ровный"), on_progress=lambda _p, done, _t: seen.append(done))

        self.assertEqual(self.target.read_bytes(), PAYLOAD)
        self.assertEqual(seen, sorted(seen))

    def test_dead_server_does_not_delay_the_download(self) -> None:
        dead = DownloadSource(_closed_port_url(), False, name="мёртвый")

        started = time.monotonic()
        self._download(dead, self._server().source("живой"))

        self.assertEqual(self.target.read_bytes(), PAYLOAD)
        self.assertLess(time.monotonic() - started, 5.0)

    def test_fallback_address_is_used_when_the_main_one_is_closed(self) -> None:
        live = self._server()
        source = DownloadSource(_closed_port_url(), False, name="зеркало", fallback_url=live.url)

        self._download(source)

        self.assertEqual(self.target.read_bytes(), PAYLOAD)

    def test_corrupt_mirror_does_not_spoil_a_good_one(self) -> None:
        corrupt = self._server(bytes(len(PAYLOAD)))

        self._download(corrupt.source("испорченное"), self._server().source("исправное"))

        self.assertEqual(self.target.read_bytes(), PAYLOAD)

    def test_wrong_size_on_server_is_rejected_at_the_first_answer(self) -> None:
        short = self._server(PAYLOAD[:-1])

        with self.assertRaisesRegex(UpdatePipelineError, "другого размера"):
            self._download(short.source("короткий"))

        self.assertEqual(short.requests, 1)

    def test_all_sources_failing_reports_every_reason(self) -> None:
        missing = self._server(None)
        corrupt = self._server(bytes(len(PAYLOAD)))

        with self.assertRaisesRegex(UpdatePipelineError, "SHA-256"):
            self._download(missing.source("пустой"), corrupt.source("испорченный"))

        with self.assertRaisesRegex(UpdatePipelineError, "пустой — сервер ответил HTTP 404"):
            self._download(missing.source("пустой"))

    def test_server_without_ranges_is_downloaded_in_one_stream(self) -> None:
        plain = self._server(ranges=False)

        self._download(plain.source("без кусков"))

        self.assertEqual(self.target.read_bytes(), PAYLOAD)

    def test_local_write_error_does_not_walk_other_mirrors(self) -> None:
        first, second = self._server(), self._server()

        with patch.object(engine, "_open_target", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(LocalWriteError):
                self._download(first.source("A"), second.source("B"))

        self.assertEqual(first.requests + second.requests, 0)

    def test_cancellation_stops_download(self) -> None:
        token = CancellationToken()
        slow = self._server(delay_per_step=0.05)
        threading.Timer(0.3, token.cancel).start()

        started = time.monotonic()
        with self.assertRaises(UpdateCancelled):
            download_artifact(self._artifact(slow.source("медленный")), self.target, token=token)

        self.assertLess(time.monotonic() - started, 3.0)


class UpdaterDownloadContractTests(unittest.TestCase):
    def test_every_server_is_one_source_with_http_as_fallback(self) -> None:
        servers = [
            {"id": "a", "name": "VPS 1", "host": "a.example", "https_port": 888, "http_port": 887},
            {"id": "b", "name": "VPS 2", "host": "b.example", "https_port": 888, "http_port": 887},
        ]
        metadata = ReleaseArtifactMetadata.from_mapping(
            {
                "version": "21.1.5.80",
                "update_url": "https://b.example:888/download/file.exe",
                "file_name": "file.exe",
                "file_size": 10,
                "sha256": "ab" * 32,
                "verify_ssl": False,
            }
        )

        with patch.object(mirrors, "VPS_SERVERS", servers):
            sources = build_download_sources(metadata)

        self.assertEqual(
            [(source.name, source.url, source.fallback_url) for source in sources],
            [
                ("VPS 2", "https://b.example:888/download/file.exe", ""),
                ("VPS 1", "https://a.example:888/download/file.exe", "http://a.example:887/download/file.exe"),
            ],
        )

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
