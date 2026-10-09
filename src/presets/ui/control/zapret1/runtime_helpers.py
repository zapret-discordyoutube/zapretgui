"""Runtime/helper слой для Zapret1 mode control page."""

from __future__ import annotations

from settings.mode import ZAPRET1_MODE
from app.ui_texts import tr as tr_catalog
from presets.ui.control.quick_actions import apply_quick_actions_language
import presets.ui.control.control_runtime as control_runtime
from presets.ui.control.control_page_runtime_shared import (
    apply_program_settings_toggles,
    apply_status_plan as apply_status_plan_shared,
    set_button_text_accessibility,
    set_toggle_checked,
)
from presets.ui.control.additional_settings_runtime import (
    build_additional_settings_state,
    create_additional_settings_save_worker,
    create_additional_settings_worker,
    create_refresh_runtime,
    create_top_summary_worker,
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


def apply_status_plan(plan, *, status_title, status_desc, status_dot, close_btn) -> None:
    apply_status_plan_shared(
        plan,
        status_title=status_title,
        status_desc=status_desc,
        status_dot=status_dot,
        close_btn=close_btn,
    )


def apply_winws1_pages_language(
    *,
    language: str,
    close_btn,
    program_settings_card,
    auto_dpi_toggle,
    gui_autostart_toggle,
    tray_close_mode_combo,
    defender_toggle,
    max_block_toggle,
    state_media_block_toggle,
    internet_cleanup_card,
    folder_card,
    docs_card,
    git_card,
    tour_card=None,
    quick_actions_title=None,
    windows_settings_card=None,
    additional_settings_card,
    additional_settings_notice,
    discord_restart_toggle,
    wssize_toggle,
    debug_log_toggle,
    refresh_preset_name,
    get_current_dpi_runtime_state,
    update_status,
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

    program_settings_card.titleLabel.setText(
        tr_catalog("page.control.section.launch_behavior", language=language, default="Запуск и поведение")
    )
    if windows_settings_card is not None:
        windows_settings_card.titleLabel.setText(
            tr_catalog("page.control.section.windows_blocks", language=language, default="Windows и блокировки")
        )
    if auto_dpi_toggle is not None:
        auto_dpi_toggle.set_texts(
            tr_catalog("page.winws1_control.setting.autostart.title", language=language, default="Автозапуск DPI после старта программы"),
            tr_catalog("page.winws1_control.setting.autostart.desc", language=language, default="После запуска ZapretGUI автоматически запускать текущий DPI-режим"),
        )
    if gui_autostart_toggle is not None:
        gui_autostart_toggle.set_texts(
            tr_catalog("page.control.setting.gui_autostart.title", language=language, default="Автозапуск ZapretGUI"),
            tr_catalog("page.control.setting.gui_autostart.desc", language=language, default="Запускать программу в трее при входе в Windows"),
        )
    if tray_close_mode_combo is not None:
        tray_close_mode_combo.set_texts(
            tr_catalog("page.control.setting.tray_close_mode.title", language=language, default="Поведение окна и трея"),
            tr_catalog("page.control.setting.tray_close_mode.desc", language=language, default="Выберите, когда ZapretGUI будет скрывать окно в системный трей"),
        )
    if defender_toggle is not None:
        defender_toggle.set_texts(
            tr_catalog("page.control.setting.defender.title", language=language, default="Отключить Windows Defender"),
            tr_catalog("page.control.setting.defender.desc", language=language, default="Требуются права администратора"),
        )
    if max_block_toggle is not None:
        max_block_toggle.set_texts(
            tr_catalog("page.control.setting.max_block.title", language=language, default="Блокировать установку MAX"),
            tr_catalog("page.control.setting.max_block.desc", language=language, default="Блокирует запуск/установку MAX и домены в hosts"),
        )
    if state_media_block_toggle is not None:
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

    apply_quick_actions_language(
        tr_fn=lambda key, default: tr_catalog(key, language=language, default=default),
        text_prefix="page.winws1_control",
        title_label=quick_actions_title,
        tour_card=tour_card,
        internet_cleanup_card=internet_cleanup_card,
        folder_card=folder_card,
        docs_card=docs_card,
        git_card=git_card,
    )

    additional_settings_card.titleLabel.setText(
        tr_catalog("page.control.section.advanced_bypass", language=language, default="Тонкая настройка обхода")
    )
    additional_settings_notice.setText(
        tr_catalog("page.winws1_control.advanced.warning", language=language, default="Эти параметры лучше менять, только если уверены в результате")
    )
    discord_restart_toggle.set_texts(
        tr_catalog("page.winws1_control.advanced.discord_restart.title", language=language, default="Перезапуск Discord"),
        tr_catalog("page.winws1_control.advanced.discord_restart.desc", language=language, default="Автоперезапуск при смене стратегии"),
    )
    wssize_toggle.set_texts(
        tr_catalog("page.winws1_control.advanced.wssize.title", language=language, default="Включить --wssize"),
        tr_catalog("page.winws1_control.advanced.wssize.desc", language=language, default="Добавляет параметр размера окна TCP"),
    )
    debug_log_toggle.set_texts(
        tr_catalog("page.winws1_control.advanced.debug_log.title", language=language, default="Включить лог-файл (--debug)"),
        tr_catalog("page.winws1_control.advanced.debug_log.desc", language=language, default="Записывает логи winws в папку logs"),
    )

    refresh_preset_name()
    phase, last_error = get_current_dpi_runtime_state()
    update_status(phase, last_error)


def show_simple_infobar_result(*, ok: bool, message: str, window, info_bar_cls) -> None:
    if ok:
        return
    info_bar_cls.warning(title="Ошибка", content=f"Не удалось очистить кэш: {message}", parent=window)
