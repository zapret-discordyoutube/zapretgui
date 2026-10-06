"""Когда в следующий раз спрашивать сервер о Premium-статусе.

Чистые правила без Qt: их применяет donater.status_runtime — единственный,
кто решает, когда идти в сеть. Сервис и страница расписанием не занимаются.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


# После запуска сначала показываем сохранённый статус, сеть — чуть позже.
STARTUP_NETWORK_DELAY_MS = 3_000
# Пользователь отправил код боту и ждёт: опрашиваем часто.
PAIRING_POLL_MS = 3_000
# Каждый запрос приостанавливает winws2 — ждём привязку реже.
PAIRING_POLL_DIRECT_MS = 10_000
PAIRING_NETWORK_RETRY_MS = 15_000
# Устройство привязано, но подписка не активна: продление ловим быстрее.
INACTIVE_REFRESH_MS = 30 * 60_000
# Обычное обновление. Подписанный ответ сервера живёт 7 дней, так что
# сохранённый статус не успевает устареть, пока программа хоть иногда в сети.
REFRESH_MS = 3 * 60 * 60_000
# Повторы после сбоя сети: быстро в начале, затем всё реже.
NETWORK_RETRY_STEPS_MS = (30_000, 2 * 60_000, 10 * 60_000, 30 * 60_000)
# Запрос «заодно» (открыли страницу Premium) не чаще раза в минуту.
SOFT_REFRESH_MIN_GAP_SEC = 60


def next_check_delay_ms(
    info: Mapping[str, Any] | None,
    *,
    network_failures: int = 0,
) -> int:
    """Пауза до следующей проверки по итогу только что завершённой.

    info is None — проверка упала целиком; считаем это сбоем и повторяем
    по той же лесенке, что и после сбоя сети.
    """
    if info is not None and info.get("pairing_pending"):
        if info.get("network_failed"):
            return PAIRING_NETWORK_RETRY_MS
        if info.get("direct_window"):
            return PAIRING_POLL_DIRECT_MS
        return PAIRING_POLL_MS
    if info is None or info.get("network_failed"):
        step = max(1, int(network_failures)) - 1
        return NETWORK_RETRY_STEPS_MS[min(step, len(NETWORK_RETRY_STEPS_MS) - 1)]
    if info.get("found") and not info.get("activated"):
        return INACTIVE_REFRESH_MS
    return REFRESH_MS


def soft_refresh_allowed(*, now: float, last_network_check_at: float) -> bool:
    """Можно ли сходить в сеть по необязательному поводу."""
    if last_network_check_at <= 0.0:
        return True
    return now - last_network_check_at >= SOFT_REFRESH_MIN_GAP_SEC


__all__ = [
    "INACTIVE_REFRESH_MS",
    "NETWORK_RETRY_STEPS_MS",
    "PAIRING_NETWORK_RETRY_MS",
    "PAIRING_POLL_DIRECT_MS",
    "PAIRING_POLL_MS",
    "REFRESH_MS",
    "SOFT_REFRESH_MIN_GAP_SEC",
    "STARTUP_NETWORK_DELAY_MS",
    "next_check_delay_ms",
    "soft_refresh_allowed",
]
