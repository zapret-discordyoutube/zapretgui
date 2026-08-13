from __future__ import annotations

import time
import unittest
from unittest.mock import Mock, patch


class PremiumStatusSourceTests(unittest.TestCase):
    def _signed_status(self) -> dict:
        return {
            "type": "zapret_premium_status",
            "device_id": "device-1",
            "activated": True,
            "linked": True,
            "subscription_level": "vless_max",
            "days_remaining": 51,
            "expires_at": "2099-01-01T00:00:00",
            "valid_until": int(time.time()) + 3600,
        }

    def _verify_valid_cache_only(self, raw, *args, **kwargs):
        if isinstance(raw, dict) and raw.get("signed") == {}:
            return self._signed_status()
        return None

    def _patch_valid_cache(self, *, token: str = "token-1"):
        return (
            patch("donater.service.PremiumStorage.get_device_id", return_value="device-1"),
            patch(
                "donater.service.PremiumStorage.get_binding",
                return_value={
                    "device_token": token,
                    "binding_id": "binding-1",
                    "binding_generation": 1,
                },
            ),
            patch("donater.service.PremiumStorage.get_pending_pairing", return_value=None),
            patch("donater.service.PremiumStorage.get_premium_cache", return_value={"signed": {}}),
            patch("donater.service.verify_signed_response", side_effect=self._verify_valid_cache_only),
        )

    def test_cache_only_startup_reports_cache_source_not_offline(self) -> None:
        from donater.service import PremiumService

        service = PremiumService(api_base_url="http://premium.local/api")

        patches = self._patch_valid_cache()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = service.check_device_activation(use_cache=True)

        self.assertTrue(result["is_premium"])
        self.assertEqual(result["source"], "cache")
        self.assertEqual(result["status"], "Активировано")

    def test_network_failure_cache_reports_offline_source(self) -> None:
        from donater.service import PremiumService

        service = PremiumService(api_base_url="http://premium.local/api")
        service._api.post_check = Mock(
            return_value=(
                {"success": False, "error": "Ошибка сети", "_http_status": 0},
                "nonce-1",
            )
        )

        patches = self._patch_valid_cache()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patch("donater.service.PremiumStorage.save_last_network_failure_now"),
        ):
            result = service.check_device_activation(use_cache=False)

        self.assertTrue(result["is_premium"])
        self.assertEqual(result["source"], "offline")
        self.assertEqual(result["status"], "Активировано (offline)")


if __name__ == "__main__":
    unittest.main()
