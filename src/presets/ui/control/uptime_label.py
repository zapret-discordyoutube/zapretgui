"""Подпись «сколько уже работает обход» в карточке «Статус работы».

Подпись сама ничего не замеряет: момент, с которого обход работает,
приходит из общего состояния окна (его пишет LaunchRuntimeService).
Текст меняется раз в минуту одиночным таймером — и только пока подпись
видна, а окно не свёрнуто.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen

from qfluentwidgets import CaptionLabel

from app.ui_texts import tr as tr_catalog
from ui.accessibility import set_state_text


def format_uptime(seconds: float, *, language: str) -> str:
    """«12 с», «14 мин», «2 ч 14 мин», «3 д 4 ч»."""
    total_seconds = max(0, int(seconds))
    total_minutes = total_seconds // 60
    if total_minutes < 1:
        return tr_catalog(
            "page.control.status.uptime.seconds", language=language, default="{seconds} с"
        ).format(seconds=total_seconds)
    if total_minutes < 60:
        return tr_catalog(
            "page.control.status.uptime.minutes", language=language, default="{minutes} мин"
        ).format(minutes=total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    if hours < 24:
        return tr_catalog(
            "page.control.status.uptime.hours", language=language, default="{hours} ч {minutes} мин"
        ).format(hours=hours, minutes=minutes)
    days, hours = divmod(hours, 24)
    return tr_catalog(
        "page.control.status.uptime.days", language=language, default="{days} д {hours} ч"
    ).format(days=days, hours=hours)


PILL_PAD_Y = 2
PILL_PAD_LEFT = 20
PILL_PAD_RIGHT = 8


class UptimeLabel(CaptionLabel):
    def __init__(self, parent=None, *, language: str = "ru", clock=time.time):
        super().__init__(parent)
        self._language = str(language or "ru")
        self._clock = clock
        self._since = 0.0
        # Приглушённый цвет: это пояснение к заголовку, а не второй заголовок.
        self.setTextColor("#3f4650", "#c8ced8")
        # Плашка-«таблетка» с часиками: ровно стоит рядом с крупным заголовком
        # по центру строки, и мелкий шрифт не кажется съехавшим.
        self.setContentsMargins(PILL_PAD_LEFT, PILL_PAD_Y, PILL_PAD_RIGHT, PILL_PAD_Y)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._refresh)
        self.setVisible(False)

    def running_since(self) -> float:
        return self._since

    def set_running_since(self, since: float) -> None:
        """since — секунды Unix, с которых работает обход; 0 — не работает."""
        try:
            value = max(0.0, float(since or 0.0))
        except (TypeError, ValueError):
            value = 0.0
        if value == self._since:
            return
        self._since = value
        if self.isHidden() == (value > 0.0):
            self.setVisible(value > 0.0)
        self._refresh()

    def set_language(self, language: str) -> None:
        language = str(language or "ru")
        if language == self._language:
            return
        self._language = language
        self._refresh()

    def _can_tick(self) -> bool:
        if self._since <= 0.0 or not self.isVisible():
            return False
        window = self.window()
        return window is None or not window.isMinimized()

    def _refresh(self) -> None:
        self._timer.stop()
        if self._since <= 0.0:
            if self.text():
                self.setText("")
            return
        elapsed = max(0.0, float(self._clock()) - self._since)
        uptime = format_uptime(elapsed, language=self._language)
        text = uptime
        if self.text() != text:
            self.setText(text)
            set_state_text(
                self,
                tr_catalog(
                    "page.control.status.uptime.accessible",
                    language=self._language,
                    default="Обход работает: {uptime}",
                ).format(uptime=uptime),
            )
        if self._can_tick():
            # Следующая смена текста — на границе минуты, раньше будить незачем.
            if elapsed < 60.0:
                # Первую минуту считаем секунды: текст меняется раз в секунду.
                self._timer.start(int((1.0 - elapsed % 1.0) * 1000) + 20)
            else:
                # Дальше — только на границе минуты, раньше будить незачем.
                self._timer.start(int((60.0 - elapsed % 60.0) * 1000) + 250)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self.palette().color(self.foregroundRole()))
        pill = QColor(color)
        pill.setAlphaF(0.12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pill)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        # Часики: кружок и две стрелки.
        pen = QPen(color, 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        center = QPointF(PILL_PAD_Y + 5.0 + 2.0, rect.center().y())
        painter.drawEllipse(center, 4.6, 4.6)
        painter.drawLine(center, QPointF(center.x(), center.y() - 2.8))
        painter.drawLine(center, QPointF(center.x() + 2.2, center.y()))
        painter.end()
        super().paintEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._refresh()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._refresh()


__all__ = ["UptimeLabel", "format_uptime"]
