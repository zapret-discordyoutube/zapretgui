"""Страница «Свой DNS»: шапка с итогом, строки полей, сохранение и хлебные крошки."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget

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

    @staticmethod
    def _type(edit, text: str) -> None:
        """Ввод текста пользователем: setText сам сигнал правки не шлёт."""
        edit.setText(text)
        edit.textEdited.emit(text)

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

    def test_header_shows_what_the_typed_text_will_become(self) -> None:
        page = self._page()
        page.open_server(None)
        self.assertEqual((page.eyebrow_label.text(), page.title_text.text()), ("Новый сервер", "Свой DNS"))
        self.assertIn("одного из двух достаточно", page.detail_label.text())

        self._type(page.doh_edit, "https://dns.example.com/dns-query")

        # Одна строка DoH: название и адреса подставятся сами — это видно сразу.
        self.assertEqual(page.title_text.text(), "dns.example.com")
        self.assertIn("найдёт и проверит сама", page.detail_label.text())
        self.assertEqual(page.name_edit.placeholderText(), "dns.example.com")
        self.assertEqual(page.addresses_edit.placeholderText(), "Найдутся сами")

        self._type(page.addresses_edit, "203.0.113.5 2001:db8::5")
        self.assertEqual(page.detail_label.text(), "Шифрованный DNS (DoH) · 203.0.113.5 · 2001:db8::5")

        self._type(page.doh_edit, "")
        self._type(page.name_edit, "Мой сервер")
        self.assertEqual(page.title_text.text(), "Мой сервер")
        self.assertEqual(page.detail_label.text(), "Обычный DNS без шифрования · 203.0.113.5 · 2001:db8::5")
        self.assertEqual(page.addresses_edit.placeholderText(), "9.9.9.9 149.112.112.112")
        self.assertIn("Мой сервер", page.header.accessibleDescription())

    def test_half_typed_field_keeps_the_last_clear_summary(self) -> None:
        page = self._page()
        page.open_server(None)
        self._type(page.addresses_edit, "9.9.9.9")
        clear = page.detail_label.text()

        # «9.9.» — ещё не адрес: шапка не мигает ошибкой, пока человек печатает.
        self._type(page.addresses_edit, "9.9.9.9 149.112.")

        self.assertEqual(page.detail_label.text(), clear)
        self.assertEqual(page.error_text(), "")
        self.assertEqual(page.doh_edit.text(), "")

    def test_typing_by_hand_is_never_moved_between_fields(self) -> None:
        page = self._page()
        page.open_server(None)

        # По одному знаку — это набор, а не вставка: текст остаётся там, где его пишут.
        text = "dns.example.com 9.9.9.9"
        for length in range(1, len(text) + 1):
            self._type(page.doh_edit, text[:length])

        self.assertEqual((page.doh_edit.text(), page.addresses_edit.text()), (text, ""))

    def test_pasted_doh_with_addresses_is_spread_over_the_fields(self) -> None:
        page = self._page()
        page.open_server(None)

        # Так выглядит строка из «Копировать DNS в буфер обмена».
        self._type(page.doh_edit, "https://dns.example.com/dns-query, 203.0.113.5, 2001:db8::5")

        self.assertEqual(page.doh_edit.text(), "https://dns.example.com/dns-query")
        self.assertEqual(page.addresses_edit.text(), "203.0.113.5 2001:db8::5")

        page.open_server(None)
        self._type(page.addresses_edit, "dns.example.com/dns-query 203.0.113.5")
        self.assertEqual((page.doh_edit.text(), page.addresses_edit.text()), ("dns.example.com/dns-query", "203.0.113.5"))

    def test_paste_does_not_overwrite_a_filled_field(self) -> None:
        page = self._page()
        page.open_server(SECURE)

        self._type(page.doh_edit, "https://other.example/dns-query 198.51.100.7")

        self.assertEqual(page.addresses_edit.text(), "203.0.113.5 203.0.113.6 2001:db8::5")
        self.assertEqual(page.doh_edit.text(), "https://other.example/dns-query 198.51.100.7")

    def test_existing_server_is_shown_in_the_header(self) -> None:
        page = self._page()

        page.open_server(SECURE)

        self.assertEqual((page.eyebrow_label.text(), page.title_text.text()), ("Свой сервер", "Мой шифрованный"))
        self.assertEqual(
            page.detail_label.text(),
            "Шифрованный DNS (DoH) · 203.0.113.5 · 203.0.113.6 · 2001:db8::5",
        )

    def test_page_is_built_from_standard_setting_rows(self) -> None:
        from ui.widgets.win11_controls import Win11ControlRow

        page = self._page()

        for row, edit in (
            (page.doh_row, page.doh_edit),
            (page.addresses_row, page.addresses_edit),
            (page.name_row, page.name_edit),
        ):
            self.assertIsInstance(row, Win11ControlRow)
            self.assertIs(edit.parent(), row)
            # Строка с пояснением — стандартной высоты, как на остальных страницах настроек.
            self.assertEqual(row.height(), 70)
            self.assertTrue(row.contentLabel.text())
        # Кнопки действия стоят в шапке, рядом с итогом.
        self.assertIs(page.save_button.parent(), page.header)
        self.assertIs(page.cancel_button.parent(), page.header)

    def test_form_keeps_rows_whole_when_a_long_error_wraps_in_a_narrow_window(self) -> None:
        page = self._page()
        window = QMainWindow()
        self.addCleanup(window.deleteLater)
        window.setCentralWidget(page)
        window.resize(820, 560)
        window.show()
        page.open_server(None)
        page.doh_edit.setText("https://dns.example.com/dns-query")
        page.save_button.click()

        page._on_saved(
            CustomServerResult(
                success=False,
                field="doh",
                error="Сервер dns.example.com найден (203.0.113.5), но на запрос DoH не ответил: сервер молчит. "
                "Проверьте адрес DoH; возможно, сервер закрыт на вашей линии.",
            )
        )
        self._app.processEvents()

        rows = (page.doh_row, page.addresses_row, page.name_row)
        self.assertGreater(page.error_label.height(), 30)
        for upper, lower in zip(rows, rows[1:]):
            self.assertGreaterEqual(lower.y(), upper.y() + upper.height())
        self.assertLessEqual(rows[-1].y() + rows[-1].height(), page.card.height())
        window.takeCentralWidget()

    # ── сохранение ──────────────────────────────────────────

    def test_one_doh_line_is_sent_for_saving_with_address_lookup(self) -> None:
        page = self._page()
        page.open_server(None)
        page.doh_edit.setText("dns.example.com/dns-query")

        page.save_button.click()

        server = page._save_lane.request.call_args.args[0]
        self.assertEqual(server["doh"], "https://dns.example.com/dns-query")
        self.assertEqual((server["ipv4"], server["ipv6"], server["name"]), ([], [], "dns.example.com"))
        self.assertEqual(page.save_button.text(), "Проверяю…")
        self.assertIn("проверяю каждый запросом DoH", page.detail_label.text())
        # Пока идёт проверка, вокруг значка бежит комета — как при смене DNS.
        self.assertTrue(page.badge.is_busy())
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

    def test_wrong_input_is_reported_in_the_header_and_marks_its_field(self) -> None:
        page = self._page()
        page.open_server(None)
        page.doh_edit.setText("https://dns.example.com/dns-query")
        page.addresses_edit.setText("9.9.9.9 кот")

        page.save_button.click()

        page._save_lane.request.assert_not_called()
        self.assertIn("кот", page.error_label.text())
        # Ошибка встаёт на место строки состояния, её поле подчёркнуто.
        self.assertFalse(page.error_label.isHidden())
        self.assertTrue(page.detail_label.isHidden())
        self.assertTrue(page.addresses_edit.isError())
        self.assertFalse(page.doh_edit.isError())
        self.assertIn("Ошибка", page.error_label.accessibleName())
        self.assertIn("кот", page.header.accessibleDescription())

        # Правка поля убирает ошибку и возвращает строку состояния.
        page.addresses_edit.setText("9.9.9.9")
        page.addresses_edit.textEdited.emit("9.9.9.9")
        self.assertTrue(page.error_label.isHidden())
        self.assertFalse(page.detail_label.isHidden())
        self.assertFalse(page.addresses_edit.isError())
        self.assertEqual(page.detail_label.text(), "Шифрованный DNS (DoH) · 9.9.9.9")

    def test_empty_form_asks_for_doh_or_addresses(self) -> None:
        page = self._page()
        page.open_server(None)

        page.save_button.click()

        self.assertIn("адрес DoH", page.error_label.text())
        self.assertTrue(page.doh_edit.isError())

    def test_failed_check_shows_the_reason_and_unlocks_the_form(self) -> None:
        page = self._page()
        page.open_server(None)
        page.doh_edit.setText("https://dns.example.com/dns-query")
        page.save_button.click()

        page._on_saved(CustomServerResult(success=False, error="сервер молчит", field="doh"))

        self.assertEqual(page.error_label.text(), "сервер молчит")
        self.assertTrue(page.doh_edit.isError())
        self.assertFalse(page.doh_edit.isReadOnly())
        self.assertFalse(page.badge.is_busy() and page._busy)
        self.assertEqual(page.save_button.text(), "Добавить")
        self.open_dns_page.assert_not_called()

    def test_taken_name_is_reported_under_the_name_field(self) -> None:
        page = self._page()
        page.open_server(SECURE)
        page.save_button.click()

        page._on_saved(CustomServerResult(success=False, error="Сервер с названием «Дом» уже есть.", field="name"))

        self.assertIn("«Дом»", page.error_label.text())
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
        self.assertEqual((page.doh_row.titleLabel.text(), page.save_button.text()), ("DoH address", "Add"))
        self.assertEqual(page.eyebrow_label.text(), "New server")
        self.assertIn("DoH address", page.detail_label.text())

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
        feature, opener, external = object(), Mock(), Mock()

        custom = build_custom_dns_server_page_kwargs(page_name=PageName.NETWORK_CUSTOM_DNS, dns_feature=feature, show_page=show_page)
        custom["deps"].open_dns_page()
        network = build_network_page_kwargs(
            page_name=PageName.NETWORK,
            dns_feature=feature,
            external_actions_feature=external,
            open_custom_dns_server=opener,
        )

        show_page.assert_called_once_with(PageName.NETWORK)
        self.assertIs(custom["deps"].dns_feature, feature)
        self.assertIs(network["deps"].open_custom_server, opener)
        # Странице нужна только задача «открыть ссылку», а не все внешние действия.
        self.assertIs(network["deps"].create_open_url_worker, external.create_open_url_worker)


if __name__ == "__main__":
    unittest.main()
