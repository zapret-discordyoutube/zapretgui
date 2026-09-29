# hosts/ui/page.py
"""Страница Hosts: сервисы через системный файл hosts.

Как это работает:
- сервисы — плитки по группам; включённая плитка мягко залита акцентом;
- щелчок по плитке сразу записывает hosts (в фоне); у сервисов с
  DNS-профилем под плиткой открывается меню профиля;
- пока идёт запись, щёлкать можно дальше: новый выбор запишется следом;
- сверху кнопки «Файл hosts» (весь файл с раскраской), «DNS для всех» и
  «Выключить все»; сводка сворачивается, когда список прокручивают;
- поиск появляется по Ctrl+F и прячется по Esc.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, Qt, QVariantAnimation
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    Action,
    BodyLabel,
    DropDownPushButton,
    FluentIcon,
    InfoBar,
    PushButton,
    RoundMenu,
    ScrollArea,
    SearchLineEdit,
    TransparentPushButton,
)

from app.ui_texts import tr as tr_catalog
from hosts.draft import MIXED, HostsDraft
from hosts.hosts_blocks import BLOCK_ZAPRETGUI
from hosts.page_snapshot import CATEGORY_AI, CATEGORY_DIRECT, CATEGORY_OTHER, HostsPageSnapshot
from hosts.ui.services_tiles import HostsTile, HostsTilesGrid
from ui.accessibility import set_control_accessibility, set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.fluent_widgets import SettingsCard
from ui.one_shot_worker_runtime import OneShotWorkerRuntime
from ui.pages.base_page import BasePage
from ui.smooth_scroll import apply_page_smooth_scroll_preference
from ui.theme import get_cached_qta_pixmap, get_theme_tokens
from ui.theme_semantic import get_semantic_palette


ADOBE_TILE_KEY = "__adobe__"
# Сводка сворачивается, когда список прокручен ниже этого места.
COLLAPSE_SCROLL_PX = 24
COLLAPSE_MS = 180

_GROUP_TITLES = {
    CATEGORY_DIRECT: ("page.hosts.group.direct", "Напрямую — адрес прописывается как есть"),
    CATEGORY_AI: ("page.hosts.group.ai", "ИИ — сами закрыты для России, нужен DNS-профиль"),
    CATEGORY_OTHER: ("page.hosts.group.other", "Остальные — через DNS-профиль"),
}


class HostsPage(BasePage):
    """Страница hosts: плитки сервисов, каждое изменение сразу пишется в файл."""

    def __init__(self, parent=None, *, deps):
        super().__init__(
            "Hosts",
            "Щёлкните по плитке — адреса сервиса сразу запишутся в системный файл hosts.",
            parent,
            title_key="page.hosts.title",
            subtitle_key="page.hosts.subtitle",
        )
        self._hosts = deps.hosts_feature
        self._open_file_page = deps.open_file_page
        self._snapshot: HostsPageSnapshot | None = None
        self._draft: HostsDraft | None = None
        self._search = ""
        self._cleanup_in_progress = False
        self._snapshot_runtime = OneShotWorkerRuntime()
        self._snapshot_pending = False
        # Номер записи в hosts: чтение, начатое до записи, старше её результата.
        self._write_epoch = 0
        self._snapshot_epoch = 0
        self._apply_runtime = OneShotWorkerRuntime()
        self._applying = False
        # Пока шла запись, пользователь успел ещё что-то поменять.
        self._apply_pending = False
        self._apply_force_pending = False
        self._just_written = False
        self._restore_runtime = OneShotWorkerRuntime()
        self._profile_menu: RoundMenu | None = None
        self._summary_collapsed = False
        self._collapse_anim: QVariantAnimation | None = None

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
        # Прокручиваются только плитки: второй прокрутки у страницы нет.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalScrollBar().hide()
        self._build_header()

        # Описание и сводка сворачиваются вместе, когда плитки прокручивают.
        self.top_panel = QWidget(self.content)
        top_layout = QVBoxLayout(self.top_panel)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(12)
        if self.subtitle_label is not None:
            self.vBoxLayout.removeWidget(self.subtitle_label)
            self.subtitle_label.setParent(self.top_panel)
            top_layout.addWidget(self.subtitle_label)
        self.summary_card = SettingsCard(parent=self.top_panel)
        summary_row = QHBoxLayout()
        summary_row.setSpacing(10)
        self.summary_dot = QLabel("●", self.summary_card)
        summary_row.addWidget(self.summary_dot)
        self.summary_label = BodyLabel(self.summary_card)
        self.summary_label.setWordWrap(True)
        summary_row.addWidget(self.summary_label, 1)
        self.summary_card.main_layout.addLayout(summary_row)
        top_layout.addWidget(self.summary_card)
        self.add_widget(self.top_panel)

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
        self.restore_button = PushButton(FluentIcon.SYNC, "", self.access_card)
        self.restore_button.clicked.connect(self._restore_permissions)
        access_row.addWidget(self.restore_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.access_card.main_layout.addLayout(access_row)
        self.access_card.hide()
        self.add_widget(self.access_card)

        # Поиск: появляется по Ctrl+F, прячется по Esc.
        self.search_edit = SearchLineEdit(self.content)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search_changed)
        self.search_edit.installEventFilter(self)
        self.search_edit.hide()
        self.add_widget(self.search_edit)
        self._find_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self)
        self._find_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._find_shortcut.activated.connect(self.open_search)

        # Плитки со своей прокруткой занимают всё оставшееся место.
        self.services_scroll = ScrollArea(self.content)
        self.services_scroll.setWidgetResizable(True)
        self.services_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.services_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.services_scroll.viewport().setStyleSheet("background: transparent;")
        apply_page_smooth_scroll_preference(self.services_scroll)
        self.tiles = HostsTilesGrid(self.services_scroll)
        self.tiles.activated.connect(self._on_tile_activated)
        self.services_scroll.setWidget(self.tiles)
        self.services_scroll.verticalScrollBar().valueChanged.connect(self._on_tiles_scrolled)
        self.add_widget(self.services_scroll, 1)

    def _build_header(self) -> None:
        """Заголовок и обычные кнопки справа от него."""
        header = QWidget(self.content)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        self.vBoxLayout.removeWidget(self.title_label)
        self.title_label.setParent(header)
        header_layout.addWidget(self.title_label, 1)
        self.file_button = PushButton(FluentIcon.DOCUMENT, "", header)
        self.file_button.clicked.connect(lambda: self._open_file_page())
        header_layout.addWidget(self.file_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.dns_all_button = DropDownPushButton(FluentIcon.GLOBE, "", header)
        header_layout.addWidget(self.dns_all_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.all_off_button = TransparentPushButton(FluentIcon.CLOSE, "", header)
        self.all_off_button.clicked.connect(self._turn_all_off)
        header_layout.addWidget(self.all_off_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.vBoxLayout.insertWidget(0, header)

    def _retranslate(self) -> None:
        tr = self._tr
        search_name = tr("page.hosts.search.placeholder", "Найти сервис")
        self.search_edit.setPlaceholderText(search_name)
        set_control_accessibility(self.search_edit, name=search_name)
        self.file_button.setText(tr("page.hosts.button.file", "Файл hosts"))
        set_control_accessibility(
            self.file_button,
            name=tr("page.hosts.button.file", "Файл hosts"),
            description=tr("page.hosts.button.file.description", "Весь файл hosts с раскраской строк по владельцам."),
        )
        self.dns_all_button.setText(tr("page.hosts.dns_all.button", "DNS для всех"))
        set_control_accessibility(
            self.dns_all_button,
            name=tr("page.hosts.dns_all.button", "DNS для всех"),
            description=tr(
                "page.hosts.dns_all.hint",
                "Ставит выбранный профиль всем сервисам с DNS-профилем. Потом любой можно поменять отдельно.",
            ),
        )
        self.all_off_button.setText(tr("page.hosts.button.all_off", "Выключить все"))
        set_control_accessibility(self.all_off_button, name=tr("page.hosts.button.all_off", "Выключить все"))
        self.restore_button.setText(tr("page.hosts.button.restore_access", "Снять защиту и восстановить права"))
        set_control_accessibility(self.restore_button, name=self.restore_button.text())
        self._render()

    def set_ui_language(self, language: str) -> None:
        super().set_ui_language(language)
        self._retranslate()

    def _apply_page_theme(self, tokens=None, force: bool = False) -> None:
        _ = (tokens, force)
        self._render_summary()
        self._render_access()
        self.tiles.update()

    # ── поиск по Ctrl+F ──────────────────────────────────────

    def open_search(self) -> None:
        self.search_edit.show()
        self.search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search_edit.selectAll()

    def close_search(self) -> None:
        self.search_edit.clear()
        self.search_edit.hide()
        self.tiles.setFocus(Qt.FocusReason.OtherFocusReason)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        # Фильтр зовётся и во время построения базовой страницы, до поиска.
        if watched is getattr(self, "search_edit", None) and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.close_search()
                return True
        menu = getattr(self, "_profile_menu", None)
        if menu is not None and watched is menu and event.type() == QEvent.Type.Wheel:
            # Меню профиля не висит над уехавшими плитками.
            self._close_profile_menu()
            return True
        return super().eventFilter(watched, event)

    # ── жизненный цикл ───────────────────────────────────────

    def on_page_activated(self) -> None:
        if self._snapshot is None:
            cached = self._hosts.peek_page_snapshot()
            if cached is not None:
                self._set_snapshot(cached)
        self._request_snapshot()

    def on_page_hidden(self) -> None:
        self._close_profile_menu()
        super().on_page_hidden()

    def cleanup(self) -> None:
        self._cleanup_in_progress = True
        self._close_profile_menu()
        for runtime in (self._snapshot_runtime, self._apply_runtime, self._restore_runtime):
            try:
                runtime.stop(blocking=False, warning_prefix="Hosts worker")
                runtime.cancel()
            except Exception:
                pass
        super().cleanup()

    # ── чтение файла ─────────────────────────────────────────

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
            # Файл читали до записи: такой снимок старее результата записи.
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

    # ── запись: каждое изменение сразу ───────────────────────

    def _commit(self, *, force: bool = False) -> None:
        """Записывает текущий выбор. Во время записи — ставит в очередь.

        force — записать, даже если выбор не менялся («Выключить все» убирает
        и лишние строки блока ZapretGUI).
        """
        draft = self._draft
        if draft is None or self._cleanup_in_progress:
            return
        if self._applying:
            self._apply_pending = True
            self._apply_force_pending = self._apply_force_pending or force
            return
        if not force and not draft.has_user_changes():
            return
        self._applying = True
        self._just_written = False
        self._write_epoch += 1
        selection = draft.selection()
        adobe = draft.adobe if draft.adobe_changed else None
        self._render()
        self._apply_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._hosts.create_apply_worker(request_id, selection, adobe, self),
            on_loaded=self._on_apply_finished,
            on_failed=self._on_apply_failed,
        )

    def _take_pending(self) -> tuple[bool, bool]:
        pending, force = self._apply_pending, self._apply_force_pending
        self._apply_pending = False
        self._apply_force_pending = False
        return pending, force

    def _on_apply_finished(self, request_id: int, result) -> None:
        if not self._apply_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        self._applying = False
        pending, force = self._take_pending()
        snapshot = getattr(result, "snapshot", None)
        if not getattr(result, "success", False):
            self._fail_write(str(getattr(result, "message", "") or ""), snapshot)
            return
        self._just_written = True
        if snapshot is not None:
            # Записанное уходит из черновика само; остаётся только то, что
            # пользователь поменял во время записи.
            self._set_snapshot(snapshot)
        if pending and (force or (self._draft is not None and self._draft.has_user_changes())):
            self._commit(force=force)
        elif snapshot is None:
            self._request_snapshot()
        elif self._draft is not None and self._draft.has_user_changes():
            # Снимок не совпал с записанным — правду показывает файл.
            self._draft.reset()
            self._render()
        if self._snapshot_pending and not self._applying:
            self._request_snapshot()
        self._render()

    def _on_apply_failed(self, request_id: int, error: str) -> None:
        if not self._apply_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        self._applying = False
        self._take_pending()
        self._fail_write(error, None)

    def _fail_write(self, message: str, snapshot) -> None:
        """Запись не удалась: плитки возвращаются к тому, что реально в файле."""
        if self._draft is not None:
            self._draft.reset()
        if snapshot is not None:
            self._set_snapshot(snapshot)
        self._render()
        self._show_error(self._tr("page.hosts.apply_failed.title", "Не удалось записать hosts"), message)
        self._request_snapshot()

    def _restore_permissions(self) -> None:
        if self._restore_runtime.is_running():
            return
        self.restore_button.setEnabled(False)
        self._restore_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._hosts.create_permission_restore_worker(request_id, self),
            on_loaded=self._on_restore_finished,
            on_failed=lambda request_id, error: self._on_restore_finished(request_id, None, error),
        )

    def _on_restore_finished(self, request_id: int, result, error: str = "") -> None:
        if not self._restore_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        self.restore_button.setEnabled(True)
        if result is not None and getattr(result, "success", False):
            InfoBar.success(
                title=self._tr("page.hosts.permissions.restored.title", "Права восстановлены"),
                content=self._tr("page.hosts.permissions.restored.content", "Теперь сервисы снова можно включать."),
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

    # ── действия пользователя ────────────────────────────────

    def _on_search_changed(self, text: str) -> None:
        self._search = str(text or "").strip().casefold()
        self._render_tiles()

    def _on_tile_activated(self, key: str, global_pos: QPoint) -> None:
        draft = self._draft
        if draft is None:
            return
        if key == ADOBE_TILE_KEY:
            draft.set_adobe(not draft.adobe)
            self._after_change(key)
            return
        entry = draft.snapshot.service(key)
        if entry is None or entry.unavailable_reason:
            return
        if entry.is_direct:
            draft.set(key, None if draft.value(key) else (entry.profiles[0] if entry.profiles else None))
            self._after_change(key)
            return
        self._show_profile_menu(key, global_pos)

    def _after_change(self, key: str = "") -> None:
        self.tiles.flash(key)
        self._render()
        self._commit()

    def _show_profile_menu(self, service_name: str, global_pos: QPoint) -> None:
        draft = self._draft
        if draft is None:
            return
        entry = draft.snapshot.service(service_name)
        if entry is None:
            return
        self._close_profile_menu()
        labels = dict(draft.snapshot.dns_profiles)
        current = draft.value(service_name)
        menu = RoundMenu(parent=self)
        choices = [(None, self._tr("page.hosts.profile.off", "Выкл."))]
        choices += [(profile_id, labels.get(profile_id, profile_id)) for profile_id in entry.profiles]
        for profile_id, label in choices:
            action = Action(FluentIcon.ACCEPT, label, parent=menu) if profile_id == current else Action(label, parent=menu)
            action.triggered.connect(
                lambda _checked=False, name=service_name, value=profile_id: self._set_service_profile(name, value)
            )
            menu.addAction(action)
        menu.installEventFilter(self)
        self._profile_menu = menu
        menu.exec(global_pos)

    def _close_profile_menu(self) -> None:
        menu, self._profile_menu = self._profile_menu, None
        if menu is None:
            return
        try:
            menu.close()
        except RuntimeError:
            pass

    def _on_tiles_scrolled(self, value: int) -> None:
        self._close_profile_menu()
        self._set_summary_collapsed(value > COLLAPSE_SCROLL_PX)

    def _set_service_profile(self, service_name: str, profile_id) -> None:
        if self._draft is not None and self._draft.set(service_name, profile_id):
            self._after_change(service_name)

    def _set_all_dns(self, profile_id) -> None:
        draft = self._draft
        if draft is None:
            return
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
        self._after_change()

    def _turn_all_off(self) -> None:
        draft = self._draft
        if draft is None:
            return
        for entry in draft.snapshot.services:
            draft.set(entry.name, None)
        block = draft.snapshot.block(BLOCK_ZAPRETGUI)
        self._render()
        # Запись и тогда, когда всё уже выключено: уберутся лишние строки блока.
        self._commit(force=block is not None)

    # ── сводка: сворачивается при прокрутке ──────────────────

    def _set_summary_collapsed(self, collapsed: bool) -> None:
        if collapsed == self._summary_collapsed:
            return
        self._summary_collapsed = collapsed
        panel = self.top_panel
        full = max(panel.sizeHint().height(), 1)
        if self._collapse_anim is not None:
            self._collapse_anim.stop()
        if not are_live_animations_enabled():
            panel.setMaximumHeight(0 if collapsed else 16777215)
            panel.setVisible(not collapsed)
            return
        panel.setVisible(True)
        start = panel.height() if panel.isVisible() else 0
        anim = QVariantAnimation(self)
        anim.setStartValue(start)
        anim.setEndValue(0 if collapsed else full)
        anim.setDuration(COLLAPSE_MS)
        anim.valueChanged.connect(lambda value: panel.setMaximumHeight(int(value)))

        def _done() -> None:
            if self._summary_collapsed:
                panel.hide()
            else:
                panel.setMaximumHeight(16777215)

        anim.finished.connect(_done)
        self._collapse_anim = anim
        anim.start()

    # ── отрисовка ────────────────────────────────────────────

    def _render(self) -> None:
        self._render_access()
        self._render_summary()
        self._render_dns_all()
        self._render_tiles()

    def _render_access(self) -> None:
        snapshot = self._snapshot
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
            self.access_icon.setPixmap(
                get_cached_qta_pixmap("fa5s.lock", color=get_semantic_palette().warning, size=18)
            )

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
                    "В hosts включено сервисов: {services} · строк от ZapretGUI: {lines}",
                    lines=lines,
                    services=services,
                )
            else:
                text = self._tr("page.hosts.summary.off", "Сейчас ZapretGUI ничего не прописывает в hosts")
            if self._applying:
                text += " · " + self._tr("page.hosts.summary.writing", "записываю…")
            elif self._just_written:
                text += " · " + self._tr(
                    "page.hosts.summary.written",
                    "записано — перезапустите браузер, чтобы изменения заработали",
                )
        self.summary_label.setText(text)
        set_state_text(self.summary_label, text)
        self.summary_dot.setStyleSheet(f"color: {semantic.success if active else tokens.fg_faint}; font-size: 12px;")
        self.all_off_button.setEnabled(snapshot is not None)

    def _render_dns_all(self) -> None:
        draft = self._draft
        self.dns_all_button.setEnabled(draft is not None and bool(draft.snapshot.dns_profiles))
        if draft is None:
            return
        common = draft.dns_common_value()
        menu = RoundMenu(parent=self.dns_all_button)
        items = [(None, self._tr("page.hosts.profile.off", "Выкл."))] + list(draft.snapshot.dns_profiles)
        for profile_id, label in items:
            selected = common != MIXED and common == profile_id
            action = Action(FluentIcon.ACCEPT, label, parent=menu) if selected else Action(label, parent=menu)
            action.triggered.connect(lambda _checked=False, value=profile_id: self._set_all_dns(value))
            menu.addAction(action)
        self.dns_all_button.setMenu(menu)

    def _render_tiles(self) -> None:
        draft = self._draft
        if draft is None:
            self.tiles.set_tiles([HostsTile(kind="empty", title=self._tr("page.hosts.loading", "Загрузка…"))])
            return
        labels = dict(draft.snapshot.dns_profiles)
        off_label = self._tr("page.hosts.profile.off", "Выкл.")
        on_caption = self._tr("page.hosts.state.on", "включён")
        off_caption = self._tr("page.hosts.state.off", "выключен")
        ipv6_hint = self._tr("page.hosts.hint.ipv6", "Нужен IPv6 — сейчас его нет")
        writing_text = self._tr("page.hosts.state.changed", "записывается")
        grouped: dict[str, list[HostsTile]] = {CATEGORY_DIRECT: [], CATEGORY_AI: [], CATEGORY_OTHER: []}
        for entry in draft.snapshot.services:
            if self._search and self._search not in entry.name.casefold():
                continue
            value = draft.value(entry.name)
            pending = draft.is_changed(entry.name)
            if entry.is_direct:
                badge = ""
                caption = ipv6_hint if entry.unavailable_reason else (on_caption if value else off_caption)
                state = on_caption if value else off_caption
            else:
                badge = "" if entry.unavailable_reason else (labels.get(value, value) if value else off_label)
                caption = ipv6_hint if entry.unavailable_reason else ""
                state = labels.get(value, value) if value else off_label
            accessible = f"{entry.name}: {state}" + (f", {writing_text}" if pending else "")
            grouped.setdefault(entry.category, []).append(
                HostsTile(
                    kind="tile",
                    title=entry.name,
                    key=entry.name,
                    icon_name=entry.icon_name,
                    icon_color=entry.icon_color,
                    is_on=bool(value),
                    badge=badge,
                    caption=caption,
                    has_menu=not entry.is_direct,
                    pending=pending and self._applying,
                    enabled=not entry.unavailable_reason,
                    accessible_text=accessible,
                )
            )
        tiles: list[HostsTile] = []
        for category in (CATEGORY_DIRECT, CATEGORY_AI, CATEGORY_OTHER):
            items = grouped.get(category) or []
            if not items:
                continue
            key, default = _GROUP_TITLES[category]
            on_count = sum(1 for item in items if item.is_on)
            tiles.append(HostsTile(kind="group", title=f"{self._tr(key, default)}  ·  {on_count} из {len(items)}"))
            tiles.extend(items)

        adobe_title = self._tr("page.hosts.adobe.title", "Блокировать активацию Adobe")
        if not self._search or self._search in adobe_title.casefold():
            tiles.append(HostsTile(kind="group", title=self._tr("page.hosts.group.blocks", "Блокировки")))
            tiles.append(
                HostsTile(
                    kind="tile",
                    title=adobe_title,
                    key=ADOBE_TILE_KEY,
                    icon_name="fa5s.ban",
                    icon_color="#ff6b61",
                    is_on=draft.adobe,
                    caption=on_caption if draft.adobe else off_caption,
                    pending=draft.adobe_changed and self._applying,
                    accessible_text=f"{adobe_title}: {on_caption if draft.adobe else off_caption}",
                )
            )
        if not tiles:
            tiles.append(HostsTile(kind="empty", title=self._tr("page.hosts.empty", "Ничего не найдено")))
        self.tiles.set_tiles(tiles)


__all__ = ["ADOBE_TILE_KEY", "HostsPage"]
