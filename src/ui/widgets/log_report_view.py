"""Страница с подробным логом или отчётом проверки, оформленная как редактор кода.

Отчёт открывается не окном поверх программы, а страницей на месте вкладки:
назад ведёт строка пути наверху. Текст показан в редакторе только для чтения:

- номера строк, подсветка разделов, результата, адресов и времени
  (``ui.code_editor.log_syntax``), выделение мышью;
- поиск по тексту (кнопка «Найти» или Ctrl+F) с метками совпадений справа;
- нейтральные метки над текстом: сколько строк с ошибками и предупреждениями — нажатие
  ведёт к следующей такой строке; названия разделов — нажатие ведёт к разделу;
- «Перенос строк» для длинных строк и «Скопировать всё» для поддержки.

Редактор занимает всю оставшуюся высоту окна и прокручивается сам, поэтому
у страницы второй полосы прокрутки нет.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QGuiApplication, QPainter
from PyQt6.QtWidgets import QAbstractButton, QAbstractScrollArea, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BreadcrumbBar,
    CaptionLabel,
    FlowLayout,
    FluentIcon,
    PushButton,
    SubtitleLabel,
    TogglePushButton,
    isDarkTheme,
)

from ui.accessibility import set_breadcrumb_accessibility, set_control_accessibility, set_state_text
from ui.code_editor.chunked_fill import ChunkedReadOnlyFill
from ui.code_editor.editor import CodeEditor, build_cursor_status_text
from ui.code_editor.find_bar import FindReplaceBar
from ui.code_editor.find_controller import FindController
from ui.code_editor.log_syntax import (
    KIND_FAIL,
    KIND_OK,
    KIND_WARN,
    LogSyntaxHighlighter,
    log_line_kind,
    log_outline,
    state_color,
)
from ui.fluent_widgets import set_tooltip
from ui.theme_refresh import ThemeRefreshBinding
from ui.widgets.stagger_float_in import float_in

_MIN_EDITOR_HEIGHT = 300
# Столько названий разделов помещается над текстом, не вытесняя его с экрана.
_MAX_SECTION_CHIPS = 14
_STATE_CAPTIONS = {KIND_FAIL: "Ошибки", KIND_WARN: "Предупреждения", KIND_OK: "Успешно"}
_STATE_ORDER = (KIND_FAIL, KIND_WARN, KIND_OK)
_HINT = "Ctrl+F — поиск · F3 — следующее совпадение · Ctrl+G — к строке · Ctrl+колесо — размер шрифта"


@dataclass(frozen=True, slots=True)
class LogReport:
    """Что показать на странице отчёта."""

    title: str
    text: str
    # Куда ведёт строка пути: название вкладки, с которой открыли отчёт.
    root_title: str
    description: str = ""
    empty_text: str = "Лог пока пуст."
    # Живой лог читают с конца: там последние события.
    scroll_to_end: bool = False


class Chip(QAbstractButton):
    """Нажимаемая метка на нейтральной подложке; ``dot`` — цветная точка перед текстом."""

    _DOT = 8

    def __init__(self, text: str, dot: QColor | None = None, parent=None) -> None:
        super().__init__(parent)
        self._dot_color = dot
        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def dot_color(self) -> QColor | None:
        return self._dot_color

    def set_dot_color(self, color: QColor | None) -> None:
        self._dot_color = color
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        extra = self._DOT + 7 if self._dot_color is not None else 0
        return QSize(QFontMetrics(self.font()).horizontalAdvance(self.text()) + 22 + extra, 26)

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        text = QColor(Qt.GlobalColor.white if isDarkTheme() else Qt.GlobalColor.black)
        fill = QColor(text)
        fill.setAlpha(34 if self.underMouse() or self.hasFocus() else 16)
        text.setAlpha(225)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(self.rect()), 6, 6)
        left = 11.0
        if self._dot_color is not None:
            painter.setBrush(self._dot_color)
            painter.drawEllipse(QRectF(left, (self.height() - self._DOT) / 2, self._DOT, self._DOT))
            left += self._DOT + 7
        painter.setPen(text)
        painter.drawText(
            QRectF(left, 0, self.width() - left - 11, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.text(),
        )
        painter.end()


class _ChipFlow(QWidget):
    """Метки в ряд с переносом на новую строку."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._flow = FlowLayout(self, needAni=False)
        self._flow.setContentsMargins(0, 0, 0, 0)
        self._flow.setHorizontalSpacing(6)
        self._flow.setVerticalSpacing(6)
        self._items: list[QWidget] = []

    def clear(self) -> None:
        self._flow.takeAllWidgets()
        for item in self._items:
            item.setParent(None)
            item.deleteLater()
        self._items = []
        self._sync_height()

    def add(self, widget: QWidget) -> None:
        self._flow.addWidget(widget)
        self._items.append(widget)
        self._sync_height()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_height()

    def _sync_height(self) -> None:
        height = self._flow.heightForWidth(self.width()) if self._items and self.width() > 0 else 0
        if height != self.minimumHeight() or height != self.maximumHeight():
            self.setFixedHeight(height)


class LogReportView(QWidget):
    """Отчёт на всю страницу: путь «вкладка → отчёт», метки и редактор с подсветкой."""

    closed = pyqtSignal()
    ROOT_KEY = "root"
    REPORT_KEY = "report"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._report: LogReport | None = None
        self._state_lines: dict[str, list[int]] = {}
        self._watched_viewport: QWidget | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)

        self.breadcrumb = BreadcrumbBar(self)
        self.breadcrumb.currentItemChanged.connect(self._on_breadcrumb)
        self._layout.addWidget(self.breadcrumb)

        self._header = QWidget(self)
        header = QHBoxLayout(self._header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title_label = SubtitleLabel("", self._header)
        titles.addWidget(self.title_label)
        self.description_label = CaptionLabel("", self._header)
        self.description_label.setWordWrap(True)
        titles.addWidget(self.description_label)
        header.addLayout(titles, 1)

        self.find_button = PushButton(FluentIcon.SEARCH, "Найти", self._header)
        set_tooltip(self.find_button, "Поиск по тексту отчёта (Ctrl+F).")
        set_control_accessibility(
            self.find_button, name="Найти в отчёте", description="Открывает строку поиска по тексту отчёта."
        )
        header.addWidget(self.find_button, 0, Qt.AlignmentFlag.AlignTop)
        self.wrap_button = TogglePushButton("Перенос строк", self._header)
        set_tooltip(self.wrap_button, "Переносить длинные строки по ширине окна, чтобы не листать вбок.")
        set_control_accessibility(
            self.wrap_button,
            name="Перенос строк",
            description="Переносит длинные строки по ширине окна вместо прокрутки вбок.",
        )
        header.addWidget(self.wrap_button, 0, Qt.AlignmentFlag.AlignTop)
        self.copy_button = PushButton(FluentIcon.COPY, "Скопировать всё", self._header)
        set_control_accessibility(
            self.copy_button,
            name="Скопировать весь отчёт",
            description="Кладёт весь текст отчёта в буфер обмена. Часть текста копируется выделением и Ctrl+C.",
        )
        header.addWidget(self.copy_button, 0, Qt.AlignmentFlag.AlignTop)
        self._layout.addWidget(self._header)

        self.chips = _ChipFlow(self)
        self._layout.addWidget(self.chips)
        self.state_chips: dict[str, Chip] = {}
        self.section_chips: list[Chip] = []

        self.find_bar = FindReplaceBar(self)
        self.find_bar.search_input.setPlaceholderText("Поиск по тексту отчёта")
        self.find_bar.setVisible(False)
        self.find_bar.installEventFilter(self)
        self._layout.addWidget(self.find_bar)

        self.editor = CodeEditor(self, highlighter_factory=lambda document: LogSyntaxHighlighter(document))
        self.editor.setReadOnly(True)
        self.editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.editor.setFixedHeight(_MIN_EDITOR_HEIGHT)
        # До контроллера поиска: первый Escape закрывает поиск, и только следующий — страницу.
        self.editor.escapePressed.connect(self._on_editor_escape)
        self.find_controller = FindController(self.editor, self.find_bar, parent=self)
        self._fill = ChunkedReadOnlyFill(self.editor)
        self.editor.cursorStatusChanged.connect(self._on_cursor_status)
        self._layout.addWidget(self.editor)

        self.status_label = CaptionLabel("", self)
        self._layout.addWidget(self.status_label)

        self.find_button.clicked.connect(lambda: self.find_controller.open_panel(False))
        self.wrap_button.toggled.connect(self.editor.set_word_wrap)
        self.copy_button.clicked.connect(self._copy)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)

    # ── что показано ────────────────────────────────────────

    def report(self) -> LogReport | None:
        return self._report

    def show_report(self, report: LogReport, *, animate: bool = True) -> None:
        self._report = report
        self.breadcrumb.blockSignals(True)
        try:
            self.breadcrumb.clear()
            self.breadcrumb.addItem(self.ROOT_KEY, report.root_title)
            self.breadcrumb.addItem(self.REPORT_KEY, report.title)
            set_breadcrumb_accessibility(self.breadcrumb, [report.root_title, report.title])
        finally:
            self.breadcrumb.blockSignals(False)
        self.title_label.setText(report.title)
        self.description_label.setText(report.description)
        self.description_label.setVisible(bool(report.description))

        text = str(report.text or "")
        if not self.find_bar.isHidden():
            self.find_controller.close_panel()
        self._fill.set_text(text or report.empty_text)
        if report.scroll_to_end and text:
            # Конец нужен сразу, а не когда текст допишется порциями.
            self._fill.finish_now()
            self.editor.goto_line(self.editor.blockCount())
        else:
            self.editor.goto_line(1)
        self.copy_button.setEnabled(bool(text))
        self.copy_button.setText("Скопировать всё")
        self.find_button.setEnabled(bool(text))

        self._build_chips(text)
        set_control_accessibility(self.editor, name=report.title, description=f"{report.description} {_HINT}".strip())
        set_state_text(self, f"{report.title}: строк {text.count(chr(10)) + 1 if text else 0}")
        self._on_cursor_status(self.editor.cursor_status())
        if animate:
            for order, block in enumerate((self._header, self.chips, self.editor)):
                float_in(block, delay_ms=order * 60)
        self._schedule_fit()

    def _build_chips(self, text: str) -> None:
        self.chips.clear()
        self.state_chips = {}
        self.section_chips = []
        self._state_lines = {kind: [] for kind in _STATE_ORDER}
        for number, line in enumerate(text.split("\n"), start=1):
            kind = log_line_kind(line)
            if kind in self._state_lines:
                self._state_lines[kind].append(number)
        dark = isDarkTheme()
        for kind in _STATE_ORDER:
            lines = self._state_lines[kind]
            if not lines:
                continue
            chip = Chip(f"{_STATE_CAPTIONS[kind]}: {len(lines)}", state_color(kind, dark=dark), self.chips)
            description = f"Строк такого вида: {len(lines)}. Нажатие ведёт к следующей из них."
            set_tooltip(chip, description)
            set_control_accessibility(chip, name=f"{_STATE_CAPTIONS[kind]}: {len(lines)}", description=description)
            chip.clicked.connect(lambda _checked=False, key=kind: self.goto_next_state(key))
            self.chips.add(chip)
            self.state_chips[kind] = chip
        for number, title in log_outline(text)[:_MAX_SECTION_CHIPS]:
            chip = Chip(title, None, self.chips)
            description = f"Перейти к разделу «{title}», строка {number}."
            set_tooltip(chip, description)
            set_control_accessibility(chip, name=f"Раздел: {title}", description=description)
            chip.clicked.connect(lambda _checked=False, line=number: self.goto_line(line))
            self.chips.add(chip)
            self.section_chips.append(chip)
        self.chips.setVisible(bool(self.state_chips or self.section_chips))

    # ── поведение ───────────────────────────────────────────

    def goto_line(self, line: int) -> None:
        # Раздел может лежать в ещё не дописанной части длинного текста.
        if line > self.editor.blockCount():
            self._fill.finish_now()
        self.editor.goto_line(line)
        self.editor.setFocus()

    def goto_next_state(self, kind: str) -> int:
        """Ведёт к следующей строке этого вида после курсора; с конца — снова к первой."""
        lines = self._state_lines.get(kind) or []
        if not lines:
            return 0
        current = self.editor.cursor_status().line
        target = next((line for line in lines if line > current), lines[0])
        self.goto_line(target)
        return target

    def _on_cursor_status(self, status) -> None:
        self.status_label.setText(f"{build_cursor_status_text(status)}  ·  {_HINT}")

    def _on_editor_escape(self) -> None:
        if self.find_bar.isHidden():
            self.closed.emit()

    def _on_breadcrumb(self, key: str) -> None:
        if key == self.ROOT_KEY:
            self.closed.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.closed.emit()
            return
        super().keyPressEvent(event)

    def _copy(self) -> None:
        if self._report is None:
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._report.text)
        self.copy_button.setText("Скопировано")

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = tokens, force
        dark = isDarkTheme()
        for kind, chip in self.state_chips.items():
            chip.set_dot_color(state_color(kind, dark=dark))
        for chip in self.section_chips:
            chip.update()

    # ── высота редактора ────────────────────────────────────

    def _scroll_area(self) -> QAbstractScrollArea | None:
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    def _schedule_fit(self) -> None:
        self._fit_editor()
        # Раскладка меток и строки поиска досчитывается после первого прохода событий.
        QTimer.singleShot(0, self._fit_editor)

    def _fit_editor(self) -> None:
        """Редактор занимает всё место до нижнего края окна и прокручивается сам."""
        area = self._scroll_area()
        if area is None or area.widget() is None or not self.isVisible():
            return
        viewport = area.viewport()
        if self._watched_viewport is not viewport:
            if self._watched_viewport is not None:
                self._watched_viewport.removeEventFilter(self)
            self._watched_viewport = viewport
            viewport.installEventFilter(self)
        top = self.editor.mapTo(area.widget(), QPoint(0, 0)).y()
        below = self.status_label.sizeHint().height() + self._layout.spacing() + 12
        height = max(_MIN_EDITOR_HEIGHT, viewport.height() - top - below)
        if height != self.editor.height():
            self.editor.setFixedHeight(height)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        kind = event.type()
        if watched is self._watched_viewport and kind == QEvent.Type.Resize:
            QTimer.singleShot(0, self._fit_editor)
        elif watched is self.find_bar and kind in (QEvent.Type.Show, QEvent.Type.Hide):
            QTimer.singleShot(0, self._fit_editor)
        return False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._schedule_fit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_editor)


__all__ = ["Chip", "LogReport", "LogReportView"]
