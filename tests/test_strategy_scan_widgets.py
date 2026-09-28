"""Панель хода и итога подбора, плитки, переключатели и тексты итога."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from blockcheck.scan_models import StrategyScanReport
from blockcheck.strategy_scan_page_plans import build_panel_outcome
from blockcheck.ui.fun_texts import phrases, strategy_phrases, technique_kind
from blockcheck.ui.strategy_scan_widgets import ChoiceRadios, ChoiceTiles, ScanProgressPanel


class ChoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_tiles_behave_like_a_combo(self) -> None:
        tiles = ChoiceTiles()
        self.addCleanup(tiles.deleteLater)
        changes = []
        tiles.currentIndexChanged.connect(changes.append)
        tiles.addItem("Сайты", userData="tcp_https")
        tiles.addItem("Игры", userData="udp_games")
        self.assertEqual(tiles.currentData(), "tcp_https")
        self.assertEqual(tiles.findData("udp_games"), 1)
        tiles.tiles()[1].click()
        self.assertEqual(tiles.currentIndex(), 1)
        self.assertEqual(changes[-1], 1)
        self.assertTrue(tiles.tiles()[1].is_selected())
        self.assertFalse(tiles.tiles()[0].is_selected())
        tiles.setItemText(1, "Онлайн-игры")
        self.assertEqual(tiles.currentText(), "Онлайн-игры")

    def test_radios_behave_like_a_combo(self) -> None:
        radios = ChoiceRadios()
        self.addCleanup(radios.deleteLater)
        for text, value in (("Быстро", "quick"), ("Тщательно", "standard")):
            radios.addItem(text, userData=value)
        radios.buttons()[1].setChecked(True)
        self.assertEqual(radios.currentData(), "standard")
        radios.setCurrentIndex(0)
        self.assertTrue(radios.buttons()[0].isChecked())


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_panel_walks_through_states(self) -> None:
        panel = ScanProgressPanel()
        self.addCleanup(panel.deleteLater)
        self.assertEqual(panel.state, "idle")
        self.assertFalse(panel.steps.isVisibleTo(panel))

        panel.show_running("discord.com")
        self.assertEqual(panel.state, "running")
        self.assertTrue(panel.steps.isVisibleTo(panel))
        panel.set_step("network", "done", "Интернет есть")
        self.assertEqual(panel.steps.status("network"), "done")
        panel.note_found(1)
        self.assertEqual(panel.found_badge.value(), 1)

        requested = []
        panel.apply_best_clicked.connect(lambda: requested.append(True))
        panel.show_outcome(kind="found", title="Ура!", detail="…", best_text="Лучшая — «X»")
        self.assertTrue(panel._actions_host.isVisibleTo(panel))
        self.assertFalse(panel.found_badge.isVisibleTo(panel))
        panel.apply_best_btn.click()
        self.assertEqual(requested, [True])

        panel.show_outcome(kind="address_block", title="Тут стратегии бессильны", detail="порт 443")
        self.assertFalse(panel._actions_host.isVisibleTo(panel))
        self.assertEqual(panel.mascot.mood(), "alarm")
        self.assertEqual(panel.property("screenReaderStateText"), "Подбор стратегии: Тут стратегии бессильны")


class OutcomeTests(unittest.TestCase):
    def _report(self, **fields):
        base = dict(target="discord.com", total_tested=30, total_available=463)
        base.update(fields)
        return StrategyScanReport(**base)

    def test_found_picks_fastest_and_celebrates(self) -> None:
        rows = [
            {"success": False, "name": "a"},
            {"success": True, "name": "slow", "time_ms": 300.0},
            {"success": True, "name": "fast", "time_ms": 90.0},
        ]
        outcome = build_panel_outcome(self._report(), rows)
        self.assertEqual(outcome.kind, "found")
        self.assertEqual(outcome.best_index, 2)
        self.assertIn("fast", outcome.best_text)
        self.assertIn("2 надёжные стратегии", outcome.title)
        self.assertTrue(outcome.celebrate)

    def test_single_found_uses_singular(self) -> None:
        outcome = build_panel_outcome(self._report(), [{"success": True, "name": "x", "time_ms": 1.0}])
        self.assertIn("Нашлась 1 надёжная стратегия", outcome.title)

    def test_stops_keep_the_precise_reason(self) -> None:
        outcome = build_panel_outcome(
            self._report(cancelled=True, stop_kind="address_block", fatal_error="порт 443 закрыт, порт 80 открыт"),
            [],
        )
        self.assertEqual(outcome.kind, "address_block")
        self.assertEqual(outcome.detail, "порт 443 закрыт, порт 80 открыт")
        self.assertEqual(outcome.title, "Тут стратегии бессильны")

    def test_plural_forms(self) -> None:
        rows = [{"success": True, "name": f"s{i}", "time_ms": float(i + 1)} for i in range(24)]
        self.assertIn("Нашлось 24 надёжные стратегии", build_panel_outcome(self._report(), rows).title)
        rows = [{"success": True, "name": f"s{i}", "time_ms": float(i + 1)} for i in range(21)]
        self.assertIn("Нашлась 21 надёжная стратегия", build_panel_outcome(self._report(), rows).title)
        rows = [{"success": True, "name": f"s{i}", "time_ms": float(i + 1)} for i in range(12)]
        self.assertIn("Нашлось 12 надёжных стратегий", build_panel_outcome(self._report(), rows).title)

    def test_forced_scan_does_not_celebrate_or_offer_apply(self) -> None:
        rows = [{"success": True, "name": "x", "time_ms": 10.0}]
        outcome = build_panel_outcome(self._report(baseline_accessible=True), rows)
        self.assertEqual(outcome.kind, "open")
        self.assertFalse(outcome.celebrate)
        self.assertEqual(outcome.best_text, "")
        self.assertIn("ни при чём", outcome.title)

    def test_switching_back_to_sites_drops_stun_target(self) -> None:
        from blockcheck.strategy_scan_page_plans import build_protocol_ui_plan

        plan = build_protocol_ui_plan(scan_protocol="tcp_https", current_value="stun.cloudflare.com:3478")
        self.assertEqual(plan.normalized_target, "discord.com")
        kept = build_protocol_ui_plan(scan_protocol="tcp_https", current_value="youtube.com")
        self.assertEqual(kept.normalized_target, "youtube.com")

    def test_open_without_bypass_and_not_found(self) -> None:
        self.assertEqual(build_panel_outcome(self._report(total_tested=0, baseline_accessible=True), []).kind, "open")
        not_found = build_panel_outcome(self._report(), [{"success": False}])
        self.assertEqual(not_found.kind, "not_found")
        self.assertIn("30 из 463", not_found.detail)


class FunTextsTests(unittest.TestCase):
    def test_technique_phrases(self) -> None:
        self.assertEqual(technique_kind("--lua-desync=multisplit:pos=1"), "split")
        self.assertEqual(technique_kind("--lua-desync=fake\n--lua-desync=multidisorder"), "disorder")
        self.assertEqual(technique_kind("--lua-desync=hostfakesplit:x"), "hostfake")
        self.assertEqual(technique_kind("--lua-desync=wssize"), "scan_generic")
        self.assertIn(phrases("split")[0], strategy_phrases("--lua-desync=multisplit"))
        self.assertTrue(phrases("dns", "en"))
        self.assertTrue(all(isinstance(item, str) and item for item in phrases("blockcheck")))


if __name__ == "__main__":
    unittest.main()
