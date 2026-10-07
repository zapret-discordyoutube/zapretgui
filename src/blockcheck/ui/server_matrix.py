"""Сводка «сервис × способ связи» вместо сырой таблицы DNS-серверов.

Полная проверка отдаёт таблицу серверов текстом: по строке на каждый адрес
(их больше сотни) и по столбцу на способ связи. Читать её глазами тяжело.
Здесь тот же текст превращается в короткую сводку: строка на сервис, а в
ячейке — у скольких его адресов этот способ работает («3/4»), цветом:
зелёный — у всех, жёлтый — у части или с потерями, красный — ни у одного.
Что именно ответил каждый адрес, показывает подсказка под мышью.

- ``parse_server_table`` — разбор текста (без окон, проверяется тестом);
- ``summarize_servers`` — сводка по сервисам;
- ``ServerMatrix`` — сама сводка на экране, один рисующий виджет.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import QSizePolicy, QWidget

from blockcheck.ui.brand_icons import named_brand, readable_color
from ui.accessibility import set_state_text
from ui.theme_refresh import ThemeRefreshBinding
from ui.widgets.hover_hint import HoverHint

_FIRST, _SECOND = "Сервер", "Адрес"
OK, WARN, FAIL, NONE = "ok", "warn", "fail", "none"
# (тёмная тема, светлая тема)
_COLORS = {OK: ("#5bb974", "#1a7f37"), WARN: ("#d99a4e", "#955800"), FAIL: ("#e5645d", "#b3261e")}


@dataclass(frozen=True, slots=True)
class ServerRow:
    server: str
    address: str
    cells: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ServiceSummary:
    name: str
    rows: tuple[ServerRow, ...]
    # По способу связи: (работает, с потерями, не работает) среди адресов сервиса.
    counts: tuple[tuple[int, int, int], ...]


def cell_state(text: str) -> str:
    """Ячейка таблицы → состояние: время ответа — работает, «2 из 3» — с потерями, слово — не работает."""
    text = str(text or "").strip()
    if not text or text in ("—", "-"):
        return NONE
    if "мс" in text:
        return WARN if " из " in text else OK
    return FAIL


def parse_server_table(text: str) -> tuple[list[str], list[ServerRow]]:
    """Таблица серверов из текста отчёта: названия способов связи и строки адресов.

    Столбцы выровнены пробелами, поэтому границы берутся из строки заголовка.
    Если заголовка нет, возвращает пустые списки — тогда показывают сам текст.
    """
    lines = str(text or "").splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.startswith(_FIRST) and f"  {_SECOND}" in line), -1
    )
    if start < 0:
        return [], []
    header = lines[start]
    titles = [part.strip() for part in header.split("  ") if part.strip()]
    offsets: list[int] = []
    position = 0
    for title in titles:
        position = header.index(title, position)
        offsets.append(position)
        position += len(title)
    rows: list[ServerRow] = []
    for line in lines[start + 1 :]:
        if not line.strip():
            break
        cells = [line[begin:end].strip() for begin, end in zip(offsets, [*offsets[1:], None])]
        if len(cells) < 2 or not cells[0]:
            continue
        rows.append(ServerRow(cells[0], cells[1], tuple(cells[2:])))
    return titles[2:], rows


def summarize_servers(rows: list[ServerRow], columns: int) -> list[ServiceSummary]:
    """Адреса одного сервиса — в одну строку сводки, в порядке первого появления."""
    grouped: dict[str, list[ServerRow]] = {}
    for row in rows:
        grouped.setdefault(row.server, []).append(row)
    summaries = []
    for name, items in grouped.items():
        counts = []
        for column in range(columns):
            states = [cell_state(item.cells[column]) if column < len(item.cells) else NONE for item in items]
            counts.append((states.count(OK), states.count(WARN), states.count(FAIL)))
        summaries.append(ServiceSummary(name, tuple(items), tuple(counts)))
    return summaries


def count_state(ok: int, warn: int, fail: int) -> str:
    if ok + warn + fail == 0:
        return NONE
    if ok + warn == 0:
        return FAIL
    return OK if fail == 0 and warn == 0 else WARN


class ServerMatrix(QWidget):
    """Сводка по сервисам: значок и название, число адресов и ячейка на каждый способ связи."""

    HEADER = 26
    ROW = 28
    NAME = 230
    ADDRESSES = 86
    CELL = 96

    def __init__(self, columns: list[str], rows: list[ServerRow], parent=None) -> None:
        super().__init__(parent)
        self._columns = list(columns)
        self._services = summarize_servers(rows, len(columns))
        self._hover = -1
        self._hint = HoverHint(self)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.HEADER + self.ROW * len(self._services))
        self._theme_refresh = ThemeRefreshBinding(self, lambda *_args, **_kwargs: self.update())
        set_state_text(self, f"DNS-серверы по способам связи: сервисов {len(self._services)}, адресов {len(rows)}")

    def services(self) -> list[ServiceSummary]:
        return list(self._services)

    def row_at(self, y: float) -> int:
        index = int((y - self.HEADER) // self.ROW) if y >= self.HEADER else -1
        return index if 0 <= index < len(self._services) else -1

    def hint(self, index: int) -> str:
        """Что ответил каждый адрес сервиса каждым способом связи."""
        service = self._services[index]
        lines = [service.name]
        for row in service.rows:
            cells = ", ".join(f"{title} — {cell or '—'}" for title, cell in zip(self._columns, row.cells))
            lines.append(f"{row.address}: {cells}")
        return "\n".join(lines)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        hover = self.row_at(event.position().y())
        if hover != self._hover:
            self._hover = hover
            self.update()
            self._hint.show(self.hint(hover) if hover >= 0 else "", event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = -1
        self._hint.hide()
        self.update()
        super().leaveEvent(event)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            return True
        return super().event(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        light = _is_light()
        text = QColor(0, 0, 0, 228) if light else QColor(255, 255, 255, 235)
        muted = QColor(0, 0, 0, 150) if light else QColor(255, 255, 255, 150)
        line = QColor(0, 0, 0, 20) if light else QColor(255, 255, 255, 18)
        hover = QColor(0, 0, 0, 14) if light else QColor(255, 255, 255, 14)
        font = QFont(self.font())
        font.setPixelSize(13)
        small = QFont(self.font())
        small.setPixelSize(12)
        metrics = QFontMetrics(font)
        left = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        center = int(Qt.AlignmentFlag.AlignCenter)

        painter.setFont(small)
        painter.setPen(muted)
        painter.drawText(QRectF(28, 0, self.NAME, self.HEADER), left, "Сервис")
        painter.drawText(QRectF(28 + self.NAME, 0, self.ADDRESSES, self.HEADER), left, "Адресов")
        x = 28 + self.NAME + self.ADDRESSES
        for title in self._columns:
            painter.drawText(QRectF(x, 0, self.CELL, self.HEADER), center, title)
            x += self.CELL

        for index, service in enumerate(self._services):
            top = self.HEADER + index * self.ROW
            painter.setPen(Qt.PenStyle.NoPen)
            if index == self._hover:
                painter.setBrush(hover)
                painter.drawRoundedRect(QRectF(0, top + 1, self.width(), self.ROW - 1), 5, 5)
            else:
                painter.setBrush(line)
                painter.drawRect(QRectF(0, top, self.width(), 1))
            brand = named_brand(service.name)
            if brand is not None:
                try:
                    from profile.ui.profile_icon import profile_icon_pixmap

                    icon = profile_icon_pixmap(brand.icon, color=readable_color(brand.color, light_theme=light), size=14)
                    painter.drawPixmap(6, int(top + (self.ROW - 14) / 2), 14, 14, icon)
                except Exception:
                    pass
            painter.setFont(font)
            painter.setPen(text)
            painter.drawText(
                QRectF(28, top, self.NAME - 8, self.ROW),
                left,
                metrics.elidedText(service.name, Qt.TextElideMode.ElideRight, self.NAME - 8),
            )
            painter.setPen(muted)
            painter.drawText(QRectF(28 + self.NAME, top, self.ADDRESSES, self.ROW), left, str(len(service.rows)))
            x = 28 + self.NAME + self.ADDRESSES
            painter.setFont(small)
            for ok, warn, fail in service.counts:
                state = count_state(ok, warn, fail)
                cell = QRectF(x + 14, top + 5, self.CELL - 28, self.ROW - 10)
                if state == NONE:
                    painter.setPen(muted)
                    painter.drawText(cell, center, "—")
                else:
                    color = QColor(_COLORS[state][1 if light else 0])
                    back = QColor(color)
                    back.setAlphaF(0.16)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(back)
                    painter.drawRoundedRect(cell, 4, 4)
                    painter.setPen(color)
                    painter.drawText(cell, center, f"{ok + warn}/{ok + warn + fail}")
                x += self.CELL
        painter.end()


def _is_light() -> bool:
    try:
        from ui.theme import get_theme_tokens

        return bool(get_theme_tokens().is_light)
    except Exception:
        return False


__all__ = ["ServerMatrix", "ServerRow", "ServiceSummary", "cell_state", "count_state", "parse_server_table", "summarize_servers"]
