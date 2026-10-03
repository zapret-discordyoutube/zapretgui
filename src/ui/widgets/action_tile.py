"""Плитка-действие: крупный цветной значок, название и короткое пояснение.

Вся плитка — одна кнопка. Под мышью она мягко заливается цветом своего
значка, значок подпрыгивает, а в углу проявляется стрелка. Пока действие
выполняется, плитку выключают (``setEnabled(False)``), и она бледнеет.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QVBoxLayout

from qfluentwidgets import CaptionLabel, StrongBodyLabel

from ui.accessibility import set_control_accessibility, set_state_text
from ui.widgets.motion_icon import MotionIcon
from ui.widgets.tile_grid import SoftTile


ACTION_ICON_SIZE = 20
ACTION_ICON_BOX = 36
ACTION_TILE_MARGIN = 14
# Значок рисуется чуть позже плитки: первая отрисовка страницы не ждёт qtawesome.
ACTION_ICON_DELAY_MS = 250
DISABLED_OPACITY = 0.45


class ActionTile(SoftTile):
    def __init__(
        self,
        parent=None,
        *,
        icon_name: str,
        icon_color: str,
        title: str,
        content: str = "",
        accessible_name: str = "",
    ):
        super().__init__(parent, clickable=True)
        self._icon_name = str(icon_name or "fa5s.circle")
        self._icon_color = QColor(str(icon_color or "#60cdff"))
        self._accessible_name = ""

        self._icon = MotionIcon(self, size=ACTION_ICON_SIZE)
        self._title = StrongBodyLabel(self)
        self._title.setWordWrap(True)
        self._content = CaptionLabel(self)
        self._content.setWordWrap(True)
        # Приглушённое пояснение: главное в плитке — название.
        self._content.setTextColor("#5f6672", "#aab1bd")

        # Ряд значка высотой с его подложку; сам значок стоит в её середине.
        self._icon_row_height = max(ACTION_ICON_BOX, self._icon.height())
        icon_row = QHBoxLayout()
        icon_row.setContentsMargins(0, 0, 0, 0)
        icon_row.addStrut(self._icon_row_height)
        icon_row.addSpacing(max(0, (ACTION_ICON_BOX - self._icon.width()) // 2))
        icon_row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        icon_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(ACTION_TILE_MARGIN, ACTION_TILE_MARGIN, ACTION_TILE_MARGIN, ACTION_TILE_MARGIN)
        layout.setSpacing(3)
        layout.addLayout(icon_row)
        layout.addSpacing(7)
        layout.addWidget(self._title)
        layout.addWidget(self._content)
        layout.addStretch(1)

        self.set_texts(title, content, accessible_name=accessible_name)
        QTimer.singleShot(ACTION_ICON_DELAY_MS, self._apply_icon)

    # ---- тексты --------------------------------------------------------

    def title(self) -> str:
        return self._title.text()

    def content(self) -> str:
        return self._content.text()

    def set_texts(self, title: str, content: str = "", *, accessible_name: str = "") -> None:
        """Название, пояснение и имя для диктора («Открыть тест соединения»)."""
        title = str(title or "")
        content = str(content or "")
        if self._title.text() != title:
            self._title.setText(title)
        if self._content.text() != content:
            self._content.setText(content)
        if self._content.isHidden() == bool(content):
            self._content.setVisible(bool(content))
        self._accessible_name = str(accessible_name or title)
        self._sync_accessibility()

    def _sync_accessibility(self) -> None:
        name = self._accessible_name
        if not self.isEnabled():
            name = f"{name}, недоступно"
        set_control_accessibility(
            self,
            name=name,
            description=f"{self._content.text()} Нажмите Enter или Пробел.".strip(),
        )
        set_state_text(self, name)

    # ---- вид -----------------------------------------------------------

    def accent_color(self) -> QColor:
        # Плитка загорается цветом своего значка, а не общим акцентом темы.
        return QColor(self._icon_color)

    def chevron_center_y(self) -> float:
        return float(ACTION_TILE_MARGIN + self._icon_row_height / 2)

    def icon_box(self) -> QRectF:
        center = self._icon.geometry().center()
        half = ACTION_ICON_BOX / 2
        return QRectF(center.x() - half + 0.5, center.y() - half + 0.5, ACTION_ICON_BOX, ACTION_ICON_BOX)

    def _apply_icon(self) -> None:
        from ui.theme import get_cached_qta_pixmap

        self._icon.setPixmap(
            get_cached_qta_pixmap(self._icon_name, color=self._icon_color.name(), size=ACTION_ICON_SIZE)
        )

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        if self.isEnabled():
            self._icon.bounce()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            if self.isEnabled():
                self.setGraphicsEffect(None)
            else:
                effect = QGraphicsOpacityEffect(self)
                effect.setOpacity(DISABLED_OPACITY)
                self.setGraphicsEffect(effect)
            self._sync_accessibility()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_tile_background(painter)
        # Подложка значка: мягкий квадрат его цвета, под мышью становится ярче.
        box = QColor(self._icon_color)
        box.setAlphaF(0.14 + 0.10 * self.hover_progress())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(box)
        painter.drawRoundedRect(self.icon_box(), 9.0, 9.0)
        painter.end()


__all__ = ["ActionTile"]
