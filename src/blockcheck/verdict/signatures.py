"""Факты одной цели → исход и сигнатура DPI.

Главное отличие от прежнего ``DPIClassifier``: «проверить не удалось» больше не
маскируется под «заблокировано». Несуществующий хост, отсутствие связи и
неподдерживаемый протокол дают :class:`TargetOutcome.INCONCLUSIVE` и выпадают из
знаменателя вердикта отчёта.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from blockcheck.models import (
    DPIClassification,
    InconclusiveReason,
    NetworkBaseline,
    SingleTestResult,
    TargetOutcome,
    TargetResult,
    TestStatus,
    TestType,
)

__all__ = ["TargetFacts", "TargetJudgement", "collect_facts", "judge_target"]


# Пробы, по которым судим о доступности самого сервиса.
WEB_PROBE_TYPES = frozenset({
    TestType.HTTP,
    TestType.TLS_12,
    TestType.TLS_13,
    TestType.ISP_PAGE,
})

# Пробы-улики: сами по себе доступность не характеризуют.
EVIDENCE_PROBE_TYPES = frozenset({
    TestType.PING,
    TestType.DNS_UDP,
    TestType.DNS_DOH,
})

# Проба не дошла до сети: имя не разрешилось.
RESOLUTION_FAILURE_CODES = frozenset({
    "DNS_ERR",
    "DNS_TIMEOUT",
    "NO_ADDR",
    "NO_RECORDS",
    "RESOLVE_ERR",
})

TIMEOUT_CODES = frozenset({"TIMEOUT", "TCP_TIMEOUT", "TLS_TIMEOUT", "READ_TIMEOUT"})

# Сброс или обрыв на этапе TLS-хендшейка — классический почерк DPI.
TLS_DROP_CODES = frozenset({"TLS_RESET", "TCP_RESET", "TLS_EOF_EARLY"})

# Минимум успешных проб разного типа, ниже которого «всё легло» — не улика,
# а слишком маленькая выборка.
MIN_WEB_PROBES_FOR_FULL_BLOCK = 3


@dataclass(frozen=True)
class TargetJudgement:
    outcome: TargetOutcome
    classification: DPIClassification = DPIClassification.NONE
    reason: InconclusiveReason = InconclusiveReason.NONE
    detail: str = ""


@dataclass
class TargetFacts:
    """Нормализованные факты по цели — считаются один раз, читаются правилами."""

    web: list[SingleTestResult] = field(default_factory=list)
    web_unsupported: list[SingleTestResult] = field(default_factory=list)
    isp: list[SingleTestResult] = field(default_factory=list)
    stun: list[SingleTestResult] = field(default_factory=list)
    tcp: list[SingleTestResult] = field(default_factory=list)
    ping: list[SingleTestResult] = field(default_factory=list)
    dns: list[SingleTestResult] = field(default_factory=list)

    @property
    def web_ok(self) -> list[SingleTestResult]:
        return [test for test in self.web if test.status == TestStatus.OK]

    @property
    def web_failed(self) -> list[SingleTestResult]:
        return [test for test in self.web if test.status != TestStatus.OK]

    @property
    def has_any_probe(self) -> bool:
        return bool(self.web or self.web_unsupported or self.stun or self.tcp)

    @property
    def resolution_failed(self) -> bool:
        """Ни одна веб-проба не дошла до сети — имя не разрешилось."""
        candidates = self.web + self.web_unsupported
        if not candidates:
            return False
        return all(test.error_code in RESOLUTION_FAILURE_CODES for test in candidates)

    @property
    def host_reachable(self) -> bool:
        """Сам хост отвечает, даже если :443 молчит."""
        return any(test.status == TestStatus.OK for test in self.isp)


def collect_facts(result: TargetResult) -> TargetFacts:
    facts = TargetFacts()
    for test in result.tests:
        if test.test_type in WEB_PROBE_TYPES:
            if test.test_type == TestType.ISP_PAGE:
                facts.isp.append(test)
            # UNSUPPORTED исключается из знаменателя: отсутствие IPv6 или TLS 1.2
            # на стороне сервера — не блокировка.
            if test.status == TestStatus.UNSUPPORTED:
                facts.web_unsupported.append(test)
            else:
                facts.web.append(test)
        elif test.test_type == TestType.STUN:
            facts.stun.append(test)
        elif test.test_type == TestType.TCP_16_20:
            facts.tcp.append(test)
        elif test.test_type == TestType.PING:
            facts.ping.append(test)
        elif test.test_type in (TestType.DNS_UDP, TestType.DNS_DOH):
            facts.dns.append(test)
    return facts


def judge_target(result: TargetResult, baseline: NetworkBaseline) -> TargetJudgement:
    """Исход и сигнатура для одной цели с учётом опорной точки сети."""
    facts = collect_facts(result)

    if not result.tests or not facts.has_any_probe:
        return TargetJudgement(
            outcome=TargetOutcome.INCONCLUSIVE,
            reason=InconclusiveReason.NOT_PROBED,
            detail="Проверка не выполнялась",
        )

    # Без связи любые выводы про DPI недостоверны.
    if baseline.probed and not baseline.internet_ok:
        return TargetJudgement(
            outcome=TargetOutcome.INCONCLUSIVE,
            reason=InconclusiveReason.NO_BASELINE,
            detail="Нет связи с контрольными хостами — проверка недостоверна",
        )

    if facts.resolution_failed:
        return TargetJudgement(
            outcome=TargetOutcome.INCONCLUSIVE,
            reason=InconclusiveReason.HOST_NOT_RESOLVED,
            detail="Хост не существует или не резолвится",
        )

    signature = _detect_signature(facts, baseline)
    if signature is not None:
        classification, detail = signature
        return TargetJudgement(
            outcome=TargetOutcome.BLOCKED,
            classification=classification,
            detail=detail,
        )

    if facts.web_ok or any(test.status == TestStatus.OK for test in facts.stun + facts.tcp):
        return TargetJudgement(outcome=TargetOutcome.OK, detail="Доступ есть")

    if not facts.web and facts.web_unsupported:
        return TargetJudgement(
            outcome=TargetOutcome.INCONCLUSIVE,
            reason=InconclusiveReason.UNSUPPORTED_ONLY,
            detail="Протокол не поддерживается сервером — сравнивать нечего",
        )

    return TargetJudgement(
        outcome=TargetOutcome.INCONCLUSIVE,
        reason=InconclusiveReason.NOT_PROBED,
        detail="Недостаточно данных для вывода",
    )


# ---------------------------------------------------------------------------
# Детекторы сигнатур — от самой прямой улики к самой косвенной
# ---------------------------------------------------------------------------

def _detect_signature(
    facts: TargetFacts,
    baseline: NetworkBaseline,
) -> tuple[DPIClassification, str] | None:
    for detector in (
        _isp_injection,
        _tls_mitm,
        _tls_drop,
        _tcp_16_20,
        _tls_timeout_while_host_alive,
        _dns_fake,
        _stun_block,
        _full_block,
    ):
        found = detector(facts, baseline)
        if found is not None:
            return found
    return None


def _isp_injection(facts: TargetFacts, _baseline: NetworkBaseline):
    for test in facts.isp:
        if test.status != TestStatus.FAIL:
            continue
        if test.error_code == "ISP_PAGE":
            return DPIClassification.ISP_PAGE, "Страница-заглушка провайдера"
        if test.error_code == "HTTP_INJECT":
            return DPIClassification.HTTP_INJECT, "Провайдер подменяет HTTP-ответ"
    return None


def _tls_mitm(facts: TargetFacts, _baseline: NetworkBaseline):
    if any("MITM" in (test.error_code or "") for test in facts.web_failed):
        return DPIClassification.TLS_MITM, "Сертификат подменён (MITM-прокси)"
    return None


def _tls_drop(facts: TargetFacts, _baseline: NetworkBaseline):
    if any(test.error_code in TLS_DROP_CODES for test in facts.web_failed):
        return DPIClassification.TLS_DPI, "RST/EOF во время TLS-хендшейка"
    return None


def _tcp_16_20(facts: TargetFacts, _baseline: NetworkBaseline):
    if any(
        test.status == TestStatus.FAIL and test.error_code == "TCP_16_20"
        for test in facts.tcp
    ):
        return DPIClassification.TCP_16_20, "Обрыв на границе 16-20 КБ"
    return None


def _tls_timeout_while_host_alive(facts: TargetFacts, _baseline: NetworkBaseline):
    """:443 молчит, но сам хост отвечает — почерк DPI, роняющего соединение.

    Достижимость проверяется по HTTP :80 этой же цели, а не по ICMP: CDN штатно
    не отвечают на ping, и раньше это давало ложную уверенность в обе стороны.
    """
    probes = [test for test in facts.web if test.test_type != TestType.ISP_PAGE]
    if not probes or any(test.status == TestStatus.OK for test in probes):
        return None

    timed_out = all(
        test.status == TestStatus.TIMEOUT or test.error_code in TIMEOUT_CODES
        for test in probes
    )
    if timed_out and facts.host_reachable:
        return DPIClassification.TLS_DPI, "HTTPS не отвечает при живом хосте"
    return None


def _dns_fake(facts: TargetFacts, _baseline: NetworkBaseline):
    if any(test.error_code == "STUB" for test in facts.dns):
        return DPIClassification.DNS_FAKE, "DNS отдаёт подменённый адрес"
    return None


def _stun_block(facts: TargetFacts, baseline: NetworkBaseline):
    """STUN/UDP не проходит при рабочем TLS.

    Опорной точкой служит baseline: раньше правило требовало успешной HTTPS-пробы
    у той же цели, а у STUN-целей веб-проб нет вовсе — правило не срабатывало
    никогда.
    """
    if not facts.stun or any(test.status == TestStatus.OK for test in facts.stun):
        return None
    if facts.web_ok or baseline.tls_ok:
        return DPIClassification.STUN_BLOCK, "STUN/UDP заблокирован при рабочем HTTPS"
    return None


def _full_block(facts: TargetFacts, baseline: NetworkBaseline):
    """Все каналы цели легли при живой сети и разрешённом имени.

    Требования жёсткие намеренно: именно это правило раньше срабатывало на
    несуществующих хостах и красило весь отчёт.
    """
    if not baseline.probed or not baseline.internet_ok:
        return None
    if len(facts.web) < MIN_WEB_PROBES_FOR_FULL_BLOCK:
        return None
    if facts.web_ok:
        return None
    return DPIClassification.FULL_BLOCK, "Все протоколы недоступны"
