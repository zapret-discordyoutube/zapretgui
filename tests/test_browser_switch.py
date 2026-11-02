"""ECH и QUIC одной кнопкой: правило Edge/Chrome и user.js Firefox; отмена убирает только своё. Браузер по умолчанию."""

from __future__ import annotations

import os
import tempfile
import unittest

from blockcheck import browser_switch as es
from blockcheck import default_browser as db


def _text(path: str) -> str:
    with open(path, encoding="utf-8") as source:
        return source.read()


class _Registry:
    def __init__(self, keys: dict | None = None) -> None:
        self.keys: dict[str, dict[str, int]] = {path: dict(values) for path, values in (keys or {}).items()}

    def get(self, path, name):
        return self.keys.get(path, {}).get(name)

    def key_exists(self, path):
        return path in self.keys

    def set_dword(self, path, name, value):
        self.keys.setdefault(path, {})[name] = value

    def delete_value(self, path, name):
        self.keys.get(path, {}).pop(name, None)

    def key_is_empty(self, path):
        return path in self.keys and not self.keys[path]

    def delete_key(self, path):
        self.keys.pop(path, None)


class DefaultBrowserTests(unittest.TestCase):
    def test_prog_ids_name_the_browser(self) -> None:
        self.assertEqual(db.browser_from_prog_id("FirefoxURL-308046B0AF4A39CB").family, db.BROWSER_FIREFOX)
        self.assertEqual(db.browser_from_prog_id("MSEdgeHTM").title, "Edge")
        self.assertEqual(db.browser_from_prog_id("ChromeHTML").family, db.BROWSER_CHROME)
        self.assertEqual(db.browser_from_prog_id("YandexHTML").family, db.BROWSER_OTHER_CHROMIUM)
        self.assertEqual(db.browser_from_prog_id("ChromiumHTM.GHELN7VHGRK6O5VJDXNMMMCNIE").title, "Chromium")
        self.assertFalse(db.browser_from_prog_id("SomethingHTML").known)

    def test_no_record_means_unknown(self) -> None:
        def missing():
            raise FileNotFoundError

        self.assertFalse(db.default_browser(missing).known)


class PolicyTests(unittest.TestCase):
    def test_edge_policy_is_set_and_undo_leaves_nothing_behind(self) -> None:
        registry = _Registry()
        result = es.turn_off(es.ECH, db.BROWSER_EDGE, "Edge", registry=registry)
        self.assertTrue(result.ok)
        self.assertIn("Закройте все окна Edge", result.text)
        self.assertEqual(registry.keys[es.POLICY_KEYS[db.BROWSER_EDGE]], {"EncryptedClientHelloEnabled": 0})
        self.assertTrue(es.is_disabled(es.ECH, db.BROWSER_EDGE, registry=registry))
        es.restore(es.ECH, db.BROWSER_EDGE, "Edge", registry=registry)
        # Раздел создала программа — его и убрали.
        self.assertNotIn(es.POLICY_KEYS[db.BROWSER_EDGE], registry.keys)

    def test_undo_keeps_other_rules_of_the_browser(self) -> None:
        path = es.POLICY_KEYS[db.BROWSER_CHROME]
        registry = _Registry({path: {"HomepageLocation": 1}})
        es.turn_off(es.ECH, db.BROWSER_CHROME, "Chrome", registry=registry)
        es.restore(es.ECH, db.BROWSER_CHROME, "Chrome", registry=registry)
        self.assertEqual(registry.keys[path], {"HomepageLocation": 1})

    def test_unknown_browsers_get_no_button(self) -> None:
        self.assertFalse(es.can_switch(db.BROWSER_OTHER_CHROMIUM))
        self.assertFalse(es.can_switch(""))
        self.assertTrue(es.can_switch(db.BROWSER_FIREFOX))


class FirefoxTests(unittest.TestCase):
    def _profiles(self, *contents: str | None):
        root = tempfile.mkdtemp()
        base = os.path.join(root, "Mozilla", "Firefox")
        lines = []
        for index, content in enumerate(contents):
            folder = os.path.join(base, "Profiles", f"p{index}.default")
            os.makedirs(folder)
            if content is not None:
                with open(os.path.join(folder, "user.js"), "w", encoding="utf-8") as target:
                    target.write(content)
            lines += [f"[Profile{index}]", f"Path=Profiles/p{index}.default", "IsRelative=1", ""]
        with open(os.path.join(base, "profiles.ini"), "w", encoding="utf-8") as target:
            target.write("[General]\nStartWithLastProfile=1\n\n" + "\n".join(lines))
        return root

    def test_lines_are_added_to_every_profile_and_undo_removes_only_them(self) -> None:
        root = self._profiles(None, 'user_pref("browser.startup.page", 3);\n')
        dirs = es.firefox_profile_dirs(root)
        self.assertEqual(len(dirs), 2)
        result = es.turn_off(es.ECH, db.BROWSER_FIREFOX, "Firefox", profiles=lambda: dirs)
        self.assertTrue(result.ok)
        for folder in dirs:
            text = _text(os.path.join(folder, "user.js"))
            self.assertIn('user_pref("network.dns.echconfig.enabled", false);', text)
            self.assertIn('user_pref("network.dns.http3_echconfig.enabled", false);', text)
        self.assertTrue(es.is_disabled(es.ECH, db.BROWSER_FIREFOX, profiles=lambda: dirs))
        # Второе нажатие строки не дублирует.
        es.turn_off(es.ECH, db.BROWSER_FIREFOX, "Firefox", profiles=lambda: dirs)
        self.assertEqual(_text(os.path.join(dirs[1], "user.js")).count(es.FEATURES[es.ECH].firefox_mark), 2)
        es.restore(es.ECH, db.BROWSER_FIREFOX, "Firefox", profiles=lambda: dirs)
        # Файл, который создала программа, убран; чужие строки остались.
        self.assertFalse(os.path.exists(os.path.join(dirs[0], "user.js")))
        self.assertEqual(_text(os.path.join(dirs[1], "user.js")), 'user_pref("browser.startup.page", 3);\n')

    def test_quic_and_ech_lines_live_side_by_side_and_undo_separately(self) -> None:
        root = self._profiles(None)
        dirs = es.firefox_profile_dirs(root)
        es.turn_off(es.ECH, db.BROWSER_FIREFOX, "Firefox", profiles=lambda: dirs)
        es.turn_off(es.QUIC, db.BROWSER_FIREFOX, "Firefox", profiles=lambda: dirs)
        path = os.path.join(dirs[0], "user.js")
        self.assertIn('user_pref("network.http.http3.enable", false);', _text(path))
        es.restore(es.QUIC, db.BROWSER_FIREFOX, "Firefox", profiles=lambda: dirs)
        text = _text(path)
        self.assertNotIn("http3.enable", text)
        self.assertIn("echconfig.enabled", text)

    def test_quic_policy_for_edge(self) -> None:
        registry = _Registry()
        es.turn_off(es.QUIC, db.BROWSER_EDGE, "Edge", registry=registry)
        self.assertEqual(registry.keys[es.POLICY_KEYS[db.BROWSER_EDGE]], {"QuicAllowed": 0})

    def test_no_firefox_profile_is_said_plainly(self) -> None:
        result = es.turn_off(es.ECH, db.BROWSER_FIREFOX, "Firefox", profiles=list)
        self.assertFalse(result.ok)
        self.assertIn("about:config", result.text)


if __name__ == "__main__":
    unittest.main()
