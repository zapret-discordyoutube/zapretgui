"""Группа строк с цветной точкой: заголовок, пояснение и строки на спокойной подложке.

Так экраны проверок собирают однородные строки: в BlockCheck — проблемы
одного вида блокировки, во вкладке «DNS-серверы» — находки одной важности.
У группы свой цвет, но им окрашена только точка перед заголовком: подложка
нейтральная, одна на все группы, чтобы экран не превращался в радугу.
Рамок нет — окно программы без рамок.

Группа не знает, что в ней лежит: экран даёт название, функцию цвета и
добавляет свои строки через ``add_widget``.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, StrongBodyLabel

from ui.theme_refresh import ThemeRefreshBinding

# Функция цвета получает токены темы (или None) и возвращает цвет строкой.
ColorFor = Callable[[object], str]


# Приглушённый текст пояснений: (светлая тема, тёмная тема).
MUTED_TEXT = (QColor(0, 0, 0, 160), QColor(255, 255, 255, 165))


def mute(label):
    """Делает подпись второстепенной: заголовки строк остаются главными."""
    label.setTextColor(*MUTED_TEXT)
    return label


class ToneDot(QWidget):
    """Цветная точка — единственное цветное пятно группы, плитки или строки.

    ``hollow`` — кольцо вместо заливки: так «предупреждение» отличается от
    «ошибки» не только цветом.
    """

    def __init__(self, color_for: ColorFor, parent=None, *, size: int = 8, hollow: bool = False) -> None:
        super().__init__(parent)
        self._color_for = color_for
        self._hollow = hollow
        self._color = QColor()
        self.setFixedSize(size, size)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        self._color = QColor(self._color_for(tokens))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_dot(painter, QRectF(self.rect()), self._color, hollow=self._hollow)
        painter.end()


def paint_dot(painter: QPainter, rect: QRectF, color: QColor, *, hollow: bool = False) -> None:
    if hollow:
        painter.setPen(QPen(color, 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect.adjusted(0.8, 0.8, -0.8, -0.8))
    else:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(rect)


class TonePill(QLabel):
    """Цветная метка-«таблетка»: название группы."""

    def __init__(self, text: str, color_for: ColorFor, parent=None) -> None:
        super().__init__(text, parent)
        self._color_for = color_for
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # Скругление в QSS работает, только пока радиус не больше половины высоты.
        self.setFixedHeight(22)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        color = QColor(self._color_for(tokens))
        self.setStyleSheet(
            f"QLabel {{ color: {color.name()}; "
            f"background-color: rgba({color.red()}, {color.green()}, {color.blue()}, 0.16); "
            "border-radius: 10px; padding: 0px 10px; font-weight: 600; }"
        )


class ToneGroup(QWidget):
    """Группа строк одного вида.

    ``plain`` — группа без названия и подложки: строки идут как обычный список
    (для того, что ни к какому виду не отнесли).
    ``flat`` — заголовок есть, а общей подложки нет: внутри лежат карточки со
    своей подложкой, и вторая вокруг них была бы лишней.
    """

    def __init__(
        self,
        title: str,
        color_for: ColorFor,
        parent=None,
        *,
        count: int = 0,
        about: str = "",
        plain: bool = False,
        flat: bool = False,
    ) -> None:
        super().__init__(parent)
        self._color_for = color_for
        self._plain = plain or flat
        self._surface = QColor()
        self._body = QVBoxLayout(self)
        self._body.setSpacing(8)
        if self._plain:
            self._body.setContentsMargins(0, 4 if flat else 0, 0, 0)
        else:
            self._body.setContentsMargins(14, 12, 12, 12)

        self.title_label: StrongBodyLabel | None = None
        self.about_label: CaptionLabel | None = None
        if not plain:
            header = QHBoxLayout()
            header.setSpacing(8)
            header.addWidget(ToneDot(color_for, self), 0, Qt.AlignmentFlag.AlignVCenter)
            self.title_label = StrongBodyLabel(title, self)
            header.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)
            if count > 1:
                header.addWidget(mute(CaptionLabel(str(count), self)), 0, Qt.AlignmentFlag.AlignVCenter)
            header.addStretch(1)
            self._body.addLayout(header)
            if about:
                self.about_label = self.add_note(about)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def add_widget(self, widget: QWidget) -> None:
        self._body.addWidget(widget)

    def add_note(self, text: str) -> CaptionLabel:
        """Строка мелкого текста во всю ширину группы: пояснение или общий совет."""
        label = mute(CaptionLabel(text, self))
        label.setWordWrap(True)
        self._body.addWidget(label)
        return label

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        try:
            from ui.theme import get_theme_tokens, to_qcolor

            tokens = tokens or get_theme_tokens()
            self._surface = to_qcolor(tokens.surface_bg, "#0affffff")
        except Exception:
            self._surface = QColor(255, 255, 255, 10)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        if self._plain:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._surface)
        painter.drawRoundedRect(self.rect(), 6, 6)
        painter.end()


__all__ = ["MUTED_TEXT", "ToneDot", "ToneGroup", "TonePill", "mute", "paint_dot"]
