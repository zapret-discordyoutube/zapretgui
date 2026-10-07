import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from blockcheck.page_run_workflow import request_blockcheck_stop, reset_blockcheck_running_ui, start_blockcheck_page_run
from blockcheck.ui.check_results import BlockcheckSummaryPanel
from blockcheck.ui.page import BlockcheckPage


class _FeatureStub:
    pass


def _make_page() -> BlockcheckPage:
    with patch.object(BlockcheckPage, "_request_page_initial_state_load", lambda self: None):
        return BlockcheckPage(
            blockcheck_feature=_FeatureStub(),
            dns_feature=_FeatureStub(),
            create_strategy_scan_worker=lambda *_args, **_kwargs: None,
        )


_REPORT = {
    "scope": "all",
    "zapret_running": True,
    "elapsed": 7.4,
    "timed_out": False,
    "problems": [
        {
            "level": "fail",
            "text": "X (Twitter) не открывается: соединение блокирует провайдер",
            "advice": ["Подберите другую стратегию"],
            "action": "strategy",
            "target": "x.com",
        },
        {
            "level": "warn",
            "text": "DNS подменяет адреса: www.youtube.com",
            "advice": ["Включите DoH"],
            "action": "dns",
            "target": "",
        },
    ],
    "working": ["Discord", "YouTube"],
    "services": [
        {
            "key": "youtube",
            "label": "YouTube",
            "control": False,
            "level": "warn",
            "headline": "YouTube открывается",
            "dns_note": "DNS подменяет адрес www.youtube.com",
            "targets": [
                {"host": "www.youtube.com", "purpose": "сайт", "ok": True, "short": "открывается", "text": "открывается"},
                {"host": "i.ytimg.com", "purpose": "превью", "ok": True, "short": "открывается", "text": "открывается"},
            ],
        },
        {
            "key": "x",
            "label": "X (Twitter)",
            "control": False,
            "level": "fail",
            "headline": "X (Twitter) не открывается",
            "dns_note": "",
            "targets": [{"host": "x.com", "purpose": "сайт", "ok": False, "short": "соединение сброшено", "text": "сброс"}],
        },
    ],
    "voice": {"level": "ok", "headline": "Голосовые звонки: UDP проходит", "advice": [], "items": []},
    "freeze": {"level": "ok", "headline": "Обрыва загрузки на 16–20 КБ нет", "advice": [], "items": []},
}


class BlockcheckPageAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_main_controls_are_named_for_screen_reader(self) -> None:
        page = _make_page()
        self.addCleanup(page.deleteLater)

        self.assertEqual(page._tabs_pivot.accessibleName(), "Раздел BlockCheck, выбрано: BlockCheck")
        self.assertIn("BlockCheck, Подбор стратегии, Проверка домена, DNS-серверы или DNS подмена", page._tabs_pivot.accessibleDescription())
        self.assertEqual(
            page._scope_combo.accessibleName(),
            "Что проверить BlockCheck, выбрано: Полная проверка (около минуты)",
        )
        self.assertEqual(page._start_btn.text(), "Проверить")
        self.assertEqual(page._start_btn.accessibleName(), "Запустить BlockCheck")
        self.assertTrue(page._stop_btn.isHidden())
        self.assertFalse(page._report_btn.isEnabled())
        self.assertEqual(page._report_btn.accessibleName(), "Открыть подробный отчёт BlockCheck")
        self.assertEqual(page._prepare_support_btn.accessibleName(), "Подготовить обращение по BlockCheck")
        self.assertEqual(page._result_cards.accessibleName(), "Результаты BlockCheck: пока нет результатов")
        self.assertEqual(page._summary_panel.level, "idle")

    def test_hidden_progress_bars_do_not_animate(self) -> None:
        # IndeterminateProgressBar по умолчанию запускает бесконечную анимацию
        # в конструкторе, и она крутится 60 раз в секунду у скрытой полосы.
        from qfluentwidgets import IndeterminateProgressBar

        page = _make_page()
        self.addCleanup(page.deleteLater)

        bars = page.findChildren(IndeterminateProgressBar)
        self.assertTrue(bars)
        for bar in bars:
            if not bar.isVisibleTo(page):
                self.assertFalse(bar.isStarted(), bar.accessibleName())

    def test_scope_combo_menu_items_are_named_for_screen_reader(self) -> None:
        page = _make_page()
        self.addCleanup(page.deleteLater)
        menu = page._scope_combo._create_accessible_combo_menu()

        self.assertEqual(
            menu.view.item(0).data(Qt.ItemDataRole.AccessibleTextRole),
            "Что проверить BlockCheck: Полная проверка (около минуты), выбран",
        )

    def test_cards_have_no_headers_to_save_space(self) -> None:
        page = _make_page()
        self.addCleanup(page.deleteLater)

        for card in (page._control_card, page._domains_card, page._results_card, page._footer_card):
            self.assertIsNone(card._title_label)
        # Пустая таблица до первой проверки не показывается.
        self.assertTrue(page._results_card.isHidden())
        # Отчёта ещё нет — блока «Отчёт / Подготовить обращение» тоже нет.
        self.assertTrue(page._footer_card.isHidden())
        page._switch_tab(0)
        self.assertTrue(page._footer_card.isHidden())

    def test_diagnostics_tab_is_merged_into_blockcheck(self) -> None:
        page = _make_page()
        self.addCleanup(page.deleteLater)

        self.assertEqual(page.TAB_ORDER, ("blockcheck", "strategy_scan", "domain_lookup", "dns_servers", "dns_spoofing"))
        self.assertEqual(page._normalize_tab_key("diagnostics"), "blockcheck")
        self.assertEqual(page._normalize_tab_key("connection"), "blockcheck")

    def test_open_diagnostics_selects_discord_youtube_and_focuses_start(self) -> None:
        page = _make_page()
        self.addCleanup(page.deleteLater)
        page.is_page_ready = lambda: True

        page.request_diagnostics_start_focus()

        self.assertEqual(page._scope_combo.currentData(), "main")
        self.assertFalse(page._pending_diagnostics_start_focus)

    def test_finished_report_fills_summary_and_sites(self) -> None:
        page = _make_page()
        self.addCleanup(page.deleteLater)
        page._report_lines = ["🔍 BlockCheck"]

        page._on_finished(dict(_REPORT))

        self.assertEqual(page._summary_panel.level, "fail")
        # Точный смысл — в начале заголовка, шутка (если есть) — после.
        self.assertTrue(page._summary_panel.title_label.text().startswith("Найдены проблемы: 2"))
        self.assertFalse(page._results_card.isHidden())
        self.assertTrue(page._report_btn.isEnabled())
        self.assertFalse(page._footer_card.isHidden())
        page._switch_tab(1)
        self.assertTrue(page._footer_card.isHidden())
        page._switch_tab(0)
        self.assertFalse(page._footer_card.isHidden())
        cards = {widget.card.title: widget.card for widget in page._result_cards.cards()}
        names = list(cards)
        # Сначала сломанное; YouTube открывается — подмена DNS только меткой.
        self.assertEqual(names[0], "X (Twitter)")
        self.assertEqual(cards["YouTube"].status, "Открывается")
        self.assertIn(("DNS подменён", "warn"), cards["YouTube"].chips)
        self.assertIn("Голосовые звонки (UDP)", names)
        # Ход проверки после итога убирается.
        self.assertTrue(page._progress_card.isHidden())

    def test_stopped_run_does_not_leave_pending_summary(self) -> None:
        page = _make_page()
        self.addCleanup(page.deleteLater)
        page._summary_panel.set_pending()

        page._on_finished(None)

        self.assertEqual(page._summary_panel.level, "unknown")
        self.assertEqual(page._summary_panel.title_label.text(), "Проверка остановлена")

    def test_failed_run_is_not_reported_as_stopped(self) -> None:
        page = _make_page()
        self.addCleanup(page.deleteLater)

        page._on_finished({"failed": True, "error": "boom"})

        self.assertEqual(page._summary_panel.title_label.text(), "Проверка завершилась с ошибкой")

    def test_stop_while_waiting_in_queue_removes_the_run(self) -> None:
        page = _make_page()
        self.addCleanup(page.deleteLater)
        runtime = Mock()
        runtime.is_queued.return_value = True
        page._run_runtime = runtime

        page._on_stop()

        runtime.stop.assert_called_once_with()
        self.assertEqual(page._summary_panel.title_label.text(), "Проверка остановлена")

    def test_zapret_and_dns_buttons_open_their_pages(self) -> None:
        from app.page_names import PageName

        page = _make_page()
        self.addCleanup(page.deleteLater)
        with patch("ui.window_adapter.show_page") as show_page, patch(
            "ui.workflows.mode.show_active_mode_control_page"
        ) as show_control:
            page._on_problem_action("dns", "")
            page._on_problem_action("start_zapret", "x.com")

        self.assertEqual(show_page.call_args.args[1], PageName.NETWORK)
        show_control.assert_called_once()

    def test_strategy_button_opens_strategy_tab_with_target(self) -> None:
        page = _make_page()
        self.addCleanup(page.deleteLater)
        strategy_page = Mock()
        page._strategy_tab_page = strategy_page
        page.switch_to_tab = Mock()

        page._on_problem_action("strategy", "x.com")
        page._on_problem_action("strategy_voice", "")

        page.switch_to_tab.assert_called_with("strategy_scan")
        strategy_page.prefill_target.assert_any_call("x.com", protocol="tcp_https")
        strategy_page.prefill_target.assert_any_call("", protocol="stun_voice")


class SummaryPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_problem_rows_have_their_action_buttons(self) -> None:
        calls = []
        panel = BlockcheckSummaryPanel(on_action=lambda action, target: calls.append((action, target)))
        panel.show_report(dict(_REPORT))

        rows = panel.problem_rows()
        rows[0].action_button.click()
        # У предупреждения про DNS — кнопка «Настройка DNS», у строки «Открываются» кнопки нет.
        self.assertEqual(rows[1].action_button.text(), "Настройка DNS")
        rows[1].action_button.click()
        self.assertIsNone(rows[2].action_button)
        self.assertEqual(calls, [("strategy", "x.com"), ("dns", "")])
        self.assertIn("Zapret включён", panel.env_label.text())

    def test_no_problems_is_all_ok(self) -> None:
        panel = BlockcheckSummaryPanel()
        panel.show_report({"problems": [], "working": ["Discord"], "zapret_running": False, "elapsed": 3})

        self.assertEqual(panel.level, "ok")
        self.assertTrue(panel.title_label.text().startswith("Всё открывается"))
        self.assertIn("Zapret выключен", panel.env_label.text())


def _kind_problem(kind: str, title: str, *, evidence=(), advice=("Подберите другую стратегию",), level="fail") -> dict:
    return {
        "level": level,
        "text": f"{title} не открывается: причина",
        "advice": [*evidence, *advice],
        "evidence": list(evidence),
        "action": "strategy",
        "target": f"{title.lower()}.com",
        "kind": kind,
        "title": title,
    }


def _kind_service(label: str, level: str, kind: str = "", *, ok: bool = False, control: bool = False) -> dict:
    return {
        "key": label.lower(),
        "label": label,
        "control": control,
        "level": level,
        "kind": kind,
        "headline": label,
        "targets": [{"host": f"{label.lower()}.com", "purpose": "сайт", "ok": ok, "short": "подробность"}],
    }


_KINDS_REPORT = {
    "zapret_running": True,
    "elapsed": 9,
    "problems": [
        _kind_problem("sni", "X", evidence=("С именем x.com соединение обрывается.",)),
        _kind_problem("sni", "LinkedIn", evidence=("С именем linkedin.com соединение обрывается.",)),
        _kind_problem("ip", "Telegram", advice=("Попробуйте другой DNS",)),
        _kind_problem("cut16", "Spotify"),
        {"level": "warn", "text": "DNS подменяет ответы", "advice": ["Включите DoH"], "action": "dns", "kind": "dns"},
    ],
    "working": ["Discord"],
    "services": [
        _kind_service("Discord", "ok", ok=True),
        # Подмена DNS при открывающемся сайте — не блокировка сайта.
        _kind_service("YouTube", "warn", ok=True),
        _kind_service("X", "fail", "sni"),
        _kind_service("LinkedIn", "fail", "sni"),
        _kind_service("Telegram", "fail", "ip"),
        _kind_service("Spotify", "fail", "cut16"),
        _kind_service("Google", "ok", ok=True, control=True),
        _kind_service("Reddit", "unknown"),
    ],
}


class BlockKindsOnScreenTests(unittest.TestCase):
    """На экране видно, где бан по адресу, где блокировка по имени, а где обрыв после 16 КБ."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_sites_are_counted_by_block_kind(self) -> None:
        from blockcheck.ui.block_kinds_view import site_groups

        groups = {group.key: group.names for group in site_groups(_KINDS_REPORT)}

        self.assertEqual(
            groups,
            {
                "open": ("Discord", "YouTube"),
                "ip": ("Telegram",),
                "sni": ("X", "LinkedIn"),
                "cut16": ("Spotify",),
                "unknown": ("Reddit",),
            },
        )
        # Сначала хорошее, затем виды по тяжести; контрольный сайт не считается.
        self.assertEqual([group.key for group in site_groups(_KINDS_REPORT)], ["open", "ip", "sni", "cut16", "unknown"])

    def test_overview_shows_a_tile_and_a_bar_share_for_every_kind(self) -> None:
        panel = BlockcheckSummaryPanel()
        self.addCleanup(panel.deleteLater)
        panel.show_report(dict(_KINDS_REPORT))

        tiles = panel.overview.tiles()
        self.assertFalse(panel.overview.isHidden())
        self.assertEqual([tile.group().title for tile in tiles][:4], ["Открываются", "По адресу (IP)", "По имени (SNI)", "Обрыв после 16 КБ"])
        self.assertEqual([tile.group().count for tile in tiles], [2, 1, 2, 1, 1])
        self.assertEqual([group.key for group in panel.overview.bar.groups()], ["open", "ip", "sni", "cut16", "unknown"])
        self.assertIn("по имени (sni) — 2", panel.overview.accessibleDescription() + panel.overview.accessibleName())
        self.assertIn("SNI", tiles[2].toolTip())

        panel.set_pending()
        self.assertTrue(panel.overview.isHidden())
        self.assertEqual(panel.overview.tiles(), [])

    def test_tile_number_ends_on_its_value_with_animations_off(self) -> None:
        from blockcheck.ui import block_kinds_view as view

        tile = view.KindTile(view.SiteGroup("sni", "По имени (SNI)", ("X", "LinkedIn")))
        self.addCleanup(tile.deleteLater)
        with patch.object(view, "are_live_animations_enabled", return_value=False):
            tile.play(0)

        self.assertEqual(tile.shown_count(), 2)

    def test_problems_are_grouped_by_kind_with_one_explanation_per_group(self) -> None:
        panel = BlockcheckSummaryPanel(on_action=lambda *_args: None)
        self.addCleanup(panel.deleteLater)
        panel.show_report(dict(_KINDS_REPORT))

        groups = panel.problem_groups()
        self.assertEqual([group.kind() for group in groups], ["ip", "sni", "cut16", "dns"])
        sni = groups[1]
        self.assertEqual(sni.title_label.text(), "Блокировка по имени (SNI)")
        self.assertIn("SNI", sni.about_label.text())
        # Одинаковый у обоих сайтов совет показан один раз — в заголовке группы.
        self.assertEqual([label.text() for label in sni.shared_labels], ["→ Подберите другую стратегию"])
        # Сайты — карточки одной сетки: в карточке название, объяснение — в подсказке.
        self.assertEqual([card.title for card in sni.rows], ["X", "LinkedIn"])
        self.assertEqual(sni.flow.cards(), sni.rows)
        self.assertIn("С именем x.com соединение обрывается.", sni.rows[0].hint_text)
        self.assertEqual(sni.rows[0].action_button.toolTip(), "Подобрать стратегию для x.com")
        # В группе из одного сайта совет не выносится в заголовок: он в подсказке карточки.
        self.assertEqual(groups[0].shared_labels, [])
        self.assertIn("→ Попробуйте другой DNS", groups[0].rows[0].hint_text)
        # Все строки по-прежнему доступны списком: группы, затем «Открываются».
        self.assertEqual(len(panel.problem_rows()), 6)

    def test_problem_without_kind_gets_its_own_titled_group(self) -> None:
        panel = BlockcheckSummaryPanel()
        self.addCleanup(panel.deleteLater)
        panel.show_report(dict(_REPORT))

        [group] = panel.problem_groups()
        self.assertEqual(group.kind(), "other")
        # Заголовок и пояснение есть: без них карточки висели между группами как случайные.
        self.assertEqual(group.title_label.text(), "Остальное")
        self.assertIn("не определила", group.about_label.text())
        # Сайт без вида блокировки — такая же карточка: название, что с ним, причина в подсказке.
        card = group.rows[0]
        self.assertEqual((card.title, card.note), ("X (Twitter)", "не открывается"))
        self.assertIn("Соединение блокирует провайдер.", card.hint_text)


class HistoryCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    RUNS = [
        {"kind": "blockcheck", "time": "2026-10-05T09:00:00", "title": "Все сайты", "level": "ok", "headline": "Всё открывается"},
        {"kind": "blockcheck", "time": "2026-10-06T10:00:00", "title": "Все сайты", "level": "fail", "headline": "YouTube не открывается"},
        {"kind": "blockcheck", "time": "2026-10-07T20:15:00", "title": "Полная проверка", "level": "катастрофа", "headline": ""},
    ]

    def test_lines_are_newest_first_with_time_scope_and_outcome(self) -> None:
        from blockcheck.ui.check_results import history_lines

        lines = history_lines(self.RUNS)

        self.assertEqual(lines[0], ("unknown", "? 07.10 20:15 · Полная проверка — итог не записан"))
        self.assertEqual(lines[1], ("fail", "✗ 06.10 10:00 · Все сайты — YouTube не открывается"))
        self.assertEqual(lines[2], ("ok", "✓ 05.10 09:00 · Все сайты — Всё открывается"))
        # Показываются только последние записи.
        self.assertEqual(len(history_lines(self.RUNS * 5, limit=4)), 4)
        self.assertEqual(history_lines([]), [])

    def test_card_is_hidden_until_there_is_history_and_follows_new_runs(self) -> None:
        page = _make_page()
        self.assertTrue(page._history_card.isHidden())

        page._show_history(self.RUNS[:2])
        self.assertFalse(page._history_card.isHidden())
        self.assertEqual(len(page._history_list.lines()), 2)
        self.assertIn("YouTube не открывается", page._history_list.accessibleName())

        # Свежая проверка приносит обновлённую историю вместе с отчётом.
        page._on_finished({"problems": [], "services": [], "elapsed": 1.0, "history": self.RUNS})
        self.assertEqual(len(page._history_list.lines()), 3)

        # На другой вкладке карточка скрыта, при возврате — снова видна.
        page._switch_tab(page.TAB_ORDER.index("strategy_scan"))
        self.assertTrue(page._history_card.isHidden())
        page._switch_tab(0)
        self.assertFalse(page._history_card.isHidden())

    def test_history_comes_with_initial_page_state(self) -> None:
        from blockcheck import page_runtime

        settings = {"blockcheck": {"user_domains": ["example.com"], "check_history": self.RUNS[:1] + ["мусор"]}}
        with patch("settings.store.read_settings", return_value=settings):
            state = page_runtime.load_page_initial_state()

        self.assertEqual(state.user_domains, ("example.com",))
        self.assertEqual(len(state.check_history), 1)
        with patch("settings.store.read_settings", side_effect=OSError("нет базы")):
            self.assertEqual(page_runtime.load_page_initial_state().check_history, ())


class ScopeChoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_full_check_is_the_first_choice_and_the_default(self) -> None:
        page = _make_page()
        combo = page._scope_combo

        self.assertEqual([combo.itemData(index) for index in range(combo.count())], ["full", "all", "main"])
        self.assertEqual(page._current_scope(), "full")
        self.assertIn("Полная проверка", combo.currentText())
        combo.setCurrentIndex(1)
        self.assertEqual(page._current_scope(), "all")
        page.set_ui_language("en")
        self.assertEqual(combo.itemText(0), "Full check (about a minute)")


class SummaryChangesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_changes_since_last_run_are_shown_and_cleared(self) -> None:
        panel = BlockcheckSummaryPanel()
        self.assertTrue(panel.changes_label.isHidden())

        panel.show_report(
            {"problems": [], "changes": ["перестали открываться: YouTube"], "previous_time": "2026-10-06T10:00:00"}
        )
        self.assertFalse(panel.changes_label.isHidden())
        self.assertEqual(panel.changes_label.text(), "С прошлой проверки (06.10 10:00) перестали открываться: YouTube.")

        # Отчёт без перемен и новая проверка строку убирают.
        panel.show_report({"problems": [], "changes": []})
        self.assertTrue(panel.changes_label.isHidden())
        panel.show_report({"problems": [], "changes": ["снова открываются: Discord"], "previous_time": "x"})
        panel.set_pending()
        self.assertTrue(panel.changes_label.isHidden())


class _ButtonStub:
    def __init__(self, text: str = "") -> None:
        self._enabled = True
        self._visible = True
        self.properties = {}
        self.accessible_name = ""
        self.accessible_description = ""
        self._text = text

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802
        self._text = str(text)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        self._enabled = bool(enabled)

    def isEnabled(self) -> bool:  # noqa: N802
        return self._enabled

    def setVisible(self, visible: bool) -> None:  # noqa: N802
        self._visible = bool(visible)

    def isVisible(self) -> bool:  # noqa: N802
        return self._visible

    def accessibleName(self) -> str:  # noqa: N802
        return self.accessible_name

    def setAccessibleName(self, text: str) -> None:  # noqa: N802
        self.accessible_name = str(text)

    def accessibleDescription(self) -> str:  # noqa: N802
        return self.accessible_description

    def setAccessibleDescription(self, text: str) -> None:  # noqa: N802
        self.accessible_description = str(text)

    def property(self, name: str) -> object:
        return self.properties.get(name)

    def setProperty(self, name: str, value: object) -> None:  # noqa: N802
        self.properties[name] = value


class _FakeProgressBar(_ButtonStub):
    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False


class _SignalStub:
    def connect(self, _callback) -> None:
        pass


class _WorkerStub:
    def __init__(self) -> None:
        self.log_message = _SignalStub()
        self.run_log_started = _SignalStub()
        self.finished = _SignalStub()
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _RunFeatureStub:
    def __init__(self) -> None:
        self.kwargs = {}

    def create_blockcheck_worker(self, **kwargs):
        self.kwargs = kwargs
        return _WorkerStub()


class _RunRuntimeStub:
    def __init__(self) -> None:
        self.worker = None

    def start_qobject_worker(self, *, parent, worker_factory) -> None:
        _ = parent
        self.worker = worker_factory(1)


class BlockcheckWorkerTests(unittest.TestCase):
    def _worker(self):
        from types import SimpleNamespace

        from blockcheck.worker import BlockcheckWorker

        return BlockcheckWorker(
            start_run_log=lambda *_a: SimpleNamespace(path=None, created=True),
            append_run_log=lambda *_a: None,
            close_run_log=lambda *_a: None,
        )

    def test_stop_pressed_before_start_is_not_lost(self) -> None:
        seen = []
        worker = self._worker()
        worker.stop()

        def _fake_run(*_args, should_stop, **_kwargs):
            seen.append(should_stop())
            return {"stopped": True}

        with patch("diagnostics.engine.run_blockcheck", side_effect=_fake_run):
            worker.run()

        self.assertEqual(seen, [True])

    def test_crash_is_reported_as_failure_not_stop(self) -> None:
        results = []
        worker = self._worker()
        worker.finished.connect(results.append)

        with patch("diagnostics.engine.run_blockcheck", side_effect=RuntimeError("boom")):
            worker.run()

        self.assertEqual(results, [{"failed": True, "error": "boom"}])


class RunWorkflowTests(unittest.TestCase):
    def test_run_controls_read_running_and_idle_states(self) -> None:
        progress_bar = _FakeProgressBar()
        start_button = _ButtonStub()
        stop_button = _ButtonStub()
        scope_combo = _ButtonStub()
        status_label = _ButtonStub()
        feature = _RunFeatureStub()

        start_blockcheck_page_run(
            blockcheck_feature=feature,
            scope="all",
            user_domains=["example.org"],
            parent=None,
            run_runtime=_RunRuntimeStub(),
            start_button=start_button,
            stop_button=stop_button,
            scope_combo=scope_combo,
            progress_bar=progress_bar,
            status_label=status_label,
            set_support_status=lambda _text: None,
            tr_fn=lambda _key, default: default,
            on_log=lambda *_args: None,
            on_run_log_started=lambda *_args: None,
            on_finished=lambda *_args: None,
        )

        self.assertEqual(feature.kwargs["scope"], "all")
        self.assertEqual(feature.kwargs["user_domains"], ["example.org"])
        self.assertEqual(progress_bar.accessibleName(), "Ход BlockCheck: выполняется")
        self.assertEqual(start_button.accessibleName(), "Запустить BlockCheck, недоступно")
        self.assertEqual(stop_button.accessibleName(), "Остановить BlockCheck, доступно")
        self.assertTrue(stop_button.isVisible())
        self.assertEqual(scope_combo.accessibleName(), "Что проверить BlockCheck, недоступно во время проверки")
        self.assertTrue(status_label.accessibleName().startswith("Статус BlockCheck: Проверяем"))

        reset_blockcheck_running_ui(
            start_button=start_button,
            stop_button=stop_button,
            scope_combo=scope_combo,
            progress_bar=progress_bar,
        )

        self.assertEqual(progress_bar.accessibleName(), "Ход BlockCheck: не выполняется")
        self.assertEqual(start_button.accessibleName(), "Запустить BlockCheck, доступно")
        self.assertFalse(stop_button.isVisible())
        self.assertEqual(scope_combo.accessibleName(), "Что проверить BlockCheck, доступно")

    def test_stop_request_reads_stopping_state(self) -> None:
        worker = _WorkerStub()
        stop_button = _ButtonStub()
        status_label = _ButtonStub()

        request_blockcheck_stop(
            worker=worker,
            stop_button=stop_button,
            status_label=status_label,
            force_stop=lambda _worker: None,
            tr_fn=lambda _key, default: default,
        )

        self.assertTrue(worker.stopped)
        self.assertEqual(stop_button.accessibleName(), "Остановить BlockCheck, недоступно")
        self.assertEqual(status_label.accessibleName(), "Статус BlockCheck: Останавливаем…")


if __name__ == "__main__":
    unittest.main()
