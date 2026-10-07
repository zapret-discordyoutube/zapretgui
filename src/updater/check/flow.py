from __future__ import annotations

"""Ручная проверка обновлений целиком — без Qt и без окон.

1. Одновременно: поиск новейшего выпуска своего канала и строки таблицы
   источников; Telegram — отдельно, его никто не ждёт.
2. Итог даёт только поиск выпуска. Нашёлся выпуск — проверка закончена
   сразу: таблица лишь показывает, кто отвечает, и дозаполняется сама.
   Молчащее зеркало не задерживает ответ «есть обновление».
3. Если выпуск узнать не удалось, таблицу приходится дождаться: по ней
   видно, ответил ли хоть один источник. Никто не ответил, а DPI
   работает, — один повтор с остановленным DPI. DPI запускается обратно в
   ``finally``: он не может остаться выключенным, что бы ни случилось.

Результат — выпуск или понятная ошибка; «не удалось» никогда не
превращается в «обновлений нет».
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from log.log import log

from ..dpi_guard import DpiGuard
from ..release.http import BackgroundCall, short_error
from ..release.resolver import ReleaseLookup, lookup_latest_release
from .sources import EmitRow, probe_update_sources, start_telegram_probe


UPDATE_LOG_LEVEL = "🔄 UPDATE"


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    release: dict[str, Any] | None
    error: str = ""


def _single_pass(
    channel: str,
    *,
    language: str,
    emit_row: EmitRow,
    lookup: Callable[[str], ReleaseLookup],
    probe: Callable[..., bool],
) -> tuple[ReleaseLookup, BackgroundCall[bool]]:
    """Поиск выпуска и запущенный рядом опрос таблицы, которого никто не ждёт."""
    probe_call = BackgroundCall(lambda: probe(language=language, emit_row=emit_row), name="update-check-sources")
    try:
        result = lookup(channel)
    except Exception as exc:
        result = ReleaseLookup(None, f"Не удалось узнать новейшую версию: {short_error(exc)}")
    return result, probe_call


def _any_source_online(probe_call: BackgroundCall[bool]) -> bool:
    """Ждёт таблицу: у опроса источников свой срок, дольше него он не идёт."""
    outcome = probe_call.wait(None)
    if outcome is None or outcome.error is not None:
        return False
    return bool(outcome.value)


def run_update_check(
    channel: str,
    *,
    language: str,
    emit_row: EmitRow,
    dpi: DpiGuard | None,
    lookup: Callable[[str], ReleaseLookup] = lookup_latest_release,
    probe: Callable[..., bool] = probe_update_sources,
    probe_telegram: Callable[..., Any] = start_telegram_probe,
) -> CheckOutcome:
    probe_telegram(language=language, emit_row=emit_row)
    result, probe_call = _single_pass(
        channel, language=language, emit_row=emit_row, lookup=lookup, probe=probe
    )
    if result.ok:
        return CheckOutcome(dict(result.release or {}))

    if dpi is not None and dpi.is_running() and not _any_source_online(probe_call):
        log("⚠️ Источники обновлений недоступны при работающем DPI — один повтор без DPI", UPDATE_LOG_LEVEL)
        try:
            if dpi.stop(reason="server_status_probe_retry", update_runtime_state=True):
                result, _probe_call = _single_pass(
                    channel, language=language, emit_row=emit_row, lookup=lookup, probe=probe
                )
        except Exception as exc:
            log(f"Повтор проверки без DPI не удался: {exc}", "WARNING")
        finally:
            dpi.restore()

    if result.ok:
        return CheckOutcome(dict(result.release or {}))
    return CheckOutcome(None, result.error or "Не удалось узнать новейшую версию")


__all__ = ["CheckOutcome", "run_update_check"]
