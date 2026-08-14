"""Ядро автосинка удалённых пресетов: решения, хэши, лимиты, конфликты."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import requests

from presets.remote_sync import (
    AUTO_CHECK_INTERVAL_SECONDS,
    MAX_REMOTE_PRESET_BYTES,
    STATUS_DETACHED,
    STATUS_ERROR,
    STATUS_NOT_MODIFIED,
    STATUS_SKIPPED,
    STATUS_UNCHANGED,
    STATUS_UPDATED,
    RemoteFetchResult,
    RemoteSyncFetchError,
    comparison_hash,
    fetch_remote_preset_text,
    should_auto_check,
    sync_remote_preset,
)

NOW_ISO = "2026-08-14T12:00:00Z"

VALID_WINWS2_TEXT = (
    "# Preset: Example\n"
    "--wf-tcp-out=80,443\n"
    "--lua-desync=hostfakesplit:host=x.com:tcp_ts=-1000\n"
)

UPDATED_WINWS2_TEXT = (
    "# Preset: Example\n"
    "--wf-tcp-out=80,443,8080\n"
    "--lua-desync=hostfakesplit:host=y.com:tcp_ts=-500\n"
)


def _binding(**overrides):
    base = {
        "url": "https://example.com/p.txt",
        "etag": "",
        "last_modified": "",
        "synced_hash": comparison_hash(VALID_WINWS2_TEXT),
        "checked_at": "",
        "updated_at": "",
        "error": "",
        "auto": True,
        "detached": False,
    }
    base.update(overrides)
    return base


def _sync(binding, *, current_text=VALID_WINWS2_TEXT, fetch=None, save=None, force=False):
    save = save or Mock(return_value=None)
    outcome = sync_remote_preset(
        binding,
        engine="winws2",
        read_current_text=lambda: current_text,
        fetch=fetch or Mock(return_value=RemoteFetchResult(status_code=304)),
        save_text=save,
        now_iso=NOW_ISO,
        force=force,
    )
    return outcome, save


class ComparisonHashTests(unittest.TestCase):
    def test_ignores_comments_and_debug_line(self):
        base = comparison_hash(VALID_WINWS2_TEXT)
        renamed = VALID_WINWS2_TEXT.replace("# Preset: Example", "# Preset: Renamed")
        with_debug = VALID_WINWS2_TEXT + "--debug=@C:\\logs\\preset.log\n"
        crlf = VALID_WINWS2_TEXT.replace("\n", "\r\n")
        self.assertEqual(comparison_hash(renamed), base)
        self.assertEqual(comparison_hash(with_debug), base)
        self.assertEqual(comparison_hash(crlf), base)

    def test_detects_meaningful_change(self):
        self.assertNotEqual(
            comparison_hash(VALID_WINWS2_TEXT), comparison_hash(UPDATED_WINWS2_TEXT)
        )


class ShouldAutoCheckTests(unittest.TestCase):
    def _parse(self, value):
        return {"old": 0.0, "fresh": 1000.0}.get(value)

    def test_never_checked_is_due(self):
        self.assertTrue(should_auto_check(_binding(), now_ts=1.0, parse_ts=self._parse))

    def test_interval_gate(self):
        binding = _binding(checked_at="old")
        self.assertTrue(
            should_auto_check(binding, now_ts=AUTO_CHECK_INTERVAL_SECONDS + 1, parse_ts=self._parse)
        )
        binding = _binding(checked_at="fresh")
        self.assertFalse(should_auto_check(binding, now_ts=1500.0, parse_ts=self._parse))

    def test_disabled_or_detached(self):
        self.assertFalse(should_auto_check(_binding(auto=False), now_ts=1e12, parse_ts=self._parse))
        self.assertFalse(should_auto_check(_binding(detached=True), now_ts=1e12, parse_ts=self._parse))

    def test_unparsable_checked_at_is_due(self):
        self.assertTrue(
            should_auto_check(_binding(checked_at="garbage"), now_ts=1.0, parse_ts=self._parse)
        )


class SyncRemotePresetTests(unittest.TestCase):
    def test_not_modified_updates_checked_at_only(self):
        binding = _binding(etag='W/"1"')
        fetch = Mock(return_value=RemoteFetchResult(status_code=304))
        outcome, save = _sync(binding, fetch=fetch)
        self.assertEqual(outcome.status, STATUS_NOT_MODIFIED)
        self.assertEqual(outcome.binding_updates["checked_at"], NOW_ISO)
        save.assert_not_called()
        fetch.assert_called_once_with("https://example.com/p.txt", 'W/"1"', "")

    def test_unchanged_content_does_not_save(self):
        fetch = Mock(
            return_value=RemoteFetchResult(status_code=200, text=VALID_WINWS2_TEXT, etag='W/"2"')
        )
        outcome, save = _sync(_binding(), fetch=fetch)
        self.assertEqual(outcome.status, STATUS_UNCHANGED)
        self.assertEqual(outcome.binding_updates["etag"], 'W/"2"')
        save.assert_not_called()

    def test_updated_content_saves_via_pipeline(self):
        fetch = Mock(return_value=RemoteFetchResult(status_code=200, text=UPDATED_WINWS2_TEXT))
        outcome, save = _sync(_binding(), fetch=fetch)
        self.assertEqual(outcome.status, STATUS_UPDATED)
        save.assert_called_once_with(UPDATED_WINWS2_TEXT)
        self.assertEqual(outcome.binding_updates["updated_at"], NOW_ISO)
        self.assertEqual(
            outcome.binding_updates["synced_hash"], comparison_hash(UPDATED_WINWS2_TEXT)
        )
        self.assertFalse(outcome.binding_updates["detached"])

    def test_synced_hash_uses_saved_normalized_text(self):
        normalized = UPDATED_WINWS2_TEXT + "# normalized-marker\n"
        fetch = Mock(return_value=RemoteFetchResult(status_code=200, text=UPDATED_WINWS2_TEXT))
        save = Mock(return_value=normalized)
        outcome, _ = _sync(_binding(), fetch=fetch, save=save)
        self.assertEqual(outcome.binding_updates["synced_hash"], comparison_hash(normalized))

    def test_invalid_remote_text_is_error_without_save(self):
        fetch = Mock(return_value=RemoteFetchResult(status_code=200, text="случайный текст\n"))
        outcome, save = _sync(_binding(), fetch=fetch)
        self.assertEqual(outcome.status, STATUS_ERROR)
        self.assertIn("не похож на пресет", outcome.binding_updates["error"])
        save.assert_not_called()

    def test_winws2_requires_lua_desync(self):
        no_lua = "# Preset: X\n--wf-tcp-out=443\n"
        fetch = Mock(return_value=RemoteFetchResult(status_code=200, text=no_lua))
        outcome, save = _sync(_binding(), fetch=fetch)
        self.assertEqual(outcome.status, STATUS_ERROR)
        save.assert_not_called()

    def test_local_edit_detaches_without_touching_file(self):
        edited = VALID_WINWS2_TEXT + "--filter-tcp=8080\n"
        fetch = Mock()
        outcome, save = _sync(_binding(), current_text=edited, fetch=fetch)
        self.assertEqual(outcome.status, STATUS_DETACHED)
        self.assertTrue(outcome.binding_updates["detached"])
        fetch.assert_not_called()
        save.assert_not_called()

    def test_detached_binding_is_skipped(self):
        edited = VALID_WINWS2_TEXT + "--filter-tcp=8080\n"
        outcome, save = _sync(_binding(detached=True), current_text=edited)
        self.assertEqual(outcome.status, STATUS_SKIPPED)
        self.assertEqual(outcome.binding_updates, {})
        save.assert_not_called()

    def test_force_overwrites_local_edits_and_reattaches(self):
        edited = VALID_WINWS2_TEXT + "--filter-tcp=8080\n"
        fetch = Mock(return_value=RemoteFetchResult(status_code=200, text=UPDATED_WINWS2_TEXT))
        outcome, save = _sync(_binding(detached=True), current_text=edited, fetch=fetch, force=True)
        self.assertEqual(outcome.status, STATUS_UPDATED)
        save.assert_called_once_with(UPDATED_WINWS2_TEXT)
        self.assertFalse(outcome.binding_updates["detached"])
        fetch.assert_called_once_with("https://example.com/p.txt", "", "")

    def test_fetch_error_recorded_in_binding(self):
        fetch = Mock(side_effect=RemoteSyncFetchError("HTTP 404"))
        outcome, save = _sync(_binding(), fetch=fetch)
        self.assertEqual(outcome.status, STATUS_ERROR)
        self.assertEqual(outcome.binding_updates["error"], "HTTP 404")
        save.assert_not_called()

    def test_missing_file_is_error(self):
        outcome = sync_remote_preset(
            _binding(),
            engine="winws2",
            read_current_text=lambda: None,
            fetch=Mock(),
            save_text=Mock(),
            now_iso=NOW_ISO,
        )
        self.assertEqual(outcome.status, STATUS_ERROR)


class _FakeResponse:
    def __init__(self, *, status_code=200, headers=None, chunks=None):
        self.status_code = status_code
        self.headers = dict(headers or {})
        self._chunks = list(chunks or [])
        self.closed = False

    def iter_content(self, chunk_size):
        yield from self._chunks

    def close(self):
        self.closed = True


class FetchRemotePresetTextTests(unittest.TestCase):
    def _patch(self, response=None, error=None):
        def _fake(url, **kwargs):
            if error is not None:
                raise error
            self.last_kwargs = kwargs
            return response

        return patch("presets.remote_sync.request_get_bypass_proxy", side_effect=_fake)

    def test_https_only(self):
        with self.assertRaises(RemoteSyncFetchError):
            fetch_remote_preset_text("http://example.com/p.txt")

    def test_conditional_headers_sent(self):
        response = _FakeResponse(status_code=304)
        with self._patch(response):
            result = fetch_remote_preset_text(
                "https://example.com/p.txt", etag='W/"9"', last_modified="Thu, 01 Jan 2026"
            )
        self.assertEqual(result.status_code, 304)
        headers = self.last_kwargs["headers"]
        self.assertEqual(headers["If-None-Match"], 'W/"9"')
        self.assertEqual(headers["If-Modified-Since"], "Thu, 01 Jan 2026")

    def test_success_returns_text_and_validators(self):
        response = _FakeResponse(
            headers={"ETag": 'W/"5"', "Last-Modified": "Fri, 02 Jan 2026"},
            chunks=[VALID_WINWS2_TEXT.encode("utf-8")],
        )
        with self._patch(response):
            result = fetch_remote_preset_text("https://example.com/p.txt")
        self.assertEqual(result.text, VALID_WINWS2_TEXT)
        self.assertEqual(result.etag, 'W/"5"')
        self.assertEqual(result.last_modified, "Fri, 02 Jan 2026")
        self.assertTrue(response.closed)

    def test_size_limit(self):
        big = b"x" * (MAX_REMOTE_PRESET_BYTES + 1)
        with self._patch(_FakeResponse(chunks=[big])):
            with self.assertRaises(RemoteSyncFetchError):
                fetch_remote_preset_text("https://example.com/p.txt")

    def test_zip_content_rejected(self):
        with self._patch(_FakeResponse(chunks=[b"PK\x03\x04zip"])):
            with self.assertRaises(RemoteSyncFetchError):
                fetch_remote_preset_text("https://example.com/p.txt")

    def test_http_status_error(self):
        with self._patch(_FakeResponse(status_code=500)):
            with self.assertRaises(RemoteSyncFetchError):
                fetch_remote_preset_text("https://example.com/p.txt")

    def test_network_error(self):
        with self._patch(error=requests.exceptions.ConnectionError("refused")):
            with self.assertRaises(RemoteSyncFetchError):
                fetch_remote_preset_text("https://example.com/p.txt")


if __name__ == "__main__":
    unittest.main()
