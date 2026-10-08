"""Вкладки раздела BlockCheck начинаются одинаково: панель с талисманом, главной фразой и кнопками под ней."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QHBoxLayout
from qfluentwidgets import BodyLabel, PushButton, SubtitleLabel

from blockcheck.ui.check_results import BlockcheckSummaryPanel
from blockcheck.ui.strategy_scan_widgets import ScanProgressPanel
from dns.ui.dns_check_widgets import DnsSummaryPanel
from dns.ui.server_check_widgets import ServerCheckVerdictPanel
from ui.widgets.check_hero import MASCOT_SIZE, CheckHero


class TabHeroesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_every_tab_panel_has_the_same_mascot_title_and_margins(self) -> None:
        panels = {
            "BlockCheck": BlockcheckSummaryPanel(),
            "Подбор стратегии": ScanProgressPanel(),
            "Проверка домена": CheckHero(),
            "DNS-серверы": ServerCheckVerdictPanel(),
            "DNS подмена": DnsSummaryPanel(),
        }
        # Образец — вкладка «DNS-серверы»: у остальных талисман того же размера.
        sample = panels["DNS-серверы"].mascot.size()
        self.assertGreaterEqual(sample.width(), MASCOT_SIZE)
        for name, panel in panels.items():
            self.addCleanup(panel.deleteLater)
            with self.subTest(tab=name):
                self.assertEqual(panel.mascot.size(), sample)
                # Главная фраза — крупным шрифтом, как на вкладке-образце «DNS-серверы».
                self.assertIsInstance(panel.title_label, SubtitleLabel)
                # У итога BlockCheck подложка только у шапки: группы проблем лежат под ней без своей.
                margins = getattr(panel, "hero", panel).layout().contentsMargins()
                self.assertEqual((margins.left(), margins.top(), margins.right(), margins.bottom()), (16, 14, 16, 14))

    def test_panels_take_page_buttons_right_under_the_main_phrase(self) -> None:
        slots = {
            "BlockCheck": BlockcheckSummaryPanel().actions,
            "Подбор стратегии": ScanProgressPanel().run_actions,
            "DNS-серверы": ServerCheckVerdictPanel().actions,
            "DNS подмена": DnsSummaryPanel().actions,
        }
        for name, slot in slots.items():
            with self.subTest(tab=name):
                self.assertIsInstance(slot, QHBoxLayout)

    def test_domain_tab_hero_keeps_the_settings_card_calls(self) -> None:
        hero = CheckHero("fa5s.search-location")
        self.addCleanup(hero.deleteLater)
        hero.set_texts("Проверка домена или адреса", "Покажем пинг и ответы DNS.")
        row = QHBoxLayout()
        button = PushButton("Проверить")
        row.addWidget(button)
        hero.add_layout(row)
        note = BodyLabel("подсказка")
        hero.add_widget(note)

        self.assertEqual((hero.title_label.text(), hero.detail_label.text()), ("Проверка домена или адреса", "Покажем пинг и ответы DNS."))
        self.assertIs(button.parentWidget(), hero)
        self.assertIs(note.parentWidget(), hero)
        self.assertFalse(hero.icon.pixmap().isNull())


if __name__ == "__main__":
    unittest.main()
