"""Картина блокировок одним взглядом: цветная полоса и плитки по видам.

Итог BlockCheck отвечает на вопрос «что не так». Здесь — ответ на вопрос
«чем именно мешают»: сколько сайтов открывается, сколько закрыто по адресу
(IP), сколько режут по имени сайта (SNI) и у скольких загрузка обрывается
после 16 КБ. У каждого вида свой цвет — он один и тот же в полосе, в точке на
плитке, в заголовке группы проблем и в таблице сайтов. Цвет — только метка:
сами плитки нейтральные, чтобы экран читался как сводка, а не как светофор.

- ``site_groups`` — чистый подсчёт по отчёту (без окон, проверяется тестом);
- ``KindBar`` — полоса из цветных долей (общая ``ui.widgets.share_bar``);
- ``KindTile`` — плитка вида: число (досчитывает от нуля), название и сайты;
- ``KindsOverview`` — полоса и плитки вместе; плитки делят ширину поровну.

Анимация идёт меньше секунды после показа итога и подчиняется переключателю
«живых анимаций».
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QEasingCurve, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import FlowLayout

from diagnostics.block_kind import (
    KIND_CERT,
    KIND_CUT,
    KIND_DNS,
    KIND_IP,
    KIND_NETWORK,
    KIND_OTHER,
    KIND_QUIC,
    KIND_SNI,
    KIND_STUB,
    KIND_SYSTEM,
    KIND_NO_CONNECT,
    KIND_UNCLEAR,
    KIND_VOICE,
    kind_info,
)
from ui.accessibility import set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.fluent_widgets import set_tooltip
from ui.theme_refresh import ThemeRefreshBinding
from ui.widgets.share_bar import REVEAL_MS, ShareBar
from ui.widgets.tone_group import paint_dot

# Сайт открывается — не вид блокировки, но своя доля в полосе и своя плитка.
GROUP_OPEN = "open"
# Проверка не дала ответа.
GROUP_UNKNOWN = "unknown"

TILE_STEP_MS = 80

# Цвета видов: (тёмная тема, светлая тема). Три главных вида — красный,
# оранжевый и фиолетовый — подобраны так, чтобы различаться и без названий.
# Тона приглушённые: цвет здесь метка, а не сигнал тревоги.
_COLORS: dict[str, tuple[str, str]] = {
    GROUP_OPEN: ("#5bb974", "#1a7f37"),
    KIND_IP: ("#e5645d", "#b3261e"),
    KIND_SNI: ("#d99a4e", "#955800"),
    KIND_CUT: ("#a48be0", "#6a3fc8"),
    KIND_STUB: ("#d17aa5", "#a3266a"),
    KIND_CERT: ("#cdb15a", "#765a00"),
    KIND_DNS: ("#6aa7de", "#0f5fb5"),
    KIND_QUIC: ("#5bbac0", "#00707a"),
    KIND_VOICE: ("#5bbac0", "#00707a"),
    KIND_NETWORK: ("#e5645d", "#b3261e"),
    KIND_SYSTEM: ("#d99a4e", "#955800"),
    KIND_UNCLEAR: ("#9aa0aa", "#5f6470"),
    KIND_NO_CONNECT: ("#9aa0aa", "#5f6470"),
    KIND_OTHER: ("#9aa0aa", "#5f6470"),
    GROUP_UNKNOWN: ("#9aa0aa", "#5f6470"),
}

_OPEN_TITLE = "Открываются"
_UNKNOWN_TITLE = "Не удалось проверить"
_OPEN_ABOUT = "Эти сайты открылись так же, как открылись бы в браузере."
_UNKNOWN_ABOUT = "Проверка этих сайтов не дала ответа: не хватило времени или не удалось узнать адрес."
# Порядок долей и плиток: сначала хорошее, затем виды по тяжести.
_SITE_ORDER = (GROUP_OPEN, KIND_IP, KIND_SNI, KIND_CUT, KIND_STUB, KIND_CERT, KIND_UNCLEAR, KIND_NO_CONNECT, KIND_OTHER, GROUP_UNKNOWN)


def _is_light(tokens=None) -> bool:
    if tokens is None:
        try:
            from ui.theme import get_theme_tokens

            tokens = get_theme_tokens()
        except Exception:
            return False
    return bool(getattr(tokens, "is_light", False))


def kind_color(kind: str, tokens=None) -> str:
    """Цвет вида блокировки для текущей темы."""
    dark, light = _COLORS.get(kind) or _COLORS[KIND_OTHER]
    return light if _is_light(tokens) else dark


def group_title(key: str) -> str:
    """Короткое название для плитки — те же слова, что в таблице сайтов."""
    if key == GROUP_OPEN:
        return _OPEN_TITLE
    if key == GROUP_UNKNOWN:
        return _UNKNOWN_TITLE
    short = kind_info(key).short
    return f"{short[:1].upper()}{short[1:]}"


def group_about(key: str) -> str:
    if key == GROUP_OPEN:
        return _OPEN_ABOUT
    if key == GROUP_UNKNOWN:
        return _UNKNOWN_ABOUT
    info = kind_info(key)
    return f"{info.about}\n{info.cure}".strip()


@dataclass(frozen=True, slots=True)
class SiteGroup:
    key: str
    title: str
    names: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.names)


def _service_group(service: dict) -> str:
    level = str(service.get("level") or "unknown")
    targets = list(service.get("targets") or ())
    kind = str(service.get("kind") or "")
    if level in ("fail", "warn") and kind:
        return kind
    if level == "ok" or (level == "warn" and targets and all(item.get("ok") for item in targets)):
        # Подмена DNS при открывающемся сайте — не блокировка сайта.
        return GROUP_OPEN
    if level in ("fail", "warn"):
        return KIND_OTHER
    return GROUP_UNKNOWN


def site_groups(report: dict) -> list[SiteGroup]:
    """Сайты отчёта по видам блокировки. Контрольные сайты не считаются: их не блокируют."""
    names: dict[str, list[str]] = {}
    for service in report.get("services") or ():
        if service.get("control"):
            continue
        names.setdefault(_service_group(service), []).append(str(service.get("label") or service.get("key") or ""))
    order = {key: index for index, key in enumerate(_SITE_ORDER)}
    keys = sorted(names, key=lambda key: order.get(key, len(order)))
    return [SiteGroup(key, group_title(key), tuple(names[key])) for key in keys]


def groups_state_text(groups: list[SiteGroup]) -> str:
    total = sum(group.count for group in groups)
    if not total:
        return "Виды блокировок: пока нет результатов"
    parts = ", ".join(f"{group.title.lower()} — {group.count}" for group in groups)
    return f"Сайтов проверено: {total}. {parts[:1].upper()}{parts[1:]}"


class KindBar(ShareBar):
    """Полоса из цветных долей: какая часть сайтов открывается и чем мешают остальным."""

    def __init__(self, parent=None) -> None:
        super().__init__(kind_color, parent)
        self._groups: list[SiteGroup] = []

    def groups(self) -> list[SiteGroup]:
        return list(self._groups)

    def set_groups(self, groups: list[SiteGroup], *, animate: bool = True) -> None:
        self._groups = [group for group in groups if group.count > 0]
        self.set_segments([(group.key, group.count) for group in self._groups], animate=animate)


class KindTile(QWidget):
    """Плитка вида: крупное число, название и сайты, которых это касается."""

    WIDTH = 248
    MIN_WIDTH = 200
    HEIGHT = 60

    def __init__(self, group: SiteGroup, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self._group = group
        self._shown = float(group.count)
        self._color = QColor(_COLORS.get(group.key, _COLORS[KIND_OTHER])[0])
        self._text = QColor("#f2f2f2")
        self._muted = QColor("#a3a8b3")
        self._surface = QColor(255, 255, 255, 10)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(float(group.count))
        self._anim.setDuration(REVEAL_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_value)
        self._delay = QTimer(self)
        self._delay.setSingleShot(True)
        self._delay.timeout.connect(self._anim.start)
        set_tooltip(self, f"{group.title}: {', '.join(group.names)}\n{group_about(group.key)}".strip())
        set_state_text(self, f"{group.title}: {group.count}. {', '.join(group.names)}")
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def group(self) -> SiteGroup:
        return self._group

    def shown_count(self) -> int:
        return round(self._shown)

    def play(self, delay_ms: int = 0) -> None:
        """Число досчитывает от нуля до своего значения."""
        if not are_live_animations_enabled() or self._group.count <= 0:
            return
        self._shown = 0.0
        self.update()
        self._delay.start(max(0, int(delay_ms)))

    def _on_value(self, value) -> None:
        self._shown = float(value)
        self.update()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        try:
            from ui.theme import get_theme_tokens, to_qcolor

            tokens = tokens or get_theme_tokens()
            self._text = to_qcolor(tokens.fg, "#f2f2f2")
            self._muted = to_qcolor(tokens.fg_muted, "#a3a8b3")
            self._surface = to_qcolor(tokens.surface_bg, "#0affffff")
        except Exception:
            pass
        self._color = QColor(kind_color(self._group.key, tokens))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._surface)
        painter.drawRoundedRect(rect, 6, 6)

        number_font = QFont(self.font())
        number_font.setPixelSize(24)
        number_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(number_font)
        painter.setPen(self._text)
        number_width = 52
        painter.drawText(
            QRectF(rect.left() + 6, rect.top(), number_width, rect.height()),
            Qt.AlignmentFlag.AlignCenter,
            str(round(self._shown)),
        )

        left = rect.left() + 6 + number_width + 4
        # Цветная точка перед названием — та же, что у группы проблем ниже.
        paint_dot(painter, QRectF(left, rect.top() + 15, 8, 8), self._color)
        painter.setPen(self._text)
        title_left = left + 8 + 7
        width = rect.right() - left - 10
        title_font = QFont(self.font())
        title_font.setPixelSize(13)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        metrics = QFontMetrics(title_font)
        painter.drawText(
            QRectF(title_left, rect.top() + 10, width - 15, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(self._group.title, Qt.TextElideMode.ElideRight, int(width - 15)),
        )
        names_font = QFont(self.font())
        names_font.setPixelSize(12)
        painter.setFont(names_font)
        painter.setPen(self._muted)
        metrics = QFontMetrics(names_font)
        painter.drawText(
            QRectF(left, rect.top() + 31, width, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(", ".join(self._group.names), Qt.TextElideMode.ElideRight, int(width)),
        )
        painter.end()


class KindsOverview(QWidget):
    """Полоса и плитки: сколько сайтов открывается и чем мешают остальным."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(10)
        self.bar = KindBar(self)
        layout.addWidget(self.bar)
        self._tiles_host = QWidget(self)
        self._tiles_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._flow = FlowLayout(self._tiles_host, needAni=False)
        self._flow.setContentsMargins(0, 0, 0, 0)
        self._flow.setHorizontalSpacing(8)
        self._flow.setVerticalSpacing(8)
        layout.addWidget(self._tiles_host)
        self._tiles: list[KindTile] = []
        set_state_text(self, groups_state_text([]))

    def tiles(self) -> list[KindTile]:
        return list(self._tiles)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_tiles()

    def _fit_tiles(self) -> None:
        """Плитки делят строку поровну и встают вровень с краями полосы."""
        # Запас в пару точек: раскладка переносит плитку, если та встаёт край в край.
        available = self.contentsRect().width() - 2
        if not self._tiles or available <= 0:
            return
        gap = self._flow.horizontalSpacing()
        columns = max(1, min(len(self._tiles), (available + gap) // (KindTile.MIN_WIDTH + gap)))
        width = (available - gap * (columns - 1)) // columns
        if self._tiles[0].width() == width:
            return
        for tile in self._tiles:
            tile.setFixedWidth(width)
        self._tiles_host.updateGeometry()

    def clear(self) -> None:
        self._flow.takeAllWidgets()
        for tile in self._tiles:
            tile.hide()
            tile.deleteLater()
        self._tiles = []
        self.bar.set_groups([], animate=False)
        set_state_text(self, groups_state_text([]))

    def show_groups(self, groups: list[SiteGroup], *, animate: bool = True) -> None:
        self.clear()
        groups = [group for group in groups if group.count > 0]
        for order, group in enumerate(groups):
            tile = KindTile(group, self._tiles_host)
            self._flow.addWidget(tile)
            tile.show()
            self._tiles.append(tile)
            if animate:
                tile.play(order * TILE_STEP_MS)
        self.bar.set_groups(groups, animate=animate)
        set_state_text(self, groups_state_text(groups))
        self._tiles_host.setVisible(bool(self._tiles))
        self._fit_tiles()
        self._tiles_host.updateGeometry()


__all__ = [
    "GROUP_OPEN",
    "GROUP_UNKNOWN",
    "KindBar",
    "KindTile",
    "KindsOverview",
    "SiteGroup",
    "group_about",
    "group_title",
    "groups_state_text",
    "kind_color",
    "site_groups",
]
