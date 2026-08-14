from __future__ import annotations

import inspect
import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import Action, RoundMenu, Theme, setTheme

from hosts.page_plans import HostsServiceGroupPlan, HostsServiceRowPlan
from hosts.ui.services_matrix import (
    HostsServicesMatrixCanvas,
    HostsServicesMatrixDelegate,
    HostsServicesMatrixModel,
    build_hosts_services_matrix,
)


class HostsServicesMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])
        setTheme(Theme.DARK)

    def test_dns_profiles_render_as_compact_single_selection_column(self) -> None:
        rows = [
            HostsServiceRowPlan(
                service_name=f"Service {idx}",
                icon_name="fa5s.globe",
                icon_color=None,
                direct_only=False,
                available_profiles=["zapret_dns", "xbox_dns", "comss_dns", "malw_dns"],
                profile_items=[
                    ("zapret_dns", "Zapret DNS"),
                    ("xbox_dns", "XBOX DNS"),
                    ("comss_dns", "Comss DNS"),
                    ("malw_dns", "Malw DNS"),
                ],
                selected_profile="xbox_dns",
                toggle_enabled=True,
                toggle_checked=False,
            )
            for idx in range(4)
        ]
        group = HostsServiceGroupPlan(
            title="Видео",
            direct_only=False,
            service_names=[row.service_name for row in rows],
            common_profiles=[
                ("zapret_dns", "Zapret DNS"),
                ("xbox_dns", "XBOX DNS"),
                ("comss_dns", "Comss DNS"),
                ("malw_dns", "Malw DNS"),
            ],
            rows=rows,
        )

        widgets = build_hosts_services_matrix(
            [group],
            off_label="Откл.",
            on_profile_selected=Mock(),
            on_bulk_profile_selected=Mock(),
        )

        self.assertEqual(widgets.model.columnCount(), 2)
        self.assertLessEqual(widgets.view.minimumWidth(), 640)
        self.assertEqual(
            widgets.model.data(widgets.model.index(1, 1), Qt.ItemDataRole.DisplayRole),
            "XBOX DNS",
        )

    def test_service_rows_expose_icon_metadata_for_painter(self) -> None:
        group = HostsServiceGroupPlan(
            title="ИИ",
            direct_only=False,
            service_names=["ChatGPT"],
            common_profiles=[("zapret_dns", "Zapret DNS")],
            rows=[
                HostsServiceRowPlan(
                    service_name="ChatGPT",
                    icon_name="fa5s.robot",
                    icon_color="#10a37f",
                    direct_only=False,
                    available_profiles=["zapret_dns"],
                    profile_items=[("zapret_dns", "Zapret DNS")],
                    selected_profile="zapret_dns",
                    toggle_enabled=True,
                    toggle_checked=False,
                )
            ],
        )

        model = HostsServicesMatrixModel([group], off_label="Откл.")

        self.assertEqual(model.data(model.index(1, 0), model.IconNameRole), "fa5s.robot")
        self.assertEqual(model.data(model.index(1, 0), model.IconColorRole), "#10a37f")

    def test_matrix_uses_lightweight_delegate_without_item_stylesheet(self) -> None:
        group = HostsServiceGroupPlan(
            title="ИИ",
            direct_only=False,
            service_names=["ChatGPT"],
            common_profiles=[("zapret_dns", "Zapret DNS")],
            rows=[
                HostsServiceRowPlan(
                    service_name="ChatGPT",
                    icon_name="fa5s.robot",
                    icon_color="#10a37f",
                    direct_only=False,
                    available_profiles=["zapret_dns"],
                    profile_items=[("zapret_dns", "Zapret DNS")],
                    selected_profile=None,
                    toggle_enabled=True,
                    toggle_checked=False,
                )
            ],
        )

        widgets = build_hosts_services_matrix(
            [group],
            off_label="Откл.",
            on_profile_selected=Mock(),
            on_bulk_profile_selected=Mock(),
        )

        self.assertIsInstance(widgets.view, HostsServicesMatrixCanvas)
        self.assertNotIn("QTableView#hostsServicesMatrix::item", widgets.view.styleSheet())
        self.assertEqual(widgets.view.delegate().sizeHint(None, widgets.model.index(1, 0)).height(), 38)

    def test_matrix_keeps_keyboard_focus_without_qtable_selection_highlight(self) -> None:
        group = HostsServiceGroupPlan(
            title="ИИ",
            direct_only=False,
            service_names=["Windsurf"],
            common_profiles=[("zapret_dns", "Zapret DNS")],
            rows=[
                HostsServiceRowPlan(
                    service_name="Windsurf",
                    icon_name="fa5s.wind",
                    icon_color="#25d9d1",
                    direct_only=False,
                    available_profiles=["zapret_dns"],
                    profile_items=[("zapret_dns", "Zapret DNS")],
                    selected_profile="zapret_dns",
                    toggle_enabled=True,
                    toggle_checked=False,
                )
            ],
        )

        widgets = build_hosts_services_matrix(
            [group],
            off_label="Откл.",
            on_profile_selected=Mock(),
            on_bulk_profile_selected=Mock(),
        )

        self.assertEqual(widgets.view.focusPolicy(), Qt.FocusPolicy.StrongFocus)
        self.assertNotIn("QTableView#hostsServicesMatrix::item", widgets.view.styleSheet())

    def test_canvas_tracks_hover_row_for_soft_row_highlight(self) -> None:
        group = HostsServiceGroupPlan(
            title="ИИ",
            direct_only=False,
            service_names=["Windsurf"],
            common_profiles=[("zapret_dns", "Zapret DNS")],
            rows=[
                HostsServiceRowPlan(
                    service_name="Windsurf",
                    icon_name="fa5s.wind",
                    icon_color="#25d9d1",
                    direct_only=False,
                    available_profiles=["zapret_dns"],
                    profile_items=[("zapret_dns", "Zapret DNS")],
                    selected_profile="zapret_dns",
                    toggle_enabled=True,
                    toggle_checked=False,
                )
            ],
        )

        widgets = build_hosts_services_matrix(
            [group],
            off_label="Откл.",
            on_profile_selected=Mock(),
            on_bulk_profile_selected=Mock(),
        )

        self.assertTrue(widgets.view.hasMouseTracking())
        self.assertEqual(widgets.view._hover_row, -1)
        widgets.view._set_hover_row(1)
        self.assertEqual(widgets.view._hover_row, 1)
        widgets.view._set_hover_row(0)
        self.assertEqual(widgets.view._hover_row, -1)

    def test_selected_profile_cell_does_not_paint_accent_bar(self) -> None:
        source = inspect.getsource(HostsServicesMatrixCanvas._paint_profile_cell)

        self.assertNotIn("accent_bar_rect", source)
        self.assertNotIn("pill_rect", source)

    def test_selected_profile_menu_action_gets_accent_icon(self) -> None:
        row = HostsServiceRowPlan(
            service_name="ChatGPT",
            icon_name="fa5s.robot",
            icon_color="#10a37f",
            direct_only=False,
            available_profiles=["zapret_dns"],
            profile_items=[("zapret_dns", "Zapret DNS")],
            selected_profile="zapret_dns",
            toggle_enabled=True,
            toggle_checked=False,
        )
        group = HostsServiceGroupPlan(
            title="ИИ",
            direct_only=False,
            service_names=[row.service_name],
            common_profiles=[("zapret_dns", "Zapret DNS")],
            rows=[row],
        )

        widgets = build_hosts_services_matrix(
            [group],
            off_label="Откл.",
            on_profile_selected=Mock(),
            on_bulk_profile_selected=Mock(),
        )
        widgets.view.resize(widgets.view.minimumWidth(), widgets.view.sizeHint().height())
        widgets.view.show()
        self._app.processEvents()

        profile_cell = QRect(
            widgets.view._column_rect(1).left(),
            widgets.view._row_tops[1],
            widgets.view._column_rect(1).width(),
            widgets.view._row_heights[1],
        )
        try:
            QTest.mouseClick(widgets.view, Qt.MouseButton.LeftButton, pos=profile_cell.center())

            actions = widgets.view._profile_menu.actions()
            self.assertEqual(actions[0].text(), "Откл.")
            self.assertEqual(actions[1].text(), "Zapret DNS")
            self.assertTrue(actions[0].icon().isNull())
            self.assertFalse(actions[1].icon().isNull())
        finally:
            widgets.view._profile_menu.hide()

    def test_selection_update_emits_only_changed_profile_cell(self) -> None:
        group = HostsServiceGroupPlan(
            title="ИИ",
            direct_only=False,
            service_names=["ChatGPT", "Claude"],
            common_profiles=[("zapret_dns", "Zapret DNS")],
            rows=[
                HostsServiceRowPlan(
                    service_name="ChatGPT",
                    icon_name="fa5s.robot",
                    icon_color="#10a37f",
                    direct_only=False,
                    available_profiles=["zapret_dns"],
                    profile_items=[("zapret_dns", "Zapret DNS")],
                    selected_profile=None,
                    toggle_enabled=True,
                    toggle_checked=False,
                ),
                HostsServiceRowPlan(
                    service_name="Claude",
                    icon_name="fa5s.brain",
                    icon_color="#d9aa7a",
                    direct_only=False,
                    available_profiles=["zapret_dns"],
                    profile_items=[("zapret_dns", "Zapret DNS")],
                    selected_profile=None,
                    toggle_enabled=True,
                    toggle_checked=False,
                ),
            ],
        )
        model = HostsServicesMatrixModel([group], off_label="Откл.")
        changed: list[tuple[int, int, int, int]] = []
        model.dataChanged.connect(
            lambda top_left, bottom_right, _roles=None: changed.append(
                (top_left.row(), top_left.column(), bottom_right.row(), bottom_right.column())
            )
        )

        model.update_selection({"Claude": "zapret_dns"})

        self.assertEqual(changed, [(2, 1, 2, 1)])

    def test_canvas_limits_repaint_to_visible_rows(self) -> None:
        rows = [
            HostsServiceRowPlan(
                service_name=f"Service {idx}",
                icon_name="fa5s.globe",
                icon_color=None,
                direct_only=False,
                available_profiles=["zapret_dns"],
                profile_items=[("zapret_dns", "Zapret DNS")],
                selected_profile=None,
                toggle_enabled=True,
                toggle_checked=False,
            )
            for idx in range(40)
        ]
        group = HostsServiceGroupPlan(
            title="Остальные",
            direct_only=False,
            service_names=[row.service_name for row in rows],
            common_profiles=[("zapret_dns", "Zapret DNS")],
            rows=rows,
        )

        widgets = build_hosts_services_matrix(
            [group],
            off_label="Откл.",
            on_profile_selected=Mock(),
            on_bulk_profile_selected=Mock(),
        )

        visible_rows = widgets.view.visible_rows_for_rect(QRect(0, 120, 900, 180))

        self.assertLess(len(visible_rows), widgets.model.rowCount())
        self.assertGreater(len(visible_rows), 0)

    def test_canvas_clicks_keep_profile_selection_callbacks(self) -> None:
        row = HostsServiceRowPlan(
            service_name="ChatGPT",
            icon_name="fa5s.robot",
            icon_color="#10a37f",
            direct_only=False,
            available_profiles=["zapret_dns"],
            profile_items=[("zapret_dns", "Zapret DNS")],
            selected_profile=None,
            toggle_enabled=True,
            toggle_checked=False,
        )
        group = HostsServiceGroupPlan(
            title="ИИ",
            direct_only=False,
            service_names=[row.service_name],
            common_profiles=[("zapret_dns", "Zapret DNS")],
            rows=[row],
        )
        on_profile = Mock()
        on_bulk = Mock()

        widgets = build_hosts_services_matrix(
            [group],
            off_label="Откл.",
            on_profile_selected=on_profile,
            on_bulk_profile_selected=on_bulk,
        )
        widgets.view.resize(widgets.view.minimumWidth(), widgets.view.sizeHint().height())
        widgets.view.show()
        self._app.processEvents()

        profile_cell = QRect(
            widgets.view._column_rect(1).left(),
            widgets.view._row_tops[1],
            widgets.view._column_rect(1).width(),
            widgets.view._row_heights[1],
        )
        QTest.mouseClick(widgets.view, Qt.MouseButton.LeftButton, pos=profile_cell.center())

        on_profile.assert_not_called()
        self.assertIsNotNone(widgets.view._profile_menu)
        self.assertIsInstance(widgets.view._profile_menu, RoundMenu)
        self.assertTrue(all(isinstance(action, Action) for action in widgets.view._profile_menu.actions()))
        self.assertNotIn("QMenu", widgets.view._profile_menu.styleSheet())
        self.assertTrue(widgets.view._profile_menu.isVisible())
        widgets.view._profile_menu.actions()[1].trigger()
        on_profile.assert_called_once_with("ChatGPT", "zapret_dns")
        widgets.view._profile_menu.hide()
        on_bulk.assert_not_called()

    def test_canvas_keyboard_opens_profile_menu_for_current_service_row(self) -> None:
        row = HostsServiceRowPlan(
            service_name="ChatGPT",
            icon_name="fa5s.robot",
            icon_color="#10a37f",
            direct_only=False,
            available_profiles=["zapret_dns"],
            profile_items=[("zapret_dns", "Zapret DNS")],
            selected_profile=None,
            toggle_enabled=True,
            toggle_checked=False,
        )
        group = HostsServiceGroupPlan(
            title="ИИ",
            direct_only=False,
            service_names=[row.service_name],
            common_profiles=[("zapret_dns", "Zapret DNS")],
            rows=[row],
        )
        on_profile = Mock()

        widgets = build_hosts_services_matrix(
            [group],
            off_label="Откл.",
            on_profile_selected=on_profile,
            on_bulk_profile_selected=Mock(),
        )
        widgets.view.resize(widgets.view.minimumWidth(), widgets.view.sizeHint().height())
        widgets.view.show()
        widgets.view.setFocus()
        self._app.processEvents()

        QTest.keyClick(widgets.view, Qt.Key.Key_Return)

        self.assertTrue(widgets.view._profile_menu.isVisible())
        widgets.view._profile_menu.actions()[1].trigger()
        on_profile.assert_called_once_with("ChatGPT", "zapret_dns")
        widgets.view._profile_menu.hide()


if __name__ == "__main__":
    unittest.main()
