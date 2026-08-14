"""Запуск синхронизации удалённых пресетов: общий раннер и QThread-воркер.

Раннер sync_remote_preset_by_file_name собирает callables для ядра
remote_sync и персистит обновления binding-а. Используется фоновым
автосинком (подсистемная очередь "presets") и ручным «Обновить из
источника» (QThread со страницы). In-flight guard защищает от гонки
«фоновый тик против ручного клика».
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log

_IN_FLIGHT: set[tuple[str, str]] = set()
_IN_FLIGHT_LOCK = threading.Lock()


def _try_acquire(scope: str, file_name: str) -> bool:
    key = (scope, file_name)
    with _IN_FLIGHT_LOCK:
        if key in _IN_FLIGHT:
            return False
        _IN_FLIGHT.add(key)
        return True


def _release(scope: str, file_name: str) -> None:
    with _IN_FLIGHT_LOCK:
        _IN_FLIGHT.discard((scope, file_name))


def sync_remote_preset_by_file_name(presets_feature, launch_method: str, file_name: str, *, force: bool = False):
    """Синхронизирует один привязанный пресет; возвращает RemoteSyncOutcome.

    Вызывать только из рабочего потока (сеть и диск). Обновлённые поля
    binding-а сохраняются в settings store здесь же.
    """
    from presets.remote_bindings import get_remote_preset_binding, set_remote_preset_binding
    from presets.remote_sync import (
        STATUS_SKIPPED,
        RemoteSyncOutcome,
        fetch_remote_preset_text,
        sync_remote_preset,
        utc_now_iso,
    )

    from settings.mode import ENGINE_BY_LAUNCH_METHOD, ENGINE_WINWS2, normalize_launch_method

    scope = ENGINE_BY_LAUNCH_METHOD.get(normalize_launch_method(launch_method), ENGINE_WINWS2)
    binding = get_remote_preset_binding(scope, file_name)
    if binding is None:
        return RemoteSyncOutcome(status="error", detail="Пресет не привязан к источнику")

    if not _try_acquire(scope, file_name):
        return RemoteSyncOutcome(status=STATUS_SKIPPED, detail="Синхронизация уже идёт")
    try:
        def _read_current_text():
            try:
                return presets_feature.read_preset_source_by_file_name(launch_method, file_name)
            except Exception:
                return None

        def _fetch(url, etag, last_modified):
            return fetch_remote_preset_text(url, etag=etag, last_modified=last_modified)

        def _save_text(text):
            # Локальная identity пресета (имя в списке) не должна затираться
            # заголовком из источника: переписываем шапку на локальное имя.
            from presets.preset_text_ops import _rewrite_preset_headers

            local_name = file_name
            try:
                manifest = presets_feature.get_preset_manifest_by_file_name(launch_method, file_name)
                local_name = str(getattr(manifest, "name", "") or "").strip() or file_name
            except Exception:
                pass
            text = _rewrite_preset_headers(text, local_name, preset_kind="imported")
            presets_feature.save_preset_source_by_file_name(
                launch_method,
                file_name,
                text,
                publish_content_changed=True,
                content_change_kind="remote_sync",
            )
            try:
                return presets_feature.read_preset_source_by_file_name(launch_method, file_name)
            except Exception:
                return None

        outcome = sync_remote_preset(
            binding,
            engine=scope,
            read_current_text=_read_current_text,
            fetch=_fetch,
            save_text=_save_text,
            now_iso=utc_now_iso(),
            force=force,
        )
        if outcome.binding_updates:
            updated_binding = dict(binding)
            updated_binding.update(outcome.binding_updates)
            set_remote_preset_binding(scope, file_name, updated_binding)
        return outcome
    finally:
        _release(scope, file_name)


class RemotePresetSyncWorker(QThread):
    """Ручные действия remote-пресета со страницы: update / unlink."""

    completed = pyqtSignal(int, str, object, object)  # request_id, action, outcome, context
    failed = pyqtSignal(int, str, str, object)

    def __init__(
        self,
        request_id: int,
        run_remote_action,
        *,
        action: str,
        file_name: str,
        display_name: str = "",
        force: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._run_remote_action = run_remote_action
        self._action = str(action or "")
        self._file_name = str(file_name or "")
        self._display_name = str(display_name or "")
        self._force = bool(force)

    def run(self) -> None:
        context = {"file_name": self._file_name, "display_name": self._display_name}
        try:
            outcome = self._run_remote_action(
                action=self._action,
                file_name=self._file_name,
                force=self._force,
            )
        except Exception as exc:
            log(f"Действие remote-пресета ({self._action}) не выполнено: {exc}", "ERROR")
            self.failed.emit(self._request_id, self._action, str(exc), context)
            return
        self.completed.emit(self._request_id, self._action, outcome, context)
