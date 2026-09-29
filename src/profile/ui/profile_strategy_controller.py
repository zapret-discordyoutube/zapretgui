"""Контроллер применения готовых стратегий и оценок (feedback) profile.

Вынесено из profile_setup_page.py (этап 4, фаза B, чанк M4): машины
strategy apply (C14) и strategy feedback save (C15). Локальные применения
(`_apply_strategy_locally`, `_apply_strategy_feedback_locally`) остаются
методами страницы: они мутируют `_payload` и виджеты.

Контроллер не владеет состоянием: state-объекты, рантаймы и счётчики
request_id живут на странице — поведенческие тесты создают страницу через
`__new__` и присваивают эти атрибуты напрямую. Вызовы методов, которые
тесты подменяют на экземпляре страницы, идут через атрибут страницы.
"""

from __future__ import annotations

from profile.strategy_state import ProfileStrategyState
from profile.ui.profile_strategy_list_widget import _current_strategy_id
from ui.latest_value_worker_state import LatestValueWorkerState


def _page_module():
    """Модуль страницы через ленивый импорт.

    Разрывает циклический импорт со страницей и сохраняет monkeypatch-цели
    `profile.ui.profile_setup_page.*`: module-функции, `log`, `QTimer` и
    `InfoBar` патчатся тестами по пути модуля страницы и резолвятся здесь
    в момент вызова."""
    from profile.ui import profile_setup_page

    return profile_setup_page


_CONFIRMED_APPLY_STATUSES = frozenset({"applied", "already_applied"})


def _apply_result_confirms_write(apply_result) -> bool:
    """Подтвердил ли сервис, что выбранная стратегия лежит в файле пресета.

    Отсутствие результата — не подтверждение: без него страница держала бы
    оптимистичное состояние, не проверенное ни одним чтением пресета.
    """
    if apply_result is None:
        return False
    return str(getattr(apply_result, "status", "") or "").strip() in _CONFIRMED_APPLY_STATUSES


class ProfileStrategyController:
    """Stateless-оркестратор apply/feedback стратегий со ссылкой на страницу."""

    def __init__(self, page) -> None:
        self._page = page

    def _current_profile_reference(self) -> str:
        page = self._page
        return page._profile_result_reference(page.__dict__.get("_payload"), page._profile_key)

    def _effective_strategy_id(self) -> str:
        """Стратегия, которая окажется в пресете после всех записей открытого
        профиля: ожидающая, иначе записываемая сейчас, иначе из payload.

        Сравнение щелчка только с payload теряло щелчки: при записи B щелчок
        по A (ещё в payload) молча отбрасывался."""
        page = self._page
        current = self._current_profile_reference()
        pending = str(page._strategy_apply_state_obj().pending or "").strip()
        pending_key = str(page.__dict__.get("_strategy_apply_pending_profile_key") or "").strip()
        if pending and pending_key in {"", current}:
            return pending
        running = str(page.__dict__.get("_strategy_apply_runtime_strategy_id") or "").strip()
        running_key = str(page.__dict__.get("_strategy_apply_runtime_profile_key") or "").strip()
        if running and running_key in {"", current}:
            return running
        return _current_strategy_id(page.__dict__.get("_payload"))

    def _drop_pending_strategy_apply(self, profile_key: str) -> None:
        """Отменить ожидающее применение стратегии этого профиля."""
        page = self._page
        state = page._strategy_apply_state_obj()
        pending_key = str(page.__dict__.get("_strategy_apply_pending_profile_key") or "").strip()
        if pending_key in {"", profile_key}:
            state.pending = None
            page.__dict__.pop("_strategy_apply_pending_profile_key", None)
        write_state = page._profile_setup_write_state_obj()
        write_state.pending[:] = [
            operation
            for operation in write_state.pending
            if not (
                str(operation.get("kind") or "") == "strategy_apply"
                and str(operation.get("profile_key") or "").strip() in {"", profile_key}
            )
        ]
        scheduled = page.__dict__.get("_scheduled_profile_setup_write_operation")
        if (
            isinstance(scheduled, dict)
            and str(scheduled.get("kind") or "") == "strategy_apply"
            and str(scheduled.get("profile_key") or "").strip() in {"", profile_key}
        ):
            page._scheduled_profile_setup_write_operation = None

    def _request_strategy_apply(self, strategy_id: str) -> None:
        page = self._page
        strategy_id = str(strategy_id or "").strip()
        # Ключ фиксируется при щелчке: к выполнению отложенного применения
        # страница может показывать уже другой профиль.
        profile_key = self._current_profile_reference()
        if page._profile_setup_write_is_running():
            running_strategy_id = str(getattr(page, "_strategy_apply_runtime_strategy_id", "") or "").strip()
            running_profile_key = str(page.__dict__.get("_strategy_apply_runtime_profile_key") or "").strip()
            if strategy_id == running_strategy_id and running_profile_key in {"", profile_key}:
                # Вернулись к стратегии, которая пишется прямо сейчас: она и
                # должна остаться, ожидающая после неё — отменяется.
                self._drop_pending_strategy_apply(profile_key)
                return
            page._strategy_apply_state_obj().pending = strategy_id
            page._strategy_apply_pending_profile_key = profile_key
            page._queue_profile_setup_write_operation(
                {
                    "kind": "strategy_apply",
                    "strategy_id": strategy_id,
                    "profile_key": profile_key,
                }
            )
            return
        page._start_strategy_apply_worker(strategy_id)

    def _start_strategy_apply_worker(self, strategy_id: str, profile_key: str = "") -> None:
        page = self._page
        strategy_id = str(strategy_id or "").strip()
        if not strategy_id or not page._profile_key:
            return
        runtime = page._worker_runtime("_strategy_apply_runtime")
        page._strategy_apply_request_id = int(getattr(page, "_strategy_apply_request_id", 0) or 0) + 1
        request_id = page._strategy_apply_request_id
        page._strategy_apply_runtime_strategy_id = strategy_id
        # Стабильная ссылка вместо возможного "profile:N": позиционный ключ,
        # захваченный при открытии страницы, после сдвига соседей резолвится
        # в чужой профиль — и стратегия уходит не туда. Для отложенного
        # применения ключ приходит из операции (зафиксирован при щелчке).
        profile_key = str(profile_key or "").strip() or self._current_profile_reference()
        page._strategy_apply_runtime_profile_key = profile_key
        runtime.start_qthread_worker(
            worker_factory=lambda _runtime_request_id: page.create_profile_strategy_apply_worker(
                request_id,
                profile_key=profile_key,
                strategy_id=strategy_id,
                parent=page,
            ),
            on_loaded=page._on_strategy_apply_finished,
            on_failed=page._on_strategy_apply_failed,
            on_finished=page._on_strategy_apply_worker_finished,
            loaded_signal_name="applied",
        )

    def _on_strategy_apply_finished(
        self,
        request_id: int,
        requested_profile_key: str,
        profile_key: str,
        strategy_id: str,
        payload=None,
    ) -> None:
        page = self._page
        if request_id != int(getattr(page, "_strategy_apply_request_id", 0) or 0):
            return
        requested = str(requested_profile_key or "").strip()
        current = str(page._profile_key or "").strip()
        # Страница могла принять persistent-ссылку уже после старта запроса —
        # позиционный ключ того же профиля не повод выбрасывать результат.
        item_key = str(getattr(getattr(page.__dict__.get("_payload"), "item", None), "key", "") or "").strip()
        if requested not in {current, item_key, self._current_profile_reference()}:
            # Стратегия записана в профиль, открытый при щелчке, а страница уже
            # показывает другой: её не трогаем, но список профилей узнаёт о
            # записи (он пропускает ревизии strategy_only и ждёт этот сигнал).
            result_item = getattr(
                _page_module()._profile_setup_payload_and_apply_signature(payload)[0] if payload is not None else None,
                "item",
                None,
            )
            written_key = str(profile_key or requested).strip()
            if written_key:
                if result_item is not None:
                    page._on_profile_changed_callback(written_key, "strategy", result_item)
                else:
                    page._on_profile_changed_callback(written_key, "strategy")
            return
        pending_strategy_id = str(page._strategy_apply_state_obj().pending or "").strip()
        pending_profile_key = str(page.__dict__.get("_strategy_apply_pending_profile_key") or "").strip()
        # Ожидающая стратегия ДРУГОГО профиля не делает этот результат устаревшим.
        if (
            pending_strategy_id
            and pending_profile_key in {"", current, item_key, self._current_profile_reference()}
            and pending_strategy_id != str(strategy_id or "").strip()
        ):
            return
        apply_result = _page_module()._profile_setup_apply_result_from_worker_result(payload)
        result_payload, apply_signature = (
            _page_module()._profile_setup_payload_and_apply_signature(payload)
            if payload is not None
            else (None, None)
        )
        previous_key = page._profile_key
        new_key = page._profile_result_reference(result_payload, profile_key)
        if new_key:
            page._profile_key = new_key
        self._report_strategy_write_rejected(apply_result)
        self._report_strategy_blob_warnings(apply_result)
        if apply_result is not None and bool(getattr(apply_result, "should_reload", False)):
            if result_payload is not None:
                page._payload = result_payload
                page._schedule_profile_setup_payload_apply(result_payload, apply_signature=apply_signature)
                page._on_profile_changed_callback(
                    page._profile_key,
                    "strategy",
                    getattr(result_payload, "item", None),
                )
                return
            page.reload_current_profile()
            page._on_profile_changed_callback(page._profile_key, "strategy")
            return
        if not _apply_result_confirms_write(apply_result):
            # Сервис не подтвердил, что стратегия действительно легла в файл:
            # отметка выбора недостоверна, факт берём из пресета.
            page.reload_current_profile()
            page._on_profile_changed_callback(page._profile_key, "strategy")
            return
        item = getattr(getattr(page, "_payload", None), "item", None)
        if page._profile_key == previous_key and strategy_id == _current_strategy_id(page._payload):
            page._on_profile_changed_callback(page._profile_key, "strategy", item)
            return
        if result_payload is not None:
            page._payload = result_payload
            page._schedule_profile_setup_payload_apply(result_payload, apply_signature=apply_signature)
            page._on_profile_changed_callback(
                page._profile_key,
                "strategy",
                getattr(result_payload, "item", None),
            )
            return
        # Запись подтверждена, но payload не приехал: состояние всё равно
        # берём из пресета, а не достраиваем на странице.
        page.reload_current_profile()
        page._on_profile_changed_callback(page._profile_key, "strategy")

    def _report_strategy_write_rejected(self, apply_result) -> None:
        """Сообщить пользователю, что выбор не записан в пресет.

        Молчаливый отказ — худший исход: пользователь уверен, что стратегия
        применена, а в бой уходит прежняя.
        """
        page = self._page
        status = str(getattr(apply_result, "status", "") or "").strip()
        if status not in {"write_failed", "not_applicable"}:
            return
        message = str(getattr(apply_result, "message", "") or "").strip()
        _page_module().log(
            f"{page.__class__.__name__}: стратегия не записана в пресет: {status} {message}".strip(),
            "ERROR",
        )
        _page_module().InfoBar.warning(
            title="Стратегия не применена",
            content="Выбор не записан в пресет — показано состояние из файла.",
            parent=page.window(),
        )

    def _report_strategy_blob_warnings(self, apply_result) -> None:
        """Стратегия записана, но часть её фейков (--blob=) не объявлена в пресете."""
        warnings = tuple(getattr(apply_result, "blob_warnings", ()) or ())
        if not warnings:
            return
        _page_module().InfoBar.warning(
            title="Фейки стратегии не объявлены",
            content="\n".join(warnings),
            parent=self._page.window(),
        )

    def _on_strategy_apply_failed(self, request_id: int, error: str) -> None:
        page = self._page
        if request_id != int(getattr(page, "_strategy_apply_request_id", 0) or 0):
            return
        if page._strategy_apply_state_obj().has_pending():
            return
        _page_module().log(f"{page.__class__.__name__}: не удалось применить стратегию: {error}", "ERROR")
        page.reload_current_profile()

    def _on_strategy_apply_worker_finished(self, worker) -> None:
        page = self._page
        accepted, scheduled = page._schedule_next_profile_setup_write_operation_after_finish(
            "_strategy_apply_request_id",
            worker,
        )
        if not accepted:
            return
        page._strategy_apply_runtime_strategy_id = ""
        page._strategy_apply_runtime_profile_key = ""
        if scheduled:
            return
        pending = page._strategy_apply_state_obj().pending
        page._strategy_apply_state_obj().pending = None
        pending_profile_key = str(page.__dict__.pop("_strategy_apply_pending_profile_key", "") or "").strip()
        if pending:
            operation = {
                "kind": "strategy_apply",
                "strategy_id": str(pending or ""),
            }
            if pending_profile_key:
                operation["profile_key"] = pending_profile_key
            page._schedule_profile_setup_write_operation_start(operation)

    def _strategy_apply_state_obj(self) -> LatestValueWorkerState:
        page = self._page
        state = page.__dict__.get("_strategy_apply_state")
        runtime = page.__dict__.get("_strategy_apply_runtime")
        if state is None:
            pending = page.__dict__.pop("_pending_strategy_apply", None)
            state = LatestValueWorkerState(
                runtime,
                empty_value=None,
                pending=pending,
            )
            page.__dict__["_strategy_apply_state"] = state
        elif getattr(state, "runtime", None) is None and runtime is not None:
            state.runtime = runtime
        return state

    def _request_strategy_feedback_save(self, request: dict) -> None:
        page = self._page
        runtime = page._worker_runtime("_strategy_feedback_save_runtime")
        if page._strategy_feedback_save_state_obj().is_busy():
            page._merge_pending_strategy_feedback_save(request)
            return
        page._start_strategy_feedback_save_worker(request)

    def _merge_pending_strategy_feedback_save(self, request: dict) -> None:
        page = self._page
        state = page._strategy_feedback_save_state_obj()
        pending = dict(state.pending or {})
        next_request = dict(request or {})
        for key in ("rating", "favorite"):
            if key in next_request and next_request.get(key) is not None:
                pending[key] = next_request.get(key)
            elif key not in pending:
                pending[key] = next_request.get(key)
        state.pending = pending

    def _start_strategy_feedback_save_worker(self, request: dict) -> None:
        page = self._page
        if not page._profile_key:
            return
        item = getattr(getattr(page, "_payload", None), "item", None)
        strategy_id = _current_strategy_id(page._payload)
        if not strategy_id or strategy_id in {"none", "custom"}:
            return
        runtime = page._worker_runtime("_strategy_feedback_save_runtime")
        page._strategy_feedback_save_request_id = int(getattr(page, "_strategy_feedback_save_request_id", 0) or 0) + 1
        request_id = page._strategy_feedback_save_request_id
        runtime.start_qthread_worker(
            worker_factory=lambda _runtime_request_id: page.create_profile_strategy_feedback_save_worker(
                request_id,
                profile_key=page._profile_key,
                strategy_id=strategy_id,
                rating=request.get("rating"),
                favorite=request.get("favorite"),
                parent=page,
            ),
            on_loaded=page._on_strategy_feedback_save_finished,
            on_failed=page._on_strategy_feedback_save_failed,
            on_finished=page._on_strategy_feedback_save_worker_finished,
            loaded_signal_name="saved",
        )

    def _on_strategy_feedback_save_finished(
        self,
        request_id: int,
        profile_key: str,
        strategy_id: str,
        state,
    ) -> None:
        page = self._page
        if request_id != int(getattr(page, "_strategy_feedback_save_request_id", 0) or 0):
            return
        if page._strategy_feedback_save_state_obj().has_pending():
            return
        if str(profile_key or "").strip() != str(page._profile_key or "").strip():
            return
        item = getattr(getattr(page, "_payload", None), "item", None)
        current_strategy_id = _current_strategy_id(page._payload)
        if str(strategy_id or "").strip() != current_strategy_id:
            return
        next_state = state if isinstance(state, ProfileStrategyState) else ProfileStrategyState()
        current_state = getattr(getattr(page, "_payload", None), "current_strategy_state", None)
        if (
            current_state == next_state
            and str(getattr(item, "rating", "") or "") == str(getattr(next_state, "rating", "") or "")
            and bool(getattr(item, "favorite", False)) == bool(getattr(next_state, "favorite", False))
        ):
            return
        if not page._apply_strategy_feedback_locally(state):
            page.reload_current_profile()
            page._on_profile_changed_callback(page._profile_key, "feedback")
            return
        item = getattr(getattr(page, "_payload", None), "item", None)
        page._on_profile_changed_callback(page._profile_key, "feedback", item)

    def _on_strategy_feedback_save_failed(self, request_id: int, error: str) -> None:
        page = self._page
        if request_id != int(getattr(page, "_strategy_feedback_save_request_id", 0) or 0):
            return
        if page._strategy_feedback_save_state_obj().has_pending():
            return
        _page_module().log(f"{page.__class__.__name__}: не удалось обновить оценку стратегии: {error}", "ERROR")
        page.reload_current_profile()

    def _on_strategy_feedback_save_worker_finished(self, worker) -> None:
        page = self._page
        if not page._accept_current_profile_setup_worker_finished("_strategy_feedback_save_request_id", worker):
            return
        if page._strategy_feedback_save_state_obj().has_pending():
            page._schedule_strategy_feedback_save_worker_start()

    def _schedule_strategy_feedback_save_worker_start(self) -> None:
        page = self._page
        state = page._strategy_feedback_save_state_obj()

        def _single_shot(delay: int, callback) -> None:
            try:
                _page_module().QTimer.singleShot(delay, callback)
            except Exception:
                callback()

        state.schedule_start(_single_shot, page._run_scheduled_strategy_feedback_save_worker_start)

    def _run_scheduled_strategy_feedback_save_worker_start(self) -> None:
        page = self._page
        request = page._strategy_feedback_save_state_obj().take_pending_for_scheduled_start(
            cleanup_in_progress=page.__dict__.get("_cleanup_in_progress", False),
        )
        if not request:
            return
        page._start_strategy_feedback_save_worker(dict(request or {}))

    def _strategy_feedback_save_state_obj(self) -> LatestValueWorkerState:
        page = self._page
        state = page.__dict__.get("_strategy_feedback_save_state")
        runtime = page.__dict__.get("_strategy_feedback_save_runtime")
        if state is None:
            pending = page.__dict__.pop("_pending_strategy_feedback_save", None)
            start_scheduled = bool(page.__dict__.pop("_strategy_feedback_save_start_scheduled", False))
            state = LatestValueWorkerState(
                runtime,
                empty_value=None,
                pending=pending,
                start_scheduled=start_scheduled,
            )
            page.__dict__["_strategy_feedback_save_state"] = state
        elif getattr(state, "runtime", None) is None and runtime is not None:
            state.runtime = runtime
        return state
