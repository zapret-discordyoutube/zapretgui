# dns/ui/custom_server_page.py
"""Страница «Свой DNS»: добавить свой DNS-сервер или изменить уже добавленный.

Открывается плиткой «Свой DNS» и пунктом «Редактировать» страницы
«Настройка DNS»; путь в хлебных крошках — «Настройка DNS → Новый DNS» (или
название сервера). Какой сервер править, странице сообщает команда
edit_custom_server (см. main.window_page_presenters).

Достаточно одной строки: адреса DoH (https://имя/dns-query) — IP-адреса
сервера программа найдёт и проверит сама. Можно, наоборот, вписать только
IP-адреса, это обычный DNS без шифрования.

Страница только собирает текст полей. Правила разбора — dns.custom_servers,
поиск адресов и запись в настройки идут в фоновой задаче DNS-слоя.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BreadcrumbBar,
    CaptionLabel,
    IndeterminateProgressBar,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
)

from app.ui_texts import tr as tr_catalog
from dns import custom_servers
from log.log import log
from ui.accessibility import (
    remove_line_edit_buttons_from_tab_order,
    set_breadcrumb_accessibility,
    set_control_accessibility,
    set_state_text,
)
from ui.fluent_widgets import SettingsCard, style_semantic_caption_label
from ui.latest_worker_lane import LatestWorkerLane
from ui.pages.base_page import BasePage

FORM_MAX_WIDTH = 720


class CustomDnsServerPage(BasePage):
    """Форма одного своего DNS-сервера: адрес DoH, IP-адреса и название."""

    def __init__(self, parent=None, *, deps):
        super().__init__(
            "Свой DNS",
            "",
            parent,
            title_key="page.network.custom_server.title",
        )
        self._dns = deps.dns_feature
        self._open_dns_page = deps.open_dns_page
        # Запись сервера, который правят; пустая — добавляется новый.
        self._server: dict = custom_servers.copy_server(None)
        self._busy = False
        # Сохранение ищет IP-адреса сервера в сети: кнопка пишет, чем занята.
        self._busy_lookup = False
        self._error_text = ""
        self._closed = False
        self._save_lane = LatestWorkerLane(
            name="dns_custom_server_save",
            create_worker=lambda request_id, server: self._dns.create_custom_server_worker(
                request_id, action="save", server=server, parent=self
            ),
            on_result=lambda _server, result: self._on_saved(result),
            on_error=lambda _server, error: self._on_save_failed(error),
            log_fn=log,
        )
        self._build_ui()
        self._retranslate()

    def _t(self, key: str, default: str, **values) -> str:
        text = tr_catalog(key, language=self._ui_language, default=default)
        return text.format(**values) if values else text

    # ── сборка ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Заголовок заменяют хлебные крошки «Настройка DNS → Новый DNS».
        if self.title_label is not None:
            self.title_label.hide()
        if self.subtitle_label is not None:
            self.subtitle_label.hide()

        self.breadcrumb = BreadcrumbBar(self.content)
        self.breadcrumb.currentItemChanged.connect(self._on_breadcrumb_item_changed)
        self.add_widget(self.breadcrumb)
        self.add_spacing(8)

        self.card = SettingsCard(parent=self.content)
        self.card.setMaximumWidth(FORM_MAX_WIDTH)
        form = QVBoxLayout()
        form.setContentsMargins(4, 2, 4, 2)
        form.setSpacing(4)

        self.doh_title, self.doh_edit, self.doh_hint, self.doh_error = self._field(form)
        form.addSpacing(14)
        self.addresses_title, self.addresses_edit, self.addresses_hint, self.addresses_error = self._field(form)
        form.addSpacing(14)
        self.name_title, self.name_edit, self.name_hint, self.name_error = self._field(form)

        # Ошибка, которая не относится к одному полю (сбой самой записи).
        self.error_label = self._error_label()
        form.addSpacing(6)
        form.addWidget(self.error_label)

        self.progress_bar = IndeterminateProgressBar(self.card, start=False)
        self.progress_bar.hide()
        form.addWidget(self.progress_bar)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 10, 0, 0)
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.cancel_button = PushButton("", self.card)
        self.cancel_button.clicked.connect(self._cancel)
        buttons.addWidget(self.cancel_button)
        self.save_button = PrimaryPushButton("", self.card)
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.save_button)
        form.addLayout(buttons)

        self.card.add_layout(form)
        # Форма не растягивается на всю ширину окна и прижата к левому краю.
        row = QWidget(self.content)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self.card, 1)
        row_layout.addStretch(0)
        self.add_widget(row)

        for edit in (self.doh_edit, self.addresses_edit, self.name_edit):
            edit.setClearButtonEnabled(True)
            edit.returnPressed.connect(self._save)
            edit.textEdited.connect(self._on_text_edited)
            remove_line_edit_buttons_from_tab_order(edit)

    def _error_label(self) -> CaptionLabel:
        label = CaptionLabel("", self.card)
        label.setWordWrap(True)
        style_semantic_caption_label(label, tone="error")
        label.hide()
        return label

    def _field(self, form: QVBoxLayout) -> tuple[StrongBodyLabel, LineEdit, CaptionLabel, CaptionLabel]:
        """Поле формы: подпись, строка ввода и пояснение под ней.

        Ошибка поля показывается на месте пояснения, прямо под строкой ввода.
        """
        title = StrongBodyLabel("", self.card)
        edit = LineEdit(self.card)
        hint = CaptionLabel("", self.card)
        hint.setWordWrap(True)
        hint.setTextColor(QColor(0, 0, 0, 150), QColor(255, 255, 255, 160))
        error = self._error_label()
        form.addWidget(title)
        form.addWidget(edit)
        form.addWidget(hint)
        form.addWidget(error)
        return title, edit, hint, error

    def _retranslate(self) -> None:
        t = self._t
        self.doh_title.setText(t("page.network.custom_server.doh", "Адрес DoH"))
        self.doh_edit.setPlaceholderText("https://dns.example.com/dns-query")
        self.doh_hint.setText(
            t(
                "page.network.custom_server.doh.hint",
                "Шифрованный DNS, как в браузере. Вставьте адрес — IP-адреса сервера программа найдёт и проверит сама.",
            )
        )
        self.addresses_title.setText(t("page.network.custom_server.addresses", "IP-адреса"))
        self.addresses_edit.setPlaceholderText("9.9.9.9 149.112.112.112 2620:fe::fe")
        self.addresses_hint.setText(
            t(
                "page.network.custom_server.addresses.hint",
                "Через пробел, первым — основной; IPv4 и IPv6 вместе. С адресом DoH поле можно оставить пустым.",
            )
        )
        self.name_title.setText(t("page.network.custom_server.name", "Название"))
        self.name_hint.setText(
            t("page.network.custom_server.name.hint", "Подпись на плитке. Можно не писать — подставится имя сервера.")
        )
        self.cancel_button.setText(t("page.network.custom_server.cancel", "Отмена"))
        set_control_accessibility(
            self.doh_edit,
            name=self.doh_title.text(),
            description=self.doh_hint.text(),
        )
        set_control_accessibility(
            self.addresses_edit,
            name=self.addresses_title.text(),
            description=self.addresses_hint.text(),
        )
        set_control_accessibility(
            self.name_edit,
            name=self.name_title.text(),
            description=self.name_hint.text(),
        )
        set_control_accessibility(
            self.cancel_button,
            name=t("page.network.custom_server.cancel.name", "Отмена: вернуться к настройке DNS"),
            description=t("page.network.custom_server.cancel.description", "Закрывает страницу без сохранения."),
        )
        self._render()

    def set_ui_language(self, language: str) -> None:
        super().set_ui_language(language)
        self._retranslate()

    # ── какой сервер открыт ─────────────────────────────────

    def handle_page_command(self, command: str, payload: dict) -> bool:
        if command != "edit_custom_server":
            return False
        self.open_server(payload.get("server"))
        return True

    def open_server(self, server: dict | None) -> None:
        """Показывает запись сервера в полях; None — чистая форма нового сервера."""
        self._abandon_save()
        self._server = custom_servers.copy_server(server)
        self.doh_edit.setText(self._server["doh"])
        self.addresses_edit.setText(custom_servers.addresses_text(self._server))
        self.name_edit.setText(self._server["name"])
        self._show_error("", "")
        self._render()

    def is_editing(self) -> bool:
        return bool(self._server["id"])

    def on_page_activated(self) -> None:
        (self.name_edit if self.is_editing() else self.doh_edit).setFocus(Qt.FocusReason.OtherFocusReason)

    def cleanup(self) -> None:
        self._closed = True
        self._save_lane.close()
        super().cleanup()

    # ── отрисовка ───────────────────────────────────────────

    def _current_title(self) -> str:
        if self.is_editing():
            return self._server["name"] or self._t("page.network.custom_server.title", "Свой DNS")
        return self._t("page.network.custom_server.new", "Новый DNS")

    def _render(self) -> None:
        items = (self._t("nav.page.network", "Настройка DNS"), self._current_title())
        self.breadcrumb.blockSignals(True)
        try:
            self.breadcrumb.clear()
            self.breadcrumb.addItem("dns", items[0])
            self.breadcrumb.addItem("server", items[1])
            set_breadcrumb_accessibility(self.breadcrumb, items)
        finally:
            self.breadcrumb.blockSignals(False)
        self._render_save_button()
        self._render_name_placeholder()

    def _render_save_button(self) -> None:
        if self._busy and self._busy_lookup:
            text = self._t("page.network.custom_server.checking", "Проверяю сервер…")
        elif self._busy:
            text = self._t("page.network.custom_server.saving", "Сохраняю…")
        elif self.is_editing():
            text = self._t("page.network.custom_server.save", "Сохранить")
        else:
            text = self._t("page.network.custom_server.add", "Добавить")
        self.save_button.setText(text)
        set_control_accessibility(
            self.save_button,
            name=text,
            description=self._t(
                "page.network.custom_server.save.description",
                "Сохраняет сервер и возвращает к списку DNS. Адреса сервера DoH перед этим проверяются.",
            ),
        )

    def _render_name_placeholder(self) -> None:
        """В пустом поле названия видно, каким оно станет само."""
        form = self._read_form()
        fallback = self._t("page.network.custom_server.name.placeholder", "Например, Мой DNS")
        self.name_edit.setPlaceholderText(form.server["name"] if form.server and not self.name_edit.text().strip() else fallback)

    def _show_error(self, text: str, field: str) -> None:
        """Ошибка встаёт на место пояснения своего поля; пустой текст убирает все ошибки."""
        fields = {
            custom_servers.FIELD_DOH: (self.doh_edit, self.doh_hint, self.doh_error),
            custom_servers.FIELD_ADDRESSES: (self.addresses_edit, self.addresses_hint, self.addresses_error),
            custom_servers.FIELD_NAME: (self.name_edit, self.name_hint, self.name_error),
        }
        shown = self.error_label
        for name, (edit, hint, error) in fields.items():
            here = bool(text) and name == field
            edit.setError(here)
            hint.setVisible(not here)
            error.setVisible(here)
            if here:
                shown = error
        self.error_label.setVisible(bool(text) and shown is self.error_label)
        self._error_text = text
        if not text:
            return
        shown.setText(text)
        set_state_text(shown, self._t("page.network.custom_server.error", "Ошибка: {text}", text=text))
        if field in fields:
            fields[field][0].setFocus(Qt.FocusReason.OtherFocusReason)

    def error_text(self) -> str:
        """Текст показанной ошибки или пустая строка."""
        return self._error_text

    def _set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        # Поля не выключаются, а запираются: выключение увело бы фокус с поля.
        for edit in (self.doh_edit, self.addresses_edit, self.name_edit):
            edit.setReadOnly(self._busy)
        self.progress_bar.setVisible(self._busy)
        if self._busy:
            self.progress_bar.start()
        else:
            self.progress_bar.stop()
        self._render_save_button()

    # ── действия ────────────────────────────────────────────

    def _read_form(self) -> custom_servers.CustomServerForm:
        return custom_servers.read_form(
            server_id=self._server["id"],
            name=self.name_edit.text(),
            doh=self.doh_edit.text(),
            addresses=self.addresses_edit.text(),
        )

    def _on_text_edited(self, _text: str) -> None:
        if self._error_text:
            self._show_error("", "")
        self._render_name_placeholder()

    def _on_breadcrumb_item_changed(self, key: str) -> None:
        # Клик по крошке обрезал путь: восстанавливаем его для следующего захода.
        self._render()
        if key == "dns":
            self._cancel()

    def _cancel(self) -> None:
        self._abandon_save()
        self._open_dns_page()

    def _abandon_save(self) -> None:
        """Незаконченное сохранение больше не нужно: обрываем поиск адресов и не ждём его итога."""
        if not self._busy:
            return
        stop = getattr(self._save_lane.runtime.worker, "stop", None)
        if callable(stop):
            try:
                stop()
            except RuntimeError:
                pass
        self._set_busy(False)

    def _save(self) -> None:
        if self._busy or self._closed:
            return
        form = self._read_form()
        if form.server is None:
            self._show_error(form.error, form.field)
            return
        self._show_error("", "")
        self._busy_lookup = form.needs_lookup
        self._set_busy(True)
        self._save_lane.request(form.server)

    def _on_saved(self, result) -> None:
        # Не занята — значит сохранение бросили (отмена, открыт другой сервер): его итог не нужен.
        if self._closed or not self._busy:
            return
        self._set_busy(False)
        if not getattr(result, "success", False):
            self._show_error(str(getattr(result, "error", "") or ""), str(getattr(result, "field", "") or ""))
            return
        server = custom_servers.copy_server(getattr(result, "server", None))
        added = not self.is_editing()
        title = (
            self._t("page.network.custom_server.added", "Сервер «{name}» добавлен", name=server["name"])
            if added
            else self._t("page.network.custom_server.saved", "Сервер «{name}» сохранён", name=server["name"])
        )
        notice = str(getattr(result, "notice", "") or "")
        content = notice or self._t(
            "page.network.custom_server.saved.content",
            "Адреса: {addresses}. Чтобы включить сервер, нажмите его плитку.",
            addresses=", ".join([*server["ipv4"], *server["ipv6"]]),
        )
        (InfoBar.warning if notice else InfoBar.success)(
            title=title,
            content=content,
            orient=Qt.Orientation.Vertical,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=8000 if notice else 5000,
            parent=self.window(),
        )
        # Кнопка «назад» может вернуть сюда: форма уже показывает сохранённый сервер, а не черновик.
        self.open_server(server)
        self._open_dns_page()

    def _on_save_failed(self, error: str) -> None:
        if self._closed or not self._busy:
            return
        self._set_busy(False)
        self._show_error(
            self._t("page.network.custom_server.save_failed", "Не удалось сохранить сервер: {error}", error=error),
            "",
        )


__all__ = ["CustomDnsServerPage"]
