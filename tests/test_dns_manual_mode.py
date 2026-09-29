from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class DnsManualModeTests(unittest.TestCase):
    def test_force_dns_setting_defaults_to_disabled(self) -> None:
        schema = ast.parse(_read("src/settings/schema.py"))
        default_dns = next(
            node
            for node in schema.body
            if isinstance(node, ast.FunctionDef) and node.name == "default_dns"
        )
        return_node = next(node for node in ast.walk(default_dns) if isinstance(node, ast.Return))
        defaults = ast.literal_eval(return_node.value)
        self.assertIs(defaults["force_dns_enabled"], False)

        store_source = _read("src/settings/store.py")
        self.assertIn('return _get_bool(("dns", "force_dns_enabled"), False)', store_source)

    def test_startup_dns_apply_is_disabled(self) -> None:
        worker_source = _read("src/dns/dns_worker.py")

        self.assertIn("DNS startup apply disabled: manual mode only", worker_source)
        self.assertNotIn("QTimer.singleShot(3000, delayed_apply)", worker_source)

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
