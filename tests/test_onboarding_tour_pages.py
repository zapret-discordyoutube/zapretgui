"""Экскурсия на настоящих страницах: цели шагов и примеры, которые она показывает."""

from __future__ import annotations

import inspect
import os
import sys
import unittest
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from app.page_names import PageName


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _BlockcheckFeature:
    """Настоящие чистые функции BlockCheck; всё остальное — заглушки."""

    def __init__(self) -> None:
        self.load_past_blockcheck_report = Mock(return_value=None)
        self.remember_blockcheck_run = Mock()

    def __getattr__(self, name):
        import blockcheck.public as public

        if hasattr(public, name):
            return getattr(public, name)
        value = MagicMock()
        setattr(self, name, value)
        return value


RUNS = [
    {
        "kind": "blockcheck",
        "time": "2026-10-01T10:00:00",
        "title": "Все сайты",
        "level": "ok",
        "headline": "Всё открывается",
        "problems": [],
        "states": {"YouTube": "ok"},
        "log_file": "C:/logs/run.log",
    }
]
REAL_REPORT = {
    "problems": [],
    "working": ["YouTube"],
    "services": [{"key": "youtube", "label": "YouTube", "level": "ok", "kind": "", "targets": []}],
}


class BlockcheckTourGuideTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        from blockcheck.ui.page import BlockcheckPage

        self.feature = _BlockcheckFeature()
        with patch.object(BlockcheckPage, "_request_page_initial_state_load", lambda self: None):
            self.page = BlockcheckPage(
                blockcheck_feature=self.feature,
                dns_feature=MagicMock(),
                create_strategy_scan_worker=lambda *_args, **_kwargs: None,
            )
        self.addCleanup(self.page.deleteLater)
        self.page.resize(1100, 900)
        self.page.show()

    def _titles(self) -> list[str]:
        return [card.card.title for card in self.page._result_cards.cards()]

    def test_empty_page_gets_an_example_and_is_empty_again_afterwards(self) -> None:
        page = self.page
        self.assertTrue(page._results_card.isHidden())
        self.assertTrue(page._history_card.isHidden())

        page.onboarding_set_state("report")

        self.assertEqual(page._summary_panel.title_label.text(), "Найдены проблемы: 3")
        self.assertIn("Discord", self._titles())
        for name in ("summary", "history", "footer"):
            self.assertFalse(page.onboarding_target(name).isHidden(), name)
        self.assertTrue(all(not widget.isHidden() for widget in page.onboarding_target("cards")))
        self.assertEqual(len(page._history_list.rows()), 4)

        page.onboarding_set_state(None)
        # Вид возвращается не сразу: следующий шаг экскурсии обычно на этой же странице.
        self.assertEqual(len(page._history_list.rows()), 4)
        QApplication.processEvents()

        self.assertEqual(page._summary_panel.title_label.text(), "Сеть ещё не проверялась")
        self.assertEqual(self._titles(), [])
        self.assertEqual(page._history_list.rows(), [])
        for card in (page._results_card, page._history_card, page._footer_card):
            self.assertTrue(card.isHidden())
        # Пример нигде не сохранялся и ничего не читал с диска.
        self.feature.remember_blockcheck_run.assert_not_called()
        self.feature.load_past_blockcheck_report.assert_not_called()

    def test_neighbouring_steps_keep_the_same_example_on_screen(self) -> None:
        page = self.page
        page.onboarding_set_state("report")
        first_cards = page._result_cards.cards()

        page.onboarding_set_state(None)
        page.onboarding_set_state("report")
        QApplication.processEvents()

        self.assertEqual(page._result_cards.cards(), first_cards)
        self.assertFalse(page._results_card.isHidden())

    def test_real_report_and_history_come_back_after_the_example(self) -> None:
        page = self.page
        page._show_history(RUNS)
        page._on_finished(dict(REAL_REPORT))
        self.assertEqual(self._titles(), ["YouTube"])

        page.onboarding_set_state("report")
        self.assertIn("Discord", self._titles())
        # История, пришедшая во время примера, ждёт своей очереди.
        newer = [*RUNS, {**RUNS[0], "time": "2026-10-02T10:00:00"}]
        page._show_history(newer)
        self.assertEqual(len(page._history_list.rows()), 4)

        page.onboarding_set_state(None)
        QApplication.processEvents()

        self.assertEqual(self._titles(), ["YouTube"])
        self.assertEqual(page._summary_panel.title_label.text(), "Всё открывается")
        self.assertEqual(len(page._history_list.rows()), 2)
        self.assertFalse(page._results_card.isHidden())
        self.assertFalse(page._history_card.isHidden())

    def test_card_details_and_past_check_open_on_the_example(self) -> None:
        page = self.page

        page.onboarding_set_state("card_detail")
        self.assertFalse(page.onboarding_target("card_detail").isHidden())
        self.assertTrue(page._tabs_pivot.isHidden())

        page.onboarding_set_state(None)
        page.onboarding_set_state("past_check")
        _breadcrumb, summary = page.onboarding_target("past_check")
        self.assertTrue(page._detail_view.isHidden())
        self.assertFalse(summary.isHidden())
        self.assertEqual(summary.title_label.text(), "Найдены проблемы: 3")
        # Отчёт подаёт сама экскурсия: пометки «отчёт не сохранился» нет, диск не читается.
        self.assertTrue(page._past_check_view.note_label.isHidden())
        self.feature.load_past_blockcheck_report.assert_not_called()

        page.onboarding_set_state(None)
        page.onboarding_set_state("report")
        self.assertTrue(page._past_check_view.isHidden())
        self.assertFalse(page._tabs_pivot.isHidden())
        self.assertFalse(page._results_card.isHidden())

    def test_running_check_is_not_covered_by_the_example(self) -> None:
        page = self.page
        page._run_runtime.is_running = lambda: True

        page.onboarding_set_state("report")

        self.assertEqual(self._titles(), [])
        self.assertEqual(page._history_list.rows(), [])

    def test_strategy_search_shows_a_finished_example_and_goes_back_to_idle(self) -> None:
        page = self.page

        page.onboarding_set_state("strategy_scan_result")
        scan = page._strategy_tab_page
        panel, results_card = page.onboarding_target("scan_result")

        self.assertEqual(page.TAB_ORDER[page._active_tab_index], page.TAB_STRATEGY_SCAN)
        self.assertEqual(panel.state, "found")
        self.assertIn("2 надёжные стратегии", panel.title_label.text())
        self.assertFalse(results_card.isHidden())
        self.assertEqual(len(scan._results_view.working_group.rows()), 2)

        page.onboarding_set_state(None)
        QApplication.processEvents()

        self.assertEqual(panel.state, "idle")
        self.assertEqual(scan._results_view.row_count(), 0)
        self.assertTrue(results_card.isHidden())
        # Страница вернулась на вкладку, с которой экскурсия её увела.
        self.assertEqual(page._active_tab_index, 0)

    def test_finished_strategy_search_is_left_as_it_is(self) -> None:
        page = self.page
        page.onboarding_set_state("strategy_scan")
        scan = page._strategy_tab_page
        scan._scan_panel.show_outcome(kind="not_found", title="Ничего не нашлось", detail="")

        page.onboarding_set_state(None)
        page.onboarding_set_state("strategy_scan_result")
        page.onboarding_set_state(None)
        QApplication.processEvents()

        self.assertEqual(scan._scan_panel.state, "not_found")
        self.assertEqual(scan._results_view.row_count(), 0)

    def test_other_tabs_show_their_top_card(self) -> None:
        page = self.page
        for state, attribute in (
            ("domain_lookup", "_domain_lookup_tab_page"),
            ("dns_servers", "_dns_servers_tab_page"),
            ("dns_spoofing", "_dns_spoofing_tab_page"),
        ):
            with self.subTest(tab=state):
                page.onboarding_set_state(None)
                page.onboarding_set_state(state)
                pivot, card = page.onboarding_target("tab_page")
                self.assertIs(pivot, page._tabs_pivot)
                self.assertTrue(getattr(page, attribute).isAncestorOf(card))
                self.assertFalse(card.isHidden())


class BlockcheckDemoDataTests(unittest.TestCase):
    def test_example_report_is_drawn_by_the_real_card_builder(self) -> None:
        from blockcheck.ui.check_results import history_rows
        from blockcheck.ui.onboarding_demo import demo_history, demo_report
        from blockcheck.ui.onboarding_guide import DEMO_CARD_KEY
        from blockcheck.ui.result_cards_model import build_cards

        report = demo_report()
        cards = {card.key: card for card in build_cards(report)}

        self.assertEqual(cards[DEMO_CARD_KEY].level, "fail")
        self.assertEqual(cards["site:youtube"].level, "ok")
        for key in ("hostings", "voice", "ipv6", "filter", "system"):
            self.assertIn(key, cards)
        # Последняя запись истории — та же проверка, что и в отчёте.
        newest = history_rows(demo_history())[0]
        self.assertEqual((newest.opened, newest.blocked), (2, 2))
        self.assertIn("Перестали открываться", newest.changes)


def _strategy_entries(per_family: int) -> dict:
    entries = {}
    for number in range(per_family):
        for key, desync in (("fake", "fake"), ("split", "multisplit"), ("host", "hostfakesplit")):
            entries[f"{key}-{number:02d}"] = SimpleNamespace(name=f"{key} {number:02d}", args=f"--lua-desync={desync}")
    return entries


class ProfilePageTourStatesTests(unittest.TestCase):
    """Страница профиля: что экскурсия показывает на время шага и как убирает за собой."""

    def setUp(self) -> None:
        _app()

    def _page(self, *, per_family: int):
        from profile.state import ProfileListItem, ProfileSetupPayload
        from profile.ui.profile_setup_page import Zapret2ProfileSetupPage

        def worker(*_args, **_kwargs):
            return None

        page = Zapret2ProfileSetupPage(
            create_profile_setup_load_worker=worker,
            create_profile_list_file_load_worker=worker,
            create_profile_list_file_save_worker=worker,
            create_profile_list_file_validation_worker=worker,
            create_profile_settings_save_worker=worker,
            create_profile_raw_text_save_worker=worker,
            create_profile_enabled_save_worker=worker,
            create_profile_user_update_worker=worker,
            create_profile_user_delete_worker=worker,
            create_profile_strategy_apply_worker=worker,
            create_profile_strategy_feedback_save_worker=worker,
            create_profile_strategy_open_group_save_worker=worker,
            open_profiles=lambda: None,
            open_root=lambda: None,
            on_profile_changed=lambda *_args, **_kwargs: None,
        )
        self.addCleanup(page.deleteLater)
        page._profile_key = "profile:0"
        item = ProfileListItem(
            key="profile:0",
            persistent_key="uid:youtube",
            profile_index=0,
            display_name="YouTube",
            enabled=True,
            in_preset=True,
            strategy_id="host-01",
            strategy_name="host 01",
            match_lines=("--filter-tcp=443", "--hostlist=lists/youtube.txt"),
            list_type="hostlist",
            rating="",
            favorite=False,
            group="youtube",
            group_name="YouTube",
            order=0,
        )
        page._apply_payload(
            ProfileSetupPayload(
                item=item,
                strategy_entries=_strategy_entries(per_family),
                strategy_states={},
                raw_profile_text="--filter-tcp=443",
                raw_strategy_text="--lua-desync=hostfakesplit",
                match_summary="TCP 443",
                strategy_open_group=None,
            )
        )
        page.resize(1200, 800)
        page.show()
        QApplication.processEvents()
        return page

    def test_strategy_details_open_for_the_step_and_close_after_it(self) -> None:
        page = self._page(per_family=12)
        self.assertIsNone(page.onboarding_target("details_steps"))

        page.onboarding_set_state("details")

        self.assertEqual(page._strategy_list.details_strategy_id(), "host-01")
        self.assertFalse(page.onboarding_target("details_steps").isHidden())
        self.assertIsNotNone(page.onboarding_target("details_places"))

        page.onboarding_set_state(None)
        self.assertFalse(page._strategy_list.details_open())
        self.assertIs(page.onboarding_target("strategies"), page._strategy_list)

    def test_details_opened_by_the_user_are_not_closed_by_the_tour(self) -> None:
        page = self._page(per_family=12)
        page._strategy_list.show_details("fake-03")

        page.onboarding_set_state("details")
        page.onboarding_set_state(None)

        self.assertEqual(page._strategy_list.details_strategy_id(), "fake-03")

    def test_try_panel_and_geo_card_are_shown_as_examples_on_a_short_list(self) -> None:
        # Девять стратегий: список короткий, панели «Не помогло — следующая» у профиля нет.
        page = self._page(per_family=3)
        self.assertIsNone(page.onboarding_target("strategy_try"))
        self.assertTrue(page.onboarding_target("geo_notice").isHidden())

        page.onboarding_set_state("try_panel")
        panel = page.onboarding_target("strategy_try")
        self.assertFalse(panel.isHidden())
        self.assertIn("Сейчас выбрана", panel.title.text())
        page.onboarding_set_state(None)
        self.assertIsNone(page.onboarding_target("strategy_try"))

        page.onboarding_set_state("geo_notice")
        card = page.onboarding_target("geo_notice")
        self.assertTrue(card.isVisible())
        self.assertIn("стратегия не поможет", card.title_label.text())
        page.onboarding_set_state(None)
        self.assertFalse(card.is_shown())


# Шаги, которые обязаны найти свою цель на страницах с заглушками вместо данных.
STEPS_WITH_TARGET = (
    "control_nav",
    "start",
    "status",
    "preset",
    "presets_list",
    "presets_toolbar",
    "profiles_toolbar",
    "profile_order",
    "dpi_mode",
    "tools",
    "geo_blocks",
    "diagnostics",
    "appearance",
    "finish",
    "quick_actions",
    "program_settings",
    "windows_settings",
    "fine_tuning",
    "fakes",
    "fakes_table",
    "fakes_own",
    "dpi_modes",
    "dns_now",
    "dns_providers",
    "dns_ai",
    "dns_custom",
    "hosts_summary",
    "hosts_file",
    "telegram_status",
    "telegram_connect",
    "telegram_settings",
    "telegram_hosts",
    "telegram_logs",
    "telegram_advanced",
    "telegram_cloudflare",
    "blockcheck_start",
    "blockcheck_domains",
    "blockcheck_summary",
    "blockcheck_cards",
    "blockcheck_checks",
    "blockcheck_card_detail",
    "blockcheck_history",
    "blockcheck_past_check",
    "blockcheck_report",
    "blockcheck_tabs",
    "strategy_scan",
    "strategy_scan_result",
    "domain_lookup",
    "dns_servers_check",
    "dns_spoofing_check",
    "log_analyzer_source",
    "log_analyzer_connections",
    "log_analyzer_packets",
    "appearance_theme",
    "appearance_accent",
    "appearance_performance",
    "logs_view",
    "logs_send",
    "about_version",
    "about_help",
    "updates",
)

# Шаги, цели которых появляются только с настоящими данными: строка пресета,
# текст пресета в редакторе, профили и их стратегии, плитки сервисов hosts,
# запись реестра фейков. Общий прогон ниже их не видит; шаги про страницу
# профиля проверяет ProfilePageTourStatesTests.
STEPS_NEEDING_REAL_DATA = (
    "preset_menu",
    "preset_file",
    "preset_header",
    "preset_lua_init",
    "preset_engine_options",
    "preset_interception",
    "preset_blobs",
    "preset_profile",
    "preset_profile_name",
    "preset_profile_match",
    "preset_profile_packets",
    "preset_profile_strategy",
    "preset_profile_new",
    "profiles_list",
    "profile_group",
    "profile_row",
    "profile_menu",
    "list_type",
    "ranges",
    "profile_tabs",
    "strategy_choice",
    "strategy_try",
    "strategy_find",
    "strategy_details",
    "strategy_details_places",
    "profile_geo_notice",
    "list_entries",
    "fakes_blob",
    "hosts_direct",
    "hosts_ai",
)

_NAV_GROUPS = {
    "root": (PageName.ZAPRET2_MODE_CONTROL,),
    "settings": (PageName.ZAPRET2_USER_PRESETS, PageName.ZAPRET2_PRESET_SETUP, PageName.DPI_SETTINGS),
    "system": (PageName.NETWORK, PageName.HOSTS, PageName.TELEGRAM_PROXY),
    "diagnostics": (PageName.BLOCKCHECK, PageName.WINWS_LOG_ANALYZER),
    "appearance": (PageName.APPEARANCE, PageName.PREMIUM, PageName.LOGS, PageName.ABOUT),
}


class _PageHost:
    """Как настоящий page host: строит страницу при первом показе, но с заглушками зависимостей."""

    def __init__(self, window: QWidget, stack: QStackedWidget) -> None:
        self._window = window
        self._stack = stack
        self.pages: dict[PageName, QWidget] = {}

    def _deps(self, page_cls) -> dict:
        deps = {
            parameter.name: MagicMock()
            for parameter in inspect.signature(page_cls.__init__).parameters.values()
            if parameter.kind is parameter.KEYWORD_ONLY and parameter.default is parameter.empty
        }
        if "blockcheck_feature" in deps:
            deps["blockcheck_feature"] = _BlockcheckFeature()
        return deps

    def show_page(self, page_name, allow_internal=False) -> bool:
        from ui.page_registry import PAGE_CLASS_SPECS

        _ = allow_internal
        if page_name not in self.pages:
            module_name, class_name = PAGE_CLASS_SPECS[page_name]
            page_cls = getattr(import_module(module_name), class_name)
            page = page_cls(parent=self._window, **self._deps(page_cls))
            self._stack.addWidget(page)
            self.pages[page_name] = page
            if page_name is PageName.NETWORK:
                # Настоящая страница открывает «Свой DNS» через окно; здесь — через этот же host.
                page._open_custom_server = lambda _server=None: self.show_page(PageName.NETWORK_CUSTOM_DNS)
        self._stack.setCurrentWidget(self.pages[page_name])
        return True

    def get_loaded_page(self, page_name):
        return self.pages.get(page_name)

    def current_page(self):
        return self._stack.currentWidget()


class TourWalkOverRealPagesTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        # Заглушки зависимостей иногда роняют фоновые обработчики страниц; без своего
        # перехватчика PyQt завершил бы процесс на первом же таком исключении.
        self.slot_errors: list[str] = []
        hook = patch.object(sys, "excepthook", lambda kind, value, _tb: self.slot_errors.append(f"{kind.__name__}: {value}"))
        hook.start()
        self.addCleanup(hook.stop)

    def _window(self):
        window = QWidget()
        self.addCleanup(window.deleteLater)
        window.resize(1280, 860)
        root = QHBoxLayout(window)
        nav = QWidget(window)
        nav.setFixedWidth(210)
        nav_layout = QVBoxLayout(nav)
        stack = QStackedWidget(window)
        root.addWidget(nav)
        root.addWidget(stack, 1)
        nav_items, nav_header_by_group, nav_headers = {}, {}, []
        for group, page_names in _NAV_GROUPS.items():
            if group != "root":
                header = QLabel(group, nav)
                nav_layout.addWidget(header)
                nav_header_by_group[group] = header
                nav_headers.append((header, page_names, f"nav.header.{group}"))
            for page_name in page_names:
                item = QPushButton(page_name.name, nav)
                nav_layout.addWidget(item)
                nav_items[page_name] = item
        nav_layout.addStretch(1)
        host = _PageHost(window, stack)
        window.ui_session = SimpleNamespace(
            nav_items=nav_items,
            nav_header_by_group=nav_header_by_group,
            nav_headers=nav_headers,
            page_host=host,
        )
        return window, host

    def test_every_step_with_a_target_is_checked_on_real_pages_or_listed_as_needing_data(self) -> None:
        from ui.onboarding.steps import TOUR_STEPS

        with_target = {step.key for step in TOUR_STEPS if step.target is not None}
        self.assertEqual(set(STEPS_WITH_TARGET) & set(STEPS_NEEDING_REAL_DATA), set())
        self.assertEqual(
            sorted(with_target - set(STEPS_WITH_TARGET) - set(STEPS_NEEDING_REAL_DATA)),
            [],
            "Новый шаг с подсветкой не проверяется на настоящей странице. Добавьте его ключ в "
            "STEPS_WITH_TARGET (или в STEPS_NEEDING_REAL_DATA, если цели нет без настоящих данных). "
            "Как править экскурсию: .codex/skills/zapretgui-guided-tour/SKILL.md",
        )
        # Удалённый шаг не остаётся в списках.
        self.assertEqual(sorted((set(STEPS_WITH_TARGET) | set(STEPS_NEEDING_REAL_DATA)) - with_target), [])

    def test_every_new_step_finds_its_target_on_the_real_page(self) -> None:
        from ui.onboarding.overlay import OnboardingOverlay
        from ui.onboarding.steps import TOUR_STEPS, build_tour_context

        window, host = self._window()
        window.show()
        host.show_page(PageName.ZAPRET2_MODE_CONTROL)
        QApplication.processEvents()
        context = build_tour_context(window)
        context.current_page = host.current_page()
        with patch("ui.onboarding.overlay.are_live_animations_enabled", return_value=False):
            overlay = OnboardingOverlay(window, context, TOUR_STEPS)
        self.assertTrue(overlay.start())
        self.addCleanup(lambda: overlay.finish("skipped", immediate=True))

        with_target: set[str] = set()
        on_screen: set[str] = set()
        for _ in range(len(TOUR_STEPS)):
            for _ in range(4):
                QApplication.processEvents()
                overlay._on_frame()
            key = overlay.current_step_key()
            if overlay._targets:
                with_target.add(key)
            if overlay._hole is not None:
                on_screen.add(key)
            if overlay._index >= len(overlay._steps) - 1:
                break
            overlay.go_next()

        self.assertEqual(overlay.current_step_key(), "finish")
        self.assertEqual(sorted(set(STEPS_WITH_TARGET) - with_target), [])
        # Цель не только найдена, но и докручена в видимую часть окна.
        self.assertEqual(sorted(set(STEPS_WITH_TARGET) - on_screen), [])


if __name__ == "__main__":
    unittest.main()
