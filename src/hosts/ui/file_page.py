# hosts/ui/file_page.py
"""Страница «Файл hosts»: весь файл в редакторе, строки раскрашены по владельцу.

Открывается из меню «☰» страницы Hosts, путь в хлебных крошках:
«Редактор hosts → Файл hosts». Текст читается и записывается в фоне,
«Сохранить» пишет весь файл как есть. Защиту «только чтение» страница
сама не снимает.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget
from qfluentwidgets import (
    BodyLabel,
    BreadcrumbBar,
    CaptionLabel,
    FluentIcon,
    InfoBar,
    PrimaryPushButton,
    PushButton,
)

from app.ui_texts import tr as tr_catalog
from hosts.hosts_blocks import (
    BLOCK_ADOBE,
    BLOCK_MAX,
    BLOCK_ORDER,
    BLOCK_STATE_MEDIA,
    BLOCK_TELEGRAM,
    BLOCK_USER,
    BLOCK_ZAPRETGUI,
    parse_hosts_blocks,
)
from hosts.ui.hosts_syntax import HostsSyntaxHighlighter, owner_color
from ui.accessibility import set_breadcrumb_accessibility, set_control_accessibility, set_state_text
from ui.code_editor.editor import CodeEditor
from ui.code_editor.find_bar import FindReplaceBar
from ui.code_editor.find_controller import FindController
from ui.fluent_widgets import SettingsCard
from ui.one_shot_worker_runtime import OneShotWorkerRuntime
from ui.pages.base_page import BasePage
from ui.theme import get_cached_qta_pixmap
from ui.theme_semantic import get_semantic_palette


_OWNER_TITLES = {
    BLOCK_ZAPRETGUI: ("page.hosts_file.owner.zapretgui", "ZapretGUI"),
    BLOCK_TELEGRAM: ("page.hosts_file.owner.telegram", "Telegram Proxy"),
    BLOCK_MAX: ("page.hosts_file.owner.max", "Блокировка MAX"),
    BLOCK_STATE_MEDIA: ("page.hosts_file.owner.state_media", "Блокировка госСМИ"),
    BLOCK_ADOBE: ("page.hosts_file.owner.adobe", "Adobe"),
    BLOCK_USER: ("page.hosts_file.owner.user", "Ваши строки"),
}


class _LegendChip(QWidget):
    """Цветная точка, имя владельца и число его строк."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.dot = QLabel("●", self)
        layout.addWidget(self.dot)
        self.label = CaptionLabel(self)
        layout.addWidget(self.label)

    def set_state(self, *, title: str, count: int, color) -> None:
        text = f"{title}: {count}"
        self.label.setText(text)
        self.dot.setStyleSheet(f"color: {color.name()}; font-size: 12px;")
        set_state_text(self, text)


class HostsFilePage(BasePage):
    """Весь файл hosts с раскраской по владельцам и кнопкой «Сохранить»."""

    def __init__(self, parent=None, *, deps):
        super().__init__(
            "Файл hosts",
            "",
            parent,
            title_key="page.hosts_file.title",
        )
        self._hosts = deps.hosts_feature
        self._open_hosts_page = deps.open_hosts_page
        self._cleanup_in_progress = False
        self._loaded_text: str | None = None
        self._read_only = False
        self._readable = True
        self._path = ""
        self._saving = False
        self._load_runtime = OneShotWorkerRuntime()
        self._save_runtime = OneShotWorkerRuntime()
        # Счётчики строк по владельцам — по тексту файла (загруженному или
        # сохранённому), а не на каждое нажатие: hosts бывает огромным.
        self._legend_counts: dict[str, int] = {}
        self._build_ui()
        self._retranslate()

    def _tr(self, key: str, default: str) -> str:
        return tr_catalog(key, language=self._ui_language, default=default)

    # ── построение ───────────────────────────────────────────

    def _build_ui(self) -> None:
        # Страница не прокручивается целиком: прокручивается только редактор.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalScrollBar().hide()
        # Заголовок заменяют хлебные крошки «Редактор hosts → Файл hosts».
        if self.title_label is not None:
            self.title_label.hide()
        if self.subtitle_label is not None:
            self.subtitle_label.hide()

        self.breadcrumb = BreadcrumbBar(self.content)
        self.breadcrumb.currentItemChanged.connect(self._on_breadcrumb_item_changed)
        self.add_widget(self.breadcrumb)

        # Предупреждение о защите или нет доступа (скрыто, пока всё хорошо).
        self.notice_card = SettingsCard(parent=self.content)
        notice_row = QHBoxLayout()
        notice_row.setSpacing(10)
        self.notice_icon = QLabel(self.notice_card)
        self.notice_icon.setFixedSize(20, 20)
        notice_row.addWidget(self.notice_icon, 0, Qt.AlignmentFlag.AlignTop)
        self.notice_label = BodyLabel(self.notice_card)
        self.notice_label.setWordWrap(True)
        notice_row.addWidget(self.notice_label, 1)
        self.notice_card.main_layout.addLayout(notice_row)
        self.notice_card.hide()
        self.add_widget(self.notice_card)

        # Цвета владельцев со счётчиками и две кнопки.
        top_row = QWidget(self.content)
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(16)
        self.legend: dict[str, _LegendChip] = {}
        for kind in BLOCK_ORDER:
            chip = _LegendChip(top_row)
            self.legend[kind] = chip
            top_layout.addWidget(chip)
        top_layout.addStretch(1)
        self.revert_button = PushButton(FluentIcon.CANCEL, "", top_row)
        self.revert_button.clicked.connect(self._revert)
        top_layout.addWidget(self.revert_button)
        self.save_button = PrimaryPushButton(FluentIcon.SAVE, "", top_row)
        self.save_button.clicked.connect(self._save)
        top_layout.addWidget(self.save_button)
        self.add_widget(top_row)

        self.hint_label = CaptionLabel(self.content)
        self.hint_label.setWordWrap(True)
        self.add_widget(self.hint_label)

        self.find_bar = FindReplaceBar(self.content)
        self.add_widget(self.find_bar)
        self.editor = CodeEditor(
            self.content,
            highlighter_factory=lambda document: HostsSyntaxHighlighter(document),
        )
        self.editor.setLineWrapMode(CodeEditor.LineWrapMode.NoWrap)
        # Признак правок — флаг документа «изменён»: весь текст не читаем.
        self.editor.document().modificationChanged.connect(lambda _modified: self._render_buttons())
        self.find_controller = FindController(self.editor, self.find_bar, parent=self)
        self.add_widget(self.editor, 1)

        self.path_label = CaptionLabel(self.content)
        self.add_widget(self.path_label)

    def _retranslate(self) -> None:
        tr = self._tr
        items = (tr("nav.page.hosts", "Редактор hosts"), tr("page.hosts_file.title", "Файл hosts"))
        self.breadcrumb.blockSignals(True)
        try:
            self.breadcrumb.clear()
            self.breadcrumb.addItem("hosts", items[0])
            self.breadcrumb.addItem("file", items[1])
            set_breadcrumb_accessibility(self.breadcrumb, items)
        finally:
            self.breadcrumb.blockSignals(False)
        self.save_button.setText(tr("page.hosts_file.save", "Сохранить"))
        self.revert_button.setText(tr("page.hosts_file.revert", "Отменить правки"))
        set_control_accessibility(self.save_button, name=tr("page.hosts_file.save", "Сохранить"))
        set_control_accessibility(self.revert_button, name=tr("page.hosts_file.revert", "Отменить правки"))
        self.hint_label.setText(
            tr(
                "page.hosts_file.hint",
                "Строки раскрашены по владельцу. Блок ZapretGUI можно править, но при следующем «Применить» "
                "на странице Hosts он перепишется по переключателям. Ctrl+F — поиск.",
            )
        )
        editor_name = tr("page.hosts_file.editor", "Текст файла hosts")
        set_control_accessibility(
            self.editor,
            name=editor_name,
            description=tr(
                "page.hosts_file.editor.description",
                "Весь файл hosts. Ctrl+F — поиск, Ctrl+H — замена. Изменения записываются кнопкой «Сохранить».",
            ),
        )
        set_state_text(self.editor, editor_name)
        search_name = tr("page.hosts_file.search", "Поиск по файлу hosts")
        self.find_bar.search_input.setPlaceholderText(search_name)
        set_control_accessibility(self.find_bar.search_input, name=search_name)
        self._render()

    def set_ui_language(self, language: str) -> None:
        super().set_ui_language(language)
        self._retranslate()

    def _apply_page_theme(self, tokens=None, force: bool = False) -> None:
        _ = (tokens, force)
        self._render_legend()
        self._render_notice()

    def _on_breadcrumb_item_changed(self, key: str) -> None:
        # Клик по крошке обрезал путь: восстанавливаем его для следующего захода.
        self._retranslate()
        if key == "hosts":
            self._open_hosts_page()

    # ── жизненный цикл ───────────────────────────────────────

    def on_page_activated(self) -> None:
        # Несохранённые правки не затираем: перечитываем только чистый текст.
        if not self.is_dirty():
            self._request_load()

    def cleanup(self) -> None:
        self._cleanup_in_progress = True
        for runtime in (self._load_runtime, self._save_runtime):
            try:
                runtime.stop(blocking=False, warning_prefix="Hosts file worker")
                runtime.cancel()
            except Exception:
                pass
        super().cleanup()

    # ── состояние ────────────────────────────────────────────

    def is_dirty(self) -> bool:
        return self._loaded_text is not None and self.editor.document().isModified()

    def _current_editor_text(self) -> str:
        """Весь текст редактора — читается только при сохранении."""
        return self.editor.toPlainText()

    def _show_text(self, text: str) -> None:
        self.editor.setPlainText(text)
        self.editor.document().setModified(False)

    # ── фон: чтение и запись ─────────────────────────────────

    def _request_load(self) -> None:
        if self._cleanup_in_progress or self._load_runtime.is_running():
            return
        self._load_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._hosts.create_file_text_worker(request_id, self),
            on_loaded=self._on_loaded,
            on_failed=self._on_load_failed,
        )

    def _on_loaded(self, request_id: int, result) -> None:
        if not self._load_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        self.apply_file_text(result)

    def apply_file_text(self, result) -> None:
        """Показывает прочитанный файл (если нет несохранённых правок)."""
        if self.is_dirty():
            return
        text = str(getattr(result, "text", "") or "")
        self._read_only = bool(getattr(result, "read_only", False))
        self._readable = bool(getattr(result, "readable", True))
        self._path = str(getattr(result, "path", "") or "")
        changed = text != self._loaded_text
        self._loaded_text = text
        if changed:
            self._show_text(text)
            self._legend_counts = _owner_counts(text)
        self._render()

    def _on_load_failed(self, request_id: int, error: str) -> None:
        if not self._load_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        InfoBar.error(
            title=self._tr("page.hosts_file.load_failed", "Не удалось прочитать hosts"),
            content=str(error or ""),
            duration=8000,
            parent=self.window(),
        )

    def _save(self) -> None:
        if self._saving or not self.is_dirty():
            return
        text = self._current_editor_text()
        self._saving = True
        self._render_buttons()
        managed_changed = _managed_lines(text) != _managed_lines(self._loaded_text or "")
        self._save_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._hosts.create_file_save_worker(request_id, text, self),
            on_loaded=lambda request_id, result: self._on_saved(request_id, result, text, managed_changed),
            on_failed=lambda request_id, error: self._on_save_failed(request_id, error),
        )

    def _on_saved(self, request_id: int, result, text: str, managed_changed: bool) -> None:
        if not self._save_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        self._saving = False
        if not getattr(result, "success", False):
            self._on_save_failed(request_id, str(getattr(result, "message", "") or ""))
            return
        self._loaded_text = text
        self.editor.document().setModified(False)
        self._legend_counts = _owner_counts(text)
        self._render()
        if managed_changed:
            InfoBar.warning(
                title=self._tr("page.hosts_file.saved", "Сохранено"),
                content=self._tr(
                    "page.hosts_file.saved.managed",
                    "Вы поменяли блок ZapretGUI вручную: при следующем «Применить» на странице Hosts он перепишется.",
                ),
                duration=7000,
                parent=self.window(),
            )
        else:
            InfoBar.success(
                title=self._tr("page.hosts_file.saved", "Сохранено"),
                content=self._tr("page.hosts_file.saved.content", "Перезапустите браузер, чтобы изменения заработали."),
                duration=5000,
                parent=self.window(),
            )

    def _on_save_failed(self, request_id: int, error: str) -> None:
        if not self._save_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        self._saving = False
        self._render_buttons()
        InfoBar.error(
            title=self._tr("page.hosts_file.save_failed", "Не удалось сохранить hosts"),
            content=str(error or ""),
            duration=8000,
            parent=self.window(),
        )

    def _revert(self) -> None:
        if self._saving or self._loaded_text is None:
            return
        self._show_text(self._loaded_text)
        self._render()

    # ── отрисовка ────────────────────────────────────────────

    def _render(self) -> None:
        self._render_notice()
        self._render_legend()
        self._render_buttons()
        self.path_label.setText(self._path)
        self.path_label.setVisible(bool(self._path))

    def _render_notice(self) -> None:
        text = ""
        if self._loaded_text is not None and not self._readable:
            text = self._tr(
                "page.hosts_file.notice.no_access",
                "Нет доступа к файлу hosts. Часто его блокирует антивирус. Восстановить права можно в меню страницы Hosts.",
            )
        elif self._read_only:
            text = self._tr(
                "page.hosts_file.notice.read_only",
                "Файл защищён от записи (стоит «только чтение»). Сохранить не получится, пока защита стоит — снять её можно в меню страницы Hosts.",
            )
        self.notice_card.setVisible(bool(text))
        if text:
            self.notice_label.setText(text)
            set_state_text(self.notice_label, text)
            self.notice_icon.setPixmap(
                get_cached_qta_pixmap("fa5s.lock", color=get_semantic_palette().warning, size=18)
            )

    def _render_legend(self) -> None:
        counts = self._legend_counts
        theme = self.editor.theme_colors()
        for kind, chip in self.legend.items():
            key, default = _OWNER_TITLES[kind]
            chip.set_state(title=self._tr(key, default), count=counts.get(kind, 0), color=owner_color(kind, theme))
            # Пустые блоки не показываем, кроме ваших строк и ZapretGUI.
            chip.setVisible(bool(counts.get(kind)) or kind in (BLOCK_ZAPRETGUI, BLOCK_USER))

    def _render_buttons(self) -> None:
        dirty = self.is_dirty()
        self.save_button.setEnabled(dirty and not self._saving and not self._read_only)
        self.revert_button.setEnabled(dirty and not self._saving)
        self.editor.setReadOnly(self._saving)


def _owner_counts(text: str) -> dict[str, int]:
    return {block.kind: block.count for block in parse_hosts_blocks(text)}


def _managed_lines(text: str) -> tuple[str, ...]:
    for block in parse_hosts_blocks(text):
        if block.kind == BLOCK_ZAPRETGUI:
            return block.lines
    return ()


__all__ = ["HostsFilePage"]
