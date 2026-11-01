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
        server_now = int(time.time())
        raw, nonce = self._response(
            {
                "type": "zapret_pairing_started",
                "request_id": request_id,
                "device_id": device_id,
                "pairing_id": "pairing-1",
                "pair_code": "ABCDEFGH",
                "pair_expires_at": server_now + 600,
            },
            "start-nonce",
        )
        raw["_server_time"] = server_now
        return raw, nonce

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
        store.close_settings_database()
        store.MAIN_DIRECTORY = self.tmp.name

    def tearDown(self) -> None:
        self.store.close_settings_database()
        self.store.MAIN_DIRECTORY = self.old_root
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
            self.assertIsNone(PremiumStorage.get_pending_pairing())
            self.assertIsNone(PremiumStorage.get_pair_code())
            self.assertTrue(service.clear_activation())

        self.assertIsNone(PremiumStorage.get_binding(include_disabled=True))
        self.assertEqual(len(service._api.revoke_requests), 1)
        revoke = service._api.revoke_requests[0]
        self.assertEqual(revoke["binding_id"], "binding-1")
        self.assertEqual(revoke["binding_generation"], 1)

    def test_pc_clock_ahead_of_server_still_accepts_fresh_code(self) -> None:
        """Часы ПК спешат на 3 часа (неверный часовой пояс): код всё равно создаётся."""

        from types import SimpleNamespace

        from donater.storage import PremiumStorage

        service = self._service()
        pc_clock = SimpleNamespace(time=lambda: time.time() + 3 * 3600)
        with (
            patch("donater.service.verify_signed_response", side_effect=self._verify),
            patch("donater.service.time", pc_clock),
        ):
            ok, message, code = service.pair_start(device_name="Test PC")
            self.assertTrue(ok, message)
            self.assertEqual(code, "ABCDEFGH")
            pending = PremiumStorage.get_pending_pairing()
            self.assertEqual(pending["pairing_id"], "pairing-1")
            # Срок переведён на часы ПК: те же 10 минут от «сейчас» ПК.
            self.assertAlmostEqual(
                int(pending["expires_at"]) - int(pc_clock.time()), 600, delta=5
            )
            # Код не считается истёкшим: статус опрашивает finish и сохраняет привязку.
            service.check_status()
        self.assertEqual(PremiumStorage.get_binding()["binding_id"], "binding-1")

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
                self.store.get_settings_database_path().parent / "premium.sqlite3"
            ).read_bytes()
            self.assertNotIn(b"new-secret-token", database_bytes)
            service._api.revoke_fails = False
            self.assertTrue(service.clear_activation())
        self.assertEqual(
            service._api.revoke_requests[0]["request_id"],
            service._api.revoke_requests[1]["request_id"],
        )

    def _make_token_unreadable(self) -> None:
        """Как после переустановки Windows: ключ привязки зашифрован DPAPI, а расшифровать его нельзя."""
        database = self.store.get_settings_database_path().parent / "premium.sqlite3"
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute("UPDATE current_binding SET device_token=?", (b"dpapi:old-windows-blob",))

    @staticmethod
    def _unreadable(_raw):
        raise OSError(-2146893813, "CryptUnprotectData", "Ключ не может быть использован в указанном состоянии.")

    def test_unreadable_windows_key_asks_to_pair_again_instead_of_raw_error(self) -> None:
        from donater.storage import UNREADABLE_BINDING_MESSAGE, PremiumStorage

        service = self._service()
        with patch("donater.service.verify_signed_response", side_effect=self._verify):
            self.assertTrue(service.pair_start()[0])
            self.assertTrue(service.check_status().is_activated)
        device_id = PremiumStorage.get_device_id()
        self._make_token_unreadable()

        with patch("donater.storage._dpapi_decrypt", side_effect=self._unreadable):
            status = service.check_status()
            self.assertFalse(status.is_activated)
            self.assertFalse(status.is_linked)
            self.assertEqual(status.status_message, UNREADABLE_BINDING_MESSAGE)
            # Привязка забыта, устройство то же — его можно привязать заново.
            self.assertIsNone(PremiumStorage.get_binding(include_disabled=True))
            self.assertEqual(PremiumStorage.get_device_id(), device_id)
            # Объяснение показывается один раз, дальше — обычное «не привязано».
            self.assertEqual(service.check_status().status_message, "Устройство не привязано")

    def test_unbinding_with_unreadable_key_just_forgets_the_binding(self) -> None:
        from donater.storage import PremiumStorage

        service = self._service()
        with patch("donater.service.verify_signed_response", side_effect=self._verify):
            self.assertTrue(service.pair_start()[0])
            self.assertTrue(service.check_status().is_activated)
        self._make_token_unreadable()

        with patch("donater.storage._dpapi_decrypt", side_effect=self._unreadable):
            self.assertTrue(service.clear_activation())
        self.assertIsNone(PremiumStorage.get_binding(include_disabled=True))
        self.assertEqual(service._api.revoke_requests, [])

    def test_legacy_settings_and_plaintext_token_are_not_carried_forward(self) -> None:
        from donater.storage import PremiumStorage

        settings_database = self.store.get_settings_database_path()
        settings_database.parent.mkdir(parents=True, exist_ok=True)
        legacy_path = settings_database.parent / "settings.json"
        legacy_path.write_text(
            json.dumps(
                {
                    "premium": {
                        "device_id": "legacy-device",
                        "device_token": "legacy-token",
                        "premium_cache": {"activated": True},
                    }
                }
            ),
            encoding="utf-8",
        )

        device_id = PremiumStorage.get_device_id()
        self.assertNotEqual(device_id, "legacy-device")
        normalized = self.store.read_settings()
        self.assertNotIn("premium", normalized)
        self.assertIn("legacy-token", legacy_path.read_text(encoding="utf-8"))

        service = self._service()
        with patch("donater.service.verify_signed_response", side_effect=self._verify):
            self.assertTrue(service.pair_start()[0])
            self.assertTrue(service.check_status().is_activated)
        database_bytes = (settings_database.parent / "premium.sqlite3").read_bytes()
        self.assertNotIn(b"new-secret-token", database_bytes)
        with closing(sqlite3.connect(settings_database.parent / "premium.sqlite3")) as conn:
            self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
