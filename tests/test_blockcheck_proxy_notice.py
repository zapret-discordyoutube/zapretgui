"""Системный прокси перед проверкой: уведомление с кнопкой, а не молчаливо «всё работает»."""

import unittest
from unittest.mock import Mock, patch

from blockcheck import proxy_notice


def _state(manual="", pac=""):
    return proxy_notice.ProxyState(manual, pac)


class ProxyNoticeTests(unittest.TestCase):
    def setUp(self) -> None:
        proxy_notice._last_shown = None
        self.addCleanup(setattr, proxy_notice, "_last_shown", None)

    def test_no_proxy_means_no_notice(self) -> None:
        self.assertIsNone(proxy_notice.notice(_state()))

    def test_manual_proxy_gets_the_disable_button(self) -> None:
        payload = proxy_notice.notice(_state(manual="127.0.0.1:8080"))

        self.assertIn("127.0.0.1:8080", payload["content"])
        self.assertEqual(payload["source"], "blockcheck.proxy")
        self.assertEqual([button["kind"] for button in payload["buttons"]], ["disable_proxy"])

    def test_pac_script_alone_has_no_button_that_would_change_nothing(self) -> None:
        payload = proxy_notice.notice(_state(pac="http://example.test/proxy.pac"))

        self.assertIn("proxy.pac", payload["content"])
        self.assertEqual(payload["buttons"], [])

    def test_detect_reads_the_platform_settings(self) -> None:
        settings = Mock()
        settings.system_settings.proxy_settings.return_value = ("10.0.0.1:3128", "")
        with patch("platforms.current", return_value=settings):
            self.assertEqual(proxy_notice.detect().manual, "10.0.0.1:3128")
        settings.system_settings.proxy_settings.side_effect = OSError("нет ключа")
        with patch("platforms.current", return_value=settings):
            self.assertFalse(proxy_notice.detect().enabled)

    def test_notify_goes_to_the_window_callback_and_is_not_repeated_at_once(self) -> None:
        notify = Mock()
        with patch.object(proxy_notice, "detect", return_value=_state(manual="1.2.3.4:80")):
            self.assertTrue(proxy_notice.notify_if_system_proxy(notify, now=lambda: 1000.0))
            # Вторая проверка подряд не повторяет то же уведомление...
            self.assertFalse(proxy_notice.notify_if_system_proxy(notify, now=lambda: 1030.0))
            # ...а через пару минут напоминает снова.
            self.assertTrue(proxy_notice.notify_if_system_proxy(notify, now=lambda: 1000.0 + proxy_notice.REPEAT_AFTER_S + 1))
        self.assertEqual(notify.call_count, 2)

    def test_proxy_switched_off_resets_the_throttle(self) -> None:
        notify = Mock()
        with patch.object(proxy_notice, "detect", return_value=_state(manual="1.2.3.4:80")):
            proxy_notice.notify_if_system_proxy(notify, now=lambda: 1000.0)
        with patch.object(proxy_notice, "detect", return_value=_state()):
            self.assertFalse(proxy_notice.notify_if_system_proxy(notify, now=lambda: 1001.0))
        with patch.object(proxy_notice, "detect", return_value=_state(manual="1.2.3.4:80")):
            self.assertTrue(proxy_notice.notify_if_system_proxy(notify, now=lambda: 1002.0))

    def test_without_callback_the_window_gets_a_plain_bar(self) -> None:
        with (
            patch.object(proxy_notice, "detect", return_value=_state(manual="1.2.3.4:80")),
            patch("ui.fluent_widgets.InfoBarHelper.warning") as bar,
        ):
            self.assertTrue(proxy_notice.notify_if_system_proxy(None, window=object(), now=lambda: 5.0))
        bar.assert_called_once()


if __name__ == "__main__":
    unittest.main()
