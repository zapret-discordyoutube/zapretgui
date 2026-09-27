"""Диалог имени своего фейка."""

from __future__ import annotations

from qfluentwidgets import BodyLabel, CaptionLabel, LineEdit, SubtitleLabel

from ui.accessibility import remove_line_edit_buttons_from_tab_order, set_control_accessibility
from ui.fluent_dialog import MessageBoxBase
from ui.fluent_widgets import style_semantic_caption_label


class AddUserFakeDialog(MessageBoxBase):
    """Имя и описание нового своего фейка с проверкой имени на лету.

    ``rules`` — правила имён из данных страницы (метод ``problem(name)``
    возвращает причину отказа или пустую строку, ``suggest(file_name)`` —
    свободное имя по имени файла). Диалог сам ничего не читает с диска.
    """

    def __init__(self, *, rules, file_name: str, parent=None):
        if parent is not None and not parent.isWindow():
            parent = parent.window()
        super().__init__(parent)
        self._rules = rules

        self.titleLabel = SubtitleLabel("Свой фейк", self.widget)
        self.subtitleLabel = BodyLabel(
            f"Файл: {file_name}\n"
            "Имя нужно, чтобы стратегия могла сослаться на фейк (blob=имя), "
            "а пресет — объявить его строкой --blob=.",
            self.widget,
        )
        self.subtitleLabel.setWordWrap(True)

        name_label = BodyLabel("Имя", self.widget)
        self.nameEdit = LineEdit(self.widget)
        self.nameEdit.setPlaceholderText("Например: tls_my_site")
        self.nameEdit.setClearButtonEnabled(True)

        description_label = BodyLabel("Описание (необязательно)", self.widget)
        self.descriptionEdit = LineEdit(self.widget)
        self.descriptionEdit.setPlaceholderText("Например: ClientHello для моего сайта")
        self.descriptionEdit.setMaxLength(200)

        self.warningLabel = CaptionLabel("", self.widget)
        style_semantic_caption_label(self.warningLabel, tone="error")
        self.warningLabel.hide()

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.subtitleLabel)
        self.viewLayout.addWidget(name_label)
        self.viewLayout.addWidget(self.nameEdit)
        self.viewLayout.addWidget(description_label)
        self.viewLayout.addWidget(self.descriptionEdit)
        self.viewLayout.addWidget(self.warningLabel)

        self.yesButton.setText("Добавить")
        self.cancelButton.setText("Отмена")
        self.widget.setMinimumWidth(440)

        set_control_accessibility(
            self.nameEdit,
            name="Имя своего фейка",
            description="Латинские буквы, цифры и знак подчёркивания; не с цифры в начале.",
        )
        set_control_accessibility(self.descriptionEdit, name="Описание своего фейка")
        remove_line_edit_buttons_from_tab_order(self.nameEdit)

        self.nameEdit.textChanged.connect(self._refresh_validation)
        suggested = ""
        try:
            suggested = str(rules.suggest(file_name) or "")
        except Exception:
            suggested = ""
        self.nameEdit.setText(suggested)
        self._refresh_validation(self.nameEdit.text())

    def _problem(self, text: str) -> str:
        try:
            return str(self._rules.problem(text) or "")
        except Exception as exc:
            return str(exc)

    def _refresh_validation(self, text: str) -> None:
        problem = self._problem(text)
        self.warningLabel.setText(problem)
        self.warningLabel.setVisible(bool(problem) and bool(str(text or "").strip()))
        self.yesButton.setEnabled(not problem)

    def validate(self) -> bool:
        problem = self._problem(self.nameEdit.text())
        if problem:
            self.warningLabel.setText(problem)
            self.warningLabel.show()
            return False
        return True

    def fake_name(self) -> str:
        return self.nameEdit.text().strip()

    def fake_description(self) -> str:
        return self.descriptionEdit.text().strip()


__all__ = ["AddUserFakeDialog"]
