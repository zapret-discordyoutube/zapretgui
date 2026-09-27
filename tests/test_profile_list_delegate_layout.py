import unittest
from unittest.mock import patch

from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication, QListView

from profile.state import ProfileListItem
from profile.ui.profile_list_delegate import (
    ProfileListDelegate,
    _profile_row_layout,
    _strategy_payload_badge_rects,
)
from profile.ui.profile_list_model import ProfileListModel


def _composite_item() -> ProfileListItem:
    return ProfileListItem(
        key="profile:0",
        persistent_key="profile:0",
        profile_index=0,
        display_name="18+ сайты",
        enabled=True,
        in_preset=True,
        strategy_id="flowseal_1103_alt13_tcp",
        strategy_name="Flowseal 1.10.3 / ALT13 / TCP",
        match_lines=("--filter-tcp=443", "--hostlist=lists/x.txt"),
        list_type="hostlist",
        rating="",
        favorite=False,
        group="g",
        group_name="Группа",
        order=0,
        strategy_payload_scopes=("tls_client_hello", "http_req", "tls_client_hello,http_req"),
    )


class ProfileListDelegateLayoutTests(unittest.TestCase):
    def test_status_dot_stays_next_to_short_strategy_text(self) -> None:
        layout = _profile_row_layout(
            QRect(8, 2, 404, 40),
            strategy_text_width=48,
            feedback_text_width=0,
            badge_width=0,
        )

        self.assertTrue(layout.strategy_rect.isValid())
        self.assertEqual(layout.strategy_rect.left() - layout.dot_rect.right(), 4)
        self.assertEqual(layout.strategy_rect.width(), 48)

    def test_payload_badge_stays_visible_and_strategy_name_gets_the_rest(self) -> None:
        # Значок составной стратегии не прячется: при нехватке места
        # сокращается имя стратегии, а не значок.
        for row_width in (600, 800, 1000):
            with self.subTest(row_width=row_width):
                layout = _profile_row_layout(
                    QRect(8, 2, row_width, 40),
                    strategy_text_width=420,
                    feedback_text_width=0,
                    badge_width=0,
                )
                badge_rect, text_rect = _strategy_payload_badge_rects(layout.strategy_rect, 150)

                self.assertTrue(badge_rect.isValid())
                self.assertEqual(badge_rect.width(), 150)
                self.assertGreater(text_rect.width(), 0)
                self.assertLess(text_rect.width(), 420 - 150)
                self.assertGreater(text_rect.left(), badge_rect.right())

    def test_delegate_paints_payload_badge_at_narrow_width(self) -> None:
        app = QApplication.instance() or QApplication([])
        model = ProfileListModel()
        model.set_profiles((_composite_item(),))
        view = QListView()
        view.setModel(model)
        view.setItemDelegate(ProfileListDelegate(view))
        view.resize(700, 120)
        self.addCleanup(view.deleteLater)

        with patch("profile.ui.profile_list_delegate.paint_payload_badge") as paint_badge:
            view.show()
            app.processEvents()
            view.grab()

        painted = [call.args for call in paint_badge.call_args_list if call.args[2]]
        self.assertTrue(painted)
        self.assertEqual(painted[-1][2], "TLS · HTTP · TLS+HTTP")
        self.assertTrue(painted[-1][1].isValid())
        self.assertGreater(painted[-1][1].width(), 0)


if __name__ == "__main__":
    unittest.main()
