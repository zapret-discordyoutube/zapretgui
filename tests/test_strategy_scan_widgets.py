"""Панель хода и итога подбора, плитки, переключатели и тексты итога."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from blockcheck.scan_models import StrategyScanReport
from blockcheck.strategy_scan_page_plans import build_panel_outcome
from blockcheck.ui.fun_texts import phrases, show_state_line, state_line, state_lines, strategy_phrases, technique_kind
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

    def test_every_block_has_its_own_big_pool(self) -> None:
        # Фразы идут без повторов, пока не кончится набор: чтобы от проверки
        # к проверке текст был разный, в каждом блоке их не меньше 50 и ни одна
        # не встречается в двух блоках. Строка не переносится — фразы короткие.
        kinds = (
            "scan_network", "scan_baseline", "scan_control", "fake", "split", "disorder", "syndata", "oob",
            "seqovl", "hostfake", "udp", "scan_generic", "scan_found", "blockcheck", "dns", "dns_servers",
            "dns_lookup",
        )  # fmt: skip
        for language in ("ru", "en"):
            seen: dict[str, str] = {}
            for kind in kinds:
                pool = phrases(kind, language)
                self.assertGreaterEqual(len(pool), 50, (language, kind))
                for phrase in pool:
                    self.assertLessEqual(len(phrase), 70, phrase)
                    self.assertNotIn(phrase, seen, (language, kind, seen.get(phrase)))
                    seen[phrase] = kind
        self.assertNotEqual(phrases("blockcheck", "ru"), phrases("blockcheck", "en"))

    def test_every_verdict_state_has_thirty_jokes_of_its_own(self) -> None:
        # Под заголовком итога каждый раз новая шутка: в наборе состояния их не меньше 30,
        # и одна шутка не стоит в двух наборах. Английскому интерфейсу строка не показывается.
        kinds = (
            "stopped", "error", "bc_idle", "bc_ok", "bc_unknown", "bc_problems",
            "dns_idle", "dns_ok", "dns_spoofed", "dns_partial", "dns_unverified",
            "srv_idle", "srv_empty", "srv_intercepted", "srv_spoofed", "srv_shaky", "srv_closed",
            "srv_notes", "srv_ok",
        )  # fmt: skip
        seen: dict[str, str] = {}
        for kind in kinds:
            pool = state_lines(kind)
            self.assertGreaterEqual(len(pool), 30, kind)
            for line in pool:
                self.assertLessEqual(len(line), 60, line)
                self.assertNotIn(line, seen, (kind, seen.get(line)))
                seen[line] = kind
            self.assertIn(state_line(kind), pool)
            self.assertEqual(state_lines(kind, "en"), ())
        self.assertGreater(len({state_line("bc_idle") for _ in range(40)}), 5)
        self.assertEqual(state_line("нет такого"), "")

    def test_state_line_is_shown_in_a_stopped_ticker(self) -> None:
        from ui.widgets.fun import FunTicker

        ticker = FunTicker()
        self.addCleanup(ticker.deleteLater)
        show_state_line(ticker, "bc_ok")
        self.assertIn(ticker.text(), state_lines("bc_ok"))
        self.assertFalse(ticker.is_running())
        self.assertFalse(ticker.isHidden())
        show_state_line(ticker, "bc_ok", "en")
        self.assertEqual(ticker.text(), "")
        self.assertTrue(ticker.isHidden())

    def test_strategy_gets_only_its_own_phrases(self) -> None:
        self.assertEqual(strategy_phrases("--lua-desync=multisplit"), phrases("split"))
        self.assertEqual(strategy_phrases("--lua-desync=wssize", "en"), phrases("scan_generic", "en"))


if __name__ == "__main__":
    unittest.main()
