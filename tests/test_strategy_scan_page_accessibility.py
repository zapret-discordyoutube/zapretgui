import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import CaptionLabel, PushButton

from blockcheck import public as blockcheck_public
from blockcheck.ui.strategy_scan_page import StrategyScanPage
from blockcheck.ui.strategy_scan_page_results_workflow import (
    apply_finished_scan,
    apply_phase_change,
    apply_strategy_started_progress,
)
from blockcheck.ui.strategy_scan_page_runtime_helpers import apply_language_plan_ui


class _BlockcheckFeatureStub:
    build_selection_state = staticmethod(blockcheck_public.build_selection_state)
    build_protocol_ui_plan = staticmethod(blockcheck_public.build_protocol_ui_plan)
    build_udp_scope_hint_plan = staticmethod(blockcheck_public.build_udp_scope_hint_plan)
    build_idle_interaction_plan = staticmethod(blockcheck_public.build_idle_interaction_plan)
    build_running_interaction_plan = staticmethod(blockcheck_public.build_running_interaction_plan)
    plan_scan_start = staticmethod(blockcheck_public.plan_scan_start)


class StrategyScanPageAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_main_controls_are_named_for_screen_reader(self) -> None:
        page = StrategyScanPage(
            blockcheck_feature=_BlockcheckFeatureStub(),
            create_strategy_scan_worker=lambda *_args, **_kwargs: None,
        )
        self.addCleanup(page.deleteLater)

        self.assertEqual(page._protocol_combo.accessibleName(), "Что должно заработать, выбрано: Сайты и приложения")
        self.assertEqual(
            page._protocol_combo.property("screenReaderStateText"),
            "Что должно заработать, выбрано: Сайты и приложения",
        )
        self.assertIn("для чего подобрать стратегию", page._protocol_combo.accessibleDescription())
        self.assertEqual(page._games_scope_combo.accessibleName(), "Адреса игр, выбрано: Все списки адресов (по умолчанию)")
        self.assertIn("онлайн-игр", page._games_scope_combo.accessibleDescription())
        self.assertEqual(page._mode_combo.accessibleName(), "Тщательность подбора, выбрано: Быстро · 30")
        self.assertIn("сколько стратегий", page._mode_combo.accessibleDescription())
        self.assertEqual(page._target_input.accessibleName(), "Цель подбора стратегии")
        self.assertIn("домен или STUN-цель", page._target_input.accessibleDescription())
        self.assertEqual(
            page._target_label.property("screenReaderStateText"),
            "Поле подбора стратегии: Какой сайт проверить:",
        )
        self.assertEqual(page._quick_domain_btn.accessibleName(), "Быстрый выбор цели")
        self.assertEqual(page._start_btn.text(), "Найти рабочую стратегию")
        self.assertEqual(page._start_btn.accessibleName(), "Начать подбор стратегии")
        self.assertEqual(page._stop_btn.accessibleName(), "Остановить подбор стратегии")
        self.assertEqual(page._progress_bar.accessibleName(), "Ход подбора стратегии: не выполняется")
        self.assertIn("Показывает", page._progress_bar.accessibleDescription())
        self.assertTrue(page._status_label.accessibleName().startswith("Статус подбора стратегии: "))
        self.assertEqual(page._results_view.accessibleName(), "Результаты подбора стратегии: пока нет результатов")
        self.assertEqual(page._log_btn.accessibleName(), "Открыть подробный лог подбора стратегии")
        self.assertEqual(page._prepare_support_btn.accessibleName(), "Подготовить обращение по подбору стратегии")
        self.assertEqual(
            page._support_status_label.property("screenReaderStateText"),
            "Статус обращения по подбору стратегии: нет статуса",
        )

    def test_protocol_tiles_are_named_and_selectable_from_keyboard(self) -> None:
        page = StrategyScanPage(
            blockcheck_feature=_BlockcheckFeatureStub(),
            create_strategy_scan_worker=lambda *_args, **_kwargs: None,
        )
        self.addCleanup(page.deleteLater)
        tiles = page._protocol_combo.tiles()

        self.assertEqual(tiles[0].accessibleName(), "Что должно заработать: Сайты и приложения, выбрано")
        self.assertEqual(tiles[1].accessibleName(), "Что должно заработать: Голосовые звонки, не выбрано")

        tiles[2].click()

        self.assertEqual(page._protocol_combo.currentData(), "udp_games")
        self.assertTrue(page._games_scope_combo.isVisibleTo(page))
        self.assertFalse(page._target_input.isVisibleTo(page))
        self.assertEqual(tiles[2].accessibleName(), "Что должно заработать: Онлайн-игры, выбрано")

    def test_log_opens_in_dialog_with_collected_lines(self) -> None:
        page = StrategyScanPage(
            blockcheck_feature=_blockcheck_feature(),
            create_strategy_scan_worker=lambda *_args, **_kwargs: None,
        )
        self.addCleanup(page.deleteLater)
        page._on_log("первая строка")
        page._on_log("вторая строка")

        with patch("blockcheck.ui.strategy_scan_page.show_log_report_dialog") as show_dialog:
            page._log_btn.click()

        show_dialog.assert_called_once()
        kwargs = show_dialog.call_args.kwargs
        self.assertEqual(kwargs["text"], "первая строка\nвторая строка")
        self.assertTrue(kwargs["scroll_to_end"])

    def test_quick_target_menu_items_are_named_for_screen_reader(self) -> None:
        page = StrategyScanPage(
            blockcheck_feature=_BlockcheckFeatureStub(),
            create_strategy_scan_worker=lambda *_args, **_kwargs: None,
        )
        self.addCleanup(page.deleteLater)
        captured = []

        with patch(
            "blockcheck.ui.strategy_scan_page.exec_popup_menu",
            side_effect=lambda menu, *_args, **_kwargs: captured.append(menu),
        ):
            page._open_quick_targets_menu(
                SimpleNamespace(
                    options=("discord.com", "youtube.com"),
                    current_value="youtube.com",
                )
            )

        self.assertEqual(len(captured), 1)
        menu = captured[0]
        self.assertEqual(
            menu.view.item(0).data(Qt.ItemDataRole.AccessibleTextRole),
            "Быстрая цель: discord.com, не выбрана",
        )
        self.assertEqual(
            menu.view.item(1).data(Qt.ItemDataRole.AccessibleTextRole),
            "Быстрая цель: youtube.com, выбрана",
        )

    def test_runtime_status_updates_state_text_for_screen_reader(self) -> None:
        label = CaptionLabel("Подсказка")

        apply_phase_change(status_label=label, phase="Проверяется стратегия TLS fake")

        # Видимый ход подбора — в шагах панели; подпись не прыгает.
        self.assertEqual(label.text(), "Подсказка")
        self.assertEqual(
            label.property("screenReaderStateText"),
            "Статус подбора стратегии: Проверяется стратегия TLS fake",
        )

    def test_progress_bar_reads_runtime_state_for_screen_reader(self) -> None:
        progress_bar = _FakeProgressBar()
        status_label = CaptionLabel()

        apply_strategy_started_progress(
            blockcheck_feature=_ProgressFeatureStub(),
            strategy_name="TLS fake",
            index=1,
            total=3,
            result_rows=[],
            progress_bar=progress_bar,
            status_label=status_label,
            done_count=1,
        )

        self.assertEqual(progress_bar.accessibleName(), "Ход подбора стратегии: выполняется")
        self.assertEqual(progress_bar.property("screenReaderStateText"), "Ход подбора стратегии: выполняется")

        apply_finished_scan(
            blockcheck_feature=_ProgressFeatureStub(),
            finish_plan=SimpleNamespace(
                total_available=3,
                total_count=3,
                status_text="Подбор завершён",
                support_status_code="ready_after_error",
                cancelled=False,
            ),
            reset_ui=lambda: None,
            scan_protocol="tcp",
            progress_bar=progress_bar,
            status_label=status_label,
            set_support_status=lambda _text: None,
            parent_widget=None,
        )

        self.assertEqual(progress_bar.accessibleName(), "Ход подбора стратегии: не выполняется")
        self.assertEqual(progress_bar.property("screenReaderStateText"), "Ход подбора стратегии: не выполняется")

    def test_run_start_restores_empty_result_and_log_screen_reader_states(self) -> None:
        from ui.accessibility import set_state_text

        page = StrategyScanPage(
            blockcheck_feature=_BlockcheckFeatureStub(),
            create_strategy_scan_worker=lambda *_args, **_kwargs: _WorkerStub(),
        )
        self.addCleanup(page.deleteLater)
        page._strategy_scan_run_runtime = _RunRuntimeStub()
        set_state_text(page._results_view, "Старая строка подбора стратегии")
        page._log_lines.append("Старый лог подбора стратегии")
        # До первого подбора блока «Подробный лог / Подготовить обращение» нет.
        self.assertTrue(page._log_card.isHidden())

        page._on_start()

        self.assertFalse(page._log_card.isHidden())

        self.assertEqual(page._results_view.row_count(), 0)
        self.assertEqual(page._results_view.accessibleName(), "Результаты подбора стратегии: пока нет результатов")
        self.assertEqual(list(page._log_lines), [])
        self.assertEqual(page._scan_panel.state, "running")

    def test_language_refresh_updates_field_labels(self) -> None:
        protocol_label = CaptionLabel("Old")
        start_btn = PushButton()
        expand_btn = PushButton()

        apply_language_plan_ui(
            blockcheck_feature=blockcheck_public,
            language="ru",
            log_btn=expand_btn,
            protocol_label=protocol_label,
            mode_label=CaptionLabel(),
            mode_combo=_ComboStub(3),
            target_label=CaptionLabel(),
            start_btn=start_btn,
            stop_btn=PushButton(),
            prepare_support_btn=PushButton(),
            protocol_combo=_ComboStub(3),
            games_scope_label=CaptionLabel("Old UDP"),
            games_scope_combo=_ComboStub(2),
            quick_domain_btn=PushButton(),
        )

        self.assertEqual(protocol_label.text(), "Что должно заработать?")
        self.assertEqual(
            protocol_label.property("screenReaderStateText"),
            "Поле подбора стратегии: Что должно заработать?",
        )
        self.assertEqual(start_btn.text(), "Найти рабочую стратегию")
        self.assertEqual(expand_btn.text(), "Подробный лог")

    def test_cards_have_no_headers_to_save_space(self) -> None:
        page = StrategyScanPage(
            blockcheck_feature=_BlockcheckFeatureStub(),
            create_strategy_scan_worker=lambda *_args, **_kwargs: None,
        )
        self.addCleanup(page.deleteLater)

        for card in (page._control_card, page._results_card, page._log_card):
            self.assertIsNone(card._title_label)


def _blockcheck_feature():
    from app.feature_facades.blockcheck import BlockcheckFeature

    return BlockcheckFeature(presets_feature=None, profile_feature=None)


class _ProgressFeatureStub:
    def build_progress_plan(self, **_kwargs):
        return SimpleNamespace(total=3, status_text="Проверяется стратегия TLS fake")


class _SignalStub:
    def connect(self, _callback) -> None:
        pass


class _WorkerStub:
    def __init__(self) -> None:
        self.run_log_started = _SignalStub()
        self.strategy_started = _SignalStub()
        self.strategy_result = _SignalStub()
        self.scan_log = _SignalStub()
        self.phase_changed = _SignalStub()
        self.continue_question = _SignalStub()
        self.strategy_args_started = _SignalStub()
        self.stage_changed = _SignalStub()
        self.scan_finished = _SignalStub()


class _RunRuntimeStub:
    worker = None

    def is_running(self) -> bool:
        return False

    def start_qobject_worker(self, *, parent, worker_factory) -> None:
        self.worker = worker_factory(1)


class _TitleCardStub:
    def __init__(self) -> None:
        self.title = ""

    def set_title(self, title: str) -> None:
        self.title = str(title)


class _ComboStub:
    def __init__(self, count: int) -> None:
        self.items = [""] * int(count)

    def setItemText(self, index: int, text: str) -> None:  # noqa: N802
        self.items[int(index)] = str(text)


class _FakeProgressBar:
    def __init__(self) -> None:
        self.properties = {}
        self.accessible_name = ""
        self.accessible_description = ""
        self._value = 0
        self._maximum = 100

    def setRange(self, _minimum: int, maximum: int) -> None:  # noqa: N802
        self._maximum = int(maximum)

    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:  # noqa: N802
        self._value = int(value)

    def maximum(self) -> int:
        return self._maximum

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


if __name__ == "__main__":
    unittest.main()
