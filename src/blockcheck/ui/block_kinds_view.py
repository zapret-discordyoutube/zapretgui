"""Картина блокировок одним взглядом: цветная полоса и плитки по видам.

Итог BlockCheck отвечает на вопрос «что не так». Здесь — ответ на вопрос
«чем именно мешают»: сколько сайтов открывается, сколько закрыто по адресу
(IP), сколько режут по имени сайта (SNI) и у скольких загрузка обрывается
после 16 КБ. У каждого вида свой цвет — он один и тот же в полосе, на плитке,
в заголовке группы проблем и в таблице сайтов.

- ``site_groups`` — чистый подсчёт по отчёту (без окон, проверяется тестом);
- ``KindBar`` — полоса из цветных долей, заполняется слева направо;
- ``KindTile`` — плитка вида: число (досчитывает от нуля), название и сайты;
- ``KindsOverview`` — полоса и плитки вместе;
- ``KindPill`` — цветная метка вида для заголовка группы.

Анимация идёт меньше секунды после показа итога и подчиняется переключателю
«живых анимаций».
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QEasingCurve, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget
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
    KIND_UNCLEAR,
    KIND_VOICE,
    kind_info,
)
from ui.accessibility import set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.fluent_widgets import set_tooltip
from ui.theme_refresh import ThemeRefreshBinding

# Сайт открывается — не вид блокировки, но своя доля в полосе и своя плитка.
GROUP_OPEN = "open"
# Проверка не дала ответа.
GROUP_UNKNOWN = "unknown"

REVEAL_MS = 760
TILE_STEP_MS = 80

# Цвета видов: (тёмная тема, светлая тема). Три главных вида — красный,
# оранжевый и фиолетовый — подобраны так, чтобы различаться и без названий.
_COLORS: dict[str, tuple[str, str]] = {
    GROUP_OPEN: ("#6ccb5f", "#0f7b0f"),
    KIND_IP: ("#ff5c5c", "#c42b1c"),
    KIND_SNI: ("#ffa033", "#a85d00"),
    KIND_CUT: ("#b68cff", "#6a3fc8"),
    KIND_STUB: ("#ff7eb6", "#b3266e"),
    KIND_CERT: ("#f2c94c", "#7a5c00"),
    KIND_DNS: ("#62b5ff", "#0f5fb5"),
    KIND_QUIC: ("#4fd1d9", "#00707a"),
    KIND_VOICE: ("#4fd1d9", "#00707a"),
    KIND_NETWORK: ("#ff5c5c", "#c42b1c"),
    KIND_SYSTEM: ("#ffa033", "#a85d00"),
    KIND_UNCLEAR: ("#a3a8b3", "#5f6470"),
    KIND_OTHER: ("#a3a8b3", "#5f6470"),
    GROUP_UNKNOWN: ("#a3a8b3", "#5f6470"),
}

_OPEN_TITLE = "Открываются"
_UNKNOWN_TITLE = "Не удалось проверить"
_OPEN_ABOUT = "Эти сайты открылись так же, как открылись бы в браузере."
_UNKNOWN_ABOUT = "Проверка этих сайтов не дала ответа: не хватило времени или не удалось узнать адрес."
# Порядок долей и плиток: сначала хорошее, затем виды по тяжести.
_SITE_ORDER = (GROUP_OPEN, KIND_IP, KIND_SNI, KIND_CUT, KIND_STUB, KIND_CERT, KIND_UNCLEAR, KIND_OTHER, GROUP_UNKNOWN)


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


class KindBar(QWidget):
    """Полоса из цветных долей: какая часть сайтов открывается и чем мешают остальным."""

    HEIGHT = 10
    GAP = 3.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._groups: list[SiteGroup] = []
        self._reveal = 1.0
        self._is_light = False
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(REVEAL_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_value)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def groups(self) -> list[SiteGroup]:
        return list(self._groups)

    def set_groups(self, groups: list[SiteGroup], *, animate: bool = True) -> None:
        self._groups = [group for group in groups if group.count > 0]
        self._anim.stop()
        if animate and self._groups and are_live_animations_enabled():
            self._reveal = 0.0
            self._anim.start()
        else:
            self._reveal = 1.0
        self.update()

    def _on_value(self, value) -> None:
        self._reveal = float(value)
        self.update()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        self._is_light = _is_light(tokens)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        track = QColor(0, 0, 0, 22) if self._is_light else QColor(255, 255, 255, 20)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, radius, radius)
        total = sum(group.count for group in self._groups)
        if total:
            gaps = self.GAP * (len(self._groups) - 1)
            usable = max(0.0, rect.width() - gaps)
            # Всё, что правее границы заполнения, не рисуется: доли «выезжают» слева.
            painter.setClipRect(QRectF(rect.left(), rect.top(), rect.width() * self._reveal, rect.height()))
            x = rect.left()
            for group in self._groups:
                width = usable * group.count / total
                dark, light = _COLORS.get(group.key) or _COLORS[KIND_OTHER]
                painter.setBrush(QColor(light if self._is_light else dark))
                painter.drawRoundedRect(QRectF(x, rect.top(), width, rect.height()), radius, radius)
                x += width + self.GAP
        painter.end()


class KindTile(QWidget):
    """Плитка вида: крупное число, название и сайты, которых это касается."""

    WIDTH = 248
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
        except Exception:
            pass
        self._color = QColor(kind_color(self._group.key, tokens))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        fill = QColor(self._color)
        fill.setAlphaF(0.13)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 8, 8)

        number_font = QFont(self.font())
        number_font.setPixelSize(26)
        number_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(number_font)
        painter.setPen(self._color)
        number_width = 52
        painter.drawText(
            QRectF(rect.left() + 6, rect.top(), number_width, rect.height()),
            Qt.AlignmentFlag.AlignCenter,
            str(round(self._shown)),
        )

        left = rect.left() + 6 + number_width + 4
        width = rect.right() - left - 10
        title_font = QFont(self.font())
        title_font.setPixelSize(13)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(self._text)
        metrics = QFontMetrics(title_font)
        painter.drawText(
            QRectF(left, rect.top() + 10, width, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(self._group.title, Qt.TextElideMode.ElideRight, int(width)),
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
        self._tiles_host.updateGeometry()


class KindPill(QLabel):
    """Цветная метка вида блокировки — заголовок группы проблем."""

    def __init__(self, kind: str, parent=None) -> None:
        super().__init__(kind_info(kind).title, parent)
        self._kind = kind
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # Скругление в QSS работает, только пока радиус не больше половины высоты.
        self.setFixedHeight(22)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def kind(self) -> str:
        return self._kind

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        color = QColor(kind_color(self._kind, tokens))
        self.setStyleSheet(
            f"QLabel {{ color: {color.name()}; "
            f"background-color: rgba({color.red()}, {color.green()}, {color.blue()}, 0.16); "
            "border-radius: 10px; padding: 0px 10px; font-weight: 600; }"
        )


__all__ = [
    "GROUP_OPEN",
    "GROUP_UNKNOWN",
    "KindBar",
    "KindPill",
    "KindTile",
    "KindsOverview",
    "SiteGroup",
    "group_about",
    "group_title",
    "groups_state_text",
    "kind_color",
    "site_groups",
]
