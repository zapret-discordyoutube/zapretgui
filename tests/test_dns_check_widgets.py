"""Итог и список доменов вкладки «DNS подмена»."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from dns.ui.dns_check_widgets import DnsDomainsView, DnsSummaryPanel, summarize_dns_results

SPOOFED = {
    "summary": {"dns_poisoning_detected": True},
    "domains": {
        "www.youtube.com": {"state": "ok", "system_ips": ["1.1.1.1"], "reference_ips": ["1.1.1.1"]},
        "discord.com": {"state": "spoofed", "reason": "заглушка", "system_ips": ["195.82.146.214"], "reference_ips": ["2.2.2.2"]},
    },
}


class SummarizeTests(unittest.TestCase):
    def test_kinds(self) -> None:
        spoofed = summarize_dns_results(SPOOFED)
        self.assertEqual(spoofed["kind"], "spoofed")
        self.assertIn("1 сайта", spoofed["title"])
        self.assertIn("Настройка DNS", spoofed["detail"])
        honest = {"domains": {"a": {"state": "ok"}}}
        self.assertEqual(summarize_dns_results(honest)["kind"], "ok")
        self.assertEqual(summarize_dns_results({"domains": {"a": {"state": "unknown"}}})["kind"], "partial")
        self.assertEqual(summarize_dns_results({"stopped": True})["kind"], "stopped")
        self.assertEqual(summarize_dns_results({})["kind"], "error")


class WidgetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_panel_offers_dns_settings_only_when_spoofed(self) -> None:
        opened = []
        panel = DnsSummaryPanel(on_open_dns_settings=lambda: opened.append(True))
        self.addCleanup(panel.deleteLater)
        panel.show_results({"domains": {"a": {"state": "ok"}}})
        self.assertFalse(panel._actions_host.isVisibleTo(panel))
        panel.show_results(SPOOFED)
        self.assertTrue(panel._actions_host.isVisibleTo(panel))
        self.assertEqual(panel.mascot.mood(), "alarm")
        panel.open_settings_btn.click()
        self.assertEqual(opened, [True])

    def test_domains_sorted_spoofed_first(self) -> None:
        view = DnsDomainsView()
        self.addCleanup(view.deleteLater)
        view.show_results(SPOOFED)
        rows = view.rows()
        self.assertEqual([row.state for row in rows], ["spoofed", "ok"])
        self.assertIn("подменено 1", view.property("screenReaderStateText"))


if __name__ == "__main__":
    unittest.main()
