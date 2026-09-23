"""Status card for Premium page."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy

from ui.accessibility import set_control_accessibility, set_state_text
from ui.theme import get_theme_tokens
from ui.theme_refresh import ThemeRefreshBinding
from ui.theme_semantic import get_semantic_palette


_STATUS_ICONS = {
    'active': '✓',
    'warning': '⚠',
    'expired': '✕',
    'neutral': 'ℹ',
}


def _normalize_status(status: str) -> str:
    return status if status in _STATUS_ICONS else 'neutral'


def _status_colors(status: str, tokens) -> tuple[str, str]:
    """Возвращает (цвет текста и значка, цвет фона) для статуса в теме tokens."""
    palette = get_semantic_palette(tokens.theme_name)
    status = _normalize_status(status)
    if status == 'active':
        return palette.success_text, palette.success_soft_bg
    if status == 'warning':
        return palette.warning_text, palette.warning_soft_bg
    if status == 'expired':
        return palette.error_text, palette.error_soft_bg
    return palette.info, tokens.accent_soft_bg


class StatusCard(QFrame):
    """Full-width subscription status card."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(52)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(10)

        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedWidth(22)
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._title_lbl = QLabel()
        self._detail_lbl = QLabel()

        row.addWidget(self._icon_lbl)
        row.addWidget(self._title_lbl)
        row.addSpacing(8)
        row.addWidget(self._detail_lbl)
        row.addStretch(1)

        self._status = 'neutral'
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self.set_status("", "", "neutral")

    def set_status(self, text: str, details: str = "", status: str = "neutral"):
        self._status = _normalize_status(status)

        self._icon_lbl.setText(_STATUS_ICONS[self._status])
        self._title_lbl.setText(text)
        self._detail_lbl.setText(details)
        self._detail_lbl.setVisible(bool(details))

        spoken_parts = [str(text or "").strip(), str(details or "").strip()]
        spoken_text = ". ".join(part for part in spoken_parts if part)
        if not spoken_text:
            spoken_text = "статус пока не загружен"
        state_text = f"Статус Premium: {spoken_text}"
        set_control_accessibility(
            self,
            name=state_text,
            description="Показывает состояние Premium-подписки и срок действия.",
        )
        set_state_text(self, state_text)
        title_text = str(text or "").strip() or "статус пока не загружен"
        details_text = str(details or "").strip() or "—"
        set_state_text(self._icon_lbl, f"Индикатор Premium: {spoken_text}")
        set_state_text(self._title_lbl, f"Статус Premium: {title_text}")
        set_state_text(self._detail_lbl, f"Детали Premium: {details_text}")

        self._apply_theme_refresh()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        tokens = tokens or get_theme_tokens()
        fg, bg = _status_colors(self._status, tokens)

        self._icon_lbl.setStyleSheet(
            f"color: {fg}; font-size: 15px; font-weight: bold; background: transparent;"
        )
        self._title_lbl.setStyleSheet(
            f"color: {fg}; font-weight: 600; font-size: 13px; background: transparent;"
        )
        self._detail_lbl.setStyleSheet(
            f"color: {tokens.fg_muted}; font-size: 13px; background: transparent;"
        )
        self.setStyleSheet(f"""
            StatusCard {{
                background-color: {bg};
                border: none;
                border-radius: 8px;
            }}
        """)
