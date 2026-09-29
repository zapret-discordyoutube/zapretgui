from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TelegramProxyDiagnosticsStartPlan:
    button_enabled: bool
    button_text: str
    initial_text: str
    poll_interval_ms: int


@dataclass(slots=True)
class TelegramProxyDiagnosticsPollPlan:
    updated_text: str | None
    should_stop_timer: bool
    should_finish: bool


@dataclass(slots=True)
class TelegramProxyDiagnosticsFinishPlan:
    button_enabled: bool
    button_text: str


@dataclass(slots=True)
class TelegramProxyActionResult:
    ok: bool
    log_line: str
    info_title: str
    info_content: str


TELEGRAM_HOSTS_ACTIONS = ("status", "add", "remove")


@dataclass(frozen=True, slots=True)
class TelegramHostsActionResult:
    """Итог действия с записями Telegram в hosts.

    ``status`` — состояние файла после действия (None, если его не удалось
    прочитать). ``message`` — текст успеха или ошибки для пользователя.
    """

    action: str
    ok: bool
    changed: bool
    message: str
    status: object | None


def build_diagnostics_start_plan() -> TelegramProxyDiagnosticsStartPlan:
    return TelegramProxyDiagnosticsStartPlan(
        button_enabled=False,
        button_text="Тестирование...",
        initial_text="Запуск диагностики Telegram DC...\n",
        poll_interval_ms=200,
    )


def build_diagnostics_poll_plan(*, result_text: str | None, thread_done: bool) -> TelegramProxyDiagnosticsPollPlan:
    return TelegramProxyDiagnosticsPollPlan(
        updated_text=result_text if result_text is not None else None,
        should_stop_timer=bool(thread_done),
        should_finish=bool(thread_done),
    )


def build_diagnostics_finish_plan() -> TelegramProxyDiagnosticsFinishPlan:
    return TelegramProxyDiagnosticsFinishPlan(
        button_enabled=True,
        button_text="Запустить диагностику",
    )
