from __future__ import annotations

"""Единственный путь узнать новейший выпуск канала.

Порядок источников:

1. Forgejo — главный. У него фора ``MIRROR_FALLBACK_DELAY_SECONDS``: пока она
   идёт, зеркала не трогаются, и в обычном случае проверка стоит один поход
   в Forgejo.
2. Зеркала — запасные. Они запускаются, как только Forgejo ответил ошибкой
   или промолчал всю фору. Дальше побеждает первый успешный ответ — Forgejo
   или зеркала: готовый ответ зеркала не ждёт тайм-аутов заблокированного
   Forgejo.

Результат — всегда ``ReleaseLookup``: либо выпуск, либо понятная ошибка. «Не
удалось узнать» никогда не превращается в «обновлений нет». Кэшей нет:
каждый вызов — свежий ответ.
"""

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from log.log import log

from ..channel_utils import normalize_update_channel
from . import forgejo, mirrors
from .http import Outcome, short_error


MIRROR_FALLBACK_DELAY_SECONDS = 3.0
FORGEJO_DEADLINE_SECONDS = 20.0
NO_ANSWER_IN_TIME = "нет ответа за отведённое время"

_FORGEJO = "forgejo"
_MIRRORS = "mirrors"


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
    answers: queue.Queue[tuple[str, Outcome[dict[str, Any]]]] = queue.Queue()

    def ask(source: str, fetch: Callable[[str], dict[str, Any]]) -> None:
        def run() -> None:
            try:
                answers.put((source, Outcome(value=fetch(selected))))
            except BaseException as exc:  # noqa: BLE001 — итог передаётся ждущему
                answers.put((source, Outcome(error=exc)))

        threading.Thread(target=run, name=f"update-{source}", daemon=True).start()

    # Источник → момент, после которого его ответ уже не ждём.
    waiting: dict[str, float] = {_FORGEJO: started + float(forgejo_deadline)}
    errors: dict[str, str] = {}
    mirrors_start_at: float | None = started + float(fallback_delay)
    ask(_FORGEJO, fetch_forgejo)

    def fail(source: str, reason: str) -> None:
        errors[source] = reason
        if source == _FORGEJO:
            log(f"⚠️ Forgejo: {reason}; проверяем зеркала", "🔄 RELEASE")

    while True:
        now = time.monotonic()
        for source in [source for source, deadline in waiting.items() if now >= deadline]:
            del waiting[source]
            fail(source, NO_ANSWER_IN_TIME)
        if mirrors_start_at is not None and (_FORGEJO in errors or now >= mirrors_start_at):
            mirrors_start_at = None
            waiting[_MIRRORS] = now + float(mirrors_deadline)
            ask(_MIRRORS, fetch_mirrors)
        if not waiting:
            break

        wake_at = min(waiting.values())
        if mirrors_start_at is not None:
            wake_at = min(wake_at, mirrors_start_at)
        try:
            source, outcome = answers.get(timeout=max(wake_at - time.monotonic(), 0.0))
        except queue.Empty:
            continue
        if waiting.pop(source, None) is None:
            # Ответ пришёл после своего срока: источник уже записан в ошибки.
            continue
        if outcome.ok:
            return ReleaseLookup(outcome.value)
        fail(source, short_error(outcome.error, limit=300 if source == _MIRRORS else 120))

    message = (
        f"Не удалось узнать новейшую версию. Forgejo: {errors[_FORGEJO]}. Зеркала: {errors[_MIRRORS]}"
    )
    log(f"❌ {message}", "🔄 RELEASE")
    return ReleaseLookup(None, message)


__all__ = [
    "FORGEJO_DEADLINE_SECONDS",
    "MIRROR_FALLBACK_DELAY_SECONDS",
    "ReleaseLookup",
    "lookup_latest_release",
]
