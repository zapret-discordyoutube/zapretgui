"""Вкладка «DNS подмена»: о недоступном эталонном сервере сказано в итоге."""

from __future__ import annotations

import unittest

from dns.ui.dns_check_widgets import summarize_dns_results

HONEST = {"discord.com": {"state": "ok"}}
UP = {"label": "Cloudflare", "address": "1.1.1.1", "ok": True, "reason": ""}
DOWN = {"label": "Google", "address": "8.8.8.8", "ok": False, "reason": "сервер молчит"}


class ReferenceNoteTests(unittest.TestCase):
    def test_all_references_answering_adds_nothing(self) -> None:
        summary = summarize_dns_results({"domains": HONEST, "reference": [UP]})

        self.assertEqual(summary["kind"], "ok")
        self.assertNotIn("эталон", summary["detail"].replace("совпадают с эталоном", ""))

    def test_silent_reference_is_named_with_its_reason(self) -> None:
        summary = summarize_dns_results({"domains": HONEST, "reference": [UP, DOWN]})

        self.assertEqual(summary["kind"], "ok")
        self.assertIn("Google (8.8.8.8) — сервер молчит", summary["detail"])
        self.assertIn("по остальным", summary["detail"])

    def test_without_any_reference_dns_is_not_called_honest(self) -> None:
        summary = summarize_dns_results({"domains": HONEST, "reference": [DOWN]})

        self.assertEqual((summary["kind"], summary["title"]), ("partial", "Проверить DNS не удалось"))
        self.assertIn("сравнивать ответы было не с чем", summary["detail"])

    def test_spoofing_keeps_its_verdict_and_gets_the_note(self) -> None:
        summary = summarize_dns_results({"domains": {"x.com": {"state": "spoofed"}}, "reference": [UP, DOWN]})

        self.assertEqual(summary["kind"], "spoofed")
        self.assertIn("Google (8.8.8.8)", summary["detail"])


if __name__ == "__main__":
    unittest.main()
