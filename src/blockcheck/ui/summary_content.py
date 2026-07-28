"""Helper'ы содержимого DPI summary для Blockcheck page.

Модуль ничего не решает: вердикт уже посчитан движком
(:mod:`blockcheck.verdict`), здесь он только превращается в текст. Раньше
итоговый бейдж считался прямо тут — по принципу «худшая сигнатура среди всех
целей», из-за чего одна недоступная цель красила весь отчёт.
"""

from __future__ import annotations

from dataclasses import dataclass

from blockcheck.ui.dpi_labels import (
    DPI_BADGE_COLORS,
    DPI_LABELS_RU,
    VERDICT_LABELS_RU,
    badge_colors,
)


__all__ = [
    "DPI_BADGE_COLORS",
    "DPI_LABELS_RU",
    "BlockcheckDpiSummaryContent",
    "build_dpi_summary_content",
    "generate_recommendations",
]


# Что советовать по каждой подтверждённой сигнатуре.
_RECOMMENDATIONS: dict[str, str] = {
    "dns_fake": "DNS подменяется — используйте DoH/DoT или шифрованный DNS",
    "tls_dpi": "TLS DPI обнаружен — включите обход DPI (zapret)",
    "tls_mitm": "MITM прокси — проверьте сертификаты и настройки VPN/прокси",
    "isp_page": "Страница-заглушка провайдера — используйте HTTPS и обход DPI",
    "http_inject": "HTTP инъекция провайдера — используйте HTTPS и обход DPI",
    "tcp_16_20": "TCP блок 16-20KB — включите фрагментацию пакетов",
    "tcp_reset": "TCP RST — включите обход DPI (zapret)",
    "stun_block": "STUN/UDP заблокирован — голосовые звонки могут не работать",
    "full_block": "Полная блокировка — попробуйте VPN или прокси",
}


@dataclass(slots=True)
class BlockcheckDpiSummaryContent:
    badge_label: str
    badge_fg: str
    badge_bg: str
    detail_text: str
    dns_summary_text: str
    recommendation_text: str


def _verdict_key(report) -> str:
    """Ключ для подписи и цвета бейджа."""
    verdict = report.verdict
    if verdict.headline is not None:
        return verdict.headline.value
    return verdict.code.value


def _badge_label(report) -> str:
    key = _verdict_key(report)
    return VERDICT_LABELS_RU.get(key) or DPI_LABELS_RU.get(key, key)


def generate_recommendations(report) -> str:
    """Советы по подтверждённым сигнатурам."""
    from blockcheck.models import TestStatus, TestType, VerdictCode

    verdict = report.verdict

    if verdict.code == VerdictCode.NO_INTERNET:
        return (
            "• Контрольные хосты недоступны — проверьте подключение к сети, "
            "затем повторите проверку"
        )
    if verdict.code == VerdictCode.UNRELIABLE:
        return (
            "• Ни одну цель не удалось проверить достоверно — результатам "
            "доверять нельзя, повторите проверку"
        )

    recommendations = [
        _RECOMMENDATIONS[summary.classification.value]
        for summary in verdict.confirmed
        if summary.classification.value in _RECOMMENDATIONS
        and not (
            summary.classification.value == "full_block"
            and verdict.code == VerdictCode.PARTIAL
        )
    ]
    if verdict.code == VerdictCode.PARTIAL:
        affected = sorted({name for item in verdict.confirmed for name in item.targets})
        shown = ", ".join(affected[:4])
        if len(affected) > 4:
            shown += f" (+{len(affected) - 4})"
        recommendations.append(
            f"Недоступны отдельные ресурсы ({shown}) — включите обход DPI (zapret) "
            "и повторите проверку"
        )

    tls12_fail_13_ok = False
    for target in report.targets:
        tls12 = [test for test in target.tests if test.test_type == TestType.TLS_12]
        tls13 = [test for test in target.tests if test.test_type == TestType.TLS_13]
        if (
            tls12 and tls13
            and tls12[0].status != TestStatus.OK
            and tls13[0].status == TestStatus.OK
        ):
            tls12_fail_13_ok = True
            break
    if tls12_fail_13_ok:
        recommendations.append(
            "TLS 1.2 блокируется (DPI видит SNI), но TLS 1.3 работает — "
            "сайты доступны через современные браузеры"
        )

    if verdict.isolated:
        names = sorted({name for item in verdict.isolated for name in item.targets})
        shown = ", ".join(names[:3])
        if len(names) > 3:
            shown += f" (+{len(names) - 3})"
        recommendations.append(
            f"Единичные срабатывания ({shown}) не образуют картину блокировки — "
            "возможны проблемы самих сервисов"
        )

    if not recommendations:
        recommendations.append("Блокировки не обнаружены — всё работает нормально")

    return "\n".join(f"• {item}" for item in recommendations)


def build_dpi_summary_content(*, report, is_dark: bool, no_dpi_text: str) -> BlockcheckDpiSummaryContent:
    badge_fg, badge_bg = badge_colors(_verdict_key(report), is_dark=is_dark)

    return BlockcheckDpiSummaryContent(
        badge_label=_badge_label(report),
        badge_fg=badge_fg,
        badge_bg=badge_bg,
        detail_text=_build_detail_text(report, no_dpi_text),
        dns_summary_text=_build_dns_summary(report),
        recommendation_text=generate_recommendations(report),
    )


def _build_detail_text(report, no_dpi_text: str) -> str:
    from blockcheck.models import TargetOutcome

    verdict = report.verdict
    lines: list[str] = []

    for summary in verdict.confirmed:
        names = ", ".join(summary.targets[:4])
        if len(summary.targets) > 4:
            names += f" (+{len(summary.targets) - 4})"
        label = _signature_label(summary, verdict)
        lines.append(f"{label}: {len(summary.targets)} из {verdict.valid_targets} — {names}")

    for summary in verdict.isolated:
        label = _signature_label(summary, verdict)
        lines.append(f"{label} (единично): {', '.join(summary.targets[:4])}")

    if verdict.valid_targets:
        lines.append(
            f"Проверено достоверно: {verdict.valid_targets}, "
            f"из них с блокировкой: {verdict.blocked_targets}"
        )

    if verdict.inconclusive_targets:
        skipped = [
            target.name for target in report.targets
            if target.outcome == TargetOutcome.INCONCLUSIVE and not target.informational
        ]
        shown = ", ".join(skipped[:4])
        if len(skipped) > 4:
            shown += f" (+{len(skipped) - 4})"
        lines.append(f"Без вывода ({verdict.inconclusive_targets}): {shown}")

    if report.baseline.probed:
        lines.append(f"Сеть: {report.baseline.detail}")

    return "\n".join(lines) if lines else no_dpi_text


def _signature_label(summary, verdict) -> str:
    """Подпись сигнатуры в деталях отчёта.

    «Полная блокировка» без сетевого масштаба описывает конкретные ресурсы, а не
    сеть, — иначе детали противоречили бы заголовку.
    """
    from blockcheck.models import DPIClassification

    if (
        summary.classification == DPIClassification.FULL_BLOCK
        and verdict.headline != DPIClassification.FULL_BLOCK
    ):
        return "Полностью недоступны"
    return DPI_LABELS_RU.get(summary.classification.value, summary.classification.value)


def _build_dns_summary(report) -> str:
    from blockcheck.models import DnsVerdict

    if not report.dns_integrity:
        return ""

    ok = [item for item in report.dns_integrity if item.verdict == DnsVerdict.OK]
    fake = [item for item in report.dns_integrity if item.verdict == DnsVerdict.FAKE]
    unknown = [
        item for item in report.dns_integrity if item.verdict == DnsVerdict.INCONCLUSIVE
    ]

    text = f"DNS: {len(ok)}/{len(report.dns_integrity)} в порядке"
    if fake:
        text += f"\nDNS подмена: {', '.join(item.domain for item in fake[:5])}"
        if len(fake) > 5:
            text += f" (+{len(fake) - 5})"
    if unknown:
        text += f"\nБез вывода: {len(unknown)}"
    return text
