"""Скачивание пресета по ссылке: валидация URL, имена, лимиты, ошибки."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from presets.preset_url_import import (
    ERROR_KIND_CANCELLED,
    ERROR_KIND_CONTENT,
    ERROR_KIND_HTTP,
    ERROR_KIND_NETWORK,
    ERROR_KIND_SSL,
    ERROR_KIND_TIMEOUT,
    ERROR_KIND_TOO_LARGE,
    ERROR_KIND_URL,
    MAX_DOWNLOAD_BYTES,
    PresetUrlDownloadError,
    cleanup_download,
    download_preset_from_url,
    is_https_preset_import_url,
    is_managed_download_path,
    preset_file_stem_from_url,
    validate_preset_import_url,
)


class _FakeResponse:
    def __init__(self, *, status_code=200, headers=None, chunks=None, iter_error=None):
        self.status_code = status_code
        self.headers = dict(headers or {})
        self._chunks = list(chunks or [])
        self._iter_error = iter_error
        self.closed = False

    def iter_content(self, chunk_size):
        for chunk in self._chunks:
            yield chunk
        if self._iter_error is not None:
            raise self._iter_error

    def close(self):
        self.closed = True


def _patch_get(response=None, error=None):
    def _fake(url, **kwargs):
        if error is not None:
            raise error
        return response

    return patch("presets.preset_url_import.request_get_bypass_proxy", side_effect=_fake)


class ValidateUrlTests(unittest.TestCase):
    def test_accepts_http_and_https(self):
        self.assertEqual(validate_preset_import_url("https://example.com/p.txt"), "")
        self.assertEqual(validate_preset_import_url("http://example.com/p.txt"), "")

    def test_rejects_empty_other_schemes_and_no_host(self):
        self.assertNotEqual(validate_preset_import_url(""), "")
        self.assertNotEqual(validate_preset_import_url("ftp://example.com/p.txt"), "")
        self.assertNotEqual(validate_preset_import_url("file:///etc/passwd"), "")
        self.assertNotEqual(validate_preset_import_url("https:///p.txt"), "")
        self.assertNotEqual(validate_preset_import_url("просто текст"), "")

    def test_is_https_helper(self):
        self.assertTrue(is_https_preset_import_url("https://example.com/a"))
        self.assertFalse(is_https_preset_import_url("http://example.com/a"))
        self.assertFalse(is_https_preset_import_url(""))


class FileStemTests(unittest.TestCase):
    def test_prefers_content_disposition(self):
        stem = preset_file_stem_from_url(
            "https://example.com/raw/abc",
            {"Content-Disposition": 'attachment; filename="My Preset.txt"'},
        )
        self.assertEqual(stem, "My Preset")

    def test_content_disposition_rfc5987(self):
        stem = preset_file_stem_from_url(
            "https://example.com/raw/abc",
            {"Content-Disposition": "attachment; filename*=UTF-8''%D0%94%D0%BE%D0%BC.txt"},
        )
        self.assertEqual(stem, "Дом")

    def test_falls_back_to_url_segment(self):
        stem = preset_file_stem_from_url("https://example.com/presets/tv-preset.txt?raw=1")
        self.assertEqual(stem, "tv-preset")

    def test_url_encoded_segment(self):
        stem = preset_file_stem_from_url("https://example.com/%D0%94%D0%BE%D0%BC.txt")
        self.assertEqual(stem, "Дом")

    def test_sanitizes_windows_forbidden_chars(self):
        stem = preset_file_stem_from_url(
            "https://example.com/x",
            {"Content-Disposition": 'attachment; filename="a<b>c:d.txt"'},
        )
        self.assertNotRegex(stem, r'[\\/:*?"<>|]')

    def test_fallback_when_nothing_usable(self):
        self.assertEqual(preset_file_stem_from_url("https://example.com/"), "Imported")


class DownloadTests(unittest.TestCase):
    def _cleanup(self, result):
        cleanup_download(result.file_path)

    def test_downloads_txt(self):
        response = _FakeResponse(chunks=[b"--wf-tcp-out=443\n"])
        with _patch_get(response):
            result = download_preset_from_url("https://example.com/mine.txt")
        self.addCleanup(self._cleanup, result)
        path = Path(result.file_path)
        self.assertTrue(path.exists())
        self.assertEqual(path.name, "mine.txt")
        self.assertEqual(result.suffix, ".txt")
        self.assertEqual(result.source_url, "https://example.com/mine.txt")
        self.assertTrue(is_managed_download_path(result.file_path))
        self.assertTrue(response.closed)

    def test_zip_by_magic_bytes(self):
        response = _FakeResponse(chunks=[b"PK\x03\x04rest-of-zip"])
        with _patch_get(response):
            result = download_preset_from_url("https://example.com/bundle")
        self.addCleanup(self._cleanup, result)
        self.assertEqual(result.suffix, ".zip")

    def test_invalid_url_kind(self):
        with self.assertRaises(PresetUrlDownloadError) as ctx:
            download_preset_from_url("ftp://example.com/p.txt")
        self.assertEqual(ctx.exception.kind, ERROR_KIND_URL)

    def test_http_error_status(self):
        with _patch_get(_FakeResponse(status_code=404)):
            with self.assertRaises(PresetUrlDownloadError) as ctx:
                download_preset_from_url("https://example.com/p.txt")
        self.assertEqual(ctx.exception.kind, ERROR_KIND_HTTP)

    def test_too_large_by_content_length_before_read(self):
        response = _FakeResponse(
            headers={"Content-Length": str(MAX_DOWNLOAD_BYTES + 1)},
            chunks=[b"x"],
        )
        with _patch_get(response):
            with self.assertRaises(PresetUrlDownloadError) as ctx:
                download_preset_from_url("https://example.com/p.txt")
        self.assertEqual(ctx.exception.kind, ERROR_KIND_TOO_LARGE)

    def test_too_large_by_streamed_bytes(self):
        chunk = b"x" * (1024 * 1024)
        response = _FakeResponse(chunks=[chunk] * 11)
        with _patch_get(response):
            with self.assertRaises(PresetUrlDownloadError) as ctx:
                download_preset_from_url("https://example.com/p.txt")
        self.assertEqual(ctx.exception.kind, ERROR_KIND_TOO_LARGE)

    def test_empty_content(self):
        with _patch_get(_FakeResponse(chunks=[b"   \n"])):
            with self.assertRaises(PresetUrlDownloadError) as ctx:
                download_preset_from_url("https://example.com/p.txt")
        self.assertEqual(ctx.exception.kind, ERROR_KIND_CONTENT)

    def test_cancel_between_chunks(self):
        response = _FakeResponse(chunks=[b"a", b"b"])
        calls = {"n": 0}

        def cancel():
            calls["n"] += 1
            return calls["n"] > 1

        with _patch_get(response):
            with self.assertRaises(PresetUrlDownloadError) as ctx:
                download_preset_from_url("https://example.com/p.txt", cancel_cb=cancel)
        self.assertEqual(ctx.exception.kind, ERROR_KIND_CANCELLED)

    def test_ssl_timeout_network_kinds(self):
        cases = [
            (requests.exceptions.SSLError("bad cert"), ERROR_KIND_SSL),
            (requests.exceptions.ConnectTimeout("slow"), ERROR_KIND_TIMEOUT),
            (requests.exceptions.ConnectionError("refused"), ERROR_KIND_NETWORK),
        ]
        for error, expected_kind in cases:
            with self.subTest(kind=expected_kind):
                with _patch_get(error=error):
                    with self.assertRaises(PresetUrlDownloadError) as ctx:
                        download_preset_from_url("https://example.com/p.txt")
                self.assertEqual(ctx.exception.kind, expected_kind)

    def test_read_timeout_mid_stream(self):
        response = _FakeResponse(
            chunks=[b"partial"],
            iter_error=requests.exceptions.ReadTimeout("stalled"),
        )
        with _patch_get(response):
            with self.assertRaises(PresetUrlDownloadError) as ctx:
                download_preset_from_url("https://example.com/p.txt")
        self.assertEqual(ctx.exception.kind, ERROR_KIND_TIMEOUT)


class CleanupTests(unittest.TestCase):
    def test_cleanup_removes_managed_dir_only(self):
        response = _FakeResponse(chunks=[b"--wf-tcp-out=443\n"])
        with _patch_get(response):
            result = download_preset_from_url("https://example.com/p.txt")
        download_dir = Path(result.file_path).parent
        cleanup_download(result.file_path)
        self.assertFalse(download_dir.exists())

    def test_cleanup_ignores_foreign_paths(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            foreign = Path(tmp) / "user-preset.txt"
            foreign.write_text("--wf-tcp-out=443\n", encoding="utf-8")
            self.assertFalse(is_managed_download_path(foreign))
            cleanup_download(foreign)
            self.assertTrue(foreign.exists())


if __name__ == "__main__":
    unittest.main()
