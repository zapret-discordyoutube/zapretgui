"""Части находки, общие для итога BlockCheck и отчёта карточки: перечень серверов и его метки.

Находка про DNS — фраза вида «Обычные ответы подменяются у серверов:
Cloudflare (1.1.1.1), Cloudflare (1.0.0.1) и ещё 6. Пояснение». Читать её
сплошным текстом тяжело, поэтому перечень серверов показывают метками: по
одной на сервис, со счётчиком адресов и значком, сами адреса — в подсказке.

Проверка отдаёт находку ещё и готовыми частями (``FindingParts`` в
``result_cards_model``: заголовок, все серверы, пояснение) — показывают их.
Разбор фразы (``split_finding``, ``split_server_list``) остался для отчётов
прошлых проверок: в них сохранена только фраза с перечнем, обрезанным до
«и ещё N».
"""

from __future__ import annotations

import re

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel

from blockcheck.ui.brand_icons import BrandIcon, named_brand
from ui.accessibility import set_state_text
from ui.fluent_widgets import set_tooltip
from ui.theme_refresh import ThemeRefreshBinding
from ui.widgets.elided_label import ElidedLabel
from ui.widgets.tone_group import ToneDot, mute

_SERVER = re.compile(r"\s*([^,()]+?) \(([^()]+)\)")
_MORE = re.compile(r"\s*и ещё (\d+)")


_BRACKETS = re.compile(r"\s*\([^()]*\)")
_FOR_LIST = re.compile(r"^(.*?) для (\S+\.\S+.*)$")


def short_title(title: str) -> tuple[str, list[str], int]:
    """Заголовок для карточки: без скобок и без перечня сайтов. Возвращает (заголовок, сайты, сколько не названо).

    «DNS подменяет ответы для www.youtube.com, rutracker.org и ещё 9» →
    («DNS подменяет ответы», [«www.youtube.com», «rutracker.org»], 9). Полный
    заголовок остаётся в подсказке и на странице находки.
    """
    text = str(title or "").strip()
    names: list[str] = []
    more = 0
    match = _FOR_LIST.match(text)
    if match is not None:
        text, tail = match.group(1), match.group(2)
        found = _MORE.search(tail)
        if found is not None:
            more = int(found.group(1))
            tail = tail[: found.start()]
        names = [name.strip() for name in tail.split(",") if name.strip()]
    return _BRACKETS.sub("", text).strip(), names, more


def split_finding(text: str) -> tuple[str, str]:
    """Находка про DNS → заголовок и подробности: «что случилось: подробности» или «что случилось. Пояснение»."""
    text = str(text or "").strip()
    for separator in (": ", ". "):
        head, found, tail = text.partition(separator)
        tail = tail.strip()
        if found and tail:
            tail = f"{tail[:1].upper()}{tail[1:]}"
            return head.rstrip("."), tail if tail.endswith((".", "!", "?")) else f"{tail}."
    return text, ""


def split_server_list(detail: str) -> tuple[list[tuple[str, list[str]]], int, str]:
    """Перечень «Cloudflare (1.1.1.1), Cloudflare (1.0.0.1) и ещё 6. Пояснение» по частям.

    Возвращает серверы по названиям с их адресами, число не названных и
    остаток текста. Если пояснение начинается не с перечня — серверов нет, а
    остаток равен всему тексту.
    """
    text = str(detail or "")
    servers: dict[str, list[str]] = {}
    position = 0
    while True:
        match = _SERVER.match(text, position)
        if match is None:
            break
        servers.setdefault(match.group(1).strip(), []).append(match.group(2).strip())
        position = match.end()
        if not text.startswith(", ", position):
            break
        position += 2
    if not servers:
        return [], 0, text
    more = 0
    match = _MORE.match(text, position)
    if match is not None:
        more = int(match.group(1))
        position = match.end()
    return list(servers.items()), more, text[position:].lstrip(". ").strip()


class ServerChip(QWidget):
    """Метка сервиса (DNS, хостинг, сайт): значок, название и сколько его адресов названо. Адреса — в подсказке.

    Своей подложки у метки нет: она стоит на карточке, а карточка — в группе, и
    третья рамка внутри двух делала экран тяжёлым.
    """

    def __init__(self, name: str, addresses: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(20)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        brand = named_brand(name)
        self.icon: BrandIcon | None = None
        if brand is not None:
            self.icon = BrandIcon(brand.icon, brand.color, self, size=13)
            layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text = name if len(addresses) < 2 else f"{name} ×{len(addresses)}"
        # Адрес сайта на метке — без «www.»: так он короче и не обрезается.
        self.label = mute(CaptionLabel(self.text.removeprefix("www."), self))
        layout.addWidget(self.label, 0, Qt.AlignmentFlag.AlignVCenter)
        if addresses:
            set_tooltip(self, f"{name}: {', '.join(addresses)}")


def theme_color(token: str, fallback: QColor) -> QColor:
    try:
        from ui.theme import get_theme_tokens, to_qcolor

        return to_qcolor(getattr(get_theme_tokens(), token), fallback)
    except Exception:
        return QColor(fallback)


class CardsFlow(QWidget):
    """Сетка карточек: колонок столько, сколько помещается, карточки делят ширину поровну."""

    GAP = 6

    def __init__(self, parent=None, *, min_width: int = 210, card_height: int = 48) -> None:
        super().__init__(parent)
        self.MIN_WIDTH = int(min_width)
        self._card_height = int(card_height)
        self._cards: list[QWidget] = []
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def cards(self) -> list[QWidget]:
        return list(self._cards)

    def add(self, card: QWidget) -> None:
        card.setParent(self)
        self._cards.append(card)

    def columns_for(self, width: int) -> int:
        return max(1, (int(width) + self.GAP) // (self.MIN_WIDTH + self.GAP))

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        rows = -(-len(self._cards) // self.columns_for(width))
        return max(0, rows * (self._card_height + self.GAP) - self.GAP)

    def sizeHint(self) -> QSize:  # noqa: N802
        width = max(self.MIN_WIDTH, self.width())
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.MIN_WIDTH, self._card_height)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        columns = self.columns_for(self.width())
        width = (self.width() - self.GAP * (columns - 1)) // columns
        for index, card in enumerate(self._cards):
            row, column = divmod(index, columns)
            card.setGeometry(column * (width + self.GAP), row * (self._card_height + self.GAP), width, self._card_height)
        height = self.heightForWidth(self.width())
        if height != self.minimumHeight():
            self.setFixedHeight(height)


class FindingCard(QWidget):
    """Находка карточкой: точка важности, заголовок и серверы метками (или одна строка пояснения).

    Пояснение целиком и советы — в подсказке: на карточке только суть, иначе
    восемь находок подряд читаются как сплошной текст. Одна и та же карточка
    стоит в итоге проверки и в отчёте «DNS-серверы».
    """

    HEIGHT = 54
    MIN_WIDTH = 380
    CHIPS_SHOWN = 3
    CHIP_GAP = 12
    # Нажали карточку (когда ей есть что открыть).
    clicked = pyqtSignal()

    def __init__(
        self,
        title: str,
        parent=None,
        *,
        servers=(),
        more: int = 0,
        note: str = "",
        hint: str = "",
        color_for=None,
        hollow: bool = False,
        level_word: str = "",
        state_text: str = "",
    ) -> None:
        super().__init__(parent)
        # На карточке — короткий заголовок; сайты из него встают метками рядом с серверами.
        self.full_title = str(title or "")
        self.title, sites, more_sites = short_title(self.full_title)
        servers = [*[(name, []) for name in sites], *servers]
        more = int(more) + more_sites
        self._hover = False
        self._clickable = False
        # Действие и отчёт у таких карточек общие — они стоят в заголовке группы.
        self.action_button = None
        self.card_key = ""
        self.setFixedHeight(self.HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 10, 0)
        layout.setSpacing(10)

        self.dot = ToneDot(color_for or (lambda _tokens: "#9aa0aa"), self, hollow=hollow)
        if level_word:
            set_tooltip(self.dot, level_word)
        layout.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(3)
        texts.addStretch(1)
        self.text_label = ElidedLabel(self.title, self, strong=True)
        font = self.text_label.font()
        font.setPixelSize(13)
        self.text_label.setFont(font)
        texts.addWidget(self.text_label)
        self.server_chips: list[ServerChip] = []
        self.note_label: ElidedLabel | None = None
        servers = list(servers)
        # Сколько серверов не названо совсем (сверх меток) и надпись «и ещё N».
        self._unnamed = int(more) + max(0, len(servers) - self.CHIPS_SHOWN)
        self._more = mute(CaptionLabel("", self))
        self._more.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._more.hide()
        if servers:
            chips = QHBoxLayout()
            chips.setContentsMargins(0, 0, 0, 0)
            chips.setSpacing(self.CHIP_GAP)
            for name, addresses in servers[: self.CHIPS_SHOWN]:
                chip = ServerChip(name, list(addresses), self)
                chips.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
                self.server_chips.append(chip)
            chips.addWidget(self._more, 0, Qt.AlignmentFlag.AlignVCenter)
            chips.addStretch(1)
            texts.addLayout(chips)
            self._set_hidden(self._unnamed)
        elif note:
            self.note_label = mute(ElidedLabel(note, self))
            texts.addWidget(self.note_label)
        texts.addStretch(1)
        layout.addLayout(texts, 1)

        self.hint_text = str(hint or self.title)
        set_tooltip(self, self.hint_text)
        set_state_text(self, state_text or self.title)
        self._theme_refresh = ThemeRefreshBinding(self, lambda *_args, **_kwargs: self.update())

    @property
    def more_label(self) -> CaptionLabel | None:
        """Надпись «и ещё N»; ``None`` — все серверы названы метками."""
        return None if self._more.isHidden() else self._more

    def _set_hidden(self, count: int) -> None:
        self._more.setText(f"и ещё {count}" if count else "")
        self._more.adjustSize()
        self._more.setVisible(bool(count))

    def fit_chips(self, width: int) -> None:
        """Показывает столько меток, сколько помещается целиком; остальные уходят в «и ещё N»."""
        if not self.server_chips:
            return
        room = width - 12 - 10 - 10 - self.dot.width()
        metrics = self._more.fontMetrics()
        used, shown = 0, 0
        for order, chip in enumerate(self.server_chips):
            need = chip.sizeHint().width() + (self.CHIP_GAP if order else 0)
            # Сколько останется за меткой: под надпись «и ещё N» нужно место.
            left = self._unnamed + len(self.server_chips) - order - 1
            tail = metrics.horizontalAdvance(f"и ещё {left}") + self.CHIP_GAP + 4 if left else 0
            # Первая метка остаётся всегда: без неё на карточке нет ни одного сервера.
            if order and used + need + tail > room:
                break
            used += need
            shown += 1
        for order, chip in enumerate(self.server_chips):
            chip.setVisible(order < shown)
        self._set_hidden(self._unnamed + len(self.server_chips) - shown)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.fit_chips(self.width())

    def set_clickable(self) -> None:
        """Карточка открывает свою страницу: рука вместо стрелки, Enter с клавиатуры, строка в подсказке."""
        self._clickable = True
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        set_tooltip(self, f"{self.hint_text}\nНажмите, чтобы открыть подробности")

    def event(self, event) -> bool:
        if event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self._hover = event.type() == QEvent.Type.HoverEnter
            self.update()
        return super().event(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._clickable and event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self._clickable and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        token = "surface_bg_hover" if self._hover or self.hasFocus() else "surface_bg"
        painter.setBrush(theme_color(token, QColor(255, 255, 255, 18 if self._hover else 10)))
        painter.drawRoundedRect(self.rect(), 6, 6)
        painter.end()


__all__ = ["CardsFlow", "FindingCard", "ServerChip", "short_title", "split_finding", "split_server_list", "theme_color"]
