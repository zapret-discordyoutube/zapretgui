"""Вкладка «Проверка домена»: пинг, ответы разных DNS и соседи по адресу.

Живёт вкладкой страницы BlockCheck. Сама в сеть не ходит: просит фасад DNS
запустить фоновую проверку и показывает результаты по мере готовности.
"""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
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
from blockcheck.ui.brand_icons import BrandIcon
from blockcheck.ui.result_cards import CardsGrid, TilesGrid, plain_tile
from blockcheck.ui.result_cards_model import Line
from dns.ui import domain_lookup_cards as lookup_cards
from log.log import log
from ui.accessibility import set_control_accessibility, set_state_text
from ui.widgets.check_hero import CheckHero
from ui.widgets.flat_section import FlatSection
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


class _TilesGroup(QWidget):
    """Группа строк: заголовок со значком и сетка плиток, которую рисует один виджет."""

    def __init__(self, group, icon: str, parent=None, *, clickable: bool = False) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(BrandIcon(icon, "", self, size=15), 0, Qt.AlignmentFlag.AlignVCenter)
        self.title_label = StrongBodyLabel(group.title, self)
        header.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)
        tiles = [plain_tile(Line(row.state, row.name, row.text)) for row in group.rows]
        if clickable:
            tiles = [replace(tile, hint=f"{tile.hint}\nНажмите, чтобы открыть эту проверку") for tile in tiles]
        self.grid = TilesGrid(tiles, self, clickable=clickable, min_width=TILE_MIN_WIDTH)
        layout.addWidget(self.grid)


class RowsView(QWidget):
    """Группы строк плитками: значок состояния, подпись и значение; полный текст — в подсказке.

    Плитки группы рисует один виджет. Строка-виджет на каждую запись (а их
    десятки) делала вкладку тяжёлой: долго строилась и дёргалась при прокрутке.
    """

    # Нажали плитку: (номер группы, номер строки в ней).
    opened = pyqtSignal(int, int)

    def __init__(self, parent=None, *, icon: str = "fa5s.list-ul", clickable: bool = False) -> None:
        super().__init__(parent)
        self._icon = icon
        self._clickable = clickable
        self._shown: tuple = ()
        self._blocks: list[QWidget] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(12)

    def groups(self) -> tuple:
        return self._shown

    def blocks(self) -> list[QWidget]:
        return list(self._blocks)

    def show_groups(self, groups) -> None:
        groups = tuple(groups)
        if groups == self._shown:
            return
        # Перестраивается только то, что изменилось: строка — это несколько виджетов,
        # и пересборка всех групп на каждый новый ответ подвешивала окно.
        same = 0
        while same < min(len(groups), len(self._shown)) and groups[same] == self._shown[same]:
            same += 1
        for block in self._blocks[same:]:
            self._layout.removeWidget(block)
            block.deleteLater()
        self._blocks = self._blocks[:same]
        self._shown = groups
        for group in groups[same:]:
            block = _TilesGroup(group, self._icon, self, clickable=self._clickable)
            block.grid.opened.connect(lambda row, order=len(self._blocks): self.opened.emit(order, row))
            self._layout.addWidget(block)
            self._blocks.append(block)
        set_state_text(self, "; ".join(f"{group.title}: строк {len(group.rows)}" for group in groups) or "нет данных")


# Карточек итога немного, и они широкие: в ряд встают две-четыре.
CARD_MIN_WIDTH = 300
# Плитка прошлой проверки шире обычной: в ней адрес, время и фраза итога.
TILE_MIN_WIDTH = 340
# Как часто экран перерисовывается, пока идут промежуточные ответы.
STAGE_REDRAW_MS = 300

HISTORY_TITLE = "Прошлые проверки"
HISTORY_SHOWN = 20


def history_groups(runs, title: str = HISTORY_TITLE) -> tuple:
    """Прошлые проверки вкладки одной группой строк, свежие сверху. Пусто — показывать нечего."""
    from diagnostics.history import history_rows

    rows = tuple(plans.Row(level, name, text) for level, name, text in history_rows(runs))
    return (plans.RowGroup(title, rows),) if rows else ()


class DomainLookupPage(BasePage):
    """Пинг, адреса с разных DNS и «кто ещё на этом адресе» для одного домена или IP."""

    # Просят показать отчёт страницей: её открывает страница-хозяин вкладки (LogReport).
    report_requested = pyqtSignal(object)
    # Нажали карточку итога: её подробности страницей открывает страница-хозяин вкладки.
    card_opened = pyqtSignal(object)

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
        self._pending_stage = None
        self._stage_timer = QTimer(self)
        self._stage_timer.setSingleShot(True)
        self._stage_timer.timeout.connect(self._flush_stage)
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
        # start=False: иначе анимация крутится и у скрытой полосы, пока жива страница.
        self.progress_bar = IndeterminateProgressBar(self.control_card, start=False)
        self.progress_bar.setVisible(False)
        self.control_card.add_widget(self.progress_bar)
        self.ticker = FunTicker(self.control_card)
        self.ticker.setVisible(False)
        self.control_card.add_widget(self.ticker)
        self.layout.addWidget(self.control_card)

        # Итог — несколькими карточками; всё подробное открывается по нажатию на карточку.
        self.cards = CardsGrid(CARD_MIN_WIDTH, self)
        self.cards.opened.connect(self.card_opened)
        self.cards.setVisible(False)
        self._cards_shown: list = []
        self.layout.addWidget(self.cards)

        # Прошлые проверки: что проверяли и чем кончилось. Видна, пока есть записи.
        # Без своей подложки: она есть у каждой плитки внутри.
        self.history_card = FlatSection()
        self.history_rows = RowsView(self.history_card, icon="fa5s.history", clickable=True)
        self.history_rows.opened.connect(lambda _group, row: self._open_past(row))
        self.history_card.add_widget(self.history_rows)
        self.layout.addWidget(self.history_card)

        self.history_card.setVisible(False)
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
        self._card_titles = {
            lookup_cards.KEY_PING: self._t("section.ping", "Пинг и сеть"),
            lookup_cards.KEY_PATH: self._t("section.path", "Путь до сервера"),
            lookup_cards.KEY_DNS: self._t("section.dns", "Адреса с разных DNS-серверов"),
            lookup_cards.KEY_NEIGHBORS: self._t("section.neighbors", "Кто ещё на этом адресе"),
        }
        set_control_accessibility(
            self.cards,
            name=self._t("cards.name", "Итог проверки домена"),
            description=self._t("cards.description", "Карточки частей проверки; нажатие открывает подробности."),
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
        self.cards.clear()
        self.cards.setVisible(False)
        self._cards_shown = []
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
        # Ответы приходят десятками за секунду; экран обновляется не чаще раза в STAGE_REDRAW_MS.
        self._pending_stage = report
        if not self._stage_timer.isActive():
            self._stage_timer.start(STAGE_REDRAW_MS)

    def _flush_stage(self) -> None:
        report, self._pending_stage = self._pending_stage, None
        if report is not None and not self._closed:
            self._show_report(report)

    def set_history(self, runs) -> None:
        """Показывает прошлые проверки (от старых к новым, как они лежат в настройках)."""
        self._history_runs = [dict(run) for run in runs or () if isinstance(run, dict)]
        groups = history_groups(self._history_runs)
        self.history_rows.show_groups(groups)
        self.history_card.setVisible(bool(groups))

    def _remember(self, report) -> None:
        # Запись в настройки и файл полного текста уже сделал фоновый поток проверки; экран берёт
        # ту же запись. Своя, собранная заново, была бы без пути к файлу — и прошлая проверка
        # открывалась бы словами «полный текст не сохранился» до перезапуска программы.
        entry = getattr(report, "history_entry", None) or plans.build_history_entry(report)
        if entry is not None:
            self.set_history([*getattr(self, "_history_runs", []), entry][-HISTORY_SHOWN:])

    def _on_finished(self, report) -> None:
        self._remember(report)
        if self._closed:
            return
        self._stage_timer.stop()
        self._pending_stage = None
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

        cards = lookup_cards.build_lookup_cards(report, getattr(self, "_card_titles", None))
        if cards != self._cards_shown:
            # Выплывают только при первом показе: промежуточные ответы обновляют карточки молча.
            self.cards.show_cards(cards, animate=not self._cards_shown)
            self._cards_shown = cards
        self.cards.setVisible(bool(cards))

    def result_cards(self) -> list:
        """Карточки итога, как они сейчас показаны."""
        return list(self._cards_shown)

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

    def _open_past(self, row: int) -> None:
        """Нажатие на плитку «Прошлых проверок»: полный текст той проверки страницей."""
        # На экране свежие сверху, а в списке они лежат от старых к новым.
        runs = list(reversed(getattr(self, "_history_runs", [])))
        if not 0 <= row < len(runs):
            return
        run = runs[row]
        from diagnostics.history import format_time
        from dns.domain_lookup import DomainLookupReport

        # Прошлая проверка — теми же карточками, что и свежая: её отчёт лежит в файле целиком.
        restore = getattr(self._dns, "load_past_domain_lookup_report", None)
        past = restore(str(run.get("log_file") or "")) if callable(restore) and not self._running else None
        if isinstance(past, DomainLookupReport):
            self._show_report(past)
            when = format_time(str(run.get("time") or ""))
            self.status_lines.set_lines(
                (plans.InfoLine(f"Показана прошлая проверка: {run.get('title', '')} · {when}", plans.TONE_ACCENT),)
            )
            self.report_button.setEnabled(True)
            return
        loader = getattr(self._dns, "load_past_domain_lookup", None)
        text = str(loader(str(run.get("log_file") or "")) if callable(loader) else "")
        if not text:
            # Запись сделана до того, как текст стали сохранять: показываем то, что есть в истории.
            lines = [f"Проверка: {run.get('title', '')}", str(run.get("headline") or ""), *map(str, run.get("problems") or ())]
            lines += ["", "Полный текст этой проверки не сохранился."]
            text = "\n".join(line for line in lines if line is not None)
        self.report_requested.emit(
            LogReport(
                title=f"{run.get('title', '')} · {format_time(str(run.get('time') or ''))}",
                text=text,
                root_title=self._t("title", "Проверка домена"),
                description=self._t("report.past.description", "Полный текст прошлой проверки домена."),
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
        self._stage_timer.stop()
        self._lane.close()
        super().cleanup()


__all__ = ["RowsView", "DomainLookupPage"]
