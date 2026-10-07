"""Вкладка «DNS-серверы»: каждый адрес каждым способом и перехват по дороге.

Живёт вкладкой страницы BlockCheck. Сама в сеть не ходит: просит фасад DNS
запустить фоновую проверку и показывает строки по мере готовности.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHeaderView, QSizePolicy, QTableWidgetItem
from qfluentwidgets import FluentIcon, PrimaryPushButton, PushButton, StrongBodyLabel, TableWidget, isDarkTheme

import dns.domain_lookup_plans as tones
import dns.server_check_plans as plans
import dns.server_check_verdict as verdicts
from blockcheck.ui.fun_texts import phrases
from app.ui_texts import tr as tr_catalog
from dns.server_check import LEVEL_FAIL, LEVEL_WARN, TRANSPORTS
from dns.ui.domain_lookup_page import _InfoLines, _tone_color
from dns.ui.server_check_widgets import ServerCheckVerdictPanel
from log.log import log
from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_widgets import SettingsCard, set_tooltip
from ui.latest_worker_lane import LatestWorkerLane
from ui.log_report_dialog import show_log_report_dialog
from ui.pages.base_page import BasePage
from ui.theme_refresh import ThemeRefreshBinding
from ui.widgets.fluent_item_tooltip import (
    FLUENT_ITEM_TOOLTIP_ROLE,
    install_fluent_item_tooltips,
    set_fluent_item_tooltip,
)
from ui.widgets.stagger_float_in import float_in

_FIRST_TRANSPORT_COLUMN = 2
_NOTE_COLUMN = _FIRST_TRANSPORT_COLUMN + len(TRANSPORTS)
_CELL_TONES = {
    plans.CELL_OK: tones.TONE_SUCCESS,
    plans.CELL_WARN: tones.TONE_WARNING,
    plans.CELL_FAIL: tones.TONE_ERROR,
}
_NOTE_TONES = {LEVEL_FAIL: tones.TONE_ERROR, LEVEL_WARN: tones.TONE_WARNING}
# Неважное («нет ответа» на пинг, «Без замечаний») — обычным цветом текста, но бледнее.
# Отдельный «приглушённый» цвет темы на тёмном фоне таблицы почти не виден.
_FADED_ALPHA = 150
# Промежуточные результаты приходят пачками по несколько в секунду: показываем не чаще.
_STAGE_INTERVAL_MS = 120
# Таблица показывает столько строк, остальные прокручиваются внутри неё. Так Qt
# рисует только видимые строки, а не все адреса разом при каждой перерисовке.
_VISIBLE_ROWS = 12


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
        # Ширину столбцов считаем сами, один раз на пачку изменений. В режиме
        # «по содержимому» Qt заново обмеряет весь столбец после каждой записи
        # в ячейку — на полной таблице окно замирало на секунду.
        header = self.horizontalHeader()
        for column in range(_NOTE_COLUMN):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(_NOTE_COLUMN, QHeaderView.ResizeMode.Stretch)
        install_fluent_item_tooltips(self)
        self._rows: tuple[plans.ServerRow, ...] = ()
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)

    def set_headers(self, server: str, address: str, note: str) -> None:
        titles = [plans.TRANSPORT_TITLES[transport] for transport in TRANSPORTS]
        self.setHorizontalHeaderLabels([server, address, *titles, note])
        self._fit_columns()

    def show_rows(self, rows) -> None:
        rows = tuple(rows)
        previous, self._rows = self._rows, rows
        # Переписываем только строки, которые изменились.
        changed = [index for index, row in enumerate(rows) if index >= len(previous) or previous[index] != row]
        resized = len(rows) != len(previous)
        if not changed and not resized:
            return
        self.setUpdatesEnabled(False)
        try:
            if resized:
                self.setRowCount(len(rows))
            for index in changed:
                self._fill_row(index, rows[index])
            self._fit_columns()
        finally:
            self.setUpdatesEnabled(True)
        if resized:
            self._fit_height()
        problems = sum(1 for row in rows if row.level in (LEVEL_WARN, LEVEL_FAIL))
        set_state_text(self, f"Проверка DNS-серверов: {len(rows)} адресов, с замечаниями {problems}")

    def _fill_row(self, index: int, row: plans.ServerRow) -> None:
        for column, text in enumerate((row.server, row.address, *row.cells, row.note)):
            item = self.item(index, column)
            if item is None:
                item = QTableWidgetItem()
                self.setItem(index, column, item)
            if item.text() != text:
                item.setText(text)
            if item.data(FLUENT_ITEM_TOOLTIP_ROLE) != row.tooltip:
                set_fluent_item_tooltip(item, row.tooltip)
        self._paint_row(index, row)

    def _fit_columns(self) -> None:
        for column in range(_NOTE_COLUMN):
            self.resizeColumnToContents(column)

    def _fit_height(self) -> None:
        height = self.horizontalHeader().height() + 2 * self.frameWidth() + 4
        for row in range(min(self.rowCount(), _VISIBLE_ROWS)):
            height += self.rowHeight(row)
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)

    def _paint(self, item, tone: str | None) -> None:
        if item is None:
            return
        if tone is None:
            faded = QColor(Qt.GlobalColor.white if isDarkTheme() else Qt.GlobalColor.black)
            faded.setAlpha(_FADED_ALPHA)
            item.setForeground(faded)
        else:
            item.setForeground(QColor(_tone_color(tone)))

    def _paint_row(self, index: int, row: plans.ServerRow) -> None:
        for offset, level in enumerate(row.cell_levels):
            self._paint(self.item(index, _FIRST_TRANSPORT_COLUMN + offset), _CELL_TONES.get(level))
        self._paint(self.item(index, _NOTE_COLUMN), _NOTE_TONES.get(row.note_level))

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = tokens, force
        for index, row in enumerate(self._rows):
            self._paint_row(index, row)


class ServerCheckPage(BasePage):
    """Какие DNS-серверы и какими способами доступны в этой сети."""

    def __init__(self, parent=None, *, dns_feature, embedded: bool = False, open_dns_settings=None):
        super().__init__(
            "DNS-серверы",
            "Каждый адрес каждого сервера: пинг, обычный и шифрованный DNS, перехват по дороге",
            parent,
            title_key="page.server_check.title",
            subtitle_key="page.server_check.subtitle",
        )
        self._dns = dns_feature
        self._open_dns_settings = open_dns_settings
        self._closed = False
        self._report = None
        self._running = False
        # Последний промежуточный результат, который ещё не показан.
        self._staged = None
        self._stage_timer = QTimer(self)
        self._stage_timer.setSingleShot(True)
        self._stage_timer.setInterval(_STAGE_INTERVAL_MS)
        self._stage_timer.timeout.connect(self._flush_stage)
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
        self.verdict_panel = ServerCheckVerdictPanel(on_open_dns_settings=self._open_dns_settings)
        self.start_button = PrimaryPushButton(FluentIcon.PLAY, "", self.verdict_panel)
        self.start_button.clicked.connect(self.start_check)
        self.stop_button = PushButton(FluentIcon.CANCEL, "", self.verdict_panel)
        self.stop_button.clicked.connect(self.stop_check)
        self.report_button = PushButton(FluentIcon.DOCUMENT, "", self.verdict_panel)
        self.report_button.clicked.connect(self._open_report)
        self.verdict_panel.add_actions(self.start_button, self.stop_button, self.report_button)
        self.layout.addWidget(self.verdict_panel)

        self.table_card = SettingsCard()
        self.table_title = StrongBodyLabel("", self.table_card)
        self.table_card.add_widget(self.table_title)
        self.status_lines = _InfoLines(self.table_card)
        self.table_card.add_widget(self.status_lines)
        self.table = DnsServersTable(self.table_card)
        self.table_card.add_widget(self.table)
        self.table_card.setVisible(False)
        self.layout.addWidget(self.table_card)
        self.layout.addStretch()

    def _apply_texts(self) -> None:
        self.start_button.setText(self._t("button.start", "Проверить серверы"))
        start_description = self._t(
            "button.start.description",
            "Спросить каждый адрес каждого DNS-сервера всеми способами, по три раза. Занимает около десяти секунд.",
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
        if self._report is not None:
            # Смена языка — не новый итог: без салюта и без выплывания строк.
            self._show_report(self._report, celebrate=False)
        elif not self._running:
            self._show_idle()

    def _show_idle(self) -> None:
        self.verdict_panel.set_idle(
            self._t("idle.title", "Медоед обзвонит DNS-серверы"),
            self._t(
                "intro",
                "Проверка покажет, какие DNS-серверы и какими способами доступны в вашей сети: обычный DNS "
                "(порт 53) и шифрованный — DoT (порт 853) и DoH (порт 443). Провайдер может закрыть любой "
                "из них отдельно и для отдельного адреса. Заодно видно, не подменяются ли ответы по дороге.",
            ),
        )

    def _progress_title(self, done: int, total: int) -> str:
        title = self._t("pending.title", "Опрашиваем серверы")
        return f"{title}: {done} / {total}" if total else f"{title}…"

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

    def start_check(self) -> None:
        if self._closed or self._running:
            return
        # Старые результаты не копим: каждая проверка начинается с чистого экрана.
        self._report = None
        self._drop_staged()
        self.table_card.setVisible(False)
        self.table.show_rows(())
        self.verdict_panel.set_pending(self._progress_title(0, 0), phrases("dns_servers", self._ui_language))
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
        if report.finished:
            # Готовый отчёт следом придёт как итог: дважды подряд таблицу не рисуем.
            return
        self._staged = report
        if not self._stage_timer.isActive():
            self._stage_timer.start()

    def _flush_stage(self) -> None:
        report, self._staged = self._staged, None
        if report is not None and self._running and not self._closed:
            self._show_report(report)

    def _drop_staged(self) -> None:
        self._stage_timer.stop()
        self._staged = None

    def _on_finished(self, report) -> None:
        if self._closed:
            return
        self._drop_staged()
        self._show_report(report)
        self.stop_button.setEnabled(True)
        self._set_running(False)

    def _on_failed(self, error: str) -> None:
        if self._closed:
            return
        self._drop_staged()
        self.verdict_panel.set_failed(self._t("status.failed", "Проверка не удалась"), str(error))
        self.stop_button.setEnabled(True)
        self._set_running(False)

    def _show_report(self, report, *, celebrate: bool = True) -> None:
        self._report = report
        rows = plans.build_rows(report)
        appeared = bool(rows) and self.table_card.isHidden()
        self.table_card.setVisible(bool(rows))
        self.table.show_rows(rows)
        # Пока проверка идёт, её ход виден в панели итога: вторая строка о том же не нужна.
        self.status_lines.set_lines((plans.build_status(report),) if report.finished else ())
        if appeared and celebrate:
            float_in(self.table_card)
        tally = verdicts.tally(report)
        if report.finished:
            self.verdict_panel.show_verdict(verdicts.build_verdict(report), tally, celebrate=celebrate)
        else:
            self.verdict_panel.show_progress(
                self._progress_title(len(report.rows), report.total), len(report.rows), report.total, tally
            )

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
        self._drop_staged()
        self._lane.close()
        super().cleanup()


__all__ = ["DnsServersTable", "ServerCheckPage"]
