"""Runtime/helper слой для Zapret2 mode control page."""

from __future__ import annotations

from app.ui_texts import tr as tr_catalog
from presets.ui.control.quick_actions import apply_quick_actions_language
from presets.ui.control.control_page_runtime_shared import (
    apply_program_settings_toggles,
    apply_status_plan as apply_status_plan_shared,
    set_button_text_accessibility,
    set_toggle_checked,
)


def apply_program_settings_snapshot(
    snapshot,
    *,
    auto_dpi_toggle,
    gui_autostart_toggle=None,
    tray_close_mode_combo=None,
    defender_toggle=None,
    max_block_toggle=None,
    state_media_block_toggle=None,
) -> None:
    apply_program_settings_toggles(
        snapshot,
        auto_dpi_toggle=auto_dpi_toggle,
        gui_autostart_toggle=gui_autostart_toggle,
        tray_close_mode_combo=tray_close_mode_combo,
        defender_toggle=defender_toggle,
        max_block_toggle=max_block_toggle,
        state_media_block_toggle=state_media_block_toggle,
    )


def apply_additional_settings_state(state, *, discord_restart_toggle, wssize_toggle, debug_log_toggle) -> None:
    if discord_restart_toggle is not None:
        set_toggle_checked(discord_restart_toggle, bool(state.discord_restart))
    if wssize_toggle is not None:
        set_toggle_checked(wssize_toggle, bool(state.wssize_enabled))
    if debug_log_toggle is not None:
        set_toggle_checked(debug_log_toggle, bool(state.debug_log_enabled))


def apply_status_plan(plan, *, status_title, status_desc, status_dot, close_btn) -> None:
    apply_status_plan_shared(
        plan,
        status_title=status_title,
        status_desc=status_desc,
        status_dot=status_dot,
        close_btn=close_btn,
    )


def apply_profile_language(
    *,
    language: str,
    close_btn,
    test_card,
    internet_cleanup_card,
    folder_card,
    docs_card,
    tour_card=None,
    quick_actions_title=None,
    windows_settings_card=None,
    additional_settings_notice,
    fakes_card,
    program_settings_card,
    auto_dpi_toggle,
    gui_autostart_toggle,
    tray_close_mode_combo,
    defender_toggle,
    max_block_toggle,
    state_media_block_toggle,
    additional_settings_card,
    discord_restart_toggle,
    wssize_toggle,
    debug_log_toggle,
) -> None:
    set_button_text_accessibility(
        close_btn,
        tr_catalog("launch.action.close_app", language=language, default="Закрыть программу"),
        description=tr_catalog(
            "launch.action.close_app.description",
            language=language,
            default="Остановить Zapret и закрыть программу",
        ),
    )
    if additional_settings_notice is not None:
        additional_settings_notice.setText(
            tr_catalog("page.winws2_control.advanced.warning", language=language, default="Эти параметры лучше менять, только если уверены в результате")
        )

    program_settings_card.titleLabel.setText(
        tr_catalog("page.control.section.launch_behavior", language=language, default="Запуск и поведение")
    )
    if windows_settings_card is not None:
        windows_settings_card.titleLabel.setText(
            tr_catalog("page.control.section.windows_blocks", language=language, default="Windows и блокировки")
        )

    auto_dpi_toggle.set_texts(
        tr_catalog("page.winws2_control.setting.autostart.title", language=language, default="Автозапуск DPI после старта программы"),
        tr_catalog("page.winws2_control.setting.autostart.desc", language=language, default="После запуска ZapretGUI автоматически запускать текущий DPI-режим"),
    )
    if gui_autostart_toggle is not None:
        gui_autostart_toggle.set_texts(
            tr_catalog("page.control.setting.gui_autostart.title", language=language, default="Автозапуск ZapretGUI"),
            tr_catalog("page.control.setting.gui_autostart.desc", language=language, default="Запускать программу в трее при входе в Windows"),
        )
    tray_close_mode_combo.set_texts(
        tr_catalog("page.control.setting.tray_close_mode.title", language=language, default="Поведение окна и трея"),
        tr_catalog("page.control.setting.tray_close_mode.desc", language=language, default="Выберите, когда ZapretGUI будет скрывать окно в системный трей"),
    )
    defender_toggle.set_texts(
        tr_catalog("page.control.setting.defender.title", language=language, default="Отключить Windows Defender"),
        tr_catalog("page.control.setting.defender.desc", language=language, default="Требуются права администратора"),
    )
    max_block_toggle.set_texts(
        tr_catalog("page.control.setting.max_block.title", language=language, default="Блокировать установку MAX"),
        tr_catalog("page.control.setting.max_block.desc", language=language, default="Блокирует запуск/установку MAX и домены в hosts"),
    )
    state_media_block_toggle.set_texts(
        tr_catalog(
            "page.control.setting.state_media_block.title",
            language=language,
            default="Блокировать государственные СМИ РФ",
        ),
        tr_catalog(
            "page.control.setting.state_media_block.desc",
            language=language,
            default="Добавляет базовый список государственных новостных сайтов в hosts",
        ),
    )
    additional_settings_card.titleLabel.setText(
        tr_catalog("page.control.section.advanced_bypass", language=language, default="Тонкая настройка обхода")
    )

    apply_quick_actions_language(
        tr_fn=lambda key, default: tr_catalog(key, language=language, default=default),
        text_prefix="page.winws2_control",
        title_label=quick_actions_title,
        tour_card=tour_card,
        test_card=test_card,
        internet_cleanup_card=internet_cleanup_card,
        folder_card=folder_card,
        docs_card=docs_card,
    )

    if fakes_card is not None:
        fakes_desc = tr_catalog(
            "page.winws2_control.button.fakes.desc",
            language=language,
            default="Встроенные фейки winws2 и свои .bin-файлы для стратегий",
        )
        fakes_card.setTitle(tr_catalog("page.winws2_control.button.fakes", language=language, default="Фейки"))
        fakes_card.setContent(fakes_desc)
        set_button_text_accessibility(
            fakes_card.button,
            tr_catalog("page.winws2_control.button.open", language=language, default="Открыть"),
            accessible_name=tr_catalog(
                "page.winws2_control.button.fakes.accessible_name",
                language=language,
                default="Открыть страницу фейков",
            ),
            description=fakes_desc,
        )

    discord_restart_toggle.set_texts(
        tr_catalog("page.dpi_settings.discord_restart.title", language=language, default="Перезапуск Discord"),
        tr_catalog("page.dpi_settings.discord_restart.desc", language=language, default="Автоперезапуск при смене стратегии"),
    )
    wssize_toggle.set_texts(
        tr_catalog("page.dpi_settings.advanced.wssize.title", language=language, default="Включить --wssize"),
        tr_catalog("page.dpi_settings.advanced.wssize.desc", language=language, default="Добавляет параметр размера окна TCP"),
    )
    debug_log_toggle.set_texts(
        tr_catalog("page.dpi_settings.advanced.debug_log.title", language=language, default="Включить лог-файл (--debug)"),
        tr_catalog("page.dpi_settings.advanced.debug_log.desc", language=language, default="Записывает логи winws в папку logs"),
    )
