from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from ui.performance_metrics import log_ui_timing_since


@dataclass(frozen=True, slots=True)
class HostsFeature:
    refresh_applied_selection: Callable
    warm_page_data_cache: Callable
    peek_page_snapshot: Callable
    get_hosts_path_str: Callable
    create_snapshot_worker: Callable
    create_apply_worker: Callable
    create_open_hosts_file_worker: Callable
    create_permission_restore_worker: Callable
    create_file_text_worker: Callable
    create_file_save_worker: Callable


def build_hosts_feature() -> HostsFeature:
    def _public():
        from hosts import public as hosts_public

        return hosts_public

    def _warm_page_data_cache() -> bool:
        # Разогревает кэши каталога и hosts: первое открытие страницы
        # получит снимок за миллисекунды. Сам снимок не хранится.
        started_at = time.perf_counter()
        _public().load_page_snapshot()
        log_ui_timing_since("warmup", "hosts", "hosts_warmup.snapshot", started_at, important=True)
        return True

    def _peek_page_snapshot():
        from hosts.page_snapshot import peek_page_snapshot

        return peek_page_snapshot()

    def _call_worker(request_id: int, call: Callable[[], object], *, name: str, parent=None):
        from hosts.call_worker import HostsCallWorker

        return HostsCallWorker(request_id, call, name=name, parent=parent)

    def _create_snapshot_worker(request_id: int, parent=None):
        return _call_worker(
            request_id,
            lambda: _public().load_page_snapshot(),
            name="snapshot",
            parent=parent,
        )

    def _create_apply_worker(request_id: int, selection: dict[str, str], adobe: bool | None, parent=None):
        selection = dict(selection or {})
        return _call_worker(
            request_id,
            lambda: _public().apply_hosts_draft(selection, adobe),
            name="apply",
            parent=parent,
        )

    def _create_open_hosts_file_worker(request_id: int, parent=None):
        return _call_worker(
            request_id,
            lambda: _public().open_hosts_file(),
            name="open",
            parent=parent,
        )

    def _create_permission_restore_worker(request_id: int, parent=None):
        return _call_worker(
            request_id,
            lambda: _public().restore_hosts_permissions(),
            name="restore_permissions",
            parent=parent,
        )

    def _create_file_text_worker(request_id: int, parent=None):
        return _call_worker(
            request_id,
            lambda: _public().load_hosts_text(),
            name="file_text",
            parent=parent,
        )

    def _create_file_save_worker(request_id: int, text: str, parent=None):
        text = str(text or "")
        return _call_worker(
            request_id,
            lambda: _public().save_hosts_text(text),
            name="file_save",
            parent=parent,
        )

    return HostsFeature(
        refresh_applied_selection=lambda *args, **kwargs: _public().refresh_applied_selection(*args, **kwargs),
        warm_page_data_cache=_warm_page_data_cache,
        # Только память, без чтения файлов: безопасно звать из UI-потока.
        peek_page_snapshot=lambda: _peek_page_snapshot(),
        get_hosts_path_str=lambda *args, **kwargs: _public().get_hosts_path_str(*args, **kwargs),
        create_snapshot_worker=_create_snapshot_worker,
        create_apply_worker=_create_apply_worker,
        create_open_hosts_file_worker=_create_open_hosts_file_worker,
        create_permission_restore_worker=_create_permission_restore_worker,
        create_file_text_worker=_create_file_text_worker,
        create_file_save_worker=_create_file_save_worker,
    )
