from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from config._build_secrets import PREMIUM_API_BASE_URL as API_BASE_URL

from .api import PremiumApiClient
from .crypto import verify_signed_response
from .storage import PremiumStorage
from .types import ActivationStatus


REQUEST_TIMEOUT = 5
BACKGROUND_NETWORK_REFRESH_SEC = 3 * 60 * 60
PAIR_CODE_TTL_MINUTES = 10

_TRANSIENT_NETWORK_ERROR_CODES = frozenset(
    {
        "timeout",
        "connect_timeout",
        "read_timeout",
        "dns_error",
        "tls_error",
        "network_error",
    }
)


def _error_data(raw: Any, signed: Any = None) -> tuple[str, bool]:
    value: Any = None
    if isinstance(signed, dict) and signed.get("type") == "zapret_premium_error":
        value = signed.get("error")
    elif isinstance(raw, dict):
        value = raw.get("error")
    if isinstance(value, dict):
        code = str(value.get("code") or "unknown").strip().lower()
        retryable = bool(value.get("retryable"))
    else:
        code = str(value or "unknown").strip().lower()
        retryable = code in _TRANSIENT_NETWORK_ERROR_CODES
        if code.casefold() == "ошибка сети".casefold():
            code = "network_error"
            retryable = True
    if not code or len(code) > 64 or not code.replace("_", "").isalnum():
        code = "unknown"
    return code, retryable


def _error_message(code: str, *, pairing: bool = False) -> str:
    messages = {
        "timeout": "Сервер не ответил вовремя. Повторите попытку.",
        "connect_timeout": "Не удалось установить соединение с сервером Premium вовремя.",
        "read_timeout": "Сервер Premium принял соединение, но не прислал ответ вовремя.",
        "dns_error": "Не удалось найти адрес сервера Premium через DNS.",
        "tls_error": "Не удалось установить защищённое TLS-соединение с сервером Premium.",
        "network_error": "Нет соединения с сервером Premium.",
        "winws_restore_failed": "Сервер проверен напрямую, но winws2 не удалось безопасно восстановить.",
        "pairing_not_confirmed": "Код ещё не подтверждён в Telegram-боте.",
        "pairing_not_found": "Сопряжение не найдено. Создайте новый код.",
        "pairing_expired": "Код истёк. Создайте новый код.",
        "binding_inactive": "Привязка больше не активна. Создайте новый код.",
        "invalid_device_credential": "Привязка устройства недействительна.",
        "invalid_response": "Сервер вернул некорректный ответ.",
    }
    fallback = "Не удалось завершить привязку." if pairing else "Ошибка сервиса Premium."
    return messages.get(code, fallback)


class PremiumService:
    """One serialized client actor backed by transactional SQLite state."""

    def __init__(self, *, api_base_url: str = API_BASE_URL, timeout: int = REQUEST_TIMEOUT):
        self._lock = threading.Lock()
        self._api = PremiumApiClient(base_url=api_base_url, timeout=timeout)
        self._last_background_network_attempt_at = 0.0

    def _automatic_network_due(self, *, has_pending_pairing: bool) -> bool:
        """Allow startup probe, three-hour refreshes and active pairing polls."""

        if has_pending_pairing:
            return True
        now = time.monotonic()
        previous = float(self._last_background_network_attempt_at or 0.0)
        if previous > 0.0 and now - previous < BACKGROUND_NETWORK_REFRESH_SEC:
            return False
        self._last_background_network_attempt_at = now
        return True

    @property
    def device_id(self) -> str:
        return PremiumStorage.get_device_id()

    def test_connection(self) -> Tuple[bool, str]:
        with self._lock:
            result = self._api.get_status()
            if isinstance(result, dict) and result.get("success") is True:
                version = str(result.get("version") or "unknown")
                schema = result.get("schema_version")
                suffix = f", SQLite {schema}" if schema is not None else ""
                return True, f"API сервер доступен (v{version}{suffix})"
            code, _retryable = _error_data(result)
            return False, _error_message(code)

    def pair_start(
        self, *, device_name: Optional[str] = None
    ) -> Tuple[bool, str, Optional[str]]:
        with self._lock:
            operation = PremiumStorage.begin_pairing()
            device_id = operation["device_id"]
            raw, nonce = self._api.post_pair_start(
                request_id=operation["start_request_id"],
                device_id=device_id,
                device_name=device_name,
            )
            signed = verify_signed_response(
                raw,
                expected_device_id=device_id,
                expected_nonce=nonce,
            )
            if not signed or signed.get("type") != "zapret_pairing_started":
                code, _retryable = _error_data(raw, signed)
                return False, _error_message(code, pairing=True), None

            pairing_id = str(signed.get("pairing_id") or "").strip()
            code = str(signed.get("pair_code") or "").strip().upper()
            try:
                expires_at = int(signed.get("pair_expires_at"))
            except (TypeError, ValueError):
                expires_at = 0
            if not pairing_id or not code or expires_at <= int(time.time()):
                PremiumStorage.clear_pair_code()
                return False, "Сервер вернул некорректное сопряжение.", None
            if not PremiumStorage.store_pairing_started(
                request_id=operation["start_request_id"],
                pairing_id=pairing_id,
                code=code,
                expires_at=expires_at,
            ):
                return False, "Не удалось сохранить код сопряжения.", None
            return (
                True,
                str(
                    signed.get("message")
                    or f"Код создан на {PAIR_CODE_TTL_MINUTES} минут"
                ),
                code,
            )

    def clear_activation(self) -> bool:
        """Disable locally first, then require a signed exact server revoke."""

        with self._lock:
            operation = PremiumStorage.prepare_revoke()
            if operation is None:
                return True
            raw, nonce = self._api.post_revoke(
                request_id=str(operation["operation_id"]),
                device_id=str(operation["device_id"]),
                binding_id=str(operation["binding_id"]),
                binding_generation=int(operation["binding_generation"]),
                device_token=str(operation["device_token"]),
            )
            signed = verify_signed_response(
                raw,
                expected_device_id=str(operation["device_id"]),
                expected_nonce=nonce,
            )
            exact = bool(
                isinstance(signed, dict)
                and signed.get("type") == "zapret_premium_device_revoked"
                and str(signed.get("binding_id") or "")
                == str(operation["binding_id"])
                and int(signed.get("binding_generation") or 0)
                == int(operation["binding_generation"])
                and (signed.get("revoked") is True or signed.get("already_absent") is True)
            )
            if exact and PremiumStorage.complete_revoke(str(operation["operation_id"])):
                return True
            code, _retryable = _error_data(raw, signed)
            PremiumStorage.mark_revoke_failed(str(operation["operation_id"]), code)
            raise RuntimeError(
                "Локальный доступ закрыт, но сервер ещё не подтвердил отвязку. "
                "Повторите сброс после восстановления сети."
            )

    @staticmethod
    def _apply_network_health(raw: Any) -> bool:
        if not isinstance(raw, dict):
            return False
        try:
            http_status = int(raw.get("_http_status") or 0)
        except (TypeError, ValueError):
            http_status = 0
        if http_status > 0:
            PremiumStorage.clear_last_network_failure()
            return False
        code, retryable = _error_data(raw)
        if retryable and code in _TRANSIENT_NETWORK_ERROR_CODES:
            PremiumStorage.save_last_network_failure_now()
            return True
        return False

    @staticmethod
    def _cached_status(
        *, device_id: str, offline: bool
    ) -> ActivationStatus | None:
        cache = PremiumStorage.get_premium_cache()
        if not isinstance(cache, dict):
            return None
        signed = verify_signed_response(
            {
                "kid": cache.get("kid"),
                "sig": cache.get("sig"),
                "signed": cache.get("signed"),
            },
            expected_device_id=device_id,
            expected_nonce=None,
        )
        if not signed or signed.get("activated") is not True:
            return None
        now = int(time.time())
        try:
            valid_until = int(signed.get("valid_until") or 0)
        except (TypeError, ValueError):
            return None
        if valid_until < now:
            return None
        expires_epoch = signed.get("expires_at_epoch")
        if expires_epoch is not None:
            try:
                if int(expires_epoch) <= now:
                    return None
            except (TypeError, ValueError):
                return None
        else:
            try:
                expires = datetime.fromisoformat(
                    str(signed.get("expires_at") or "").replace("Z", "+00:00")
                )
                if expires.tzinfo is None:
                    expires = expires.astimezone()
                if int(expires.timestamp()) <= now:
                    return None
            except (TypeError, ValueError):
                return None
        return ActivationStatus(
            is_activated=True,
            days_remaining=signed.get("days_remaining"),
            expires_at=signed.get("expires_at"),
            status_message="Активировано (offline)" if offline else "Активировано",
            is_linked=True,
            subscription_level=str(signed.get("subscription_level") or "zapretik"),
            source="offline" if offline else "cache",
        )

    def check_status(
        self, *, allow_network: bool = True, automatic: bool = False
    ) -> ActivationStatus:
        with self._lock:
            device_id = PremiumStorage.get_device_id()
            binding = PremiumStorage.get_binding()
            device_token = str((binding or {}).get("device_token") or "")
            network_cooldown = False
            network_failed = False

            pending = PremiumStorage.get_pending_pairing()
            has_pending = bool(
                pending
                and pending.get("pairing_id")
                and int(pending.get("expires_at") or 0) >= int(time.time())
            )
            if (
                allow_network
                and automatic
                and not self._automatic_network_due(has_pending_pairing=has_pending)
            ):
                allow_network = False
                network_cooldown = True
            pairing_message: str | None = None
            if pending and not has_pending and pending.get("pairing_id"):
                PremiumStorage.clear_pair_code()
                pending = None
            if allow_network and has_pending and pending:
                raw, nonce = self._api.post_pair_finish(
                    request_id=str(pending["finish_request_id"]),
                    device_id=device_id,
                    pairing_id=str(pending["pairing_id"]),
                )
                network_failed = self._apply_network_health(raw)
                signed = verify_signed_response(
                    raw, expected_device_id=device_id, expected_nonce=nonce
                )
                if signed and signed.get("type") == "zapret_premium_activation":
                    token = str(signed.get("device_token") or "").strip()
                    binding_id = str(signed.get("binding_id") or "").strip()
                    try:
                        generation = int(signed.get("binding_generation"))
                    except (TypeError, ValueError):
                        generation = 0
                    if token and binding_id and generation > 0:
                        if PremiumStorage.store_after_pairing(
                            device_id=device_id,
                            binding_id=binding_id,
                            binding_generation=generation,
                            device_token=token,
                            signed_payload=signed,
                            kid=raw.get("kid"),
                            sig=raw.get("sig"),
                        ):
                            binding = PremiumStorage.get_binding()
                            device_token = token
                else:
                    code, _retryable = _error_data(raw, signed)
                    pairing_message = _error_message(code, pairing=True)
                    if code in {
                        "pairing_not_found",
                        "pairing_expired",
                        "binding_inactive",
                    }:
                        PremiumStorage.clear_pair_code()
                        has_pending = False

            if not device_token:
                cached = self._cached_status(
                    device_id=device_id,
                    offline=network_failed or network_cooldown,
                )
                if cached is not None:
                    return cached
                return ActivationStatus(
                    is_activated=False,
                    days_remaining=None,
                    expires_at=None,
                    status_message=(
                        pairing_message
                        or ("Ожидание привязки" if has_pending else "Устройство не привязано")
                    ),
                    is_linked=False,
                    subscription_level="–",
                )

            api_error: str | None = None
            if allow_network:
                raw, nonce = self._api.post_check(
                    request_id=str(uuid.uuid4()),
                    device_id=device_id,
                    device_token=device_token,
                )
                network_failed = self._apply_network_health(raw)
                signed = verify_signed_response(
                    raw, expected_device_id=device_id, expected_nonce=nonce
                )
                if signed and signed.get("type") == "zapret_premium_status":
                    activated = bool(signed.get("activated"))
                    linked = bool(signed.get("linked"))
                    if activated:
                        PremiumStorage.store_status_active(
                            signed_payload=signed,
                            kid=raw.get("kid"),
                            sig=raw.get("sig"),
                        )
                    else:
                        PremiumStorage.apply_status_inactive(
                            message=str(signed.get("message") or "")
                        )
                    return ActivationStatus(
                        is_activated=activated,
                        days_remaining=signed.get("days_remaining"),
                        expires_at=signed.get("expires_at"),
                        status_message=str(
                            signed.get("message")
                            or ("Активировано" if activated else "Не активировано")
                        ),
                        is_linked=linked,
                        subscription_level=str(
                            signed.get("subscription_level")
                            or ("zapretik" if activated else "–")
                        ),
                    )
                code, _retryable = _error_data(raw, signed)
                api_error = _error_message(code)
            elif network_cooldown:
                api_error = "Недавняя ошибка сети, используется подписанный кэш."

            cached = self._cached_status(
                device_id=device_id,
                offline=network_failed or network_cooldown,
            )
            if cached is not None:
                return cached
            return ActivationStatus(
                is_activated=False,
                days_remaining=None,
                expires_at=None,
                status_message=api_error or "Не активировано",
                is_linked=None,
                subscription_level="–",
            )

    def check_device_activation(
        self, *, use_cache: bool = False, automatic: bool = False
    ) -> Dict[str, Any]:
        status = self.check_status(allow_network=not use_cache, automatic=automatic)
        found = (
            status.is_linked
            if status.is_linked is not None
            else PremiumStorage.get_binding() is not None
        )
        return {
            "found": found,
            "activated": status.is_activated,
            "is_premium": status.is_activated,
            "days_remaining": status.days_remaining,
            "status": status.status_message,
            "expires_at": status.expires_at,
            "level": "Premium" if status.subscription_level != "–" else "–",
            "subscription_level": status.subscription_level,
            "source": status.source,
        }


_SERVICE: Optional[PremiumService] = None


def get_premium_service() -> PremiumService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = PremiumService()
    return _SERVICE


__all__ = ["PremiumService", "get_premium_service"]
