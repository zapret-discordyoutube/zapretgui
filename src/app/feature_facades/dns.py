from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class DnsFeature:
    migrate_outdated_dns_addresses: Callable
    warm_page_data_cache: Callable
    consume_warmed_page_data: Callable
    create_dns_check_worker: Callable
    create_dns_check_save_worker: Callable
    create_page_load_worker: Callable
    create_dns_latency_worker: Callable
    create_dns_flush_cache_worker: Callable
    create_isp_dns_warning_worker: Callable
    create_dns_apply_worker: Callable


def build_dns_feature() -> DnsFeature:
    def _commands():
        from dns import commands as dns_commands

        return dns_commands

    def _public():
        from dns import public as dns_public

        return dns_public

    load_state = lambda *args, **kwargs: _public().load_state(*args, **kwargs)
    apply_dns = lambda *args, **kwargs: _public().apply_dns(*args, **kwargs)
    reset_to_auto = lambda *args, **kwargs: _public().reset_to_auto(*args, **kwargs)
    is_isp_dns_warning_shown = lambda *args, **kwargs: _public().is_isp_dns_warning_shown(*args, **kwargs)
    mark_isp_dns_warning_shown = lambda *args, **kwargs: _public().mark_isp_dns_warning_shown(*args, **kwargs)
    flush_dns_cache = lambda *args, **kwargs: _public().flush_dns_cache(*args, **kwargs)
    measure_dns_latency = lambda *args, **kwargs: _public().measure_dns_latency(*args, **kwargs)
    run_dns_poisoning_check = lambda *args, **kwargs: _commands().run_dns_poisoning_check(*args, **kwargs)
    save_dns_check_results = lambda *args, **kwargs: _commands().save_dns_check_results(*args, **kwargs)

    def _create_dns_flush_cache_worker(request_id: int, *, language: str = "ru", parent=None):
        from dns.page_workers import DnsFlushCacheWorker

        return DnsFlushCacheWorker(
            request_id,
            language=language,
            flush_dns_cache=flush_dns_cache,
            parent=parent,
        )

    def _create_isp_dns_warning_worker(request_id: int, *, adapters, language: str = "ru", parent=None):
        from dns.page_workers import DnsIspWarningWorker

        return DnsIspWarningWorker(
            request_id,
            adapters=adapters,
            language=language,
            is_isp_dns_warning_shown=is_isp_dns_warning_shown,
            mark_isp_dns_warning_shown=mark_isp_dns_warning_shown,
            parent=parent,
        )

    def _create_dns_apply_worker(
        request_id: int,
        *,
        action: str,
        adapters,
        name: str = "",
        data=None,
        ipv6_available: bool = False,
        parent=None,
    ):
        from dns.page_workers import DnsApplyWorker

        return DnsApplyWorker(
            request_id,
            action=action,
            adapters=adapters,
            name=name,
            data=data,
            ipv6_available=ipv6_available,
            apply_dns=apply_dns,
            reset_to_auto=reset_to_auto,
            load_state=load_state,
            parent=parent,
        )

    def _create_page_load_worker(request_id: int, *, parent=None):
        from dns.page_workers import DnsPageLoadWorker

        return DnsPageLoadWorker(request_id, load_state, parent)

    def _create_dns_latency_worker(request_id: int, *, servers, parent=None):
        from dns.page_workers import DnsLatencyWorker

        return DnsLatencyWorker(
            request_id,
            servers=servers,
            measure_dns_latency=measure_dns_latency,
            parent=parent,
        )

    def _create_dns_check_worker(request_id: int):
        from dns.dns_check_worker import DNSCheckWorker

        return DNSCheckWorker(
            request_id,
            run_dns_poisoning_check=run_dns_poisoning_check,
        )

    def _create_dns_check_save_worker(request_id: int, *, file_path: str, plain_text: str, parent=None):
        from dns.dns_check_worker import DNSCheckSaveWorker

        return DNSCheckSaveWorker(
            request_id,
            file_path=file_path,
            plain_text=plain_text,
            save_dns_check_results=save_dns_check_results,
            parent=parent,
        )

    return DnsFeature(
        migrate_outdated_dns_addresses=lambda *args, **kwargs: _public().migrate_outdated_dns_addresses(*args, **kwargs),
        warm_page_data_cache=lambda *args, **kwargs: _public().warm_state(*args, **kwargs),
        consume_warmed_page_data=lambda *args, **kwargs: _public().consume_warmed_state(*args, **kwargs),
        create_dns_check_worker=_create_dns_check_worker,
        create_dns_check_save_worker=_create_dns_check_save_worker,
        create_page_load_worker=_create_page_load_worker,
        create_dns_latency_worker=_create_dns_latency_worker,
        create_dns_flush_cache_worker=_create_dns_flush_cache_worker,
        create_isp_dns_warning_worker=_create_isp_dns_warning_worker,
        create_dns_apply_worker=_create_dns_apply_worker,
    )
