"""Горячие действия пишут своё время в журнал (UiMetric), а сборка списка
профилей не пересчитывает папку одного и того же текста."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch


class HotActionTimingTests(unittest.TestCase):
    def test_preset_save_worker_logs_its_duration_even_on_failure(self) -> None:
        from presets import raw_preset_loader
        from presets.raw_preset_loader import RawPresetSaveWorker

        save = Mock(side_effect=RuntimeError("диск занят"))
        worker = RawPresetSaveWorker(1, save, file_name="P.txt", source_text="--a\n", publish_content_changed=False)
        with patch.object(raw_preset_loader, "log_ui_timing_since") as timing:
            worker.run()

        timing.assert_called_once()
        args, kwargs = timing.call_args
        self.assertEqual(args[:3], ("worker", "presets", "preset_save.run"))
        self.assertTrue(kwargs["important"])

    def test_strategy_apply_worker_logs_its_duration(self) -> None:
        from profile import profile_setup_loader
        from profile.profile_setup_loader import ProfileStrategyApplyWorker

        worker = ProfileStrategyApplyWorker(
            1,
            Mock(return_value=None),
            Mock(return_value=None),
            profile_key="uid:1",
            strategy_id="s",
        )
        with patch.object(profile_setup_loader, "log_ui_timing_since") as timing:
            worker.run()

        self.assertEqual(timing.call_args.args[:3], ("worker", "profile", "strategy_apply.run"))


class FolderClassificationCacheTests(unittest.TestCase):
    def test_same_text_is_classified_once(self) -> None:
        from folders import defaults

        defaults._classify_profile_folder_text.cache_clear()
        text = "YouTube --hostlist=lists/youtube.txt"
        self.assertEqual(defaults.classify_profile_folder(text), "youtube")
        self.assertEqual(defaults.classify_profile_folder("  " + text.upper() + " "), "youtube")

        info = defaults._classify_profile_folder_text.cache_info()
        # Регистр и пробелы нормализуются до кэша — второй вызов попадает в кэш.
        self.assertEqual((info.misses, info.hits), (1, 1))


if __name__ == "__main__":
    unittest.main()
