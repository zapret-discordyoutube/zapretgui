from __future__ import annotations

"""Скачивание одного файла сразу со всех источников.

Файл режется на куски. Каждый источник (Forgejo, зеркала) берёт куски из
общей очереди своими соединениями, поэтому быстрый источник сам забирает
большую часть работы, а медленный или мёртвый никого не задерживает.

Правила:

* уже полученные байты не теряются: оборвавшийся кусок докачивает любой
  источник с того же места;
* кусок, застрявший на медленном соединении, перехватывает свободное
  соединение побыстрее — тоже с текущего места. Оба пишут одинаковые байты,
  кусок готов, как только дошёл любой из них;
* источник отбрасывается сразу, если отдаёт файл другого размера или не
  отдаёт его вовсе, и после нескольких сетевых ошибок подряд;
* если сеть пропала у всех разом, а до этого работала, — несколько повторов
  с паузой;
* ошибка записи на диск останавливает всё: другие источники тут не помогут.

Здесь нет проверки SHA-256: её делает ``downloader`` над готовым файлом.
"""

import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

import requests

from log.log import log

from ..network_hints import maybe_log_disable_dpi_for_update
from ..release.http import new_session, short_error
from .contracts import (
    CancellationToken,
    DownloadSource,
    LocalWriteError,
    UpdateCancelled,
    UpdatePipelineError,
)


# Крупный кусок — меньше запросов: на линии с большой задержкой каждый запрос стоит времени.
PIECE_SIZE = 4 * 1024 * 1024
READ_SIZE = 32 * 1024
CONNECT_TIMEOUT = 6
# Соединение, которое молчит дольше, считается оборванным.
READ_TIMEOUT = 15
MAX_CONNECTIONS = 8
MAX_CONNECTIONS_PER_SOURCE = 4
# Столько байт подряд от источника — и ему можно дать больше одного соединения.
PROVEN_BYTES = 256 * 1024
# Сетевых ошибок подряд, после которых источник больше не пробуется.
SOURCE_FAILURE_LIMIT = 2
# Кусок моложе этого не перехватывается: соединение ещё разгоняется.
HEDGE_MIN_AGE_SECONDS = 3.0
# Перехват оправдан, только если свободное соединение закончит хотя бы вдвое быстрее.
HEDGE_ADVANTAGE = 2.0
# Паузы перед повторами, когда сеть пропала у всех источников разом.
REVIVAL_PAUSES_SECONDS = (2.0, 5.0)
TICK_SECONDS = 0.2
LOG_LEVEL = "🔁 UPDATE"

_CONTENT_RANGE_RE = re.compile(r"\Abytes (\d+)-(\d+)/(\d+)\Z")


class _SourceRejected(Exception):
    """Источник отдаёт не тот файл или не отдаёт его: пробовать снова незачем."""


class _Piece:
    __slots__ = ("start", "size", "have", "done", "fetches")

    def __init__(self, start: int, size: int) -> None:
        self.start = start
        self.size = size
        # Сколько байт от начала куска уже лежит в файле без пропусков.
        self.have = 0
        self.done = False
        self.fetches: list[_Fetch] = []


class _Fetch:
    """Одно соединение, которое сейчас качает кусок с места ``begin``."""

    __slots__ = ("piece", "state", "begin", "received", "started_at")

    def __init__(self, piece: _Piece, state: "_SourceState", started_at: float) -> None:
        self.piece = piece
        self.state = state
        self.begin = piece.have
        self.received = 0
        self.started_at = started_at


class _SourceState:
    def __init__(self, source: DownloadSource) -> None:
        self.source = source
        self.urls = [url for url in (source.url, source.fallback_url) if url]
        self.url_index = 0
        self.name = source.label
        self.workers = 0
        self.failures = 0
        # Источник уже показал, что отдаёт нужный файл.
        self.proven = False
        self.dead = False
        self.rejected = False
        self.no_range = False
        self.reason = ""
        self.delivered = 0
        self.busy_seconds = 0.0

    @property
    def url(self) -> str:
        return self.urls[self.url_index]

    def speed(self) -> float | None:
        """Средняя скорость одного соединения или None, пока мерить не на чем."""
        if self.busy_seconds < 0.3 or self.delivered <= 0:
            return None
        return self.delivered / self.busy_seconds


def _open_target(path: Path, total: int):
    file_obj = open(path, "r+b" if os.path.exists(path) else "w+b", buffering=0)
    try:
        file_obj.truncate(total)
    except BaseException:
        file_obj.close()
        raise
    return file_obj


def _local_write_error(exc: OSError) -> LocalWriteError:
    return LocalWriteError(f"Не удалось записать файл обновления: {exc.strerror or exc}")


class MultiSourceDownload:
    """Одна попытка скачать ``total`` байт в ``path`` с набора источников."""

    def __init__(
        self,
        sources: tuple[DownloadSource, ...],
        path: str | os.PathLike[str],
        *,
        total: int,
        token: CancellationToken,
        on_progress: Callable[[int, int, int], None] | None = None,
    ) -> None:
        self._states = [_SourceState(source) for source in sources]
        self._path = Path(path)
        self._total = int(total)
        self._token = token
        self._on_progress = on_progress or (lambda *_args: None)
        self._cond = threading.Condition()
        self._pieces = [
            _Piece(start, min(PIECE_SIZE, self._total - start)) for start in range(0, self._total, PIECE_SIZE)
        ]
        self._pending: deque[_Piece] = deque(self._pieces)
        self._left = len(self._pieces)
        self._done_bytes = 0
        self._stopped = False
        self._file = None
        self._write_error: LocalWriteError | None = None
        self._last_reported = -1

    # ── вход ────────────────────────────────────────────────────────────

    def run(self) -> frozenset[str]:
        """Скачивает файл целиком. Возвращает имена источников, давших байты."""
        if not self._states:
            raise UpdatePipelineError("Не удалось скачать обновление: нет источников")
        self._token.checkpoint()
        try:
            self._file = _open_target(self._path, self._total)
        except OSError as exc:
            raise _local_write_error(exc) from exc
        started = time.monotonic()
        try:
            self._download_pieces()
            if self._left:
                self._download_whole_stream()
        finally:
            with self._cond:
                self._stopped = True
                self._cond.notify_all()
                try:
                    self._file.close()
                except OSError:
                    pass
        self._log_summary(time.monotonic() - started)
        return frozenset(state.name for state in self._states if state.delivered > 0)

    # ── надзор ──────────────────────────────────────────────────────────

    def _download_pieces(self) -> None:
        revivals = 0
        while True:
            self._supervise()
            if not self._left:
                return
            can_revive = (
                revivals < len(REVIVAL_PAUSES_SECONDS)
                and any(state.proven and not state.rejected for state in self._states)
            )
            if not can_revive:
                return
            pause = REVIVAL_PAUSES_SECONDS[revivals]
            revivals += 1
            log(f"Сеть пропала у всех источников — повтор через {pause:g} с", LOG_LEVEL)
            self._sleep(pause)
            with self._cond:
                for state in self._states:
                    if not state.rejected:
                        state.dead = False
                        state.failures = 0

    def _supervise(self) -> None:
        """Держит нужное число соединений, пока есть живые источники и работа."""
        while True:
            with self._cond:
                if self._write_error is not None:
                    raise self._write_error
                if self._token.is_cancelled:
                    raise UpdateCancelled("Обновление остановлено")
                finished = not self._left
                if not finished:
                    self._spawn_workers()
                stalled = not finished and not any(state.workers for state in self._states)
                done = self._done_bytes
                if not finished and not stalled:
                    self._cond.wait(TICK_SECONDS)
            self._report(done)
            if finished or stalled:
                return

    def _spawn_workers(self) -> None:
        alive = [state for state in self._states if not state.dead]
        if not alive:
            return
        share = min(MAX_CONNECTIONS_PER_SOURCE, max(1, MAX_CONNECTIONS // len(alive)))
        for state in alive:
            # Непроверенный источник получает одно соединение: мёртвый сервер
            # не должен занимать несколько потоков ожиданием.
            wanted = share if state.proven else 1
            while state.workers < wanted:
                state.workers += 1
                threading.Thread(
                    target=self._worker,
                    args=(state,),
                    name=f"update-download-{state.name}",
                    daemon=True,
                ).start()

    def _report(self, done: int) -> None:
        if done == self._last_reported:
            return
        self._last_reported = done
        percent = min(done * 100 // self._total, 100) if self._total > 0 else 0
        self._on_progress(percent, done, self._total)

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._token.checkpoint()
            time.sleep(min(TICK_SECONDS, max(deadline - time.monotonic(), 0.0)))

    # ── соединения ──────────────────────────────────────────────────────

    def _worker(self, state: _SourceState) -> None:
        session = new_session()
        session.headers.update({"Accept": "application/octet-stream", "Accept-Encoding": "identity"})
        try:
            while True:
                fetch = self._take(state)
                if fetch is None:
                    return
                error: BaseException | None = None
                try:
                    self._fetch(session, state, fetch)
                except BaseException as exc:  # noqa: BLE001 — разбор ниже, под замком
                    error = exc
                self._settle(state, fetch, error)
        finally:
            session.close()
            with self._cond:
                state.workers -= 1
                self._cond.notify_all()

    def _take(self, state: _SourceState) -> _Fetch | None:
        """Следующая работа для соединения или None, если ему пора закончить."""
        with self._cond:
            while True:
                if self._stopped or state.dead or not self._left:
                    return None
                now = time.monotonic()
                piece = self._pending.popleft() if self._pending else self._slow_piece(state, now)
                if piece is not None:
                    fetch = _Fetch(piece, state, now)
                    piece.fetches.append(fetch)
                    return fetch
                self._cond.wait(TICK_SECONDS)

    def _slow_piece(self, state: _SourceState, now: float) -> _Piece | None:
        """Кусок, который это соединение докачает заметно быстрее нынешнего."""
        own_speed = state.speed()
        best: _Piece | None = None
        best_eta = 0.0
        for piece in self._pieces:
            if piece.done or len(piece.fetches) != 1:
                continue
            current = piece.fetches[0]
            age = now - current.started_at
            if age < HEDGE_MIN_AGE_SECONDS:
                continue
            remaining = piece.size - piece.have
            current_speed = current.received / age
            current_eta = remaining / current_speed if current_speed > 0 else float("inf")
            # Без своих замеров перехватываем только явно застрявший кусок.
            own_eta = remaining / own_speed if own_speed else HEDGE_MIN_AGE_SECONDS
            if own_eta * HEDGE_ADVANTAGE < current_eta and current_eta > best_eta:
                best, best_eta = piece, current_eta
        return best

    def _fetch(self, session: requests.Session, state: _SourceState, fetch: _Fetch) -> None:
        piece = fetch.piece
        position = piece.start + fetch.begin
        end = piece.start + piece.size - 1
        with session.get(
            state.url,
            headers={"Range": f"bytes={position}-{end}"},
            stream=True,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            verify=bool(state.source.verify_ssl),
        ) as response:
            if response.status_code == 200:
                state.no_range = True
                raise _SourceRejected("сервер не отдаёт файл кусками")
            if response.status_code >= 500:
                # Временный сбой сервера — как обрыв связи, а не «файла нет».
                raise requests.HTTPError(response=response)
            if response.status_code != 206:
                raise _SourceRejected(f"сервер ответил HTTP {response.status_code}")
            match = _CONTENT_RANGE_RE.fullmatch(response.headers.get("Content-Range", "").strip())
            if match is None or int(match.group(1)) != position:
                raise _SourceRejected("сервер отдал не тот кусок файла")
            if int(match.group(3)) != self._total:
                raise _SourceRejected(
                    f"на сервере файл другого размера: {match.group(3)} вместо {self._total}"
                )
            for chunk in response.iter_content(chunk_size=READ_SIZE):
                if not chunk:
                    continue
                if position + len(chunk) > end + 1:
                    raise _SourceRejected("сервер отдал больше байт, чем просили")
                with self._cond:
                    if self._stopped or piece.done:
                        return
                    try:
                        self._file.seek(position)
                        self._file.write(chunk)
                    except OSError as exc:
                        raise _local_write_error(exc) from exc
                    position += len(chunk)
                    fetch.received += len(chunk)
                    if not state.proven and fetch.received >= PROVEN_BYTES:
                        state.proven = True
                        state.failures = 0
                    have = position - piece.start
                    if have > piece.have:
                        self._done_bytes += have - piece.have
                        piece.have = have
                    if piece.have >= piece.size:
                        piece.done = True
                        self._left -= 1
                        self._cond.notify_all()
                        return
        raise requests.ConnectionError("соединение оборвалось")

    def _settle(self, state: _SourceState, fetch: _Fetch, error: BaseException | None) -> None:
        """Итог одного соединения: учёт скорости, возврат куска в очередь, судьба источника."""
        piece = fetch.piece
        with self._cond:
            piece.fetches.remove(fetch)
            state.delivered += fetch.received
            state.busy_seconds += time.monotonic() - fetch.started_at
            if not piece.done and not piece.fetches:
                self._pending.appendleft(piece)
            if error is None:
                # Кусок дошёл — этим соединением или тем, кто его перехватил.
                if fetch.received > 0:
                    state.proven = True
                    state.failures = 0
            elif isinstance(error, LocalWriteError):
                self._write_error = self._write_error or error
                state.dead = True
            elif isinstance(error, _SourceRejected):
                self._drop(state, str(error), rejected=True)
            else:
                maybe_log_disable_dpi_for_update(error, scope="download", level="🔄 DOWNLOAD")
                self._network_failure(state, short_error(error))
            self._cond.notify_all()

    def _network_failure(self, state: _SourceState, reason: str) -> None:
        if not state.proven and state.url_index + 1 < len(state.urls):
            state.url_index += 1
            state.failures = 0
            log(f"Источник {state.name}: {reason}; пробуем запасной адрес", LOG_LEVEL)
            return
        state.failures += 1
        if state.failures >= SOURCE_FAILURE_LIMIT:
            self._drop(state, reason, rejected=False)

    def _drop(self, state: _SourceState, reason: str, *, rejected: bool) -> None:
        if not state.dead:
            log(f"Источник {state.name} не подошёл: {reason}", "WARNING")
        state.dead = True
        state.rejected = state.rejected or rejected
        state.reason = reason

    # ── запасной путь ───────────────────────────────────────────────────

    def _download_whole_stream(self) -> None:
        """Сервер не умеет отдавать куски: качаем файл одним потоком с начала."""
        candidates = [state for state in self._states if state.no_range]
        for state in candidates:
            self._token.checkpoint()
            log(f"Источник {state.name}: скачивание одним потоком", LOG_LEVEL)
            try:
                self._stream_whole(state)
            except (UpdateCancelled, LocalWriteError):
                raise
            except Exception as exc:
                state.reason = str(exc) if isinstance(exc, _SourceRejected) else short_error(exc)
                log(f"Источник {state.name} не подошёл: {state.reason}", "WARNING")
                continue
            self._left = 0
            return
        reasons = "; ".join(f"{state.name} — {state.reason or 'нет ответа'}" for state in self._states)
        raise UpdatePipelineError(f"Не удалось скачать обновление: {reasons}")

    def _stream_whole(self, state: _SourceState) -> None:
        session = new_session()
        session.headers.update({"Accept": "application/octet-stream", "Accept-Encoding": "identity"})
        position = 0
        reported_at = 0.0
        try:
            with session.get(
                state.url,
                stream=True,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                verify=bool(state.source.verify_ssl),
            ) as response:
                if response.status_code != 200:
                    raise _SourceRejected(f"сервер ответил HTTP {response.status_code}")
                length = response.headers.get("Content-Length", "")
                if length.isdigit() and int(length) != self._total:
                    raise _SourceRejected(f"на сервере файл другого размера: {length} вместо {self._total}")
                for chunk in response.iter_content(chunk_size=READ_SIZE):
                    self._token.checkpoint()
                    if not chunk:
                        continue
                    if position + len(chunk) > self._total:
                        raise _SourceRejected("сервер отдал больше байт, чем просили")
                    try:
                        self._file.seek(position)
                        self._file.write(chunk)
                    except OSError as exc:
                        raise _local_write_error(exc) from exc
                    position += len(chunk)
                    now = time.monotonic()
                    if now - reported_at >= TICK_SECONDS:
                        reported_at = now
                        self._report(position)
        finally:
            session.close()
        if position != self._total:
            raise requests.ConnectionError("соединение оборвалось")
        state.delivered = position
        self._report(position)

    def _log_summary(self, elapsed: float) -> None:
        parts = [
            f"{state.name} — {state.delivered / 1048576:.1f} МБ"
            for state in self._states
            if state.delivered > 0
        ]
        log(f"Файл получен за {elapsed:.1f} с: " + "; ".join(parts), LOG_LEVEL)


__all__ = ["MultiSourceDownload"]
