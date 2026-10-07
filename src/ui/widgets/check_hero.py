"""Панель, с которой начинается вкладка проверки: талисман, значок с главной фразой, пояснение и кнопки.

Образец — вкладка «DNS-серверы» раздела BlockCheck: всё, что нужно, чтобы
начать, стоит в одной панели сверху, кнопки — сразу под главной фразой.
Вкладка кладёт свои поля и кнопки через ``add_layout`` и ``add_widget`` —
теми же вызовами, что у обычной карточки настроек.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, SimpleCardWidget, SubtitleLabel

from ui.accessibility import set_state_text
from ui.theme import get_cached_qta_pixmap, get_theme_tokens
from ui.theme_refresh import ThemeRefreshBinding
from ui.widgets.fun import Mascot

MASCOT_SIZE = 56
ICON_SIZE = 24


class CheckHero(SimpleCardWidget):
    """Шапка вкладки проверки. Размеры — общие для всех вкладок раздела."""

    def __init__(self, icon: str = "fa5s.search", parent=None) -> None:
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(14)
        self.mascot = Mascot(self, size=MASCOT_SIZE)
        root.addWidget(self.mascot, 0, Qt.AlignmentFlag.AlignTop)

        self._body = QVBoxLayout()
        self._body.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self._icon_name = icon
        self.icon = QLabel(self)
        self.icon.setFixedSize(ICON_SIZE, ICON_SIZE)
        title_row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title_label = SubtitleLabel("", self)
        self.title_label.setWordWrap(True)
        title_row.addWidget(self.title_label, 1)
        self._body.addLayout(title_row)
        self.detail_label = BodyLabel("", self)
        self.detail_label.setWordWrap(True)
        self._body.addWidget(self.detail_label)
        self._body.addSpacing(4)
        root.addLayout(self._body, 1)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        try:
            color = str((tokens or get_theme_tokens()).icon_fg_muted)
            self.icon.setPixmap(get_cached_qta_pixmap(self._icon_name, color=color, size=ICON_SIZE))
        except Exception:
            pass

    def set_texts(self, title: str, detail: str = "") -> None:
        self.title_label.setText(title)
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail))
        set_state_text(self, f"{title}. {detail}".strip())

    def add_layout(self, layout: QLayout) -> None:
        self._body.addLayout(layout)

    def add_widget(self, widget: QWidget) -> None:
        self._body.addWidget(widget)


__all__ = ["CheckHero", "ICON_SIZE", "MASCOT_SIZE"]
