"""Настройки BlockCheck: пределы времени и отбор целей."""

from __future__ import annotations

# Сколько секунд ждать ответа голосового сервера (STUN по UDP).
STUN_TIMEOUT = 5

# Отбор серверов для проверки обрыва загрузки на 16–20 КБ.
TCP_TARGET_MAX_COUNT = 6
TCP_TARGETS_PER_PROVIDER = 2
