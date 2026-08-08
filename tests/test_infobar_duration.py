from __future__ import annotations

import inspect
import unittest


def _fake_info_bar(calls: list[dict[str, object]]):
    class FakeInfoBar:
        @classmethod
        def success(cls, *args, **kwargs):
            calls.append({"name": "success", "args": args, "kwargs": kwargs})
            return "bar"

        @classmethod
        def info(cls, *args, **kwargs):
            calls.append({"name": "info", "args": args, "kwargs": kwargs})
            return "bar"

        @classmethod
        def warning(cls, *args, **kwargs):
            calls.append({"name": "warning", "args": args, "kwargs": kwargs})
            return "bar"

        @classmethod
        def error(cls, *args, **kwargs):
            calls.append({"name": "error", "args": args, "kwargs": kwargs})
            return "bar"

    return FakeInfoBar


class InfoBarDurationTests(unittest.TestCase):
    def test_every_infobar_level_lasts_at_least_five_seconds(self) -> None:
        from ui.infobar_duration import install_infobar_min_duration

        calls: list[dict[str, object]] = []
        FakeInfoBar = _fake_info_bar(calls)

        install_infobar_min_duration(FakeInfoBar)

        for factory in (FakeInfoBar.success, FakeInfoBar.info, FakeInfoBar.warning, FakeInfoBar.error):
            self.assertEqual(factory("Заголовок", "Текст", duration=1000), "bar")
            self.assertEqual(calls[-1]["kwargs"]["duration"], 5000)

            factory("Заголовок", "Текст")
            self.assertEqual(calls[-1]["kwargs"]["duration"], 5000)

    def test_infobar_keeps_long_and_persistent_duration(self) -> None:
        from ui.infobar_duration import install_infobar_min_duration

        calls: list[dict[str, object]] = []
        FakeInfoBar = _fake_info_bar(calls)

        install_infobar_min_duration(FakeInfoBar)

        FakeInfoBar.warning("Готово", "Долго", duration=10000)
        self.assertEqual(calls[-1]["kwargs"]["duration"], 10000)

        FakeInfoBar.error("Готово", "Постоянно", duration=-1)
        self.assertEqual(calls[-1]["kwargs"]["duration"], -1)

    def test_infobar_clamps_positional_duration_argument(self) -> None:
        from ui.infobar_duration import install_infobar_min_duration

        calls: list[dict[str, object]] = []
        FakeInfoBar = _fake_info_bar(calls)

        install_infobar_min_duration(FakeInfoBar)

        FakeInfoBar.warning("Готово", "Позиционно", "orient", True, 3000)
        self.assertEqual(calls[-1]["args"][4], 5000)

    def test_repeated_install_patches_only_once(self) -> None:
        from ui.infobar_duration import install_infobar_min_duration

        calls: list[dict[str, object]] = []
        FakeInfoBar = _fake_info_bar(calls)

        install_infobar_min_duration(FakeInfoBar)
        install_infobar_min_duration(FakeInfoBar)

        FakeInfoBar.warning("Готово", "Один раз", duration=1000)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[-1]["kwargs"]["duration"], 5000)

    def test_qt_runtime_installs_infobar_duration_hook(self) -> None:
        from main.qt_runtime import ensure_qt_runtime

        source = inspect.getsource(ensure_qt_runtime)

        self.assertIn("install_infobar_min_duration", source)


if __name__ == "__main__":
    unittest.main()
