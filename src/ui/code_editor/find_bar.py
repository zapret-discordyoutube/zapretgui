"""Панель поиска и замены над редактором."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    LineEdit,
    PushButton,
    SearchLineEdit,
    TogglePushButton,
    TransparentToolButton,
)

from ui.accessibility import (
    remove_line_edit_buttons_from_tab_order,
    set_control_accessibility,
    set_state_text,
)
from ui.code_editor.find_engine import SearchOptions
from ui.fluent_widgets import set_tooltip


def _configure_toggle(button, *, name: str, description: str) -> None:
    button.setCheckable(True)
    button.setFixedHeight(30)
    button.setMinimumWidth(44)
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    set_control_accessibility(button, name=name, description=description)
    set_state_text(button, name)
    set_tooltip(button, description)


class FindReplaceBar(QWidget):
    """Строка поиска (Ctrl+F) с раскрывающейся строкой замены (Ctrl+H)."""

    searchChanged = pyqtSignal(str)
    findRequested = pyqtSignal(bool)
    replaceRequested = pyqtSignal()
    replaceAllRequested = pyqtSignal()
    optionsChanged = pyqtSignal()
    closeRequested = pyqtSignal()
    focusEditorRequested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("noDrag", True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        find_row = QWidget(self)
        find_layout = QHBoxLayout(find_row)
        find_layout.setContentsMargins(0, 0, 0, 0)
        find_layout.setSpacing(6)

        self.search_input = SearchLineEdit(find_row)
        self.search_input.setPlaceholderText("Поиск по тексту пресета")
        self.search_input.setClearButtonEnabled(True)
        remove_line_edit_buttons_from_tab_order(self.search_input)
        self.search_input.setFixedHeight(34)
        self.search_input.setMinimumWidth(320)
        self.search_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.search_input.setProperty("noDrag", True)
        self.search_input.textChanged.connect(self.searchChanged)
        self.search_input.installEventFilter(self)
        find_layout.addWidget(self.search_input, 1)

        self.caseButton = TogglePushButton("Aa", find_row)
        _configure_toggle(
            self.caseButton,
            name="Учитывать регистр",
            description="Искать с учётом регистра букв.",
        )
        self.caseButton.toggled.connect(self._on_option_toggled)
        find_layout.addWidget(self.caseButton)

        self.wordButton = TogglePushButton("Сл", find_row)
        _configure_toggle(
            self.wordButton,
            name="Только слово целиком",
            description="Находить только совпадения целым словом.",
        )
        self.wordButton.toggled.connect(self._on_option_toggled)
        find_layout.addWidget(self.wordButton)

        self.regexButton = TogglePushButton(".*", find_row)
        _configure_toggle(
            self.regexButton,
            name="Регулярное выражение",
            description="Трактовать запрос как регулярное выражение.",
        )
        self.regexButton.toggled.connect(self._on_option_toggled)
        find_layout.addWidget(self.regexButton)

        self.counterLabel = CaptionLabel("", find_row)
        self.counterLabel.setMinimumWidth(96)
        self.counterLabel.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        set_control_accessibility(
            self.counterLabel,
            name="Счётчик совпадений",
            description="Номер текущего совпадения и общее количество найденных.",
        )
        find_layout.addWidget(self.counterLabel)

        self.previousButton = TransparentToolButton(FluentIcon.UP, find_row)
        set_control_accessibility(
            self.previousButton,
            name="Предыдущее совпадение",
            description="Переходит к предыдущему совпадению (Shift+F3).",
        )
        set_state_text(self.previousButton, "Предыдущее совпадение")
        set_tooltip(self.previousButton, "Предыдущее совпадение (Shift+F3)")
        self.previousButton.clicked.connect(lambda: self.findRequested.emit(True))
        find_layout.addWidget(self.previousButton)

        self.nextButton = TransparentToolButton(FluentIcon.DOWN, find_row)
        set_control_accessibility(
            self.nextButton,
            name="Следующее совпадение",
            description="Переходит к следующему совпадению (F3).",
        )
        set_state_text(self.nextButton, "Следующее совпадение")
        set_tooltip(self.nextButton, "Следующее совпадение (F3)")
        self.nextButton.clicked.connect(lambda: self.findRequested.emit(False))
        find_layout.addWidget(self.nextButton)

        self.closeButton = TransparentToolButton(FluentIcon.CLOSE, find_row)
        set_control_accessibility(
            self.closeButton,
            name="Закрыть поиск",
            description="Закрывает панель поиска и снимает подсветку (Esc).",
        )
        set_state_text(self.closeButton, "Закрыть поиск")
        set_tooltip(self.closeButton, "Закрыть поиск (Esc)")
        self.closeButton.clicked.connect(self.closeRequested)
        find_layout.addWidget(self.closeButton)

        root.addWidget(find_row)

        self.replaceRow = QWidget(self)
        replace_layout = QHBoxLayout(self.replaceRow)
        replace_layout.setContentsMargins(0, 0, 0, 0)
        replace_layout.setSpacing(6)

        self.replace_input = LineEdit(self.replaceRow)
        self.replace_input.setPlaceholderText("Заменить на")
        self.replace_input.setClearButtonEnabled(True)
        remove_line_edit_buttons_from_tab_order(self.replace_input)
        self.replace_input.setFixedHeight(34)
        self.replace_input.setProperty("noDrag", True)
        set_control_accessibility(
            self.replace_input,
            name="Текст замены",
            description=(
                "Введите текст, на который нужно заменить найденное. "
                "В режиме регулярного выражения доступны ссылки на группы вида \\1."
            ),
        )
        set_state_text(self.replace_input, "Текст замены")
        self.replace_input.installEventFilter(self)
        replace_layout.addWidget(self.replace_input, 1)

        self.replaceButton = PushButton("Заменить", self.replaceRow)
        set_control_accessibility(
            self.replaceButton,
            name="Заменить совпадение",
            description="Заменяет текущее совпадение и переходит к следующему.",
        )
        set_state_text(self.replaceButton, "Заменить совпадение")
        self.replaceButton.clicked.connect(self.replaceRequested)
        replace_layout.addWidget(self.replaceButton)

        self.replaceAllButton = PushButton("Заменить всё", self.replaceRow)
        set_control_accessibility(
            self.replaceAllButton,
            name="Заменить все совпадения",
            description="Заменяет все совпадения одной операцией — отменяется одним Ctrl+Z.",
        )
        set_state_text(self.replaceAllButton, "Заменить все совпадения")
        self.replaceAllButton.clicked.connect(self.replaceAllRequested)
        replace_layout.addWidget(self.replaceAllButton)

        root.addWidget(self.replaceRow)
        self.replaceRow.setVisible(False)

    # ------------------------------------------------------------------ API

    def options(self) -> SearchOptions:
        return SearchOptions(
            case_sensitive=self.caseButton.isChecked(),
            whole_word=self.wordButton.isChecked(),
            regex=self.regexButton.isChecked(),
        )

    def query(self) -> str:
        return str(self.search_input.text() or "")

    def replacement(self) -> str:
        return str(self.replace_input.text() or "")

    def is_replace_visible(self) -> bool:
        # isHidden(), а не isVisible(): состояние строки замены не должно
        # зависеть от того, показано ли уже окно со страницей.
        return not self.replaceRow.isHidden()

    def set_replace_visible(self, visible: bool) -> None:
        self.replaceRow.setVisible(bool(visible))

    def set_counter_text(self, text: str) -> None:
        value = str(text or "")
        self.counterLabel.setText(value)
        set_state_text(self.counterLabel, value or "Счётчик совпадений")

    def set_error(self, has_error: bool) -> None:
        self.search_input.setProperty("codeEditorSearchError", bool(has_error))
        try:
            self.search_input.style().unpolish(self.search_input)
            self.search_input.style().polish(self.search_input)
        except Exception:
            pass

    def focus_search(self, *, select_all: bool = True) -> None:
        self.search_input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        if select_all:
            self.search_input.selectAll()

    def focus_replace(self) -> None:
        self.replace_input.setFocus(Qt.FocusReason.ShortcutFocusReason)

    # --------------------------------------------------------------- события

    def _on_option_toggled(self, _checked: bool) -> None:
        self.optionsChanged.emit()

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            modifiers = event.modifiers()
            shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
            if obj is self.search_input:
                if key == Qt.Key.Key_Down:
                    self.focusEditorRequested.emit()
                    event.accept()
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self.findRequested.emit(shift)
                    event.accept()
                    return True
            elif obj is self.replace_input and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.replaceRequested.emit()
                event.accept()
                return True
            if key == Qt.Key.Key_F3:
                self.findRequested.emit(shift)
                event.accept()
                return True
            if key == Qt.Key.Key_Escape:
                self.closeRequested.emit()
                event.accept()
                return True
        return super().eventFilter(obj, event)
