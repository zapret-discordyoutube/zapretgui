"""Скачивание файлов и заплаток из хранилища: с докачкой и запасными источниками.

Имя файла в хранилище — контрольная сумма содержимого, поэтому у всех
источников (Forgejo, зеркала) байты одни и те же: оборванный кусок докачивается
по ``Range`` с любого из них, в том числе после перезапуска программы.

Готовый файл распаковывается (заплатка — накладывается на файл с диска),
проверяется по контрольной сумме из подписанного списка и кладётся в
``<рабочая папка>/ready/<sha256>``. Файл, уже лежащий там с верной суммой,
второй раз не качается.

Только стандартная библиотека: так одинаково в обеих программах.
"""

from __future__ import annotations

import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import store
from .manifest import file_sha256, safe_relative_path
from .plan import UpdatePlan, Want

CHUNK = 256 * 1024
ROUNDS = 3
ROUND_PAUSES = (2.0, 5.0)
# Сколько источник может молчать: до первых байт ответа и между кусками.
# Молчит дольше — считаем зависшим и берём следующий. Живой источник отвечает
# за доли секунды; при выходе 21.1.7.207 перегруженный Forgejo отвечал по
# 30+ секунд, и с прежними 30 секундами каждый файл ждал его до обрыва.
STALL_TIMEOUT = 10.0


class FetchCancelled(Exception):
    """Скачивание остановлено по просьбе программы."""


class FetchError(Exception):
    """Файл не скачался ни с одного источника. ``code`` — причина для сервера."""

    def __init__(self, message: str, code: str = "net") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Source:
    """Один путь к хранилищу: как назвать ссылку на файл по его имени.

    ``proxy`` — адрес прокси (``http://127.0.0.1:порт``) или пусто: напрямую.
    Системный прокси Windows не берётся никогда — путь задаёт программа.
    """

    label: str
    url_for: Callable[[str], str]
    verify_ssl: bool = True
    proxy: str = ""


class _Progress:
    def __init__(self, total: int, callback: Callable[[int, int], None]) -> None:
        self.total = max(int(total), 0)
        self.done = 0
        self._callback = callback
        self._lock = threading.Lock()

    def add(self, amount: int) -> None:
        with self._lock:
            self.done += amount
            done = self.done
        try:
            self._callback(min(done, self.total), self.total)
        except Exception:  # noqa: BLE001 — показ хода не должен рвать скачивание
            pass


class _Ranking:
    """Общий для всех потоков порядок источников.

    Источник, который завис или отдал ошибку, уходит в конец очереди для всех
    файлов сразу: один зависший сервер стоит одно ожидание, а не по ожиданию
    на каждый файл в каждом потоке. Удачная загрузка возвращает ему место.
    """

    def __init__(self, sources: Sequence[Source]) -> None:
        self._sources = list(sources)
        self._strikes = [0] * len(self._sources)
        self._lock = threading.Lock()

    def order(self, skip: set[int]) -> list[int]:
        with self._lock:
            return sorted(
                (index for index in range(len(self._sources)) if index not in skip),
                key=lambda index: (self._strikes[index], index),
            )

    def source(self, index: int) -> Source:
        return self._sources[index]

    def failed(self, index: int) -> None:
        with self._lock:
            self._strikes[index] += 1

    def worked(self, index: int) -> None:
        with self._lock:
            self._strikes[index] = 0


def _opener(source: Source) -> urllib.request.OpenerDirector:
    context = ssl.create_default_context()
    if not source.verify_ssl:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    proxies = {"http": source.proxy, "https": source.proxy} if source.proxy else {}
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies), urllib.request.HTTPSHandler(context=context))


def _download_part(
    source: Source,
    name: str,
    part: Path,
    size: int,
    *,
    timeout: float,
    cancelled: Callable[[], bool],
    progress: _Progress,
) -> None:
    """Докачивает ``part`` до ``size`` байт с одного источника."""
    have = part.stat().st_size if part.exists() else 0
    if have > size:
        part.unlink()
        have = 0
    if have == size:
        return
    headers = {"User-Agent": "ZapretUpdate/1"}
    if have:
        headers["Range"] = f"bytes={have}-"
    request = urllib.request.Request(source.url_for(name), headers=headers)
    with _opener(source).open(request, timeout=timeout) as response:
        status = getattr(response, "status", 200)
        mode = "ab"
        if have and status != 206:
            # Источник не умеет докачку: начинаем этот файл сначала.
            progress.add(-have)
            have, mode = 0, "wb"
        with open(part, mode) as handle:
            while True:
                if cancelled():
                    raise FetchCancelled("скачивание остановлено")
                # read1 отдаёт то, что уже пришло: при обрыве посреди куска
                # полученные байты не теряются, и ход загрузки идёт ровно.
                chunk = response.read1(CHUNK)
                if not chunk:
                    break
                if have + len(chunk) > size:
                    raise FetchError(f"{source.label} отдал больше байт, чем в списке выпуска", "size")
                handle.write(chunk)
                have += len(chunk)
                progress.add(len(chunk))
    if have != size:
        raise ConnectionError(f"{source.label}: соединение оборвалось на {have} из {size} байт")


def _finish(want: Want, part: Path, ready: Path, root: Path) -> None:
    data = part.read_bytes()
    entry = want.entry
    if want.patch is None:
        content = store.unpack_blob(data, sha256=entry.sha256, size=entry.size)
    else:
        base = Path(root).joinpath(*safe_relative_path(entry.path).split("/"))
        content = store.apply_patch(
            base.read_bytes(), data, sha256=entry.sha256, size=entry.size, kind=want.patch.kind
        )
    temporary = ready.with_name(ready.name + ".tmp")
    temporary.write_bytes(content)
    os.replace(temporary, ready)
    part.unlink(missing_ok=True)


def _ready_ok(ready: Path, sha256: str, size: int) -> bool:
    try:
        return ready.stat().st_size == size and file_sha256(ready) == sha256
    except OSError:
        return False


def fetch_plan(
    plan: UpdatePlan,
    *,
    root: Path,
    work_dir: Path,
    sources: Sequence[Source],
    threads: int = 4,
    timeout: float = STALL_TIMEOUT,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[int, int], None] = lambda _done, _total: None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Path]:
    """Скачивает всё, что нужно плану; ``{sha256: готовый файл}``."""
    if not sources:
        raise FetchError("нет источников хранилища")
    work = Path(work_dir)
    parts_dir, ready_dir = work / "parts", work / "ready"
    parts_dir.mkdir(parents=True, exist_ok=True)
    ready_dir.mkdir(parents=True, exist_ok=True)
    tracker = _Progress(plan.download_size, progress)
    ranking = _Ranking(sources)
    result: dict[str, Path] = {}
    # Окончательная ошибка одного файла останавливает остальные: без него
    # версию всё равно не собрать.
    failed = threading.Event()

    def stopped() -> bool:
        return failed.is_set() or cancelled()

    def one(want: Want) -> None:
        ready = ready_dir / want.entry.sha256
        if _ready_ok(ready, want.entry.sha256, want.entry.size):
            tracker.add(want.download_size)
            result[want.entry.sha256] = ready
            return
        part = parts_dir / (want.name + ".part")
        if part.exists():
            tracker.add(min(part.stat().st_size, want.download_size))
        errors: list[str] = []
        broken = False
        for round_index in range(ROUNDS):
            tried: set[int] = set()
            # Порядок берётся заново перед каждой попыткой: пока этот файл ждал
            # один источник, соседние потоки могли узнать, что завис другой.
            while candidates := ranking.order(tried):
                index = candidates[0]
                tried.add(index)
                source = ranking.source(index)
                if stopped():
                    raise FetchCancelled("скачивание остановлено")
                try:
                    _download_part(
                        source, want.name, part, want.download_size,
                        timeout=timeout, cancelled=stopped, progress=tracker,
                    )
                    _finish(want, part, ready, root)
                    ranking.worked(index)
                    result[want.entry.sha256] = ready
                    return
                except FetchCancelled:
                    raise
                except store.StoreError as exc:
                    # Битый файл: этот кусок не годится ни с какого источника.
                    ranking.failed(index)
                    broken = True
                    if part.exists():
                        tracker.add(-part.stat().st_size)
                        part.unlink()
                    errors.append(f"{source.label}: {exc}")
                except FetchError as exc:
                    ranking.failed(index)
                    if part.exists():
                        tracker.add(-part.stat().st_size)
                        part.unlink()
                    errors.append(f"{source.label}: {exc}")
                except (OSError, urllib.error.URLError, ValueError) as exc:
                    ranking.failed(index)
                    errors.append(f"{source.label}: {exc}")
            if round_index < ROUNDS - 1:
                sleep(ROUND_PAUSES[min(round_index, len(ROUND_PAUSES) - 1)])
        raise FetchError(f"{want.entry.path} не скачался: " + "; ".join(errors[-3:]), "hash" if broken else "net")

    def guarded(want: Want) -> None:
        try:
            one(want)
        except BaseException:
            failed.set()
            raise

    errors: list[BaseException] = []
    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        for future in [pool.submit(guarded, want) for want in plan.want]:
            try:
                future.result()
            except BaseException as exc:  # noqa: BLE001 — собрать и отдать главную
                errors.append(exc)
    if errors:
        # Настоящая причина важнее «остановлено», которое она вызвала у соседей.
        raise next((exc for exc in errors if not isinstance(exc, FetchCancelled)), errors[0])
    return result


__all__ = ["STALL_TIMEOUT", "FetchCancelled", "FetchError", "Source", "fetch_plan"]
