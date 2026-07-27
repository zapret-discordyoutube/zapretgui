"""Помощники для пулов потоков в длительных сетевых проверках."""

from __future__ import annotations

import time
from concurrent.futures import Future, TimeoutError as FuturesTimeout, wait as futures_wait
from concurrent.futures import FIRST_COMPLETED
from collections.abc import Callable, Iterable, Iterator

__all__ = ["iter_completed"]


# Как часто перепроверяем флаг отмены, пока сеть молчит.
CANCEL_POLL_SECONDS = 0.25


def iter_completed(
    futures: Iterable[Future],
    *,
    cancelled: Callable[[], bool] | None = None,
    poll: float = CANCEL_POLL_SECONDS,
    timeout: float | None = None,
) -> Iterator[Future]:
    """``as_completed``, регулярно проверяющий отмену.

    Штатный ``as_completed`` отдаёт управление только когда очередная задача
    завершилась. Если сеть висит, флаг отмены не проверяется вовсе — кнопка
    «Остановить» перестаёт работать до конца самой долгой операции.

    Генератор останавливается, как только ``cancelled()`` вернул истину;
    незавершённые задачи при этом не отменяются — это забота вызывающего.
    """
    pending = set(futures)
    deadline = None if timeout is None else time.monotonic() + timeout

    def _is_cancelled() -> bool:
        if not callable(cancelled):
            return False
        try:
            return bool(cancelled())
        except Exception:
            return False

    while pending:
        if _is_cancelled():
            return
        if deadline is not None and time.monotonic() >= deadline:
            raise FuturesTimeout(f"{len(pending)} задач не завершились вовремя")

        done, pending = futures_wait(pending, timeout=poll, return_when=FIRST_COMPLETED)
        for future in done:
            yield future
            if _is_cancelled():
                return
