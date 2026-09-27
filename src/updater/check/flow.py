from __future__ import annotations

"""Ручная проверка обновлений целиком — без Qt и без окон.

1. Одновременно: поиск новейшего выпуска своего канала и строки таблицы
   источников; Telegram — отдельно, его никто не ждёт.
2. Если выпуск узнать не удалось, ни один источник не ответил, а DPI
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
) -> tuple[ReleaseLookup, bool]:
    lookup_call = BackgroundCall(lambda: lookup(channel), name="update-check-lookup")
    any_online = probe(language=language, emit_row=emit_row)
    outcome = lookup_call.wait(None)
    if outcome is None or outcome.error is not None:
        reason = short_error(outcome.error) if outcome is not None and outcome.error is not None else "нет ответа"
        return ReleaseLookup(None, f"Не удалось узнать новейшую версию: {reason}"), any_online
    return outcome.value, any_online


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
    result, any_online = _single_pass(
        channel, language=language, emit_row=emit_row, lookup=lookup, probe=probe
    )

    if not result.ok and not any_online and dpi is not None and dpi.is_running():
        log("⚠️ Источники обновлений недоступны при работающем DPI — один повтор без DPI", UPDATE_LOG_LEVEL)
        try:
            if dpi.stop(reason="server_status_probe_retry", update_runtime_state=True):
                result, _any_online = _single_pass(
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
