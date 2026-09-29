"""Раскраска файла hosts по владельцу строк.

Каждый блок (ZapretGUI, Telegram, MAX, госСМИ, Adobe) получает свой цвет,
ваши строки — обычный цвет текста, комментарии вне блоков — приглушённый.
Кто владелец строки, решает hosts.hosts_blocks.classify_hosts_line — тот же
разбор, по которому считаются строки на странице.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QFont, QTextCharFormat

from hosts.hosts_blocks import (
    BLOCK_ADOBE,
    BLOCK_MAX,
    BLOCK_STATE_MEDIA,
    BLOCK_TELEGRAM,
    BLOCK_USER,
    BLOCK_ZAPRETGUI,
    ROLE_BLANK,
    ROLE_COMMENT,
    ROLE_MARKER,
    STATE_NONE,
    classify_hosts_line,
)
from ui.code_editor.syntax import BaseSyntaxHighlighter, SyntaxTheme


# (тёмная тема, светлая тема). У ZapretGUI — акцент темы, у ваших строк —
# обычный цвет текста.
_OWNER_COLORS: dict[str, tuple[str, str]] = {
    BLOCK_TELEGRAM: ("#8fa8ff", "#3451b2"),
    BLOCK_MAX: ("#ffab70", "#b54708"),
    BLOCK_STATE_MEDIA: ("#ff7a7a", "#c42b1c"),
    BLOCK_ADOBE: ("#d08cff", "#8a3ab9"),
}


def is_dark_text_theme(theme: SyntaxTheme) -> bool:
    """Светлый текст — значит, тёмная тема."""
    return theme.text.lightness() > 128


def owner_color(kind: str | None, theme: SyntaxTheme) -> QColor:
    if kind == BLOCK_ZAPRETGUI:
        return QColor(theme.accent)
    if kind in _OWNER_COLORS:
        dark, light = _OWNER_COLORS[kind]
        return QColor(dark if is_dark_text_theme(theme) else light)
    return QColor(theme.text)


class HostsSyntaxHighlighter(BaseSyntaxHighlighter):
    """Подсветка hosts: цвет строки — цвет её владельца."""

    def _rebuild_formats(self) -> None:
        super()._rebuild_formats()
        theme = self.theme
        self._owner_formats: dict[tuple[str | None, str], QTextCharFormat] = {}
        for kind in (BLOCK_ZAPRETGUI, BLOCK_TELEGRAM, BLOCK_MAX, BLOCK_STATE_MEDIA, BLOCK_ADOBE, BLOCK_USER, None):
            color = owner_color(kind, theme)
            entry = QTextCharFormat()
            entry.setForeground(color)
            marker = QTextCharFormat()
            marker.setForeground(color)
            marker.setFontWeight(QFont.Weight.Bold)
            comment = QTextCharFormat()
            faded = QColor(color)
            faded.setAlpha(150 if kind is not None else 110)
            comment.setForeground(faded)
            comment.setFontItalic(True)
            self._owner_formats[(kind, "entry")] = entry
            self._owner_formats[(kind, ROLE_MARKER)] = marker
            self._owner_formats[(kind, ROLE_COMMENT)] = comment

    def format_for(self, kind: str | None, role: str) -> QTextCharFormat | None:
        if role == ROLE_BLANK:
            return None
        key = (kind, role if role in (ROLE_MARKER, ROLE_COMMENT) else "entry")
        return self._owner_formats.get(key)

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        previous = self.previousBlockState()
        kind, role, state = classify_hosts_line(text, previous if previous >= 0 else STATE_NONE)
        self.setCurrentBlockState(state)
        line = str(text or "")
        text_format = self.format_for(kind, role)
        if not line or text_format is None:
            return
        comment_at = line.find("#") if role not in (ROLE_MARKER, ROLE_COMMENT) else -1
        if comment_at > 0:
            # Запись с хвостовым комментарием: хвост приглушён.
            self.setFormat(0, comment_at, text_format)
            self.setFormat(comment_at, len(line) - comment_at, self._owner_formats[(kind, ROLE_COMMENT)])
            return
        self.setFormat(0, len(line), text_format)


__all__ = ["HostsSyntaxHighlighter", "is_dark_text_theme", "owner_color"]
