from __future__ import annotations

from collections import OrderedDict

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from ui.theme import get_cached_qta_pixmap


_INITIALS_PREFIX = "profile-initials:"
_SIMPLE_PREFIX = "simple:"
# Свои значки из profile/ui/own_icons.py: тот же вид имени, что у simple.
_OWN_PREFIX = "own:"
_PROFILE_PIXMAP_CACHE_MAX = 256
_PROFILE_PIXMAP_CACHE: OrderedDict[tuple[str, str, str, int], QPixmap] = OrderedDict()

_simple_icons_bundle: dict[str, tuple[str, str]] | None = None


def _bundled_simple_icons() -> dict[str, tuple[str, str]]:
    """Возвращает сгенерированный бандл slug -> (primary_color, raw_svg).

    Бандл содержит только иконки из каталога profile/icons.py и импортируется
    за миллисекунды. Раньше здесь лениво импортировался simplepycons целиком
    (~3400 модулей, ~2.6с в GUI-потоке при первой отрисовке списка профилей).
    """
    global _simple_icons_bundle
    bundle = _simple_icons_bundle
    if bundle is None:
        try:
            from profile.ui.simple_icons_bundle import SIMPLE_ICON_SVGS

            bundle = dict(SIMPLE_ICON_SVGS)
        except Exception:
            bundle = {}
        _simple_icons_bundle = bundle
    return bundle


def _own_icons() -> dict[str, tuple[str, str]]:
    try:
        from profile.ui.own_icons import OWN_ICON_SVGS

        return OWN_ICON_SVGS
    except Exception:
        return {}


def profile_icon_pixmap(icon_name: str, *, color: str, size: int, theme_name: str = "") -> QPixmap:
    name = str(icon_name or "").strip()
    if name.startswith((_SIMPLE_PREFIX, _OWN_PREFIX)):
        kind = "own" if name.startswith(_OWN_PREFIX) else "simple"
        slug, fallback = _parse_simple_icon_name(name)
        pixmap = _cached_profile_pixmap(kind, slug, str(color or ""), size)
        if not pixmap.isNull():
            return pixmap
        return _cached_profile_pixmap(
            "initials",
            fallback or _initials_from_slug(slug),
            str(color or "#3B82F6"),
            size,
        )
    if name.startswith(_INITIALS_PREFIX):
        return _cached_profile_pixmap(
            "initials",
            name.removeprefix(_INITIALS_PREFIX).strip() or "P",
            str(color or "#3B82F6"),
            size,
        )
    return get_cached_qta_pixmap(name, color=color, size=size, theme_name=theme_name)


def _parse_simple_icon_name(icon_name: str) -> tuple[str, str]:
    payload = str(icon_name or "").removeprefix(_SIMPLE_PREFIX).removeprefix(_OWN_PREFIX)
    slug, _sep, fallback = payload.partition(":")
    return slug.strip(), fallback.strip()


def _cached_profile_pixmap(kind: str, value: str, color: str, size: int) -> QPixmap:
    safe_size = max(12, int(size or 18))
    cache_key = (str(kind or ""), str(value or ""), _valid_hex_color(color) or str(color or ""), safe_size)
    cached = _PROFILE_PIXMAP_CACHE.get(cache_key)
    if cached is not None and not cached.isNull():
        _PROFILE_PIXMAP_CACHE.move_to_end(cache_key)
        return QPixmap(cached)

    if cache_key[0] in ("simple", "own"):
        icons = _own_icons() if cache_key[0] == "own" else _bundled_simple_icons()
        pixmap = _simple_icon_pixmap(cache_key[1], color=cache_key[2], size=safe_size, icons=icons)
    elif cache_key[0] == "initials":
        pixmap = _initials_pixmap(cache_key[1], color=cache_key[2], size=safe_size)
    else:
        return QPixmap()

    if pixmap.isNull():
        return pixmap

    _PROFILE_PIXMAP_CACHE[cache_key] = QPixmap(pixmap)
    _PROFILE_PIXMAP_CACHE.move_to_end(cache_key)
    while len(_PROFILE_PIXMAP_CACHE) > _PROFILE_PIXMAP_CACHE_MAX:
        _PROFILE_PIXMAP_CACHE.popitem(last=False)

    return pixmap


def _simple_icon_pixmap(slug: str, *, color: str, size: int, icons: dict | None = None) -> QPixmap:
    svg = _simple_icon_svg(slug, color=color, icons=icons)
    if not svg:
        return QPixmap()

    safe_size = max(12, int(size or 18))
    renderer = QSvgRenderer(svg.encode("utf-8"))
    if not renderer.isValid():
        return QPixmap()

    canvas = QPixmap(safe_size, safe_size)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return canvas


def _simple_icon_svg(slug: str, *, color: str, icons: dict | None = None) -> str:
    clean_slug = str(slug or "").strip().lower().replace("-", "")
    if not clean_slug:
        return ""
    entry = (_bundled_simple_icons() if icons is None else icons).get(clean_slug)
    if entry is None:
        return ""
    primary_color_raw, raw_svg = entry
    if not raw_svg:
        return ""
    primary_color = _valid_hex_color(primary_color_raw)
    fill_color = _valid_hex_color(color) or primary_color or "#3B82F6"
    if "<svg" in raw_svg and "fill=" not in raw_svg.split(">", 1)[0]:
        raw_svg = raw_svg.replace("<svg", f'<svg fill="{fill_color}"', 1)
    return raw_svg


def _valid_hex_color(color: str) -> str:
    qcolor = QColor(str(color or ""))
    return qcolor.name() if qcolor.isValid() else ""


def _initials_pixmap(initials: str, *, color: str, size: int) -> QPixmap:
    safe_size = max(12, int(size or 18))
    canvas = QPixmap(safe_size, safe_size)
    canvas.fill(Qt.GlobalColor.transparent)

    bg = QColor(str(color or "#3B82F6"))
    if not bg.isValid():
        bg = QColor("#3B82F6")

    painter = QPainter(canvas)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(0, 0, safe_size, safe_size, max(4, safe_size // 4), max(4, safe_size // 4))

        font = QFont()
        font.setBold(True)
        font.setPixelSize(max(8, int(safe_size * 0.48)))
        painter.setFont(font)
        painter.setPen(QPen(QColor("#FFFFFF")))
        painter.drawText(canvas.rect(), Qt.AlignmentFlag.AlignCenter, str(initials or "P")[:2].upper())
    finally:
        painter.end()
    return canvas


def _initials_from_slug(slug: str) -> str:
    clean = "".join(ch for ch in str(slug or "") if ch.isalnum())
    return (clean[:2] or "P").upper()


__all__ = ["profile_icon_pixmap"]
