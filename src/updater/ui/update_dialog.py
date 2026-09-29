"""Большое окно обновления и окно «Что нового».

Одно окно на любой путь обновления: проверка при запуске, ручная проверка на
странице «Серверы», кнопка в «О программе». Окно только показывает и
сообщает о нажатиях сигналами; скачивает ``UpdateInstallService``, а что
показывать, хранит ``UpdateFlow`` (его переживает закрытие окна кнопкой
«Скрыть»).

- ``UpdateDialog`` — предложение обновиться: вкладки «Что нового» (изменения
  всех пропущенных версий) и «Подробности», кнопки «Обновить», «Позже»,
  «Пропустить версию», «Открыть в браузере», «Telegram». Во время загрузки
  внизу бежит логотип по переливающейся полосе и меняются шутки.
- ``WhatsNewDialog`` — только почитать изменения установленной версии.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    SubtitleLabel,
    TextBrowser,
    TransparentPushButton,
)

from config.build_info import APP_VERSION, CHANNEL
from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_dialog import MessageBoxBase
from ui.segmented_accessibility import set_segmented_items_accessibility
from ui.theme import get_theme_tokens
from ui.theme_refresh import ThemeRefreshBinding
from ui.widgets.fun import FunTicker, Mascot, UpdateRunway, burst_confetti
from ui.widgets.fun.mascot import MOOD_BUSY, MOOD_HAPPY, MOOD_IDLE
from updater.channel_utils import is_dev_update_channel
from updater.ui import plans
from updater.ui.fun_texts import phrases as fun_phrases
from updater.ui.update_flow import (
    PHASE_DOWNLOADING,
    PHASE_FAILED,
    PHASE_INSTALLING,
    PHASE_OFFER,
    UpdateFlow,
)

_MIN_WIDTH = 720
_MIN_HEIGHT = 440
_MAX_WIDTH = 1180
_MAX_HEIGHT = 860


def _dialog_size(parent) -> tuple[int, int]:
    """Окно занимает большую часть окна программы, но не меньше минимума."""
    try:
        window = parent.window() if parent is not None else None
        host_width = int(window.width()) if window is not None else 0
        host_height = int(window.height()) if window is not None else 0
    except Exception:
        host_width = host_height = 0
    width = max(_MIN_WIDTH, min(_MAX_WIDTH, int(host_width * 0.72)))
    # Высота считается для текста: шапка и кнопки добавляются сверху.
    height = max(_MIN_HEIGHT - 200, min(_MAX_HEIGHT - 260, int(host_height * 0.78) - 260))
    return width, height


class _ReleaseDialogBase(MessageBoxBase):
    """Шапка с талисманом, поле с текстом выпусков и ряд своих кнопок."""

    link_clicked = pyqtSignal(str)

    def __init__(self, parent, *, language: str = "ru") -> None:
        super().__init__(parent)
        self._language = str(language or "ru")
        self._history: tuple = ()
        self._history_empty_text = self._t("history.empty", "Описание изменений не опубликовано.")
        self.setClosableOnMaskClicked(False)

        # Стандартные «ОК/Отмена» не нужны: у окна свой ряд кнопок.
        self.yesButton.hide()
        self.cancelButton.hide()
        self.buttonLayout.removeWidget(self.yesButton)
        self.buttonLayout.removeWidget(self.cancelButton)

        header = QHBoxLayout()
        header.setSpacing(14)
        self.mascot = Mascot(self.widget, size=52)
        header.addWidget(self.mascot, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title_label = SubtitleLabel("", self.widget)
        self.title_label.setWordWrap(True)
        titles.addWidget(self.title_label)
        self.subtitle_label = CaptionLabel("", self.widget)
        self.subtitle_label.setWordWrap(True)
        titles.addWidget(self.subtitle_label)
        titles.addStretch(1)
        header.addLayout(titles, 1)
        self.viewLayout.addLayout(header)

        self.browser = TextBrowser(self.widget)
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(lambda url: self.link_clicked.emit(url.toString()))
        width, height = _dialog_size(parent)
        self.widget.setMinimumWidth(width)
        self.browser.setMinimumHeight(height)
        set_control_accessibility(
            self.browser,
            name=self._t("history.accessible_name", "Список изменений"),
            description=self._t(
                "history.accessible_description",
                "Что изменилось в каждой версии, от новой к старой. Ссылки открываются в браузере.",
            ),
        )

        self._buttons_left = QHBoxLayout()
        self._buttons_left.setSpacing(6)
        self._buttons_right = QHBoxLayout()
        self._buttons_right.setSpacing(10)
        self.buttonLayout.addLayout(self._buttons_left)
        self.buttonLayout.addStretch(1)
        self.buttonLayout.addLayout(self._buttons_right)

        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)

    # --- общее ---------------------------------------------------------------

    def _t(self, key: str, default: str) -> str:
        return plans.update_flow_text(self._language, key, default)

    def _make_button(self, cls, text: str, *, icon=None, name: str, description: str, on_click):
        button = cls(text, self.buttonGroup) if icon is None else cls(icon, text, self.buttonGroup)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        set_control_accessibility(button, name=name, description=description)
        set_state_text(button, name)
        button.clicked.connect(on_click)
        return button

    def set_history(self, history) -> None:
        self._history = tuple(item for item in history or () if isinstance(item, dict))
        self._render_history()

    def _render_history(self) -> None:
        tokens = get_theme_tokens()
        html = plans.release_history_html(
            self._history,
            accent_hex=tokens.accent_hex,
            muted_hex=tokens.fg_muted,
            language=self._language,
            empty_text=self._history_empty_text,
        )
        self.browser.setHtml(html)
        plain = " ".join(
            f"v{item.get('version', '')}: {' '.join(str(item.get('notes') or '').split())}"
            for item in self._history
        )
        set_state_text(self.browser, plain or self._history_empty_text)

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = (tokens, force)
        if self._history:
            self._render_history()


class UpdateDialog(_ReleaseDialogBase):
    """Предложение обновиться и ход загрузки. Состояние берёт из ``UpdateFlow``."""

    install_clicked = pyqtSignal()
    later_clicked = pyqtSignal()
    skip_clicked = pyqtSignal()
    hide_clicked = pyqtSignal()
    telegram_clicked = pyqtSignal()

    def __init__(self, parent, *, flow: UpdateFlow, language: str = "ru") -> None:
        super().__init__(parent, language=language)
        self._flow = flow
        self._rendered_phase = ""
        self._ticker_kind = ""

        self.tabs = SegmentedWidget(self.widget)
        self.tabs.addItem(
            routeKey="changes",
            text=" " + self._t("tab.changes", "Что нового"),
            onClick=lambda: self._switch_tab(0),
        )
        self.tabs.addItem(
            routeKey="details",
            text=" " + self._t("tab.details", "Подробности"),
            onClick=lambda: self._switch_tab(1),
        )
        self.tabs.setCurrentItem("changes")
        set_segmented_items_accessibility(
            self.tabs,
            name=self._t("tab.accessible_name", "Разделы окна обновления"),
            labels={
                "changes": self._t("tab.changes", "Что нового"),
                "details": self._t("tab.details", "Подробности"),
            },
        )
        self.viewLayout.addWidget(self.tabs, 0, Qt.AlignmentFlag.AlignLeft)

        self.stack = QStackedWidget(self.widget)
        self.stack.addWidget(self.browser)
        self.details_widget = QWidget(self.widget)
        details_layout = QVBoxLayout(self.details_widget)
        details_layout.setContentsMargins(4, 8, 4, 8)
        details_layout.setSpacing(10)
        self.details_label = BodyLabel("", self.details_widget)
        self.details_label.setWordWrap(True)
        self.details_label.setTextFormat(Qt.TextFormat.RichText)
        details_layout.addWidget(self.details_label)
        details_layout.addStretch(1)
        self.stack.addWidget(self.details_widget)
        self.viewLayout.addWidget(self.stack, 1)

        # Панель загрузки: логотип бежит по полосе, под ней цифры и шутки.
        self.download_panel = QWidget(self.widget)
        self.download_panel.setObjectName("updateDownloadPanel")
        self.download_panel.setStyleSheet("QWidget#updateDownloadPanel { background: transparent; }")
        panel = QVBoxLayout(self.download_panel)
        panel.setContentsMargins(0, 4, 0, 0)
        panel.setSpacing(6)
        self.stage_label = BodyLabel("", self.download_panel)
        self.stage_label.setWordWrap(True)
        panel.addWidget(self.stage_label)
        self.runway = UpdateRunway(self.download_panel)
        panel.addWidget(self.runway)
        stats = QHBoxLayout()
        stats.setSpacing(18)
        self.size_label = CaptionLabel("", self.download_panel)
        self.percent_label = CaptionLabel("", self.download_panel)
        self.speed_label = CaptionLabel("", self.download_panel)
        self.eta_label = CaptionLabel("", self.download_panel)
        for label in (self.percent_label, self.size_label, self.speed_label, self.eta_label):
            stats.addWidget(label)
        stats.addStretch(1)
        panel.addLayout(stats)
        self.ticker = FunTicker(self.download_panel)
        panel.addWidget(self.ticker)
        self.download_panel.hide()
        self.viewLayout.addWidget(self.download_panel)

        # Кнопки. Видимость каждой задаёт текущая фаза.
        self.browser_btn = self._make_button(
            TransparentPushButton,
            self._t("button.browser", "Открыть в браузере"),
            icon=FluentIcon.GLOBE,
            name=self._t("button.browser", "Открыть в браузере"),
            description=self._t("button.browser_description", "Открывает страницу выпуска на Forgejo."),
            on_click=self._on_browser,
        )
        self.telegram_btn = self._make_button(
            TransparentPushButton,
            self._t("button.telegram", "Telegram"),
            icon=FluentIcon.SEND,
            name=self._t("button.telegram_name", "Открыть Telegram-канал"),
            description=self._t(
                "button.telegram_description",
                "Все версии программы выкладываются в Telegram-канале — можно скачать вручную.",
            ),
            on_click=self.telegram_clicked.emit,
        )
        self._buttons_left.addWidget(self.browser_btn)
        self._buttons_left.addWidget(self.telegram_btn)

        self.skip_btn = self._make_button(
            PushButton,
            self._t("button.skip", "Пропустить версию"),
            name=self._t("button.skip", "Пропустить версию"),
            description=self._t(
                "button.skip_description",
                "При запуске больше не напоминать об этой версии. Следующая версия снова покажет окно.",
            ),
            on_click=self._on_skip,
        )
        self.later_btn = self._make_button(
            PushButton,
            self._t("button.later", "Позже"),
            name=self._t("button.later_name", "Отложить обновление"),
            description=self._t("button.later_description", "Закрывает окно. Обновление напомнит о себе при следующем запуске."),
            on_click=self._on_later,
        )
        self.hide_btn = self._make_button(
            PushButton,
            self._t("button.hide", "Скрыть"),
            icon=FluentIcon.MINIMIZE,
            name=self._t("button.hide_name", "Скрыть окно обновления"),
            description=self._t(
                "button.hide_description",
                "Загрузка продолжится. Вернуть окно можно на странице «Серверы».",
            ),
            on_click=self._on_hide,
        )
        self.close_btn = self._make_button(
            PushButton,
            self._t("button.close", "Закрыть"),
            name=self._t("button.close", "Закрыть"),
            description=self._t("button.close_description", "Закрывает окно обновления."),
            on_click=self._on_later,
        )
        self.install_btn = self._make_button(
            PrimaryPushButton,
            self._t("button.install", "Обновить"),
            icon=FluentIcon.DOWNLOAD,
            name=self._t("button.install_name", "Скачать и установить обновление"),
            description=self._t(
                "button.install_description",
                "Скачивает новую версию и запускает установщик. Программа закроется и откроется снова.",
            ),
            on_click=self.install_clicked.emit,
        )
        for button in (self.skip_btn, self.later_btn, self.hide_btn, self.close_btn, self.install_btn):
            self._buttons_right.addWidget(button)

        self._flow.changed.connect(self._render)
        self.finished.connect(self._detach_flow)
        self._render()

    # --- действия -------------------------------------------------------------

    def _switch_tab(self, index: int) -> None:
        self.stack.setCurrentIndex(int(index))

    def _on_browser(self) -> None:
        offer = self._flow.offer
        if offer is not None and offer.url:
            self.link_clicked.emit(offer.url)

    def _on_skip(self) -> None:
        self.skip_clicked.emit()
        self.reject()

    def _on_later(self) -> None:
        self.later_clicked.emit()
        self.reject()

    def _on_hide(self) -> None:
        self.hide_clicked.emit()
        self.reject()

    def _detach_flow(self, _code: int = 0) -> None:
        try:
            self._flow.changed.disconnect(self._render)
        except (TypeError, RuntimeError):
            pass
        self.ticker.stop()

    # --- отрисовка ------------------------------------------------------------

    def _render(self) -> None:
        flow = self._flow
        offer = flow.offer
        phase = flow.phase
        phase_changed = phase != self._rendered_phase
        self._rendered_phase = phase

        if offer is not None and ((phase_changed and phase == PHASE_OFFER) or not self._history):
            self.set_history(offer.history)
            self._render_details()

        version = offer.version if offer is not None else ""
        current = offer.current_version if offer is not None else APP_VERSION
        self.subtitle_label.setText(self._subtitle_text(current, version, offer))

        downloading = phase in {PHASE_DOWNLOADING, PHASE_INSTALLING, PHASE_FAILED}
        self.download_panel.setVisible(downloading)
        self.mascot.setVisible(not downloading)
        self.browser_btn.setVisible(bool(offer is not None and offer.url) and phase in {PHASE_OFFER, PHASE_FAILED})
        self.telegram_btn.setVisible(phase in {PHASE_OFFER, PHASE_FAILED})
        self.skip_btn.setVisible(phase == PHASE_OFFER)
        self.later_btn.setVisible(phase == PHASE_OFFER)
        self.hide_btn.setVisible(phase in {PHASE_DOWNLOADING, PHASE_INSTALLING})
        self.close_btn.setVisible(phase == PHASE_FAILED)
        self.install_btn.setVisible(phase in {PHASE_OFFER, PHASE_FAILED})

        if phase == PHASE_OFFER:
            self.title_label.setText(self._t("title.available", "Доступно обновление"))
            self.install_btn.setText(self._t("button.install", "Обновить"))
            if phase_changed:
                self.mascot.set_mood(MOOD_IDLE)
                self.ticker.stop()
                self.runway.reset()
        elif phase == PHASE_DOWNLOADING:
            self.title_label.setText(
                self._t("title.downloading_template", "Загружаем v{version}").format(version=version)
            )
            if phase_changed:
                self.mascot.set_mood(MOOD_BUSY)
                self.runway.reset()
                self._start_ticker("preparing")
            self._render_progress()
        elif phase == PHASE_INSTALLING:
            self.title_label.setText(self._t("title.installing", "Устанавливаем…"))
            self._render_progress()
            if phase_changed:
                self.runway.finish()
                self._start_ticker("installing")
                QTimer.singleShot(250, lambda: burst_confetti(self.widget))
        elif phase == PHASE_FAILED:
            self.title_label.setText(self._t("title.failed", "Не удалось загрузить обновление"))
            self.install_btn.setText(self._t("button.retry", "Повторить"))
            self._render_progress()
            if phase_changed:
                self.runway.fail()
                self._start_ticker("failed")
        if phase_changed:
            self._focus_main_button()
        self._update_accessibility()

    def _focus_main_button(self) -> None:
        # Фокус — на главной видимой кнопке фазы, а не на поле текста: у поля
        # появилась бы акцентная рамка фокуса, чуждая безрамочному окну.
        for button in (self.install_btn, self.hide_btn, self.close_btn):
            if not button.isHidden():
                button.setFocus()
                return

    def _subtitle_text(self, current: str, version: str, offer) -> str:
        if not version:
            return ""
        parts = [
            self._t("subtitle.transition_template", "v{current}  →  v{target}").format(
                current=current, target=version
            )
        ]
        count = len(offer.history) if offer is not None else 0
        if count > 1:
            parts.append(self._t("subtitle.versions_template", "версий в обновлении: {count}").format(count=count))
        if offer is not None and offer.source:
            parts.append(self._t("subtitle.source_template", "источник: {source}").format(source=offer.source))
        return "   ·   ".join(parts)

    def _render_details(self) -> None:
        offer = self._flow.offer
        if offer is None:
            self.details_label.setText("")
            return
        channel = "Dev" if is_dev_update_channel(CHANNEL) else "Stable"
        rows = (
            (self._t("details.current", "Установлена"), f"v{offer.current_version}"),
            (self._t("details.target", "Новая версия"), f"v{offer.version}"),
            (self._t("details.channel", "Канал обновлений"), channel),
            (self._t("details.source", "Источник"), offer.source or "—"),
            (self._t("details.count", "Версий в обновлении"), str(max(len(offer.history), 1))),
        )
        muted = get_theme_tokens().fg_muted
        html = "".join(
            f"<p style='margin: 0 0 8px 0;'><span style='color: {muted};'>{name}:</span>&nbsp; <b>{value}</b></p>"
            for name, value in rows
        )
        self.details_label.setText(html)
        set_state_text(self.details_label, "; ".join(f"{name}: {value}" for name, value in rows))

    def _render_progress(self) -> None:
        progress = self._flow.progress
        phase = self._flow.phase
        if phase == PHASE_FAILED:
            self.stage_label.setText(progress.error_text or self._t("stage.failed", "Загрузка прервалась"))
        else:
            self.stage_label.setText(progress.stage_text)
        if phase == PHASE_DOWNLOADING:
            if progress.known_size:
                self.runway.set_value(progress.percent)
                # Байты пошли: разминочные шутки сменяются дорожными.
                if self._ticker_kind == "preparing":
                    self._start_ticker("downloading")
            else:
                self.runway.set_indeterminate(True)
        self.percent_label.setText(f"{int(progress.percent)}%" if progress.known_size else "")
        self.size_label.setText(progress.size_text)
        self.speed_label.setText(progress.speed_text if phase == PHASE_DOWNLOADING else "")
        self.eta_label.setText(progress.eta_text if phase == PHASE_DOWNLOADING else "")

    def _start_ticker(self, kind: str) -> None:
        self._ticker_kind = kind
        self.ticker.set_phrases(fun_phrases(kind, self._language))
        if not self.ticker.is_running():
            self.ticker.start()

    def _update_accessibility(self) -> None:
        title = str(self.title_label.text() or "").strip()
        subtitle = str(self.subtitle_label.text() or "").strip()
        set_control_accessibility(
            self.widget,
            name=title,
            description=self._t(
                "accessible_description",
                "Окно обновления: список изменений, подробности и кнопки установки.",
            ),
        )
        set_state_text(self.widget, f"{title}. {subtitle}" if subtitle else title)
        set_state_text(self.stage_label, f"Этап обновления: {self.stage_label.text() or '—'}")

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        super()._apply_theme_refresh(tokens, force)
        self._render_details()


class WhatsNewDialog(_ReleaseDialogBase):
    """«Что нового» в установленной версии: только почитать."""

    def __init__(self, parent, *, version: str, history=(), loading: bool = False, language: str = "ru") -> None:
        super().__init__(parent, language=language)
        self._version = str(version or "")
        self._url = ""
        self.title_label.setText(
            self._t("whats_new.title_template", "Что нового в v{version}").format(version=self._version)
        )
        self.viewLayout.addWidget(self.browser, 1)

        self.loading_panel = QWidget(self.widget)
        self.loading_panel.setObjectName("whatsNewLoadingPanel")
        self.loading_panel.setStyleSheet("QWidget#whatsNewLoadingPanel { background: transparent; }")
        loading_layout = QVBoxLayout(self.loading_panel)
        loading_layout.setContentsMargins(0, 0, 0, 0)
        self.runway = UpdateRunway(self.loading_panel)
        loading_layout.addWidget(self.runway)
        self.ticker = FunTicker(self.loading_panel)
        loading_layout.addWidget(self.ticker)
        self.viewLayout.addWidget(self.loading_panel)

        self.browser_btn = self._make_button(
            TransparentPushButton,
            self._t("button.browser", "Открыть в браузере"),
            icon=FluentIcon.GLOBE,
            name=self._t("button.browser", "Открыть в браузере"),
            description=self._t("button.browser_description", "Открывает страницу выпуска на Forgejo."),
            on_click=self._on_browser,
        )
        self._buttons_left.addWidget(self.browser_btn)
        self.ok_btn = self._make_button(
            PrimaryPushButton,
            self._t("whats_new.ok", "Понятно"),
            icon=FluentIcon.ACCEPT,
            name=self._t("whats_new.ok_name", "Закрыть «Что нового»"),
            description=self._t("whats_new.ok_description", "Закрывает окно со списком изменений."),
            on_click=self.accept,
        )
        self._buttons_right.addWidget(self.ok_btn)
        self.ok_btn.setFocus()
        self.finished.connect(lambda _code: self.ticker.stop())

        if loading:
            self.show_loading()
        else:
            self.set_history(history)

    def show_loading(self) -> None:
        self.subtitle_label.setText(self._t("whats_new.loading", "Загружаем список изменений…"))
        self.browser.setHtml("")
        self.browser_btn.hide()
        self.loading_panel.show()
        self.mascot.set_mood(MOOD_BUSY)
        self.runway.reset()
        self.ticker.set_phrases(fun_phrases("whats_new", self._language))
        self.ticker.start()

    def set_history(self, history) -> None:
        self.loading_panel.hide()
        self.ticker.stop()
        self.mascot.set_mood(MOOD_HAPPY)
        super().set_history(history)
        count = len(self._history)
        self.subtitle_label.setText(
            self._t("whats_new.subtitle_many_template", "Версий в списке: {count}").format(count=count)
            if count > 1
            else self._t("whats_new.subtitle", "Список изменений этого выпуска")
        )
        self._url = next((str(item.get("url") or "") for item in self._history if item.get("url")), "")
        self.browser_btn.setVisible(bool(self._url))

    def show_error(self, message: str) -> None:
        self.loading_panel.hide()
        self.ticker.stop()
        self.mascot.set_mood(MOOD_IDLE)
        self.subtitle_label.setText(str(message or ""))
        self._url = ""
        from updater.release.forgejo import release_page_url

        if self._version:
            self._url = release_page_url(self._version)
        self.browser_btn.setVisible(bool(self._url))
        self.browser.setHtml("")

    def _on_browser(self) -> None:
        if self._url:
            self.link_clicked.emit(self._url)


def open_url_in_background(url: str) -> None:
    """Ссылка из окна открывается в фоне: запуск браузера не держит интерфейс."""
    import threading

    from log.log import log

    def run() -> None:
        try:
            from app.external_actions import open_url

            result = open_url(str(url or ""))
            if not getattr(result, "ok", True):
                log(f"Не удалось открыть ссылку {url}: {getattr(result, 'error', '')}", "WARNING")
        except Exception as exc:
            log(f"Не удалось открыть ссылку {url}: {exc}", "WARNING")

    threading.Thread(target=run, name="update-dialog-open-url", daemon=True).start()


def show_whats_new_dialog(parent, *, version: str, history=(), loading: bool = False, language: str = "ru") -> WhatsNewDialog:
    """Открывает «Что нового» поверх окна программы и возвращает окно."""
    dialog = WhatsNewDialog(parent, version=version, history=history, loading=loading, language=language)
    dialog.link_clicked.connect(open_url_in_background)
    dialog.finished.connect(lambda _code: dialog.deleteLater())
    dialog.open()
    return dialog


class _HistoryBridge(QObject):
    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)


def open_whats_new_window(parent, *, updater_feature, version: str, language: str = "ru") -> WhatsNewDialog:
    """«Что нового» из «О программе»: окно сразу, текст — из фона.

    Сначала сохранённое перед установкой (там все пропущенные версии), иначе
    текст выпуска с Forgejo.
    """
    import threading

    dialog = show_whats_new_dialog(parent, version=version, loading=True, language=language)
    bridge = _HistoryBridge(dialog)

    def on_loaded(history) -> None:
        try:
            dialog.set_history(history)
        except RuntimeError:
            pass

    def on_failed(error: str) -> None:
        try:
            dialog.show_error(
                plans.update_flow_text(
                    language,
                    "whats_new.load_error_template",
                    "Не удалось загрузить список изменений: {error}",
                ).format(error=error)
            )
        except RuntimeError:
            pass

    bridge.loaded.connect(on_loaded)
    bridge.failed.connect(on_failed)

    def run() -> None:
        try:
            history = tuple(updater_feature.load_release_history(version) or ())
        except Exception as exc:
            signal, payload = bridge.failed, str(exc) or type(exc).__name__
        else:
            signal, payload = bridge.loaded, history
        try:
            signal.emit(payload)
        except RuntimeError:
            # Окно уже закрыли — показывать некому.
            pass

    threading.Thread(target=run, name="whats-new-load", daemon=True).start()
    return dialog


__all__ = [
    "UpdateDialog",
    "WhatsNewDialog",
    "open_url_in_background",
    "open_whats_new_window",
    "show_whats_new_dialog",
]
