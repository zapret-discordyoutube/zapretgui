"""Исходы всех целей → вердикт отчёта.

Прежняя агрегация брала худшую сигнатуру среди всех целей, причём «Полная
блокировка» стояла первой в приоритете. Одна недоступная цель из двадцати
красила весь отчёт. Здесь сигнатура попадает в вердикт только по кворуму, а
недостоверные цели не входят даже в знаменатель.
"""

from __future__ import annotations

from blockcheck.models import (
    DPIClassification,
    NetworkBaseline,
    ReportVerdict,
    SignatureSummary,
    TargetOutcome,
    TargetResult,
    VerdictCode,
)

__all__ = [
    "FULL_BLOCK_MIN_SHARE",
    "FULL_BLOCK_MIN_TARGETS",
    "QUORUM_MIN_SHARE",
    "QUORUM_MIN_TARGETS",
    "SIGNATURE_PRIORITY",
    "build_report_verdict",
]


# Сигнатура попадает в вердикт при любом из условий.
QUORUM_MIN_TARGETS = 2
QUORUM_MIN_SHARE = 0.3

# «Полная блокировка» — единственный вердикт, который обесценивает весь отчёт,
# поэтому порог для него отдельный и заведомо высокий.
FULL_BLOCK_MIN_TARGETS = 3
FULL_BLOCK_MIN_SHARE = 0.8

# От самой тяжёлой сигнатуры к самой лёгкой — определяет заголовок отчёта.
SIGNATURE_PRIORITY: tuple[DPIClassification, ...] = (
    DPIClassification.FULL_BLOCK,
    DPIClassification.TLS_MITM,
    DPIClassification.ISP_PAGE,
    DPIClassification.TLS_DPI,
    DPIClassification.HTTP_INJECT,
    DPIClassification.DNS_FAKE,
    DPIClassification.TCP_16_20,
    DPIClassification.TCP_RESET,
    DPIClassification.STUN_BLOCK,
)


def build_report_verdict(
    targets: list[TargetResult],
    baseline: NetworkBaseline,
) -> ReportVerdict:
    """Собирает итоговый вердикт по уже классифицированным целям."""
    counted = [target for target in targets if not target.informational]
    valid = [target for target in counted if target.outcome != TargetOutcome.INCONCLUSIVE]
    inconclusive = len(counted) - len(valid)
    blocked = [target for target in valid if target.outcome == TargetOutcome.BLOCKED]

    if baseline.probed and not baseline.internet_ok:
        return ReportVerdict(
            code=VerdictCode.NO_INTERNET,
            inconclusive_targets=inconclusive,
            detail=baseline.detail or "Контрольные хосты недоступны — выводы невозможны",
        )

    if not valid:
        return ReportVerdict(
            code=VerdictCode.UNRELIABLE,
            inconclusive_targets=inconclusive,
            detail="Ни одну цель не удалось проверить достоверно",
        )

    summaries = _group_by_signature(blocked, denominator=len(valid))
    confirmed: list[SignatureSummary] = []
    isolated: list[SignatureSummary] = []
    for summary in summaries:
        target_list = confirmed if _passes_quorum(summary, len(valid)) else isolated
        target_list.append(summary)

    headline = _headline(confirmed)
    code = VerdictCode.SIGNATURES if confirmed else VerdictCode.CLEAN

    return ReportVerdict(
        code=code,
        headline=headline,
        confirmed=confirmed,
        isolated=isolated,
        valid_targets=len(valid),
        blocked_targets=len(blocked),
        inconclusive_targets=inconclusive,
        detail=_detail(code, confirmed, isolated),
    )


def _group_by_signature(
    blocked: list[TargetResult],
    *,
    denominator: int,
) -> list[SignatureSummary]:
    grouped: dict[DPIClassification, list[str]] = {}
    for target in blocked:
        if target.classification == DPIClassification.NONE:
            continue
        grouped.setdefault(target.classification, []).append(target.name)

    summaries = [
        SignatureSummary(
            classification=classification,
            targets=sorted(names),
            share=len(names) / denominator if denominator else 0.0,
        )
        for classification, names in grouped.items()
    ]
    summaries.sort(key=_priority_index)
    return summaries


def _passes_quorum(summary: SignatureSummary, denominator: int) -> bool:
    hits = len(summary.targets)
    if summary.classification == DPIClassification.FULL_BLOCK:
        return hits >= FULL_BLOCK_MIN_TARGETS and summary.share >= FULL_BLOCK_MIN_SHARE
    if denominator == 1:
        # Единственная проверенная цель — её результат и есть весь отчёт.
        return hits >= 1
    return hits >= QUORUM_MIN_TARGETS or summary.share >= QUORUM_MIN_SHARE


def _priority_index(summary: SignatureSummary) -> int:
    try:
        return SIGNATURE_PRIORITY.index(summary.classification)
    except ValueError:
        return len(SIGNATURE_PRIORITY)


def _headline(confirmed: list[SignatureSummary]) -> DPIClassification | None:
    if not confirmed:
        return None
    return min(confirmed, key=_priority_index).classification


def _detail(
    code: VerdictCode,
    confirmed: list[SignatureSummary],
    isolated: list[SignatureSummary],
) -> str:
    parts: list[str] = []
    if code == VerdictCode.CLEAN:
        parts.append(
            "Массовых блокировок не обнаружено" if isolated
            else "Блокировок не обнаружено"
        )
    for summary in confirmed:
        parts.append(f"{summary.classification.value}: {len(summary.targets)} целей")
    if isolated:
        names = sorted({name for summary in isolated for name in summary.targets})
        shown = ", ".join(names[:5])
        if len(names) > 5:
            shown += f" (+{len(names) - 5})"
        parts.append(f"Единичные срабатывания: {shown}")
    return "; ".join(parts)
