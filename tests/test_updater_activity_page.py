from __future__ import annotations

"""Страница «Кто подключается»: что показывает человеку журнал раздачи."""

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from zapret_update import share_activity_view as activity_view
from zapret_update.share_activity import ActivityLog, Event


def tr(_key, default):
    return default


def event(**kw):
    base = dict(when=1_800_000_000.0, direction="in", via="lan", peer="10.0.0.5", what="file", detail="_internal/a.dll", size=2_500_000, result="ok")
    base.update(kw)
    return Event(**base)


class ViewWordsTests(unittest.TestCase):
    def test_file_row_names_who_from_where_what_and_how_much(self) -> None:
        row = activity_view.row_texts(event(), tr)
        self.assertEqual(row[1:], ("К вам", "Локальная сеть", "10.0.0.5", "Файл _internal/a.dll", "2.4 МБ", "отдано"))

    def test_internet_visitor_and_our_own_download_read_differently(self) -> None:
        net = activity_view.row_texts(event(via="internet", peer="93.184.216.34"), tr)
        self.assertEqual((net[1], net[2], net[3]), ("К вам", "Интернет", "93.184.216.34"))
        out = activity_view.row_texts(event(direction="out"), tr)
        self.assertEqual((out[1], out[6]), ("От вас", "получено"))

    def test_questions_in_the_network_and_exchanges_are_answered_not_given(self) -> None:
        for what in ("search", "peers", "manifest"):
            self.assertEqual(activity_view.row_texts(event(what=what, size=0), tr)[6], "отвечено")

    def test_every_outcome_has_its_own_word_and_unknown_ones_do_not_break(self) -> None:
        words = {r: activity_view.result_text(event(result=r), tr) for r in ("interrupted", "refused", "busy", "not_found", "failed")}
        self.assertEqual(len(set(words.values())), 5)
        self.assertEqual(activity_view.result_text(event(result="strange"), tr), "strange")
        self.assertIn("Посторонний", activity_view.what_text(event(what="request", detail=""), tr))
        self.assertIn("Файл ?", activity_view.what_text(event(detail=""), tr))

    def test_sizes_read_naturally(self) -> None:
        self.assertEqual([activity_view.size_text(n) for n in (0, -1, 512, 2048, 5_000_000)], ["—", "—", "512 Б", "2 КБ", "4.8 МБ"])

    def test_status_names_the_ports_and_the_internet_address(self) -> None:
        lines = activity_view.status_lines(
            {"available": True, "lan_port": 51298, "internet_running": True, "internet_address": "93.184.216.34:51310"}, tr
        )
        self.assertIn("порт 51298", lines[0])
        self.assertIn("93.184.216.34:51310", lines[1])
        off = activity_view.status_lines({"available": True, "lan_port": None, "internet_running": False}, tr)
        self.assertEqual([("не работает" in line) for line in off], [True, True])
        self.assertIn("ещё не запускалась", activity_view.status_lines({"available": False}, tr)[0])

    def test_a_switch_that_is_off_says_off_and_a_failed_one_says_not_running(self) -> None:
        both_off = activity_view.status_lines(
            {"available": True, "lan_port": None, "internet_running": False, "lan_switch": False, "internet_switch": False}, tr
        )
        self.assertEqual([("выключена" in line) for line in both_off], [True, True])
        self.assertFalse(any("не работает" in line for line in both_off))
        failed = activity_view.status_lines(
            {"available": True, "lan_port": None, "internet_running": False, "lan_switch": True, "internet_switch": True}, tr
        )
        self.assertEqual([("не работает" in line) for line in failed], [True, True])

    def test_summary_line(self) -> None:
        text = activity_view.summary_text({"events": 7, "visitors": 3, "given_bytes": 2_500_000, "taken_bytes": 0}, tr)
        self.assertIn("Обращений: 7", text)
        self.assertIn("разных гостей: 3", text)
        self.assertIn("отдано: 2.4 МБ", text)


class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _page(self, snapshot):
        from updater.ui.activity_page import SharingActivityPage

        calls = {"clear": 0, "open": 0}
        state = {"snapshot": snapshot}

        class Feature:
            def sharing_activity(self):
                return state["snapshot"]

            def clear_sharing_activity(self):
                calls["clear"] += 1
                state["snapshot"] = {"available": True, "events": [], "summary": {}}

        deps = SimpleNamespace(updater_feature=Feature(), open_servers_page=lambda: calls.__setitem__("open", calls["open"] + 1))
        page = SharingActivityPage(deps=deps)
        self.addCleanup(page.deleteLater)
        return page, calls, state

    @staticmethod
    def _snapshot(events):
        log = ActivityLog()
        for item in events:
            log.add(*item)
        return {
            "available": True, "lan_port": 51298, "internet_running": False, "internet_address": "", "notes": [],
            "events": log.snapshot(), "summary": log.summary(),
        }

    def test_rows_show_who_connected_and_where_from(self) -> None:
        page, _, _ = self._page(self._snapshot([
            ("in", "lan", "10.0.0.5", "file", "_internal/a.dll", 2_500_000, "ok"),
            ("in", "internet", "93.184.216.34", "peers", "", 120, "ok"),
        ]))
        self.assertEqual(page.table.rowCount(), 2)
        cells = [[page.table.item(r, c).text() for c in range(page.table.columnCount())] for r in range(2)]
        self.assertEqual((cells[0][2], cells[0][3]), ("Интернет", "93.184.216.34"))  # новые сверху
        self.assertEqual((cells[1][2], cells[1][3], cells[1][4]), ("Локальная сеть", "10.0.0.5", "Файл _internal/a.dll"))
        from PyQt6.QtCore import Qt

        self.assertTrue(page.table.item(0, 0).data(Qt.ItemDataRole.AccessibleTextRole))  # строка читается скринридером целиком
        self.assertIn("порт 51298", page.status_labels[0].text())

    def test_empty_journal_explains_itself_for_both_states(self) -> None:
        page, _, _ = self._page({"available": True, "events": [], "summary": {}, "lan_port": 1})
        self.assertFalse(page.empty_label.isHidden())
        self.assertIn("Пока никто не подключался", page.empty_label.text())
        off, _, _ = self._page({"available": False})
        self.assertIn("включите её", off.empty_label.text())

    def test_notes_say_why_internet_sharing_did_not_start(self) -> None:
        page, _, _ = self._page({**self._snapshot([]), "notes": ["Раздача по интернету недоступна: роутер отказался открыть порт (ответ 500)"]})
        self.assertFalse(page.notes_label.isHidden())
        self.assertIn("роутер отказался", page.notes_label.text())

    def test_page_follows_new_events_and_does_not_rebuild_without_changes(self) -> None:
        page, _, state = self._page(self._snapshot([("in", "lan", "10.0.0.5", "file", "a", 10, "ok")]))
        item = page.table.item(0, 0)
        page.refresh()
        self.assertIs(page.table.item(0, 0), item)  # без изменений таблица та же
        state["snapshot"] = self._snapshot([("in", "lan", "10.0.0.5", "file", "a", 10, "ok"), ("in", "lan", "10.0.0.6", "file", "b", 20, "ok")])
        page.refresh()
        self.assertEqual(page.table.rowCount(), 2)

    def test_clear_button_empties_the_journal(self) -> None:
        page, calls, _ = self._page(self._snapshot([("in", "lan", "10.0.0.5", "file", "a", 10, "ok")]))
        page.clear_button.click()
        self.assertEqual((calls["clear"], page.table.rowCount()), (1, 0))

    def test_a_broken_feature_does_not_break_the_page(self) -> None:
        page, _, state = self._page({"available": False})

        class Broken:
            def sharing_activity(self):
                raise RuntimeError("boom")

        page._feature = Broken()
        page._last_signature = None
        page.refresh()  # не падает
        self.assertEqual(page.table.rowCount(), 0)

    def test_breadcrumb_goes_back_to_servers_and_timer_runs_only_while_shown(self) -> None:
        page, calls, _ = self._page({"available": False})
        page._on_breadcrumb_item_changed("servers")
        self.assertEqual(calls["open"], 1)
        self.assertFalse(page._timer.isActive())
        page.show()
        self.assertTrue(page._timer.isActive())
        page.hide()
        self.assertFalse(page._timer.isActive())

    def test_empty_page_stays_compact_at_the_top_instead_of_spreading_over_the_window(self) -> None:
        page, _, _ = self._page({"available": True, "events": [], "summary": {}, "lan_port": 1, "notes": ["Раздача по интернету недоступна: роутер с UPnP не нашёлся"]})
        page.resize(1100, 1000)
        page.show()
        for _ in range(5):
            QApplication.processEvents()
        # Раньше свободное место делилось между надписями поровну: карточка вырастала на сотни точек,
        # а подпись внизу уезжала к краю окна.
        self.assertLess(page.status_card.height(), 170)
        from PyQt6.QtCore import QPoint

        def top(widget):  # всё в координатах страницы: сводка лежит в своей строке, остальное прямо на странице
            return widget.mapTo(page.content, QPoint(0, 0)).y()

        def bottom(widget):
            return top(widget) + widget.height()

        self.assertLess(top(page.empty_label) - bottom(page.summary_label), 80)
        self.assertLess(top(page.hint_label) - bottom(page.empty_label), 60)
        self.assertLess(bottom(page.hint_label), 700)  # всё уместилось в верхней части окна высотой 1000

    def test_texts_exist_in_both_languages(self) -> None:
        from app.ui_texts import tr as catalog

        for key in activity_view.DEFAULTS:
            for language in ("ru", "en"):
                self.assertTrue(catalog(key, language=language, default=""), (key, language))
        self.assertNotEqual(
            catalog("page.sharing_activity.title", language="ru", default=""),
            catalog("page.sharing_activity.title", language="en", default=""),
        )


if __name__ == "__main__":
    unittest.main()
