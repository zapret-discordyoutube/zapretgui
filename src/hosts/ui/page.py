# hosts/ui/page.py
"""Страница Hosts: сервисы через системный файл hosts.

Как это работает:
- при открытии страница сразу рисует готовый снимок из памяти (его заранее
  собирает прогрев при запуске), а фоновая задача проверяет, не изменился ли
  файл, и тихо обновляет список;
- щелчки меняют только черновик: файл hosts не трогается;
- панель внизу показывает, что изменится, и одной кнопкой «Применить»
  записывает всё за один раз.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget
from qfluentwidgets import (
    Action,
    BodyLabel,
    CaptionLabel,
    ComboBox,
    InfoBar,
    PushButton,
    RoundMenu,
    SearchLineEdit,
    SegmentedWidget,
    TransparentPushButton,
)

from app.ui_texts import tr as tr_catalog
from hosts.draft import MIXED, HostsDraft
from hosts.hosts_blocks import BLOCK_ZAPRETGUI
from hosts.page_snapshot import CATEGORY_AI, CATEGORY_DIRECT, CATEGORY_OTHER, HostsPageSnapshot
from hosts.ui.blocks_section import HostsBlocksSection
from hosts.ui.draft_bar import HostsDraftBar
from hosts.ui.services_list import HostsListRow, HostsServicesList
from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_widgets import SettingsCard
from ui.one_shot_worker_runtime import OneShotWorkerRuntime
from ui.pages.base_page import BasePage
from ui.segmented_accessibility import set_segmented_items_accessibility
from ui.theme import get_cached_qta_pixmap, get_theme_tokens
from ui.theme_semantic import get_semantic_palette


FILTER_ALL = "all"
FILTER_ON = "on"
_FILTERS = (
    (FILTER_ALL, "page.hosts.filter.all", "Все"),
    (FILTER_ON, "page.hosts.filter.on", "Включённые"),
    (CATEGORY_DIRECT, "page.hosts.filter.direct", "Напрямую"),
    (CATEGORY_AI, "page.hosts.filter.ai", "ИИ"),
    (CATEGORY_OTHER, "page.hosts.filter.other", "Остальные"),
)
_GROUP_TITLES = {
    CATEGORY_DIRECT: ("page.hosts.group.direct", "Напрямую — адрес прописывается как есть"),
    CATEGORY_AI: ("page.hosts.group.ai", "ИИ — сами закрыты для России, нужен DNS-профиль"),
    CATEGORY_OTHER: ("page.hosts.group.other", "Остальные — через DNS-профиль"),
}
_BAR_MARGIN = 16


class HostsPage(BasePage):
    """Страница hosts: черновик изменений и одна запись по кнопке."""

    def __init__(self, parent=None, *, deps):
        super().__init__(
            "Hosts",
            "Прописывает адреса сервисов в системный файл hosts. Файл меняется, только когда вы нажмёте «Применить».",
            parent,
            title_key="page.hosts.title",
            subtitle_key="page.hosts.subtitle",
        )
        self._hosts = deps.hosts_feature
        self._snapshot: HostsPageSnapshot | None = None
        self._draft: HostsDraft | None = None
        self._filter = FILTER_ALL
        self._search = ""
        self._applying = False
        self._cleanup_in_progress = False
        self._snapshot_runtime = OneShotWorkerRuntime()
        self._snapshot_pending = False
        # Номер записи в hosts: чтение, начатое до записи, старше её результата.
        self._write_epoch = 0
        self._snapshot_epoch = 0
        self._apply_runtime = OneShotWorkerRuntime()
        self._open_runtime = OneShotWorkerRuntime()
        self._restore_runtime = OneShotWorkerRuntime()

        self._build_ui()
        self._retranslate()

    # ── построение ───────────────────────────────────────────

    def _tr(self, key: str, default: str, **kwargs) -> str:
        text = tr_catalog(key, language=self._ui_language, default=default)
        if kwargs:
            try:
                return text.format(**kwargs)
            except Exception:
                return text
        return text

    def _build_ui(self) -> None:
        # Предупреждение о защите файла или нет доступа (скрыто, пока всё хорошо).
        self.access_card = SettingsCard(parent=self.content)
        access_row = QHBoxLayout()
        access_row.setSpacing(10)
        self.access_icon = QLabel(self.access_card)
        self.access_icon.setFixedSize(20, 20)
        access_row.addWidget(self.access_icon, 0, Qt.AlignmentFlag.AlignTop)
        self.access_label = BodyLabel(self.access_card)
        self.access_label.setWordWrap(True)
        access_row.addWidget(self.access_label, 1)
        self.restore_button = PushButton(self.access_card)
        self.restore_button.clicked.connect(self._restore_permissions)
        access_row.addWidget(self.restore_button)
        self.access_card.main_layout.addLayout(access_row)
        self.access_card.hide()
        self.add_widget(self.access_card)

        # Сводка: что сейчас прописано и две общие кнопки.
        self.summary_card = SettingsCard(parent=self.content)
        summary_row = QHBoxLayout()
        summary_row.setSpacing(10)
        self.summary_dot = QLabel("●", self.summary_card)
        summary_row.addWidget(self.summary_dot)
        self.summary_label = BodyLabel(self.summary_card)
        self.summary_label.setWordWrap(True)
        summary_row.addWidget(self.summary_label, 1)
        self.all_off_button = TransparentPushButton(self.summary_card)
        self.all_off_button.clicked.connect(self._turn_all_off)
        summary_row.addWidget(self.all_off_button)
        self.open_button = PushButton(self.summary_card)
        self.open_button.clicked.connect(self._open_hosts_file)
        summary_row.addWidget(self.open_button)
        self.summary_card.main_layout.addLayout(summary_row)
        self.add_widget(self.summary_card)

        self.services_title = self.add_section_title(return_widget=True, text_key="page.hosts.services")

        # Панель: поиск, фильтр, DNS для всех.
        toolbar = QWidget(self.content)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(10)
        self.search_edit = SearchLineEdit(toolbar)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search_changed)
        toolbar_layout.addWidget(self.search_edit, 1)
        self.filter_bar = SegmentedWidget(toolbar)
        for key, text_key, default in _FILTERS:
            self.filter_bar.addItem(
                routeKey=key,
                text=self._tr(text_key, default),
                onClick=lambda k=key: self._set_filter(k),
            )
        self.filter_bar.setCurrentItem(FILTER_ALL)
        toolbar_layout.addWidget(self.filter_bar)
        self.add_widget(toolbar)

        dns_row = QWidget(self.content)
        dns_layout = QHBoxLayout(dns_row)
        dns_layout.setContentsMargins(0, 0, 0, 0)
        dns_layout.setSpacing(10)
        self.dns_all_label = BodyLabel(dns_row)
        dns_layout.addWidget(self.dns_all_label)
        self.dns_all_combo = ComboBox(dns_row)
        self.dns_all_combo.setMinimumWidth(200)
        self.dns_all_combo.currentIndexChanged.connect(self._on_dns_all_changed)
        dns_layout.addWidget(self.dns_all_combo)
        self.dns_all_hint = CaptionLabel(dns_row)
        self.dns_all_hint.setWordWrap(True)
        dns_layout.addWidget(self.dns_all_hint, 1)
        self.add_widget(dns_row)

        self.services_card = SettingsCard(parent=self.content)
        self.services_card.main_layout.setContentsMargins(4, 4, 4, 8)
        self.services_list = HostsServicesList(self.services_card)
        self.services_list.activated.connect(self._on_service_activated)
        self.services_card.main_layout.addWidget(self.services_list)
        self.add_widget(self.services_card)

        self.blocks_title = self.add_section_title(return_widget=True, text_key="page.hosts.blocks.title")
        self.blocks_section = HostsBlocksSection(parent=self.content)
        self.blocks_section.adobe_toggled.connect(self._on_adobe_toggled)
        self.add_widget(self.blocks_section)

        # Место под нижнюю панель, чтобы она не закрывала последние строки.
        self.bottom_spacer = QWidget(self.content)
        self.bottom_spacer.setFixedHeight(0)
        self.add_widget(self.bottom_spacer)

        self.draft_bar = HostsDraftBar(self)
        self.draft_bar.apply_clicked.connect(self._apply_draft)
        self.draft_bar.cancel_clicked.connect(self._reset_draft)
        self.draft_bar.preview_toggled.connect(lambda _opened: self._render_draft_bar())
        self.draft_bar.hide()

    def _retranslate(self) -> None:
        tr = self._tr
        self.search_edit.setPlaceholderText(tr("page.hosts.search.placeholder", "Найти сервис"))
        set_control_accessibility(self.search_edit, name=tr("page.hosts.search.placeholder", "Найти сервис"))
        for key, text_key, default in _FILTERS:
            item = self.filter_bar.items.get(key) if hasattr(self.filter_bar, "items") else None
            if item is not None:
                item.setText(tr(text_key, default))
        set_segmented_items_accessibility(
            self.filter_bar,
            name=tr("page.hosts.filter.name", "Какие сервисы показать"),
            labels={key: tr(text_key, default) for key, text_key, default in _FILTERS},
        )
        self.dns_all_label.setText(tr("page.hosts.dns_all.label", "DNS для всех сервисов:"))
        self.dns_all_hint.setText(
            tr(
                "page.hosts.dns_all.hint",
                "Ставит выбранный профиль всем сервисам с DNS-профилем. Потом любой можно поменять отдельно.",
            )
        )
        self.all_off_button.setText(tr("page.hosts.button.all_off", "Выключить все"))
        self.open_button.setText(tr("page.hosts.button.open", "Открыть файл"))
        self.restore_button.setText(tr("page.hosts.button.restore_access", "Снять защиту и восстановить права"))
        self.draft_bar.set_texts(
            show=tr("page.hosts.draft.show_lines", "Показать строки"),
            hide=tr("page.hosts.draft.hide_lines", "Скрыть строки"),
            cancel=tr("page.hosts.draft.cancel", "Отменить"),
            apply=tr("page.hosts.draft.apply", "Применить"),
            applying=tr("page.hosts.draft.applying", "Записываю…"),
        )
        self.blocks_section.set_translator(lambda key, default: self._tr(key, default))
        self._render()

    def set_ui_language(self, language: str) -> None:
        super().set_ui_language(language)
        self._retranslate()

    def _apply_page_theme(self, tokens=None, force: bool = False) -> None:
        _ = (tokens, force)
        self._render_summary()
        self._render_access()
        self.services_list.update()
        self.draft_bar.update()

    # ── жизненный цикл ───────────────────────────────────────

    def on_page_activated(self) -> None:
        if self._snapshot is None:
            cached = self._hosts.peek_page_snapshot()
            if cached is not None:
                self._set_snapshot(cached)
        self._request_snapshot()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_draft_bar()

    def _place_draft_bar(self) -> None:
        if not self.draft_bar.isVisible():
            self.bottom_spacer.setFixedHeight(0)
            return
        viewport = self.viewport().geometry()
        width = min(viewport.width() - 2 * _BAR_MARGIN, 980)
        self.draft_bar.setFixedWidth(max(320, width))
        self.draft_bar.adjustSize()
        height = self.draft_bar.sizeHint().height()
        x = viewport.left() + (viewport.width() - self.draft_bar.width()) // 2
        y = viewport.bottom() - height - _BAR_MARGIN
        self.draft_bar.setGeometry(x, y, self.draft_bar.width(), height)
        self.draft_bar.raise_()
        self.bottom_spacer.setFixedHeight(height + _BAR_MARGIN)

    def cleanup(self) -> None:
        self._cleanup_in_progress = True
        for runtime in (self._snapshot_runtime, self._apply_runtime, self._open_runtime, self._restore_runtime):
            try:
                runtime.stop(blocking=False, warning_prefix="Hosts worker")
                runtime.cancel()
            except Exception:
                pass
        super().cleanup()

    # ── фоновые задачи ───────────────────────────────────────

    def _request_snapshot(self) -> None:
        if self._cleanup_in_progress:
            return
        if self._snapshot_runtime.is_running():
            self._snapshot_pending = True
            return
        self._snapshot_pending = False
        self._snapshot_epoch = self._write_epoch
        self._snapshot_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._hosts.create_snapshot_worker(request_id, self),
            on_loaded=self._on_snapshot_loaded,
            on_failed=self._on_snapshot_failed,
            on_finished=self._on_snapshot_worker_finished,
        )

    def _on_snapshot_loaded(self, request_id: int, snapshot) -> None:
        if not self._snapshot_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        if self._applying or self._snapshot_epoch != self._write_epoch:
            # Файл читали до записи: такой снимок старее результата «Применить».
            self._snapshot_pending = True
            return
        if snapshot is not self._snapshot:
            self._set_snapshot(snapshot)

    def _on_snapshot_failed(self, request_id: int, error: str) -> None:
        if not self._snapshot_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        self._show_error(self._tr("page.hosts.error.read.title", "Не удалось прочитать hosts"), error)

    def _on_snapshot_worker_finished(self, _worker) -> None:
        # Во время записи не перечитываем: после неё снимок запросится сам.
        if self._snapshot_pending and not self._cleanup_in_progress and not self._applying:
            self._request_snapshot()

    def _set_snapshot(self, snapshot: HostsPageSnapshot) -> None:
        self._snapshot = snapshot
        if self._draft is None:
            self._draft = HostsDraft(snapshot)
        else:
            self._draft.rebase(snapshot)
        self._render()

    def _apply_draft(self) -> None:
        draft = self._draft
        if draft is None or self._applying or not draft.is_dirty():
            return
        self._applying = True
        self._write_epoch += 1
        self._render()
        selection = draft.selection()
        adobe = draft.adobe if draft.adobe_changed else None
        self._apply_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._hosts.create_apply_worker(request_id, selection, adobe, self),
            on_loaded=self._on_apply_finished,
            on_failed=self._on_apply_failed,
        )

    def _on_apply_finished(self, request_id: int, result) -> None:
        if not self._apply_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        self._applying = False
        snapshot = getattr(result, "snapshot", None)
        if getattr(result, "success", False):
            if self._draft is not None:
                self._draft.reset()
            InfoBar.success(
                title=self._tr("page.hosts.applied.title", "Записано в hosts"),
                content=self._tr(
                    "page.hosts.applied.content",
                    "Перезапустите браузер, чтобы изменения заработали.",
                ),
                duration=5000,
                parent=self.window(),
            )
        else:
            self._show_error(
                self._tr("page.hosts.apply_failed.title", "Не удалось записать hosts"),
                str(getattr(result, "message", "") or ""),
            )
        if snapshot is not None:
            self._set_snapshot(snapshot)
        else:
            self._render()
        if snapshot is None or self._snapshot_pending:
            self._request_snapshot()

    def _on_apply_failed(self, request_id: int, error: str) -> None:
        if not self._apply_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        self._applying = False
        self._show_error(self._tr("page.hosts.apply_failed.title", "Не удалось записать hosts"), error)
        self._render()
        self._request_snapshot()

    def _open_hosts_file(self) -> None:
        if self._open_runtime.is_running():
            return
        self._open_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._hosts.create_open_hosts_file_worker(request_id, self),
            on_loaded=self._on_open_finished,
            on_failed=lambda request_id, error: self._on_open_finished(request_id, None, error),
        )

    def _on_open_finished(self, request_id: int, result, error: str = "") -> None:
        if not self._open_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        if result is not None and getattr(result, "success", False):
            return
        message = error or str(getattr(result, "message", "") or "")
        self._show_error(self._tr("page.hosts.open.error.title", "Не удалось открыть hosts"), message)

    def _restore_permissions(self) -> None:
        if self._restore_runtime.is_running():
            return
        self.restore_button.setEnabled(False)
        self.restore_button.setText(self._tr("page.hosts.button.restoring_access", "Восстанавливаю…"))
        self._restore_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._hosts.create_permission_restore_worker(request_id, self),
            on_loaded=self._on_restore_finished,
            on_failed=lambda request_id, error: self._on_restore_finished(request_id, None, error),
        )

    def _on_restore_finished(self, request_id: int, result, error: str = "") -> None:
        if not self._restore_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        self.restore_button.setEnabled(True)
        self.restore_button.setText(self._tr("page.hosts.button.restore_access", "Снять защиту и восстановить права"))
        if result is not None and getattr(result, "success", False):
            InfoBar.success(
                title=self._tr("page.hosts.permissions.restored.title", "Права восстановлены"),
                content=self._tr("page.hosts.permissions.restored.content", "Теперь можно нажать «Применить»."),
                duration=4000,
                parent=self.window(),
            )
        else:
            self._show_error(
                self._tr("page.hosts.permissions.failed.title", "Не удалось восстановить права"),
                error or str(getattr(result, "message", "") or ""),
            )
        self._request_snapshot()

    def _show_error(self, title: str, content: str) -> None:
        InfoBar.error(title=title, content=str(content or ""), duration=8000, parent=self.window())

    # ── действия пользователя (только черновик) ──────────────

    def _on_search_changed(self, text: str) -> None:
        self._search = str(text or "").strip().casefold()
        self._render_list()

    def _set_filter(self, key: str) -> None:
        self._filter = key
        self._render_list()

    def _on_service_activated(self, service_name: str, global_pos: QPoint) -> None:
        draft = self._draft
        if draft is None or self._applying:
            return
        entry = draft.snapshot.service(service_name)
        if entry is None or entry.unavailable_reason:
            return
        if entry.is_direct:
            draft.set(service_name, None if draft.value(service_name) else (entry.profiles[0] if entry.profiles else None))
            self._render()
            return
        self._show_profile_menu(service_name, global_pos)

    def _show_profile_menu(self, service_name: str, global_pos: QPoint) -> None:
        draft = self._draft
        if draft is None:
            return
        entry = draft.snapshot.service(service_name)
        if entry is None:
            return
        labels = dict(draft.snapshot.dns_profiles)
        current = draft.value(service_name)
        menu = RoundMenu(parent=self)
        choices = [(None, self._tr("page.hosts.profile.off", "Выкл."))]
        choices += [(profile_id, labels.get(profile_id, profile_id)) for profile_id in entry.profiles]
        for profile_id, label in choices:
            text = f"✓  {label}" if profile_id == current else f"     {label}"
            action = Action(text, parent=menu)
            action.triggered.connect(
                lambda _checked=False, name=service_name, value=profile_id: self._set_service_profile(name, value)
            )
            menu.addAction(action)
        menu.exec(global_pos)

    def _set_service_profile(self, service_name: str, profile_id) -> None:
        if self._draft is not None and not self._applying and self._draft.set(service_name, profile_id):
            self._render()

    def _on_dns_all_changed(self, index: int) -> None:
        draft = self._draft
        if draft is None or self._applying or index < 0:
            return
        value = self.dns_all_combo.itemData(index)
        if value == MIXED:
            return
        profile_id = None if value in (None, "") else str(value)
        _changed, skipped = draft.set_all_dns(profile_id)
        if skipped:
            InfoBar.info(
                title=self._tr("page.hosts.dns_all.skipped.title", "Не у всех сервисов есть этот профиль"),
                content=self._tr("page.hosts.dns_all.skipped.content", "Оставлены как были: {names}").format(
                    names=", ".join(skipped)
                ),
                duration=6000,
                parent=self.window(),
            )
        self._render()

    def _turn_all_off(self) -> None:
        draft = self._draft
        if draft is None or self._applying:
            return
        for entry in draft.snapshot.services:
            draft.set(entry.name, None)
        self._render()

    def _on_adobe_toggled(self, checked: bool) -> None:
        if self._draft is None or self._applying:
            self._render()
            return
        self._draft.set_adobe(bool(checked))
        self._render()

    def _reset_draft(self) -> None:
        if self._draft is not None and not self._applying:
            self._draft.reset()
            self._render()

    # ── отрисовка ────────────────────────────────────────────

    def _render(self) -> None:
        self._render_access()
        self._render_summary()
        self._render_dns_all()
        self._render_list()
        self._render_blocks()
        self._render_draft_bar()

    def _render_access(self) -> None:
        snapshot = self._snapshot
        semantic = get_semantic_palette()
        text = ""
        if snapshot is not None and not snapshot.readable:
            text = self._tr(
                "page.hosts.notice.no_access",
                "Нет доступа к файлу hosts. Часто его блокирует антивирус. Кнопка справа вернёт стандартные права Windows.",
            )
        elif snapshot is not None and snapshot.read_only:
            text = self._tr(
                "page.hosts.notice.read_only",
                "Файл hosts защищён от записи (стоит «только чтение»). Программа сама защиту не снимает — нажмите кнопку справа, если хотите менять файл.",
            )
        self.access_card.setVisible(bool(text))
        if text:
            self.access_label.setText(text)
            set_state_text(self.access_label, text)
            self.access_icon.setPixmap(get_cached_qta_pixmap("fa5s.lock", color=semantic.warning, size=18))

    def _render_summary(self) -> None:
        snapshot = self._snapshot
        tokens = get_theme_tokens()
        semantic = get_semantic_palette()
        if snapshot is None:
            text = self._tr("page.hosts.loading", "Загрузка…")
            active = False
        else:
            block = snapshot.block(BLOCK_ZAPRETGUI)
            lines = block.count if block is not None else 0
            services = sum(1 for entry in snapshot.services if entry.current)
            active = lines > 0
            if active:
                text = self._tr(
                    "page.hosts.summary.on",
                    "Сейчас в hosts от ZapretGUI: {lines} строк, включено сервисов: {services}",
                    lines=lines,
                    services=services,
                )
            else:
                text = self._tr("page.hosts.summary.off", "Сейчас ZapretGUI ничего не прописывает в hosts")
        self.summary_label.setText(text)
        set_state_text(self.summary_label, text)
        self.summary_dot.setStyleSheet(f"color: {semantic.success if active else tokens.fg_faint}; font-size: 12px;")
        self.all_off_button.setEnabled(snapshot is not None and not self._applying)

    def _render_dns_all(self) -> None:
        draft = self._draft
        combo = self.dns_all_combo
        combo.blockSignals(True)
        combo.clear()
        if draft is None:
            combo.setEnabled(False)
            combo.blockSignals(False)
            return
        common = draft.dns_common_value()
        items = [(MIXED, self._tr("page.hosts.dns_all.mixed", "Разные"))] if common == MIXED else []
        items += [("", self._tr("page.hosts.profile.off", "Выкл."))]
        items += list(draft.snapshot.dns_profiles)
        for value, label in items:
            combo.addItem(label, userData=value)
        current = MIXED if common == MIXED else (common or "")
        for index, (value, _label) in enumerate(items):
            if value == current:
                combo.setCurrentIndex(index)
                break
        combo.setEnabled(not self._applying and bool(draft.snapshot.dns_profiles))
        set_control_accessibility(combo, name=f"{self.dns_all_label.text()} {combo.currentText()}")
        combo.blockSignals(False)

    def _render_list(self) -> None:
        draft = self._draft
        if draft is None:
            self.services_list.set_rows([HostsListRow(kind="empty", title=self._tr("page.hosts.loading", "Загрузка…"))])
            return
        labels = dict(draft.snapshot.dns_profiles)
        off_label = self._tr("page.hosts.profile.off", "Выкл.")
        ipv6_hint = self._tr("page.hosts.hint.ipv6", "Нужен IPv6 — сейчас его нет")
        grouped: dict[str, list[HostsListRow]] = {CATEGORY_DIRECT: [], CATEGORY_AI: [], CATEGORY_OTHER: []}
        for entry in draft.snapshot.services:
            value = draft.value(entry.name)
            if self._search and self._search not in entry.name.casefold():
                continue
            if self._filter == FILTER_ON and not value:
                continue
            if self._filter in grouped and self._filter != entry.category:
                continue
            value_text = off_label if not value else labels.get(value, value)
            changed = draft.is_changed(entry.name)
            state = (
                (self._tr("page.hosts.state.on", "включён") if value else self._tr("page.hosts.state.off", "выключен"))
                if entry.is_direct
                else value_text
            )
            accessible = f"{entry.name}: {state}"
            if changed:
                accessible += ", " + self._tr("page.hosts.state.changed", "изменено, ещё не записано")
            grouped.setdefault(entry.category, []).append(
                HostsListRow(
                    kind="service",
                    title=entry.name,
                    service_name=entry.name,
                    icon_name=entry.icon_name,
                    icon_color=entry.icon_color,
                    is_direct=entry.is_direct,
                    is_on=bool(value),
                    value_text=value_text,
                    hint=ipv6_hint if entry.unavailable_reason else "",
                    changed=changed,
                    enabled=not entry.unavailable_reason and not self._applying,
                    accessible_text=accessible,
                )
            )
        rows: list[HostsListRow] = []
        for category in (CATEGORY_DIRECT, CATEGORY_AI, CATEGORY_OTHER):
            items = grouped.get(category) or []
            if not items:
                continue
            key, default = _GROUP_TITLES[category]
            rows.append(HostsListRow(kind="group", title=f"{self._tr(key, default)}  ·  {len(items)}"))
            rows.extend(items)
        if not rows:
            rows.append(HostsListRow(kind="empty", title=self._tr("page.hosts.empty", "Ничего не найдено")))
        self.services_list.set_rows(rows)

    def _render_blocks(self) -> None:
        if self._draft is None:
            return
        self.blocks_section.set_snapshot(self._draft.snapshot, adobe_value=self._draft.adobe)

    def _render_draft_bar(self) -> None:
        draft = self._draft
        dirty = draft is not None and (draft.is_dirty() or self._applying)
        if not dirty:
            if self.draft_bar.isVisible():
                self.draft_bar.set_preview_open(False)
                self.draft_bar.hide()
                self._place_draft_bar()
            return
        preview = draft.preview()
        parts = [
            self._tr(
                "page.hosts.draft.summary",
                "Не записано: сервисов {services}, строк +{added} / −{removed}",
                services=len(draft.changed_services()),
                added=len(preview.added),
                removed=len(preview.removed),
            )
        ]
        if draft.stale_lines:
            parts.append(
                self._tr(
                    "page.hosts.draft.stale",
                    "лишних строк в блоке ZapretGUI: {count} — уберутся при записи",
                    count=len(draft.stale_lines),
                )
            )
        if draft.adobe_changed:
            parts.append(
                self._tr("page.hosts.draft.adobe_on", "Adobe будет заблокирован")
                if draft.adobe
                else self._tr("page.hosts.draft.adobe_off", "блокировка Adobe будет снята")
            )
        self.draft_bar.set_summary(" · ".join(parts))
        self.draft_bar.set_busy(self._applying, can_cancel=draft.has_user_changes())
        if self.draft_bar.is_preview_open():
            self.draft_bar.set_preview_text(self._preview_text(preview))
        self.draft_bar.show()
        self._place_draft_bar()

    def _preview_text(self, preview) -> str:
        lines: list[str] = []
        if preview.added:
            lines.append(self._tr("page.hosts.preview.added", "Добавятся в блок ZapretGUI:"))
            lines.extend(f"+ {line}" for line in preview.added)
        if preview.removed:
            if lines:
                lines.append("")
            lines.append(self._tr("page.hosts.preview.removed", "Удалятся из блока ZapretGUI:"))
            lines.extend(f"− {line}" for line in preview.removed)
        if preview.shadowed:
            if lines:
                lines.append("")
            lines.append(
                self._tr(
                    "page.hosts.preview.shadowed",
                    "Ваши строки с теми же доменами останутся, но Windows возьмёт адрес из блока ZapretGUI — он выше:",
                )
            )
            lines.extend(f"  {line}" for line in preview.shadowed)
        if not lines:
            lines.append(self._tr("page.hosts.preview.nothing", "Строки блока ZapretGUI не меняются."))
        return "\n".join(lines)


__all__ = ["HostsPage"]
