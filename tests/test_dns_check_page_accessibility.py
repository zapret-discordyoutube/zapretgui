import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from dns.ui.dns_check_page import DNSCheckPage


class _DnsFeatureStub:
    pass


class DNSCheckPageAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_actions_status_and_results_are_named_for_screen_reader(self) -> None:
        page = DNSCheckPage(dns_feature=_DnsFeatureStub())
        self.addCleanup(page.deleteLater)

        self.assertEqual(page.check_button.accessibleName(), "Начать полную проверку DNS, доступно")
        self.assertEqual(
            page.check_button.property("screenReaderStateText"),
            "Начать полную проверку DNS, доступно",
        )
        self.assertIn("расширенный отчёт", page.check_button.accessibleDescription())
        self.assertEqual(page.save_button.accessibleName(), "Сохранить результаты проверки DNS, недоступно")
        self.assertEqual(
            page.save_button.property("screenReaderStateText"),
            "Сохранить результаты проверки DNS, недоступно",
        )
        self.assertIn("текстовый файл", page.save_button.accessibleDescription())
        self.assertEqual(page.status_label.accessibleName(), "Статус проверки DNS: Сравниваем ответ DNS с эталоном и видим, подменяет ли провайдер адреса")
        self.assertEqual(page.log_button.accessibleName(), "Открыть подробный лог проверки DNS, недоступно")
        self.assertIn("текстовый отчёт", page.log_button.accessibleDescription())

    def test_detailed_log_is_handed_to_the_host_page_as_plain_text(self) -> None:
        page = DNSCheckPage(dns_feature=_DnsFeatureStub())
        self.addCleanup(page.deleteLater)

        page.append_result("www.youtube.com — сайт")
        page.append_result("x < y & z")
        requested = []
        page.report_requested.connect(requested.append)

        self.assertTrue(page.log_button.isEnabled())
        page.log_button.click()

        # Раскраску делает редактор страницы отчёта, поэтому текст уходит как есть, без разметки.
        (report,) = requested
        self.assertEqual(report.text, "www.youtube.com — сайт\nx < y & z")
        self.assertEqual(report.title, "Подробный лог проверки DNS")

    def test_embedded_tab_is_compact(self) -> None:
        """Во вкладке BlockCheck лишние заголовки и карточка «Что проверяем» съедали место."""
        page = DNSCheckPage(dns_feature=_DnsFeatureStub(), embedded=True)
        self.addCleanup(page.deleteLater)

        self.assertTrue(page.title_label.isHidden())
        # Отдельной карточки с кнопками нет: они стоят в панели итога, под главной фразой.
        self.assertIs(page.control_card, page.summary_panel)
        self.assertIs(page.summary_panel.actions.itemAt(0).widget(), page.check_button)
        self.assertIs(page.progress_bar.parentWidget(), page.summary_panel)
        self.assertFalse(hasattr(page, "info_card"))

    def test_progress_bar_exposes_screen_reader_state(self) -> None:
        page = DNSCheckPage(dns_feature=_DnsFeatureStub())
        self.addCleanup(page.deleteLater)

        self.assertEqual(page.progress_bar.accessibleName(), "Ход проверки DNS: не выполняется")
        self.assertEqual(
            page.progress_bar.property("screenReaderStateText"),
            "Ход проверки DNS: не выполняется",
        )

        page._apply_interaction_state(
            check_enabled=False,
            save_enabled=False,
            progress_visible=True,
        )

        self.assertEqual(page.progress_bar.accessibleName(), "Ход проверки DNS: выполняется")
        self.assertEqual(
            page.progress_bar.property("screenReaderStateText"),
            "Ход проверки DNS: выполняется",
        )
        self.assertEqual(
            page.check_button.property("screenReaderStateText"),
            "Начать полную проверку DNS, недоступно",
        )
        self.assertEqual(
            page.save_button.property("screenReaderStateText"),
            "Сохранить результаты проверки DNS, недоступно",
        )

        page._apply_interaction_state(
            check_enabled=True,
            save_enabled=True,
            progress_visible=False,
        )

        self.assertEqual(
            page.save_button.property("screenReaderStateText"),
            "Сохранить результаты проверки DNS, доступно",
        )

    def test_full_check_start_clears_previous_log(self) -> None:
        page = DNSCheckPage(dns_feature=_DnsFeatureStub())
        self.addCleanup(page.deleteLater)
        page._check_runtime = _StartRuntimeStub()
        page._check_state.runtime = page._check_runtime
        page.append_result("Старый результат DNS-проверки")
        self.assertTrue(page.log_button.isEnabled())

        page.start_check()

        self.assertFalse(page.log_button.isEnabled())
        self.assertEqual(page._resolve_save_results_text(None), "")
        self.assertEqual(page._results_plain_text_cache, "")
        self.assertEqual(
            page.log_button.property("screenReaderStateText"),
            "Открыть подробный лог проверки DNS, недоступно",
        )

    def test_no_separate_quick_check_button(self) -> None:
        """Обычная проверка укладывается в секунду-две, отдельная «Быстрая» только путала."""
        page = DNSCheckPage(dns_feature=_DnsFeatureStub())
        self.addCleanup(page.deleteLater)

        self.assertFalse(hasattr(page, "quick_check_button"))
        self.assertFalse(hasattr(page, "quick_dns_check"))


class _StartRuntimeStub:
    def __init__(self) -> None:
        self.started = False

    def is_running(self) -> bool:
        return False

    def start_qobject_worker(self, **_kwargs) -> None:
        self.started = True

    def start_qthread_worker(self, **_kwargs) -> None:
        self.started = True


if __name__ == "__main__":
    unittest.main()
