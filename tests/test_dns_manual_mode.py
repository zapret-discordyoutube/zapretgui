from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class DnsManualModeTests(unittest.TestCase):
    def test_force_dns_settings_are_gone(self) -> None:
        from settings import schema, store

        self.assertEqual(schema.default_dns(), {"custom_servers": []})
        for name in ("get_force_dns_enabled", "set_force_dns_enabled", "get_dns_crash_count", "increment_dns_crash_count"):
            self.assertFalse(hasattr(store, name), name)

    def test_program_does_not_touch_dns_on_startup(self) -> None:
        post_startup = _read("src/main/post_startup.py")

        self.assertNotIn("install_dns_startup", post_startup)
        self.assertNotIn("apply_dns_on_startup", post_startup)
        self.assertFalse((ROOT / "src/dns/dns_worker.py").exists())

    def test_dns_page_has_no_forced_dns_mode(self) -> None:
        page_source = _read("src/dns/ui/page.py")

        self.assertNotIn("force_dns_action", page_source)
        self.assertNotIn("POWER_BUTTON", page_source)
        self.assertNotIn("create_force_dns_action_worker", _read("src/app/feature_facades/dns.py"))

    def test_visible_dns_texts_do_not_offer_forced_dns_mode(self) -> None:
        texts_source = _read("src/app/ui_texts.py")

        self.assertNotIn("включить принудительный DNS", texts_source)
        self.assertNotIn("Включить принудительный DNS", texts_source)
        self.assertNotIn("Выключить принудительный DNS", texts_source)
        self.assertNotIn("enable forced DNS", texts_source)
        self.assertIn("Применить Quad9", texts_source)

    def test_isp_warning_applies_recommended_dns_manually(self) -> None:
        page_source = _read("src/dns/ui/page.py")

        self.assertIn('RECOMMENDED_PROVIDER = ("Безопасные", "Quad9")', page_source)
        self.assertIn("self._choose_provider(name)", page_source)
        self.assertNotIn("on_force_dns_toggled", page_source)


if __name__ == "__main__":
    unittest.main()
