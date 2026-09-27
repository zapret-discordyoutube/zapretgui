"""Фоновые проверки для страницы «Серверы».

* ``ServerCheckWorker`` — диагностическая таблица: Forgejo, каждое зеркало и
  Telegram опрашиваются одновременно и свежо. ``update_sources_complete``
  приходит, как только ответили Forgejo и зеркала, не дожидаясь Telegram.
  Строки таблицы никогда не создают предложение обновиться.
* ``VersionCheckWorker`` — новейший выпуск своего канала через
  ``lookup_latest_release``: выпуск или понятная ошибка.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from PyQt6.QtCore import QThread, pyqtSignal

from config.build_info import CHANNEL
from config.config import CHANNEL_DEV, CHANNEL_STABLE

from app.ui_texts import tr as tr_catalog
from updater.channel_utils import normalize_update_channel
from updater.release.http import short_error


FORGEJO_ROW_NAME = "Forgejo API"
TELEGRAM_ROW_NAME = "Telegram"


@dataclass(frozen=True, slots=True)
class _StatusProbeResult:
    name: str
    status: dict
    server_id: str = ""


class ServerCheckWorker(QThread):
    """Воркер для проверки статуса серверов."""

    server_checked = pyqtSignal(str, dict)
    update_sources_complete = pyqtSignal()

    def __init__(self, telegram_only: bool = False, *, language: str = "ru"):
        super().__init__()
        self._telegram_only = telegram_only
        self._ui_language = language
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def is_stop_requested(self) -> bool:
        return self._stop_requested

    def _tr(self, key: str, default: str) -> str:
        return tr_catalog(key, language=self._ui_language, default=default)

    def _probe_telegram_status(self) -> _StatusProbeResult:
        from updater.release.telegram import get_telegram_version_info

        started = time.monotonic()
        channel = normalize_update_channel(CHANNEL)
        try:
            info = get_telegram_version_info(channel)
        except Exception as exc:
            info = None
            error = short_error(exc)
        else:
            error = self._tr("page.servers.error.version_not_found", "Версия не найдена")
        response_time = time.monotonic() - started
        if info and info.get("version"):
            version = str(info["version"])
            status = {
                "status": "online",
                "response_time": response_time,
                "stable_version": version if channel == CHANNEL_STABLE else "—",
                "dev_version": version if channel == CHANNEL_DEV else "—",
                "stable_notes": "",
                "dev_notes": "",
                "is_current": False,
                "update_source": False,
            }
        else:
            status = {
                "status": "error",
                "response_time": response_time,
                "error": error,
                "is_current": False,
                "update_source": False,
            }
        return _StatusProbeResult(TELEGRAM_ROW_NAME, status)

    def _probe_update_server(self, server: dict) -> _StatusProbeResult:
        from updater.release.mirrors import fetch_versions

        server_id = str(server.get("id") or server.get("host"))
        server_name = str(server.get("name") or server.get("host"))
        started = time.monotonic()
        try:
            data, _protocol, _base_url, _verify, response_time = fetch_versions(server)
        except Exception as exc:
            return _StatusProbeResult(
                server_name,
                {
                    "status": "error",
                    "response_time": time.monotonic() - started,
                    "error": str(exc)[:80],
                    "is_current": False,
                    "update_source": True,
                },
                server_id=server_id,
            )
        stable = data.get("stable") if isinstance(data.get("stable"), dict) else {}
        dev = data.get("dev") if isinstance(data.get("dev"), dict) else {}
        return _StatusProbeResult(
            server_name,
            {
                "status": "online",
                "response_time": response_time,
                "stable_version": stable.get("version", "—"),
                "dev_version": dev.get("version", "—"),
                "stable_notes": stable.get("release_notes", ""),
                "dev_notes": dev.get("release_notes", ""),
                "is_current": False,
                "update_source": True,
            },
            server_id=server_id,
        )

    def _probe_forgejo_status(self) -> _StatusProbeResult:
        from updater.release.forgejo import probe_forgejo

        try:
            response_time = probe_forgejo()
        except Exception as exc:
            return _StatusProbeResult(
                FORGEJO_ROW_NAME,
                {"status": "error", "error": short_error(exc)[:50], "update_source": True},
            )
        return _StatusProbeResult(
            FORGEJO_ROW_NAME,
            {
                "status": "online",
                "response_time": response_time,
                "details": self._tr("page.servers.status.api_available", "API доступен"),
                "update_source": True,
            },
        )

    def _emit_skipped_servers(self, servers: list[dict]) -> None:
        for server in servers:
            self.server_checked.emit(
                str(server.get("name") or server.get("host")),
                {
                    "status": "skipped",
                    "response_time": 0,
                    "error": self._tr("page.servers.status.rate_limited", "Ожидание"),
                    "is_current": False,
                    "update_source": True,
                },
            )

    def _mark_first_configured_online_server(
        self,
        servers: list[dict],
        results_by_server_id: dict[str, _StatusProbeResult],
    ) -> None:
        for server in servers:
            result = results_by_server_id.get(str(server.get("id") or server.get("host")))
            if result is None or result.status.get("status") != "online":
                continue
            active_status = dict(result.status)
            active_status["is_current"] = True
            self.server_checked.emit(result.name, active_status)
            return

    def run(self):
        from updater.release.mirrors import mirror_servers

        servers = mirror_servers()
        self._stop_requested = False

        if self._telegram_only:
            self._emit_skipped_servers(servers)
            self.update_sources_complete.emit()
            telegram_result = self._probe_telegram_status()
            if not self.is_stop_requested():
                self.server_checked.emit(telegram_result.name, telegram_result.status)
            return

        jobs: list[tuple[str, str, Callable[[], _StatusProbeResult]]] = [
            ("telegram", TELEGRAM_ROW_NAME, self._probe_telegram_status),
            ("forgejo", FORGEJO_ROW_NAME, self._probe_forgejo_status),
        ]
        for server in servers:
            jobs.append(
                (
                    "server",
                    str(server.get("name") or server.get("host")),
                    lambda server=server: self._probe_update_server(server),
                )
            )

        results_by_server_id: dict[str, _StatusProbeResult] = {}
        required_remaining = len(jobs) - 1  # Telegram — только диагностика.
        sources_emitted = False
        executor = ThreadPoolExecutor(max_workers=max(1, len(jobs)), thread_name_prefix="update-status")
        futures = {executor.submit(probe): (kind, name) for kind, name, probe in jobs}
        try:
            for future in as_completed(futures):
                kind, name = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = _StatusProbeResult(
                        name,
                        {
                            "status": "error",
                            "error": str(exc)[:80],
                            "is_current": False,
                            "update_source": kind != "telegram",
                        },
                    )

                if self.is_stop_requested():
                    continue

                if result.server_id:
                    results_by_server_id[result.server_id] = result
                self.server_checked.emit(result.name, result.status)

                if kind != "telegram":
                    required_remaining -= 1
                    if required_remaining == 0 and not sources_emitted:
                        self._mark_first_configured_online_server(servers, results_by_server_id)
                        sources_emitted = True
                        self.update_sources_complete.emit()
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        if self.is_stop_requested():
            return
        if not sources_emitted:
            self._mark_first_configured_online_server(servers, results_by_server_id)
            self.update_sources_complete.emit()


class VersionCheckWorker(QThread):
    """Новейший выпуск канала программы: выпуск или понятная ошибка."""

    version_found = pyqtSignal(str, dict)
    complete = pyqtSignal()

    def __init__(self, channel: str | None = None):
        super().__init__()
        self._channel = normalize_update_channel(channel if channel is not None else CHANNEL)
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def is_stop_requested(self) -> bool:
        return self._stop_requested

    def run(self):
        from updater.release.resolver import lookup_latest_release

        self._stop_requested = False
        try:
            lookup = lookup_latest_release(self._channel)
            payload = dict(lookup.release) if lookup.ok else {"error": lookup.error}
        except Exception as exc:
            payload = {"error": str(exc) or type(exc).__name__}

        # Остановленная проверка молчит: её результат устарел и не должен
        # перебить ответ новой проверки.
        if self.is_stop_requested():
            return
        self.version_found.emit(self._channel, payload)
        self.complete.emit()


__all__ = [
    "ServerCheckWorker",
    "VersionCheckWorker",
]
