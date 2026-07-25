from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


class StartupPostInitAutostartTests(unittest.TestCase):
    def test_post_init_uses_runtime_snapshot_in_autostart_callback(self) -> None:
        from main import startup_coordinator
        from main.startup_coordinator import StartupCoordinator

        class Runtime:
            def __init__(self) -> None:
                self.autostart_calls: list[str | None] = []
                self.snapshot = Mock(
                    return_value=SimpleNamespace(launch_method="zapret1_mode")
                )

            def start_autostart(self, launch_method: str | None = None) -> None:
                self.autostart_calls.append(launch_method)

        runtime = Runtime()
        window_shell = SimpleNamespace(
            start_in_tray=False,
            set_status=Mock(),
            mark_startup_core_ready=Mock(),
            mark_startup_post_init_done=Mock(),
            init_theme_manager=Mock(),
        )
        coordinator = StartupCoordinator(
            runtime_feature=runtime,
            tray_feature=SimpleNamespace(init=Mock(), is_initialized=Mock(return_value=False)),
            window_shell=window_shell,
            log_startup_metric=Mock(),
            migrate_gui_autostart=Mock(return_value=False),
        )
        scheduled: list[tuple[int, object]] = []

        with patch.object(
            startup_coordinator.QTimer,
            "singleShot",
            side_effect=lambda delay_ms, callback: scheduled.append((int(delay_ms), callback)),
        ):
            coordinator._post_init_tasks()

        self.assertEqual(runtime.autostart_calls, [])
        self.assertEqual(len(scheduled), 1)
        window_shell.mark_startup_post_init_done.assert_called_once_with("post_init_scheduled:auto")

        _delay_ms, callback = scheduled.pop(0)
        callback()

        self.assertEqual(runtime.autostart_calls, ["zapret1_mode"])
        runtime.snapshot.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
