"""Русские подписи и цвета для результатов BlockCheck — единственная копия.

Раньше словари ``DPI_LABELS_RU`` и ``DPI_BADGE_COLORS`` были продублированы
один в один в ``summary_content`` и ``page_results_workflow``: бейдж и колонка
таблицы могли разъехаться при правке одной из копий.
"""

from __future__ import annotations

__all__ = [
    "DPI_BADGE_COLORS",
    "DPI_LABELS_RU",
    "INCONCLUSIVE_LABELS_RU",
    "NEUTRAL_COLOR",
    "VERDICT_COLORS",
    "VERDICT_LABELS_RU",
    "badge_colors",
]


# (передний план, фон) — фон используется в тёмной теме.
DPI_BADGE_COLORS: dict[str, tuple[str, str]] = {
    "none": ("#52c477", "#1a3a24"),
    "dns_fake": ("#e0a854", "#3a2e1a"),
    "http_inject": ("#e07854", "#3a221a"),
    "isp_page": ("#e05454", "#3a1a1a"),
    "tls_dpi": ("#e05454", "#3a1a1a"),
    "tls_mitm": ("#e05454", "#3a1a1a"),
    "tcp_reset": ("#e07854", "#3a221a"),
    "tcp_16_20": ("#e0a854", "#3a2e1a"),
    "stun_block": ("#e0a854", "#3a2e1a"),
    "full_block": ("#e05454", "#3a1a1a"),
}

NEUTRAL_COLOR: tuple[str, str] = ("#9aa0a6", "#2a2d31")

DPI_LABELS_RU: dict[str, str] = {
    "none": "DPI не обнаружен",
    "dns_fake": "DNS подмена",
    "http_inject": "HTTP инъекция",
    "isp_page": "Страница-заглушка ISP",
    "tls_dpi": "TLS DPI (RST/EOF)",
    "tls_mitm": "TLS MITM прокси",
    "tcp_reset": "TCP RST",
    "tcp_16_20": "TCP блок 16-20KB",
    "stun_block": "STUN/UDP блокировка",
    "full_block": "Полная блокировка",
}

# Причины, по которым цель не даёт вывода. Показываются вместо сигнатуры и
# намеренно не окрашены в тревожный цвет: это отсутствие данных, а не блокировка.
INCONCLUSIVE_LABELS_RU: dict[str, str] = {
    "host_not_resolved": "Хост не существует",
    "no_baseline": "Нет связи",
    "unsupported_only": "Протокол не поддерживается",
    "budget_exceeded": "Не успели проверить",
    "not_probed": "Не проверялось",
    "none": "Нет данных",
}

VERDICT_LABELS_RU: dict[str, str] = {
    "clean": "Блокировок не обнаружено",
    "partial": "Заблокирована часть ресурсов",
    "no_internet": "Нет связи — проверка недостоверна",
    "unreliable": "Проверка недостоверна",
}

VERDICT_COLORS: dict[str, tuple[str, str]] = {
    "clean": ("#52c477", "#1a3a24"),
    "partial": ("#e0a854", "#3a2e1a"),
    "no_internet": NEUTRAL_COLOR,
    "unreliable": NEUTRAL_COLOR,
}


def badge_colors(key: str, *, is_dark: bool) -> tuple[str, str]:
    """Цвет текста и фона бейджа для ключа классификации или вердикта."""
    foreground, background = (
        DPI_BADGE_COLORS.get(key) or VERDICT_COLORS.get(key) or NEUTRAL_COLOR
    )
    return "#ffffff", (background if is_dark else foreground)
