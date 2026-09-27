from __future__ import annotations

"""Строки диагностической таблицы: Forgejo, каждое зеркало и Telegram.

Все источники опрашиваются одновременно и свежо, строки приходят по мере
ответов. Таблица только показывает, кто отвечает, — она никогда не создаёт
предложение обновиться. Telegram опрашивается отдельно и никого не ждёт и
никого не задерживает.
"""

import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from app.ui_texts import tr as tr_catalog
from config.build_info import CHANNEL
from config.config import CHANNEL_DEV, CHANNEL_STABLE

from ..channel_utils import normalize_update_channel
from ..release import forgejo, mirrors, telegram
from ..release.http import short_error


FORGEJO_ROW_NAME = "Forgejo API"
TELEGRAM_ROW_NAME = "Telegram"
SOURCES_DEADLINE_SECONDS = 30.0

EmitRow = Callable[[str, dict], None]


def _tr(language: str, key: str, default: str) -> str:
    return tr_catalog(key, language=language, default=default)


def _forgejo_row(language: str) -> tuple[str, dict]:
    try:
        response_time = forgejo.probe_forgejo()
    except Exception as exc:
        return FORGEJO_ROW_NAME, {"status": "error", "error": short_error(exc)[:50], "update_source": True}
    return FORGEJO_ROW_NAME, {
        "status": "online",
        "response_time": response_time,
        "details": _tr(language, "page.servers.status.api_available", "API доступен"),
        "update_source": True,
    }


def _mirror_row(server: dict[str, Any]) -> tuple[str, dict]:
    name = str(server.get("name") or server.get("host"))
    started = time.monotonic()
    try:
        data, _protocol, _base_url, _verify, response_time = mirrors.fetch_versions(server)
    except Exception as exc:
        return name, {
            "status": "error",
            "response_time": time.monotonic() - started,
            "error": str(exc)[:80],
            "is_current": False,
            "update_source": True,
        }
    stable = data.get("stable") if isinstance(data.get("stable"), dict) else {}
    dev = data.get("dev") if isinstance(data.get("dev"), dict) else {}
    return name, {
        "status": "online",
        "response_time": response_time,
        "stable_version": stable.get("version", "—"),
        "dev_version": dev.get("version", "—"),
        "stable_notes": stable.get("release_notes", ""),
        "dev_notes": dev.get("release_notes", ""),
        "is_current": False,
        "update_source": True,
    }


def _telegram_row(language: str) -> tuple[str, dict]:
    started = time.monotonic()
    channel = normalize_update_channel(CHANNEL)
    error = _tr(language, "page.servers.error.version_not_found", "Версия не найдена")
    try:
        info = telegram.get_telegram_version_info(channel)
    except Exception as exc:
        info = None
        error = short_error(exc)
    response_time = time.monotonic() - started
    if info and info.get("version"):
        version = str(info["version"])
        return TELEGRAM_ROW_NAME, {
            "status": "online",
            "response_time": response_time,
            "stable_version": version if channel == CHANNEL_STABLE else "—",
            "dev_version": version if channel == CHANNEL_DEV else "—",
            "stable_notes": "",
            "dev_notes": "",
            "is_current": False,
            "update_source": False,
        }
    return TELEGRAM_ROW_NAME, {
        "status": "error",
        "response_time": response_time,
        "error": error,
        "is_current": False,
        "update_source": False,
    }


def start_telegram_probe(*, language: str, emit_row: EmitRow) -> threading.Thread:
    """Строка Telegram в своём фоновом потоке: результат придёт, когда придёт."""

    def run() -> None:
        name, status = _telegram_row(language)
        emit_row(name, status)

    thread = threading.Thread(target=run, name="update-status-telegram", daemon=True)
    thread.start()
    return thread


def probe_update_sources(
    *,
    language: str,
    emit_row: EmitRow,
    servers: list[dict[str, Any]] | None = None,
    deadline: float = SOURCES_DEADLINE_SECONDS,
) -> bool:
    """Опрашивает Forgejo и все зеркала. True, если хоть один источник ответил.

    Первое по порядку ответившее зеркало помечается текущим.
    """
    selected = servers if servers is not None else mirrors.mirror_servers()
    jobs: list[tuple[str, Callable[[], tuple[str, dict]]]] = [("forgejo", lambda: _forgejo_row(language))]
    for server in selected:
        jobs.append((str(server.get("id") or server.get("host")), lambda server=server: _mirror_row(server)))

    results: queue.Queue[tuple[str, str, dict]] = queue.Queue()

    def runner(job_id: str, job: Callable[[], tuple[str, dict]]) -> None:
        try:
            name, status = job()
        except Exception as exc:
            name, status = job_id, {"status": "error", "error": str(exc)[:80], "update_source": True}
        results.put((job_id, name, status))

    for job_id, job in jobs:
        threading.Thread(target=runner, args=(job_id, job), name=f"update-status-{job_id}", daemon=True).start()

    online_mirrors: dict[str, tuple[str, dict]] = {}
    any_online = False
    finish_at = time.monotonic() + float(deadline)
    for _ in jobs:
        remaining = finish_at - time.monotonic()
        if remaining <= 0:
            break
        try:
            job_id, name, status = results.get(timeout=remaining)
        except queue.Empty:
            break
        emit_row(name, status)
        if status.get("status") == "online":
            any_online = True
            if job_id != "forgejo":
                online_mirrors[job_id] = (name, status)

    for server in selected:
        found = online_mirrors.get(str(server.get("id") or server.get("host")))
        if found is not None:
            name, status = found
            emit_row(name, {**status, "is_current": True})
            break
    return any_online


__all__ = [
    "FORGEJO_ROW_NAME",
    "TELEGRAM_ROW_NAME",
    "probe_update_sources",
    "start_telegram_probe",
]
