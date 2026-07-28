"""BlockCheck data models — pure Python, no Qt dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class TestStatus(Enum):
    OK = "ok"
    FAIL = "fail"
    TIMEOUT = "timeout"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


class DPIClassification(Enum):
    NONE = "none"
    DNS_FAKE = "dns_fake"
    HTTP_INJECT = "http_inject"
    ISP_PAGE = "isp_page"
    TLS_DPI = "tls_dpi"
    TLS_MITM = "tls_mitm"
    TCP_RESET = "tcp_reset"
    TCP_16_20 = "tcp_16_20"
    STUN_BLOCK = "stun_block"
    FULL_BLOCK = "full_block"


class TargetOutcome(Enum):
    """Исход проверки одной цели.

    Ключевое разделение, которого раньше не было: «не удалось проверить» — это не
    «заблокировано». Несуществующий хост, отсутствие связи и неподдерживаемый
    протокол дают INCONCLUSIVE и не участвуют в вердикте отчёта.
    """

    OK = "ok"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"


class InconclusiveReason(Enum):
    NONE = "none"
    HOST_NOT_RESOLVED = "host_not_resolved"
    NO_BASELINE = "no_baseline"
    UNSUPPORTED_ONLY = "unsupported_only"
    BUDGET_EXCEEDED = "budget_exceeded"
    NOT_PROBED = "not_probed"


class VerdictCode(Enum):
    """Итог отчёта целиком."""

    CLEAN = "clean"              # блокировок не обнаружено
    SIGNATURES = "signatures"    # обнаружена сигнатура DPI, общая для многих целей
    PARTIAL = "partial"          # недоступна часть ресурсов, единой сигнатуры нет
    NO_INTERNET = "no_internet"  # контрольная группа недоступна — выводов не делаем
    UNRELIABLE = "unreliable"    # ни одной цели не удалось проверить достоверно


class TestType(Enum):
    HTTP = "http"
    TLS_12 = "tls12"
    TLS_13 = "tls13"
    STUN = "stun"
    PING = "ping"
    DNS_UDP = "dns_udp"
    DNS_DOH = "dns_doh"
    ISP_PAGE = "isp_page"
    TCP_16_20 = "tcp_16_20"
    PREFLIGHT_DNS = "preflight_dns"
    PREFLIGHT_TCP = "preflight_tcp"
    PREFLIGHT_HTTP = "preflight_http"
    PREFLIGHT_PING = "preflight_ping"


@dataclass
class SingleTestResult:
    target_name: str
    test_type: TestType
    status: TestStatus
    time_ms: float | None = None
    error_code: str | None = None
    detail: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class TargetResult:
    name: str
    value: str
    tests: list[SingleTestResult] = field(default_factory=list)
    classification: DPIClassification = DPIClassification.NONE
    classification_detail: str = ""
    outcome: TargetOutcome = TargetOutcome.INCONCLUSIVE
    inconclusive_reason: InconclusiveReason = InconclusiveReason.NOT_PROBED
    # Доля целей, попавших в знаменатель вердикта, считается только по
    # informational=False: эфемерные и справочные цели не влияют на итог.
    informational: bool = False


class DnsVerdict(Enum):
    """Итог DNS-проверки одного домена.

    Расхождение IP между системным резолвером и DoH само по себе уликой не
    является: anycast и geo-DNS крупных CDN штатно отдают разные адреса разным
    резолверам. Решает валидность сертификата на полученном адресе.
    """

    OK = "ok"
    FAKE = "fake"
    INCONCLUSIVE = "inconclusive"


@dataclass
class DNSIntegrityResult:
    domain: str
    udp_ips: list[str] = field(default_factory=list)
    doh_ips: list[str] = field(default_factory=list)
    is_comparable: bool = False
    is_consistent: bool = True
    is_stub: bool = False
    stub_ip: str | None = None
    verdict: DnsVerdict = DnsVerdict.INCONCLUSIVE
    evidence: str = ""


class PreflightVerdict(Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


@dataclass
class PreflightResult:
    domain: str
    resolved_ips: list[str] = field(default_factory=list)
    is_block_ip: bool = False
    block_ip_detail: str = ""
    ping: SingleTestResult | None = None
    tcp_443: SingleTestResult | None = None
    dns_result: SingleTestResult | None = None
    http_check: SingleTestResult | None = None
    verdict: PreflightVerdict = PreflightVerdict.PASSED
    verdict_detail: str = ""


@dataclass
class NetworkBaseline:
    """Что вообще работает на этой машине — опорная точка для всех выводов.

    Без неё «все пробы упали» неотличимо от «интернета нет», а штатное молчание
    CDN на ICMP выглядит как блокировка.
    """

    probed: bool = False
    internet_ok: bool = False
    tls_ok: bool = False
    icmp_usable: bool = False
    ipv6_usable: bool = False
    http80_usable: bool = False
    doh_usable: bool = False
    control_hosts: list[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class SignatureSummary:
    """Сигнатура и цели, на которых она встретилась."""

    classification: DPIClassification
    targets: list[str] = field(default_factory=list)
    share: float = 0.0


@dataclass
class ReportVerdict:
    code: VerdictCode = VerdictCode.UNRELIABLE
    headline: DPIClassification | None = None
    confirmed: list[SignatureSummary] = field(default_factory=list)
    isolated: list[SignatureSummary] = field(default_factory=list)
    valid_targets: int = 0
    blocked_targets: int = 0
    inconclusive_targets: int = 0
    detail: str = ""


@dataclass
class BlockcheckReport:
    preflight: list[PreflightResult] = field(default_factory=list)
    targets: list[TargetResult] = field(default_factory=list)
    dns_integrity: list[DNSIntegrityResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    baseline: NetworkBaseline = field(default_factory=NetworkBaseline)
    verdict: ReportVerdict = field(default_factory=ReportVerdict)
    elapsed_seconds: float = 0.0
    cancelled: bool = False
