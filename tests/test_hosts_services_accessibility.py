from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QImage, QKeyEvent, QPainter
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import BodyLabel, ComboBox, PushButton, StrongBodyLabel, SwitchButton

from hosts.page_plans import HostsServiceGroupPlan, HostsServiceRowPlan
from hosts.ui.services_build import HostsServiceHoverRow
from hosts.ui.services_build import build_hosts_services_group
from hosts.ui.services_build import build_hosts_services_section_title
from hosts.ui.services_build import build_hosts_service_row


class HostsServicesAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_services_section_title_reads_as_section_for_screen_reader(self) -> None:
        title = build_hosts_services_section_title(text="Сервисы")

        self.assertEqual(title.accessibleName(), "Раздел hosts: Сервисы")
        self.assertEqual(title.property("screenReaderStateText"), "Раздел hosts: Сервисы")

    def test_services_group_title_reads_as_group_for_screen_reader(self) -> None:
        widgets = build_hosts_services_group(
            HostsServiceGroupPlan(
                title="Видео",
                direct_only=False,
                service_names=["YouTube", "Twitch"],
                common_profiles=[("zapret_dns", "Zapret DNS")],
                rows=[],
            ),
            off_label="Отключено",
            strong_body_label_cls=StrongBodyLabel,
            make_chip=lambda label: PushButton(label),
            on_bulk_apply=lambda *_args: None,
        )

        self.assertEqual(widgets.title_label.accessibleName(), "Группа hosts: Видео")
        self.assertEqual(widgets.title_label.property("screenReaderStateText"), "Группа hosts: Видео")

    def test_direct_toggle_reads_service_state(self) -> None:
        widgets = build_hosts_service_row(
            HostsServiceRowPlan(
                service_name="Adobe",
                icon_name="",
                icon_color=None,
                direct_only=True,
                available_profiles=[],
                profile_items=[],
                selected_profile=None,
                toggle_enabled=True,
                toggle_checked=False,
            ),
            body_label_cls=BodyLabel,
            combo_cls=ComboBox,
            toggle_cls=SwitchButton,
            off_label="Отключено",
            on_direct_toggle=lambda *_args: None,
            on_profile_changed=lambda *_args: None,
        )

        self.assertEqual(widgets.control.accessibleName(), "Adobe, выключено")
        self.assertEqual(widgets.control.property("screenReaderStateText"), "Adobe, выключено")
        self.assertIn("Включает или отключает hosts-запись", widgets.control.accessibleDescription())

        widgets.control.setChecked(True)

        self.assertEqual(widgets.control.accessibleName(), "Adobe, включено")
        self.assertEqual(widgets.control.property("screenReaderStateText"), "Adobe, включено")

    def test_direct_toggle_works_from_keyboard(self) -> None:
        events: list[tuple[str, bool]] = []
        widgets = build_hosts_service_row(
            HostsServiceRowPlan(
                service_name="Adobe",
                icon_name="",
                icon_color=None,
                direct_only=True,
                available_profiles=[],
                profile_items=[],
                selected_profile=None,
                toggle_enabled=True,
                toggle_checked=False,
            ),
            body_label_cls=BodyLabel,
            combo_cls=ComboBox,
            toggle_cls=SwitchButton,
            off_label="Отключено",
            on_direct_toggle=lambda service, checked: events.append((service, checked)),
            on_profile_changed=lambda *_args: None,
        )

        self.assertEqual(widgets.control.focusPolicy(), Qt.FocusPolicy.StrongFocus)

        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(widgets.control, event)

        self.assertTrue(event.isAccepted())
        self.assertEqual(events, [("Adobe", True)])
        self.assertEqual(widgets.control.accessibleName(), "Adobe, включено")

    def test_direct_service_row_tracks_hover_for_soft_highlight(self) -> None:
        widgets = build_hosts_service_row(
            HostsServiceRowPlan(
                service_name="Discord",
                icon_name="fa5b.discord",
                icon_color="#5865f2",
                direct_only=True,
                available_profiles=[],
                profile_items=[],
                selected_profile=None,
                toggle_enabled=True,
                toggle_checked=False,
            ),
            body_label_cls=BodyLabel,
            combo_cls=ComboBox,
            toggle_cls=SwitchButton,
            off_label="Отключено",
            on_direct_toggle=lambda *_args: None,
            on_profile_changed=lambda *_args: None,
        )

        self.assertIsInstance(widgets.row_widget, HostsServiceHoverRow)
        self.assertEqual(widgets.row_widget.minimumHeight(), 32)
        widgets.row_widget.resize(700, max(24, widgets.row_widget.sizeHint().height()))

        def background_pixel() -> int:
            image = QImage(
                widgets.row_widget.size(),
                QImage.Format.Format_ARGB32_Premultiplied,
            )
            image.fill(0)
            painter = QPainter(image)
            widgets.row_widget.render(painter)
            painter.end()
            return image.pixel(1, widgets.row_widget.height() // 2)

        self.assertFalse(widgets.row_widget.is_hovered())
        idle_pixel = background_pixel()

        QApplication.sendEvent(widgets.row_widget, QEvent(QEvent.Type.Enter))
        self.assertTrue(widgets.row_widget.is_hovered())
        self.assertNotEqual(background_pixel(), idle_pixel)

        QApplication.sendEvent(widgets.row_widget, QEvent(QEvent.Type.Leave))
        self.assertFalse(widgets.row_widget.is_hovered())

    def test_direct_service_group_compacts_spacing_for_taller_hover_rows(self) -> None:
        widgets = build_hosts_services_group(
            HostsServiceGroupPlan(
                title="Напрямую из hosts",
                direct_only=True,
                service_names=["Discord", "YouTube"],
                common_profiles=[],
                rows=[],
            ),
            off_label="Отключено",
            strong_body_label_cls=StrongBodyLabel,
            make_chip=lambda _label: None,
            on_bulk_apply=lambda *_args: None,
        )

        self.assertEqual(widgets.card.main_layout.spacing(), 4)

    def test_profile_combo_reads_selected_profile(self) -> None:
        widgets = build_hosts_service_row(
            HostsServiceRowPlan(
                service_name="YouTube",
                icon_name="",
                icon_color=None,
                direct_only=False,
                available_profiles=["zapret_dns"],
                profile_items=[("zapret_dns", "Zapret DNS")],
                selected_profile="zapret_dns",
                toggle_enabled=True,
                toggle_checked=False,
            ),
            body_label_cls=BodyLabel,
            combo_cls=ComboBox,
            toggle_cls=SwitchButton,
            off_label="Отключено",
            on_direct_toggle=lambda *_args: None,
            on_profile_changed=lambda *_args: None,
        )

        self.assertEqual(widgets.control.accessibleName(), "YouTube, выбран профиль Zapret DNS")
        self.assertEqual(widgets.control.property("screenReaderStateText"), "YouTube, выбран профиль Zapret DNS")
        self.assertIn("Выберите профиль hosts", widgets.control.accessibleDescription())
        self.assertIn("стрелками вверх и вниз", widgets.control.accessibleDescription())

        widgets.control.setCurrentIndex(0)

        self.assertEqual(widgets.control.accessibleName(), "YouTube, отключено")
        self.assertEqual(widgets.control.property("screenReaderStateText"), "YouTube, отключено")

    def test_profile_combo_menu_items_are_named_for_screen_reader(self) -> None:
        widgets = build_hosts_service_row(
            HostsServiceRowPlan(
                service_name="YouTube",
                icon_name="",
                icon_color=None,
                direct_only=False,
                available_profiles=["zapret_dns"],
                profile_items=[("zapret_dns", "Zapret DNS")],
                selected_profile="zapret_dns",
                toggle_enabled=True,
                toggle_checked=False,
            ),
            body_label_cls=BodyLabel,
            combo_cls=ComboBox,
            toggle_cls=SwitchButton,
            off_label="Отключено",
            on_direct_toggle=lambda *_args: None,
            on_profile_changed=lambda *_args: None,
        )
        create_menu = getattr(widgets.control, "_create_accessible_combo_menu", None)
        self.assertIsNotNone(create_menu)

        menu = create_menu()

        self.assertEqual(
            menu.view.item(0).data(Qt.ItemDataRole.AccessibleTextRole),
            "YouTube: Отключено, не выбран",
        )
        self.assertEqual(
            menu.view.item(1).data(Qt.ItemDataRole.AccessibleTextRole),
            "YouTube: Zapret DNS, выбран",
        )

    def test_group_chips_read_bulk_action(self) -> None:
        widgets = build_hosts_services_group(
            HostsServiceGroupPlan(
                title="Видео",
                direct_only=False,
                service_names=["YouTube", "Twitch"],
                common_profiles=[("zapret_dns", "Zapret DNS")],
                rows=[],
            ),
            off_label="Отключено",
            strong_body_label_cls=StrongBodyLabel,
            make_chip=lambda label: PushButton(label),
            on_bulk_apply=lambda *_args: None,
        )

        self.assertEqual(widgets.chip_buttons[0].accessibleName(), "Отключить группу Видео")
        self.assertEqual(widgets.chip_buttons[0].property("screenReaderStateText"), "Отключить группу Видео")
        self.assertIn("YouTube, Twitch", widgets.chip_buttons[0].accessibleDescription())
        self.assertEqual(widgets.chip_buttons[1].accessibleName(), "Применить Zapret DNS к группе Видео")
        self.assertEqual(
            widgets.chip_buttons[1].property("screenReaderStateText"),
            "Применить Zapret DNS к группе Видео",
        )
        self.assertIn("YouTube, Twitch", widgets.chip_buttons[1].accessibleDescription())

    def test_group_chips_scroll_area_does_not_take_tab_focus(self) -> None:
        widgets = build_hosts_services_group(
            HostsServiceGroupPlan(
                title="Видео",
                direct_only=False,
                service_names=["YouTube", "Twitch"],
                common_profiles=[("zapret_dns", "Zapret DNS")],
                rows=[],
            ),
            off_label="Отключено",
            strong_body_label_cls=StrongBodyLabel,
            make_chip=lambda label: PushButton(label),
            on_bulk_apply=lambda *_args: None,
        )

        self.assertIsNotNone(widgets.chips_scroll)
        self.assertEqual(widgets.chips_scroll.focusPolicy(), Qt.FocusPolicy.NoFocus)


if __name__ == "__main__":
    unittest.main()
