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

from dataclasses import replace

from PyQt6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, Qt
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    Action,
    BodyLabel,
    CaptionLabel,
    DropDownPushButton,
    FluentIcon,
    InfoBar,
    PushButton,
    RoundMenu,
    SearchLineEdit,
    StrongBodyLabel,
    TransparentPushButton,
    getFont,
    isDarkTheme,
    themeColor,
)

from app.ui_texts import tr as tr_catalog
from hosts.draft import MIXED, HostsDraft
from hosts.hosts_blocks import BLOCK_ZAPRETGUI
from hosts.page_snapshot import CATEGORY_AI, CATEGORY_DIRECT, CATEGORY_OTHER, HostsPageSnapshot
from hosts.ui.profile_icons import profile_icon
from hosts.ui.services_tiles import HostsChoice, HostsTile, HostsTilesGrid, split_service_title
from ui.accessibility import set_control_accessibility, set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.fluent_widgets import SettingsCard
from ui.one_shot_worker_runtime import OneShotWorkerRuntime
from ui.pages.base_page import BasePage
from ui.theme import get_cached_qta_pixmap, get_theme_tokens
from ui.theme_semantic import get_semantic_palette


ADOBE_TILE_KEY = "__adobe__"

_GROUP_TITLES = {
    CATEGORY_DIRECT: ("page.hosts.group.direct", "Напрямую"),
    CATEGORY_AI: ("page.hosts.group.ai", "ИИ-сервисы"),
    CATEGORY_OTHER: ("page.hosts.group.other", "Остальные сервисы"),
}
_GROUP_HINTS = {
    CATEGORY_DIRECT: ("page.hosts.group.direct.hint", "адрес прописывается как есть"),
    CATEGORY_AI: ("page.hosts.group.ai.hint", "сами закрыты для России, нужен DNS-профиль"),
    CATEGORY_OTHER: ("page.hosts.group.other.hint", "через DNS-профиль"),
}



class _StatusPill(QWidget):
    """Статус записи в сводке: значок и текст в одну строку на мягкой заливке.

    Появляется плавно и остаётся, пока следующая запись его не сменит.
    """

    FADE_MS = 180

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("hostsStatusPill")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 12, 5)
        layout.setSpacing(6)
        self.icon = QLabel(self)
        self.icon.setFixedSize(14, 14)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.label = CaptionLabel(self)
        self.label.setWordWrap(False)
        layout.addWidget(self.label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)
        self._fade: QPropertyAnimation | None = None
        self.setVisible(False)

    def text(self) -> str:
        return self.label.text()

    def set_status(self, text: str, color: str, icon_name: str) -> None:
        text = str(text or "")
        if not text:
            if self._fade is not None:
                self._fade.stop()
            self.label.setText("")
            self.setVisible(False)
            return
        self.label.setText(text)
        self.label.setStyleSheet(f"color: {color}; background: transparent;")
        if icon_name:
            self.icon.setPixmap(get_cached_qta_pixmap(icon_name, color=color, size=14))
        self.icon.setVisible(bool(icon_name))
        back = QColor(color)
        back.setAlpha(52 if isDarkTheme() else 38)
        self.setStyleSheet(
            "#hostsStatusPill {"
            f" background-color: rgba({back.red()}, {back.green()}, {back.blue()}, {back.alpha()});"
            " border-radius: 12px; }"
        )
        if self.isHidden():
            self.setVisible(True)
            self._fade_in()

    def _fade_in(self) -> None:
        if self._fade is not None:
            self._fade.stop()
        if not are_live_animations_enabled():
            self._opacity.setOpacity(1.0)
            return
        fade = QPropertyAnimation(self._opacity, b"opacity", self)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setDuration(self.FADE_MS)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade = fade
        fade.start()


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
        # Меню «DNS для всех» пересобирается, только когда меняется его содержимое.
        self._dns_all_menu_key: tuple | None = None

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
        # Страница прокручивается целиком, как все страницы: сводка уезжает
        # вверх вместе с плитками.
        self._build_header()

        # Описание и сводка.
        self.top_panel = QWidget(self.content)
        top_layout = QVBoxLayout(self.top_panel)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(12)
        if self.subtitle_label is not None:
            self.vBoxLayout.removeWidget(self.subtitle_label)
            self.subtitle_label.setParent(self.top_panel)
            top_layout.addWidget(self.subtitle_label)
        # Сводка: крупная цифра включённых сервисов, строки и статус записи.
        self.summary_card = SettingsCard(parent=self.top_panel)
        summary_row = QHBoxLayout()
        summary_row.setSpacing(14)
        self.summary_count = QLabel(self.summary_card)
        self.summary_count.setFont(getFont(28, QFont.Weight.DemiBold))
        summary_row.addWidget(self.summary_count, 0, Qt.AlignmentFlag.AlignVCenter)
        summary_text = QVBoxLayout()
        summary_text.setSpacing(0)
        self.summary_label = StrongBodyLabel(self.summary_card)
        summary_text.addWidget(self.summary_label)
        self.summary_detail = CaptionLabel(self.summary_card)
        summary_text.addWidget(self.summary_detail)
        summary_row.addLayout(summary_text, 1)
        self.summary_status = _StatusPill(self.summary_card)
        summary_row.addWidget(self.summary_status, 0, Qt.AlignmentFlag.AlignVCenter)
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

        self.tiles = HostsTilesGrid(self.content)
        self.tiles.activated.connect(self._on_tile_activated)
        self.tiles.profile_chosen.connect(self._set_service_profile)
        self.add_widget(self.tiles)

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
        return super().eventFilter(watched, event)

    # ── жизненный цикл ───────────────────────────────────────

    def on_page_activated(self) -> None:
        if self._snapshot is None:
            cached = self._hosts.peek_page_snapshot()
            if cached is not None:
                self._set_snapshot(cached)
        self._request_snapshot()

    def cleanup(self) -> None:
        self._cleanup_in_progress = True
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
        self._render_changes()
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
        self._render_changes()

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

    def _on_tile_activated(self, key: str) -> None:
        """Тумблер: сервис «напрямую» или блокировка Adobe."""
        draft = self._draft
        if draft is None:
            return
        if key == ADOBE_TILE_KEY:
            draft.set_adobe(not draft.adobe)
            self._after_change(key)
            return
        entry = draft.snapshot.service(key)
        if entry is None or entry.unavailable_reason or not entry.is_direct:
            return
        draft.set(key, None if draft.value(key) else (entry.profiles[0] if entry.profiles else None))
        self._after_change(key)

    def _after_change(self, key: str = "") -> None:
        self.tiles.flash(key)
        self._render_changes()
        self._commit()

    def _render_changes(self) -> None:
        """После щелчка меняются только плитки, сводка и «DNS для всех»."""
        self._render_summary()
        self._render_dns_all()
        self._render_tiles()

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
        self._render_changes()
        # Запись и тогда, когда всё уже выключено: уберутся лишние строки блока.
        self._commit(force=block is not None)

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

    def _loading_phrase(self) -> str:
        from ui.fun_phrases import loading_phrase_for

        return loading_phrase_for(
            self, self._ui_language, default=self._tr("page.hosts.loading", "Загрузка…")
        )

    def _render_summary(self) -> None:
        snapshot = self._snapshot
        tokens = get_theme_tokens()
        semantic = get_semantic_palette()
        if snapshot is None:
            services, lines = 0, 0
            title = self._loading_phrase()
        else:
            block = snapshot.block(BLOCK_ZAPRETGUI)
            lines = block.count if block is not None else 0
            services = sum(1 for entry in snapshot.services if entry.current)
            title = (
                self._tr("page.hosts.summary.services", "сервисов включено в hosts")
                if services
                else self._tr("page.hosts.summary.off", "Сейчас ZapretGUI ничего не прописывает в hosts")
            )
        self.summary_count.setText(str(services))
        self.summary_count.setVisible(snapshot is not None and services > 0)
        accent = QColor(themeColor())
        self.summary_count.setStyleSheet(f"color: {accent.name()}; background: transparent;")
        self.summary_label.setText(title)
        detail = self._tr("page.hosts.summary.lines", "строк от ZapretGUI в файле: {lines}", lines=lines) if lines else ""
        self.summary_detail.setText(detail)
        self.summary_detail.setVisible(bool(detail))
        if self._applying:
            status, color, icon = self._tr("page.hosts.summary.writing", "Записываю…"), accent.name(), "fa5s.sync-alt"
        elif self._just_written:
            status = self._tr(
                "page.hosts.summary.written",
                "Записано — перезапустите браузер, чтобы изменения заработали",
            )
            color, icon = semantic.success, "fa5s.check-circle"
        else:
            status, color, icon = "", tokens.fg_muted, ""
        self.summary_status.set_status(status, color, icon)
        spoken = " ".join(part for part in (str(services) if services else "", title, detail, status) if part)
        set_state_text(self.summary_label, spoken)
        self.all_off_button.setEnabled(snapshot is not None)

    def _render_dns_all(self) -> None:
        draft = self._draft
        self.dns_all_button.setEnabled(draft is not None and bool(draft.snapshot.dns_profiles))
        if draft is None:
            return
        common = draft.dns_common_value()
        items = [(None, self._tr("page.hosts.profile.off", "Выкл."))] + list(draft.snapshot.dns_profiles)
        menu_key = (tuple(items), common)
        if menu_key == self._dns_all_menu_key:
            return
        self._dns_all_menu_key = menu_key
        menu = RoundMenu(parent=self.dns_all_button)
        for position, (profile_id, label) in enumerate(items):
            selected = common != MIXED and common == profile_id
            if selected:
                action = Action(FluentIcon.ACCEPT, label, parent=menu)
            elif profile_id is None:
                action = Action(label, parent=menu)
            else:
                icon_name, color = profile_icon(profile_id, position - 1)
                action = Action(QIcon(get_cached_qta_pixmap(icon_name, color=color, size=16)), label, parent=menu)
            action.triggered.connect(lambda _checked=False, value=profile_id: self._set_all_dns(value))
            menu.addAction(action)
        old_menu = self.dns_all_button.menu()
        self.dns_all_button.setMenu(menu)
        if old_menu is not None:
            old_menu.deleteLater()

    def _render_tiles(self) -> None:
        draft = self._draft
        if draft is None:
            self.tiles.set_tiles([HostsTile(kind="empty", title=self._loading_phrase())])
            return
        labels = dict(draft.snapshot.dns_profiles)
        off_label = self._tr("page.hosts.profile.off", "Выкл.")
        on_state = self._tr("page.hosts.state.on", "включён")
        off_state = self._tr("page.hosts.state.off", "выключен")
        ipv6_hint = self._tr("page.hosts.hint.ipv6", "Нужен IPv6 — сейчас его нет")
        writing_text = self._tr("page.hosts.state.changed", "записывается")
        all_choices = tuple(
            HostsChoice(profile_id, label, *profile_icon(profile_id, position))
            for position, (profile_id, label) in enumerate(draft.snapshot.dns_profiles)
        )
        grouped: dict[str, list[HostsTile]] = {CATEGORY_DIRECT: [], CATEGORY_AI: [], CATEGORY_OTHER: []}
        for entry in draft.snapshot.services:
            if self._search and self._search not in entry.name.casefold():
                continue
            value = draft.value(entry.name)
            pending = draft.is_changed(entry.name) and self._applying
            title, note = split_service_title(entry.name)
            if entry.unavailable_reason:
                note = ipv6_hint
            if entry.is_direct:
                state = on_state if value else off_state
            else:
                state = labels.get(value, value) if value else off_label
            accessible = f"{entry.name}: {state}" + (f", {writing_text}" if pending else "")
            grouped.setdefault(entry.category, []).append(
                HostsTile(
                    kind="tile",
                    title=title,
                    key=entry.name,
                    note=note,
                    icon_name=entry.icon_name,
                    icon_color=entry.icon_color,
                    is_on=bool(value),
                    has_switch=entry.is_direct and not entry.unavailable_reason,
                    choices=() if entry.is_direct or entry.unavailable_reason else tuple(
                        replace(choice, available=choice.profile_id in entry.profiles) for choice in all_choices
                    ),
                    selected=None if entry.is_direct else value,
                    state_text="" if entry.is_direct else state,
                    pending=pending,
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
            counter = self._tr("page.hosts.group.counter", "{on} из {total}", on=on_count, total=len(items))
            hint_key, hint_default = _GROUP_HINTS[category]
            counter = f"{counter}  ·  {self._tr(hint_key, hint_default)}"
            # У групп с DNS-профилями справа легенда: какая иконка — какой провайдер.
            legend = all_choices if any(item.has_choices for item in items) else ()
            tiles.append(HostsTile(kind="group", title=self._tr(key, default), counter=counter, legend=legend))
            tiles.extend(items)

        adobe_title = self._tr("page.hosts.adobe.title", "Блокировать активацию Adobe")
        if not self._search or self._search in adobe_title.casefold():
            tiles.append(HostsTile(kind="group", title=self._tr("page.hosts.group.blocks", "Блокировки")))
            tiles.append(
                HostsTile(
                    kind="tile",
                    title=adobe_title,
                    key=ADOBE_TILE_KEY,
                    note=self._tr("page.hosts.adobe.note", "Закрывает серверы проверки лицензии Adobe"),
                    icon_name="fa5s.ban",
                    icon_color="#ff6b61",
                    is_on=draft.adobe,
                    has_switch=True,
                    pending=draft.adobe_changed and self._applying,
                    accessible_text=f"{adobe_title}: {on_state if draft.adobe else off_state}",
                )
            )
        if not tiles:
            tiles.append(HostsTile(kind="empty", title=self._tr("page.hosts.empty", "Ничего не найдено")))
        self.tiles.set_tiles(tiles)


__all__ = ["ADOBE_TILE_KEY", "HostsPage"]
