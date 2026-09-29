"""Выбор «продолжить или начать заново» доходит от окна до движка подбора."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from blockcheck import strategy_scan_page_plans as plans  # noqa: E402
from blockcheck.scan_models import StrategyScanReport  # noqa: E402
from blockcheck.strategy_scan_run_workflow import (  # noqa: E402
    plan_strategy_scan_resume,
    start_strategy_scan_run,
)
from blockcheck.strategy_scan_worker import StrategyScanWorker  # noqa: E402

_SEARCH = "blockcheck.strategy_search.engine.run_strategy_search"


def _feature(tested: int = 0):
    feature = MagicMock()
    feature.build_selection_state.return_value = SimpleNamespace(
        scan_protocol="tcp_https", udp_games_scope="all", mode="quick"
    )
    feature.plan_scan_start.return_value = SimpleNamespace(
        target="youtube.com", scan_protocol="tcp_https", udp_games_scope="all", mode="quick", status_text=""
    )
    feature.count_resumable_strategies.return_value = tested
    return feature


def _start(feature, create_worker, **extra):
    noop = lambda *_a, **_k: None  # noqa: E731
    return start_strategy_scan_run(
        blockcheck_feature=feature,
        create_strategy_scan_worker=create_worker,
        raw_target_input="YouTube.com",
        raw_protocol_value="tcp_https",
        raw_udp_scope_value="all",
        mode_index=0,
        starting_status_text="",
        parent=None,
        on_run_log_started=noop,
        on_strategy_started=noop,
        on_strategy_result=noop,
        on_log=noop,
        on_phase_changed=noop,
        on_continue_question=noop,
        on_finished=noop,
        **extra,
    )


class ResumeInfoTests(unittest.TestCase):
    def test_resume_info_counts_failures_for_normalized_target(self) -> None:
        feature = _feature(tested=30)
        info = plan_strategy_scan_resume(
            blockcheck_feature=feature,
            raw_target_input="YouTube.com",
            raw_protocol_value="tcp_https",
            raw_udp_scope_value="all",
            mode_index=0,
        )
        self.assertEqual((info.target, info.tested_count), ("youtube.com", 30))
        feature.count_resumable_strategies.assert_called_once_with(
            target="youtube.com", scan_protocol="tcp_https", udp_games_scope="all"
        )

    def test_count_resumable_strategies_uses_history_key(self) -> None:
        with patch(
            "blockcheck.strategy_search.history.count_recent_failures", return_value=7
        ) as count:
            tested = plans.count_resumable_strategies(
                target="discord.com", scan_protocol="tcp_https", udp_games_scope="all", now=5.0
            )
        self.assertEqual(tested, 7)
        count.assert_called_once_with("tcp_https|discord.com", now=5.0)

    def test_count_resumable_strategies_survives_broken_settings(self) -> None:
        with patch(
            "blockcheck.strategy_search.history.count_recent_failures", side_effect=RuntimeError("db")
        ):
            self.assertEqual(
                plans.count_resumable_strategies(target="discord.com", scan_protocol="tcp_https", udp_games_scope="all"),
                0,
            )


class FromStartPassThroughTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_workflow_passes_choice_to_worker(self) -> None:
        for from_start in (False, True):
            create_worker = MagicMock()
            _start(_feature(), create_worker, from_start=from_start)
            self.assertIs(create_worker.call_args.kwargs["from_start"], from_start)

    def test_workflow_continues_by_default(self) -> None:
        create_worker = MagicMock()
        _start(_feature(), create_worker)
        self.assertIs(create_worker.call_args.kwargs["from_start"], False)

    def test_worker_puts_choice_into_search_request(self) -> None:
        for from_start in (False, True):
            worker = StrategyScanWorker(
                target="discord.com",
                mode="quick",
                from_start=from_start,
                shutdown_sync=lambda *a, **k: None,
                start_run_log=lambda **k: SimpleNamespace(path=None),
                append_run_log=lambda *_a: None,
                close_run_log=lambda *_a: None,
                environment_factory=lambda **_kw: object(),
            )
            with patch(_SEARCH, return_value=StrategyScanReport(target="discord.com", total_tested=0)) as search:
                worker.run()
            self.assertIs(search.call_args.args[0].from_start, from_start)


class StartChoiceDialogTests(unittest.TestCase):
    """Окно выбора на настоящей странице подбора: какая кнопка что возвращает."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _page(self, tested: int):
        from blockcheck import public as blockcheck_public
        from blockcheck.ui.strategy_scan_page import StrategyScanPage

        class _Feature:
            build_selection_state = staticmethod(blockcheck_public.build_selection_state)
            build_protocol_ui_plan = staticmethod(blockcheck_public.build_protocol_ui_plan)
            build_udp_scope_hint_plan = staticmethod(blockcheck_public.build_udp_scope_hint_plan)
            build_idle_interaction_plan = staticmethod(blockcheck_public.build_idle_interaction_plan)
            build_running_interaction_plan = staticmethod(blockcheck_public.build_running_interaction_plan)
            plan_scan_start = staticmethod(blockcheck_public.plan_scan_start)

            @staticmethod
            def count_resumable_strategies(**_kwargs):
                return tested

        page = StrategyScanPage(blockcheck_feature=_Feature(), create_strategy_scan_worker=lambda **_kw: None)
        self.addCleanup(page.deleteLater)
        return page

    def _ask(self, page, press):
        from qfluentwidgets import MessageBox, PushButton

        seen: list[str] = []

        def fake_exec(box):
            seen.append(box.contentLabel.text())
            if press == "restart":
                [button] = [b for b in box.findChildren(PushButton) if b.text() == "Начать заново"]
                button.click()
            elif press == "continue":
                box.yesButton.click()
            else:
                box.cancelButton.click()
            # Окно закрывается после короткой анимации затухания — дождаться её,
            # как это делает настоящий цикл событий внутри exec().
            from PyQt6.QtTest import QTest

            QTest.qWait(300)
            return box.result()

        with patch.object(MessageBox, "exec", new=fake_exec):
            return page._ask_scan_start_choice(), seen

    def test_no_history_starts_without_asking(self) -> None:
        answer, seen = self._ask(self._page(tested=0), "restart")
        self.assertIs(answer, False)
        self.assertEqual(seen, [])

    def test_buttons_map_to_continue_restart_and_cancel(self) -> None:
        page = self._page(tested=30)
        answer, seen = self._ask(page, "continue")
        self.assertIs(answer, False)
        self.assertIn("уже проверено стратегий: 30", seen[0])
        self.assertIs(self._ask(page, "restart")[0], True)
        self.assertIsNone(self._ask(page, "cancel")[0])


class StopTextTests(unittest.TestCase):
    def test_cancelled_outcome_mentions_the_choice(self) -> None:
        report = SimpleNamespace(target="discord.com", total_tested=3, total_available=100, cancelled=True)
        outcome = plans.build_panel_outcome(report, [])
        self.assertEqual(outcome.kind, "cancelled")
        self.assertIn("начать заново", outcome.detail)


if __name__ == "__main__":
    unittest.main()
