"""BlockCheck: какие сайты открываются и что делать с остальными.

Вкладки страницы: «BlockCheck» (сама проверка), «Подбор стратегии»,
«Проверка домена» и «DNS подмена». Бывшая вкладка «Диагностика» влилась в BlockCheck: режим
«Discord и YouTube» — это она.

Экран проверки сверху вниз: что проверить и кнопка → свои домены → итог
(одна фраза, проблемы по важности, советы и кнопка «Подобрать стратегию») →
список сайтов → «Отчёт» (отдельное окно) и «Подготовить обращение».
Проверяет движок ``diagnostics.engine.run_blockcheck`` в фоновом потоке.
"""

from __future__ import annotations

from blockcheck.strategy_search.probe_profile import PROTOCOL_STUN_VOICE, PROTOCOL_TCP_HTTPS
import logging
import time
from dataclasses import replace

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QHBoxLayout, QWidget

import blockcheck.page_runtime as blockcheck_page_runtime
from blockcheck import paired_run
from blockcheck.ui.check_results import BlockcheckHistoryList, BlockcheckSummaryPanel
from blockcheck.ui.result_cards import ProgressSteps, ResultCardsView, ResultDetailView
from blockcheck.ui.domain_chip import DomainChip
from diagnostics.services import SCOPE_ALL, SCOPE_FULL, SCOPE_MAIN, progress_steps
from blockcheck.ui.domains_build import build_blockcheck_domains_ui
from blockcheck.ui.helpers import (
    add_domain_chip,
    collect_extra_domains,
    remove_domain_chip,
)
from ui.performance_metrics import log_ui_timing_since
from blockcheck.page_run_workflow import (
    request_blockcheck_stop,
    reset_blockcheck_running_ui,
    start_blockcheck_page_run,
)
from ui.navigation.history import ScreenState
from ui.pages.base_page import BasePage
from ui.widgets.log_report_view import LogReport
from ui.accessibility import set_control_accessibility, set_state_text
from ui.combo_accessibility import set_combo_items_accessibility
from ui.segmented_accessibility import set_segmented_items_accessibility
from ui.latest_value_worker_state import LatestValueWorkerState
from ui.latest_worker_lane import LatestWorkerLane
from ui.one_shot_worker_runtime import OneShotWorkerRuntime
from ui.queued_worker_state import QueuedWorkerState
from app.ui_texts import tr as tr_catalog

from qfluentwidgets import (
    CheckBox,
    ComboBox,
    CaptionLabel,
    IndeterminateProgressBar,
    PrimaryPushButton,
    PushButton,
    LineEdit,
    SegmentedWidget,
    TransparentPushButton,
    FluentIcon,
)

from ui.widgets.flat_section import FlatSection
from ui.widgets.title_help_link import add_title_help_link
from config.urls import BLOCKCHECK_INFO_URL
from ui.fluent_widgets import SettingsCard, InfoBarHelper, set_tooltip
from log.log import log


logger = logging.getLogger(__name__)

def update_blockcheck_tabs_accessibility(pivot, *, current: object | None = None, language: str = "ru") -> None:
    if pivot is None:
        return
    labels = {
        "blockcheck": tr_catalog("page.blockcheck.tab.blockcheck", language=language, default="BlockCheck"),
        "strategy_scan": tr_catalog("page.blockcheck.tab.strategy_scan", language=language, default="Подбор стратегии"),
        "domain_lookup": tr_catalog("page.blockcheck.tab.domain_lookup", language=language, default="Проверка домена"),
        "dns_servers": tr_catalog("page.blockcheck.tab.dns_servers", language=language, default="DNS-серверы"),
        "dns_spoofing": tr_catalog("page.blockcheck.tab.dns_spoofing", language=language, default="DNS подмена"),
    }
    key = str(current or "").strip() if isinstance(current, str) else ""
    if not key:
        try:
            key = str(pivot.currentRouteKey() or "").strip()
        except Exception:
            key = ""
    selected = labels.get(key, key or "BlockCheck")
    state = f"Раздел BlockCheck, выбрано: {selected}"
    set_state_text(pivot, state)
    set_control_accessibility(
        pivot,
        name=state,
        description="Выберите раздел BlockCheck: BlockCheck, Подбор стратегии, Проверка домена, DNS-серверы или DNS подмена.",
    )
    set_segmented_items_accessibility(pivot, name="Раздел BlockCheck")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

# Как часто экран берёт свежий неполный отчёт, пока идёт проверка.
PARTIAL_REDRAW_MS = 400


# Между ключами шагов вглубь отчёта карточки в ключе экрана: «hostings>server:DE.AWS-01».
_CARD_LEVEL_SEPARATOR = ">"

class BlockcheckPage(BasePage):
    """BlockCheck — какие сайты открываются и что делать с остальными."""

    TAB_BLOCKCHECK = "blockcheck"
    TAB_STRATEGY_SCAN = "strategy_scan"
    TAB_DOMAIN_LOOKUP = "domain_lookup"
    TAB_DNS_SERVERS = "dns_servers"
    TAB_DNS_SPOOFING = "dns_spoofing"
    TAB_ORDER = (
        TAB_BLOCKCHECK,
        TAB_STRATEGY_SCAN,
        TAB_DOMAIN_LOOKUP,
        TAB_DNS_SERVERS,
        TAB_DNS_SPOOFING,
    )
    # «Диагностика» влилась в BlockCheck: старые ссылки на неё ведут сюда.
    TAB_ALIASES = {
        "diagnostics": TAB_BLOCKCHECK,
        "connection": TAB_BLOCKCHECK,
        "dns": TAB_DNS_SPOOFING,
        "domain": TAB_DOMAIN_LOOKUP,
        "ping": TAB_DOMAIN_LOOKUP,
        "servers": TAB_DNS_SERVERS,
    }

    def __init__(
        self,
        parent=None,
        *,
        blockcheck_feature,
        dns_feature,
        create_strategy_scan_worker,
        create_geo_sites_worker=None,
        open_dns_settings=None,
        ensure_zapret_for_strategy=None,
        start_zapret=None,
        zapret_is_running=None,
        stop_zapret=None,
    ):
        super().__init__(
            title=tr_catalog("page.blockcheck.title", default="BlockCheck"),
            subtitle=tr_catalog(
                "page.blockcheck.subtitle",
                default="Какие сайты открываются, почему не открываются остальные и что с этим делать",
            ),
            parent=parent,
            title_key="page.blockcheck.title",
            subtitle_key="page.blockcheck.subtitle",
        )
        self.setObjectName("BlockcheckPage")

        self._blockcheck = blockcheck_feature
        self._dns = dns_feature
        self._create_strategy_scan_worker = create_strategy_scan_worker
        self._create_geo_sites_worker = create_geo_sites_worker
        self._open_dns_settings = open_dns_settings
        self._ensure_zapret_for_strategy = ensure_zapret_for_strategy
        self._zapret_is_running = zapret_is_running
        self._start_zapret = start_zapret
        self._stop_zapret = stop_zapret
        # «С обходом и без»: две проверки подряд с переключением Zapret между ними. Без управления Zapret кнопки нет.
        self._paired = (
            paired_run.PairedRun(is_running=zapret_is_running, start=start_zapret, stop=stop_zapret)
            if zapret_is_running is not None and start_zapret is not None and stop_zapret is not None
            else None
        )
        self._paired_timer = QTimer(self)
        self._paired_timer.setInterval(500)
        self._paired_timer.timeout.connect(self._paired_tick)
        self._paired_waited = 0.0
        self._last_report: dict | None = None
        self._report_lines: list[str] = []
        # Настоящие прошлые проверки: экскурсия на время подменяет их примером.
        self._history_runs: tuple = ()
        # Проводник обучающей экскурсии; появляется, когда она дошла до этой страницы.
        self._tour_guide = None
        # Блок «Отчёт / Подготовить обращение» есть только после проверки:
        # до неё там показывать нечего.
        self._support_footer_available = False
        self._run_log_file: str | None = None
        self._tab_widgets: list[QWidget] = []
        self._detail_view: ResultDetailView | None = None
        # Страницы поверх вкладок: подробности одного DNS-сервера и подробный отчёт или лог.
        self._server_detail_view = None
        # Прошлая проверка из блока «Прошлые проверки», на всю страницу.
        self._past_check_view = None
        # Прошлая проверка других вкладок («Проверка домена»): страница с её карточками.
        self._past_cards_view = None
        # Страница прошлой проверки, из которой открыт отчёт карточки: «назад» ведёт в неё.
        self._detail_past_view = None
        # Откуда открыт отчёт карточки: (экран прошлой проверки или None, карточка).
        self._card_screen_origin = (None, None)
        # Отчёт карточки открыт из прошлой проверки — и где в ней стояла прокрутка.
        self._detail_from_past = False
        self._past_return_scroll = 0
        self._log_report_view = None
        self._over_tabs_return_scroll = 0
        # Что открыто поверх вкладок, для журнала экранов окна; None — видна сама вкладка.
        self._over_tabs_screen: ScreenState | None = None
        self._strategy_tab_page = None
        self._domain_lookup_tab_page = None
        self._dns_servers_tab_page = None
        self._dns_spoofing_tab_page = None
        self._active_tab_index: int = 0
        self._pending_tab_key: str | None = None
        self._cleanup_in_progress = False
        self._tabs_pivot = None
        self._domains_caption = None
        # Поле своих доменов открыто по ссылке «+ Свой домен» и спрятано на время проверки.
        self._custom_domains_open = False
        self._domains_hidden_for_run = False
        self._custom_domain_btn = None
        self._domains_flow = None
        self._prepare_support_btn = None
        self._support_status_label = None
        self._initial_state = blockcheck_page_runtime.BlockcheckPageInitialStatePlan(user_domains=())
        self._initial_state_runtime = OneShotWorkerRuntime()
        self._initial_state_load_started_at = 0.0
        self._run_runtime = OneShotWorkerRuntime()
        self._support_prepare_runtime = OneShotWorkerRuntime()
        self._support_prepare_state = LatestValueWorkerState(self._support_prepare_runtime, empty_value=None)
        self._user_domain_action_runtime = OneShotWorkerRuntime()
        self._user_domain_action_state = QueuedWorkerState(self._user_domain_action_runtime)
        # Отчёт прошлой проверки читается с диска в фоне; открывается последняя нажатая строка.
        self._past_report_lane = LatestWorkerLane(
            name="blockcheck past report",
            create_worker=lambda request_id, run: self._blockcheck.create_past_report_worker(
                request_id, log_file=str(run.get("log_file") or ""), parent=self
            ),
            on_result=self._show_past_check,
            on_error=lambda run, _error: self._show_past_check(run, None),
            log_fn=log,
        )
        self._build_ui()
        self._request_page_initial_state_load()
        try:
            self.set_ui_language(self._ui_language)
        except Exception:
            pass

    def create_initial_state_worker(self, request_id: int):
        return self._blockcheck.create_page_initial_state_worker(request_id, parent=self)

    def create_support_prepare_worker(
        self,
        request_id: int,
        *,
        run_log_file: str | None,
        mode_label: str,
        extra_domains: list[str],
    ):
        return self._blockcheck.create_blockcheck_support_prepare_worker(
            request_id,
            run_log_file=run_log_file,
            mode_label=mode_label,
            extra_domains=extra_domains,
            parent=self,
        )

    def create_user_domain_action_worker(self, request_id: int, *, action: str, domain: str):
        return self._blockcheck.create_user_domain_action_worker(
            request_id,
            action=action,
            domain=domain,
            parent=self,
        )

    def _request_page_initial_state_load(self) -> None:
        self._initial_state_load_started_at = time.perf_counter()

        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_initial_state_loaded)
            worker.failed.connect(self._on_initial_state_failed)

        self._initial_state_runtime.start_qthread_worker(
            worker_factory=self.create_initial_state_worker,
            bind_worker=bind_worker,
        )

    def _on_initial_state_loaded(self, request_id: int, initial_state) -> None:
        if not self._initial_state_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        self._log_ui_timing("blockcheck_ui.initial_state.load", self._initial_state_load_started_at)
        self._initial_state = initial_state
        self._apply_initial_domain_chips(tuple(getattr(initial_state, "user_domains", ()) or ()))
        self._show_history(tuple(getattr(initial_state, "check_history", ()) or ()))
        self._push_tab_histories()

    def _push_tab_histories(self) -> None:
        """Отдаёт вкладкам «Проверка домена» и «DNS подмена» их прошлые проверки (если вкладки уже созданы)."""
        for page, key in (
            (self._domain_lookup_tab_page, "domain_history"),
            (self._dns_spoofing_tab_page, "dns_history"),
            (self._dns_servers_tab_page, "servers_history"),
        ):
            if page is not None and hasattr(page, "set_history"):
                page.set_history(tuple(getattr(self._initial_state, key, ()) or ()))

    def _show_history(self, runs) -> None:
        """Обновляет карточку «Прошлые проверки»; без записей и на чужой вкладке она скрыта."""
        self._history_runs = tuple(runs or ())
        if self._tour_demo_shown():
            # На экране пример экскурсии: настоящие записи встанут на место после неё.
            return
        self._history_list.show_history(runs)
        on_main_tab = self.TAB_ORDER[self._active_tab_index] == self.TAB_BLOCKCHECK
        self._history_card.setVisible(bool(self._history_list.lines()) and on_main_tab)

    def _on_initial_state_failed(self, request_id: int, error: str) -> None:
        if not self._initial_state_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        log(f"Не удалось загрузить начальное состояние BlockCheck: {error}", "WARNING")

    def _apply_pending_tab_if_ready(self) -> None:
        pending_tab_key = str(getattr(self, "_pending_tab_key", "") or "").strip().lower()
        if pending_tab_key:
            if not self.is_page_ready():
                return
            self._pending_tab_key = None
            self._switch_tab(self.TAB_ORDER.index(self._normalize_tab_key(pending_tab_key)))

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        total_started_at = time.perf_counter()
        section_started_at = time.perf_counter()
        # Ссылка на инструкцию стоит в одном ряду с заголовком страницы.
        self._title_header = None
        self._help_button = None
        header = add_title_help_link(
            self,
            BLOCKCHECK_INFO_URL,
            tooltip="Открыть инструкцию: как подобрать стратегию",
            description="Открывает в браузере инструкцию о том, как подобрать стратегию.",
        )
        if header is not None:
            self._title_header, self._help_button = header
        self._tabs_pivot = SegmentedWidget(self)
        self._tabs_pivot.addItem(
            self.TAB_BLOCKCHECK,
            tr_catalog("page.blockcheck.tab.blockcheck", default="BlockCheck"),
            lambda: self.switch_to_tab(self.TAB_BLOCKCHECK),
        )
        self._tabs_pivot.addItem(
            self.TAB_STRATEGY_SCAN,
            tr_catalog("page.blockcheck.tab.strategy_scan", default="Подбор стратегии"),
            lambda: self.switch_to_tab(self.TAB_STRATEGY_SCAN),
        )
        self._tabs_pivot.addItem(
            self.TAB_DOMAIN_LOOKUP,
            tr_catalog("page.blockcheck.tab.domain_lookup", default="Проверка домена"),
            lambda: self.switch_to_tab(self.TAB_DOMAIN_LOOKUP),
        )
        self._tabs_pivot.addItem(
            self.TAB_DNS_SERVERS,
            tr_catalog("page.blockcheck.tab.dns_servers", default="DNS-серверы"),
            lambda: self.switch_to_tab(self.TAB_DNS_SERVERS),
        )
        self._tabs_pivot.addItem(
            self.TAB_DNS_SPOOFING,
            tr_catalog("page.blockcheck.tab.dns_spoofing", default="DNS подмена"),
            lambda: self.switch_to_tab(self.TAB_DNS_SPOOFING),
        )
        self._tabs_pivot.setCurrentItem(self.TAB_BLOCKCHECK)
        self._tabs_pivot.setItemFontSize(13)
        self._update_tabs_accessibility(self.TAB_BLOCKCHECK)
        self._tabs_pivot.currentItemChanged.connect(self._update_tabs_accessibility)
        self.add_widget(self._tabs_pivot)
        self._log_ui_timing("blockcheck_ui.tabs.build", section_started_at)

        # ── Итог и управление — одна панель, как на вкладке «DNS-серверы»: главная фраза,
        # пояснение и под ними кнопка «Проверить» с выбором набора ──
        section_started_at = time.perf_counter()
        self._summary_panel = BlockcheckSummaryPanel(
            on_action=self._on_problem_action,
            parent=self.content,
            on_open=self._open_card_by_key,
            # Находка открывает свою страницу, а не шаг внутрь отчёта раздела.
            on_open_page=self._open_card_detail,
        )
        # Esc закрывает любую подстраницу раздела, где бы ни стоял фокус.
        self._pending_partial = None
        self._partial_timer = QTimer(self)
        self._partial_timer.setSingleShot(True)
        self._partial_timer.timeout.connect(self._flush_partial)
        self._escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._escape_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._escape_shortcut.activated.connect(self._close_subpage)
        self._add_tab_widget(self._summary_panel)
        # Имя осталось от отдельной карточки управления: теперь управление стоит в панели итога.
        self._control_card = self._summary_panel
        # Панель запуска в две строки (решение пользователя 2026-10-31: «слишком
        # перегружено»). Первая — кнопки и выбор набора, вторая — галочка сверки с
        # сервером и ссылка «+ Свой домен»; поле своих доменов открывается по ссылке,
        # строка состояния видна только во время проверки и после неё. Подпись «Что
        # проверить:» убрана: выпадающий список сам называет набор.
        row = self._summary_panel.actions
        self._scope_combo = ComboBox()
        # userData — именованным: второй позиционный аргумент у qfluentwidgets —
        # значок, и значение терялось (старый выбор режима всегда давал «Полную»).
        # Первыми и по умолчанию — сайты: их проверка ходит как браузер. «Глубокая
        # диагностика» — по выбору человека: поиск места фильтра, пачки соединений,
        # серии UDP и прочие пробы, которых браузер не делает (utils.probe_gate).
        self._scope_combo.addItem(
            tr_catalog("page.blockcheck.scope_all", default="Только сайты (быстро)"), userData=SCOPE_ALL
        )
        self._scope_combo.addItem(
            tr_catalog("page.blockcheck.scope_main", default="Только Discord и YouTube (быстро)"), userData=SCOPE_MAIN
        )
        self._scope_combo.addItem(
            tr_catalog("page.blockcheck.scope_full", default="Глубокая диагностика (около минуты)"), userData=SCOPE_FULL
        )
        self._scope_combo.setCurrentIndex(0)
        self._scope_combo.setMinimumWidth(320)
        self._update_scope_combo_accessibility()
        self._scope_combo.currentIndexChanged.connect(self._update_scope_combo_accessibility)
        # Кнопки «Проверить», «С обходом и без», «Остановить» встают перед списком ниже (insertWidget 0–2).
        row.addWidget(self._scope_combo)
        # Сервер проекта открывает закрытый сайт из Нидерландов: так видно, заблокирован он
        # или не работает сам. Само соединение с сервером выдаёт программу провайдеру,
        # поэтому без галочки запрос туда не уходит (utils.probe_gate, решение 2026-10-16).
        self._outside_box = CheckBox(
            tr_catalog("page.blockcheck.ask_outside", default="Сверить с сервером в Нидерландах")
        )
        outside_description = tr_catalog(
            "page.blockcheck.ask_outside.description",
            default=(
                "Закрытые сайты проверит и сервер Zapret DNS в Нидерландах: так видно, "
                "блокировка это или сайт не работает сам. Провайдер увидит соединение с сервером проекта."
            ),
        )
        set_tooltip(self._outside_box, outside_description)
        set_control_accessibility(self._outside_box, name="Сверить с сервером в Нидерландах", description=outside_description)
        self._options_row = QWidget()
        options = QHBoxLayout(self._options_row)
        options.setContentsMargins(0, 0, 0, 0)
        options.setSpacing(10)
        options.addWidget(self._outside_box)
        options.addStretch(1)
        self._custom_domain_btn = TransparentPushButton(
            FluentIcon.ADD, tr_catalog("page.blockcheck.custom_domain.open", default="Свой домен")
        )
        custom_domain_description = tr_catalog(
            "page.blockcheck.custom_domain.open.description",
            default="Добавить в проверку сайт, которого нет в списке BlockCheck.",
        )
        set_tooltip(self._custom_domain_btn, custom_domain_description)
        set_control_accessibility(
            self._custom_domain_btn, name="Добавить свой домен в BlockCheck", description=custom_domain_description
        )
        self._custom_domain_btn.clicked.connect(self._open_custom_domains)
        options.addWidget(self._custom_domain_btn)
        self._summary_panel.controls.addWidget(self._options_row)

        self._status_label = CaptionLabel(
            tr_catalog("page.blockcheck.ready", default="Сайты, хостинги и звонки — так, как их открыл бы браузер")
        )
        self._status_label.setWordWrap(True)
        self._set_status_text(self._status_label.text())
        self._summary_panel.controls.addWidget(self._status_label)

        # start=False: иначе анимация крутится и у скрытой полосы, пока жива страница.
        self._progress_bar = IndeterminateProgressBar(start=False)
        self._progress_bar.setVisible(False)
        self._progress_bar.setFixedWidth(160)
        set_control_accessibility(
            self._progress_bar,
            name="Ход BlockCheck: не выполняется",
            description="Показывает, что проверка BlockCheck выполняется.",
        )
        set_state_text(self._progress_bar, "Ход BlockCheck: не выполняется")
        row.addStretch(1)
        row.addWidget(self._progress_bar)

        self._start_btn = PrimaryPushButton(tr_catalog("page.blockcheck.start", default="Проверить"))
        self._start_btn.setIcon(FluentIcon.PLAY)
        start_description = tr_catalog(
            "page.blockcheck.action.start.description",
            default="Проверить, какие сайты открываются и что мешает остальным.",
        )
        set_tooltip(self._start_btn, start_description)
        set_control_accessibility(self._start_btn, name="Запустить BlockCheck", description=start_description)
        set_state_text(self._start_btn, "Запустить BlockCheck")
        self._start_btn.clicked.connect(self._on_start)
        row.insertWidget(0, self._start_btn)

        self._paired_btn = PushButton(tr_catalog("page.blockcheck.paired", default="С обходом и без"))
        self._paired_btn.setIcon(FluentIcon.SYNC)
        paired_description = tr_catalog(
            "page.blockcheck.action.paired.description",
            default=(
                "Проверить дважды подряд: как сейчас и после переключения Zapret (выключится, если работает, "
                "или включится), затем сравнить. Zapret вернётся в прежнее состояние."
            ),
        )
        set_tooltip(self._paired_btn, paired_description)
        set_control_accessibility(self._paired_btn, name="Проверить с обходом и без", description=paired_description)
        set_state_text(self._paired_btn, "Проверить с обходом и без")
        self._paired_btn.clicked.connect(self._on_start_paired)
        # Без управления Zapret (в окне без запуска обхода) кнопка остаётся на месте, но недоступна.
        self._paired_btn.setEnabled(self._paired is not None)
        row.insertWidget(1, self._paired_btn)

        self._stop_btn = PushButton(tr_catalog("page.blockcheck.stop", default="Остановить"))
        self._stop_btn.setIcon(FluentIcon.CANCEL)
        stop_description = tr_catalog(
            "page.blockcheck.action.stop.description",
            default="Остановить текущую проверку.",
        )
        set_tooltip(self._stop_btn, stop_description)
        set_control_accessibility(self._stop_btn, name="Остановить BlockCheck", description=stop_description)
        set_state_text(self._stop_btn, "Остановить BlockCheck")
        self._stop_btn.clicked.connect(self._on_stop)
        self._stop_btn.setEnabled(False)
        self._stop_btn.setVisible(False)
        row.insertWidget(2, self._stop_btn)
        self._log_ui_timing("blockcheck_ui.control_card.build", section_started_at)

        # ── Свои домены ──
        section_started_at = time.perf_counter()
        domains_widgets = build_blockcheck_domains_ui(
            tr_fn=lambda key, default: tr_catalog(key, default=default),
            # Строка стоит в шапке панели итога, своей подложки у неё нет.
            settings_card_cls=lambda: FlatSection(spacing=4),
            qhbox_layout_cls=QHBoxLayout,
            qwidget_cls=QWidget,
            caption_label_cls=CaptionLabel,
            line_edit_cls=LineEdit,
            push_button_cls=PushButton,
            on_add=self._on_add_domain,
        )
        self._domains_card = domains_widgets.card
        self._domains_caption = domains_widgets.caption_label
        self._domain_input = domains_widgets.input_edit
        self._add_domain_btn = domains_widgets.add_button
        self._domains_flow = domains_widgets.flow_widget
        self._domains_flow_layout = domains_widgets.flow_layout
        # Свои домены — часть управления проверкой: под кнопкой «Проверить», а не под результатами.
        self._summary_panel.controls.addWidget(self._domains_card)
        self._log_ui_timing("blockcheck_ui.domains_card.build", section_started_at)

        # Ход проверки по шагам: виден, только пока она идёт.
        self._progress_card = SettingsCard()
        self._progress_steps = ProgressSteps()
        self._progress_card.add_widget(self._progress_steps)
        self._add_tab_widget(self._progress_card)
        self._progress_card.setVisible(False)

        # Карточки: по одной на сайт и на каждую проверку. Нажатие открывает подробности.
        # Без своей подложки: она есть у каждой карточки внутри.
        self._results_card = FlatSection()
        self._result_cards = ResultCardsView()
        self._result_cards.opened.connect(self._open_card_detail)
        self._results_card.add_widget(self._result_cards)
        self._add_tab_widget(self._results_card)
        # До первой проверки показывать нечего.
        self._results_card.setVisible(False)

        # ── Прошлые проверки ──
        self._history_card = SettingsCard()
        self._history_list = BlockcheckHistoryList()
        self._history_list.run_opened.connect(self._open_past_check)
        self._history_card.add_widget(self._history_list)
        self._add_tab_widget(self._history_card)
        self._history_card.setVisible(False)

        # ── Отчёт и обращение ──
        self._footer_card = SettingsCard()
        footer = QHBoxLayout()
        footer.setSpacing(10)
        self._support_status_label = CaptionLabel("")
        set_state_text(self._support_status_label, "Статус обращения BlockCheck: нет статуса")
        footer.addWidget(self._support_status_label, 1)
        self._report_btn = PushButton(tr_catalog("page.blockcheck.report", default="Отчёт"))
        self._report_btn.setIcon(FluentIcon.DOCUMENT)
        set_control_accessibility(
            self._report_btn,
            name="Открыть подробный отчёт BlockCheck",
            description="Открывает окно с техническими подробностями проверки.",
        )
        self._report_btn.clicked.connect(self._open_report)
        self._report_btn.setEnabled(False)
        footer.addWidget(self._report_btn)
        self._prepare_support_btn = PushButton(
            tr_catalog("page.blockcheck.prepare_support", default="Подготовить обращение")
        )
        self._prepare_support_btn.setIcon(FluentIcon.SEND)
        set_control_accessibility(
            self._prepare_support_btn,
            name="Подготовить обращение по BlockCheck",
            description="Готовит обращение с логами BlockCheck для поддержки.",
        )
        self._prepare_support_btn.clicked.connect(self._prepare_support_from_blockcheck)
        footer.addWidget(self._prepare_support_btn)
        self._footer_card.add_layout(footer)
        self._add_tab_widget(self._footer_card)
        self._footer_card.setVisible(False)

        section_started_at = time.perf_counter()
        self._sync_domains_flow_visibility()
        self._apply_initial_domain_chips(self._initial_state.user_domains)
        self._log_ui_timing("blockcheck_ui.domain_chips.apply", section_started_at)

        section_started_at = time.perf_counter()
        self._switch_tab(0)
        self._log_ui_timing("blockcheck_ui.initial_tab.switch", section_started_at)
        self._log_ui_timing("blockcheck_ui.build.total", total_started_at)

    def _apply_page_theme(self, tokens=None, force: bool = False):
        _ = tokens
        _ = force
        # Цвета итогов и строк обновляются самими виджетами; здесь — плашки доменов.
        for i in range(self._domains_flow_layout.count()):
            item = self._domains_flow_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), DomainChip):
                item.widget()._apply_chip_style()

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    def _add_tab_widget(self, widget: QWidget) -> None:
        """Add a widget to BlockCheck tab content list."""
        self._tab_widgets.append(widget)
        self.add_widget(widget)

    def _ensure_strategy_tab(self):
        """Create embedded strategy-scan tab on first open."""
        if self._strategy_tab_page is not None:
            return
        started_at = time.perf_counter()
        try:
            from blockcheck.ui.strategy_scan_page import StrategyScanPage
            self._strategy_tab_page = StrategyScanPage(
                parent=self,
                embedded=True,
                blockcheck_feature=self._blockcheck,
                create_strategy_scan_worker=self._create_strategy_scan_worker,
                create_geo_sites_worker=self._create_geo_sites_worker,
                open_hosts_editor=lambda: self._on_problem_action("hosts", ""),
                open_dns_settings=lambda: self._on_problem_action("dns", ""),
                ensure_zapret_for_strategy=self._ensure_zapret_for_strategy,
                zapret_is_running=self._zapret_is_running,
            )
            self._strategy_tab_page.report_requested.connect(self._open_log_report)
            self._strategy_tab_page.setVisible(False)
            self.add_widget(self._strategy_tab_page)
            try:
                self._strategy_tab_page.set_ui_language(self._ui_language)
            except Exception:
                pass
        except Exception as e:
            logger.warning("Failed to create embedded strategy tab: %s", e)
        finally:
            self._log_ui_timing("blockcheck_ui.strategy_tab.build", started_at)

    def _ensure_domain_lookup_tab(self):
        """Create embedded domain lookup tab on first open."""
        if self._domain_lookup_tab_page is not None:
            return
        started_at = time.perf_counter()
        try:
            from dns.ui.domain_lookup_page import DomainLookupPage

            self._domain_lookup_tab_page = DomainLookupPage(
                parent=self,
                dns_feature=self._dns,
                embedded=True,
            )
            self._domain_lookup_tab_page.report_requested.connect(self._open_log_report)
            self._domain_lookup_tab_page.card_opened.connect(self._open_card_detail)
            self._domain_lookup_tab_page.past_opened.connect(self._open_past_cards)
            self._push_tab_histories()
            self._domain_lookup_tab_page.setVisible(False)
            self.add_widget(self._domain_lookup_tab_page)
            try:
                self._domain_lookup_tab_page.set_ui_language(self._ui_language)
            except Exception:
                pass
        except Exception as e:
            logger.warning("Failed to create domain lookup tab: %s", e)
        finally:
            self._log_ui_timing("blockcheck_ui.domain_lookup_tab.build", started_at)

    def _ensure_dns_servers_tab(self):
        """Create embedded DNS servers tab on first open."""
        if self._dns_servers_tab_page is not None:
            return
        started_at = time.perf_counter()
        try:
            from dns.ui.server_check_page import ServerCheckPage

            self._dns_servers_tab_page = ServerCheckPage(
                parent=self,
                dns_feature=self._dns,
                embedded=True,
                open_dns_settings=self._open_dns_settings,
            )
            self._dns_servers_tab_page.details_requested.connect(self._open_server_detail)
            self._dns_servers_tab_page.report_requested.connect(self._open_log_report)
            self._dns_servers_tab_page.past_view_opened.connect(self._open_past_content)
            self._push_tab_histories()
            self._dns_servers_tab_page.setVisible(False)
            self.add_widget(self._dns_servers_tab_page)
            try:
                self._dns_servers_tab_page.set_ui_language(self._ui_language)
            except Exception:
                pass
        except Exception as e:
            logger.warning("Failed to create DNS servers tab: %s", e)
        finally:
            self._log_ui_timing("blockcheck_ui.dns_servers_tab.build", started_at)

    def _ensure_dns_spoofing_tab(self):
        """Create embedded DNS spoofing tab on first open."""
        if self._dns_spoofing_tab_page is not None:
            return
        started_at = time.perf_counter()
        try:
            from dns.ui.dns_check_page import DNSCheckPage

            self._dns_spoofing_tab_page = DNSCheckPage(
                parent=self,
                dns_feature=self._dns,
                embedded=True,
                open_dns_settings=self._open_dns_settings,
            )
            self._dns_spoofing_tab_page.report_requested.connect(self._open_log_report)
            self._dns_spoofing_tab_page.past_view_opened.connect(self._open_past_content)
            self._push_tab_histories()
            self._dns_spoofing_tab_page.setVisible(False)
            self.add_widget(self._dns_spoofing_tab_page)

            try:
                self._dns_spoofing_tab_page.set_ui_language(self._ui_language)
            except Exception:
                pass
        except Exception as e:
            logger.warning("Failed to create DNS spoofing tab: %s", e)
        finally:
            self._log_ui_timing("blockcheck_ui.dns_spoofing_tab.build", started_at)

    @classmethod
    def _normalize_tab_key(cls, key: str | None) -> str:
        raw_key = str(key or "").strip().lower()
        if raw_key in cls.TAB_ORDER:
            return raw_key
        return cls.TAB_ALIASES.get(raw_key, cls.TAB_BLOCKCHECK)

    def switch_to_tab(self, key: str) -> None:
        """External API: switch to one of BlockCheck tabs."""
        normalized = self._normalize_tab_key(key)
        if not self.is_page_ready():
            self._pending_tab_key = normalized
            self._over_tabs_screen = None
            self.navigation_screen_changed.emit()
            self.run_when_page_ready(self._apply_pending_tab_if_ready)
            return
        self._pending_tab_key = None
        self._switch_tab(self.TAB_ORDER.index(normalized))

    def _update_tabs_accessibility(self, current: object | None = None) -> None:
        update_blockcheck_tabs_accessibility(
            self._tabs_pivot,
            current=current,
            language=self.__dict__.get("_ui_language", "ru"),
        )

    def handle_page_command(self, command: str, payload: dict) -> bool:
        _ = payload
        normalized = str(command or "").strip().lower()
        if normalized == "stop_runtime_conflicting_checks":
            return self.request_runtime_conflicting_stop()
        return False

    def request_runtime_conflicting_stop(self) -> bool:
        """Останавливает проверки BlockCheck перед ручным запуском основного DPI."""
        stopped = False
        if self._run_runtime.is_running():
            self._on_stop()
            stopped = True

        strategy_page = self._strategy_tab_page
        request_stop = getattr(strategy_page, "request_runtime_conflicting_stop", None)
        if callable(request_stop):
            stopped = bool(request_stop()) or stopped

        return stopped

    def _switch_tab(self, index: int) -> None:
        """Переключает вкладки BlockCheck / Подбор стратегии / Проверка домена / DNS-серверы / DNS подмена."""
        started_at = time.perf_counter()
        if not self.TAB_ORDER:
            return
        index = max(0, min(int(index), len(self.TAB_ORDER) - 1))
        tab_key = self.TAB_ORDER[index]
        self._active_tab_index = index
        self._over_tabs_screen = None
        self.navigation_screen_changed.emit()
        # Вкладку могут сменить и снаружи, пока поверх открыты подробности или отчёт.
        for view in (self._detail_view, self._server_detail_view, self._log_report_view, self._past_check_view, self._past_cards_view):
            if view is not None and not view.isHidden():
                view.setVisible(False)
                self._tabs_pivot.setVisible(True)
        self._set_page_header_visible(True)

        if self._tabs_pivot is not None:
            try:
                self._tabs_pivot.setCurrentItem(tab_key)
            except Exception:
                pass
            self._update_tabs_accessibility(tab_key)

        if tab_key == self.TAB_STRATEGY_SCAN:
            self._ensure_strategy_tab()
        elif tab_key == self.TAB_DOMAIN_LOOKUP:
            self._ensure_domain_lookup_tab()
        elif tab_key == self.TAB_DNS_SERVERS:
            self._ensure_dns_servers_tab()
        elif tab_key == self.TAB_DNS_SPOOFING:
            self._ensure_dns_spoofing_tab()

        show_blockcheck = tab_key == self.TAB_BLOCKCHECK
        for widget in self._tab_widgets:
            widget.setVisible(show_blockcheck)
        # Пример экскурсии показывает те же карточки, что и законченная проверка.
        # Карточки с хода идущей проверки тоже есть что показать.
        has_report = self._last_report is not None or self._tour_demo_shown() or self._result_cards.has_cards()
        if self._results_card is not None and not has_report:
            self._results_card.setVisible(False)
        if not self._run_runtime.is_running():
            self._progress_card.setVisible(False)
        if not self._history_list.lines():
            self._history_card.setVisible(False)
        if not self._support_footer_available and not self._tour_demo_shown():
            self._footer_card.setVisible(False)

        if self._strategy_tab_page is not None:
            self._strategy_tab_page.setVisible(tab_key == self.TAB_STRATEGY_SCAN)
        if self._domain_lookup_tab_page is not None:
            self._domain_lookup_tab_page.setVisible(tab_key == self.TAB_DOMAIN_LOOKUP)
        if self._dns_servers_tab_page is not None:
            self._dns_servers_tab_page.setVisible(tab_key == self.TAB_DNS_SERVERS)
        if self._dns_spoofing_tab_page is not None:
            self._dns_spoofing_tab_page.setVisible(tab_key == self.TAB_DNS_SPOOFING)

        self._log_ui_timing(f"blockcheck_ui.switch_tab.{tab_key}", started_at)

    # ------------------------------------------------------------------
    # Проверка
    # ------------------------------------------------------------------

    def _current_scope(self) -> str:
        return str(self._scope_combo.currentData() or SCOPE_MAIN)

    def _on_start(self):
        # Закрытую страницу не запускаем: поздние сигналы прошлого запуска уже никто не ждёт.
        if self._run_runtime.is_running() or self._cleanup_in_progress:
            return
        self._last_report = None
        # Отчёт прошлого запуска не должен уйти в обращение, если у этого запуска сохранить отчёт не вышло.
        self._run_log_file = None
        self._report_lines = []
        self._summary_panel.set_pending()
        self._drop_partial()
        self._result_cards.clear()
        self._results_card.setVisible(False)
        scope = self._current_scope()
        self._outside_box.setEnabled(False)
        self._progress_steps.start(progress_steps(scope))
        self._progress_card.setVisible(True)
        # Список сайтов проверки уже взят: добавлять свои домены до конца проверки бесполезно.
        self._domains_hidden_for_run = True
        self._sync_domains_area()
        self._report_btn.setEnabled(False)
        self._set_support_footer_available(False)
        start_blockcheck_page_run(
            blockcheck_feature=self._blockcheck,
            scope=scope,
            user_domains=self._get_extra_domains(),
            ask_outside=self._outside_box.isChecked(),
            parent=self,
            run_runtime=self._run_runtime,
            start_button=self._start_btn,
            stop_button=self._stop_btn,
            scope_combo=self._scope_combo,
            progress_bar=self._progress_bar,
            status_label=self._status_label,
            set_support_status=self._set_support_status,
            tr_fn=tr_catalog,
            on_log=self._on_log,
            on_run_log_started=self._on_run_log_started,
            on_finished=self._on_finished,
            on_progress=self._on_progress,
            on_partial=self._on_partial,
        )
        self._set_status_text(self._status_label.text())

    def _on_progress(self, step: str, done: int, total: int) -> None:
        if self._cleanup_in_progress:
            return
        self._progress_steps.set_progress(step, done, total)

    def _on_partial(self, report) -> None:
        """Неполный отчёт по ходу проверки. Их до шестидесяти за прогон, сайты идут пачками —
        экран обновляется не чаще раза в ``PARTIAL_REDRAW_MS`` и берёт самый свежий."""
        if self._cleanup_in_progress or not isinstance(report, dict):
            return
        self._pending_partial = report
        if not self._partial_timer.isActive():
            self._partial_timer.start(PARTIAL_REDRAW_MS)

    def _flush_partial(self) -> None:
        report, self._pending_partial = self._pending_partial, None
        if report is None or self._cleanup_in_progress or not self._run_runtime.is_running():
            return
        self._progress_steps.set_seconds(report.get("step_seconds") or {})
        # Пока на экране пример экскурсии, настоящие карточки ждут итога.
        if self._tour_demo_shown():
            return
        self._result_cards.show_partial(report)
        on_main_tab = self.TAB_ORDER[self._active_tab_index] == self.TAB_BLOCKCHECK
        self._results_card.setVisible(on_main_tab and not self._tabs_pivot.isHidden() and self._result_cards.has_cards())

    def _drop_partial(self) -> None:
        self._partial_timer.stop()
        self._pending_partial = None

    def _on_log(self, message: str):
        if self._cleanup_in_progress:
            return
        self._report_lines.append(str(message or ""))

    def _on_finished(self, report):
        self._on_finished_core(report)
        self._paired_after(report)

    # --- «С обходом и без» ------------------------------------------------------------

    def _set_paired_buttons(self, *, busy: bool) -> None:
        """Пока идёт парный запуск (и между проверками), ни одна кнопка запуска не нужна."""
        self._paired_btn.setEnabled(self._paired is not None and not busy)
        if busy:
            self._start_btn.setEnabled(False)

    def _on_start_paired(self) -> None:
        paired = self._paired
        if paired is None or paired.active or self._run_runtime.is_running() or self._cleanup_in_progress:
            return
        paired.begin()
        self._set_paired_buttons(busy=True)
        self._on_start()
        self._set_status_text(f"Парная проверка: сначала {paired.first_label}")

    def _paired_after(self, report) -> None:
        """Конец одной проверки парного запуска: переключить Zapret для второй или вернуть его и закончить."""
        paired = self._paired
        if paired is None or not paired.active or self._cleanup_in_progress:
            return
        ok = isinstance(report, dict) and not report.get("failed")
        if paired.phase == paired_run.PHASE_FIRST and ok:
            self._set_paired_buttons(busy=True)
            self._set_status_text(f"Проверка {paired.first_label} готова. Переключаю Zapret, чтобы проверить {paired.second_label}…")
            reason = paired.switch()
            if reason:
                self._paired_end(f"Парная проверка прервана: {reason}")
                return
            self._paired_waited = 0.0
            self._paired_timer.start()
            return
        if paired.phase == paired_run.PHASE_SECOND and ok:
            self._paired_end("Готово: проверки с обходом и без — сравнение ниже")
            return
        self._paired_end("Парная проверка остановлена")

    def _paired_tick(self) -> None:
        paired = self._paired
        if paired is None or paired.phase != paired_run.PHASE_SWITCHING or self._cleanup_in_progress:
            self._paired_timer.stop()
            return
        self._paired_waited += self._paired_timer.interval() / 1000
        if paired.settled():
            self._paired_timer.stop()
            QTimer.singleShot(int(paired_run.SETTLE_PAUSE_SECONDS * 1000), self._paired_start_second)
        elif self._paired_waited >= paired_run.SETTLE_WAIT_SECONDS:
            self._paired_timer.stop()
            self._paired_end("Парная проверка прервана: Zapret не сменил состояние за 30 секунд")

    def _paired_start_second(self) -> None:
        paired = self._paired
        if paired is None or paired.phase != paired_run.PHASE_SWITCHING or self._cleanup_in_progress:
            return
        paired.second_started()
        self._on_start()
        self._set_status_text(f"Парная проверка: теперь {paired.second_label}")

    def _paired_end(self, text: str) -> None:
        """Вернуть Zapret, как был, снять блокировку кнопок и сказать, чем кончилось."""
        self._paired_timer.stop()
        problem = self._paired.finish() if self._paired is not None else ""
        self._reset_ui()
        self._set_paired_buttons(busy=False)
        self._set_status_text(f"{text}. {problem}".strip() if problem else text)

    def _on_finished_core(self, report):
        # Отложенный неполный отчёт уже не нужен: дальше идёт итог (или остановка).
        self._drop_partial()
        if self._cleanup_in_progress:
            return
        self._reset_ui()
        self._progress_card.setVisible(False)
        has_text = bool(self._report_lines) or bool(isinstance(report, dict) and report.get("text"))
        self._report_btn.setEnabled(has_text)
        self._set_support_footer_available(True)
        if isinstance(report, dict) and report.get("failed"):
            # Падение — не «остановлено»: пользователь ничего не нажимал.
            failed_text = "Проверка завершилась с ошибкой"
            self._summary_panel.set_stopped(failed_text)
            self._set_status_text(failed_text)
            self._set_support_status(
                "Подготовьте обращение: в нём будет журнал с текстом ошибки"
            )
            return
        if not isinstance(report, dict):
            self._summary_panel.set_stopped()
            self._set_status_text(tr_catalog("page.blockcheck.cancelled", default="Проверка остановлена"))
            self._set_support_status(
                tr_catalog(
                    "page.blockcheck.support_ready_after_cancel",
                    default="Можно подготовить обращение по тому, что успели проверить",
                )
            )
            return
        self._last_report = report
        if "history" in report:
            self._show_history(report["history"])
        # Пока на экране пример экскурсии, итог ждёт: он встанет на место, когда пример уберут.
        if not self._tour_demo_shown():
            self._summary_panel.show_report(report)
            self._results_card.setVisible(True)
            # Карточки выплывают по очереди следом за итогом.
            self._result_cards.show_report(report)
        elapsed = float(report.get("elapsed") or 0.0)
        self._set_status_text(
            tr_catalog("page.blockcheck.done", default="Готово") + f" за {elapsed:.0f} с — итог ниже"
        )
        self._set_support_status("")

    def _open_card_detail(self, card) -> None:
        """Нажатие на карточку: подробности этой проверки занимают всю страницу."""
        if self._detail_view is None:
            self._detail_view = ResultDetailView(self.content)
            self._detail_view.closed.connect(self._close_card_detail)
            self._detail_view.root_requested.connect(self._close_card_detail_to_tab)
            self._detail_view.text_opened.connect(self._open_section_text)
            self._detail_view.level_changed.connect(self._on_detail_level_changed)
            self._detail_view.setVisible(False)
            self.add_widget(self._detail_view)
        # Отчёт открыт из прошлой проверки: «назад» ведёт в неё же, к тому же месту.
        past = next(
            (view for view in (self._past_check_view, self._past_cards_view) if view is not None and not view.isHidden()), None
        )
        self._detail_past_view = past
        self._detail_from_past = past is not None
        if self._detail_from_past:
            self._past_return_scroll = self.verticalScrollBar().value()
        # Экран, из которого карточку открыли (прошлая проверка), — до смены экрана.
        past_screen = self._over_tabs_screen if self._detail_from_past else None
        self._show_over_tabs(self._detail_view, remember_scroll=not self._detail_from_past)
        self._detail_view.show_card(card, parent_title=past.title() if self._detail_from_past else "")
        self._detail_view.setFocus()
        # Карточка прошлой проверки: её отчёт не меняется, а «назад» из неё — в ту проверку.
        self._card_screen_origin = (past_screen, card)
        self._note_card_screen()

    def _note_card_screen(self, *, returned: bool = False) -> None:
        """Экран отчёта карточки вместе с шагами вглубь: «карточка>сервер»."""
        past_screen, card = self._card_screen_origin
        path = _CARD_LEVEL_SEPARATOR.join(self._detail_view.path_keys())
        title = (self._detail_view.card() or card).title
        if past_screen is not None:
            self._note_over_tabs_screen("pastcard", f"{past_screen.key}/{path}", title, (past_screen, card), returned=returned)
        else:
            self._note_over_tabs_screen("card", path, title, card, returned=returned)

    def _reopen_card_levels(self, levels) -> bool:
        """Повторяет записанные шаги вглубь отчёта карточки; False — такого уровня уже нет."""
        if not levels:
            return True
        if not self._detail_view.open_path(levels):
            return False
        self._note_card_screen()
        return True

    def _on_detail_level_changed(self, deeper: bool) -> None:
        """Шаг вглубь отчёта — новый шаг журнала, возврат к уровню выше — шаг «назад»."""
        if self._detail_view is None or self._detail_view.isHidden():
            return
        self._note_card_screen(returned=not deeper)

    def _open_card_by_key(self, key: str) -> None:
        """Нажатие на строку итога: открывает полный отчёт той же карточки, что стоит ниже."""
        widget = self._result_cards.card(key)
        if widget is not None:
            self._open_card_detail(widget.card)

    def _close_subpage(self) -> None:
        """Esc: назад из подробностей карточки, DNS-сервера или отчёта; на вкладке —
        тот же шаг «назад» по журналу, что и на любой странице."""
        if self._over_tabs_screen is None and not self._any_over_tabs_view_open():
            # В поле ввода Esc — клавиша поля, а не команда «назад».
            if not self._typing_in_text_field():
                self.navigation_step_back()
            return
        if self._detail_view is not None and not self._detail_view.isHidden():
            # Сначала шаг назад внутри отчёта (со страницы сервера — к списку хостингов).
            if not self._detail_view.go_back():
                self._close_card_detail()
        else:
            self._close_over_tabs()

    def _set_page_header_visible(self, visible: bool) -> None:
        """На подстранице первой идёт строка пути: название и описание раздела там лишние."""
        # Заголовок стоит в ряду со ссылкой на инструкцию: прячется весь ряд.
        for widget in (self._title_header or self.title_label, self.subtitle_label):
            if widget is not None:
                widget.setVisible(visible)

    def _close_card_detail(self) -> None:
        """Строка пути или Esc из карточки: туда, откуда её открыли, — шагом «наверх»."""
        if self._detail_view is None or self._detail_view.isHidden():
            return
        self.navigation_step_up(None, self._return_from_card_detail)

    def _return_from_card_detail(self) -> None:
        if self._detail_from_past and self._detail_past_view is not None:
            self._detail_from_past = False
            past_screen = self._detail_past_screen()
            self._show_over_tabs(self._detail_past_view, remember_scroll=False)
            self._detail_past_view.setFocus()
            QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(self._past_return_scroll))
            if past_screen is not None:
                self._note_over_tabs_screen(*past_screen)
            return
        self._return_to_tab()

    def _detail_past_screen(self):
        """Прошлая проверка, из которой открыта карточка: (вид, имя, заголовок, данные)."""
        screen = self._over_tabs_screen
        if screen is None or not screen.key.startswith("pastcard:"):
            return None
        past, _card = screen.payload[1]
        kind, _, name = past.key.partition(":")
        return kind, name, past.title, past.payload[1]

    def _close_card_detail_to_tab(self) -> None:
        """Корень строки пути из карточки: прямо на вкладку."""
        if self._detail_view is None or self._detail_view.isHidden():
            return
        tab_key = f"tab:{self.TAB_ORDER[self._active_tab_index]}"
        self.navigation_step_up(tab_key, self._return_to_tab)

    def _return_to_tab(self) -> None:
        self._detail_from_past = False
        self._switch_tab(self._active_tab_index)
        # Возвращаем к той карточке, с которой уходили: список длинный, искать её заново незачем.
        QTimer.singleShot(0, self._restore_over_tabs_scroll)

    def _show_over_tabs(self, view: QWidget, *, remember_scroll: bool = True) -> None:
        """Страница подробностей занимает место вкладок; назад ведёт её строка пути.

        ``remember_scroll`` выключают при переходе между подстраницами: место
        прокрутки вкладки уже запомнено и затирать его нельзя.
        """
        if remember_scroll:
            self._over_tabs_return_scroll = self.verticalScrollBar().value()
        for widget in self._tab_widgets:
            widget.setVisible(False)
        for page in (
            self._strategy_tab_page,
            self._domain_lookup_tab_page,
            self._dns_servers_tab_page,
            self._dns_spoofing_tab_page,
            self._detail_view,
            self._server_detail_view,
            self._log_report_view,
            self._past_check_view,
            self._past_cards_view,
        ):
            if page is not None and page is not view:
                page.setVisible(False)
        self._tabs_pivot.setVisible(False)
        self._set_page_header_visible(False)
        view.setVisible(True)
        self._scroll_to_top()

    def _any_over_tabs_view_open(self) -> bool:
        views = (self._detail_view, self._server_detail_view, self._log_report_view, self._past_check_view, self._past_cards_view)
        return any(view is not None and not view.isHidden() for view in views)

    def _close_over_tabs(self) -> None:
        """Строка пути или Esc из подробностей сервера, отчёта, прошлой проверки:
        туда, откуда пришли, — шагом «наверх», а не новым шагом вперёд."""
        views = (self._server_detail_view, self._log_report_view, self._past_check_view, self._past_cards_view)
        if all(view is None or view.isHidden() for view in views):
            return
        self.navigation_step_up(None, self._return_to_over_tabs_origin)

    def _return_to_over_tabs_origin(self) -> None:
        self._switch_tab(self._active_tab_index)
        # Возвращаем туда же, откуда уходили; вкладка к этому мигу ещё раскладывается.
        QTimer.singleShot(0, self._restore_over_tabs_scroll)

    # ------------------------------------------------------------------
    # Журнал экранов окна: кнопки «назад» и «вперёд»
    # ------------------------------------------------------------------
    def _note_over_tabs_screen(self, kind: str, name: str, title: str, data: object, *, returned: bool = False) -> None:
        """Запоминает экран поверх вкладок вместе с вкладкой, с которой его открыли.

        ``returned`` — человек вернулся сюда с экрана глубже (Esc в отчёте карточки)."""
        tab_key = self.TAB_ORDER[self._active_tab_index]
        self._over_tabs_screen = ScreenState(key=f"{kind}:{name}", title=str(title or ""), payload=(tab_key, data))
        (self.navigation_screen_returned if returned else self.navigation_screen_changed).emit()

    def navigation_screen(self) -> ScreenState:
        if self._over_tabs_screen is not None:
            return self._over_tabs_screen
        # Вкладку могли заказать до того, как страница достроилась: она и есть текущий экран.
        tab_key = self._pending_tab_key or self.TAB_ORDER[self._active_tab_index]
        title = ""
        if tab_key != self.TAB_BLOCKCHECK and self._tabs_pivot is not None:
            item = self._tabs_pivot.items.get(tab_key)
            title = item.text() if item is not None else ""
        return ScreenState(key=f"tab:{tab_key}", title=title)

    def navigation_screen_title(self, screen: ScreenState) -> str:
        """Подпись вкладки — на текущем языке программы; у остальных экранов — записанная."""
        kind, _, name = screen.key.partition(":")
        if kind != "tab" or name == self.TAB_BLOCKCHECK or self._tabs_pivot is None:
            return ""
        item = self._tabs_pivot.items.get(name)
        return item.text() if item is not None else ""

    def restore_navigation_screen(self, screen: ScreenState) -> bool:
        kind, _, name = screen.key.partition(":")
        if kind == "tab":
            if name not in self.TAB_ORDER:
                return False
            self.switch_to_tab(name)
            return True
        payload = screen.payload
        if not isinstance(payload, tuple) or len(payload) != 2:
            return False
        tab_key, data = payload
        if tab_key not in self.TAB_ORDER or not self.is_page_ready():
            return False
        opener = self._screen_opener(kind, name, data)
        if opener is None:
            return False
        self._switch_tab(self.TAB_ORDER.index(tab_key))
        return opener() is not False

    def _screen_opener(self, kind: str, name: str, data):
        """Как заново открыть экран поверх вкладок; None — его больше нет.

        Карточка текущей проверки берётся из текущего отчёта по ключу: после нового
        запуска «назад» показывает свежие данные, а пропавшая карточка честно
        выпадает из журнала. Прошлые проверки не меняются — их данные открываются
        как есть."""
        if kind == "card":
            root, *levels = name.split(_CARD_LEVEL_SEPARATOR)
            widget = self._result_cards.card(root) if self._result_cards is not None else None
            if widget is None:
                return None

            def reopen_card():
                self._open_card_detail(widget.card)
                return self._reopen_card_levels(levels)

            return reopen_card
        if kind == "tabreport":
            report = self._tab_live_report(name)
            return None if report is None else (lambda: self._open_log_report(report))
        if kind == "server":
            details, live = data
            if live:
                # Подробности сервера текущей проверки — по её свежим данным.
                details = self._dns_servers_tab_page.live_details(name) if self._dns_servers_tab_page is not None else None
            return None if details is None else (lambda: self._open_server_detail(details))
        if kind == "report":
            return lambda: self._open_log_report(data)
        if kind == "past":
            run, report = data
            return lambda: self._show_past_check(run, report)
        if kind == "pastcards":
            mode, title, headline, content, text = data
            if mode == "cards":
                return lambda: self._open_past_cards(title, headline, content, text)
            return lambda: self._open_past_content(title, headline, content, text)
        if kind == "pastcard":
            past_screen, card = data
            past_kind, _, past_name = past_screen.key.partition(":")
            past_tab, past_data = past_screen.payload
            reopen_past = self._screen_opener(past_kind, past_name, past_data)
            if reopen_past is None:
                return None
            _root, *levels = name[len(past_screen.key) + 1:].split(_CARD_LEVEL_SEPARATOR)

            def reopen():
                reopen_past()
                self._open_card_detail(card)
                return self._reopen_card_levels(levels)

            return reopen
        return None

    def _restore_over_tabs_scroll(self) -> None:
        self.verticalScrollBar().setValue(self._over_tabs_return_scroll)

    def _open_server_detail(self, details) -> None:
        """Нажатие на карточку DNS-сервера: его подробности занимают всю страницу."""
        if self._server_detail_view is None:
            from dns.ui.server_check_details import ServerDetailView

            self._server_detail_view = ServerDetailView(self.content)
            self._server_detail_view.closed.connect(self._close_over_tabs)
            self._server_detail_view.setVisible(False)
            self.add_widget(self._server_detail_view)
        # С вкладки — сервер текущей проверки; из прошлой проверки — её неизменный снимок.
        live = not self._any_over_tabs_view_open()
        self._show_over_tabs(self._server_detail_view)
        self._server_detail_view.show_details(details)
        self._server_detail_view.setFocus()
        server = details.card.server
        self._note_over_tabs_screen("server", server, server, (details, live))

    def _open_log_report(self, report) -> None:
        """«Отчёт» и «Лог» любой вкладки: текст открывается страницей-редактором с подсветкой."""
        if self._log_report_view is None:
            from ui.widgets.log_report_view import LogReportView

            self._log_report_view = LogReportView(self.content)
            self._log_report_view.closed.connect(self._close_over_tabs)
            self._log_report_view.setVisible(False)
            self.add_widget(self._log_report_view)
        # Строка пути ведёт на вкладку, с которой отчёт открыли, — под её названием на языке программы.
        tab_key = self.TAB_ORDER[self._active_tab_index]
        tab_item = self._tabs_pivot.items.get(tab_key)
        if tab_item is not None and tab_item.text():
            report = replace(report, root_title=tab_item.text())
        # Отчёт, открытый прямо с вкладки, — её текущий отчёт: из журнала он строится
        # заново по свежим данным. Отчёт прошлой проверки или раздела карточки не меняется.
        from_tab = not self._any_over_tabs_view_open()
        self._show_over_tabs(self._log_report_view)
        self._log_report_view.show_report(report)
        self._log_report_view.editor.setFocus()
        if from_tab:
            self._note_over_tabs_screen("tabreport", tab_key, report.title, None)
        else:
            self._note_over_tabs_screen("report", report.title, report.title, report)

    def _scroll_to_top(self) -> None:
        try:
            self.verticalScrollBar().setValue(0)
        except Exception:
            pass

    def _open_report(self) -> None:
        self._open_log_report(self.live_report())

    def live_report(self) -> LogReport:
        """Подробный отчёт текущей проверки BlockCheck."""
        return LogReport(
            title="Подробный отчёт BlockCheck",
            text=self._report_text(),
            root_title="BlockCheck",
            empty_text="Проверка ещё не запускалась.",
            description="Технические подробности проверки: адреса, ответы DNS и время ответа серверов.",
        )

    def _tab_live_report(self, tab_key: str):
        """Текущий отчёт вкладки (кнопка «Отчёт»/«Лог» на ней); None — отчёта пока нет."""
        if tab_key == self.TAB_BLOCKCHECK:
            return self.live_report()
        page = {
            self.TAB_STRATEGY_SCAN: self._strategy_tab_page,
            self.TAB_DOMAIN_LOOKUP: self._domain_lookup_tab_page,
            self.TAB_DNS_SERVERS: self._dns_servers_tab_page,
            self.TAB_DNS_SPOOFING: self._dns_spoofing_tab_page,
        }.get(tab_key)
        live = getattr(page, "live_report", None)
        return live() if callable(live) else None

    def _report_text(self) -> str:
        """Текст отчёта: из самого отчёта (он же лежит в файле проверки), иначе — что успели записать по ходу."""
        lines = (self._last_report or {}).get("text")
        if isinstance(lines, (list, tuple)) and lines:
            return "\n".join(str(line) for line in lines)
        return "\n".join(self._report_lines)

    def _open_past_cards(self, title: str, headline: str, cards, text: str) -> None:
        """Прошлая проверка вкладки («Проверка домена»): страница с её карточками, назад — по строке пути."""
        if self._past_cards_view is None:
            from blockcheck.ui.past_cards_view import PastCardsView

            self._past_cards_view = PastCardsView(self.content)
            self._past_cards_view.closed.connect(self._close_over_tabs)
            self._past_cards_view.card_opened.connect(self._open_card_detail)
            self._past_cards_view.text_opened.connect(self._open_section_text)
            self._past_cards_view.setVisible(False)
            self.add_widget(self._past_cards_view)
        tab_item = self._tabs_pivot.items.get(self.TAB_ORDER[self._active_tab_index])
        root = tab_item.text() if tab_item is not None and tab_item.text() else "BlockCheck"
        self._show_over_tabs(self._past_cards_view)
        self._past_cards_view.show_run(title, headline, list(cards), text, root_title=root)
        self._past_cards_view.setFocus()
        # Прошлая проверка не меняется: её данные можно открыть из журнала как есть.
        self._note_over_tabs_screen("pastcards", title, title, ("cards", title, headline, tuple(cards), text))

    def _open_past_content(self, title: str, headline: str, widget, text: str) -> None:
        """Прошлая проверка «DNS подмены» или «DNS-серверов»: тот же вид, что у вкладки, по сохранённому отчёту."""
        self._open_past_cards(title, headline, [], text)
        tab_item = self._tabs_pivot.items.get(self.TAB_ORDER[self._active_tab_index])
        root = tab_item.text() if tab_item is not None and tab_item.text() else "BlockCheck"
        self._past_cards_view.show_content(title, headline, widget, text, root_title=root)
        self._note_over_tabs_screen("pastcards", title, title, ("content", title, headline, widget, text))

    def _ensure_past_check_view(self):
        if self._past_check_view is None:
            from blockcheck.ui.past_check_view import PastCheckView

            self._past_check_view = PastCheckView(on_action=self._on_problem_action, parent=self.content)
            self._past_check_view.closed.connect(self._close_over_tabs)
            self._past_check_view.card_opened.connect(self._open_card_detail)
            self._past_check_view.text_opened.connect(self._open_section_text)
            self._past_check_view.setVisible(False)
            self.add_widget(self._past_check_view)
        return self._past_check_view

    def _open_past_check(self, run: dict) -> None:
        """Нажатие на строку «Прошлых проверок»: та проверка целиком, назад ведёт строка пути.

        Отчёт каждой проверки сохранён рядом с её журналом; читает его функция BlockCheck
        в фоновом потоке, а не страница в потоке окна.
        """
        self._past_report_lane.request(dict(run))

    def _show_past_check(self, run: dict, report) -> None:
        if self._cleanup_in_progress:
            return
        view = self._ensure_past_check_view()
        self._show_over_tabs(view)
        view.show_run(run, report)
        view.setFocus()
        name = str(run.get("log_file") or run.get("started_at") or "")
        self._note_over_tabs_screen("past", name, view.title(), (run, report))

    # ------------------------------------------------------------------
    # Обучающая экскурсия
    # ------------------------------------------------------------------
    def _tour_demo_shown(self) -> bool:
        guide = self._tour_guide
        return guide is not None and guide.report_demo_shown()

    def _ensure_tour_guide(self):
        if self._tour_guide is None:
            from blockcheck.ui.onboarding_guide import BlockcheckTourGuide

            self._tour_guide = BlockcheckTourGuide(self)
        return self._tour_guide

    def onboarding_target(self, name: str):
        return self._ensure_tour_guide().target(name)

    def onboarding_set_state(self, state: str | None) -> None:
        """Экскурсия открывает нужную вкладку и рисует пример проверки (см. blockcheck.ui.onboarding_guide)."""
        if state is None and self._tour_guide is None:
            return
        self._ensure_tour_guide().set_state(state)

    def _show_last_report(self) -> None:
        """Итог и карточки последней проверки этого запуска; без неё страница пустая."""
        report = self._last_report
        if report is None:
            self._summary_panel.set_idle()
            self._result_cards.clear()
            return
        self._summary_panel.show_report(report)
        self._result_cards.show_report(report, animate=False)

    def _open_section_text(self, title: str, text: str) -> None:
        """«Открыть на всю страницу» у длинного текста в отчёте карточки."""
        self._open_log_report(LogReport(title=title, text=text, root_title="BlockCheck"))

    def _on_problem_action(self, action: str, target: str) -> None:
        """Кнопки у проблем в итоге: подбор стратегии с нужной целью, запуск
        Zapret, настройка DNS или редактор hosts."""
        if action == "start_zapret":
            if self._start_zapret is not None:
                # Кнопка называется «Запустить» — запускаем, а не отправляем искать кнопку дальше.
                started = bool(self._start_zapret())
                show = InfoBarHelper.success if started else InfoBarHelper.warning
                show(
                    self.window(),
                    "Zapret запускается" if started else "Zapret не запустился",
                    "Когда он заработает, нажмите «Проверить» ещё раз — увидите, помог ли он."
                    if started
                    else "Откройте главную страницу: там видно, что мешает запуску.",
                )
                return
            from ui.workflows.mode import show_active_mode_control_page

            show_active_mode_control_page(self.window(), allow_internal=False)
            return
        if action == "dns":
            from app.page_names import PageName
            from ui.window_adapter import show_page

            show_page(self.window(), PageName.NETWORK)
            return
        if action == "hosts":
            from app.page_names import PageName
            from ui.window_adapter import show_page

            show_page(self.window(), PageName.HOSTS)
            return
        if action == "telegram_proxy":
            from app.page_names import PageName
            from ui.window_adapter import show_page

            show_page(self.window(), PageName.TELEGRAM_PROXY)
            return
        if action not in ("strategy", "strategy_voice"):
            return
        self.switch_to_tab(self.TAB_STRATEGY_SCAN)
        page = self._strategy_tab_page
        prefill = getattr(page, "prefill_target", None)
        if callable(prefill):
            prefill(target, protocol=PROTOCOL_STUN_VOICE if action == "strategy_voice" else PROTOCOL_TCP_HTTPS)

    def _reset_ui(self):
        reset_blockcheck_running_ui(
            start_button=self._start_btn,
            stop_button=self._stop_btn,
            scope_combo=self._scope_combo,
            progress_bar=self._progress_bar,
        )
        self._outside_box.setEnabled(True)
        self._domains_hidden_for_run = False
        self._sync_domains_area()
        # Между проверками парного запуска запускать нечего: Zapret переключается.
        busy = self._paired is not None and self._paired.active
        self._paired_btn.setEnabled(self._paired is not None and not busy)
        if busy:
            self._start_btn.setEnabled(False)

    def _update_scope_combo_accessibility(self, *_args) -> None:
        text = str(self._scope_combo.currentText() or "").strip() or "не выбрано"
        state_text = f"Что проверить BlockCheck, выбрано: {text}"
        set_state_text(self._scope_combo, state_text)
        set_control_accessibility(
            self._scope_combo,
            name=state_text,
            description="«Discord и YouTube» — только они; «Все сайты» — ещё мессенджеры, соцсети и ваши домены. Звонки и обрыв на 16 КБ проверяются всегда.",
        )
        set_combo_items_accessibility(self._scope_combo, name="Что проверить BlockCheck")

    def _on_run_log_started(self, run_log_file) -> None:
        if self._cleanup_in_progress:
            return
        self._run_log_file = run_log_file

    def _on_stop(self):
        if self._paired is not None and self._paired.phase == paired_run.PHASE_SWITCHING:
            self._paired_end("Парная проверка остановлена")
            return
        if self._run_runtime.is_queued():
            # Проверка ещё ждёт очереди фоновых задач: снимаем её из очереди,
            # иначе она запустилась бы уже после «Стопа».
            self._run_runtime.stop()
            self._on_finished(None)
            return
        request_blockcheck_stop(
            worker=self._run_runtime.worker,
            stop_button=self._stop_btn,
            status_label=self._status_label,
            force_stop=self._force_stop,
            tr_fn=tr_catalog,
        )
        self._set_status_text(self._status_label.text())

    def _force_stop(self, expected_worker=None):
        if expected_worker is None:
            return
        current_worker = self._run_runtime.worker
        if current_worker is expected_worker and current_worker.is_running:
            warning_text = tr_catalog(
                "page.blockcheck.stopping_slow",
                default="Остановка занимает больше времени, ждём завершения фоновой проверки...",
            )
            self._set_status_text(warning_text)
            self._set_support_status(
                tr_catalog(
                    "page.blockcheck.support_wait_stop",
                    default="Подождите завершения остановки перед новым запуском",
                )
            )

    def _set_support_footer_available(self, available: bool) -> None:
        self._support_footer_available = bool(available)
        self._footer_card.setVisible(
            self._support_footer_available
            and self.TAB_ORDER[self._active_tab_index] == self.TAB_BLOCKCHECK
        )

    def _set_status_text(self, text: str) -> None:
        value = str(text or "").strip()
        self._status_label.setText(value)
        set_state_text(self._status_label, f"Статус BlockCheck: {value}")
        # До первой проверки строка повторяла бы выпадающий список: её нет, пока нечего сказать.
        idle = value == tr_catalog("page.blockcheck.ready", default="Сайты, хостинги и звонки — так, как их открыл бы браузер")
        self._status_label.setVisible(bool(value) and not idle)

    def _set_support_status(self, text: str) -> None:
        if self._support_status_label is None:
            return
        value = str(text or "").strip()
        self._support_status_label.setText(value)
        if value:
            set_state_text(self._support_status_label, f"Статус обращения BlockCheck: {value}")

    def _prepare_support_from_blockcheck(self) -> None:
        mode_label = self._scope_combo.currentText() if self._scope_combo is not None else "BlockCheck"
        extra_domains = self._get_extra_domains()
        self._request_support_prepare(
            run_log_file=self._run_log_file,
            mode_label=mode_label,
            extra_domains=extra_domains,
        )

    def _request_support_prepare(
        self,
        *,
        run_log_file: str | None,
        mode_label: str,
        extra_domains: list[str],
    ) -> None:
        payload = {
            "run_log_file": run_log_file,
            "mode_label": str(mode_label or ""),
            "extra_domains": list(extra_domains or []),
        }
        state = self._support_prepare_state_obj()
        if state.is_busy():
            state.pending = dict(payload)
            self._set_support_status("Подготовка уже идёт...")
            return

        state.pending = None
        self._set_support_status("Подготовка обращения...")
        if self._prepare_support_btn is not None:
            self._prepare_support_btn.setEnabled(False)
        self._start_support_prepare_worker(payload)

    def _start_support_prepare_worker(self, payload: dict) -> None:
        def worker_factory(request_id: int):
            return self.create_support_prepare_worker(
                request_id,
                run_log_file=payload.get("run_log_file"),
                mode_label=str(payload.get("mode_label") or ""),
                extra_domains=list(payload.get("extra_domains") or []),
            )

        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_support_prepare_finished)
            worker.failed.connect(self._on_support_prepare_failed)

        self._support_prepare_runtime.start_qthread_worker(
            worker_factory=worker_factory,
            bind_worker=bind_worker,
            on_finished=self._on_support_prepare_runtime_finished,
        )

    def _on_support_prepare_finished(self, request_id: int, feedback) -> None:
        if not self._support_prepare_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        if self._support_prepare_state_obj().has_pending():
            return
        result = feedback.result
        archive_paths = list(getattr(result, "archive_paths", None) or ([result.zip_path] if result.zip_path else []))
        if archive_paths:
            logger.info("Prepared BlockCheck support archive(s): %s", ", ".join(archive_paths))

        self._set_support_status(feedback.status_text)

        try:
            InfoBarHelper.success(
                self.window(),
                tr_catalog(
                    "page.blockcheck.support_prepared_title",
                    default="Обращение подготовлено",
                ),
                feedback.info_text,
            )
        except Exception:
            pass

    def _on_support_prepare_failed(self, request_id: int, error: str) -> None:
        if not self._support_prepare_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        if self._support_prepare_state_obj().has_pending():
            return
        logger.warning("Failed to prepare BlockCheck support bundle: %s", error)
        self._set_support_status("Ошибка подготовки")
        try:
            InfoBarHelper.warning(
                self.window(),
                tr_catalog("page.blockcheck.error", default="Ошибка выполнения"),
                f"Не удалось подготовить обращение:\n{error}",
            )
        except Exception:
            pass

    def _on_support_prepare_runtime_finished(self, _worker) -> None:
        if not self._is_current_worker_finish(self.__dict__.get("_support_prepare_runtime"), _worker):
            return
        state = self._support_prepare_state_obj()
        had_pending = state.has_pending()
        state.schedule_pending_after_finish(
            _worker,
            is_current_worker_finish=self._is_current_worker_finish,
            single_shot=QTimer.singleShot,
            run_scheduled=self._run_scheduled_support_prepare_worker_start,
            cleanup_in_progress=self._cleanup_in_progress,
        )
        if had_pending and state.start_scheduled:
            return
        if self._prepare_support_btn is not None and not self._cleanup_in_progress:
            self._prepare_support_btn.setEnabled(True)

    def _schedule_support_prepare_worker_start(self, payload: dict) -> None:
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        state = self._support_prepare_state_obj()
        state.pending = dict(payload or {})
        state.schedule_start(
            QTimer.singleShot,
            self._run_scheduled_support_prepare_worker_start,
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False),
            pending_when_already_scheduled=dict(payload or {}),
        )

    def _run_scheduled_support_prepare_worker_start(self) -> None:
        pending = self._support_prepare_state_obj().take_pending_for_scheduled_start(
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False)
        )
        if pending is None or self.__dict__.get("_cleanup_in_progress", False):
            return
        self._set_support_status("Подготовка обращения...")
        if self._prepare_support_btn is not None:
            self._prepare_support_btn.setEnabled(False)
        self._start_support_prepare_worker(dict(pending or {}))

    # ------------------------------------------------------------------
    # Custom domains
    # ------------------------------------------------------------------

    def _apply_initial_domain_chips(self, domains: tuple[str, ...]) -> None:
        """Create chips from a backend-prepared domain list."""
        started_at = time.perf_counter()
        existing = set(self._get_extra_domains())
        for domain in tuple(domains or ()):
            if domain in existing:
                continue
            self._add_chip(domain)
            existing.add(domain)
        self._log_ui_timing("blockcheck_ui.domain_chips.apply.total", started_at)

    @staticmethod
    def _log_ui_timing(label: str, started_at: float) -> None:
        log_ui_timing_since("ui", "blockcheck", label, started_at)

    def _on_add_domain(self):
        """Add a domain from the input field."""
        text = self._domain_input.text().strip()
        if not text:
            return
        self._request_user_domain_action("add", text)

    def _on_remove_domain(self, domain: str):
        """Remove a domain chip and delete from persistence."""
        self._request_user_domain_action("remove", domain)

    def _request_user_domain_action(self, action: str, domain: str) -> None:
        payload = {
            "action": str(action or "").strip().lower(),
            "domain": str(domain or "").strip(),
        }
        if not payload["action"] or not payload["domain"]:
            return
        if (
            self._user_domain_action_state_obj().is_busy()
        ):
            self._queue_user_domain_action(payload)
            return
        self._start_user_domain_action_worker(payload)

    def _queue_user_domain_action(self, payload: dict[str, str]) -> None:
        queued = {
            "action": str((payload or {}).get("action") or "").strip().lower(),
            "domain": str((payload or {}).get("domain") or "").strip(),
        }
        domain = queued["domain"]
        pending = self._user_domain_action_state_obj().pending
        if domain:
            pending[:] = [
                item
                for item in pending
                if str(item.get("domain") or "").strip() != domain
            ]
        pending.append(queued)

    def _start_user_domain_action_worker(self, payload: dict[str, str]) -> None:
        def worker_factory(request_id: int):
            return self.create_user_domain_action_worker(
                request_id,
                action=str(payload.get("action") or ""),
                domain=str(payload.get("domain") or ""),
            )

        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_user_domain_action_finished)
            worker.failed.connect(self._on_user_domain_action_failed)

        self._user_domain_action_runtime.start_qthread_worker(
            worker_factory=worker_factory,
            bind_worker=bind_worker,
            on_finished=self._on_user_domain_action_runtime_finished,
        )

    def _on_user_domain_action_finished(self, request_id: int, action: str, result, context) -> None:
        if not self._user_domain_action_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        context = dict(context or {})
        if self._has_pending_user_domain_action(str(context.get("domain") or result or "")):
            return
        if action == "add":
            text = str(context.get("domain") or "")
            if isinstance(result, blockcheck_page_runtime.UserDomainRejection):
                try:
                    InfoBarHelper.warning(
                        self.window(),
                        tr_catalog(
                            "page.blockcheck.domain_googlevideo_title",
                            default="googlevideo.com добавлять не нужно",
                        ),
                        tr_catalog(
                            "page.blockcheck.domain_googlevideo_text",
                            default=(
                                "Голый googlevideo.com — не видеосервер, его проверка "
                                "всегда даёт ошибку. BlockCheck сам находит и проверяет "
                                "актуальный видеосервер rr*.googlevideo.com при каждом "
                                "запуске."
                            ),
                        ),
                        duration=10000,
                    )
                except Exception:
                    pass
                self._domain_input.clear()
                return
            normalized = str(result or "").strip()
            if normalized:
                self._add_chip(normalized)
            else:
                try:
                    InfoBarHelper.warning(
                        self.window(),
                        tr_catalog("page.blockcheck.domain_exists_title", default="Домен уже добавлен"),
                        text,
                    )
                except Exception:
                    pass
            self._domain_input.clear()
            return
        if action == "remove":
            remove_domain_chip(
                domain=str(result or context.get("domain") or ""),
                flow_layout=self._domains_flow_layout,
                chip_cls=DomainChip,
            )
            self._sync_domains_flow_visibility()

    def _on_user_domain_action_failed(self, request_id: int, action: str, error: str, _context) -> None:
        if not self._user_domain_action_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        logger.warning("Failed to %s BlockCheck domain: %s", action, error)

    def _has_pending_user_domain_action(self, domain: str) -> bool:
        candidate = str(domain or "").strip()
        if not candidate:
            return False
        pending_actions = self._user_domain_action_state_obj().pending
        return any(
            str(item.get("domain") or "").strip() == candidate
            for item in pending_actions
        )

    def _on_user_domain_action_runtime_finished(self, _worker) -> None:
        if not self._is_current_worker_finish(self.__dict__.get("_user_domain_action_runtime"), _worker):
            return
        if self._user_domain_action_state_obj().has_pending() and not self._cleanup_in_progress:
            pending = self._user_domain_action_state_obj().pop_next()
            self._schedule_user_domain_action_worker_start(dict(pending or {}))

    def _is_current_worker_finish(self, runtime, worker) -> bool:
        if self.__dict__.get("_cleanup_in_progress", False):
            return False
        request_id = getattr(worker, "_request_id", None)
        if request_id is None:
            current_worker = getattr(runtime, "worker", None)
            if current_worker is not None:
                return worker is current_worker
            return True
        try:
            return int(request_id) == int(getattr(runtime, "request_id", -1))
        except (TypeError, ValueError):
            return False

    def _support_prepare_state_obj(self) -> LatestValueWorkerState:
        state = self.__dict__.get("_support_prepare_state")
        runtime = self.__dict__.get("_support_prepare_runtime")
        if state is None:
            pending = self.__dict__.pop("_support_prepare_pending", None)
            start_scheduled = bool(self.__dict__.pop("_support_prepare_start_scheduled", False))
            state = LatestValueWorkerState(
                runtime,
                empty_value=None,
                pending=pending,
                start_scheduled=start_scheduled,
            )
            self.__dict__["_support_prepare_state"] = state
        elif getattr(state, "runtime", None) is None and runtime is not None:
            state.runtime = runtime
        return state

    @property
    def _support_prepare_pending(self):
        return self._support_prepare_state_obj().pending

    @_support_prepare_pending.setter
    def _support_prepare_pending(self, value) -> None:
        self._support_prepare_state_obj().pending = value

    @property
    def _support_prepare_start_scheduled(self) -> bool:
        return bool(self._support_prepare_state_obj().start_scheduled)

    @_support_prepare_start_scheduled.setter
    def _support_prepare_start_scheduled(self, value: bool) -> None:
        self._support_prepare_state_obj().start_scheduled = bool(value)

    def _user_domain_action_state_obj(self) -> QueuedWorkerState:
        state = self.__dict__.get("_user_domain_action_state")
        runtime = self.__dict__.get("_user_domain_action_runtime")
        if state is None:
            pending = self.__dict__.pop("_user_domain_action_pending", [])
            start_scheduled = bool(
                self.__dict__.pop("_user_domain_action_start_scheduled", False)
            )
            state = QueuedWorkerState(
                runtime,
                pending=list(pending or []),
                start_scheduled=start_scheduled,
            )
            self.__dict__["_user_domain_action_state"] = state
        elif getattr(state, "runtime", None) is None and runtime is not None:
            state.runtime = runtime
        return state

    @property
    def _user_domain_action_pending(self) -> list[dict[str, str]]:
        return self._user_domain_action_state_obj().pending

    @_user_domain_action_pending.setter
    def _user_domain_action_pending(self, value) -> None:
        self._user_domain_action_state_obj().pending = list(value or [])

    @property
    def _user_domain_action_start_scheduled(self) -> bool:
        return bool(self._user_domain_action_state_obj().start_scheduled)

    @_user_domain_action_start_scheduled.setter
    def _user_domain_action_start_scheduled(self, value: bool) -> None:
        self._user_domain_action_state_obj().start_scheduled = bool(value)

    def _schedule_user_domain_action_worker_start(self, payload: dict[str, str]) -> None:
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        queued = {
            "action": str((payload or {}).get("action") or "").strip().lower(),
            "domain": str((payload or {}).get("domain") or "").strip(),
        }
        self._user_domain_action_state_obj().schedule_start(
            queued,
            QTimer.singleShot,
            self._run_scheduled_user_domain_action_worker_start,
            queue_item=self._queue_user_domain_action,
            is_cleanup_in_progress=lambda: bool(
                self.__dict__.get("_cleanup_in_progress", False)
            ),
        )

    def _run_scheduled_user_domain_action_worker_start(self, payload: dict[str, str]) -> None:
        self._user_domain_action_state_obj().start_scheduled = False
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        self._start_user_domain_action_worker(payload)

    def _add_chip(self, domain: str):
        """Add a chip widget for a domain."""
        add_domain_chip(
            domain=domain,
            flow_widget=self._domains_flow,
            flow_layout=self._domains_flow_layout,
            chip_cls=DomainChip,
            on_removed=self._on_remove_domain,
        )
        self._sync_domains_flow_visibility()

    def _sync_domains_flow_visibility(self) -> None:
        """Строка плашек доменов видна, только когда в ней что-то есть."""
        if self._domains_flow is not None:
            self._domains_flow.setVisible(bool(self._get_extra_domains()))
        self._sync_domains_area()

    def _sync_domains_area(self) -> None:
        """Поле своих доменов: открыто по ссылке или когда домены уже есть; на время проверки спрятано.

        Ссылка «+ Свой домен» видна, пока поля нет на экране.
        """
        card = self.__dict__.get("_domains_card")
        if card is None:
            return
        shown = not self._domains_hidden_for_run and (self._custom_domains_open or bool(self._get_extra_domains()))
        card.setVisible(shown)
        button = self.__dict__.get("_custom_domain_btn")
        if button is not None:
            button.setVisible(not shown and not self._domains_hidden_for_run)

    def _open_custom_domains(self) -> None:
        """«+ Свой домен»: открыть поле и поставить в него курсор."""
        self._custom_domains_open = True
        self._sync_domains_area()
        if self._domain_input is not None:
            self._domain_input.setFocus()

    def _get_extra_domains(self) -> list[str]:
        """Collect domains from chips to pass to worker."""
        return collect_extra_domains(
            flow_layout=self._domains_flow_layout,
            chip_cls=DomainChip,
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        self._cleanup_in_progress = True
        self._past_report_lane.close()
        self._initial_state_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="blockcheck initial state worker",
        )
        self._initial_state_runtime.cancel()
        self._support_prepare_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="blockcheck support prepare worker",
        )
        self._support_prepare_runtime.cancel()
        self._support_prepare_state_obj().reset()
        self._user_domain_action_state_obj().reset()
        self._user_domain_action_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="blockcheck user domain action worker",
        )
        self._user_domain_action_runtime.cancel()
        self._run_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="blockcheck run worker",
        )
        self._run_runtime.cancel()

        for page in (
            self._strategy_tab_page,
            self._domain_lookup_tab_page,
            self._dns_servers_tab_page,
            self._dns_spoofing_tab_page,
        ):
            if page is None:
                continue
            cleanup_handler = getattr(page, "cleanup", None)
            if callable(cleanup_handler):
                try:
                    cleanup_handler()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Language
    # ------------------------------------------------------------------

    def set_ui_language(self, language: str) -> None:
        super().set_ui_language(language)

        def _tr(key: str, default: str) -> str:
            return tr_catalog(key, language=language, default=default)

        try:
            if self._tabs_pivot is not None:
                self._tabs_pivot.setItemText(self.TAB_BLOCKCHECK, _tr("page.blockcheck.tab.blockcheck", "BlockCheck"))
                self._tabs_pivot.setItemText(
                    self.TAB_STRATEGY_SCAN, _tr("page.blockcheck.tab.strategy_scan", "Подбор стратегии")
                )
                self._tabs_pivot.setItemText(
                    self.TAB_DOMAIN_LOOKUP, _tr("page.blockcheck.tab.domain_lookup", "Проверка домена")
                )
                self._tabs_pivot.setItemText(self.TAB_DNS_SERVERS, _tr("page.blockcheck.tab.dns_servers", "DNS-серверы"))
                self._tabs_pivot.setItemText(self.TAB_DNS_SPOOFING, _tr("page.blockcheck.tab.dns_spoofing", "DNS подмена"))
            self._update_tabs_accessibility()
            # Карточки без шапок: set_title не вызывается, он добавил бы шапку обратно.
            if self._custom_domain_btn is not None:
                self._custom_domain_btn.setText(_tr("page.blockcheck.custom_domain.open", "Свой домен"))
            # Порядок тот же, что при создании списка: сайты первые, глубокая диагностика последняя.
            self._scope_combo.setItemText(0, _tr("page.blockcheck.scope_all", "Только сайты (быстро)"))
            self._scope_combo.setItemText(1, _tr("page.blockcheck.scope_main", "Только Discord и YouTube (быстро)"))
            self._scope_combo.setItemText(2, _tr("page.blockcheck.scope_full", "Глубокая диагностика (около минуты)"))
            self._update_scope_combo_accessibility()
            if self._domains_caption is not None:
                self._domains_caption.setText(_tr("page.blockcheck.custom_domains", "Проверить ещё и свои домены:"))
            self._start_btn.setText(_tr("page.blockcheck.start", "Проверить"))
            self._stop_btn.setText(_tr("page.blockcheck.stop", "Остановить"))
            set_tooltip(
                self._start_btn,
                _tr(
                    "page.blockcheck.action.start.description",
                    "Проверить, какие сайты открываются и что мешает остальным.",
                ),
            )
            set_tooltip(self._stop_btn, _tr("page.blockcheck.action.stop.description", "Остановить текущую проверку."))
            self._report_btn.setText(_tr("page.blockcheck.report", "Отчёт"))
            self._add_domain_btn.setText(_tr("page.blockcheck.add_domain", "Добавить"))
            self._domain_input.setPlaceholderText(_tr("page.blockcheck.domain_placeholder", "example.com"))
            if self._prepare_support_btn is not None:
                self._prepare_support_btn.setText(_tr("page.blockcheck.prepare_support", "Подготовить обращение"))
            if self._strategy_tab_page is not None:
                self._strategy_tab_page.set_ui_language(language)
            if self._domain_lookup_tab_page is not None:
                self._domain_lookup_tab_page.set_ui_language(language)
            if self._dns_servers_tab_page is not None:
                self._dns_servers_tab_page.set_ui_language(language)
            if self._dns_spoofing_tab_page is not None:
                self._dns_spoofing_tab_page.set_ui_language(language)
        except Exception:
            pass
