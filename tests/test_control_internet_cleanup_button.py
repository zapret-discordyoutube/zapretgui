from __future__ import annotations

import inspect
import unittest

import presets.ui.control.zapret1.page as zapret1_page
import presets.ui.control.zapret2.page as zapret2_page
from presets.ui.control.quick_actions import quick_action_specs
from presets.ui.control.windows_features.runtime import ControlPageWindowsFeatureMixin


class ControlInternetCleanupButtonTests(unittest.TestCase):
    def test_zapret_control_pages_add_internet_cleanup_action_tile(self) -> None:
        for prefix in ("page.winws1_control", "page.winws2_control"):
            keys = [spec.key for spec in quick_action_specs(prefix)]
            self.assertIn("internet_cleanup", keys)

        for page_cls in (zapret1_page.Zapret1ModeControlPage, zapret2_page.Zapret2ModeControlPage):
            source = inspect.getsource(page_cls._build_ui)
            self.assertIn("build_quick_actions(", source)
            self.assertIn("on_open_internet_cleanup=self._on_internet_cleanup_clicked", source)
            self.assertIn("self.internet_cleanup_card = quick_actions.internet_cleanup_card", source)

    def test_windows_feature_mixin_waits_for_internet_cleanup_worker_on_cleanup(self) -> None:
        source = inspect.getsource(ControlPageWindowsFeatureMixin._stop_internet_cleanup_worker)

        self.assertIn("blocking=True", source)
        self.assertIn("wait_timeout_ms=5000", source)
        self.assertIn("Internet cleanup worker", source)

    def test_second_click_is_ignored_while_cleanup_runs_and_tile_stays_enabled(self) -> None:
        # Выключать нажатую плитку нельзя: фокус уходит дальше и страница прокручивается сама.
        calls: list[str] = []

        class _Runtime:
            running = True

            def is_running(self) -> bool:
                return self.running

        class _Tile:
            def setEnabled(self, _enabled: bool) -> None:  # noqa: N802
                calls.append("tile disabled")

        class _Page(ControlPageWindowsFeatureMixin):
            _ui_language = "ru"

            def __init__(self) -> None:
                self._internet_cleanup_runtime = _Runtime()
                self.internet_cleanup_card = _Tile()

            def _confirm_windows_feature_action(self, _dialog_plan, toggle=None) -> bool:
                calls.append("confirm")
                return True

            def _start_internet_cleanup_worker(self) -> None:
                calls.append("start")

        page = _Page()
        page._on_internet_cleanup_clicked()
        self.assertEqual(calls, [])

        page._internet_cleanup_runtime.running = False
        page._on_internet_cleanup_clicked()
        self.assertEqual(calls, ["confirm", "start"])

    def test_result_stays_on_screen_long_enough_to_read(self) -> None:
        from presets.ui.control.control_page_runtime_shared import show_action_result_plan
        from windows_features.internet_cleanup import CleanupStep, run_internet_cleanup

        shown: list[tuple[str, dict]] = []

        class _InfoBar:
            @staticmethod
            def success(**kwargs) -> None:
                shown.append(("success", kwargs))

            @staticmethod
            def error(**kwargs) -> None:
                shown.append(("error", kwargs))

        def broken() -> str:
            raise OSError("нет доступа")

        for steps in ([CleanupStep("кэш DNS", lambda: "Кэш DNS очищен.")], [CleanupStep("кэш DNS", broken)]):
            show_action_result_plan(
                run_internet_cleanup(steps),
                parent_widget=None,
                set_status=lambda _text: None,
                info_bar_cls=_InfoBar,
            )

        self.assertEqual([level for level, _kwargs in shown], ["success", "error"])
        self.assertGreaterEqual(shown[0][1]["duration"], 5000)
        self.assertGreater(shown[1][1]["duration"], shown[0][1]["duration"])

    def test_plans_without_own_duration_keep_the_default_one(self) -> None:
        from types import SimpleNamespace

        from presets.ui.control.control_page_runtime_shared import show_action_result_plan

        shown: list[dict] = []
        plan = SimpleNamespace(level="warning", title="t", content="c", revert_checked=None, final_status="")
        show_action_result_plan(
            plan,
            parent_widget=None,
            set_status=lambda _text: None,
            info_bar_cls=SimpleNamespace(warning=lambda **kwargs: shown.append(kwargs)),
        )

        self.assertEqual(shown, [{"title": "t", "content": "c", "parent": None}])


if __name__ == "__main__":
    unittest.main()
