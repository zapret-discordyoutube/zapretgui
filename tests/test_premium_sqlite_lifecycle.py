from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


class _Api:
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.revoke_fails = False
        self.activated = True
        self.revoke_requests: list[dict] = []

    @staticmethod
    def _response(payload, nonce):
        return {"success": True, "signed": payload, "kid": "test", "sig": "test"}, nonce

    def post_pair_start(self, *, request_id, device_id, device_name=None):
        _ = device_name
        return self._response(
            {
                "type": "zapret_pairing_started",
                "request_id": request_id,
                "device_id": device_id,
                "pairing_id": "pairing-1",
                "pair_code": "ABCDEFGH",
                "pair_expires_at": int(time.time()) + 600,
            },
            "start-nonce",
        )

    def post_pair_finish(self, *, request_id, device_id, pairing_id):
        _ = request_id, pairing_id
        return self._response(
            {
                "type": "zapret_premium_activation",
                "device_id": device_id,
                "binding_id": "binding-1",
                "binding_generation": 1,
                "device_token": "new-secret-token",
                "linked": True,
                "activated": True,
                "subscription_level": "vless_max",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "expires_at_epoch": 4070908800,
                "days_remaining": 100,
                "valid_until": int(time.time()) + 3600,
                "message": "Активировано",
            },
            "finish-nonce",
        )

    def post_check(self, *, request_id, device_id, device_token):
        _ = request_id, device_token
        return self._response(
            {
                "type": "zapret_premium_status",
                "device_id": device_id,
                "binding_id": "binding-1",
                "binding_generation": 1,
                "linked": True,
                "activated": self.activated,
                "subscription_level": "vless_max" if self.activated else None,
                "expires_at": "2099-01-01T00:00:00+00:00" if self.activated else None,
                "expires_at_epoch": 4070908800 if self.activated else None,
                "days_remaining": 100 if self.activated else 0,
                "valid_until": int(time.time()) + 3600,
                "message": "Активировано" if self.activated else "Подписка не активна",
            },
            "status-nonce",
        )

    def post_revoke(self, **payload):
        self.revoke_requests.append(dict(payload))
        if self.revoke_fails:
            return (
                {
                    "success": False,
                    "error": {"code": "network_error", "retryable": True},
                    "_http_status": 0,
                },
                "revoke-nonce",
            )
        return self._response(
            {
                "type": "zapret_premium_device_revoked",
                "device_id": payload["device_id"],
                "binding_id": payload["binding_id"],
                "binding_generation": payload["binding_generation"],
                "revoked": True,
                "already_absent": False,
            },
            "revoke-nonce",
        )


class PremiumSqliteLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        from settings import store

        self.store = store
        self.old_root = store.MAIN_DIRECTORY
        store.MAIN_DIRECTORY = self.tmp.name
        store._SETTINGS_CACHE = None
        store._SETTINGS_CACHE_SIGNATURE = None
        store._SETTINGS_CACHE_MATERIALIZED = False

    def tearDown(self) -> None:
        self.store.MAIN_DIRECTORY = self.old_root
        self.store._SETTINGS_CACHE = None
        self.store._SETTINGS_CACHE_SIGNATURE = None
        self.store._SETTINGS_CACHE_MATERIALIZED = False
        self.tmp.cleanup()

    @staticmethod
    def _verify(raw, *, expected_device_id, expected_nonce=None, **_kwargs):
        signed = raw.get("signed") if isinstance(raw, dict) else None
        if not isinstance(signed, dict):
            return None
        if str(signed.get("device_id")) != str(expected_device_id):
            return None
        _ = expected_nonce
        return signed

    def _service(self):
        from donater.service import PremiumService
        from donater.storage import PremiumStorage

        service = PremiumService(api_base_url="https://premium.test/api")
        service._api = _Api(PremiumStorage.get_device_id())
        return service

    def test_clean_pair_status_and_exact_revoke(self) -> None:
        from donater.storage import PremiumStorage

        service = self._service()
        with patch("donater.service.verify_signed_response", side_effect=self._verify):
            ok, _message, code = service.pair_start(device_name="Test PC")
            self.assertTrue(ok)
            self.assertEqual(code, "ABCDEFGH")
            status = service.check_status()
            self.assertTrue(status.is_activated)
            binding = PremiumStorage.get_binding()
            self.assertEqual(binding["binding_id"], "binding-1")
            self.assertTrue(service.clear_activation())

        self.assertIsNone(PremiumStorage.get_binding(include_disabled=True))
        self.assertEqual(len(service._api.revoke_requests), 1)
        revoke = service._api.revoke_requests[0]
        self.assertEqual(revoke["binding_id"], "binding-1")
        self.assertEqual(revoke["binding_generation"], 1)

    def test_server_inactive_subscription_closes_access(self) -> None:
        service = self._service()
        with patch("donater.service.verify_signed_response", side_effect=self._verify):
            self.assertTrue(service.pair_start()[0])
            self.assertTrue(service.check_status().is_activated)
            service._api.activated = False
            status = service.check_status()
        self.assertFalse(status.is_activated)
        self.assertTrue(status.is_linked)

    def test_failed_revoke_stays_durable_but_closes_local_access(self) -> None:
        from donater.storage import PremiumStorage

        service = self._service()
        with patch("donater.service.verify_signed_response", side_effect=self._verify):
            self.assertTrue(service.pair_start()[0])
            self.assertTrue(service.check_status().is_activated)
            service._api.revoke_fails = True
            with self.assertRaisesRegex(RuntimeError, "Локальный доступ закрыт"):
                service.clear_activation()
            self.assertIsNone(PremiumStorage.get_binding())
            self.assertIsNotNone(PremiumStorage.get_binding(include_disabled=True))
            database_bytes = (
                self.store.get_settings_path().parent / "premium.sqlite3"
            ).read_bytes()
            self.assertNotIn(b"new-secret-token", database_bytes)
            service._api.revoke_fails = False
            self.assertTrue(service.clear_activation())
        self.assertEqual(
            service._api.revoke_requests[0]["request_id"],
            service._api.revoke_requests[1]["request_id"],
        )

    def test_legacy_settings_and_plaintext_token_are_not_carried_forward(self) -> None:
        from donater.storage import PremiumStorage

        settings_path = self.store.get_settings_path()
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        raw = self.store.read_settings()
        raw["premium"] = {
            "device_id": "legacy-device",
            "device_token": "legacy-token",
            "premium_cache": {"activated": True},
        }
        settings_path.write_text(json.dumps(raw), encoding="utf-8")
        self.store._SETTINGS_CACHE = None
        self.store._SETTINGS_CACHE_SIGNATURE = None

        device_id = PremiumStorage.get_device_id()
        self.assertNotEqual(device_id, "legacy-device")
        normalized = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertNotIn("premium", normalized)

        service = self._service()
        with patch("donater.service.verify_signed_response", side_effect=self._verify):
            self.assertTrue(service.pair_start()[0])
            self.assertTrue(service.check_status().is_activated)
        database_bytes = (settings_path.parent / "premium.sqlite3").read_bytes()
        self.assertNotIn(b"new-secret-token", database_bytes)
        with closing(sqlite3.connect(settings_path.parent / "premium.sqlite3")) as conn:
            self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
