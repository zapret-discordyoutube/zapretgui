"""Иконки DNS-профилей Hosts: у каждого провайдера свой значок и цвет.

Под названием DNS-сервиса выбор профиля — ряд таких иконок; иконку проще
запомнить, чем цветную точку, а полное имя всегда видно в подсказке.
"""

from __future__ import annotations

_PROFILE_ICONS: dict[str, tuple[str, str]] = {
    "xbox_dns": ("fa5b.xbox", "#107C10"),
    "xbox_dns_old": ("fa5s.gamepad", "#7A9A01"),
    "comss_dns": ("fa5s.shield-alt", "#2F80ED"),
    "malw_dns": ("fa5s.bug", "#E5484D"),
    "malw_dns_v2": ("fa5s.spider", "#C2410C"),
    "astracat": ("fa5s.cat", "#F59E0B"),
    "geohide": ("fa5s.globe-europe", "#8B5CF6"),
}

# Для профиля, которого ещё нет в словаре: кружок цвета по его номеру.
_FALLBACK_ICON = "fa5s.circle"
_FALLBACK_COLORS = ("#0EA5E9", "#14B8A6", "#EC4899", "#84CC16", "#6366F1", "#F97316")


def profile_icon(profile_id: str, position: int) -> tuple[str, str]:
    """(имя значка qtawesome, цвет) для профиля; position — его номер в каталоге."""
    known = _PROFILE_ICONS.get(str(profile_id or ""))
    if known is not None:
        return known
    return _FALLBACK_ICON, _FALLBACK_COLORS[max(0, int(position)) % len(_FALLBACK_COLORS)]


__all__ = ["profile_icon"]
