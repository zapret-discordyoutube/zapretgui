from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import time as _time
from datetime import datetime
from typing import Callable

from PyQt6.QtCore import QThread, pyqtSignal

from config.build_info import CHANNEL
from config.config import CHANNEL_DEV, CHANNEL_STABLE

from log.log import log

from app.ui_texts import tr as tr_catalog
from updater.channel_utils import normalize_update_channel
from updater.server_config import CONNECT_TIMEOUT, READ_TIMEOUT, should_verify_ssl


@dataclass(frozen=True, slots=True)
class _StatusProbeResult:
    name: str
    status: dict
    server_id: str = ""
    stats_outcome: str = ""
    stats_error: str = ""


class ServerCheckWorker(QThread):
    """Воркер для проверки статуса серверов."""

    server_checked = pyqtSignal(str, dict)
    update_sources_complete = pyqtSignal()

    def __init__(
        self,
        update_pool_stats: bool = False,
        telegram_only: bool = False,
        *,
        language: str = "ru",
    ):
        super().__init__()
        self._update_pool_stats = update_pool_stats
        self._telegram_only = telegram_only
        self._ui_language = language
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def is_stop_requested(self) -> bool:
        return self._stop_requested

    def _tr(self, key: str, default: str) -> str:
        return tr_catalog(key, language=self._ui_language, default=default)

    @staticmethod
    def _request_versions_json(url: str, *, timeout, verify_ssl: bool):
        """Запрашивает all_versions.json без системного прокси."""
        import requests
        from updater.proxy_bypass import request_get_bypass_proxy

        headers = {
            "Accept": "application/json",
            "User-Agent": "Zapret-Updater/3.1",
        }

        if not verify_ssl:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        def _decode_response(resp):
            if resp.status_code != 200:
                return None, f"HTTP {resp.status_code}"
            try:
                return resp.json(), None
            except Exception as e:
                return None, f"json error: {str(e)[:60]}"

        try:
            response = request_get_bypass_proxy(
                url,
                timeout=timeout,
                verify=verify_ssl,
                headers=headers,
            )
            data, error = _decode_response(response)
            return data, error, "direct"
        except requests.exceptions.Timeout:
            return None, "timeout", "direct"
        except requests.exceptions.ConnectionError as e:
            return None, f"connection error: {str(e)[:80]}", "direct"
        except requests.exceptions.RequestException as e:
            return None, str(e)[:80], "direct"
        except Exception as e:
            return None, str(e)[:80], "direct"

    def _probe_telegram_status(self) -> _StatusProbeResult:
        try:
            from updater.telegram_updater import get_telegram_version_info

            start_time = _time.time()
            tg_channel = normalize_update_channel(CHANNEL)
            tg_info = get_telegram_version_info(tg_channel)
            response_time = _time.time() - start_time
            if tg_info and tg_info.get("version"):
                status = {
                    "status": "online",
                    "response_time": response_time,
                    "stable_version": tg_info.get("version") if tg_channel == CHANNEL_STABLE else "—",
                    "dev_version": tg_info.get("version") if tg_channel == CHANNEL_DEV else "—",
                    "stable_notes": tg_info.get("release_notes", "") if tg_channel == CHANNEL_STABLE else "",
                    "dev_notes": tg_info.get("release_notes", "") if tg_channel == CHANNEL_DEV else "",
                    "is_current": False,
                    "update_source": False,
                }
            else:
                status = {
                    "status": "error",
                    "response_time": response_time,
                    "error": self._tr("page.servers.error.version_not_found", "Версия не найдена"),
                    "is_current": False,
                    "update_source": False,
                }
            return _StatusProbeResult("Telegram", status)
        except Exception as exc:
            return _StatusProbeResult(
                "Telegram",
                {
                    "status": "error",
                    "error": str(exc)[:80],
                    "is_current": False,
                    "update_source": False,
                },
            )

    def _probe_update_server(self, server: dict, stats: dict) -> _StatusProbeResult:
        server_id = str(server["id"])
        server_name = str(server["name"])
        blocked_until = stats.get("blocked_until")
        if blocked_until and _time.time() < blocked_until:
            until_dt = datetime.fromtimestamp(blocked_until)
            return _StatusProbeResult(
                server_name,
                {
                    "status": "blocked",
                    "response_time": 0,
                    "error": self._tr(
                        "page.servers.error.blocked_until_template",
                        "Заблокирован до {time}",
                    ).format(time=until_dt.strftime("%H:%M:%S")),
                    "is_current": False,
                    "update_source": True,
                },
                server_id=server_id,
            )

        monitor_timeout = (min(CONNECT_TIMEOUT, 3), min(READ_TIMEOUT, 5))
        response_time = 0.0
        last_error = self._tr("page.servers.error.connect_failed", "Не удалось подключиться")
        protocol_attempts = (
            (
                "HTTPS",
                f"https://{server['host']}:{server['https_port']}/api/all_versions.json",
                should_verify_ssl(),
            ),
            (
                "HTTP",
                f"http://{server['host']}:{server['http_port']}/api/all_versions.json",
                False,
            ),
        )
        for protocol, api_url, verify_ssl in protocol_attempts:
            attempt_start = _time.time()
            data, error, _route = self._request_versions_json(
                api_url,
                timeout=monitor_timeout,
                verify_ssl=verify_ssl,
            )
            response_time = _time.time() - attempt_start
            if data:
                return _StatusProbeResult(
                    server_name,
                    {
                        "status": "online",
                        "response_time": response_time,
                        "stable_version": data.get("stable", {}).get("version", "—"),
                        "dev_version": data.get("dev", {}).get("version", "—"),
                        "stable_notes": data.get("stable", {}).get("release_notes", ""),
                        "dev_notes": data.get("dev", {}).get("release_notes", ""),
                        "is_current": False,
                        "update_source": True,
                    },
                    server_id=server_id,
                    stats_outcome="success",
                )
            if error:
                last_error = f"{protocol}: {error}"

        return _StatusProbeResult(
            server_name,
            {
                "status": "error",
                "response_time": response_time,
                "error": last_error[:80],
                "is_current": False,
                "update_source": True,
            },
            server_id=server_id,
            stats_outcome="failure",
            stats_error=last_error[:80],
        )

    def _probe_forgejo_status(self) -> _StatusProbeResult:
        try:
            from updater.forgejo_release import check_api

            api_info = check_api()
            return _StatusProbeResult(
                "Forgejo API",
                {
                    "status": "online",
                    "response_time": api_info["response_time"],
                    "details": self._tr("page.servers.status.api_available", "API доступен"),
                    "update_source": True,
                },
            )
        except Exception as exc:
            return _StatusProbeResult(
                "Forgejo API",
                {
                    "status": "error",
                    "error": str(exc)[:50],
                    "update_source": True,
                },
            )

    def _emit_skipped_servers(self, servers: list[dict]) -> None:
        for server in servers:
            self.server_checked.emit(
                str(server["name"]),
                {
                    "status": "skipped",
                    "response_time": 0,
                    "error": self._tr("page.servers.status.rate_limited", "Ожидание"),
                    "is_current": False,
                    "update_source": True,
                },
            )

    def _record_server_result(self, pool, result: _StatusProbeResult) -> None:
        if not self._update_pool_stats or not result.server_id:
            return
        if result.stats_outcome == "success":
            pool.record_success(result.server_id, float(result.status.get("response_time") or 0.0))
        elif result.stats_outcome == "failure":
            pool.record_failure(result.server_id, result.stats_error)

    def _mark_first_configured_online_server(
        self,
        servers: list[dict],
        results_by_server_id: dict[str, _StatusProbeResult],
    ) -> None:
        for server in servers:
            result = results_by_server_id.get(str(server["id"]))
            if result is None or result.status.get("status") != "online":
                continue
            active_status = dict(result.status)
            active_status["is_current"] = True
            self.server_checked.emit(result.name, active_status)
            return

    def run(self):
        from updater.server_pool import get_server_pool

        pool = get_server_pool()
        servers = list(pool.servers)
        self._stop_requested = False

        if self._telegram_only:
            self._emit_skipped_servers(servers)
            self.update_sources_complete.emit()
            telegram_result = self._probe_telegram_status()
            if not self.is_stop_requested():
                self.server_checked.emit(telegram_result.name, telegram_result.status)
            return

        jobs: list[tuple[str, str, Callable[[], _StatusProbeResult]]] = [
            ("telegram", "Telegram", self._probe_telegram_status),
            ("forgejo", "Forgejo API", self._probe_forgejo_status),
        ]
        for server in servers:
            stats = dict(pool.stats.get(server["id"], {}))
            jobs.append(
                (
                    "server",
                    str(server["name"]),
                    lambda server=server, stats=stats: self._probe_update_server(server, stats),
                )
            )

        results_by_server_id: dict[str, _StatusProbeResult] = {}
        required_remaining = len(jobs) - 1  # Telegram — только диагностика.
        sources_emitted = False
        executor = ThreadPoolExecutor(max_workers=max(1, len(jobs)), thread_name_prefix="update-status")
        futures = {
            executor.submit(probe): (kind, name)
            for kind, name, probe in jobs
        }
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
                    self._record_server_result(pool, result)
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
    """Воркер для получения версий."""

    version_found = pyqtSignal(str, dict)
    complete = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def is_stop_requested(self) -> bool:
        return self._stop_requested

    def run(self):
        from updater.release_manager import get_latest_release
        self._stop_requested = False

        for channel in (CHANNEL_STABLE, CHANNEL_DEV):
            if self.is_stop_requested():
                break
            try:
                release = get_latest_release(channel, use_cache=False)
                if release:
                    self.version_found.emit(channel, release)
                else:
                    self.version_found.emit(channel, {"error": "Не удалось получить"})
            except Exception as exc:
                self.version_found.emit(channel, {"error": str(exc)})

        self.complete.emit()


__all__ = [
    "ServerCheckWorker",
    "VersionCheckWorker",
]
