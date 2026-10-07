"""Вкладка «Проверка домена»: пинг, ответы разных DNS и соседи по адресу.

Живёт вкладкой страницы BlockCheck. Сама в сеть не ходит: просит фасад DNS
запустить фоновую проверку и показывает результаты по мере готовности.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CheckBox,
    FluentIcon,
    IndeterminateProgressBar,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
)

import dns.domain_lookup_plans as plans
from app.ui_texts import tr as tr_catalog
from blockcheck.ui.check_results import _HeightKeeper
from blockcheck.ui.result_cards import _SectionBlock
from blockcheck.ui.result_cards_model import Line, Section
from log.log import log
from ui.accessibility import set_control_accessibility, set_state_text
from ui.widgets.check_hero import CheckHero
from ui.fluent_widgets import SettingsCard, set_tooltip
from ui.latest_worker_lane import LatestWorkerLane
from ui.pages.base_page import BasePage
from ui.theme import get_theme_tokens
from ui.theme_refresh import ThemeRefreshBinding
from ui.theme_semantic import get_semantic_palette
from ui.widgets.fun import FunTicker
from ui.widgets.log_report_view import LogReport


def _tone_color(tone: str) -> str:
    tokens = get_theme_tokens()
    semantic = get_semantic_palette()
    return {
        plans.TONE_MUTED: tokens.fg_muted,
        plans.TONE_ACCENT: tokens.accent_hex,
        plans.TONE_SUCCESS: semantic.success,
        plans.TONE_WARNING: semantic.warning,
        plans.TONE_ERROR: semantic.error,
    }.get(tone, tokens.fg_muted)


class _InfoLines(_HeightKeeper, QWidget):
    """Несколько строк текста с переносом; высоту держит сам (см. _HeightKeeper)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._labels: list[CaptionLabel] = []
        self._lines: tuple[plans.InfoLine, ...] = ()
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)

    def set_lines(self, lines) -> None:
        self._lines = tuple(lines)
        while len(self._labels) < len(self._lines):
            label = CaptionLabel("", self)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._layout.addWidget(label)
            self._labels.append(label)
        for index, label in enumerate(self._labels):
            visible = index < len(self._lines)
            label.setVisible(visible)
            if visible:
                label.setText(self._lines[index].text)
        self._apply_theme_refresh()
        set_state_text(self, " ".join(line.text for line in self._lines))
        self._schedule_min_height_sync()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_min_height()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = tokens, force
        for label, line in zip(self._labels, self._lines):
            label.setStyleSheet(f"color: {_tone_color(line.tone)};")


class RowsView(QWidget):
    """Группы строк карточками-разделами — тем же видом, что подробности проверки BlockCheck.

    Заменяет таблицу и окна с моноширинным текстом: у каждой строки значок
    состояния, подпись и значение, длинные значения переносятся.
    """

    def __init__(self, parent=None, *, icon: str = "fa5s.list-ul") -> None:
        super().__init__(parent)
        self._icon = icon
        self._shown: tuple = ()
        self._blocks: list[QWidget] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)

    def groups(self) -> tuple:
        return self._shown

    def show_groups(self, groups) -> None:
        groups = tuple(groups)
        if groups == self._shown:
            return
        self._shown = groups
        for block in self._blocks:
            self._layout.removeWidget(block)
            block.deleteLater()
        self._blocks = []
        for group in groups:
            section = Section(group.title, tuple(Line(row.state, row.name, row.text) for row in group.rows))
            block = _SectionBlock(section, self, icon=self._icon)
            self._layout.addWidget(block)
            self._blocks.append(block)
        set_state_text(self, "; ".join(f"{group.title}: строк {len(group.rows)}" for group in groups) or "нет данных")


class DomainLookupPage(BasePage):
    """Пинг, адреса с разных DNS и «кто ещё на этом адресе» для одного домена или IP."""

    # Просят показать отчёт страницей: её открывает страница-хозяин вкладки (LogReport).
    report_requested = pyqtSignal(object)

    def __init__(self, parent=None, *, dns_feature, embedded: bool = False):
        super().__init__(
            "Проверка домена",
            "Пинг, адреса с разных DNS-серверов и домены на том же адресе",
            parent,
            title_key="page.domain_lookup.title",
            subtitle_key="page.domain_lookup.subtitle",
        )
        self._dns = dns_feature
        self._closed = False
        self._report = None
        # Что сейчас показано в поле соседей: сравниваем с этой строкой, а не читаем текст обратно из поля.
        self._running = False
        self._lane = LatestWorkerLane(
            name="domain_lookup",
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
        return tr_catalog(f"page.domain_lookup.{key}", language=self._ui_language, default=default)

    # ── сборка ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Вкладка начинается с той же панели, что и остальные вкладки раздела: талисман,
        # главная фраза, пояснение, под ними — поле и кнопки.
        self.control_card = CheckHero("fa5s.search-location")
        row = QHBoxLayout()
        row.setSpacing(10)
        self.target_input = LineEdit(self.control_card)
        self.target_input.setClearButtonEnabled(True)
        self.target_input.returnPressed.connect(self.start_lookup)
        row.addWidget(self.target_input, 1)
        self.start_button = PrimaryPushButton(FluentIcon.PLAY, "", self.control_card)
        self.start_button.clicked.connect(self.start_lookup)
        row.addWidget(self.start_button)
        self.stop_button = PushButton(FluentIcon.CANCEL, "", self.control_card)
        self.stop_button.clicked.connect(self.stop_lookup)
        row.addWidget(self.stop_button)
        self.report_button = PushButton(FluentIcon.DOCUMENT, "", self.control_card)
        self.report_button.clicked.connect(self._open_report)
        row.addWidget(self.report_button)
        self.control_card.add_layout(row)

        self.external_check = CheckBox("", self.control_card)
        self.external_check.setChecked(True)
        self.control_card.add_widget(self.external_check)

        self.status_lines = _InfoLines(self.control_card)
        self.control_card.add_widget(self.status_lines)
        self.progress_bar = IndeterminateProgressBar(self.control_card)
        self.progress_bar.setVisible(False)
        self.control_card.add_widget(self.progress_bar)
        self.ticker = FunTicker(self.control_card)
        self.ticker.setVisible(False)
        self.control_card.add_widget(self.ticker)
        self.layout.addWidget(self.control_card)

        self.ping_card = SettingsCard()
        self.ping_title = StrongBodyLabel("", self.ping_card)
        self.ping_card.add_widget(self.ping_title)
        self.ping_lines = _InfoLines(self.ping_card)
        self.ping_card.add_widget(self.ping_lines)
        self.network_lines = _InfoLines(self.ping_card)
        self.ping_card.add_widget(self.network_lines)
        self.layout.addWidget(self.ping_card)

        self.path_card = SettingsCard()
        self.path_title = StrongBodyLabel("", self.path_card)
        self.path_card.add_widget(self.path_title)
        self.path_lines = _InfoLines(self.path_card)
        self.path_card.add_widget(self.path_lines)
        self.path_rows = RowsView(self.path_card, icon="fa5s.route")
        self.path_card.add_widget(self.path_rows)
        self.layout.addWidget(self.path_card)

        self.neighbors_card = SettingsCard()
        self.neighbors_title = StrongBodyLabel("", self.neighbors_card)
        self.neighbors_card.add_widget(self.neighbors_title)
        self.neighbors_rows = RowsView(self.neighbors_card, icon="fa5s.sitemap")
        self.neighbors_card.add_widget(self.neighbors_rows)
        self.layout.addWidget(self.neighbors_card)

        self.dns_card = SettingsCard()
        self.dns_title = StrongBodyLabel("", self.dns_card)
        self.dns_card.add_widget(self.dns_title)
        self.dns_summary = _InfoLines(self.dns_card)
        self.dns_card.add_widget(self.dns_summary)
        self.dns_rows = RowsView(self.dns_card, icon="fa5s.network-wired")
        self.dns_card.add_widget(self.dns_rows)
        self.layout.addWidget(self.dns_card)

        for card in (self.ping_card, self.path_card, self.dns_card, self.neighbors_card):
            card.setVisible(False)
        self.layout.addStretch()

    def _apply_texts(self) -> None:
        self.control_card.set_texts(
            self._t("hero.title", "Проверка домена или адреса"),
            self._t(
                "hero.detail",
                "Покажем, отвечает ли сервер на пинг, что про этот домен говорят разные DNS-серверы "
                "и какие ещё сайты живут на том же адресе.",
            ),
        )
        self.target_input.setPlaceholderText(self._t("placeholder", "Домен или IP-адрес: example.com, 1.2.3.4"))
        set_control_accessibility(
            self.target_input,
            name=self._t("input.name", "Домен или IP-адрес для проверки"),
            description=self._t("input.description", "Введите домен, адрес или ссылку и нажмите «Проверить»."),
        )
        self.start_button.setText(self._t("button.start", "Проверить"))
        start_description = self._t(
            "button.start.description",
            "Пропинговать адрес, спросить его у разных DNS-серверов и найти домены на том же адресе.",
        )
        set_tooltip(self.start_button, start_description)
        set_control_accessibility(self.start_button, name=self._t("button.start.name", "Проверить домен"), description=start_description)
        self.stop_button.setText(self._t("button.stop", "Остановить"))
        set_control_accessibility(
            self.stop_button,
            name=self._t("button.stop.name", "Остановить проверку домена"),
            description=self._t("button.stop.description", "Прервать проверку; останется то, что уже успели узнать."),
        )
        self.report_button.setText(self._t("button.report", "Отчёт"))
        report_description = self._t("button.report.description", "Открыть полный текст проверки — его можно скопировать.")
        set_tooltip(self.report_button, report_description)
        set_control_accessibility(self.report_button, name=self._t("button.report.name", "Открыть отчёт проверки домена"), description=report_description)
        self.external_check.setText(
            self._t("external", "Искать соседей по адресу через внешние сервисы (адрес будет отправлен на их сайты)")
        )
        set_tooltip(
            self.external_check,
            self._t(
                "external.description",
                "Список доменов на том же адресе дают сторонние сервисы. Без галочки наружу ничего не уходит: "
                "остаются обратное имя, сертификат и владелец сети.",
            ),
        )
        self.ping_title.setText(self._t("section.ping", "Пинг и сеть"))
        self.path_title.setText(self._t("section.path", "Путь до сервера"))
        set_control_accessibility(
            self.path_rows,
            name=self._t("path.name", "Узлы по дороге до сервера"),
            description=self._t(
                "path.description",
                "Номер узла, его адрес и время ответа. Отметка показывает, за каким узлом стоит фильтр.",
            ),
        )
        self.dns_title.setText(self._t("section.dns", "Адреса с разных DNS-серверов"))
        self.neighbors_title.setText(self._t("section.neighbors", "Кто ещё на этом адресе"))
        set_control_accessibility(
            self.dns_rows,
            name=self._t("table.name", "Ответы DNS-серверов"),
            description=self._t("table.description", "Для каждого сервера: какие адреса он назвал и за сколько."),
        )
        set_control_accessibility(
            self.neighbors_rows,
            name=self._t("neighbors.name", "Домены на том же адресе"),
            description=self._t("neighbors.description", "Списки доменов по источникам."),
        )
        if self._report is None:
            self.status_lines.set_lines(
                (plans.InfoLine(self._t("status.ready", "Введите домен или IP-адрес и нажмите «Проверить».")),)
            )
        else:
            self._show_report(self._report)

    # ── запуск и остановка ──────────────────────────────────

    def _create_worker(self, request_id: int, payload):
        worker = self._dns.create_domain_lookup_worker(
            request_id,
            target=payload["target"],
            use_external=payload["use_external"],
            parent=self,
        )
        worker.stage.connect(self._on_stage)
        return worker

    def _set_running(self, running: bool) -> None:
        self._running = bool(running)
        self.start_button.setEnabled(not running)
        self.stop_button.setVisible(running)
        self.target_input.setEnabled(not running)
        self.external_check.setEnabled(not running)
        self.report_button.setEnabled(self._report is not None and not running)
        self.progress_bar.setVisible(running)
        self.ticker.setVisible(running)
        if running:
            from blockcheck.ui.fun_texts import phrases

            self.progress_bar.start()
            self.ticker.set_phrases(phrases("dns_lookup", self._ui_language))
            self.ticker.start()
        else:
            self.progress_bar.stop()
            self.ticker.stop()
        set_state_text(self.progress_bar, "Проверка домена: выполняется" if running else "Проверка домена: не выполняется")

    def set_target(self, target: str) -> None:
        self.target_input.setText(str(target or ""))

    def start_lookup(self) -> None:
        if self._closed or self._running:
            return
        target = self.target_input.text().strip()
        if not target:
            self.status_lines.set_lines(
                (plans.InfoLine(self._t("status.empty", "Введите домен или IP-адрес."), plans.TONE_WARNING),)
            )
            self.target_input.setFocus()
            return
        # Старые результаты не копим: каждая проверка начинается с чистого экрана.
        self._report = None
        for card in (self.ping_card, self.path_card, self.dns_card, self.neighbors_card):
            card.setVisible(False)
        for view in (self.dns_rows, self.neighbors_rows, self.path_rows):
            view.show_groups(())
        self.status_lines.set_lines((plans.InfoLine(f"Проверяем {target}…", plans.TONE_ACCENT),))
        self._set_running(True)
        self._lane.request({"target": target, "use_external": self.external_check.isChecked()})

    def stop_lookup(self) -> None:
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
            (plans.InfoLine(f"{self._t('status.failed', 'Проверка не удалась')}: {error}", plans.TONE_ERROR),)
        )
        self.stop_button.setEnabled(True)
        self._set_running(False)

    def _show_report(self, report) -> None:
        self._report = report
        self.status_lines.set_lines((plans.build_status(report),))

        ping_lines = plans.build_ping_lines(report)
        self.ping_lines.set_lines(ping_lines)
        self.network_lines.set_lines(plans.build_network_lines(report))
        self.ping_card.setVisible(bool(ping_lines))

        path_lines = plans.build_path_lines(report)
        self.path_card.setVisible(bool(path_lines))
        if path_lines:
            self.path_lines.set_lines(path_lines)
        path_rows = plans.build_path_rows(report)
        self.path_rows.setVisible(bool(path_rows))
        self.path_rows.show_groups((plans.RowGroup("Узлы по дороге до сервера", path_rows),) if path_rows else ())

        groups = plans.build_answer_groups(report)
        self.dns_card.setVisible(bool(groups))
        if groups:
            self.dns_summary.set_lines((plans.build_dns_summary(report),))
        self.dns_rows.show_groups(groups)

        neighbors = plans.build_neighbor_groups(report)
        self.neighbors_card.setVisible(bool(neighbors))
        self.neighbors_rows.show_groups(neighbors)

    def _open_report(self) -> None:
        if self._report is None:
            return
        self.report_requested.emit(
            LogReport(
                title=self._t("report.title", "Отчёт проверки домена"),
                text=plans.build_text_report(self._report),
                root_title=self._t("title", "Проверка домена"),
                empty_text=self._t("report.empty", "Проверка ещё не запускалась."),
                description=self._t(
                    "report.description", "Полный текст проверки: пинг, ответы DNS-серверов и домены на адресе."
                ),
            )
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


__all__ = ["RowsView", "DomainLookupPage"]
