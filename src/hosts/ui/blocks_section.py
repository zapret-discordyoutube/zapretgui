"""Раздел «Что сейчас в hosts»: всё содержимое файла по владельцам."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, PlainTextEdit, TransparentPushButton

from hosts.hosts_blocks import (
    BLOCK_ADOBE,
    BLOCK_MAX,
    BLOCK_ORDER,
    BLOCK_STATE_MEDIA,
    BLOCK_TELEGRAM,
    BLOCK_USER,
    BLOCK_ZAPRETGUI,
)
from hosts.page_snapshot import HostsPageSnapshot
from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_widgets import SettingsCard
from ui.widgets.aligned_switch import AlignedSwitchButton


_BLOCK_TEXTS = {
    BLOCK_ZAPRETGUI: ("ZapretGUI", "Управляется переключателями выше"),
    BLOCK_TELEGRAM: ("Telegram Proxy", "Прописывается и убирается кнопкой на странице Telegram Proxy"),
    BLOCK_MAX: ("Блокировка MAX", "Включается на странице управления Zapret"),
    BLOCK_STATE_MEDIA: ("Блокировка госСМИ", "Включается на странице управления Zapret"),
    BLOCK_ADOBE: ("Блокировка активации Adobe", "Переключатель справа, записывается по «Применить»"),
    BLOCK_USER: ("Ваши строки", "Записаны вручную или другими программами — программа их не трогает"),
}


class _BlockRow(QWidget):
    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        self.title_label = BodyLabel(self)
        self.desc_label = CaptionLabel(self)
        self.desc_label.setWordWrap(True)
        texts.addWidget(self.title_label)
        texts.addWidget(self.desc_label)
        layout.addLayout(texts, 1)
        self.count_label = CaptionLabel(self)
        layout.addWidget(self.count_label)
        self.show_button = TransparentPushButton(self)
        layout.addWidget(self.show_button)
        self.switch: AlignedSwitchButton | None = None
        if kind == BLOCK_ADOBE:
            self.switch = AlignedSwitchButton(self)
            layout.addWidget(self.switch)


class HostsBlocksSection(SettingsCard):
    """Показывает блоки файла. Менять здесь можно только Adobe (через черновик)."""

    adobe_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._tr: Callable[[str, str], str] = lambda _key, default: default
        self._snapshot: HostsPageSnapshot | None = None
        self._shown_kind: str | None = None
        self._rows: dict[str, _BlockRow] = {}

        self.state_label = CaptionLabel(self)
        self.state_label.setWordWrap(True)
        self.main_layout.addWidget(self.state_label)
        for kind in BLOCK_ORDER:
            row = _BlockRow(kind, self)
            row.show_button.clicked.connect(lambda _checked=False, k=kind: self._toggle_lines(k))
            if row.switch is not None:
                row.switch.checkedChanged.connect(self.adobe_toggled.emit)
            self._rows[kind] = row
            self.main_layout.addWidget(row)

        self.viewer = PlainTextEdit(self)
        self.viewer.setReadOnly(True)
        self.viewer.setLineWrapMode(PlainTextEdit.LineWrapMode.NoWrap)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.viewer.setFont(mono)
        self.viewer.setFixedHeight(200)
        self.viewer.hide()
        self.main_layout.addWidget(self.viewer)

    def set_translator(self, tr: Callable[[str, str], str]) -> None:
        self._tr = tr
        self._render()

    def set_snapshot(self, snapshot: HostsPageSnapshot, *, adobe_value: bool) -> None:
        self._snapshot = snapshot
        row = self._rows[BLOCK_ADOBE]
        if row.switch is not None and row.switch.isChecked() != bool(adobe_value):
            row.switch.blockSignals(True)
            row.switch.setChecked(bool(adobe_value))
            row.switch.blockSignals(False)
        self._render()

    def _toggle_lines(self, kind: str) -> None:
        self._shown_kind = None if self._shown_kind == kind else kind
        self._render()

    def _render(self) -> None:
        tr = self._tr
        snapshot = self._snapshot
        if snapshot is None:
            self.state_label.setText(tr("page.hosts.loading", "Загрузка…"))
            for row in self._rows.values():
                row.hide()
            self.viewer.hide()
            return

        if not snapshot.hosts_exists:
            state = tr("page.hosts.blocks.missing", "Файла hosts нет. Он появится при первой записи.")
        elif not snapshot.readable:
            state = tr("page.hosts.blocks.unreadable", "Файл hosts не удалось прочитать.")
        elif not snapshot.blocks:
            state = tr("page.hosts.blocks.empty", "В файле hosts нет ни одной записи.")
        else:
            state = tr(
                "page.hosts.blocks.hint",
                "Всё содержимое файла по владельцам. Программа меняет только свой блок и только по кнопке «Применить».",
            )
        self.state_label.setText(state)
        set_state_text(self.state_label, state)

        for kind, row in self._rows.items():
            block = snapshot.block(kind)
            count = block.count if block is not None else 0
            visible = count > 0 or kind == BLOCK_ADOBE
            row.setVisible(visible)
            if not visible:
                continue
            title, desc = _BLOCK_TEXTS[kind]
            row.title_label.setText(tr(f"page.hosts.block.{kind}.title", title))
            row.desc_label.setText(tr(f"page.hosts.block.{kind}.desc", desc))
            row.count_label.setText(tr("page.hosts.block.lines", "строк: {count}").format(count=count))
            row.show_button.setVisible(count > 0)
            row.show_button.setText(
                tr("page.hosts.block.hide", "Скрыть")
                if self._shown_kind == kind
                else tr("page.hosts.block.show", "Показать")
            )
            set_control_accessibility(
                row.show_button,
                name=f"{row.show_button.text()}: {row.title_label.text()}",
            )
            if row.switch is not None:
                set_control_accessibility(row.switch, name=row.title_label.text())

        block = snapshot.block(self._shown_kind) if self._shown_kind else None
        if block is None:
            self._shown_kind = None
            self.viewer.hide()
        else:
            self.viewer.setPlainText("\n".join(block.lines))
            self.viewer.show()


__all__ = ["HostsBlocksSection"]
