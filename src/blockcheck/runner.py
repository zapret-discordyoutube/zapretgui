"""BlockCheck runner — планировщик проб и сборка отчёта.

Три этапа вместо прежних семи последовательных фаз:

1. **Опорная точка.** Контрольная группа хостов и однократный резолв всех целей.
   Раньше один хост резолвился до пяти раз: в preflight, в каждой из трёх
   TLS-проб, в ISP-пробе и в ping-пробе.
2. **Пробы.** Все проверки — TLS, ISP, DNS, TCP, STUN, ping — идут одной
   параллельной группой под общим дедлайном. Зависимостей между ними нет,
   барьеры не нужны.
3. **Анализ.** Чистые вычисления: исход каждой цели и вердикт отчёта.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from utils.concurrency import iter_completed
from utils.net_resolve import clear_cache as clear_dns_cache, resolve_ips

from blockcheck.config import (
    DEFAULT_PARALLEL,
    DNS_TIMEOUT,
    HTTPS_TIMEOUT,
    MAX_PARALLEL,
    RUN_DEADLINE_SECONDS,
    STUN_TIMEOUT,
)
from blockcheck.dns_integrity import check_dns_integrity
from blockcheck.hosts import host_candidates, host_of, is_pseudo_target
from blockcheck.isp_page_detector import check_http_injection, detect_isp_page
from blockcheck.models import (
    BlockcheckReport,
    DnsVerdict,
    DNSIntegrityResult,
    InconclusiveReason,
    NetworkBaseline,
    SingleTestResult,
    TargetOutcome,
    TargetResult,
    TestStatus,
    TestType,
)
from blockcheck.ping_tester import ping_host
from blockcheck.stun_tester import test_stun
from blockcheck.targets import (
    build_targets_with_user_domains,
    load_domains_with_source,
    load_tcp_targets_with_source,
    select_tcp_targets,
)
from blockcheck.tcp_test import check_tcp_16_20
from blockcheck.tls_tester import test_https
from blockcheck.verdict import build_report_verdict, judge_target, probe_baseline

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Callback protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class BlockcheckCallback(Protocol):
    def on_target_started(self, name: str, index: int, total: int) -> None: ...
    def on_test_result(self, result: SingleTestResult) -> None: ...
    def on_target_complete(self, result: TargetResult) -> None: ...
    def on_progress(self, current: int, total: int, message: str) -> None: ...
    def on_phase_change(self, phase: str) -> None: ...
    def on_log(self, message: str) -> None: ...
    def is_cancelled(self) -> bool: ...


class _NullCallback:
    """No-op callback for headless usage."""
    def on_target_started(self, name, index, total): pass
    def on_test_result(self, result): pass
    def on_target_complete(self, result): pass
    def on_progress(self, current, total, message): pass
    def on_phase_change(self, phase): pass
    def on_log(self, message): pass
    def is_cancelled(self): return False


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------

class RunMode:
    QUICK = "quick"       # HTTPS + Ping only (fastest)
    FULL = "full"         # All tests
    DPI_ONLY = "dpi_only" # TLS + DPI + DNS + ISP (no ping/stun)


# ---------------------------------------------------------------------------
# Внутренние структуры планировщика
# ---------------------------------------------------------------------------

@dataclass
class ResolvedHost:
    """Результат однократного резолва хоста — общий для всех проб."""

    host: str
    ipv4: list[str] = field(default_factory=list)
    ipv6: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.ipv4 or self.ipv6)


@dataclass
class ProbeJob:
    """Одна проба: что запустить и к какой цели приклеить результат."""

    kind: str
    label: str
    run: Any
    target: TargetResult | None = None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class BlockcheckRunner:
    """Orchestrates all blockcheck tests. Sync — call from QThread."""

    QUICK_TIMEOUT = 5   # seconds — shorter for quick mode

    def __init__(
        self,
        mode: str = RunMode.FULL,
        timeout: int | None = None,
        parallel: int = DEFAULT_PARALLEL,
        callback: BlockcheckCallback | None = None,
        extra_domains: list[str] | None = None,
        skip_preflight_failed: bool = False,
        deadline_seconds: float = RUN_DEADLINE_SECONDS,
    ):
        self.mode = mode
        if timeout is not None:
            self.timeout = timeout
        elif mode == RunMode.QUICK:
            self.timeout = self.QUICK_TIMEOUT
        else:
            self.timeout = HTTPS_TIMEOUT
        self.parallel = max(1, min(int(parallel), MAX_PARALLEL))
        self.cb = callback or _NullCallback()
        self._cancelled = threading.Event()
        self._extra_domains = extra_domains
        self._skip_preflight_failed = skip_preflight_failed
        self._deadline_seconds = float(deadline_seconds)
        self._deadline: float | None = None

    def cancel(self) -> None:
        """Thread-safe cancellation."""
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set() or self.cb.is_cancelled()

    @property
    def expired(self) -> bool:
        return self._deadline is not None and time.monotonic() >= self._deadline

    def should_stop(self) -> bool:
        """Единый признак остановки: отмена пользователем или общий дедлайн."""
        return self.cancelled or self.expired

    def _remaining(self) -> float | None:
        if self._deadline is None:
            return None
        return max(0.0, self._deadline - time.monotonic())

    # ------------------------------------------------------------------
    # Точка входа
    # ------------------------------------------------------------------

    def run(self) -> BlockcheckReport:
        start_time = time.time()
        self._deadline = time.monotonic() + self._deadline_seconds
        # Диагностика должна видеть свежее состояние DNS, а не ответы прошлого
        # прогона. Внутри одного прогона кэш, наоборот, нужен: домен резолвится
        # в нескольких фазах подряд.
        clear_dns_cache()

        report = BlockcheckReport()
        targets = build_targets_with_user_domains(self._extra_domains)

        report.baseline = self._stage_baseline()
        resolution = self._stage_resolve(targets)
        report.targets = self._stage_probe(report, targets, resolution)

        self._stage_judge(report)

        was_cancelled = self.cancelled
        report.elapsed_seconds = time.time() - start_time
        report.cancelled = was_cancelled

        if was_cancelled:
            self.cb.on_phase_change("Отменено")
            self.cb.on_log(f"\nCancelled after {report.elapsed_seconds:.1f}s")
        else:
            self.cb.on_phase_change("Готово")
            self.cb.on_log(f"\nCompleted in {report.elapsed_seconds:.1f}s")
        return report

    # ------------------------------------------------------------------
    # Этап 1: опорная точка и резолв
    # ------------------------------------------------------------------

    def _stage_baseline(self) -> NetworkBaseline:
        if self.should_stop():
            return NetworkBaseline()

        self.cb.on_phase_change("Этап 1/3: контрольная проверка сети")
        self.cb.on_log("=== Контрольная группа ===")
        baseline = probe_baseline(cancelled=self.should_stop, log=self.cb.on_log)

        if not baseline.internet_ok:
            self.cb.on_log(
                "WARNING: контрольные хосты недоступны — выводы о блокировках "
                "делаться не будут"
            )
        if not baseline.icmp_usable:
            self.cb.on_log(
                "ICMP недоступен на этой машине или в этой сети — ping показан "
                "справочно и на вердикт не влияет"
            )
        if not baseline.ipv6_usable:
            self.cb.on_log("IPv6 недоступен — пробы по IPv6 пропускаются")
        return baseline

    def _stage_resolve(self, targets: list[dict]) -> dict[str, ResolvedHost]:
        """Резолвит каждый уникальный хост ровно один раз."""
        hosts = self._unique_hosts(targets)
        if not hosts or self.should_stop():
            return {}

        self.cb.on_log(f"=== Резолв {len(hosts)} хостов ===")

        def _resolve_one(host: str) -> ResolvedHost:
            # resolve_ips не бросает исключений и сам ограничен по времени.
            ipv4, ipv6 = resolve_ips(host, port=443, timeout=DNS_TIMEOUT)
            return ResolvedHost(host=host, ipv4=list(ipv4), ipv6=list(ipv6))

        collected: dict[str, ResolvedHost] = {}
        pool = ThreadPoolExecutor(max_workers=max(1, min(self.parallel, len(hosts))))
        try:
            futures = {pool.submit(_resolve_one, host): host for host in hosts}
            for future in iter_completed(futures, cancelled=self.should_stop):
                host = futures[future]
                try:
                    resolved = future.result()
                except Exception as e:  # noqa: BLE001 — сбой резолва не рушит прогон
                    resolved = ResolvedHost(host=host, error=str(e)[:100])
                collected[host] = resolved
                self.cb.on_log(
                    f"  DNS {host}: IPv4={resolved.ipv4 or ['-']}, "
                    f"IPv6={resolved.ipv6 or ['-']}"
                    + (" → не резолвится" if not resolved.ok else "")
                )
        finally:
            # При отмене ждать незавершимые сетевые задачи нельзя.
            pool.shutdown(wait=False, cancel_futures=True)

        # Порядок целей сохраняем: по нему строится preflight-сводка.
        resolution = {
            host: collected.get(host) or ResolvedHost(host=host, error="не проверялся")
            for host in hosts
        }

        unresolved = [host for host, item in resolution.items() if not item.ok]
        if unresolved:
            self.cb.on_log(
                f"Не резолвятся ({len(unresolved)}): {', '.join(unresolved[:5])}"
                + (f" (+{len(unresolved) - 5})" if len(unresolved) > 5 else "")
            )
        return resolution

    # ------------------------------------------------------------------
    # Этап 2: пробы
    # ------------------------------------------------------------------

    def _stage_probe(
        self,
        report: BlockcheckReport,
        targets: list[dict],
        resolution: dict[str, ResolvedHost],
    ) -> list[TargetResult]:
        results, jobs = self._plan(report, targets, resolution)
        if not jobs or self.should_stop():
            return results

        self.cb.on_phase_change("Этап 2/3: пробы")
        self.cb.on_log(f"\n=== Пробы: {len(jobs)} проверок в {self.parallel} потоков ===")

        expected: dict[int, int] = {}
        for job in jobs:
            if job.target is not None:
                expected[id(job.target)] = expected.get(id(job.target), 0) + 1
        received: dict[int, int] = {}
        completed: set[int] = set()

        done = 0
        total = len(jobs)
        pool = ThreadPoolExecutor(max_workers=self.parallel)
        try:
            futures = {pool.submit(job.run): job for job in jobs}
            for future in iter_completed(
                futures, cancelled=self.should_stop, timeout=self._remaining(),
            ):
                job = futures[future]
                done += 1
                try:
                    payload = future.result()
                except Exception:  # noqa: BLE001 — падение пробы не рушит прогон
                    logger.debug("Probe %s failed", job.label, exc_info=True)
                    payload = None

                self._absorb(report, job, payload)
                self.cb.on_progress(done, total, job.label)

                target = job.target
                if target is None:
                    continue
                key = id(target)
                received[key] = received.get(key, 0) + 1
                if received[key] >= expected.get(key, 0) and key not in completed:
                    completed.add(key)
                    self.cb.on_target_complete(target)
        except FuturesTimeout:
            self.cb.on_log("WARNING: превышен общий лимит времени — часть целей не проверена")
        finally:
            # Результаты уже собраны в цикле выше; при отмене ждать незавершимые
            # сетевые задачи нельзя — именно это и подвешивало BlockCheck.
            pool.shutdown(wait=False, cancel_futures=True)

        for target in results:
            if id(target) not in completed and target.tests:
                self.cb.on_target_complete(target)
        return results

    def _plan(
        self,
        report: BlockcheckReport,
        targets: list[dict],
        resolution: dict[str, ResolvedHost],
    ) -> tuple[list[TargetResult], list[ProbeJob]]:
        """Строит цели и полный список проб под текущий режим."""
        baseline = report.baseline
        results: list[TargetResult] = []
        jobs: list[ProbeJob] = []

        https_targets = [t for t in targets if not is_pseudo_target(t["value"])]
        total = len(https_targets)

        for index, target in enumerate(https_targets):
            host = host_of(target["value"])
            resolved = resolution.get(host)

            if resolved is not None and self._skip_preflight_failed and not resolved.ok:
                self.cb.on_log(f"  Пропущен (не резолвится): {target['name']}")
                continue

            tr = TargetResult(name=target["name"], value=target["value"])
            results.append(tr)

            if resolved is None:
                # Резолв не выполнялся (отмена или дедлайн) — цель осталась без
                # данных, но это не значит, что хоста не существует.
                continue

            if not resolved.ok:
                # Мёртвый хост незачем пробовать шесть раз: исход известен, а
                # именно эти пробы раньше и давали ложную «полную блокировку».
                self._attach_unresolved(tr, resolved)
                continue

            self.cb.on_target_started(target["name"], index, total)
            jobs.extend(self._https_jobs(tr, host, resolved, baseline))

        if self.mode in (RunMode.FULL, RunMode.DPI_ONLY):
            jobs.append(
                ProbeJob(kind="dns", label="DNS integrity", run=self._probe_dns_integrity)
            )
            tcp_target = self._append_tcp_jobs(jobs)
            if tcp_target is not None:
                results.append(tcp_target)

        if self.mode == RunMode.FULL:
            for target in targets:
                if not str(target["value"]).startswith("STUN:"):
                    continue
                tr = TargetResult(name=target["name"], value=target["value"])
                results.append(tr)
                jobs.append(
                    ProbeJob(
                        kind="test",
                        label=f"STUN {target['name']}",
                        target=tr,
                        run=lambda value=target["value"], name=target["name"]:
                            self._probe_stun(value, name),
                    )
                )

        if self.mode in (RunMode.FULL, RunMode.QUICK) and baseline.icmp_usable:
            for target in targets:
                if not str(target["value"]).startswith("PING:"):
                    continue
                # Отдельные ping-цели диагностируют канал ICMP, а не сервис,
                # поэтому в знаменатель вердикта не входят.
                tr = TargetResult(name=target["name"], value=target["value"], informational=True)
                results.append(tr)
                host = str(target["value"]).replace("PING:", "").strip()
                jobs.append(
                    ProbeJob(
                        kind="test",
                        label=f"Ping {target['name']}",
                        target=tr,
                        run=lambda h=host, name=target["name"]: self._probe_ping(h, name),
                    )
                )
        elif self.mode in (RunMode.FULL, RunMode.QUICK):
            self.cb.on_log("Ping-цели пропущены: ICMP недоступен")

        return results, jobs

    def _https_jobs(
        self,
        tr: TargetResult,
        host: str,
        resolved: ResolvedHost,
        baseline: NetworkBaseline,
    ) -> list[ProbeJob]:
        families = ["ipv4"] if resolved.ipv4 else []
        if resolved.ipv6 and baseline.ipv6_usable:
            families.append("ipv6")
        if not families:
            families = ["ipv4"]

        jobs = [
            ProbeJob(
                kind="test",
                label=f"{tr.name} {label} [{family}]",
                target=tr,
                run=lambda v=version, f=family: self._probe_https(host, tr.name, v, f),
            )
            for version, label in ((None, "HTTP"), ("1.2", "TLS1.2"), ("1.3", "TLS1.3"))
            for family in families
        ]

        # Адрес уже известен с этапа 1 — пробы не резолвят имя заново.
        first_ipv4 = resolved.ipv4[0] if resolved.ipv4 else None

        if self.mode in (RunMode.FULL, RunMode.DPI_ONLY):
            jobs.append(
                ProbeJob(
                    kind="test",
                    label=f"ISP {tr.name}",
                    target=tr,
                    run=lambda: self._probe_isp(host, tr.name, first_ipv4),
                )
            )

        if self.mode in (RunMode.FULL, RunMode.QUICK) and baseline.icmp_usable:
            jobs.append(
                ProbeJob(
                    kind="test",
                    label=f"Ping {tr.name}",
                    target=tr,
                    run=lambda: self._probe_ping(host, tr.name, first_ipv4),
                )
            )
        return jobs

    def _append_tcp_jobs(self, jobs: list[ProbeJob]) -> TargetResult | None:
        all_tcp, source = load_tcp_targets_with_source()
        self.cb.on_log(f"TCP targets source: {source} ({len(all_tcp)} total)")

        selected = select_tcp_targets(all_tcp)
        if not selected:
            self.cb.on_log("WARNING: не выбрано ни одной цели для TCP 16-20KB")
            return None

        selected_ids = ", ".join(
            str(t.get("id") or t.get("name") or t.get("url") or "?") for t in selected
        )
        self.cb.on_log(f"TCP selected IDs: {selected_ids}")

        tr = TargetResult(name="TCP 16-20KB", value="TCP:16-20KB")
        for tcp_target in selected:
            jobs.append(
                ProbeJob(
                    kind="test",
                    label=f"TCP {tcp_target.get('name') or tcp_target.get('id')}",
                    target=tr,
                    run=lambda t=tcp_target: self._probe_tcp(t),
                )
            )
        return tr

    # ------------------------------------------------------------------
    # Отдельные пробы
    # ------------------------------------------------------------------

    def _probe_https(
        self, host: str, name: str, tls_version: str | None, family: str,
    ) -> SingleTestResult:
        result = test_https(host, timeout=self.timeout, tls_version=tls_version, ip_family=family)
        result.target_name = name
        result.raw_data.setdefault("ip_family", family)
        return result

    def _probe_isp(
        self, host: str, name: str, resolved_ip: str | None = None,
    ) -> SingleTestResult:
        http_result = check_http_injection(host, resolved_ip=resolved_ip)
        http_result.target_name = name
        if http_result.status == TestStatus.FAIL and http_result.error_code == "HTTP_INJECT":
            return http_result

        https_result = detect_isp_page(host)
        https_result.target_name = name
        if https_result.status == TestStatus.FAIL and https_result.error_code == "ISP_PAGE":
            return https_result
        if http_result.status != TestStatus.OK and https_result.status == TestStatus.OK:
            return https_result
        return http_result

    def _probe_ping(
        self, host: str, name: str, resolved_ip: str | None = None,
    ) -> SingleTestResult:
        result = ping_host(host, resolved_ip=resolved_ip)
        result.target_name = name
        return result

    def _probe_stun(self, value: str, name: str) -> SingleTestResult:
        host, port = _parse_stun_endpoint(value)
        result = test_stun(host, port, timeout=STUN_TIMEOUT)
        result.target_name = name
        return result

    def _probe_tcp(self, tcp_target: dict) -> SingleTestResult:
        target_name = str(
            tcp_target.get("name") or tcp_target.get("id") or tcp_target.get("url", "unknown")
        )
        result = check_tcp_16_20(str(tcp_target.get("url") or ""))
        result.target_name = target_name
        result.raw_data.setdefault("target_id", target_name)
        result.raw_data.setdefault("provider", str(tcp_target.get("provider") or "unknown"))
        asn = str(tcp_target.get("asn") or "")
        if asn:
            result.raw_data.setdefault("asn", asn)
        url = str(tcp_target.get("url") or "")
        if url:
            result.raw_data.setdefault("url", url)
        return result

    def _probe_dns_integrity(self) -> list[DNSIntegrityResult]:
        domains, source = load_domains_with_source()
        self.cb.on_log(f"DNS domains source: {source} ({len(domains)} total)")
        return check_dns_integrity(
            domains,
            callback=self.cb.on_log,
            cancelled=self.should_stop,
            parallel=self.parallel,
        )

    def _absorb(self, report: BlockcheckReport, job: ProbeJob, payload: Any) -> None:
        """Раскладывает результат пробы по отчёту."""
        if payload is None:
            return

        if job.kind == "dns":
            report.dns_integrity = list(payload)
            for item in report.dns_integrity:
                self.cb.on_log(
                    f"  DNS {item.domain}: UDP={item.udp_ips or ['-']}, "
                    f"DoH={item.doh_ips or ['-']} → {item.verdict.value} ({item.evidence})"
                )
            return

        if job.target is not None and isinstance(payload, SingleTestResult):
            job.target.tests.append(payload)
            self.cb.on_test_result(payload)
            self.cb.on_log(f"  {job.label}: {payload.status.value} {payload.detail}")

    @staticmethod
    def _attach_unresolved(tr: TargetResult, resolved: ResolvedHost) -> None:
        """Помечает цель как непроверяемую, не тратя на неё ни одной пробы."""
        detail = resolved.error or "имя не разрешается"
        tr.tests.append(
            SingleTestResult(
                target_name=tr.name,
                test_type=TestType.HTTP,
                status=TestStatus.ERROR,
                error_code="DNS_ERR",
                detail=f"DNS: {detail}",
            )
        )

    # ------------------------------------------------------------------
    # Этап 3: анализ
    # ------------------------------------------------------------------

    def _stage_judge(self, report: BlockcheckReport) -> None:
        self.cb.on_phase_change("Этап 3/3: анализ")
        self.cb.on_log("\n=== Анализ ===")

        self._attach_dns_evidence(report.targets, report.dns_integrity)
        expired = self.expired

        for target in report.targets:
            if expired and not target.tests:
                target.outcome = TargetOutcome.INCONCLUSIVE
                target.inconclusive_reason = InconclusiveReason.BUDGET_EXCEEDED
                target.classification_detail = "Не уложились в лимит времени"
                continue

            judgement = judge_target(target, report.baseline)
            target.outcome = judgement.outcome
            target.classification = judgement.classification
            target.inconclusive_reason = judgement.reason
            target.classification_detail = judgement.detail
            self.cb.on_log(
                f"  {target.name}: {judgement.outcome.value}"
                f"/{judgement.classification.value} — {judgement.detail}"
            )

        report.verdict = build_report_verdict(report.targets, report.baseline)
        self.cb.on_log(
            f"Вердикт: {report.verdict.code.value} — {report.verdict.detail} "
            f"(проверено {report.verdict.valid_targets}, "
            f"недостоверно {report.verdict.inconclusive_targets})"
        )

    @staticmethod
    def _attach_dns_evidence(
        target_results: list[TargetResult],
        dns_results: list[DNSIntegrityResult],
    ) -> None:
        """Приклеивает улику о подмене DNS к соответствующим целям."""
        fake_domains: set[str] = set()
        for dns_result in dns_results:
            if dns_result.verdict == DnsVerdict.FAKE:
                fake_domains.update(host_candidates(dns_result.domain))
        if not fake_domains:
            return

        for tr in target_results:
            if is_pseudo_target(tr.value):
                continue
            variants = host_candidates(tr.value)
            matched = any(
                variant == fake or variant.endswith(f".{fake}")
                for variant in variants
                for fake in fake_domains
            )
            if not matched:
                continue
            if any(t.error_code == "STUB" for t in tr.tests):
                continue
            tr.tests.append(
                SingleTestResult(
                    target_name=tr.name,
                    test_type=TestType.DNS_UDP,
                    status=TestStatus.FAIL,
                    error_code="STUB",
                    detail="DNS отдаёт адрес с чужим сертификатом",
                )
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _unique_hosts(targets: list[dict]) -> list[str]:
        seen: set[str] = set()
        hosts: list[str] = []
        for target in targets:
            value = target["value"]
            if is_pseudo_target(value):
                continue
            host = host_of(value)
            if host and host not in seen:
                seen.add(host)
                hosts.append(host)
        return hosts

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _parse_stun_endpoint(value: str) -> tuple[str, int]:
    raw = str(value or "").strip()
    if raw.upper().startswith("STUN:"):
        raw = raw[5:].strip()
    if not raw:
        return "", 3478

    if raw.startswith("["):
        right = raw.find("]")
        if right > 1:
            host = raw[1:right].strip()
            rest = raw[right + 1 :].strip()
            if rest.startswith(":"):
                try:
                    port = int(rest[1:])
                    if 1 <= port <= 65535:
                        return host, port
                except ValueError:
                    pass
            return host, 3478

    if raw.count(":") == 1:
        host, port_str = raw.rsplit(":", 1)
        host = host.strip()
        if host:
            try:
                port = int(port_str)
                if 1 <= port <= 65535:
                    return host, port
            except ValueError:
                pass
            return host, 3478

    return raw, 3478
