from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log


class DnsPageLoadWorker(QThread):
    loaded = pyqtSignal(int, object)
    finished_loading = pyqtSignal()

    def __init__(self, request_id: int, load_page_data_fn, parent=None):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._load_page_data_fn = load_page_data_fn

    def run(self) -> None:
        try:
            state = self._load_page_data_fn()
            self.loaded.emit(self._request_id, state)
        except Exception as exc:
            log(f"DnsPageLoadWorker: ошибка загрузки DNS страницы: {exc}", "ERROR")
        self.finished_loading.emit()


class DnsLatencyWorker(QThread):
    """Замер скорости DNS-серверов в фоне: completed(request_id, DnsLatencyReport)."""

    completed = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(self, request_id: int, *, servers, measure_dns_latency: Callable[[list], Any], parent=None):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._servers = [str(item) for item in (servers or ()) if str(item or "").strip()]
        self._measure_dns_latency = measure_dns_latency

    def run(self) -> None:
        try:
            report = self._measure_dns_latency(self._servers)
        except Exception as exc:
            log(f"DnsLatencyWorker: ошибка замера скорости DNS: {exc}", "ERROR")
            self.failed.emit(self._request_id, str(exc))
            return
        self.completed.emit(self._request_id, report)


class DnsFlushCacheWorker(QThread):
    completed = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(
        self,
        request_id: int,
        *,
        language: str = "ru",
        flush_dns_cache: Callable[[], Any],
        parent=None,
    ):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._language = str(language or "ru")
        self._flush_dns_cache = flush_dns_cache

    def run(self) -> None:
        from dns import page_plans as dns_page_plans

        try:
            result = self._flush_dns_cache()
            plan = dns_page_plans.build_flush_dns_cache_result_plan(
                success=bool(result.success),
                message=str(result.message or ""),
                language=self._language,
            )
        except Exception as exc:
            log(f"DnsFlushCacheWorker: ошибка сброса DNS кэша: {exc}", "ERROR")
            self.failed.emit(self._request_id, str(exc))
            return
        self.completed.emit(self._request_id, plan)


class DnsIspWarningWorker(QThread):
    completed = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(
        self,
        request_id: int,
        *,
        adapters,
        dns_info: dict,
        language: str = "ru",
        is_isp_dns_warning_shown: Callable[[], bool],
        mark_isp_dns_warning_shown: Callable[[], Any],
        normalize_adapter_alias: Callable[[str], str],
        parent=None,
    ):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._adapters = list(adapters or [])
        self._dns_info = dict(dns_info or {})
        self._language = str(language or "ru")
        self._is_isp_dns_warning_shown = is_isp_dns_warning_shown
        self._mark_isp_dns_warning_shown = mark_isp_dns_warning_shown
        self._normalize_adapter_alias = normalize_adapter_alias

    def run(self) -> None:
        from dns import page_plans as dns_page_plans

        try:
            plan = dns_page_plans.build_isp_dns_warning_plan(
                self._adapters,
                self._dns_info,
                warning_already_shown=self._is_isp_dns_warning_shown(),
                normalize_alias_fn=self._normalize_adapter_alias,
                language=self._language,
            )
            if plan.should_show:
                self._mark_isp_dns_warning_shown()
        except Exception as exc:
            log(f"DnsIspWarningWorker: ошибка подготовки предупреждения ISP DNS: {exc}", "ERROR")
            self.failed.emit(self._request_id, str(exc))
            return
        self.completed.emit(self._request_id, plan)


class DnsApplyWorker(QThread):
    completed = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(
        self,
        request_id: int,
        *,
        action: str,
        adapters,
        name: str = "",
        data=None,
        ipv6_available: bool = False,
        apply_auto_dns: Callable[[list], Any],
        apply_provider_dns: Callable[..., Any],
        refresh_dns_info: Callable[[list], Any],
        parent=None,
    ):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._action = str(action or "").strip()
        self._adapters = list(adapters or [])
        self._name = str(name or "")
        self._data = dict(data or {})
        self._ipv6_available = bool(ipv6_available)
        self._apply_auto_dns = apply_auto_dns
        self._apply_provider_dns = apply_provider_dns
        self._refresh_dns_info = refresh_dns_info

    def run(self) -> None:
        try:
            result = self._run_apply()
        except Exception as exc:
            log(f"DnsApplyWorker: действие {self._action} не выполнено: {exc}", "ERROR")
            self.failed.emit(self._request_id, str(exc))
            return
        self.completed.emit(self._request_id, result)

    def _run_apply(self) -> dict[str, object]:
        from dns import page_plans as dns_page_plans

        if not self._adapters:
            return {"plan": None, "dns_info": None}

        if self._action == "auto":
            command_result = self._apply_auto_dns(self._adapters)
            plan = dns_page_plans.build_auto_dns_apply_result_plan(
                adapter_count=len(self._adapters),
                success_count=int(command_result.affected_count or 0),
            )
        elif self._action == "provider":
            provider_plan = dns_page_plans.build_provider_dns_plan(
                name=self._name,
                data=self._data,
                ipv6_available=self._ipv6_available,
            )
            if not provider_plan.valid:
                return {
                    "plan": provider_plan,
                    "dns_info": None,
                }
            command_result = self._apply_provider_dns(
                self._adapters,
                provider_plan.ipv4,
                provider_plan.ipv6,
                ipv6_available=self._ipv6_available,
            )
            plan = dns_page_plans.build_provider_dns_apply_result_plan(
                name=self._name,
                adapter_count=len(self._adapters),
                success_count=int(command_result.affected_count or 0),
                ipv6_available=self._ipv6_available,
                ipv6=provider_plan.ipv6,
            )
        else:
            raise ValueError(f"Неизвестное DNS действие: {self._action}")

        dns_info = self._refresh_dns_info(self._adapters) if getattr(plan, "should_refresh", False) else None
        return {
            "plan": plan,
            "dns_info": dns_info,
            "adapters": self._adapters,
        }
