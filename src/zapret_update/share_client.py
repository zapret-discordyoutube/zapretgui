"""Склейка для обновления: поиск соседей идёт сам, пока считается план.

``start_search`` запускают в самом начале ``prepare`` (поиск занимает до
полутора секунд и идёт в фоне, пока программа сравнивает файлы на диске).
``fill_from_neighbours`` после плана забирает у найденных соседей всё, что
получится. Ни одна из функций не бросает ошибок наружу: соседи — подспорье, и
обновление без них должно идти ровно так же, как раньше.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from .plan import UpdatePlan

from . import share_discover as discover
from . import share_prefill as prefill

SEARCH_WAIT = discover.FIND_TIMEOUT + 1.0


class Search:
    def __init__(self, product: str, version: str, *, enabled: bool = True, find=discover.find_neighbours) -> None:
        self._found: list[discover.Neighbour] = []
        self._thread: threading.Thread | None = None
        if not enabled:
            return

        def run() -> None:
            try:
                self._found = list(find(product, version))
            except Exception:  # noqa: BLE001 — поиск не должен ронять обновление
                self._found = []

        self._thread = threading.Thread(target=run, name="share-search", daemon=True)
        self._thread.start()

    def result(self, timeout: float = SEARCH_WAIT) -> list[discover.Neighbour]:
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                return []
        return self._found


def start_search(product: str, version: str, *, enabled: bool = True) -> Search:
    return Search(product, version, enabled=enabled)


def fill_from_neighbours(
    plan: UpdatePlan,
    *,
    work_dir: Path,
    search: Search,
    cancelled: Callable[[], bool] = lambda: False,
    on_stage: Callable[[str], None] = lambda _text: None,
    report: Callable[[str], None] = lambda _text: None,
    fill=prefill.prefill_from_neighbours,
) -> prefill.PrefillResult:
    """Берёт файлы у соседей; при любой беде возвращает пустой итог."""
    try:
        neighbours = search.result()
        if not neighbours or not plan.want:
            return prefill.PrefillResult()
        on_stage("Скачивание у соседей по сети…")
        result = fill(plan, work_dir=work_dir, neighbours=neighbours, cancelled=cancelled)
        report(
            f"Соседи по сети: найдено {len(neighbours)}, взято файлов {result.files} "
            f"({result.bytes / 1048576:.1f} МБ), остальное — с серверов"
        )
        return result
    except Exception as exc:  # noqa: BLE001 — обновление идёт и без соседей
        report(f"Соседи по сети не помогли: {exc}")
        return prefill.PrefillResult()


__all__ = ["Search", "fill_from_neighbours", "start_search"]
