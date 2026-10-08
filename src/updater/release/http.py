from __future__ import annotations

"""Сеть поиска выпуска: одно соединение на источник и параллельный запуск.

Все запросы идут мимо системного прокси: DPI и VPN подставляют свои
переменные окружения, и через них проверка обновлений ломалась бы.

Параллельные задачи работают в фоновых (daemon) потоках. Обычный пул потоков
при закрытии программы дожидается зависшего запроса к недоступному серверу;
фоновый поток просто бросается, и закрытие не ждёт сеть.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

import requests


USER_AGENT = "Zapret-Updater/5.0"
FORGEJO_TIMEOUT = (5, 10)
MIRROR_TIMEOUT = (4, 8)

T = TypeVar("T")


def new_session() -> requests.Session:
    """Соединение без системного прокси. Одно на источник, не на поток."""
    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": None, "https": None}
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def short_error(exc: BaseException, *, limit: int = 120) -> str:
    """Короткий текст ошибки для журнала и экрана."""
    if isinstance(exc, requests.exceptions.Timeout):
        return "нет ответа (тайм-аут)"
    if isinstance(exc, requests.exceptions.SSLError):
        return "ошибка защищённого соединения"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "не удалось подключиться"
    if isinstance(exc, requests.exceptions.HTTPError):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        return f"сервер ответил HTTP {status}" if status is not None else "ошибка HTTP"
    text = str(exc).strip() or type(exc).__name__
    return text[:limit]


@dataclass(frozen=True, slots=True)
class Outcome(Generic[T]):
    """Итог фоновой задачи: значение или ошибка."""

    value: T | None = None
    error: BaseException | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class BackgroundCall(Generic[T]):
    """Одна задача в фоновом потоке, которую можно ждать ограниченное время."""

    def __init__(self, function: Callable[[], T], *, name: str) -> None:
        self._done = threading.Event()
        self._outcome: Outcome[T] | None = None
        self._function = function
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self._outcome = Outcome(value=self._function())
        except BaseException as exc:  # noqa: BLE001 — итог передаётся ждущему
            self._outcome = Outcome(error=exc)
        finally:
            self._done.set()

    def wait(self, timeout: float | None) -> Outcome[T] | None:
        """Итог или None, если задача не успела за ``timeout`` секунд."""
        if not self._done.wait(None if timeout is None else max(float(timeout), 0.0)):
            return None
        return self._outcome


__all__ = [
    "BackgroundCall",
    "FORGEJO_TIMEOUT",
    "MIRROR_TIMEOUT",
    "Outcome",
    "USER_AGENT",
    "new_session",
    "short_error",
]
