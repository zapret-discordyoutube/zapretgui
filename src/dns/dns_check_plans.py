from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class DNSCheckStartPlan:
    status_text: str
    status_tone: str
    check_enabled: bool
    save_enabled: bool
    progress_visible: bool


@dataclass(slots=True)
class DNSResultLinePlan:
    color_role: str


@dataclass(slots=True)
class DNSCheckFinishPlan:
    status_text: str
    status_tone: str
    check_enabled: bool
    save_enabled: bool
    progress_visible: bool


@dataclass(slots=True)
class DNSCheckCleanupPlan:
    should_quit_thread: bool
    wait_timeout_ms: int


@dataclass(slots=True)
class DNSSaveResultPlan:
    success: bool
    title: str
    content: str

def build_start_plan() -> DNSCheckStartPlan:
    return DNSCheckStartPlan(
        status_text="🔄 Выполняется проверка DNS...",
        status_tone="accent",
        check_enabled=False,
        save_enabled=False,
        progress_visible=True,
    )

def build_result_line_plan(text: str) -> DNSResultLinePlan:
    raw = str(text or "")
    if "✅" in raw:
        return DNSResultLinePlan(color_role="success")
    if "❌" in raw:
        return DNSResultLinePlan(color_role="error")
    if "⚠️" in raw:
        return DNSResultLinePlan(color_role="warning")
    if "🚫" in raw:
        return DNSResultLinePlan(color_role="blocked")
    if "🔍" in raw or "📊" in raw:
        return DNSResultLinePlan(color_role="accent")
    if "=" in raw and len(raw) > 20:
        return DNSResultLinePlan(color_role="faint")
    return DNSResultLinePlan(color_role="normal")

def build_finish_plan(results: dict) -> DNSCheckFinishPlan:
    poisoning_detected = bool(results and results.get("summary", {}).get("dns_poisoning_detected"))
    if poisoning_detected:
        return DNSCheckFinishPlan(
            status_text="⚠️ Обнаружена DNS подмена!",
            status_tone="error",
            check_enabled=True,
            save_enabled=True,
            progress_visible=False,
        )
    return DNSCheckFinishPlan(
        status_text="✅ Проверка завершена",
        status_tone="success",
        check_enabled=True,
        save_enabled=True,
        progress_visible=False,
    )

def build_cleanup_plan(*, has_thread: bool, thread_running: bool) -> DNSCheckCleanupPlan:
    return DNSCheckCleanupPlan(
        should_quit_thread=bool(has_thread and thread_running),
        wait_timeout_ms=500,
    )

def build_save_default_filename() -> str:
    return f"dns_check_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
