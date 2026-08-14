from __future__ import annotations

import secrets
from typing import Any, Dict, Optional, Tuple

import requests


class PremiumApiClient:
    """Typed transport for the single Zapret Premium API contract."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 10,
        health_timeout: float = 3,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = max(0.1, float(timeout))
        self.health_timeout = max(0.1, min(float(health_timeout), self.timeout))
        self._session = requests.Session()
        self._session.trust_env = False

    def _url(self, endpoint: str) -> str:
        return f"{self.base_url}/{str(endpoint or '').lstrip('/')}"

    @staticmethod
    def _safe_error_code(value: Any) -> str:
        if isinstance(value, dict):
            value = value.get("code")
        code = str(value or "unknown").strip().lower()
        if not code or len(code) > 64 or not code.replace("_", "").isalnum():
            return "unknown"
        return code

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        payload: dict[str, Any] | None = None,
        nonce: str = "",
        timeout: float | None = None,
    ) -> Dict[str, Any]:
        try:
            response = self._session.request(
                method,
                self._url(endpoint),
                json=payload,
                timeout=self.timeout if timeout is None else max(0.1, float(timeout)),
            )
        except requests.Timeout:
            return {
                "success": False,
                "error": {"code": "timeout", "retryable": True},
                "_nonce": nonce,
                "_http_status": 0,
            }
        except requests.RequestException:
            return {
                "success": False,
                "error": {"code": "network_error", "retryable": True},
                "_nonce": nonce,
                "_http_status": 0,
            }

        try:
            data = response.json() if response.content else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return {
                "success": False,
                "error": {"code": "invalid_response", "retryable": response.status_code >= 500},
                "_nonce": nonce,
                "_http_status": int(response.status_code),
            }
        data["_nonce"] = nonce
        data["_http_status"] = int(response.status_code)
        if response.status_code >= 400 and not isinstance(data.get("signed"), dict):
            data["success"] = False
            data["error"] = {
                "code": self._safe_error_code(data.get("error")),
                "retryable": response.status_code in {408, 429, 500, 502, 503, 504},
            }
        return data

    def get_status(self) -> Optional[Dict[str, Any]]:
        # Health-check не должен ждать полный срок мутационного запроса.
        # Он не меняет состояние и используется только для быстрой индикации.
        return self._request("GET", "status", timeout=self.health_timeout)

    def post_pair_start(
        self,
        *,
        request_id: str,
        device_id: str,
        device_name: str | None = None,
    ) -> Tuple[Dict[str, Any], str]:
        nonce = secrets.token_urlsafe(16)
        return (
            self._request(
                "POST",
                "pairings/start",
                payload={
                    "request_id": request_id,
                    "device_id": device_id,
                    "device_name": device_name,
                    "nonce": nonce,
                },
                nonce=nonce,
            ),
            nonce,
        )

    def post_pair_finish(
        self,
        *,
        request_id: str,
        device_id: str,
        pairing_id: str,
    ) -> Tuple[Dict[str, Any], str]:
        nonce = secrets.token_urlsafe(16)
        return (
            self._request(
                "POST",
                "pairings/finish",
                payload={
                    "request_id": request_id,
                    "device_id": device_id,
                    "pairing_id": pairing_id,
                    "nonce": nonce,
                },
                nonce=nonce,
            ),
            nonce,
        )

    def post_check(
        self, *, request_id: str, device_id: str, device_token: str
    ) -> Tuple[Dict[str, Any], str]:
        nonce = secrets.token_urlsafe(16)
        return (
            self._request(
                "POST",
                "devices/status",
                payload={
                    "request_id": request_id,
                    "device_id": device_id,
                    "device_token": device_token,
                    "nonce": nonce,
                },
                nonce=nonce,
            ),
            nonce,
        )

    def post_revoke(
        self,
        *,
        request_id: str,
        device_id: str,
        binding_id: str,
        binding_generation: int,
        device_token: str,
    ) -> Tuple[Dict[str, Any], str]:
        nonce = secrets.token_urlsafe(16)
        return (
            self._request(
                "POST",
                "devices/revoke",
                payload={
                    "request_id": request_id,
                    "device_id": device_id,
                    "binding_id": binding_id,
                    "binding_generation": int(binding_generation),
                    "device_token": device_token,
                    "nonce": nonce,
                },
                nonce=nonce,
            ),
            nonce,
        )


__all__ = ["PremiumApiClient"]
