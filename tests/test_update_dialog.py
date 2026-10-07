from __future__ import annotations

"""Большое окно обновления, «Что нового» и их живые элементы."""

import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from updater.ui import plans
from updater.ui.fun_texts import phrases
from updater.ui.update_dialog import UpdateDialog, WhatsNewDialog
from updater.ui.update_flow import (
    PHASE_DOWNLOADING,
    PHASE_FAILED,
    PHASE_IDLE,
    PHASE_INSTALLING,
    PHASE_OFFER,
    UpdateFlow,
    UpdateOffer,
)
from ui.widgets.fun.runway import STATE_DONE, STATE_FAILED, UpdateRunway

_HISTORY = (
    {"version": "2.1", "notes": "- новая кнопка\n- <b>не тег</b>", "published_at": "2026-09-30T10:00:00Z", "url": "https://u/2.1"},
    {"version": "2.0", "notes": "починили", "published_at": "", "url": ""},
)


def _wait(predicate, timeout: float = 3.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


def _offer() -> UpdateOffer:
    return UpdateOffer(version="2.1", current_version="1.9", source="Forgejo", url="https://u/2.1", history=_HISTORY)


class UpdateFlowTests(unittest.TestCase):
    def test_phases_follow_install_service(self) -> None:
        flow = UpdateFlow()
        flow.set_offer(_offer())
        self.assertEqual(flow.phase, PHASE_OFFER)

        flow.start_download()
        self.assertTrue(flow.is_busy)
        flow.on_progress(0, 0, 0)
        self.assertFalse(flow.progress.known_size)
        flow.on_progress(40, 40 * 1024 * 1024, 100 * 1024 * 1024)
        self.assertEqual(flow.progress.percent, 40)
        self.assertTrue(flow.progress.known_size)
        self.assertIn("40.0 / 100.0", flow.progress.size_text)

        flow.on_downloaded()
        self.assertEqual(flow.phase, PHASE_INSTALLING)
        self.assertEqual(flow.progress.percent, 100)

    def test_new_offer_cannot_interrupt_download(self) -> None:
        flow = UpdateFlow()
        flow.set_offer(_offer())
        flow.start_download()

        self.assertFalse(flow.set_offer(UpdateOffer(version="9.9", current_version="1.9")))
        flow.clear_offer()

        self.assertEqual(flow.offer.version, "2.1")
        self.assertEqual(flow.phase, PHASE_DOWNLOADING)

    def test_failure_keeps_error_and_allows_new_offer(self) -> None:
        flow = UpdateFlow()
        flow.set_offer(_offer())
        flow.start_download()

        flow.on_failed("обрыв")

        self.assertEqual(flow.phase, PHASE_FAILED)
        self.assertEqual(flow.progress.error_text, "обрыв")
        flow.clear_offer()
        self.assertEqual(flow.phase, PHASE_IDLE)


class ReleaseHistoryHtmlTests(unittest.TestCase):
    def test_every_version_is_listed_with_date_lists_and_links(self) -> None:
        html = plans.release_history_html(
            ({"version": "2.1", "notes": "- пункт https://git.zapret.moe/x.\n# Раздел\nтекст", "published_at": "2026-09-30T10:00:00Z"},),
            accent_hex="#123456",
            muted_hex="#999",
            language="ru",
            empty_text="пусто",
        )

        self.assertIn("v2.1", html)
        self.assertIn("30 сентября 2026", html)
        self.assertIn("<li", html)
        self.assertIn("<b>Раздел</b>", html)
        # Точка в конце предложения не попадает в ссылку.
        self.assertIn('href="https://git.zapret.moe/x"', html)

    def test_release_text_cannot_inject_markup(self) -> None:
        html = plans.release_history_html(
            ({"version": "2.1<script>", "notes": "<img src=x onerror=1>"},),
            accent_hex="#000",
            muted_hex="#999",
            language="ru",
            empty_text="пусто",
        )

        self.assertNotIn("<img", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;img", html)

    def test_new_versions_on_top_and_earlier_section_below(self) -> None:
        html = plans.release_history_html(
            (
                {"version": "2.1", "notes": "a", "is_new": True},
                {"version": "2.0", "notes": "b", "is_new": False},
            ),
            accent_hex="#123456",
            muted_hex="#999999",
            language="ru",
            empty_text="пусто",
        )

        self.assertLess(html.index("v2.1"), html.index("новое"))
        self.assertLess(html.index("новое"), html.index("Ранее"))
        self.assertLess(html.index("Ранее"), html.index("v2.0"))
        self.assertEqual(plans.count_new_versions(({"is_new": True}, {"is_new": False}, {})), 2)

    def test_all_new_has_no_badges_or_earlier_section(self) -> None:
        html = plans.release_history_html(
            ({"version": "2.1", "notes": "a"}, {"version": "2.0", "notes": "b"}),
            accent_hex="#123456",
            muted_hex="#999999",
            language="ru",
            empty_text="пусто",
        )

        self.assertNotIn("новое", html)
        self.assertNotIn("Ранее", html)

    def test_empty_notes_say_so(self) -> None:
        html = plans.release_history_html(
            ({"version": "2.1", "notes": ""},), accent_hex="#000", muted_hex="#999", language="en", empty_text="No notes"
        )

        self.assertIn("No notes", html)


class VersionsWordTests(unittest.TestCase):
    def test_russian_plural(self) -> None:
        cases = {2: "версии", 4: "версии", 5: "версий", 11: "версий", 12: "версий", 21: "версию", 22: "версии", 25: "версий"}
        for count, word in cases.items():
            self.assertEqual(plans.versions_word(count, "ru"), word, count)
        self.assertEqual(plans.versions_word(3, "en"), "versions")


class _DialogCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.host = QWidget()
        self.host.resize(1200, 800)
        self.host.show()
        self.addCleanup(self.host.deleteLater)

    @staticmethod
    def _visible(*buttons) -> list[bool]:
        return [not button.isHidden() for button in buttons]


class UpdateDialogTests(_DialogCase):
    def _dialog(self, flow: UpdateFlow) -> UpdateDialog:
        dialog = UpdateDialog(self.host, flow=flow)
        dialog.open()
        QApplication.processEvents()
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_closed_dialog_no_longer_follows_the_flow(self) -> None:
        flow = UpdateFlow()
        flow.set_offer(_offer())
        dialog = self._dialog(flow)
        dialog._render = Mock()

        # Окно ещё затухает, но ход обновления ему уже не нужен.
        dialog.reject()
        flow.start_download()

        dialog._render.assert_not_called()
        self.assertIsNone(dialog._flow_connection)

    def test_deleted_dialog_survives_a_late_flow_signal(self) -> None:
        """В сборке Nuitka PyQt не рвёт связь сигнала с удалённым окном."""
        from PyQt6 import sip

        flow = UpdateFlow()
        flow.set_offer(_offer())
        dialog = UpdateDialog(self.host, flow=flow)
        dialog.open()
        QApplication.processEvents()
        sip.delete(dialog)

        # Сигнал всё-таки дошёл до удалённого окна: падать нельзя.
        dialog._on_flow_changed()

        self.assertIsNone(dialog._flow_connection)

    def test_offer_shows_all_versions_and_offer_buttons(self) -> None:
        flow = UpdateFlow()
        flow.set_offer(_offer())

        dialog = self._dialog(flow)

        text = dialog.browser.toPlainText()
        self.assertIn("v2.1", text)
        self.assertIn("v2.0", text)
        self.assertIn("<b>не тег</b>", text)
        self.assertIn("v1.9", dialog.subtitle_label.text())
        self.assertIn("2", dialog.subtitle_label.text())
        self.assertEqual(
            self._visible(dialog.install_btn, dialog.later_btn, dialog.skip_btn, dialog.browser_btn, dialog.telegram_btn),
            [True, True, True, True, True],
        )
        self.assertEqual(self._visible(dialog.hide_btn, dialog.close_btn), [False, False])
        self.assertTrue(dialog.download_panel.isHidden())

    def test_download_switches_to_runway_and_hide_keeps_download(self) -> None:
        flow = UpdateFlow()
        flow.set_offer(_offer())
        dialog = self._dialog(flow)
        hidden = Mock()
        dialog.hide_clicked.connect(hidden)

        flow.start_download()
        flow.on_progress(55, 55, 100)

        self.assertFalse(dialog.download_panel.isHidden())
        self.assertTrue(dialog.install_btn.isHidden())
        self.assertFalse(dialog.hide_btn.isHidden())
        self.assertEqual(dialog.runway.value(), 55.0)
        self.assertTrue(dialog.ticker.is_running())
        self.assertIn(dialog.ticker.text(), phrases("preparing") + phrases("downloading"))

        dialog.hide_btn.click()

        hidden.assert_called_once_with()
        # Окно закрылось, загрузка — нет.
        self.assertEqual(flow.phase, PHASE_DOWNLOADING)

    def test_reopened_window_shows_current_progress(self) -> None:
        flow = UpdateFlow()
        flow.set_offer(_offer())
        flow.start_download()
        flow.on_progress(70, 70, 100)

        dialog = self._dialog(flow)

        self.assertEqual(dialog.runway.value(), 70.0)
        self.assertEqual(dialog.percent_label.text(), "70%")

    def test_failure_offers_retry_and_telegram(self) -> None:
        flow = UpdateFlow()
        flow.set_offer(_offer())
        dialog = self._dialog(flow)
        flow.start_download()

        flow.on_failed("Соединение сброшено")

        self.assertEqual(dialog.runway.state(), STATE_FAILED)
        self.assertIn("Соединение сброшено", dialog.stage_label.text())
        self.assertEqual(self._visible(dialog.install_btn, dialog.telegram_btn, dialog.close_btn), [True, True, True])
        self.assertEqual(dialog.install_btn.text(), "Повторить")

    def test_buttons_report_clicks(self) -> None:
        flow = UpdateFlow()
        flow.set_offer(_offer())
        dialog = self._dialog(flow)
        clicks: list[str] = []
        dialog.install_clicked.connect(lambda: clicks.append("install"))
        dialog.link_clicked.connect(lambda url: clicks.append(url))
        dialog.telegram_clicked.connect(lambda: clicks.append("telegram"))
        dialog.skip_clicked.connect(lambda: clicks.append("skip"))

        dialog.install_btn.click()
        dialog.browser_btn.click()
        dialog.telegram_btn.click()
        dialog.skip_btn.click()

        self.assertEqual(clicks, ["install", "https://u/2.1", "telegram", "skip"])

    def test_closed_window_stops_listening_to_flow(self) -> None:
        """Состояние живёт дольше окна: закрытое окно от него отписывается."""
        flow = UpdateFlow()
        flow.set_offer(_offer())
        before = flow.receivers(flow.changed)
        dialog = self._dialog(flow)
        self.assertEqual(flow.receivers(flow.changed), before + 1)

        dialog.later_btn.click()
        self.assertTrue(_wait(lambda: flow.receivers(flow.changed) == before))


class DialogHeaderTests(_DialogCase):
    def test_title_block_is_centered_on_logo_not_hanging_below(self) -> None:
        """Раньше значок был выше текста и свисал ниже подписи — «съехал вниз»."""
        from PyQt6.QtCore import QPoint

        dialog = WhatsNewDialog(self.host, version="2.1", history=_HISTORY)
        dialog.open()
        self.addCleanup(dialog.deleteLater)
        _wait(lambda: dialog.titles_box.height() > 0)

        mascot = dialog.mascot
        logo_top = mascot.mapTo(dialog.widget, QPoint(0, 0)).y() + mascot.logo_top()
        logo_side = mascot.height() - 2 - mascot.logo_top()
        box = dialog.titles_box
        box_top = box.mapTo(dialog.widget, QPoint(0, 0)).y()

        self.assertLessEqual(abs((box_top + box.height() / 2) - (logo_top + logo_side / 2)), 2)
        # Значок не выше блока текста больше чем на пару пикселей.
        self.assertLessEqual(logo_side - box.height(), 2)


class WhatsNewDialogTests(_DialogCase):
    def test_loading_then_history(self) -> None:
        dialog = WhatsNewDialog(self.host, version="2.1", loading=True)
        dialog.open()
        self.addCleanup(dialog.deleteLater)
        QApplication.processEvents()
        self.assertFalse(dialog.loading_panel.isHidden())
        self.assertTrue(dialog.ticker.is_running())

        dialog.set_history(_HISTORY)

        self.assertTrue(dialog.loading_panel.isHidden())
        self.assertFalse(dialog.ticker.is_running())
        self.assertIn("v2.0", dialog.browser.toPlainText())
        self.assertIn("2.1", dialog.title_label.text())
        self.assertEqual(dialog.subtitle_label.text(), "Изменения за 2 версии")
        self.assertFalse(dialog.browser_btn.isHidden())

    def test_new_badge_is_a_rounded_picture_not_a_flat_block(self) -> None:
        history = (
            {"version": "2.1", "notes": "a", "is_new": True},
            {"version": "2.0", "notes": "b", "is_new": False},
        )
        dialog = WhatsNewDialog(self.host, version="2.1", history=history)
        self.addCleanup(dialog.deleteLater)

        html = dialog.browser.toHtml()

        self.assertIn("zapret-badge://new/", html)
        self.assertNotIn("background-color", html.split("Ранее")[0])

    def test_error_still_links_to_release_page(self) -> None:
        dialog = WhatsNewDialog(self.host, version="2.1", loading=True)
        self.addCleanup(dialog.deleteLater)
        links: list[str] = []
        dialog.link_clicked.connect(links.append)

        dialog.show_error("нет сети")
        dialog.browser_btn.click()

        self.assertIn("нет сети", dialog.subtitle_label.text())
        self.assertTrue(links[0].endswith("/releases/tag/2.1"))


class UpdateRunwayTests(_DialogCase):
    def test_states(self) -> None:
        runway = UpdateRunway(self.host)
        runway.show()
        self.assertTrue(runway.is_indeterminate())

        runway.set_value(150)
        self.assertEqual(runway.value(), 100.0)
        self.assertFalse(runway.is_indeterminate())

        runway.finish()
        self.assertEqual(runway.state(), STATE_DONE)
        self.assertFalse(runway.is_animating())

        runway.reset()
        runway.fail()
        self.assertEqual(runway.state(), STATE_FAILED)
        self.assertIn("ошибка", runway.property("screenReaderStateText"))

    def test_runner_follows_progress(self) -> None:
        runway = UpdateRunway(self.host)
        runway.resize(400, runway.height())
        runway.set_value(0)
        start = runway.runner_x()
        runway.set_value(100)
        runway.hide()  # без анимации процент встаёт сразу

        self.assertGreater(runway.runner_x(), start + 200)


class FunTextsTests(unittest.TestCase):
    def test_every_stage_has_jokes_in_both_languages(self) -> None:
        for kind in ("preparing", "downloading", "installing", "failed", "whats_new"):
            self.assertTrue(phrases(kind, "ru"), kind)
            self.assertTrue(phrases(kind, "en"), kind)
        self.assertNotEqual(phrases("downloading", "ru"), phrases("downloading", "en"))

    def test_joke_pools_are_big_unique_and_fit_one_line(self) -> None:
        # Чтобы от обновления к обновлению текст был разный, наборы большие;
        # окно-продолжение не переносит строки, поэтому фразы короткие.
        minimum = {"preparing": 10, "downloading": 20, "installing": 10, "failed": 6, "restarting": 15, "whats_new": 6}
        for language in ("ru", "en"):
            for kind, count in minimum.items():
                pool = phrases(kind, language)
                self.assertGreaterEqual(len(pool), count, (language, kind))
                self.assertEqual(len(pool), len(set(pool)), (language, kind))
                for phrase in pool:
                    self.assertLessEqual(len(phrase), 70, phrase)


if __name__ == "__main__":
    unittest.main()
