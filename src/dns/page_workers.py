"""Фоновые воркеры страницы DNS.

Каждый воркер получает готовое действие DNS-слоя (callable из фасада),
выполняет его вне UI-потока и отдаёт результат сигналом с request_id.
Виджеты воркеры не трогают.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log


class DnsPageLoadWorker(QThread):
    """Загрузка состояния страницы: loaded(request_id, DnsState)."""

    loaded = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(self, request_id: int, load_state: Callable[[], Any], parent=None):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._load_state = load_state

    def run(self) -> None:
        try:
            state = self._load_state()
        except Exception as exc:
            log(f"DnsPageLoadWorker: не удалось загрузить адаптеры: {exc}", "ERROR")
            self.failed.emit(self._request_id, str(exc))
            return
        self.loaded.emit(self._request_id, state)


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
    """Решает, показать ли совет про DNS провайдера (один раз за установку)."""

    completed = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(
        self,
        request_id: int,
        *,
        adapters,
        language: str = "ru",
        is_isp_dns_warning_shown: Callable[[], bool],
        mark_isp_dns_warning_shown: Callable[[], Any],
        parent=None,
    ):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._adapters = tuple(adapters or ())
        self._language = str(language or "ru")
        self._is_isp_dns_warning_shown = is_isp_dns_warning_shown
        self._mark_isp_dns_warning_shown = mark_isp_dns_warning_shown

    def run(self) -> None:
        from dns import page_plans as dns_page_plans

        try:
            plan = dns_page_plans.build_isp_dns_warning_plan(
                self._adapters,
                warning_already_shown=self._is_isp_dns_warning_shown(),
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
    """Применяет DNS и перечитывает адаптеры: completed(request_id, {"plan", "state"})."""

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
        apply_dns: Callable[..., Any],
        reset_to_auto: Callable[[list], Any],
        load_state: Callable[[], Any],
        parent=None,
    ):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._action = str(action or "").strip()
        self._adapters = [str(item) for item in (adapters or ()) if str(item or "").strip()]
        self._name = str(name or "")
        self._data = dict(data or {})
        self._ipv6_available = bool(ipv6_available)
        self._apply_dns = apply_dns
        self._reset_to_auto = reset_to_auto
        self._load_state = load_state

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
            return {"plan": None, "state": None}

        if self._action == "auto":
            command_result = self._reset_to_auto(self._adapters)
            plan = dns_page_plans.build_auto_dns_apply_result_plan(
                adapter_count=len(self._adapters),
                success_count=int(command_result.affected_count or 0),
                error=str(command_result.message or ""),
            )
        elif self._action == "provider":
            provider_plan = dns_page_plans.build_provider_dns_plan(
                name=self._name,
                data=self._data,
                ipv6_available=self._ipv6_available,
            )
            if not provider_plan.valid:
                return {"plan": provider_plan, "state": None}
            command_result = self._apply_dns(self._adapters, provider_plan.ipv4, provider_plan.ipv6)
            plan = dns_page_plans.build_provider_dns_apply_result_plan(
                name=self._name,
                adapter_count=len(self._adapters),
                success_count=int(command_result.affected_count or 0),
                ipv6=provider_plan.ipv6,
                error=str(command_result.message or ""),
            )
        else:
            raise ValueError(f"Неизвестное DNS действие: {self._action}")

        return {"plan": plan, "state": self._load_state()}
