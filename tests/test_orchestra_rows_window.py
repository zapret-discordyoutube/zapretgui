import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from orchestra.ui.blocked_page import BlockedDomainRow, OrchestraBlockedPage
from orchestra.ui.locked_page import LockedDomainRow, OrchestraLockedPage
from orchestra.ui.rows_window import ROWS_PAGE_SIZE


def _locked_feature(count: int):
    items = [SimpleNamespace(domain=f"10.0.{index // 250}.{index % 250}", strategy=3, askey="quic") for index in range(count)]
    items.append(SimpleNamespace(domain="youtube.com", strategy=5, askey="tls"))
    snapshot = SimpleNamespace(items=items, total_count=len(items), tcp_count=1, udp_count=count)
    return SimpleNamespace(ASKEY_ALL=("tls", "quic"), current_locked_snapshot=lambda: snapshot)


def _blocked_feature(user_count: int, default_count: int):
    user_items = [SimpleNamespace(hostname=f"user{index}.example", strategy=2, askey="tls") for index in range(user_count)]
    default_items = [SimpleNamespace(hostname=f"rkn{index}.example", strategy=1, askey="tls") for index in range(default_count)]
    snapshot = SimpleNamespace(
        user_items=user_items,
        default_items=default_items,
        total_count=user_count + default_count,
        user_count=user_count,
        default_count=default_count,
    )
    return SimpleNamespace(ASKEY_ALL=("tls", "quic"), current_blocked_snapshot=lambda: snapshot)


def _row_count(page, row_cls) -> int:
    return sum(
        1
        for index in range(page.rows_layout.count())
        if isinstance(page.rows_layout.itemAt(index).widget(), row_cls)
    )


class OrchestraRowsWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_locked_page_builds_only_first_rows_and_shows_more_on_request(self) -> None:
        page = OrchestraLockedPage(orchestra_feature=_locked_feature(350))
        self.addCleanup(page.deleteLater)

        page._refresh_locked_list()
        self.assertEqual(_row_count(page, LockedDomainRow), ROWS_PAGE_SIZE)
        self.assertFalse(page._rows_window.button.isHidden())
        self.assertIn("скрыто 251", page._rows_window.button.text())

        page._rows_window.button.click()
        self.assertEqual(_row_count(page, LockedDomainRow), 2 * ROWS_PAGE_SIZE)

    def test_locked_search_filters_data_not_hidden_widgets(self) -> None:
        page = OrchestraLockedPage(orchestra_feature=_locked_feature(350))
        self.addCleanup(page.deleteLater)

        page.search_input.setText("youtube")
        page._rows_window._search_timer.timeout.emit()

        self.assertEqual(list(page._domain_rows), ["youtube.com:tls"])
        self.assertTrue(page._rows_window.button.isHidden())

    def test_blocked_page_limits_rows_across_both_sections(self) -> None:
        page = OrchestraBlockedPage(orchestra_feature=_blocked_feature(80, 59))
        self.addCleanup(page.deleteLater)

        page._refresh_blocked_list()
        rows = page._blocked_rows
        self.assertEqual(len(rows), ROWS_PAGE_SIZE)
        self.assertEqual(sum(1 for row in rows if not row.is_default), 80)
        self.assertEqual(_row_count(page, BlockedDomainRow), ROWS_PAGE_SIZE)
        self.assertIn("скрыто 39", page._rows_window.button.text())


if __name__ == "__main__":
    unittest.main()
