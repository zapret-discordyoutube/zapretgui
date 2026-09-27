from __future__ import annotations

"""Единственный путь узнать новейший выпуск канала.

Порядок источников:

1. Forgejo — главный. Любой его определённый ответ побеждает зеркала.
2. Зеркала — только если Forgejo ответил ошибкой или не уложился в срок.
   Чтобы при заблокированном Forgejo не ждать его тайм-аутов впустую,
   зеркала запускаются параллельно, если Forgejo молчит дольше
   ``MIRROR_FALLBACK_DELAY_SECONDS``. В обычном случае проверка стоит один
   поход в Forgejo, и зеркала не трогаются.

Результат — всегда ``ReleaseLookup``: либо выпуск, либо понятная ошибка. «Не
удалось узнать» никогда не превращается в «обновлений нет». Кэшей нет:
каждый вызов — свежий ответ.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from log.log import log

from ..channel_utils import normalize_update_channel
from . import forgejo, mirrors
from .http import BackgroundCall, short_error


MIRROR_FALLBACK_DELAY_SECONDS = 3.0
FORGEJO_DEADLINE_SECONDS = 20.0


@dataclass(frozen=True, slots=True)
class ReleaseLookup:
    """Итог поиска выпуска: выпуск или текст ошибки для пользователя."""

    release: dict[str, Any] | None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.release is not None


def lookup_latest_release(
    channel: str,
    *,
    fetch_forgejo: Callable[[str], dict[str, Any]] = forgejo.fetch_latest_release,
    fetch_mirrors: Callable[[str], dict[str, Any]] = mirrors.fetch_latest_release,
    fallback_delay: float = MIRROR_FALLBACK_DELAY_SECONDS,
    forgejo_deadline: float = FORGEJO_DEADLINE_SECONDS,
    mirrors_deadline: float = mirrors.MIRRORS_DEADLINE_SECONDS,
) -> ReleaseLookup:
    selected = normalize_update_channel(channel)
    started = time.monotonic()

    forgejo_call = BackgroundCall(lambda: fetch_forgejo(selected), name="update-forgejo")
    outcome = forgejo_call.wait(fallback_delay)
    if outcome is not None and outcome.ok:
        return ReleaseLookup(outcome.value)

    mirrors_call = BackgroundCall(lambda: fetch_mirrors(selected), name="update-mirrors")
    if outcome is None:
        remaining = forgejo_deadline - (time.monotonic() - started)
        outcome = forgejo_call.wait(remaining)
        if outcome is not None and outcome.ok:
            return ReleaseLookup(outcome.value)

    forgejo_error = (
        short_error(outcome.error) if outcome is not None and outcome.error is not None
        else "нет ответа за отведённое время"
    )
    log(f"⚠️ Forgejo: {forgejo_error}; проверяем зеркала", "🔄 RELEASE")

    mirror_outcome = mirrors_call.wait(mirrors_deadline)
    if mirror_outcome is not None and mirror_outcome.ok:
        return ReleaseLookup(mirror_outcome.value)
    mirror_error = (
        short_error(mirror_outcome.error, limit=300)
        if mirror_outcome is not None and mirror_outcome.error is not None
        else "нет ответа за отведённое время"
    )
    message = f"Не удалось узнать новейшую версию. Forgejo: {forgejo_error}. Зеркала: {mirror_error}"
    log(f"❌ {message}", "🔄 RELEASE")
    return ReleaseLookup(None, message)


__all__ = [
    "FORGEJO_DEADLINE_SECONDS",
    "MIRROR_FALLBACK_DELAY_SECONDS",
    "ReleaseLookup",
    "lookup_latest_release",
]
