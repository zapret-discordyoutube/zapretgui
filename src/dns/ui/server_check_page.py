"""Вкладка «DNS-серверы»: каждый адрес каждым способом и перехват по дороге.

Живёт вкладкой страницы BlockCheck. Сама в сеть не ходит: просит фасад DNS
запустить фоновую проверку и показывает строки по мере готовности.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QHeaderView, QSizePolicy, QTableWidgetItem
from qfluentwidgets import (
    FluentIcon,
    IndeterminateProgressBar,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TableWidget,
)

import dns.domain_lookup_plans as tones
import dns.server_check_plans as plans
from app.ui_texts import tr as tr_catalog
from dns.server_check import LEVEL_FAIL, LEVEL_WARN, TRANSPORTS
from dns.ui.domain_lookup_page import _InfoLines, _tone_color
from log.log import log
from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_widgets import SettingsCard, set_tooltip
from ui.latest_worker_lane import LatestWorkerLane
from ui.log_report_dialog import show_log_report_dialog
from ui.pages.base_page import BasePage
from ui.theme_refresh import ThemeRefreshBinding
from ui.widgets.fluent_item_tooltip import install_fluent_item_tooltips, set_fluent_item_tooltip

_FIRST_TRANSPORT_COLUMN = 2
_NOTE_COLUMN = _FIRST_TRANSPORT_COLUMN + len(TRANSPORTS)
_CELL_TONES = {plans.CELL_OK: tones.TONE_SUCCESS, plans.CELL_FAIL: tones.TONE_ERROR, plans.CELL_MUTED: tones.TONE_MUTED}
_NOTE_TONES = {LEVEL_FAIL: tones.TONE_ERROR, LEVEL_WARN: tones.TONE_WARNING}


class DnsServersTable(TableWidget):
    """Одна строка на адрес сервера, по столбцу на каждый способ связи."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setColumnCount(_NOTE_COLUMN + 1)
        self.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.verticalHeader().setVisible(False)
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header = self.horizontalHeader()
        for column in range(_NOTE_COLUMN):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_NOTE_COLUMN, QHeaderView.ResizeMode.Stretch)
        install_fluent_item_tooltips(self)
        self._rows: tuple[plans.ServerRow, ...] = ()
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)

    def set_headers(self, server: str, address: str, note: str) -> None:
        titles = [plans.TRANSPORT_TITLES[transport] for transport in TRANSPORTS]
        self.setHorizontalHeaderLabels([server, address, *titles, note])

    def show_rows(self, rows) -> None:
        self._rows = tuple(rows)
        self.setRowCount(len(self._rows))
        for index, row in enumerate(self._rows):
            for column, text in enumerate((row.server, row.address, *row.cells, row.note)):
                item = self.item(index, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.setItem(index, column, item)
                item.setText(text)
                set_fluent_item_tooltip(item, row.tooltip)
        self._apply_theme_refresh()
        self._fit_height()
        problems = sum(1 for row in self._rows if row.level in (LEVEL_WARN, LEVEL_FAIL))
        set_state_text(self, f"Проверка DNS-серверов: {len(self._rows)} адресов, с замечаниями {problems}")

    def _fit_height(self) -> None:
        height = self.horizontalHeader().height() + 2 * self.frameWidth() + 4
        for row in range(self.rowCount()):
            height += self.rowHeight(row)
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)

    def _paint(self, item, tone: str | None) -> None:
        if item is None:
            return
        if tone is None:
            item.setData(Qt.ItemDataRole.ForegroundRole, None)
        else:
            item.setForeground(QColor(_tone_color(tone)))

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = tokens, force
        for index, row in enumerate(self._rows):
            for offset, level in enumerate(row.cell_levels):
                self._paint(self.item(index, _FIRST_TRANSPORT_COLUMN + offset), _CELL_TONES.get(level))
            self._paint(self.item(index, _NOTE_COLUMN), _NOTE_TONES.get(row.note_level, tones.TONE_MUTED))


class ServerCheckPage(BasePage):
    """Какие DNS-серверы и какими способами доступны в этой сети."""

    def __init__(self, parent=None, *, dns_feature, embedded: bool = False):
        super().__init__(
            "DNS-серверы",
            "Каждый адрес каждого сервера: пинг, обычный и шифрованный DNS, перехват по дороге",
            parent,
            title_key="page.server_check.title",
            subtitle_key="page.server_check.subtitle",
        )
        self._dns = dns_feature
        self._closed = False
        self._report = None
        self._running = False
        self._lane = LatestWorkerLane(
            name="dns_server_check",
            create_worker=self._create_worker,
            on_result=lambda _payload, report: self._on_finished(report),
            on_error=lambda _payload, error: self._on_failed(error),
            log_fn=log,
        )
        self._build_ui()
        if embedded:
            self.hide_page_header()
        self._apply_texts()
        self._set_running(False)

    # ── тексты ──────────────────────────────────────────────

    def _t(self, key: str, default: str) -> str:
        return tr_catalog(f"page.server_check.{key}", language=self._ui_language, default=default)

    # ── сборка ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.control_card = SettingsCard()
        self.intro_lines = _InfoLines(self.control_card)
        self.control_card.add_widget(self.intro_lines)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.start_button = PrimaryPushButton(FluentIcon.PLAY, "", self.control_card)
        self.start_button.clicked.connect(self.start_check)
        row.addWidget(self.start_button)
        self.stop_button = PushButton(FluentIcon.CANCEL, "", self.control_card)
        self.stop_button.clicked.connect(self.stop_check)
        row.addWidget(self.stop_button)
        self.report_button = PushButton(FluentIcon.DOCUMENT, "", self.control_card)
        self.report_button.clicked.connect(self._open_report)
        row.addWidget(self.report_button)
        row.addStretch(1)
        self.control_card.add_layout(row)
        self.status_lines = _InfoLines(self.control_card)
        self.control_card.add_widget(self.status_lines)
        self.progress_bar = IndeterminateProgressBar(self.control_card)
        self.progress_bar.setVisible(False)
        self.control_card.add_widget(self.progress_bar)
        self.layout.addWidget(self.control_card)

        self.summary_card = SettingsCard()
        self.summary_title = StrongBodyLabel("", self.summary_card)
        self.summary_card.add_widget(self.summary_title)
        self.summary_lines = _InfoLines(self.summary_card)
        self.summary_card.add_widget(self.summary_lines)
        self.layout.addWidget(self.summary_card)

        self.table_card = SettingsCard()
        self.table_title = StrongBodyLabel("", self.table_card)
        self.table_card.add_widget(self.table_title)
        self.table = DnsServersTable(self.table_card)
        self.table_card.add_widget(self.table)
        self.layout.addWidget(self.table_card)

        for card in (self.summary_card, self.table_card):
            card.setVisible(False)
        self.layout.addStretch()

    def _apply_texts(self) -> None:
        self.intro_lines.set_lines(
            (
                tones.InfoLine(
                    self._t(
                        "intro",
                        "Проверка покажет, какие DNS-серверы и какими способами доступны в вашей сети: обычный DNS "
                        "(порт 53) и шифрованный — DoT (порт 853) и DoH (порт 443). Провайдер может закрыть любой "
                        "из них отдельно и для отдельного адреса. Заодно видно, не подменяются ли ответы по дороге.",
                    )
                ),
            )
        )
        self.start_button.setText(self._t("button.start", "Проверить серверы"))
        start_description = self._t(
            "button.start.description",
            "Спросить каждый адрес каждого DNS-сервера всеми способами. Занимает около двадцати секунд.",
        )
        set_tooltip(self.start_button, start_description)
        set_control_accessibility(
            self.start_button, name=self._t("button.start.name", "Проверить DNS-серверы"), description=start_description
        )
        self.stop_button.setText(self._t("button.stop", "Остановить"))
        set_control_accessibility(
            self.stop_button,
            name=self._t("button.stop.name", "Остановить проверку DNS-серверов"),
            description=self._t("button.stop.description", "Прервать проверку; останется то, что уже успели узнать."),
        )
        self.report_button.setText(self._t("button.report", "Отчёт"))
        report_description = self._t(
            "button.report.description", "Открыть полный текст проверки — его можно скопировать и отправить."
        )
        set_tooltip(self.report_button, report_description)
        set_control_accessibility(
            self.report_button,
            name=self._t("button.report.name", "Открыть отчёт проверки DNS-серверов"),
            description=report_description,
        )
        self.summary_title.setText(self._t("section.summary", "Итог"))
        self.table_title.setText(self._t("section.table", "Серверы по адресам"))
        self.table.set_headers(
            self._t("column.server", "Сервер"),
            self._t("column.address", "Адрес"),
            self._t("column.note", "Замечания"),
        )
        set_control_accessibility(
            self.table,
            name=self._t("table.name", "DNS-серверы по адресам"),
            description=self._t(
                "table.description",
                "Для каждого адреса: время ответа на пинг и каждым способом связи или причина отказа.",
            ),
        )
        if self._report is None:
            self.status_lines.set_lines(
                (tones.InfoLine(self._t("status.ready", "Нажмите «Проверить серверы», чтобы начать.")),)
            )
        else:
            self._show_report(self._report)

    # ── запуск и остановка ──────────────────────────────────

    def _create_worker(self, request_id: int, _payload):
        worker = self._dns.create_server_check_worker(request_id, parent=self)
        worker.stage.connect(self._on_stage)
        return worker

    def _set_running(self, running: bool) -> None:
        self._running = bool(running)
        self.start_button.setEnabled(not running)
        self.stop_button.setVisible(running)
        self.report_button.setEnabled(self._report is not None and not running)
        self.progress_bar.setVisible(running)
        if running:
            self.progress_bar.start()
        else:
            self.progress_bar.stop()
        set_state_text(
            self.progress_bar,
            "Проверка DNS-серверов: выполняется" if running else "Проверка DNS-серверов: не выполняется",
        )

    def start_check(self) -> None:
        if self._closed or self._running:
            return
        # Старые результаты не копим: каждая проверка начинается с чистого экрана.
        self._report = None
        for card in (self.summary_card, self.table_card):
            card.setVisible(False)
        self.table.show_rows(())
        self.status_lines.set_lines(
            (tones.InfoLine(self._t("status.starting", "Начинаем проверку серверов…"), tones.TONE_ACCENT),)
        )
        self._set_running(True)
        self._lane.request()

    def stop_check(self) -> None:
        worker = self._lane.runtime.worker
        stop = getattr(worker, "stop", None)
        if callable(stop):
            try:
                stop()
            except RuntimeError:
                pass
        self.stop_button.setEnabled(False)

    # ── результаты ──────────────────────────────────────────

    def _on_stage(self, request_id: int, report) -> None:
        if not self._lane.runtime.is_current(request_id, cleanup_in_progress=self._closed):
            return
        self._show_report(report)

    def _on_finished(self, report) -> None:
        if self._closed:
            return
        self._show_report(report)
        self.stop_button.setEnabled(True)
        self._set_running(False)

    def _on_failed(self, error: str) -> None:
        if self._closed:
            return
        self.status_lines.set_lines(
            (tones.InfoLine(f"{self._t('status.failed', 'Проверка не удалась')}: {error}", tones.TONE_ERROR),)
        )
        self.stop_button.setEnabled(True)
        self._set_running(False)

    def _show_report(self, report) -> None:
        self._report = report
        self.status_lines.set_lines((plans.build_status(report),))
        summary = plans.build_summary(report)
        self.summary_card.setVisible(bool(summary))
        if summary:
            self.summary_lines.set_lines(summary)
        rows = plans.build_rows(report)
        self.table_card.setVisible(bool(rows))
        if rows:
            self.table.show_rows(rows)

    def _open_report(self) -> None:
        if self._report is None:
            return
        show_log_report_dialog(
            self.window(),
            title=self._t("report.title", "Отчёт проверки DNS-серверов"),
            text=plans.build_text_report(self._report),
            empty_text=self._t("report.empty", "Проверка ещё не запускалась."),
            description=self._t(
                "report.description", "Полный текст проверки: таблица по адресам, итог и подробности."
            ),
        )

    # ── жизненный цикл ──────────────────────────────────────

    def set_ui_language(self, language: str) -> None:
        super().set_ui_language(language)
        try:
            self._apply_texts()
        except Exception:
            pass

    def cleanup(self) -> None:
        self._closed = True
        self._lane.close()
        super().cleanup()


__all__ = ["DnsServersTable", "ServerCheckPage"]
