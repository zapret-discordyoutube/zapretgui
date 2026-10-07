"""Что страница BlockCheck показывает обучающей экскурсии.

Экскурсия называет «сцену» — что должно быть на экране на этом шаге: нужная
вкладка, пример отчёта, открытые подробности карточки. Проводник приводит
страницу к этой сцене, а когда экскурсия уходит — возвращает всё как было.

Примеры берутся из ``blockcheck.ui.onboarding_demo`` и рисуются теми же
виджетами, что и настоящая проверка. Они никуда не записываются.

Вид страницы возвращается не сразу, а в следующем обороте цикла событий:
соседние шаги экскурсии обычно идут по одной и той же сцене, и без этой
задержки пример пропадал бы и рисовался заново при каждом «Далее».
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, QTimer

from blockcheck.strategy_scan_page_plans import build_panel_outcome, build_result_presentation
from blockcheck.ui.onboarding_demo import demo_history, demo_report, demo_scan_report


TAB_BLOCKCHECK = "blockcheck"
TAB_STRATEGY_SCAN = "strategy_scan"
TAB_DOMAIN_LOOKUP = "domain_lookup"
TAB_DNS_SERVERS = "dns_servers"
TAB_DNS_SPOOFING = "dns_spoofing"

VIEW_CARD = "card"
VIEW_PAST_CHECK = "past_check"
# Карточка, чьи подробности экскурсия открывает как пример.
DEMO_CARD_KEY = "site:discord"


@dataclass(frozen=True, slots=True)
class _Scene:
    tab: str
    # Пример отчёта и прошлых проверок на вкладке BlockCheck.
    report: bool = False
    # Экран поверх вкладок: подробности карточки или прошлая проверка.
    view: str = ""
    # Пример итога на вкладке «Подбор стратегии».
    scan: bool = False


_SCENES: dict[str, _Scene] = {
    "main": _Scene(TAB_BLOCKCHECK),
    "report": _Scene(TAB_BLOCKCHECK, report=True),
    "card_detail": _Scene(TAB_BLOCKCHECK, report=True, view=VIEW_CARD),
    "past_check": _Scene(TAB_BLOCKCHECK, report=True, view=VIEW_PAST_CHECK),
    "strategy_scan": _Scene(TAB_STRATEGY_SCAN),
    "strategy_scan_result": _Scene(TAB_STRATEGY_SCAN, scan=True),
    "domain_lookup": _Scene(TAB_DOMAIN_LOOKUP),
    "dns_servers": _Scene(TAB_DNS_SERVERS),
    "dns_spoofing": _Scene(TAB_DNS_SPOOFING),
}

# Вкладка → (атрибут страницы с её содержимым, верхняя карточка этой вкладки).
_TAB_FIRST_CARD: dict[str, tuple[str, str]] = {
    TAB_DOMAIN_LOOKUP: ("_domain_lookup_tab_page", "control_card"),
    TAB_DNS_SERVERS: ("_dns_servers_tab_page", "verdict_panel"),
    TAB_DNS_SPOOFING: ("_dns_spoofing_tab_page", "control_card"),
}


class BlockcheckTourGuide(QObject):
    def __init__(self, page) -> None:
        super().__init__(page)
        self._page = page
        self._active = False
        self._home_tab = 0
        self._report: dict | None = None
        self._view = ""
        self._scan_shown = False
        self._restore_timer = QTimer(self)
        self._restore_timer.setSingleShot(True)
        self._restore_timer.setInterval(0)
        self._restore_timer.timeout.connect(self.restore)

    def report_demo_shown(self) -> bool:
        return self._report is not None

    # ── цели ─────────────────────────────────────────────────

    def target(self, name: str):
        page = self._page
        cards = page._result_cards
        scan = page._strategy_tab_page
        if name == "start":
            # Выбор набора и кнопка «Проверить»: они есть при любой раскладке верха страницы.
            return [page._scope_combo, page._start_btn]
        if name == "domains":
            return page._domains_card
        if name == "tabs":
            return page._tabs_pivot
        if name == "summary":
            return page._summary_panel
        if name == "cards":
            return [cards.counters, cards.sites_title, cards.sites_grid]
        if name == "checks":
            return [cards.checks_title, cards.checks_grid]
        if name == "history":
            return page._history_card
        if name == "footer":
            return page._footer_card
        if name == "card_detail":
            return page._detail_view
        if name == "past_check":
            view = page._past_check_view
            return [view.breadcrumb, view.summary] if view is not None else None
        if name == "scan_control":
            return scan._control_card if scan is not None else None
        if name == "scan_result":
            return [scan._scan_panel, scan._results_card] if scan is not None else None
        if name == "tab_page":
            page_attr, card_attr = _TAB_FIRST_CARD.get(page.TAB_ORDER[page._active_tab_index], ("", ""))
            tab_page = getattr(page, page_attr, None) if page_attr else None
            return [page._tabs_pivot, getattr(tab_page, card_attr, None)] if tab_page is not None else None
        return None

    # ── сцены ────────────────────────────────────────────────

    def set_state(self, state: str | None) -> None:
        scene = _SCENES.get(str(state or ""))
        if scene is None:
            if self._active:
                self._restore_timer.start()
            return
        self._restore_timer.stop()
        page = self._page
        tab_index = page.TAB_ORDER.index(scene.tab)
        entering = not self._active
        if entering:
            self._active = True
            self._home_tab = page._active_tab_index
        # Первый заход закрывает и то, что пользователь сам оставил открытым поверх вкладок.
        if entering or page._active_tab_index != tab_index or self._view != scene.view:
            self._view = ""
            page._switch_tab(tab_index)
        self._set_report_demo(scene.report)
        self._set_scan_demo(scene.scan)
        if scene.view and self._report is not None:
            self._open_view(scene.view)

    def restore(self) -> None:
        """Возвращает страницу к виду до экскурсии."""
        self._restore_timer.stop()
        if not self._active:
            return
        self._active = False
        self._view = ""
        page = self._page
        if page._cleanup_in_progress:
            return
        self._set_scan_demo(False)
        self._set_report_demo(False)
        page._switch_tab(self._home_tab)

    def _set_report_demo(self, shown: bool) -> None:
        page = self._page
        if shown == (self._report is not None):
            return
        if shown:
            # Идёт настоящая проверка: пример поверх неё не рисуем.
            if page._run_runtime.is_running():
                return
            self._report = demo_report()
            page._summary_panel.show_report(self._report)
            page._result_cards.show_report(self._report, animate=False)
            page._history_list.show_history(demo_history())
        else:
            self._report = None
            page._show_last_report()
            page._show_history(page._history_runs)
        # Какие карточки вкладки видны, решает страница: с примером — те же, что после проверки.
        page._switch_tab(page._active_tab_index)

    def _open_view(self, view: str) -> None:
        page = self._page
        if view == VIEW_CARD:
            widget = page._result_cards.card(DEMO_CARD_KEY)
            if widget is None:
                return
            page._open_card_detail(widget.card)
        elif view == VIEW_PAST_CHECK:
            past = page._ensure_past_check_view()
            page._show_over_tabs(past)
            past.show_run(demo_history()[-1], self._report)
        self._view = view

    def _set_scan_demo(self, shown: bool) -> None:
        scan = self._page._strategy_tab_page
        if scan is None or shown == self._scan_shown:
            return
        panel, results = scan._scan_panel, scan._results_view
        if shown:
            # Настоящий подбор идёт или уже закончился: на экране его итог, пример не нужен.
            if panel.state != "idle" or results.row_count():
                return
            report = demo_scan_report()
            rows = []
            for number, result in enumerate([*report.working_strategies, *report.failed_strategies], start=1):
                presentation = build_result_presentation(result, row_number=number)
                rows.append(presentation.stored_row)
                results.add_result(presentation, str(presentation.stored_row["verdict"]), _ignore_apply)
            outcome = build_panel_outcome(report, rows)
            panel.show_outcome(kind=outcome.kind, title=outcome.title, detail=outcome.detail, best_text=outcome.best_text)
            scan._results_card.setVisible(True)
        else:
            results.clear()
            scan._results_card.setVisible(False)
            panel.show_idle()
        self._scan_shown = shown


def _ignore_apply() -> None:
    """Кнопка «Применить» у примера видна, но ничего не делает."""


__all__ = ["BlockcheckTourGuide", "DEMO_CARD_KEY"]
