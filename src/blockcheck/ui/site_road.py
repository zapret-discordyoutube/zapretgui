"""Дорога до сайта картинкой: вы → DNS → сервер → TLS → сайт, и где на ней режут.

Карточка сайта говорит словами «По имени (SNI)», а здесь это видно: точка едет
от компьютера к сайту и останавливается на том шаге, где соединение обрывают.
Шаги до него зелёные, сам он красный, шаги за ним серые — до них не дошли.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget
from qfluentwidgets.common.font import getFont

from blockcheck.ui.result_cards_model import Card
from ui.accessibility import set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.theme import get_cached_qta_pixmap
from ui.theme_refresh import ThemeRefreshBinding

TRAVEL_MS = 1100


@dataclass(frozen=True, slots=True)
class RoadStage:
    title: str
    word: str
    state: str
    icon: str


def _mark(card: Card, label: str):
    return next((mark for mark in card.marks if mark.label == label), None)


def site_road(card: Card) -> tuple[tuple[RoadStage, ...], int]:
    """Шаги дороги до сайта и номер шага, на котором режут (``-1`` — нигде)."""
    dns = _mark(card, "DNS")
    tls = [mark for mark in (_mark(card, "TLS 1.2"), _mark(card, "TLS 1.3")) if mark is not None]
    stages = [RoadStage("Вы", "", "ok", "fa5s.desktop")]
    stages.append(RoadStage("DNS", dns.word if dns else "—", dns.state if dns else "unknown", "fa5s.exchange-alt"))

    reached = [mark for mark in card.marks if mark.label not in ("DNS", "QUIC") and mark.word not in ("нет связи", "—")]
    if card.kind == "ip" or (card.marks and not reached and card.level == "fail"):
        stages.append(RoadStage("Сервер", "адрес закрыт", "fail", "fa5s.server"))
    else:
        stages.append(RoadStage("Сервер", "отвечает" if reached or card.level == "ok" else "—", "ok" if reached or card.level == "ok" else "unknown", "fa5s.server"))

    failed = [mark for mark in tls if mark.state == "fail"]
    if tls and len(failed) == len(tls):
        stages.append(RoadStage("TLS", failed[-1].word, "fail", "fa5s.lock"))
    elif failed:
        stages.append(RoadStage("TLS", "частично", "warn", "fa5s.lock"))
    else:
        stages.append(RoadStage("TLS", "проходит" if tls else "—", "ok" if tls else "unknown", "fa5s.lock"))

    cut = any("16" in text for text, _state in card.tags)
    if card.level == "ok":
        stages.append(RoadStage("Сайт", "открывается", "ok", "fa5s.globe"))
    elif cut:
        stages.append(RoadStage("Сайт", "обрыв на 16 КБ", "fail", "fa5s.globe"))
    else:
        stages.append(RoadStage("Сайт", "не открывается", "fail" if card.level == "fail" else "warn", "fa5s.globe"))

    broken = next((index for index, stage in enumerate(stages) if stage.state == "fail"), -1)
    if 0 <= broken < len(stages) - 1:
        # До шагов за местом обрыва соединение не дошло: про них сказать нечего.
        stages = stages[: broken + 1] + [RoadStage(stage.title, "не дошли", "unknown", stage.icon) for stage in stages[broken + 1 :]]
    return tuple(stages), broken


_COLORS = {"ok": ("#5bb974", "#1a7f37"), "warn": ("#d99a4e", "#955800"), "fail": ("#e5645d", "#b3261e"), "unknown": ("#7d838c", "#8a8f98")}


class SiteRoad(QWidget):
    """Дорога до сайта: кружки шагов, линия между ними и точка, которая едет до места обрыва."""

    HEIGHT = 96
    NODE = 30
    PAD = 64

    def __init__(self, card: Card, parent=None) -> None:
        super().__init__(parent)
        self.stages, self.broken = site_road(card)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._title_font = getFont(12)
        self._word_font = getFont(11)
        self._travel = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(TRAVEL_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.valueChanged.connect(self._on_value)
        self._played = False
        self._theme_refresh = ThemeRefreshBinding(self, lambda *_args, **_kwargs: self.update())
        where = self.stages[self.broken].title if self.broken >= 0 else "нигде"
        set_state_text(self, "Дорога до сайта: " + "; ".join(f"{stage.title} — {stage.word}" for stage in self.stages if stage.word) + f". Режут: {where}")

    def last_reached(self) -> int:
        """Номер шага, до которого доезжает точка."""
        return self.broken if self.broken >= 0 else len(self.stages) - 1

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(520, self.HEIGHT)

    def node_center(self, index: int) -> QPointF:
        span = max(1.0, self.width() - 2 * self.PAD)
        return QPointF(self.PAD + span * index / max(1, len(self.stages) - 1), 30.0)

    def play(self) -> None:
        self._played = True
        self._anim.stop()
        if are_live_animations_enabled():
            self._travel = 0.0
            self._anim.start()
        else:
            self._travel = 1.0
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._played:
            self.play()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._anim.stop()
        self._travel = 1.0
        super().hideEvent(event)

    def _on_value(self, value) -> None:
        self._travel = float(value)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        light = False
        try:
            from ui.theme import get_theme_tokens

            light = bool(get_theme_tokens().is_light)
        except Exception:
            pass
        text = QColor(0, 0, 0, 228) if light else QColor(255, 255, 255, 235)
        back = QColor(0, 0, 0, 12) if light else QColor(255, 255, 255, 11)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(back)
        painter.drawRoundedRect(QRectF(self.rect()), 8, 8)

        def color(state: str) -> QColor:
            dark_color, light_color = _COLORS.get(state, _COLORS["unknown"])
            return QColor(light_color if light else dark_color)

        last = self.last_reached()
        centers = [self.node_center(index) for index in range(len(self.stages))]
        # Сколько дороги точка уже проехала (в шагах).
        gone = self._travel * last
        for index in range(len(centers) - 1):
            start, end = centers[index], centers[index + 1]
            if index < last:
                # Пройденный участок закрашивается вслед за точкой.
                share = max(0.0, min(1.0, gone - index))
                painter.setPen(QPen(color("unknown"), 2, Qt.PenStyle.DotLine))
                painter.drawLine(start, end)
                if share > 0:
                    middle = QPointF(start.x() + (end.x() - start.x()) * share, start.y())
                    painter.setPen(QPen(color("fail" if index + 1 == self.broken else "ok"), 2))
                    painter.drawLine(start, middle)
            else:
                painter.setPen(QPen(color("unknown"), 2, Qt.PenStyle.DotLine))
                painter.drawLine(start, end)

        title_metrics = QFontMetrics(self._title_font)
        for index, (stage, center) in enumerate(zip(self.stages, centers)):
            arrived = gone >= index - 0.02
            tone = color(stage.state if arrived else "unknown")
            painter.setPen(QPen(tone, 1.6))
            fill = QColor(tone)
            fill.setAlpha(46)
            painter.setBrush(QColor(32, 32, 32) if not light else QColor(245, 245, 245))
            painter.drawEllipse(center, self.NODE / 2, self.NODE / 2)
            painter.setBrush(fill)
            painter.drawEllipse(center, self.NODE / 2, self.NODE / 2)
            try:
                icon = get_cached_qta_pixmap(stage.icon, color=tone.name(), size=14)
                painter.drawPixmap(int(center.x() - 7), int(center.y() - 7), 14, 14, icon)
            except Exception:
                pass
            if index == self.broken and arrived:
                # Место обрыва — крестик на кружке.
                badge = QPointF(center.x() + 11, center.y() - 11)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color("fail"))
                painter.drawEllipse(badge, 6.5, 6.5)
                painter.setPen(QPen(QColor("#ffffff"), 1.6))
                painter.drawLine(QPointF(badge.x() - 2.5, badge.y() - 2.5), QPointF(badge.x() + 2.5, badge.y() + 2.5))
                painter.drawLine(QPointF(badge.x() - 2.5, badge.y() + 2.5), QPointF(badge.x() + 2.5, badge.y() - 2.5))
            width = max(90.0, (centers[1].x() - centers[0].x()) - 8) if len(centers) > 1 else 120.0
            box = QRectF(center.x() - width / 2, 50, width, 18)
            painter.setFont(self._title_font)
            painter.setPen(text)
            painter.drawText(box, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), stage.title)
            if stage.word:
                painter.setFont(self._word_font)
                painter.setPen(tone if stage.state in ("fail", "warn") and arrived else color("unknown"))
                word = QFontMetrics(self._word_font).elidedText(stage.word, Qt.TextElideMode.ElideRight, int(width))
                painter.drawText(box.translated(0, 17), int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), word)
        _ = title_metrics

        # Сама точка: едет от компьютера и замирает на последнем шаге, до которого дошли.
        if self._travel < 1.0 and last > 0:
            segment = min(last - 1, int(gone))
            share = gone - segment
            start, end = centers[segment], centers[segment + 1]
            point = QPointF(start.x() + (end.x() - start.x()) * share, start.y())
            glow = QColor(color("ok"))
            glow.setAlpha(70)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(point, 7, 7)
            painter.setBrush(color("ok"))
            painter.drawEllipse(point, 3.5, 3.5)
        painter.end()


__all__ = ["RoadStage", "SiteRoad", "site_road"]
