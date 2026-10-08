from __future__ import annotations

from dataclasses import replace

from dns.state import CustomServerResult, DnsCommandResult, DnsState


def migrate_outdated_dns_addresses():
    from dns.address_migration import migrate_outdated_dns_addresses as _migrate

    return _migrate()


def is_isp_dns_warning_shown() -> bool:
    from settings.store import get_isp_dns_info_shown

    return bool(get_isp_dns_info_shown())


def mark_isp_dns_warning_shown() -> bool:
    from settings.store import set_isp_dns_info_shown

    return bool(set_isp_dns_info_shown(True))


def create_dns_check_worker():
    from dns.dns_check_worker import DNSCheckWorker

    return DNSCheckWorker(run_dns_poisoning_check=run_dns_poisoning_check)


def create_dns_check_save_worker(request_id: int, *, file_path: str, plain_text: str, parent=None):
    from dns.dns_check_worker import DNSCheckSaveWorker

    return DNSCheckSaveWorker(
        request_id,
        file_path=file_path,
        plain_text=plain_text,
        save_dns_check_results=save_dns_check_results,
        parent=parent,
    )


def run_dns_poisoning_check(*, log_callback=None, should_stop=None) -> dict:
    from diagnostics.engine import run_dns_check

    # Строки отчёта копятся, чтобы лечь в тот же файл: у прошлой проверки по ним работает кнопка «Отчёт».
    lines: list[str] = []

    def emit(line: str) -> None:
        lines.append(str(line))
        if log_callback is not None:
            log_callback(line)

    results = run_dns_check(emit=emit, should_stop=should_stop)
    results = dict(results or {})
    from diagnostics.history import dns_check_entry

    entry = dns_check_entry(results)
    if entry is not None:
        # Итог целиком — в файл: прошлая проверка открывается теми же карточками.
        document = {"format": DNS_CHECK_FORMAT, "report": results, "text": lines}
        entry["log_file"] = _save_past_check("dns_check", document)
        # Экран показывает ту самую запись, что сохранена в настройки.
        results["history_entry"] = entry
    _remember_check("dns_history", entry)
    return results


def save_dns_check_results(*, file_path: str, plain_text: str):
    import os
    from datetime import datetime

    from dns.dns_check_plans import DNSSaveResultPlan

    target_path = str(file_path or "").strip()
    if not target_path:
        return DNSSaveResultPlan(
            success=False,
            title="Ошибка",
            content="Не указан путь для сохранения файла.",
        )

    try:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write("DNS CHECK RESULTS\n")
            f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 60 + "\n\n")
            f.write(str(plain_text or ""))

        folder = os.path.dirname(target_path) or None
        if folder and hasattr(os, "startfile"):
            try:
                os.startfile(folder)  # type: ignore[attr-defined]
            except Exception:
                folder = None

        return DNSSaveResultPlan(
            success=True,
            title="Сохранено",
            content=f"Результаты сохранены в:\n{target_path}",
        )
    except Exception as e:
        return DNSSaveResultPlan(
            success=False,
            title="Ошибка",
            content=f"Не удалось сохранить файл:\n{str(e)}",
        )


def load_state() -> DnsState:
    from dns.runtime import load_state as _load_state

    return _load_state()


def warm_state() -> DnsState:
    from dns.runtime import warm_state as _warm_state

    return _warm_state()


def consume_warmed_state() -> DnsState | None:
    from dns.runtime import consume_warmed_state as _consume_warmed_state

    return _consume_warmed_state()


def apply_dns(adapters: list[str], ipv4: list[str], ipv6: list[str]) -> DnsCommandResult:
    from dns.runtime import apply_dns as _apply_dns

    return _apply_dns(adapters, ipv4, ipv6)


def start_local_proxy(mode: str) -> DnsCommandResult:
    from dns.runtime import start_local_proxy as _start_local_proxy

    return _start_local_proxy(mode)


def stop_local_proxy_if_unused() -> bool:
    from dns.runtime import stop_local_proxy_if_unused as _stop_local_proxy_if_unused

    return _stop_local_proxy_if_unused()


def repair_local_proxy() -> str:
    from dns.runtime import repair_local_proxy as _repair_local_proxy

    return _repair_local_proxy()


def reset_to_auto(adapters: list[str]) -> DnsCommandResult:
    from dns.runtime import reset_to_auto as _reset_to_auto

    return _reset_to_auto(adapters)


def flush_dns_cache() -> DnsCommandResult:
    from dns.runtime import flush_dns_cache as _flush_dns_cache

    return _flush_dns_cache()


def measure_dns_latency(servers: list[str]):
    from dns.latency import measure_dns_latency as _measure_dns_latency

    return _measure_dns_latency(servers)


# ── свои DNS-серверы ──────────────────────────────────────────────────────


def load_custom_servers() -> list[dict]:
    from dns.runtime import load_custom_servers as _load_custom_servers

    return _load_custom_servers()


def _reserved_names() -> list[str]:
    """Названия серверов программы: свой сервер не может называться так же."""
    from dns.dns_providers import iter_providers

    return [name for _group, name, _data in iter_providers()]


def _change_custom_servers(change) -> CustomServerResult:
    """Меняет список своих DNS одной транзакцией; change(список) → (новый список, ошибка)."""
    from settings.store import update_custom_dns_servers

    class Rejected(Exception):
        pass

    def mutate(current: list[dict]) -> list[dict]:
        servers, error = change(current)
        if error:
            raise Rejected(error)
        return servers

    try:
        return CustomServerResult(success=True, servers=tuple(update_custom_dns_servers(mutate)))
    except Rejected as exc:
        return CustomServerResult(success=False, servers=tuple(load_custom_servers()), error=str(exc))


def save_custom_server(server: dict, *, cancel=None) -> CustomServerResult:
    """Добавляет или заменяет (по id) свой DNS-сервер.

    Если задан только адрес DoH, сначала находятся и проверяются IP-адреса
    сервера (dns.doh_lookup): без них Windows сервер не примет.
    """
    from dns import custom_servers as custom

    record = custom.copy_server(server)
    notice = ""
    if record["doh"] and not record["ipv4"] and not record["ipv6"]:
        from dns import winapi
        from dns.doh_lookup import find_doh_addresses

        template = custom.parse_doh_template(record["doh"])
        if template is None:
            return CustomServerResult(success=False, error="Адрес DoH записан неверно.", field=custom.FIELD_DOH)
        try:
            ipv6 = bool(winapi.internet_route().has_ipv6)
        except Exception:
            ipv6 = False
        found = find_doh_addresses(template, ipv6=ipv6, doh_supported=winapi.is_doh_supported(), cancel=cancel)
        if not found.found:
            return CustomServerResult(success=False, error=found.error, field=custom.FIELD_DOH)
        record["ipv4"], record["ipv6"], notice = list(found.ipv4), list(found.ipv6), found.notice

    result = _change_custom_servers(
        lambda current: custom.upsert_server(current, record, reserved_names=_reserved_names())
    )
    if not result.success:
        return CustomServerResult(success=False, servers=result.servers, error=result.error, field=custom.FIELD_NAME)
    saved = next((item for item in result.servers if str(item.get("id") or "") == record["id"]), record)
    return CustomServerResult(success=True, servers=result.servers, server=dict(saved), notice=notice)


def delete_custom_server(server_id: str) -> CustomServerResult:
    from dns import custom_servers as custom

    return _change_custom_servers(lambda current: (custom.remove_server(current, server_id), ""))


def duplicate_custom_server(server_id: str) -> CustomServerResult:
    from dns import custom_servers as custom

    return _change_custom_servers(
        lambda current: (custom.duplicate_server(current, server_id, reserved_names=_reserved_names()), "")
    )


def build_domain_lookup_servers():
    """Серверы для вкладки «Проверка домена»: системные, шифрованные, из списка программы и свои."""
    from dns.custom_servers import build_dns_providers_with_custom
    from dns.dns_providers import is_encrypted_only, network_providers
    from dns.domain_lookup import (
        EXTRA_SERVERS,
        SERVER_CUSTOM,
        SERVER_DOH,
        SERVER_PROVIDER,
        SERVER_SYSTEM,
        DnsServer,
    )
    from dns.custom_servers import CUSTOM_DNS_CATEGORY
    from utils.dns_reference import REFERENCE_RESOLVERS

    servers: list = []
    try:
        from utils.windows_dns_query import system_dns_servers

        servers.extend(DnsServer(label=address, address=address, kind=SERVER_SYSTEM) for address in system_dns_servers())
    except Exception:
        pass
    servers.extend(
        DnsServer(label=f"{resolver.label} (шифрованный)", address=resolver.address, kind=SERVER_DOH)
        for resolver in REFERENCE_RESOLVERS
    )

    custom_servers = load_custom_servers()
    # Режимы встроенного шифрованного DNS — не серверы в сети: у них один адрес 127.0.0.1.
    providers = build_dns_providers_with_custom(network_providers(), custom_servers)
    for category, group in providers.items():
        kind = SERVER_CUSTOM if category == CUSTOM_DNS_CATEGORY else SERVER_PROVIDER
        for name, data in group.items():
            if is_encrypted_only(data):
                # Вкладка спрашивает серверы списка обычным DNS, а такой сервер его не принимает.
                continue
            addresses = list(data.get("ipv4") or ()) or list(data.get("ipv6") or ())
            if addresses:
                servers.append(DnsServer(label=str(name), address=str(addresses[0]), kind=kind))
    servers.extend(DnsServer(label=label, address=address, kind=SERVER_PROVIDER) for label, address in EXTRA_SERVERS)
    return tuple(servers)


def run_domain_lookup(target: str, *, use_external: bool = True, on_stage=None, should_stop=None):
    from dns.domain_lookup import run_domain_lookup as _run_domain_lookup

    report = _run_domain_lookup(
        target,
        servers=build_domain_lookup_servers(),
        use_external=use_external,
        on_stage=on_stage,
        should_stop=should_stop,
    )
    from dns.domain_lookup_plans import build_history_entry, build_text_report

    entry = build_history_entry(report)
    if entry is not None:
        # Проверка целиком — в файл: запись истории на вкладке открывает её теми же карточками.
        entry["log_file"] = save_domain_lookup_text(report.target, build_text_report(report), report)
    _remember_check("domain_history", entry)
    # Экран показывает именно сохранённую запись: соберёт свою — в ней не будет пути к полному тексту.
    return replace(report, history_entry=entry)


DOMAIN_LOOKUP_FORMAT = "zapretgui.domain_lookup/1"
DNS_CHECK_FORMAT = "zapretgui.dns_check/1"
SERVER_CHECK_FORMAT = "zapretgui.server_check/1"
# Столько файлов прошлых проверок каждой вкладки хранится; история вкладки короче.
PAST_CHECK_FILES_KEPT = 40


def _save_past_check(prefix: str, document: dict) -> str:
    """Кладёт отчёт проверки вкладки в папку журналов. Возвращает путь; пусто — записать не удалось.

    У каждой вкладки свои файлы (``prefix``); старые сверх ``PAST_CHECK_FILES_KEPT`` удаляются.
    """
    import json
    import os
    from datetime import datetime

    try:
        from config.runtime_layout import APPLICATION_PATHS

        folder = str(APPLICATION_PATHS.logs_dir)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=1)
        old = sorted(name for name in os.listdir(folder) if name.startswith(f"{prefix}_") and name.endswith(".json"))
        for name in old[:-PAST_CHECK_FILES_KEPT]:
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
        return path
    except Exception:
        return ""


def _read_past_check(log_file: str | None, expected_format: str) -> dict:
    import json

    if not log_file:
        return {}
    try:
        with open(str(log_file), encoding="utf-8") as stream:
            document = json.load(stream)
    except (OSError, ValueError):
        return {}
    if not isinstance(document, dict) or document.get("format") != expected_format:
        return {}
    return document


def save_domain_lookup_text(target: str, text: str, report=None) -> str:
    """Кладёт проверку домена в папку журналов. Возвращает путь; пусто — записать не удалось.

    В файле и сам отчёт (по нему прошлая проверка рисуется теми же карточками), и его текст.
    """
    document = {"format": DOMAIN_LOOKUP_FORMAT, "target": str(target), "text": str(text).split("\n")}
    if report is not None:
        from utils.dataclass_json import to_plain

        document["report"] = to_plain(report)
    return _save_past_check("domain_lookup", document)


def load_past_domain_lookup(log_file: str | None) -> str:
    """Текст прошлой проверки домена. Пусто — файла нет или он не читается."""
    lines = _read_past_check(log_file, DOMAIN_LOOKUP_FORMAT).get("text")
    return "\n".join(str(line) for line in lines) if isinstance(lines, list) else ""


def load_past_domain_lookup_report(log_file: str | None):
    """Отчёт прошлой проверки домена — тем же объектом, из которого рисуются карточки.

    None — файла нет, он не читается или сохранён до того, как отчёт стали класть в файл.
    """
    from dns.domain_lookup import DomainLookupReport
    from utils.dataclass_json import from_plain

    return from_plain(DomainLookupReport, _read_past_check(log_file, DOMAIN_LOOKUP_FORMAT).get("report"))


def load_past_dns_check_report(log_file: str | None) -> dict | None:
    """Итог прошлой проверки DNS подмены — тем же словарём, что уходил на экран вкладки. None — файла нет."""
    report = _read_past_check(log_file, DNS_CHECK_FORMAT).get("report")
    return report if isinstance(report, dict) else None


def load_past_dns_check_text(log_file: str | None) -> str:
    """Текст прошлой проверки DNS подмены. Пусто — файла нет или текст в него не клали."""
    lines = _read_past_check(log_file, DNS_CHECK_FORMAT).get("text")
    return "\n".join(str(line) for line in lines) if isinstance(lines, list) else ""


def load_past_server_check_report(log_file: str | None):
    """Отчёт прошлой проверки DNS-серверов — тем же объектом, из которого строятся карточки. None — файла нет."""
    from dns.server_check import ServerCheckReport
    from utils.dataclass_json import from_plain

    return from_plain(ServerCheckReport, _read_past_check(log_file, SERVER_CHECK_FORMAT).get("report"))


def _remember_check(key: str, entry: dict | None) -> None:
    """Дописывает итог проверки в историю вкладки. Сбой записи проверку не ломает."""
    if entry is None:
        return
    try:
        from settings.store import add_tab_history_run

        add_tab_history_run(key, entry)
    except Exception:
        pass


def build_server_check_targets():
    """Адреса для вкладки «DNS-серверы»: все серверы программы и свои, IPv6 — если он есть."""
    from dns.custom_servers import build_dns_providers_with_custom
    from dns.dns_providers import network_providers
    from dns.server_check import build_targets

    custom_servers = load_custom_servers()
    try:
        from dns.winapi import internet_route

        ipv6 = bool(internet_route().has_ipv6)
    except Exception:
        ipv6 = False
    return build_targets(build_dns_providers_with_custom(network_providers(), custom_servers), ipv6=ipv6)


def run_server_check(*, on_progress=None, should_stop=None):
    from dns.server_check import run_server_check as _run_server_check

    report = _run_server_check(
        build_server_check_targets(),
        on_progress=on_progress,
        should_stop=should_stop,
        bypass=_running_bypass(),
    )
    from diagnostics.history import server_check_entry
    from utils.dataclass_json import to_plain

    entry = server_check_entry(report)
    if entry is not None:
        entry["log_file"] = _save_past_check("server_check", {"format": SERVER_CHECK_FORMAT, "report": to_plain(report)})
    _remember_check("servers_history", entry)
    return replace(report, history_entry=entry)


def _running_bypass() -> tuple[str, ...]:
    """Zapret и другие программы обхода, запущенные сейчас: оговорка к результатам проверки."""
    from utils.bypass_tools import bypass_tools_among

    try:
        from settings.mode import ALL_WINWS_EXE_NAME_SET
        from utils.windows_process_probe import iter_process_records_winapi

        names = [str(name or "").lower() for _pid, name in iter_process_records_winapi()]
    except Exception:
        return ()
    zapret = ("Zapret",) if any(name in ALL_WINWS_EXE_NAME_SET for name in names) else ()
    return zapret + bypass_tools_among(names)
