"""Страница «Свой DNS»: форма, ошибки под полями, сохранение и хлебные крошки."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

from app.page_names import PageName
from dns.state import CustomServerResult

SECURE = {
    "id": "secure",
    "name": "Мой шифрованный",
    "ipv4": ["203.0.113.5", "203.0.113.6"],
    "ipv6": ["2001:db8::5"],
    "doh": "https://dns.example.com/dns-query",
}


class CustomDnsServerPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        patcher = patch("dns.ui.custom_server_page.InfoBar")
        self.info_bar = patcher.start()
        self.addCleanup(patcher.stop)

    def _page(self):
        from dns.ui.custom_server_page import CustomDnsServerPage

        self.open_dns_page = Mock()
        self.feature = SimpleNamespace(create_custom_server_worker=Mock())
        page = CustomDnsServerPage(deps=SimpleNamespace(dns_feature=self.feature, open_dns_page=self.open_dns_page))
        self.addCleanup(page.deleteLater)
        page._save_lane.request = Mock()
        return page

    def _crumbs(self, page) -> list[str]:
        return [item.text for item in page.breadcrumb.items]

    # ── форма ───────────────────────────────────────────────

    def test_new_server_form_is_empty_and_named_in_breadcrumbs(self) -> None:
        page = self._page()

        self.assertTrue(page.handle_page_command("edit_custom_server", {"server": None}))

        self.assertEqual(self._crumbs(page), ["Настройка DNS", "Новый DNS"])
        self.assertEqual(
            (page.doh_edit.text(), page.addresses_edit.text(), page.name_edit.text()),
            ("", "", ""),
        )
        self.assertEqual(page.save_button.text(), "Добавить")
        self.assertFalse(page.is_editing())
        self.assertFalse(page.handle_page_command("something_else", {}))

    def test_existing_server_fills_the_fields(self) -> None:
        page = self._page()

        page.handle_page_command("edit_custom_server", {"server": SECURE})

        self.assertEqual(self._crumbs(page), ["Настройка DNS", "Мой шифрованный"])
        self.assertEqual(page.doh_edit.text(), "https://dns.example.com/dns-query")
        self.assertEqual(page.addresses_edit.text(), "203.0.113.5 203.0.113.6 2001:db8::5")
        self.assertEqual(page.name_edit.text(), "Мой шифрованный")
        self.assertEqual(page.save_button.text(), "Сохранить")

    def test_opening_another_server_clears_the_previous_error(self) -> None:
        page = self._page()
        page.open_server(None)
        page.save_button.click()
        self.assertTrue(page.error_text())

        page.open_server(SECURE)

        self.assertEqual(page.error_text(), "")
        self.assertFalse(page.doh_edit.isError())

    def test_empty_name_field_shows_the_name_the_server_will_get(self) -> None:
        page = self._page()
        page.open_server(None)

        page.doh_edit.setText("https://dns.example.com/dns-query")
        page.doh_edit.textEdited.emit(page.doh_edit.text())

        self.assertEqual(page.name_edit.placeholderText(), "dns.example.com")

    # ── сохранение ──────────────────────────────────────────

    def test_one_doh_line_is_sent_for_saving_with_address_lookup(self) -> None:
        page = self._page()
        page.open_server(None)
        page.doh_edit.setText("dns.example.com/dns-query")

        page.save_button.click()

        server = page._save_lane.request.call_args.args[0]
        self.assertEqual(server["doh"], "https://dns.example.com/dns-query")
        self.assertEqual((server["ipv4"], server["ipv6"], server["name"]), ([], [], "dns.example.com"))
        self.assertEqual(page.save_button.text(), "Проверяю сервер…")
        # Повторное нажатие, пока идёт проверка, второй раз не сохраняет.
        page.save_button.click()
        page._save_lane.request.assert_called_once()

    def test_fields_are_locked_not_disabled_while_saving(self) -> None:
        page = self._page()
        page.open_server(SECURE)

        page.save_button.click()

        self.assertEqual(page.save_button.text(), "Сохраняю…")
        for edit in (page.doh_edit, page.addresses_edit, page.name_edit):
            self.assertTrue(edit.isReadOnly())
            # Выключенное поле теряет фокус — поля только запираются.
            self.assertTrue(edit.isEnabled())
        self.assertTrue(page.save_button.isEnabled())

    def test_enter_in_a_field_saves(self) -> None:
        page = self._page()
        page.open_server(None)
        page.addresses_edit.setText("9.9.9.9")

        page.addresses_edit.returnPressed.emit()

        self.assertEqual(page._save_lane.request.call_args.args[0]["ipv4"], ["9.9.9.9"])

    def test_wrong_input_is_reported_under_its_field_and_not_saved(self) -> None:
        page = self._page()
        page.open_server(None)
        page.doh_edit.setText("https://dns.example.com/dns-query")
        page.addresses_edit.setText("9.9.9.9 кот")

        page.save_button.click()

        page._save_lane.request.assert_not_called()
        self.assertIn("кот", page.addresses_error.text())
        self.assertFalse(page.addresses_error.isHidden())
        self.assertTrue(page.addresses_hint.isHidden())
        self.assertTrue(page.addresses_edit.isError())
        self.assertFalse(page.doh_edit.isError())
        self.assertIn("Ошибка", page.addresses_error.accessibleName())

        # Правка поля убирает ошибку и возвращает пояснение.
        page.addresses_edit.textEdited.emit("9.9.9.9")
        self.assertTrue(page.addresses_error.isHidden())
        self.assertFalse(page.addresses_hint.isHidden())
        self.assertFalse(page.addresses_edit.isError())

    def test_empty_form_asks_for_doh_or_addresses(self) -> None:
        page = self._page()
        page.open_server(None)

        page.save_button.click()

        self.assertIn("адрес DoH", page.doh_error.text())
        self.assertTrue(page.doh_edit.isError())

    def test_failed_check_shows_the_reason_and_unlocks_the_form(self) -> None:
        page = self._page()
        page.open_server(None)
        page.doh_edit.setText("https://dns.example.com/dns-query")
        page.save_button.click()

        page._on_saved(CustomServerResult(success=False, error="сервер молчит", field="doh"))

        self.assertEqual(page.doh_error.text(), "сервер молчит")
        self.assertFalse(page.doh_edit.isReadOnly())
        self.assertEqual(page.save_button.text(), "Добавить")
        self.open_dns_page.assert_not_called()

    def test_taken_name_is_reported_under_the_name_field(self) -> None:
        page = self._page()
        page.open_server(SECURE)
        page.save_button.click()

        page._on_saved(CustomServerResult(success=False, error="Сервер с названием «Дом» уже есть.", field="name"))

        self.assertIn("«Дом»", page.name_error.text())
        self.assertTrue(page.name_edit.isError())

    def test_saved_server_is_announced_and_page_returns_to_dns(self) -> None:
        page = self._page()
        page.open_server(None)
        page.doh_edit.setText("https://dns.example.com/dns-query")
        page.save_button.click()

        page._on_saved(CustomServerResult(success=True, servers=(SECURE,), server=SECURE))

        shown = self.info_bar.success.call_args.kwargs
        self.assertEqual(shown["title"], "Сервер «Мой шифрованный» добавлен")
        self.assertIn("203.0.113.5, 203.0.113.6, 2001:db8::5", shown["content"])
        self.open_dns_page.assert_called_once_with()
        # Возврат кнопкой «назад» покажет сохранённый сервер: повторное нажатие не создаст второй.
        self.assertTrue(page.is_editing())
        self.assertEqual(page.addresses_edit.text(), "203.0.113.5 203.0.113.6 2001:db8::5")
        self.assertEqual(page.save_button.text(), "Сохранить")

    def test_saved_with_a_notice_warns_instead(self) -> None:
        page = self._page()
        page.open_server(SECURE)
        page.save_button.click()

        page._on_saved(CustomServerResult(success=True, server=SECURE, notice="Запросы пойдут обычным DNS."))

        self.info_bar.success.assert_not_called()
        shown = self.info_bar.warning.call_args.kwargs
        self.assertEqual((shown["title"], shown["content"]), ("Сервер «Мой шифрованный» сохранён", "Запросы пойдут обычным DNS."))

    def test_worker_failure_is_shown_without_a_field(self) -> None:
        page = self._page()
        page.open_server(SECURE)
        page.save_button.click()

        page._on_save_failed("база занята")

        self.assertIn("база занята", page.error_label.text())
        self.assertFalse(page.error_label.isHidden())
        self.assertFalse(page.name_edit.isReadOnly())

    def test_save_worker_gets_the_server(self) -> None:
        page = self._page()
        parent_free_lane = page._save_lane
        parent_free_lane._create_worker(7, SECURE)

        self.feature.create_custom_server_worker.assert_called_once_with(7, action="save", server=SECURE, parent=page)

    # ── навигация ───────────────────────────────────────────

    def test_cancel_and_breadcrumb_return_to_dns_without_saving(self) -> None:
        page = self._page()
        page.open_server(SECURE)

        page.cancel_button.click()
        page.breadcrumb.currentItemChanged.emit("dns")

        self.assertEqual(self.open_dns_page.call_count, 2)
        page._save_lane.request.assert_not_called()
        # Клик по крошке обрезает путь — он восстановлен для следующего захода.
        self.assertEqual(self._crumbs(page), ["Настройка DNS", "Мой шифрованный"])

    def test_cancel_while_checking_stops_the_lookup_and_drops_its_result(self) -> None:
        page = self._page()
        page.open_server(None)
        page.doh_edit.setText("https://dns.example.com/dns-query")
        page.save_button.click()
        worker = Mock()
        page._save_lane.runtime.worker = worker
        self.addCleanup(setattr, page._save_lane.runtime, "worker", None)

        page.cancel_button.click()

        worker.stop.assert_called_once_with()
        self.assertFalse(page.doh_edit.isReadOnly())
        self.open_dns_page.assert_called_once_with()
        # Итог брошенного сохранения приходит позже — он ничего не показывает и никуда не ведёт.
        page._on_saved(CustomServerResult(success=True, server=SECURE))
        page._on_save_failed("проверка остановлена")
        self.info_bar.success.assert_not_called()
        self.assertEqual(page.error_text(), "")
        self.open_dns_page.assert_called_once_with()

    def test_opening_a_server_drops_an_unfinished_save(self) -> None:
        page = self._page()
        page.open_server(None)
        page.doh_edit.setText("https://dns.example.com/dns-query")
        page.save_button.click()

        page.open_server(SECURE)
        page._on_saved(CustomServerResult(success=False, error="сервер молчит", field="doh"))

        self.assertEqual(page.error_text(), "")
        self.assertEqual(page.save_button.text(), "Сохранить")

    def test_cleanup_closes_the_save_lane_and_ignores_late_results(self) -> None:
        page = self._page()
        page._save_lane.close = Mock()

        page.cleanup()
        page._on_saved(CustomServerResult(success=True, server=SECURE))

        page._save_lane.close.assert_called_once_with()
        self.open_dns_page.assert_not_called()

    def test_english_interface(self) -> None:
        page = self._page()
        page.open_server(None)

        page.set_ui_language("en")

        self.assertEqual(self._crumbs(page)[1], "New DNS")
        self.assertEqual((page.doh_title.text(), page.save_button.text()), ("DoH address", "Add"))

    # ── доступность ─────────────────────────────────────────

    def test_fields_are_named_and_clear_buttons_stay_out_of_tab_order(self) -> None:
        page = self._page()
        page.open_server(SECURE)

        for edit, name in (
            (page.doh_edit, "Адрес DoH"),
            (page.addresses_edit, "IP-адреса"),
            (page.name_edit, "Название"),
        ):
            self.assertEqual(edit.accessibleName(), name)
            self.assertTrue(edit.accessibleDescription())
            buttons = [child for child in edit.findChildren(QWidget) if child is not edit and child.inherits("QAbstractButton")]
            self.assertTrue(buttons)
            for button in buttons:
                self.assertEqual(button.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertEqual(page.cancel_button.accessibleName(), "Отмена: вернуться к настройке DNS")
        self.assertIn("Настройка DNS > Мой шифрованный", page.breadcrumb.accessibleName())


class CustomDnsServerRouteTests(unittest.TestCase):
    def test_page_is_a_hidden_child_of_the_dns_page(self) -> None:
        from ui.navigation.schema import PAGE_ROUTE_SPECS, get_breadcrumb_chain

        spec = PAGE_ROUTE_SPECS[PageName.NETWORK_CUSTOM_DNS]

        self.assertEqual((spec.module_name, spec.class_name), ("dns.ui.custom_server_page", "CustomDnsServerPage"))
        self.assertTrue(spec.is_hidden)
        self.assertFalse(spec.is_top_level)
        self.assertEqual(get_breadcrumb_chain(PageName.NETWORK_CUSTOM_DNS), (PageName.NETWORK, PageName.NETWORK_CUSTOM_DNS))

    def test_opening_sends_the_server_to_the_page_and_then_shows_it(self) -> None:
        from main import window_page_presenters as presenters

        calls = []
        with patch.object(
            presenters, "send_page_command", side_effect=lambda *args, **kwargs: calls.append(("command", args, kwargs)) or True
        ), patch.object(presenters, "show_page", side_effect=lambda *args, **kwargs: calls.append(("show", args, kwargs)) or True):
            self.assertTrue(presenters.open_custom_dns_server("окно", SECURE))

        self.assertEqual(
            calls,
            [
                ("command", ("окно", PageName.NETWORK_CUSTOM_DNS, "edit_custom_server", {"server": SECURE}), {"ensure": True}),
                ("show", ("окно", PageName.NETWORK_CUSTOM_DNS), {"allow_internal": True}),
            ],
        )

    def test_page_is_not_shown_when_it_rejects_the_command(self) -> None:
        from main import window_page_presenters as presenters

        with patch.object(presenters, "send_page_command", return_value=False), patch.object(
            presenters, "show_page"
        ) as show, patch.object(presenters, "log"):
            self.assertFalse(presenters.open_custom_dns_server("окно", None))

        show.assert_not_called()

    def test_page_deps_give_narrow_actions(self) -> None:
        from ui.page_deps.system import build_custom_dns_server_page_kwargs, build_network_page_kwargs

        show_page = Mock()
        feature, opener = object(), Mock()

        custom = build_custom_dns_server_page_kwargs(page_name=PageName.NETWORK_CUSTOM_DNS, dns_feature=feature, show_page=show_page)
        custom["deps"].open_dns_page()
        network = build_network_page_kwargs(page_name=PageName.NETWORK, dns_feature=feature, open_custom_dns_server=opener)

        show_page.assert_called_once_with(PageName.NETWORK)
        self.assertIs(custom["deps"].dns_feature, feature)
        self.assertIs(network["deps"].open_custom_server, opener)


if __name__ == "__main__":
    unittest.main()
