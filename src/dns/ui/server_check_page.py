"""Вкладка «DNS-серверы»: каждый адрес каждым способом и перехват по дороге.

Живёт вкладкой страницы BlockCheck. Сама в сеть не ходит: просит фасад DNS
запустить фоновую проверку и показывает карточки серверов по мере готовности.
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer, pyqtSignal
from qfluentwidgets import FluentIcon, PrimaryPushButton, PushButton, StrongBodyLabel

import dns.server_check_plans as plans
import dns.server_check_verdict as verdicts
from app.ui_texts import tr as tr_catalog
from blockcheck.ui.fun_texts import phrases
from dns.ui.domain_lookup_page import _InfoLines
from dns.ui.server_check_cards import FILTER_ALL, ServerCardsView, SeverityBar, StatusFilter
from dns.ui.server_check_widgets import ServerCheckVerdictPanel
from log.log import log
from ui.accessibility import set_control_accessibility
from ui.fluent_widgets import SettingsCard, set_tooltip
from ui.latest_worker_lane import LatestWorkerLane
from ui.log_report_dialog import show_log_report_dialog
from ui.pages.base_page import BasePage
from ui.widgets.stagger_float_in import float_in

# Промежуточные результаты приходят пачками по несколько в секунду: показываем не чаще.
_STAGE_INTERVAL_MS = 120


class ServerCheckPage(BasePage):
    """Какие DNS-серверы и какими способами доступны в этой сети."""

    # Нажали на карточку сервера: страница-хозяин открывает его подробности (ServerDetails).
    details_requested = pyqtSignal(object)

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

        # Серверы: полоса «насколько всё плохо», фильтр по выводу и карточки.
        self.servers_card = SettingsCard()
        self.servers_title = StrongBodyLabel("", self.servers_card)
        self.servers_card.add_widget(self.servers_title)
        self.status_lines = _InfoLines(self.servers_card)
        self.servers_card.add_widget(self.status_lines)
        self.severity_bar = SeverityBar(self.servers_card)
        self.servers_card.add_widget(self.severity_bar)
        self.status_filter = StatusFilter(self.servers_card)
        self.servers_card.add_widget(self.status_filter)
        self.cards = ServerCardsView(self.servers_card)
        self.status_filter.changed.connect(self.cards.set_filter)
        self.cards.opened.connect(self._open_details)
        self.servers_card.add_widget(self.cards)
        self.servers_card.setVisible(False)
        self.layout.addWidget(self.servers_card)
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
        self.servers_title.setText(self._t("section.servers", "Серверы"))
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
        self.servers_card.setVisible(False)
        self.status_filter.chips[FILTER_ALL].click()
        self.cards.set_cards(())
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
            # Готовый отчёт следом придёт как итог: дважды подряд его не показываем.
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
        cards = plans.build_cards(report)
        appeared = bool(cards) and self.servers_card.isHidden()
        self.servers_card.setVisible(bool(cards))
        counts = plans.count_cards(cards)
        final = report.finished and celebrate
        self.severity_bar.set_counts(counts, animate=final)
        self.status_filter.set_counts(counts)
        self.cards.set_cards(cards)
        # Пока проверка идёт, её ход виден в панели итога: вторая строка о том же не нужна.
        self.status_lines.set_lines((plans.build_status(report),) if report.finished else ())
        if final:
            self.cards.play_reveal()
        elif appeared and celebrate:
            float_in(self.servers_card)
        tally = verdicts.tally(report)
        if report.finished:
            self.verdict_panel.show_verdict(verdicts.build_verdict(report), celebrate=celebrate)
        else:
            self.verdict_panel.show_progress(
                self._progress_title(len(report.rows), report.total), len(report.rows), report.total, tally
            )

    def _open_details(self, server: str) -> None:
        if self._report is None:
            return
        details = plans.build_details(self._report, server)
        if details is not None:
            self.details_requested.emit(details)

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


__all__ = ["ServerCheckPage"]
