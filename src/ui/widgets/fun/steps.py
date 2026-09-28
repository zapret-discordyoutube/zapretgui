"""Список шагов с живыми отметками.

У каждого шага отметка:

- ждёт — бледный кружок;
- идёт — кружок с «бегущей» дугой;
- готово — галочка, которая рисуется штрихом;
- провал — крестик, который коротко встряхивается;
- пропущен — чёрточка.

Цвета — из семантической палитры темы. Анимация идёт только пока шаг
меняется или выполняется.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel

from ui.accessibility import set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.theme_refresh import ThemeRefreshBinding

STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_DONE = "done"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"
_STATUS_WORDS = {
    STEP_PENDING: "ждёт",
    STEP_RUNNING: "идёт",
    STEP_DONE: "готово",
    STEP_FAILED: "не удалось",
    STEP_SKIPPED: "пропущен",
}


class StepMark(QWidget):
    def __init__(self, parent=None, *, size: int = 18) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._status = STEP_PENDING
        self._t = 1.0
        self._colors = {"success": QColor("#6ccb5f"), "error": QColor("#ff6b6b"), "accent": QColor("#60cdff"), "muted": QColor("#8a8a8a")}
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._on_value)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def status(self) -> str:
        return self._status

    def set_status(self, status: str) -> None:
        if status == self._status:
            return
        self._status = status
        self._anim.stop()
        if are_live_animations_enabled() and self.isVisible() and status != STEP_PENDING:
            self._t = 0.0
            self._anim.setDuration(1100 if status == STEP_RUNNING else 420)
            self._anim.setLoopCount(-1 if status == STEP_RUNNING else 1)
            self._anim.start()
        else:
            self._t = 1.0
        self.update()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._anim.stop()
        self._t = 1.0
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._status == STEP_RUNNING and are_live_animations_enabled():
            self._anim.setDuration(1100)
            self._anim.setLoopCount(-1)
            self._anim.start()

    def _on_value(self, value) -> None:
        self._t = float(value)
        self.update()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        try:
            from ui.theme import get_theme_tokens
            from ui.theme_semantic import get_semantic_palette

            tokens = tokens or get_theme_tokens()
            palette = get_semantic_palette(getattr(tokens, "theme_name", None))
            from ui.theme import to_qcolor

            self._colors = {
                "success": to_qcolor(palette.success_text, "#6ccb5f"),
                "error": to_qcolor(palette.error_text, "#ff6b6b"),
                "accent": to_qcolor(tokens.accent_hex, "#60cdff"),
                "muted": to_qcolor(tokens.fg_faint, "#8a8a8a"),
            }
        except Exception:
            pass
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        center = box.center()
        status = self._status
        t = self._t
        if status == STEP_PENDING:
            pen = QPen(self._colors["muted"], 1.6)
            painter.setPen(pen)
            painter.drawEllipse(box.adjusted(2, 2, -2, -2))
        elif status == STEP_RUNNING:
            ring = QColor(self._colors["accent"])
            ring.setAlphaF(0.25)
            painter.setPen(QPen(ring, 2.0))
            painter.drawEllipse(box.adjusted(1, 1, -1, -1))
            painter.setPen(QPen(self._colors["accent"], 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(box.adjusted(1, 1, -1, -1), int(-t * 360 * 16), 100 * 16)
        elif status == STEP_DONE:
            color = self._colors["success"]
            fill = QColor(color)
            fill.setAlphaF(0.18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            grow = 0.6 + 0.4 * min(1.0, t * 1.6)
            painter.drawEllipse(center, box.width() / 2 * grow, box.height() / 2 * grow)
            path = QPainterPath(QPointF(box.left() + box.width() * 0.26, center.y() + box.height() * 0.02))
            path.lineTo(QPointF(box.left() + box.width() * 0.44, box.top() + box.height() * 0.70))
            path.lineTo(QPointF(box.left() + box.width() * 0.76, box.top() + box.height() * 0.30))
            painter.setPen(QPen(color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if t >= 1.0:
                painter.drawPath(path)
            else:
                # Галочка рисуется штрихом: показываем только пройденную часть пути.
                length = path.length()
                pen = painter.pen()
                pen.setDashPattern([length / pen.widthF() * t + 0.01, length])
                painter.setPen(pen)
                painter.drawPath(path)
        elif status == STEP_FAILED:
            shake = 2.5 * math.sin(4 * math.pi * t) * (1.0 - t)
            painter.translate(shake, 0)
            color = self._colors["error"]
            fill = QColor(color)
            fill.setAlphaF(0.18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawEllipse(box)
            painter.setPen(QPen(color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            d = box.width() * 0.22
            painter.drawLine(QPointF(center.x() - d, center.y() - d), QPointF(center.x() + d, center.y() + d))
            painter.drawLine(QPointF(center.x() + d, center.y() - d), QPointF(center.x() - d, center.y() + d))
        else:
            painter.setPen(QPen(self._colors["muted"], 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(box.left() + 4, center.y()), QPointF(box.right() - 4, center.y()))
        painter.end()


class _StepRow(QWidget):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(10)
        self.mark = StepMark(self)
        layout.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignVCenter)
        self.label = BodyLabel(text, self)
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)


class StepList(QWidget):
    """Шаги по ключам: ``add_step(key, text)``, ``set_step(key, status, text)``."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._rows: dict[str, _StepRow] = {}

    def add_step(self, key: str, text: str) -> None:
        row = _StepRow(text, self)
        self._rows[key] = row
        self._layout.addWidget(row)
        self._announce(key)

    def clear(self) -> None:
        for row in self._rows.values():
            self._layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self._rows.clear()

    def reset(self, steps: list[tuple[str, str]]) -> None:
        self.clear()
        for key, text in steps:
            self.add_step(key, text)

    def set_step(self, key: str, status: str, text: str = "") -> None:
        row = self._rows.get(key)
        if row is None:
            return
        if text:
            row.label.setText(text)
        row.mark.set_status(status)
        self._announce(key)

    def status(self, key: str) -> str:
        row = self._rows.get(key)
        return row.mark.status() if row is not None else ""

    def text(self, key: str) -> str:
        row = self._rows.get(key)
        return row.label.text() if row is not None else ""

    def keys(self) -> list[str]:
        return list(self._rows)

    def _announce(self, key: str) -> None:
        row = self._rows[key]
        set_state_text(row, f"{row.label.text()}: {_STATUS_WORDS.get(row.mark.status(), '')}")


__all__ = ["STEP_DONE", "STEP_FAILED", "STEP_PENDING", "STEP_RUNNING", "STEP_SKIPPED", "StepList", "StepMark"]
