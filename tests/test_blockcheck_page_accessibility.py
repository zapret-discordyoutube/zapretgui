import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from blockcheck.page_run_workflow import request_blockcheck_stop, reset_blockcheck_running_ui, start_blockcheck_page_run
from blockcheck.ui.check_results import BlockcheckSitesTable, BlockcheckSummaryPanel
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
        self.assertIn("BlockCheck, Подбор стратегии или DNS подмена", page._tabs_pivot.accessibleDescription())
        self.assertEqual(
            page._scope_combo.accessibleName(),
            "Что проверить BlockCheck, выбрано: Все сайты",
        )
        self.assertEqual(page._start_btn.text(), "Проверить")
        self.assertEqual(page._start_btn.accessibleName(), "Запустить BlockCheck")
        self.assertTrue(page._stop_btn.isHidden())
        self.assertFalse(page._report_btn.isEnabled())
        self.assertEqual(page._report_btn.accessibleName(), "Открыть подробный отчёт BlockCheck")
        self.assertEqual(page._prepare_support_btn.accessibleName(), "Подготовить обращение по BlockCheck")
        self.assertEqual(page._sites_table.accessibleName(), "Результаты BlockCheck по сайтам: пока нет результатов")
        self.assertEqual(page._summary_panel.level, "idle")

    def test_scope_combo_menu_items_are_named_for_screen_reader(self) -> None:
        page = _make_page()
        self.addCleanup(page.deleteLater)
        menu = page._scope_combo._create_accessible_combo_menu()

        self.assertEqual(
            menu.view.item(0).data(Qt.ItemDataRole.AccessibleTextRole),
            "Что проверить BlockCheck: Discord и YouTube, не выбран",
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

        self.assertEqual(page.TAB_ORDER, ("blockcheck", "strategy_scan", "dns_spoofing"))
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
        names = [page._sites_table.item(row, 0).text() for row in range(page._sites_table.rowCount())]
        # Сначала сломанное; YouTube открывается — подмена DNS только в подробностях.
        self.assertEqual(names[0], "X (Twitter)")
        youtube_row = names.index("YouTube")
        self.assertEqual(page._sites_table.item(youtube_row, 1).text(), "✓ Открывается")
        self.assertIn("DNS подменён", page._sites_table.item(youtube_row, 2).text())
        self.assertIn("Голосовые звонки (UDP)", names)
        self.assertIn("Обрыв на 16–20 КБ", names)

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


class SitesTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_table_grows_to_fit_all_rows(self) -> None:
        """Строки «Звонки» и «Обрыв» не должны прятаться под прокруткой таблицы."""
        table = BlockcheckSitesTable()
        table.show_report(dict(_REPORT))

        self.assertEqual(table.rowCount(), 4)
        self.assertEqual(table.minimumHeight(), table.maximumHeight())
        self.assertGreater(table.minimumHeight(), table.horizontalHeader().height())


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
