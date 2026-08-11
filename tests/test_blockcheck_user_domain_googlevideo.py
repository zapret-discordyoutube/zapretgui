"""Запрет голого googlevideo.com в пользовательских доменах BlockCheck."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import blockcheck.page_runtime as page_runtime  # noqa: E402
import blockcheck.targets as targets  # noqa: E402


class TargetsAddUserDomainTests(unittest.TestCase):
    def test_rejects_bare_googlevideo_without_saving(self) -> None:
        with patch.object(targets, "save_user_domains") as save, patch.object(
            targets, "load_user_domains", return_value=[]
        ):
            self.assertFalse(targets.add_user_domain("https://googlevideo.com"))
        save.assert_not_called()

    def test_accepts_regular_domain(self) -> None:
        with patch.object(targets, "save_user_domains") as save, patch.object(
            targets, "load_user_domains", return_value=[]
        ):
            self.assertTrue(targets.add_user_domain("example.com"))
        save.assert_called_once_with(["example.com"])


class PageRuntimeAddUserDomainTests(unittest.TestCase):
    def test_returns_rejection_object_for_bare_googlevideo(self) -> None:
        with patch("blockcheck.targets.add_user_domain") as add:
            result = page_runtime.add_user_domain("https://googlevideo.com/")

        add.assert_not_called()
        self.assertIsInstance(result, page_runtime.UserDomainRejection)
        self.assertEqual(result.domain, "googlevideo.com")

    def test_returns_normalized_domain_and_none_for_duplicate(self) -> None:
        with patch("blockcheck.targets.add_user_domain", return_value=True):
            self.assertEqual(
                page_runtime.add_user_domain("https://Example.COM/path"),
                "example.com",
            )
        with patch("blockcheck.targets.add_user_domain", return_value=False):
            self.assertIsNone(page_runtime.add_user_domain("example.com"))


class PageShowsExplanationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_rejection_shows_infobar_and_adds_no_chip(self) -> None:
        from blockcheck.ui.page import BlockcheckPage

        page = BlockcheckPage.__new__(BlockcheckPage)
        page._user_domain_action_runtime = SimpleNamespace(
            is_current=Mock(return_value=True)
        )
        page._cleanup_in_progress = False
        page._add_chip = Mock()
        page._domain_input = Mock()
        page._has_pending_user_domain_action = Mock(return_value=False)
        page.window = Mock(return_value=None)

        rejection = page_runtime.UserDomainRejection(domain="googlevideo.com")
        with patch("blockcheck.ui.page.InfoBarHelper") as info_bar:
            BlockcheckPage._on_user_domain_action_finished(
                page, 5, "add", rejection, {"domain": "googlevideo.com"}
            )

        info_bar.warning.assert_called_once()
        page._add_chip.assert_not_called()
        page._domain_input.clear.assert_called_once()


class BuildTargetsMigrationTests(unittest.TestCase):
    def test_skips_stored_bare_googlevideo_but_keeps_other_domains(self) -> None:
        with patch.object(
            targets,
            "load_user_domains",
            return_value=["googlevideo.com", "example.com"],
        ):
            built = targets.build_targets_with_user_domains()

        values = [t["value"] for t in built]
        self.assertNotIn("https://googlevideo.com", values)
        self.assertIn("https://example.com", values)


if __name__ == "__main__":
    unittest.main()
