import unittest
from unittest.mock import patch

from orchestra import locked_strategies_manager as manager_module
from orchestra.locked_strategies_manager import LockedStrategiesManager


class OrchestraHistoryFlushTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = [1000.0]
        clock_patch = patch.object(manager_module.time, "monotonic", side_effect=lambda: self.clock[0])
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        write_patch = patch.object(manager_module, "set_orchestra_history_for_targets")
        self.write = write_patch.start()
        self.addCleanup(write_patch.stop)
        self.manager = LockedStrategiesManager()

    def test_events_are_batched_instead_of_written_one_by_one(self) -> None:
        for _ in range(50):
            self.manager.increment_history("youtube.com", 3, is_success=True)
        self.manager.increment_history("discord.com", 5, is_success=False)

        self.write.assert_not_called()

        self.clock[0] += manager_module.HISTORY_FLUSH_INTERVAL_SECONDS
        self.manager.increment_history("youtube.com", 3, is_success=False)

        self.write.assert_called_once()
        written = self.write.call_args.args[0]
        self.assertEqual(set(written), {"youtube.com", "discord.com"})
        self.assertEqual(written["youtube.com"]["3"], {"successes": 50, "failures": 1})

    def test_flush_writes_only_changed_targets_once(self) -> None:
        self.manager.update_history("a.com", 1, 2, 3)
        self.manager.flush_history()
        self.manager.flush_history()

        self.write.assert_called_once_with({"a.com": {"1": {"successes": 2, "failures": 3}}})

    def test_failed_write_keeps_targets_for_next_flush(self) -> None:
        self.manager.update_history("a.com", 1, 2, 3)
        self.write.side_effect = RuntimeError("disk busy")
        self.manager.flush_history()
        self.write.side_effect = None
        self.manager.flush_history()

        self.assertEqual(self.write.call_count, 2)
        self.assertEqual(set(self.write.call_args.args[0]), {"a.com"})

    def test_full_save_clears_pending_targets(self) -> None:
        self.manager.update_history("a.com", 1, 2, 3)
        with patch.object(manager_module, "set_orchestra_history") as save_all:
            self.manager.save_history()
        save_all.assert_called_once()
        self.manager.flush_history()

        self.write.assert_not_called()


    def test_history_keeps_recent_and_locked_targets_within_limit(self) -> None:
        self.manager.locked_by_askey["quic"]["1.1.1.1"] = 4
        with patch.object(manager_module, "MAX_HISTORY_TARGETS", 100), patch.object(
            manager_module, "set_orchestra_history"
        ) as save_all:
            self.manager.increment_history("1.1.1.1", 4, is_success=True)
            for index in range(150):
                self.manager.increment_history(f"10.0.0.{index}", 1, is_success=True)
            # Давняя цель снова встретилась — она свежая и не должна пропасть.
            self.manager.increment_history("10.0.0.5", 1, is_success=True)
            for index in range(150, 200):
                self.manager.increment_history(f"10.0.0.{index}", 1, is_success=True)

        history = self.manager.strategy_history
        self.assertLessEqual(len(history), 100)
        self.assertIn("1.1.1.1", history)
        self.assertIn("10.0.0.5", history)
        self.assertIn("10.0.0.199", history)
        self.assertNotIn("10.0.0.0", history)
        save_all.assert_called()


if __name__ == "__main__":
    unittest.main()
