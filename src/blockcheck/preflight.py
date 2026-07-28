"""Preflight — быстрая предварительная проверка одного домена.

4 проверки на домен (параллельно):
1. DNS резолвинг + сравнение IP с заглушками провайдеров
2. TCP :443 — открыт ли порт HTTPS
3. ICMP ping — базовая достижимость (справочно, на вердикт не влияет)
4. HTTP GET :80 — детекция ISP-инъекции / страницы-заглушки

Массовый прогон по списку доменов отсюда убран: в BlockCheck те же проверки
выполняет планировщик проб (``runner``), и отдельная фаза означала бы двойной
резолв и двойной коннект к каждому хосту. Модуль остался точкой входа для
``strategy_scanner``, который проверяет ровно один домен, и владельцем правила
``compute_verdict``.
"""

from __future__ import annotations

import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import TYPE_CHECKING

from utils.net_resolve import DNSTimeoutError, resolve_addrinfo, resolve_ipv4

from blockcheck.config import (
    KNOWN_BLOCK_IPS,
    PREFLIGHT_DNS_TIMEOUT,
    PREFLIGHT_HTTP_TIMEOUT,
    PREFLIGHT_PING_COUNT,
    PREFLIGHT_PING_TIMEOUT,
    PREFLIGHT_TCP_TIMEOUT,
)
from blockcheck.isp_page_detector import check_http_injection
from blockcheck.models import (
    PreflightResult,
    PreflightVerdict,
    SingleTestResult,
    TestStatus,
    TestType,
)
from blockcheck.ping_tester import ping_host

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Verdict labels (Russian)
# ---------------------------------------------------------------------------

_VERDICT_RU = {
    PreflightVerdict.PASSED: "ПРОЙДЕН",
    PreflightVerdict.WARNING: "ПРЕДУПРЕЖДЕНИЕ",
    PreflightVerdict.FAILED: "ПРОВАЛ",
}

# Идентификация провайдера по IP-заглушке
_BLOCK_IP_PROVIDERS: dict[str, str] = {
    "195.82.146.214": "Ростелеком",
    "81.19.72.32": "МТС",
    "213.180.193.250": "Билайн",
    "217.169.80.229": "Мегафон",
    "62.33.207.196": "РКН",
    "62.33.207.197": "РКН",
    "62.33.207.198": "РКН",
    "127.0.0.1": "loopback",
    "0.0.0.0": "null-route",
    "10.10.10.10": "внутренняя заглушка",
}


def _identify_provider(ip: str) -> str:
    """Определяем провайдера по IP-заглушке."""
    return _BLOCK_IP_PROVIDERS.get(ip, "неизвестный")


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_dns(domain: str, timeout: float = PREFLIGHT_DNS_TIMEOUT) -> SingleTestResult:
    """Резолвим домен через системный DNS, сравниваем IP с заглушками провайдеров.

    getaddrinfo() не поддерживает таймаут и не прерывается, поэтому идём через
    net_resolve — он ждёт результат с дедлайном на демоническом потоке.
    """
    start = time.time()

    try:
        results = resolve_addrinfo(
            domain, 443,
            timeout=timeout,
            family=socket.AF_UNSPEC,
            socktype=socket.SOCK_STREAM,
        )
    except DNSTimeoutError:
        return SingleTestResult(
            target_name=domain, test_type=TestType.PREFLIGHT_DNS,
            status=TestStatus.TIMEOUT, error_code="DNS_TIMEOUT",
            time_ms=round((time.time() - start) * 1000, 2),
            detail=f"DNS таймаут — сервер не ответил за {timeout:.0f}с",
        )
    except socket.gaierror as e:
        return SingleTestResult(
            target_name=domain, test_type=TestType.PREFLIGHT_DNS,
            status=TestStatus.FAIL, error_code="DNS_FAIL",
            time_ms=round((time.time() - start) * 1000, 2),
            detail=f"домен не резолвится — {e}",
        )
    except Exception as e:
        return SingleTestResult(
            target_name=domain, test_type=TestType.PREFLIGHT_DNS,
            status=TestStatus.ERROR, error_code="ERROR",
            time_ms=round((time.time() - start) * 1000, 2),
            detail=f"ошибка DNS: {str(e)[:80]}",
        )

    elapsed = (time.time() - start) * 1000

    if not results:
        return SingleTestResult(
            target_name=domain, test_type=TestType.PREFLIGHT_DNS,
            status=TestStatus.FAIL, error_code="NO_RECORDS",
            time_ms=round(elapsed, 2),
            detail="домен не резолвится — DNS не вернул записей",
        )

    ips = sorted({addr[4][0] for addr in results})
    blocked = [ip for ip in ips if ip in KNOWN_BLOCK_IPS]

    if blocked:
        provider = _identify_provider(blocked[0])
        return SingleTestResult(
            target_name=domain, test_type=TestType.PREFLIGHT_DNS,
            status=TestStatus.FAIL, error_code="BLOCK_IP",
            time_ms=round(elapsed, 2),
            detail=f"IP-заглушка {provider} ({', '.join(blocked)})",
            raw_data={"ips": ips, "blocked": blocked},
        )

    return SingleTestResult(
        target_name=domain, test_type=TestType.PREFLIGHT_DNS,
        status=TestStatus.OK, time_ms=round(elapsed, 2),
        detail=f"{len(ips)} адресов ({', '.join(ips)})",
        raw_data={"ips": ips},
    )


def _check_tcp_443(domain: str, resolved_ip: str | None = None,
                   timeout: float = PREFLIGHT_TCP_TIMEOUT) -> SingleTestResult:
    """Пробуем открыть TCP-соединение на порт 443.

    Подключаемся только по IP: ``connect_ex`` с именем хоста снова уходит в
    неограниченный по времени резолв, который ``settimeout`` не покрывает.
    """
    start = time.time()

    host = resolved_ip
    if not host:
        host = resolve_ipv4(domain, timeout=PREFLIGHT_DNS_TIMEOUT)
    if not host:
        return SingleTestResult(
            target_name=domain, test_type=TestType.PREFLIGHT_TCP,
            status=TestStatus.FAIL, error_code="NO_ADDR",
            time_ms=round((time.time() - start) * 1000, 2),
            detail="пропущен — нет IPv4-адреса",
        )

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        err = sock.connect_ex((host, 443))
        elapsed = (time.time() - start) * 1000

        if err == 0:
            return SingleTestResult(
                target_name=domain, test_type=TestType.PREFLIGHT_TCP,
                status=TestStatus.OK, time_ms=round(elapsed, 2),
                detail=f"порт открыт ({host}), {elapsed:.0f}мс",
            )
        return SingleTestResult(
            target_name=domain, test_type=TestType.PREFLIGHT_TCP,
            status=TestStatus.FAIL, error_code=f"ERR_{err}",
            time_ms=round(elapsed, 2),
            detail=f"порт закрыт или заблокирован (errno={err})",
        )
    except socket.timeout:
        return SingleTestResult(
            target_name=domain, test_type=TestType.PREFLIGHT_TCP,
            status=TestStatus.TIMEOUT, error_code="TIMEOUT",
            time_ms=round((time.time() - start) * 1000, 2),
            detail=f"таймаут — порт не ответил за {timeout:.0f}с",
        )
    except Exception as e:
        return SingleTestResult(
            target_name=domain, test_type=TestType.PREFLIGHT_TCP,
            status=TestStatus.ERROR, error_code="ERROR",
            time_ms=round((time.time() - start) * 1000, 2),
            detail=f"ошибка: {str(e)[:80]}",
        )
    finally:
        sock.close()


def _check_http_get(domain: str, resolved_ip: str | None = None) -> SingleTestResult:
    """HTTP GET на порт 80 — детекция ISP-инъекции / страницы-заглушки."""
    # check_http_injection always returns a fresh SingleTestResult, safe to mutate
    result = check_http_injection(
        domain, timeout=PREFLIGHT_HTTP_TIMEOUT, resolved_ip=resolved_ip,
    )
    result.test_type = TestType.PREFLIGHT_HTTP

    # Russify details
    if result.status == TestStatus.OK:
        result.detail = "провайдер не подменяет контент"
    elif result.error_code == "HTTP_INJECT":
        # Keep original detail — it already has the marker info
        pass
    elif result.error_code == "TIMEOUT":
        result.detail = "таймаут — порт 80 не ответил"
    elif result.error_code == "CONNECT_ERR":
        result.detail = "порт 80 закрыт (нормально для HTTPS-сайтов)"

    return result


# ---------------------------------------------------------------------------
# Per-domain orchestration
# ---------------------------------------------------------------------------

# Запас поверх собственного таймаута проверки: планирование потока, повторный
# резолв из кэша, накладные расходы TLS-стека.
_CHECK_GRACE_SECONDS = 2.0


def _await_check(
    future,
    domain: str,
    test_type: TestType,
    budget: float,
) -> SingleTestResult:
    """Забираем результат проверки, не позволяя ей задержать нас навсегда.

    Каждая проверка ограничена по времени изнутри, но страховка нужна: без неё
    единственная незакрывшаяся операция снова подвешивает весь preflight.
    """
    try:
        return future.result(timeout=budget + PREFLIGHT_DNS_TIMEOUT + _CHECK_GRACE_SECONDS)
    except FuturesTimeout:
        return SingleTestResult(
            target_name=domain, test_type=test_type,
            status=TestStatus.TIMEOUT, error_code="TIMEOUT",
            detail="проверка не уложилась в отведённое время",
        )
    except Exception as e:  # noqa: BLE001 — падение одной проверки не рушит preflight
        logger.debug("Preflight check %s failed for %s: %s", test_type, domain, e)
        return SingleTestResult(
            target_name=domain, test_type=test_type,
            status=TestStatus.ERROR, error_code="ERROR",
            detail=f"ошибка: {str(e)[:80]}",
        )


def check_one_domain(domain: str, cancelled: Callable[[], bool] | None = None) -> PreflightResult:
    """Запускаем все 4 проверки для одного домена.

    Публичный API — используется и в run_preflight (массовый), и в
    strategy_scanner (один домен).
    """
    pf = PreflightResult(domain=domain)

    def _is_cancelled() -> bool:
        if not callable(cancelled):
            return False
        try:
            return bool(cancelled())
        except Exception:
            return False

    def _mark_cancelled() -> PreflightResult:
        pf.verdict = PreflightVerdict.WARNING
        pf.verdict_detail = "проверка отменена"
        return pf

    # 1. DNS резолвинг + IP blocklist. Резолв уже ограничен по времени внутри
    #    net_resolve, отдельный пул под него не нужен.
    if _is_cancelled():
        return _mark_cancelled()

    dns_r = _check_dns(domain)
    pf.dns_result = dns_r
    if dns_r.raw_data.get("ips"):
        pf.resolved_ips = dns_r.raw_data["ips"]
    if dns_r.error_code == "BLOCK_IP":
        pf.is_block_ip = True
        pf.block_ip_detail = dns_r.detail

    # Выбираем первый IPv4 для TCP/Ping/HTTP — так они не резолвят имя повторно
    first_ipv4 = None
    for ip in pf.resolved_ips:
        if ":" not in ip:  # skip IPv6
            first_ipv4 = ip
            break

    # Без IPv4 проверять нечего: TCP :443, ICMP и HTTP :80 здесь работают
    # только по IPv4. Раньше они шли по имени хоста и каждая заново упиралась
    # в тот же неотвечающий DNS, утраивая время зависания.
    if not first_ipv4:
        pf.verdict, pf.verdict_detail = compute_verdict(pf)
        if pf.verdict == PreflightVerdict.PASSED:
            # DNS ответил, но только IPv6 — остальные проверки не выполнялись,
            # и объявлять «все проверки пройдены» было бы неправдой.
            pf.verdict = PreflightVerdict.WARNING
            pf.verdict_detail = (
                "нет IPv4-адреса — TCP :443, Ping и HTTP :80 не проверялись"
            )
        return pf

    # 2-4 параллельно: TCP, Ping, HTTP GET
    pool = ThreadPoolExecutor(max_workers=3)
    try:
        tcp_future = pool.submit(_check_tcp_443, domain, first_ipv4)
        ping_future = pool.submit(
            ping_host, domain,
            count=PREFLIGHT_PING_COUNT, timeout=PREFLIGHT_PING_TIMEOUT,
            resolved_ip=first_ipv4,
        )
        http_future = pool.submit(_check_http_get, domain, first_ipv4)

        if _is_cancelled():
            return _mark_cancelled()

        pf.tcp_443 = _await_check(
            tcp_future, domain, TestType.PREFLIGHT_TCP, PREFLIGHT_TCP_TIMEOUT,
        )
        if _is_cancelled():
            return _mark_cancelled()

        ping_result = _await_check(
            ping_future, domain, TestType.PREFLIGHT_PING, PREFLIGHT_PING_TIMEOUT,
        )
        ping_result.test_type = TestType.PREFLIGHT_PING
        pf.ping = ping_result
        if _is_cancelled():
            return _mark_cancelled()

        pf.http_check = _await_check(
            http_future, domain, TestType.PREFLIGHT_HTTP, PREFLIGHT_HTTP_TIMEOUT,
        )
    finally:
        # Никогда не ждём: все три проверки ограничены по времени изнутри, а
        # ждать здесь означало бы вернуть ровно тот дедлок, из-за которого
        # BlockCheck зависал на первой фазе.
        pool.shutdown(wait=False, cancel_futures=True)

    # Вычисляем verdict
    pf.verdict, pf.verdict_detail = compute_verdict(pf)
    return pf


def compute_verdict(pf: PreflightResult) -> tuple[PreflightVerdict, str]:
    """Определяем итоговый verdict по результатам всех проверок."""
    reasons: list[str] = []

    # Критичные ошибки → FAILED
    if pf.dns_result and pf.dns_result.status in (TestStatus.FAIL, TestStatus.TIMEOUT):
        if pf.is_block_ip:
            reasons.append(f"DNS заглушка провайдера — провайдер подменяет IP ({pf.block_ip_detail})")
        elif pf.dns_result.error_code == "DNS_FAIL":
            reasons.append("DNS не резолвится — домен не найден или DNS-сервер не отвечает")
        elif pf.dns_result.error_code == "DNS_TIMEOUT":
            reasons.append("DNS таймаут — DNS-сервер не ответил")
        else:
            reasons.append(f"DNS ошибка — {pf.dns_result.detail}")

    # HTTP_INJECT = ISP страница-заглушка; CONNECT_ERR/TIMEOUT на порту 80
    # ожидаемы для HTTPS-сайтов и не считаются ошибкой preflight.
    if pf.http_check and pf.http_check.status == TestStatus.FAIL:
        if pf.http_check.error_code == "HTTP_INJECT":
            reasons.append(f"ISP инъекция — провайдер подставляет страницу-заглушку")

    if reasons:
        return PreflightVerdict.FAILED, "; ".join(reasons)

    # Предупреждения. Молчание на ICMP сюда не входит: CDN штатно не отвечают
    # на ping, и раньше это давало предупреждение почти на каждом домене.
    if pf.tcp_443 and pf.tcp_443.status != TestStatus.OK:
        return (
            PreflightVerdict.WARNING,
            "TCP :443 недоступен — порт закрыт или IP заблокирован",
        )

    return PreflightVerdict.PASSED, "все проверки пройдены (DNS, TCP :443, HTTP)"


# ---------------------------------------------------------------------------
# Форматирование лога для одного домена
# ---------------------------------------------------------------------------

def format_domain_log(pf: PreflightResult) -> str:
    """Формируем подробный лог одного домена с пояснениями."""
    lines = [f"  Preflight {pf.domain}:"]
    is_block_ip = pf.is_block_ip
    dns_failed = pf.dns_result and pf.dns_result.status != TestStatus.OK if pf.dns_result else False

    # --- DNS ---
    if pf.dns_result:
        timing = f", {pf.dns_result.time_ms:.0f}мс" if pf.dns_result.time_ms else ""
        if pf.dns_result.status == TestStatus.OK:
            lines.append(f"    DNS: ОК — {pf.dns_result.detail}{timing}")
            lines.append("      → IP не в списке заглушек провайдеров")
        elif pf.dns_result.error_code == "BLOCK_IP":
            lines.append(f"    DNS: ПРОВАЛ — {pf.dns_result.detail}{timing}")
            blocked = pf.dns_result.raw_data.get("blocked", [])
            if blocked:
                provider = _identify_provider(blocked[0])
                lines.append(f"      → Провайдер подменяет DNS на IP-заглушку ({provider})")
            lines.append("      → Рекомендация: смените DNS на DoH (1.1.1.1 или 8.8.8.8)")
        else:
            lines.append(f"    DNS: ПРОВАЛ — {pf.dns_result.detail}{timing}")
            lines.append("      → Домен не существует или DNS-сервер не отвечает")
    else:
        lines.append("    DNS: — (не выполнен)")

    # --- TCP :443 ---
    if pf.tcp_443:
        timing = f", {pf.tcp_443.time_ms:.0f}мс" if pf.tcp_443.time_ms else ""
        if pf.tcp_443.status == TestStatus.OK:
            lines.append(f"    TCP :443: ОК — {pf.tcp_443.detail}")
            if is_block_ip:
                lines.append("      → Это заглушка, не реальный сервер")
        elif pf.tcp_443.status == TestStatus.TIMEOUT:
            lines.append(f"    TCP :443: ПРЕДУПРЕЖДЕНИЕ — {pf.tcp_443.detail}")
            lines.append("      → Возможно IP заблокирован или DPI сбрасывает соединение")
        else:
            lines.append(f"    TCP :443: ОШИБКА — {pf.tcp_443.detail}")
    elif dns_failed:
        lines.append("    TCP :443: — (пропущен, нет IP-адреса)")
    else:
        lines.append("    TCP :443: — (пропущен)")

    # --- Ping ---
    if pf.ping:
        if pf.ping.status == TestStatus.OK:
            ping_detail = pf.ping.detail or ""
            lines.append(f"    Ping: ОК — хост отвечает, {ping_detail}")
            if is_block_ip:
                lines.append("      → Но это IP заглушки провайдера")
        else:
            lines.append(f"    Ping: ПРЕДУПРЕЖДЕНИЕ — хост не отвечает на ping")
            lines.append("      → Многие CDN (Cloudflare, Google) блокируют ICMP — это нормально")
    elif dns_failed:
        lines.append("    Ping: — (пропущен, нет IP-адреса)")
    else:
        lines.append("    Ping: — (пропущен)")

    # --- HTTP :80 ---
    if pf.http_check:
        timing = f", {pf.http_check.time_ms:.0f}мс" if pf.http_check.time_ms else ""
        if pf.http_check.status == TestStatus.OK:
            lines.append(f"    HTTP :80: ОК — {pf.http_check.detail}{timing}")
        elif pf.http_check.error_code == "HTTP_INJECT":
            lines.append(f"    HTTP :80: ISP ИНЪЕКЦИЯ — {pf.http_check.detail}{timing}")
            lines.append("      → Провайдер перенаправляет HTTP на страницу блокировки")
        elif pf.http_check.error_code == "CONNECT_ERR":
            lines.append(f"    HTTP :80: — {pf.http_check.detail}")
        elif pf.http_check.error_code == "TIMEOUT":
            lines.append(f"    HTTP :80: — {pf.http_check.detail}")
        else:
            lines.append(f"    HTTP :80: {pf.http_check.detail}")
    elif dns_failed:
        lines.append("    HTTP :80: — (пропущен, нет IP-адреса)")
    else:
        lines.append("    HTTP :80: — (пропущен)")

    # --- Итого ---
    verdict_ru = _VERDICT_RU.get(pf.verdict, pf.verdict.value)
    lines.append(f"    Итого: {verdict_ru} — {pf.verdict_detail}")

    return "\n".join(lines)
