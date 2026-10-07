# dns/ui/custom_server_page.py
"""Страница «Свой DNS»: добавить свой DNS-сервер или изменить уже добавленный.

Открывается плиткой «Свой DNS» и пунктом «Редактировать» страницы
«Настройка DNS»; путь в хлебных крошках — «Настройка DNS → Новый DNS» (или
название сервера). Какой сервер править, странице сообщает команда
edit_custom_server (см. main.window_page_presenters).

Страница собрана так же, как остальные страницы настроек:

- шапка — карточка в стиле панели «Сейчас» страницы DNS: значок своего
  сервера, название и строка состояния. Строка сразу показывает, что
  получится из введённого (шифрованный DNS или обычный, какие адреса), а
  при сохранении — ход проверки и её ошибку. Справа кнопки «Отмена» и
  «Добавить»;
- ниже — три строки настроек во всю ширину: адрес DoH, IP-адреса, название.

Достаточно одной строки: адреса DoH (https://имя/dns-query) — IP-адреса
сервера программа найдёт и проверит сама. Можно, наоборот, вписать только
IP-адреса, это обычный DNS без шифрования.

Страница только собирает текст полей. Правила разбора — dns.custom_servers,
поиск адресов и запись в настройки идут в фоновой задаче DNS-слоя.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    BreadcrumbBar,
    CaptionLabel,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SimpleCardWidget,
    SubtitleLabel,
)

from app.ui_texts import tr as tr_catalog
from dns import custom_servers
from dns.ui.now_panel import DnsBadge
from log.log import log
from ui.accessibility import (
    remove_line_edit_buttons_from_tab_order,
    set_breadcrumb_accessibility,
    set_control_accessibility,
    set_state_text,
)
from ui.fluent_widgets import style_semantic_caption_label
from ui.latest_worker_lane import LatestWorkerLane
from ui.pages.base_page import BasePage
from ui.widgets.win11_controls import Win11ControlRow

# Значок и цвет своего сервера — те же, что на его плитке в списке DNS.
SERVER_ICON = "fa5s.edit"
SERVER_COLOR = "#22c55e"
FIELD_WIDTH = 440
BUTTON_MIN_WIDTH = 120

# Строки формы: ключ перевода и текст по умолчанию для названия и пояснения.
ROW_DOH = (
    ("page.network.custom_server.doh", "Адрес DoH"),
    ("page.network.custom_server.doh.hint", "Шифрованный DNS, как в браузере. Одной этой строки достаточно"),
)
ROW_ADDRESSES = (
    ("page.network.custom_server.addresses", "IP-адреса"),
    ("page.network.custom_server.addresses.hint", "Через пробел, основной — первым. IPv4 и IPv6 вместе"),
)
ROW_NAME = (
    ("page.network.custom_server.name", "Название"),
    ("page.network.custom_server.name.hint", "Подпись плитки в списке DNS"),
)


class _FormHost(QWidget):
    """Вся форма: шапка и строки под ней.

    Желаемый размер считается для узкой формы нарочно. Страница берёт эту
    высоту как наибольшую и потом только уменьшает её под настоящую ширину.
    Без этого длинная ошибка в шапке, перенесённая на третью строку в узком
    окне, отнимала бы высоту у строк под ней.
    """

    HINT_WIDTH = 560

    def sizeHint(self) -> QSize:  # noqa: N802
        hint = super().sizeHint()
        width = min(hint.width(), self.HINT_WIDTH)
        return QSize(width, max(hint.height(), self.heightForWidth(width)))


class CustomDnsServerPage(BasePage):
    """Один свой DNS-сервер: шапка с итогом и три строки — адрес DoH, IP-адреса, название."""

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
        # Сохранение ищет IP-адреса сервера в сети: шапка и кнопка пишут, чем заняты.
        self._busy_lookup = False
        self._error_text = ""
        # Последний понятный итог введённого: держится, пока поле заполнено наполовину.
        self._settled_detail = ""
        # Длина текста поля до последней правки: по скачку длины видно вставку.
        self._typed_length: dict[LineEdit, int] = {}
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

        # Вся форма целиком: шапка и строки под ней.
        self.card = _FormHost(self.content)
        form = QVBoxLayout(self.card)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(16)
        form.addWidget(self._build_header())

        # Строки стоят плотно, как в группе настроек.
        rows = QVBoxLayout()
        rows.setSpacing(3)
        self.doh_row, self.doh_edit = self._row("fa5s.lock", ROW_DOH)
        self.addresses_row, self.addresses_edit = self._row("fa5s.network-wired", ROW_ADDRESSES)
        self.name_row, self.name_edit = self._row("fa5s.tag", ROW_NAME)
        for row in (self.doh_row, self.addresses_row, self.name_row):
            rows.addWidget(row)
        form.addLayout(rows)
        self.add_widget(self.card)

        self.doh_edit.textEdited.connect(lambda text: self._on_text_edited(self.doh_edit, text))
        self.addresses_edit.textEdited.connect(lambda text: self._on_text_edited(self.addresses_edit, text))
        self.name_edit.textEdited.connect(lambda text: self._on_text_edited(self.name_edit, text))

    def _build_header(self) -> QWidget:
        """Шапка: значок сервера, что получится из введённого и кнопки действия."""
        self.header = SimpleCardWidget(self.card)
        self.header.setObjectName("customDnsHeader")
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # Вокруг круга значка место под свечение: внешний отступ на столько же меньше.
        glow_pad = (DnsBadge.BOX - DnsBadge.CIRCLE) // 2
        layout = QHBoxLayout(self.header)
        layout.setContentsMargins(20 - glow_pad, 18 - glow_pad, 20, 18 - glow_pad)
        layout.setSpacing(16 - glow_pad)

        self.badge = DnsBadge(self.header)
        self.badge.set_icon(SERVER_ICON, SERVER_COLOR)
        layout.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setContentsMargins(0, glow_pad, 0, glow_pad)
        text.setSpacing(2)
        self.eyebrow_label = CaptionLabel("", self.header)
        self.eyebrow_label.setTextColor(QColor(0, 0, 0, 115), QColor(255, 255, 255, 125))
        text.addWidget(self.eyebrow_label)
        self.title_text = SubtitleLabel("", self.header)
        text.addWidget(self.title_text)
        self.detail_label = BodyLabel("", self.header)
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextColor(QColor(0, 0, 0, 160), QColor(255, 255, 255, 170))
        text.addWidget(self.detail_label)
        # Ошибка встаёт на место строки состояния.
        self.error_label = BodyLabel("", self.header)
        self.error_label.setWordWrap(True)
        style_semantic_caption_label(self.error_label, tone="error")
        self.error_label.hide()
        text.addWidget(self.error_label)
        # Растяжку сюда не ставить: с ней шапка забирает всю свободную высоту страницы.
        layout.addLayout(text, 1)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(12, glow_pad, 0, 0)
        buttons.setSpacing(8)
        self.cancel_button = PushButton("", self.header)
        self.cancel_button.clicked.connect(self._cancel)
        self.save_button = PrimaryPushButton("", self.header)
        self.save_button.setMinimumWidth(BUTTON_MIN_WIDTH)
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons, 0)
        layout.setAlignment(buttons, Qt.AlignmentFlag.AlignTop)
        return self.header

    def _row(self, icon_name: str, texts) -> tuple[Win11ControlRow, LineEdit]:
        """Строка настройки с полем ввода справа.

        Пояснение задаётся сразу при создании: по нему строка выбирает свою высоту.
        """
        title, hint = texts
        row = Win11ControlRow(icon_name, self._t(*title), self._t(*hint), parent=self.card)
        edit = LineEdit(row)
        edit.setFixedWidth(FIELD_WIDTH)
        edit.setClearButtonEnabled(True)
        edit.returnPressed.connect(self._save)
        remove_line_edit_buttons_from_tab_order(edit)
        row.add_control(edit)
        return row, edit

    def _retranslate(self) -> None:
        t = self._t
        self.doh_edit.setPlaceholderText("https://dns.example.com/dns-query")
        self.cancel_button.setText(t("page.network.custom_server.cancel", "Отмена"))
        for row, edit, (title, hint) in (
            (self.doh_row, self.doh_edit, ROW_DOH),
            (self.addresses_row, self.addresses_edit, ROW_ADDRESSES),
            (self.name_row, self.name_edit, ROW_NAME),
        ):
            row.set_texts(t(*title), t(*hint))
            set_control_accessibility(edit, name=t(*title), description=t(*hint))
        set_control_accessibility(
            self.cancel_button,
            name=t("page.network.custom_server.cancel.name", "Отмена: вернуться к настройке DNS"),
            description=t("page.network.custom_server.cancel.description", "Закрывает страницу без сохранения."),
        )
        self._render()

    def set_ui_language(self, language: str) -> None:
        super().set_ui_language(language)
        self._retranslate()

    def _apply_page_theme(self, tokens=None, force: bool = False) -> None:
        """Значок рисует себя сам и берёт цвета темы при отрисовке — просто перерисовать."""
        _ = tokens, force
        badge = getattr(self, "badge", None)
        if badge is not None:
            badge.update()

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
        self._settled_detail = ""
        self.doh_edit.setText(self._server["doh"])
        self.addresses_edit.setText(custom_servers.addresses_text(self._server))
        self.name_edit.setText(self._server["name"])
        self._typed_length = {item: len(item.text()) for item in (self.doh_edit, self.addresses_edit, self.name_edit)}
        self._show_error("", "")
        self._render()

    def is_editing(self) -> bool:
        return bool(self._server["id"])

    def on_page_activated(self) -> None:
        (self.name_edit if self.is_editing() else self.doh_edit).setFocus(Qt.FocusReason.OtherFocusReason)

    def onboarding_target(self, name: str):
        """Экскурсия показывает форму нового сервера целиком."""
        return self.card if name == "form" else None

    def cleanup(self) -> None:
        self._closed = True
        self._save_lane.close()
        super().cleanup()

    # ── отрисовка ───────────────────────────────────────────

    def _read_form(self) -> custom_servers.CustomServerForm:
        return custom_servers.read_form(
            server_id=self._server["id"],
            name=self.name_edit.text(),
            doh=self.doh_edit.text(),
            addresses=self.addresses_edit.text(),
        )

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
        self._render_summary()

    def _render_save_button(self) -> None:
        if self._busy and self._busy_lookup:
            text = self._t("page.network.custom_server.checking", "Проверяю…")
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

    def _summary(self, form: custom_servers.CustomServerForm) -> str:
        """Что получится из введённого — одной фразой для строки состояния."""
        server = form.server
        if server is None:
            return self._t(
                "page.network.custom_server.summary.empty",
                "Вставьте адрес DoH или впишите IP-адреса сервера — одного из двух достаточно.",
            )
        addresses = " · ".join([*server["ipv4"], *server["ipv6"]])
        if form.needs_lookup:
            return self._t(
                "page.network.custom_server.summary.doh_only",
                "Шифрованный DNS (DoH). IP-адреса сервера программа найдёт и проверит сама.",
            )
        if server["doh"]:
            return self._t(
                "page.network.custom_server.summary.doh",
                "Шифрованный DNS (DoH) · {addresses}",
                addresses=addresses,
            )
        return self._t(
            "page.network.custom_server.summary.plain",
            "Обычный DNS без шифрования · {addresses}",
            addresses=addresses,
        )

    def _render_summary(self) -> None:
        """Шапка и подсказки в пустых полях: всё, что видно из уже введённого."""
        form = self._read_form()
        server = form.server
        typed = any(edit.text().strip() for edit in (self.doh_edit, self.addresses_edit, self.name_edit))
        self.eyebrow_label.setText(
            self._t("page.network.custom_server.eyebrow.edit", "Свой сервер")
            if self.is_editing()
            else self._t("page.network.custom_server.eyebrow.new", "Новый сервер")
        )
        name = self.name_edit.text().strip() or (server["name"] if server else "")
        self.title_text.setText(name or self._t("page.network.custom_server.title", "Свой DNS"))

        if server is not None or not typed:
            self._settled_detail = self._summary(form)
        if self._busy and self._busy_lookup:
            detail = self._t(
                "page.network.custom_server.checking.detail",
                "Ищу адреса сервера и проверяю каждый запросом DoH…",
            )
        elif self._busy:
            detail = self._t("page.network.custom_server.saving", "Сохраняю…")
        else:
            # Поле заполнено наполовину — показываем последний понятный итог.
            detail = self._settled_detail or self._summary(custom_servers.CustomServerForm())
        self.detail_label.setText(detail)
        self.detail_label.setVisible(not self._error_text)
        self.badge.set_busy(self._busy)

        # В пустых полях видно, что подставится само.
        self.addresses_edit.setPlaceholderText(
            self._t("page.network.custom_server.addresses.auto", "Найдутся сами")
            if server is not None and form.needs_lookup
            else "9.9.9.9 149.112.112.112"
        )
        self.name_edit.setPlaceholderText(
            server["name"]
            if server is not None and not self.name_edit.text().strip()
            else self._t("page.network.custom_server.name.placeholder", "Мой DNS")
        )
        summary = f"{self.eyebrow_label.text()}: {self.title_text.text()}. {self._error_text or detail}"
        set_control_accessibility(
            self.header,
            name=self._t("page.network.custom_server.header.name", "Свой DNS-сервер"),
            description=summary,
        )
        set_state_text(self.title_text, summary)

    def _show_error(self, text: str, field: str) -> None:
        """Ошибка встаёт на место строки состояния, её поле подчёркивается красным."""
        fields = {
            custom_servers.FIELD_DOH: self.doh_edit,
            custom_servers.FIELD_ADDRESSES: self.addresses_edit,
            custom_servers.FIELD_NAME: self.name_edit,
        }
        for name, edit in fields.items():
            edit.setError(bool(text) and name == field)
        self._error_text = text
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text))
        self.detail_label.setVisible(not text)
        if not text:
            return
        set_state_text(self.error_label, self._t("page.network.custom_server.error", "Ошибка: {text}", text=text))
        self._render_summary()
        if field in fields:
            fields[field].setFocus(Qt.FocusReason.OtherFocusReason)

    def error_text(self) -> str:
        """Текст показанной ошибки или пустая строка."""
        return self._error_text

    def _set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        # Поля не выключаются, а запираются: выключение увело бы фокус с поля.
        for edit in (self.doh_edit, self.addresses_edit, self.name_edit):
            edit.setReadOnly(self._busy)
        self._render_save_button()
        self._render_summary()

    # ── действия ────────────────────────────────────────────

    def _on_text_edited(self, edit: LineEdit, text: str) -> None:
        if self._error_text:
            self._show_error("", "")
        # Вставка добавляет сразу несколько знаков, набор с клавиатуры — по одному.
        pasted = len(text) - self._typed_length.get(edit, 0) > 1
        if pasted:
            self._spread_pasted(edit, text)
        self._typed_length = {item: len(item.text()) for item in (self.doh_edit, self.addresses_edit, self.name_edit)}
        self._render_summary()

    def _spread_pasted(self, edit: LineEdit, text: str) -> None:
        """Вставили «адрес DoH и IP-адреса» одной строкой — раскладываем по своим полям.

        Так выглядит строка из «Копировать DNS» и из описаний серверов. Чужое
        поле заполняется, только если оно пустое.
        """
        if edit is self.name_edit:
            return
        pasted = custom_servers.split_pasted(text)
        if pasted is None:
            return
        other = self.addresses_edit if edit is self.doh_edit else self.doh_edit
        if other.text().strip():
            return
        doh, addresses = pasted
        self.doh_edit.setText(doh)
        self.addresses_edit.setText(addresses)

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
