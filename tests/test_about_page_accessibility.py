from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from app.state_store import MainWindowStateStore
from donater.premium_display import build_premium_display
from ui.pages.about_page_about_build import build_about_page_about_content
from ui.pages.about_page import AboutPage
from ui.pages.about_page_tabs_build import build_about_page_tabs
from ui.theme import get_theme_tokens


class AboutPageAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_about_buttons_have_screen_reader_names(self) -> None:
        parent = QWidget()
        layout = QVBoxLayout(parent)
        opened: list[str] = []

        widgets = build_about_page_about_content(
            layout,
            tr_fn=lambda _key, default: default,
            tokens=get_theme_tokens(),
            content_parent=parent,
            app_version="9.9.9",
            make_section_label=lambda text: QWidget(),
            on_open_updates=lambda: None,
            on_open_premium=lambda: None,
            on_open_kvn_tab=lambda: opened.append("kvn"),
            on_open_help_tab=lambda: opened.append("help"),
        )

        self.assertEqual(widgets.update_btn.accessibleName(), "Открыть настройки обновлений")
        self.assertEqual(
            widgets.update_btn.property("screenReaderStateText"),
            "Открыть настройки обновлений",
        )
        self.assertIn("автоматической проверки", widgets.update_btn.accessibleDescription())
        self.assertEqual(widgets.about_app_name_label.accessibleName(), "Название программы: Zapret 2 GUI")
        self.assertEqual(
            widgets.about_app_name_label.property("screenReaderStateText"),
            "Название программы: Zapret 2 GUI",
        )
        self.assertEqual(widgets.about_version_value_label.accessibleName(), "Версия программы: 9.9.9")
        self.assertEqual(
            widgets.about_version_value_label.property("screenReaderStateText"),
            "Версия программы: 9.9.9",
        )
        self.assertEqual(widgets.sub_status_label.accessibleName(), "Статус подписки: Free версия")
        self.assertEqual(
            widgets.sub_status_label.property("screenReaderStateText"),
            "Статус подписки: Free версия",
        )
        self.assertEqual(
            widgets.sub_desc_label.property("screenReaderStateText"),
            "Описание подписки: Подписка Zapret Premium открывает доступ к дополнительным темам, "
            "приоритетной поддержке и VPN-сервису.",
        )
        self.assertEqual(widgets.premium_btn.accessibleName(), "Открыть Premium и VPN")
        self.assertEqual(widgets.premium_btn.property("screenReaderStateText"), "Открыть Premium и VPN")
        self.assertIn("Premium", widgets.premium_btn.accessibleDescription())
        self.assertEqual(widgets.kvn_btn.text(), "Zapret KVN")
        self.assertEqual(widgets.kvn_btn.accessibleName(), "Открыть вкладку Zapret KVN")
        self.assertEqual(widgets.kvn_btn.property("screenReaderStateText"), "Открыть вкладку Zapret KVN")
        self.assertIn("вкладку Zapret KVN", widgets.kvn_btn.accessibleDescription())

        # Ссылок на этой вкладке нет — только дорога на «Справку».
        self.assertFalse(hasattr(widgets, "course_group"))
        self.assertEqual(widgets.help_card.accessibleName(), "Открыть вкладку «Справка»")
        self.assertIn("«Справка»", widgets.help_card.accessibleDescription())
        widgets.help_card.button.click()
        widgets.kvn_btn.click()
        self.assertEqual(opened, ["help", "kvn"])

    def test_subscription_status_update_reads_state_for_screen_reader(self) -> None:
        page = AboutPage.__new__(AboutPage)
        page._ui_language = "ru"
        page.sub_status_icon = _IconWidget()
        page.sub_status_label = _TextWidget()

        AboutPage.update_subscription_status(page, build_premium_display(is_premium=True, days_remaining=5))

        self.assertEqual(page.sub_status_label.text(), "Premium (осталось 5 дней)")
        self.assertEqual(page.sub_status_label.accessible_name, "Статус подписки: Premium (осталось 5 дней)")
        self.assertEqual(
            page.sub_status_label.property("screenReaderStateText"),
            "Статус подписки: Premium (осталось 5 дней)",
        )

    def test_about_tabs_read_current_section_for_screen_reader(self) -> None:
        widgets = build_about_page_tabs(
            tr_fn=lambda _key, default: default,
            on_switch_tab=lambda _index: None,
        )
        self.addCleanup(widgets.stacked_widget.deleteLater)

        self.assertEqual(widgets.tabs_pivot.accessibleName(), "Вкладки страницы о программе, выбрано: О программе")
        self.assertIn("О программе, Справка или Zapret KVN", widgets.tabs_pivot.accessibleDescription())
        self.assertEqual(
            widgets.tabs_pivot.items["about"].accessibleName(),
            "Вкладки страницы о программе: О программе, выбрано",
        )
        self.assertNotIn("support", widgets.tabs_pivot.items)

        widgets.tabs_pivot.setCurrentItem("help")

        self.assertEqual(widgets.tabs_pivot.accessibleName(), "Вкладки страницы о программе, выбрано: Справка")
        self.assertEqual(
            widgets.tabs_pivot.property("screenReaderStateText"),
            "Вкладки страницы о программе, выбрано: Справка",
        )
        self.assertEqual(
            widgets.tabs_pivot.items["about"].accessibleName(),
            "Вкладки страницы о программе: О программе, не выбрано",
        )
        self.assertEqual(
            widgets.tabs_pivot.items["help"].accessibleName(),
            "Вкладки страницы о программе: Справка, выбрано",
        )

    def test_about_language_refresh_keeps_screen_reader_names(self) -> None:
        page = AboutPage.__new__(AboutPage)
        page._ui_language = "ru"
        page.about_section_version_label = _TextWidget()
        page.about_app_name_label = _TextWidget()
        page.about_version_value_label = _TextWidget()
        page.update_btn = _TextWidget()
        page.about_section_subscription_label = _TextWidget()
        page.sub_desc_label = _TextWidget()
        page.premium_btn = _TextWidget()
        page.kvn_btn = _TextWidget()
        page._current_subscription_state = lambda: (False, None)
        page.update_subscription_status = lambda *_args: None

        AboutPage._retranslate_about_tab(page)

        self.assertEqual(page.about_app_name_label.accessible_name, "Название программы: Zapret 2 GUI")
        self.assertEqual(
            page.about_app_name_label.property("screenReaderStateText"),
            "Название программы: Zapret 2 GUI",
        )
        self.assertTrue(page.about_version_value_label.accessible_name.startswith("Версия программы: "))
        self.assertEqual(
            page.about_version_value_label.property("screenReaderStateText"),
            page.about_version_value_label.accessible_name,
        )
        self.assertEqual(page.update_btn.accessible_name, "Открыть настройки обновлений")
        self.assertEqual(page.update_btn.property("screenReaderStateText"), "Открыть настройки обновлений")
        self.assertIn("автоматической проверки", page.update_btn.accessible_description)
        self.assertEqual(
            page.sub_desc_label.property("screenReaderStateText"),
            "Описание подписки: Подписка Zapret Premium открывает доступ к дополнительным темам, "
            "приоритетной поддержке и VPN-сервису.",
        )
        self.assertEqual(page.premium_btn.accessible_name, "Открыть Premium и VPN")
        self.assertEqual(page.premium_btn.property("screenReaderStateText"), "Открыть Premium и VPN")
        self.assertIn("Premium", page.premium_btn.accessible_description)
        self.assertEqual(page.kvn_btn.text(), "Zapret KVN")
        self.assertEqual(page.kvn_btn.accessible_name, "Открыть вкладку Zapret KVN")
        self.assertEqual(page.kvn_btn.property("screenReaderStateText"), "Открыть вкладку Zapret KVN")
        self.assertIn("вкладку Zapret KVN", page.kvn_btn.accessible_description)

    def test_about_page_keeps_links_only_on_help_tab(self) -> None:
        page = AboutPage(
            open_premium=lambda: None,
            open_updates=lambda: None,
            create_open_action_worker=lambda *_args, **_kwargs: None,
            ui_state_store=MainWindowStateStore(),
        )
        self.addCleanup(page.cleanup)
        self.addCleanup(page.deleteLater)

        self.assertNotIn("support", page.tabs_pivot.items)
        self.assertEqual(page.stacked_widget.count(), 3)
        self.assertFalse(hasattr(page, "_support_discussions_card"))

        page.switch_to_tab("support")
        self.assertEqual(page.stacked_widget.currentIndex(), 0)
        self.assertEqual(page.tabs_pivot.currentRouteKey(), "about")

        import about.plans as about_page_plans

        page._switch_tab(about_page_plans.resolve_tab_index("help"))
        self.assertEqual(page.tabs_pivot.currentRouteKey(), "help")
        self.assertIn("chats_folder", page._help_link_cards)
        self.assertIn("links_channel", page._help_link_cards)


class _TextWidget:
    def __init__(self) -> None:
        self.text_value = ""
        self.accessible_name = ""
        self.accessible_description = ""
        self.properties = {}

    def setText(self, text: str) -> None:  # noqa: N802
        self.text_value = str(text)

    def text(self) -> str:
        return self.text_value

    def accessibleName(self) -> str:  # noqa: N802
        return self.accessible_name

    def setAccessibleName(self, text: str) -> None:  # noqa: N802
        self.accessible_name = str(text)

    def accessibleDescription(self) -> str:  # noqa: N802
        return self.accessible_description

    def setAccessibleDescription(self, text: str) -> None:  # noqa: N802
        self.accessible_description = str(text)

    def property(self, name: str) -> object:
        return self.properties.get(name)

    def setProperty(self, name: str, value: object) -> None:  # noqa: N802
        self.properties[name] = value


class _IconWidget:
    def __init__(self) -> None:
        self.pixmap = None

    def setPixmap(self, pixmap) -> None:  # noqa: N802
        self.pixmap = pixmap


if __name__ == "__main__":
    unittest.main()
