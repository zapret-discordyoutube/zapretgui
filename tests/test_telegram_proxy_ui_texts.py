from __future__ import annotations

import inspect
import unittest


class TelegramProxyUiTextsTests(unittest.TestCase):
    def test_settings_text_plan_matches_clean_main_scenario(self) -> None:
        from telegram_proxy.ui import text_plan

        plan = text_plan.TELEGRAM_PROXY_SETTINGS_TEXT

        self.assertEqual(
            plan.page_subtitle,
            "Локальный прокси для Telegram. Используйте его, если Telegram подключается нестабильно.",
        )
        self.assertEqual(plan.setup_title, "Подключить Telegram")
        self.assertIn("Telegram сам предложит добавить прокси", plan.setup_description)
        self.assertIn("Zastogram", plan.setup_description)
        self.assertEqual(plan.upstream_group_title, "Внешний прокси")
        self.assertEqual(plan.upstream_toggle_title, "Использовать внешний прокси")
        self.assertEqual(
            plan.upstream_toggle_description,
            "Резервный SOCKS5, если часть серверов Telegram не отвечает.",
        )
        self.assertEqual(plan.advanced_nav_title, "Продвинутые настройки")
        self.assertEqual(plan.advanced_nav_description, "Внешний прокси, Cloudflare, DC→IP, пул и буфер")

        joined = "\n".join(plan)
        self.assertNotIn("ПЕРЕЗАПУСТИТЬ", joined)
        self.assertNotIn("upstream", joined)
        self.assertNotIn("WSS relay", joined)
        self.assertNotIn("fallback", joined)

    def test_group_titles_do_not_repeat_first_row_title(self) -> None:
        # Заголовок группы прямо над строкой с тем же названием — лишний повтор.
        from telegram_proxy.ui import text_plan

        plan = text_plan.TELEGRAM_PROXY_SETTINGS_TEXT

        self.assertNotEqual(plan.upstream_group_title, plan.upstream_toggle_title)
        self.assertNotEqual(plan.cloudflare_group_title, plan.cloudflare_toggle_title)

    def test_setup_hint_is_tooltip_not_floating_caption(self) -> None:
        from telegram_proxy.ui import settings_build

        source = inspect.getsource(settings_build)

        self.assertIn("set_tooltip(setup_title_label, text.setup_description)", source)
        self.assertNotIn("setup_desc_label", source)
        self.assertNotIn("setup_fallback_label", source)
        self.assertNotIn("QuickActionsBar", source)

    def test_advanced_page_exposes_cloudflare_test_and_copy_actions(self) -> None:
        from telegram_proxy.ui import advanced_build, settings_build

        signature = inspect.signature(advanced_build.build_telegram_proxy_advanced_panel)
        source = inspect.getsource(advanced_build)
        main_signature = inspect.signature(settings_build.build_telegram_proxy_settings_panel)

        self.assertIn("on_test_cloudflare", signature.parameters)
        self.assertIn("on_copy_cloudflare_dns", signature.parameters)
        self.assertIn("on_test_cloudflare_worker", signature.parameters)
        self.assertIn("on_copy_cloudflare_worker_code", signature.parameters)
        self.assertIn("on_copy_fake_tls_nginx_config", main_signature.parameters)
        for name in (
            "cloudflare_test_btn",
            "cloudflare_dns_btn",
            "cloudflare_worker_test_btn",
            "cloudflare_worker_code_btn",
            "Проверить",
            "DNS",
            "Код Worker",
        ):
            self.assertIn(name, source)

    def test_advanced_page_exposes_all_technical_options(self) -> None:
        from telegram_proxy.ui import advanced_build, advanced_page, settings_build

        build_source = inspect.getsource(advanced_build)
        main_build_source = inspect.getsource(settings_build)
        page_source = inspect.getsource(advanced_page.TelegramProxyAdvancedPage)

        for name in ("dc_ip_edit", "pool_size_spin", "buffer_kb_spin", "cloudflare_toggle", "upstream_toggle"):
            self.assertIn(name, build_source)
        for name in ("fake_tls_domain_edit", "proxy_protocol_toggle", "mtproxy_secret_edit", "Nginx"):
            self.assertIn(name, main_build_source)
        self.assertIn("_on_pool_size_changed", page_source)
        self.assertIn("_on_buffer_kb_changed", page_source)
        self.assertIn("_on_dc_ip_changed", page_source)

    def test_proxy_mode_choice_marks_socks5_as_recommended(self) -> None:
        from telegram_proxy.ui import settings_build

        source = inspect.getsource(settings_build)

        self.assertIn("SOCKS5 (рекомендуется)", source)
        self.assertIn("MTProxy (продвинутый)", source)

    def test_every_setting_row_uses_its_own_icon(self) -> None:
        import re

        from telegram_proxy.ui import advanced_build, settings_build

        icons = re.findall(
            r'"(fa5[sb]\.[a-z0-9-]+)"',
            inspect.getsource(settings_build) + inspect.getsource(advanced_build),
        )

        self.assertTrue(icons)
        self.assertEqual(len(icons), len(set(icons)), icons)


if __name__ == "__main__":
    unittest.main()
