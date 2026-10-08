from __future__ import annotations

import inspect
import unittest


class ControlPageImmediateStartupTests(unittest.TestCase):
    def test_control_pages_build_settings_as_a_block_below_the_first_screen(self) -> None:
        """Настройки главной страницы — блок, который собирается позже страницы.

        История. Отложенную сборку таймерами пробовали дважды и откатывали:
        таймеры откладывали всё подряд, в том числе видимое, и интерфейс
        появлялся дольше. 2026-10-08 владелец решил: то, чего не видно, не
        строить сразу, а достраивать позже.

        Отличие от прошлых попыток — в ядре ui.block_build: всё, что видно при
        открытии, собирается сразу и без таймеров (в том числе этот блок, если
        окно высокое и он попадает в первый экран); откладывается только то,
        что ниже края окна. Свои таймеры для этого страница не заводит.
        """
        import presets.ui.control.zapret1.page as zapret1_page
        import presets.ui.control.zapret2.page as zapret2_page

        for page_cls in (zapret1_page.Zapret1ModeControlPage, zapret2_page.Zapret2ModeControlPage):
            with self.subTest(page_cls=page_cls.__name__):
                page_source = inspect.getsource(page_cls)
                build_ui_source = inspect.getsource(page_cls._build_ui)

                self.assertIn("self._add_settings_blocks()", build_ui_source)
                blocks_source = inspect.getsource(page_cls._add_settings_blocks)
                self.assertIn("self.add_lazy_block(", blocks_source)
                for block in ("PROGRAM_SETTINGS_BLOCK", "WINDOWS_SETTINGS_BLOCK", "FINE_TUNING_BLOCK", "LAST_MESSAGE_BLOCK"):
                    self.assertIn(block, blocks_source)
                # Группу никто не собирает напрямую в обход ядра.
                for builder in ("_build_program_settings_block", "_build_windows_settings_block", "_build_fine_tuning_block"):
                    self.assertNotIn(f"self.{builder}()", page_source)
                # Первый экран страница собирает сама и сразу: таймеров в сборке нет.
                self.assertNotIn("QTimer", build_ui_source)
                self.assertNotIn("singleShot", build_ui_source)
                self.assertIn("_attach_program_settings_runtime", build_ui_source)
                self.assertIn("_schedule_additional_settings_reload(force=True)", build_ui_source)
                # Старая отложенная сборка на собственных таймерах не возвращается.
                self.assertNotIn("_build_deferred_sections", page_source)
                self.assertNotIn("_run_deferred_show_work", page_source)
                self.assertNotIn("_startup_can_run_deferred_sections", page_source)
                self.assertNotIn("STARTUP_DEFERRED_SECTIONS", page_source)
                self.assertNotIn("_build_settings_sections_deferred", page_source)

                # Блок собран позже страницы: настройки, пришедшие раньше него,
                # применяются в конце его сборки.
                for builder in ("_build_program_settings_block", "_build_windows_settings_block", "_build_fine_tuning_block"):
                    self.assertIn(
                        "self._apply_settings_that_came_before_the_block()",
                        inspect.getsource(getattr(page_cls, builder)),
                    )
                # Перевод и экскурсия обращаются к карточкам блоков — достраивают их сами.
                self.assertIn("self.ensure_all_blocks()", inspect.getsource(page_cls.set_ui_language))
                self.assertIn("self.ensure_block(block)", inspect.getsource(page_cls.onboarding_target))

    def test_additional_settings_workers_are_imported_only_when_requested(self) -> None:
        import presets.ui.control.zapret2.page as zapret2_page

        module_source = inspect.getsource(zapret2_page)
        import_block = "\n".join(module_source.splitlines()[:90])
        reload_source = inspect.getsource(zapret2_page.Zapret2ModeControlPage._schedule_additional_settings_reload)
        save_source = inspect.getsource(zapret2_page.Zapret2ModeControlPage._start_additional_settings_save_worker)

        self.assertNotIn("create_additional_settings_worker as create_control_additional_settings_worker", import_block)
        self.assertIn("create_additional_settings_worker as create_control_additional_settings_worker", reload_source)
        self.assertIn("_create_additional_settings_save_worker", save_source)

    def test_zapret2_page_runtime_is_not_imported_on_page_module_import(self) -> None:
        import presets.ui.control.zapret2.page as zapret2_page
        import presets.ui.control.zapret2.runtime_helpers as runtime_helpers

        page_source = inspect.getsource(zapret2_page)
        page_import_block = "\n".join(page_source.splitlines()[:90])
        helper_source = inspect.getsource(runtime_helpers)
        helper_import_block = "\n".join(helper_source.splitlines()[:30])
        after_ui_source = inspect.getsource(zapret2_page.Zapret2ModeControlPage._after_ui_built)

        self.assertNotIn("import presets.ui.control.zapret2.page_runtime", page_import_block)
        self.assertNotIn("import presets.ui.control.zapret2.page_runtime", helper_import_block)
        self.assertIn("def _zapret2_page_runtime", page_source)
        self.assertNotIn("_update_stop_winws_button_text()", after_ui_source)

    def test_zapret2_first_page_keeps_theme_refresh_imports_lazy(self) -> None:
        import presets.ui.control.shared_builders as shared_builders
        import presets.ui.control.top_summary_widget as top_summary_widget
        import presets.ui.control.zapret2.page as zapret2_page
        import ui.pages.base_page as base_page

        shared_source = inspect.getsource(shared_builders)
        shared_import_block = "\n".join(shared_source.splitlines()[:30])
        top_summary_source = inspect.getsource(top_summary_widget)
        top_summary_import_block = "\n".join(top_summary_source.splitlines()[:30])
        zapret2_page_source = inspect.getsource(zapret2_page)
        zapret2_import_block = "\n".join(zapret2_page_source.splitlines()[:45])
        item_init_source = inspect.getsource(top_summary_widget.ControlTopSummaryItem.__init__)
        activate_source = inspect.getsource(top_summary_widget.ControlTopSummaryItem._activate_theme_refresh)
        base_page_source = inspect.getsource(base_page)
        base_page_import_block = "\n".join(base_page_source.splitlines()[:35])
        zapret2_source = inspect.getsource(zapret2_page.Zapret2ModeControlPage)

        self.assertNotIn("from ui.theme import", top_summary_import_block)
        self.assertNotIn("from ui.theme_refresh import", top_summary_import_block)
        self.assertIn("_schedule_icon_refresh", item_init_source)
        self.assertIn("from ui.theme_refresh import ThemeRefreshBinding", activate_source)
        self.assertNotIn("from ui.theme_refresh import", base_page_import_block)
        self.assertIn("_create_page_theme_refresh_if_needed", base_page_source)
        self.assertNotIn("def _apply_page_theme", zapret2_source)
        self.assertNotIn("from ui.fluent_widgets import", shared_import_block)
        self.assertIn("from ui.pulsing_dot import PulsingDot", shared_import_block)

    def test_zapret2_top_summary_icons_are_delayed_past_first_paint(self) -> None:
        import inspect

        from presets.ui.control.zapret2.page import Zapret2ModeControlPage

        build_ui_source = inspect.getsource(Zapret2ModeControlPage._build_ui)

        self.assertIn("ControlTopSummaryWidget", build_ui_source)
        self.assertIn("initial_icon_delay_ms=250", build_ui_source)

    def test_zapret2_startup_does_not_build_hidden_profile_mode_card(self) -> None:
        import inspect

        import presets.ui.control.zapret2.sections_build as sections_build

        source = inspect.getsource(sections_build)

        self.assertNotIn("profile_ui_mode_card = build_push_setting_card_common", source)
        self.assertNotIn("on_open_profile_ui_mode_dialog", source)
        self.assertNotIn("get_cached_qta_pixmap", source)
        self.assertNotIn("profile_ui_mode_btn", source)

    def test_zapret2_profile_ui_mode_legacy_option_is_removed(self) -> None:
        import app.ui_texts as ui_texts
        import presets.ui.control.zapret2.page as zapret2_page
        import presets.ui.control.zapret2.page_runtime as page_runtime
        import presets.ui.control.zapret2.runtime_helpers as runtime_helpers
        import settings.normalize as settings_normalize
        import settings.schema as settings_schema
        import settings.store as settings_store

        self.assertNotIn("profile_ui_mode", settings_schema.default_program())
        self.assertNotIn("profile_ui_mode", settings_normalize.normalize_program({"profile_ui_mode": "advanced"}))
        self.assertFalse(hasattr(settings_schema, "VALID_PROFILE_UI_MODES"))
        self.assertFalse(hasattr(settings_store, "get_profile_ui_mode"))
        self.assertFalse(hasattr(settings_store, "set_profile_ui_mode"))
        self.assertFalse(hasattr(runtime_helpers, "sync_profile_ui_mode_label"))
        self.assertFalse(hasattr(page_runtime, "build_profile_ui_mode_label_plan"))
        self.assertFalse(hasattr(zapret2_page.Zapret2ModeControlPage, "_open_profile_ui_mode_dialog"))

        for key in (
            "page.winws2_control.button.change_mode",
            "page.winws2_control.profile_ui_mode.caption",
            "page.winws2_control.mode.basic",
            "page.winws2_control.mode.advanced",
            "page.winws2_control.mode.dialog.title",
            "page.winws2_control.mode.dialog.advanced_description",
        ):
            self.assertNotIn(key, ui_texts.TEXTS)

    def test_zapret2_program_settings_auto_height_is_not_applied_twice(self) -> None:
        import inspect

        import presets.ui.control.zapret2.sections_build as sections_build
        from presets.ui.control.zapret2.page import Zapret2ModeControlPage

        page_source = inspect.getsource(Zapret2ModeControlPage._build_program_settings_block)
        builder_source = inspect.getsource(sections_build.build_program_settings_group)

        self.assertIn("enable_setting_card_group_auto_height(card)", builder_source)
        self.assertNotIn("enable_setting_card_group_auto_height(", page_source)

    def test_control_settings_sections_defer_themed_action_icons(self) -> None:
        import inspect

        import presets.ui.control.zapret1.sections_build as zapret1_sections
        import presets.ui.control.zapret2.sections_build as zapret2_sections

        # Карточка «Фейки» есть только в Zapret 2; её значок рисуется после первого показа.
        self.assertIn("build_deferred_themed_push_setting_card_common", inspect.getsource(zapret2_sections))
        for module in (zapret1_sections, zapret2_sections):
            with self.subTest(module=module.__name__):
                self.assertNotIn("get_themed_qta_icon", inspect.getsource(module))

    def test_quick_action_tiles_defer_their_icons(self) -> None:
        import inspect

        import ui.widgets.action_tile as action_tile

        source = inspect.getsource(action_tile.ActionTile.__init__)
        self.assertIn("QTimer.singleShot(ACTION_ICON_DELAY_MS, self._start_icon)", source)
        self.assertNotIn("get_cached_qta_pixmap", source)


if __name__ == "__main__":
    unittest.main()
