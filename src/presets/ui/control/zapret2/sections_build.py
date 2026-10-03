"""Build-helper основных секций для Zapret2ModeControlPage."""

from __future__ import annotations

from dataclasses import dataclass

from presets.ui.control.shared_builders import build_deferred_themed_push_setting_card_common
from presets.ui.control.windows_features.build import build_state_media_block_toggle, build_windows_feature_toggles
from ui.fluent_widgets import build_additional_settings_section, enable_setting_card_group_auto_height


@dataclass(slots=True)
class Zapret2SettingsBuildWidgets:
    program_settings_section_label: object | None
    program_settings_card: object
    windows_settings_card: object
    gui_autostart_toggle: object
    auto_dpi_toggle: object
    tray_close_mode_combo: object
    defender_toggle: object
    max_block_toggle: object
    additional_settings_card: object
    additional_settings_notice: object
    fakes_card: object
    discord_restart_toggle: object | None
    wssize_toggle: object | None
    debug_log_toggle: object | None
    state_media_block_toggle: object


def build_winws2_pages_settings_sections(
    *,
    add_section_title,
    tr_fn,
    content_parent,
    setting_card_group_cls,
    push_setting_card_cls,
    win11_toggle_row_cls,
    win11_combo_row_cls,
    on_gui_autostart_toggled,
    on_auto_dpi_toggled,
    on_tray_close_mode_changed,
    on_defender_toggled,
    on_max_blocker_toggled,
    on_state_media_block_toggled,
    on_discord_restart_changed,
    on_wssize_toggled,
    on_debug_log_toggled,
    on_open_fakes,
) -> Zapret2SettingsBuildWidgets:
    # Первая группа — про саму программу: когда стартует и как ведёт себя окно.
    program_settings_title = tr_fn("page.control.section.launch_behavior", "Запуск и поведение")
    program_settings_section_label = None
    program_settings_card = setting_card_group_cls(program_settings_title, content_parent)

    gui_autostart_toggle = win11_toggle_row_cls(
        "fa5s.power-off",
        tr_fn("page.control.setting.gui_autostart.title", "Автозапуск ZapretGUI"),
        tr_fn("page.control.setting.gui_autostart.desc", "Запускать программу в трее при входе в Windows"),
    )
    gui_autostart_toggle.toggled.connect(on_gui_autostart_toggled)

    auto_dpi_toggle = win11_toggle_row_cls(
        "fa5s.bolt",
        tr_fn("page.winws2_control.setting.autostart.title", "Автозапуск DPI после старта программы"),
        tr_fn("page.winws2_control.setting.autostart.desc", "После запуска ZapretGUI автоматически запускать текущий DPI-режим"),
    )
    auto_dpi_toggle.toggled.connect(on_auto_dpi_toggled)

    tray_close_mode_combo = win11_combo_row_cls(
        "fa5s.window-minimize",
        tr_fn("page.control.setting.tray_close_mode.title", "Поведение окна и трея"),
        tr_fn("page.control.setting.tray_close_mode.desc", "Выберите, когда ZapretGUI будет скрывать окно в системный трей"),
        items=[
            ("Свернуть и крестик скрывают в трей", "minimize_and_close"),
            ("Только свернуть скрывает в трей", "minimize_only"),
            ("Не скрывать в трей", "normal"),
        ],
    )
    tray_close_mode_combo.combo.setFixedWidth(270)
    tray_close_mode_combo.combo.currentIndexChanged.connect(
        lambda _index: on_tray_close_mode_changed(tray_close_mode_combo.currentData())
    )

    windows_feature_toggles = build_windows_feature_toggles(
        tr_fn=tr_fn,
        win11_toggle_row_cls=win11_toggle_row_cls,
        on_defender_toggled=on_defender_toggled,
        on_max_blocker_toggled=on_max_blocker_toggled,
    )

    state_media_block_toggle = build_state_media_block_toggle(
        tr_fn=tr_fn,
        win11_toggle_row_cls=win11_toggle_row_cls,
        on_state_media_block_toggled=on_state_media_block_toggled,
    )

    discord_restart_toggle = (
        win11_toggle_row_cls(
            "fa5b.discord",
            "Перезапуск Discord",
            "Автоперезапуск при смене стратегии",
            "#7289da",
        )
        if win11_toggle_row_cls
        else None
    )
    if discord_restart_toggle:
        discord_restart_toggle.toggled.connect(on_discord_restart_changed)

    wssize_toggle = (
        win11_toggle_row_cls(
            "fa5s.ruler-horizontal",
            "Включить --wssize",
            "Добавляет параметр размера окна TCP",
        )
        if win11_toggle_row_cls
        else None
    )
    if wssize_toggle:
        wssize_toggle.toggled.connect(on_wssize_toggled)

    debug_log_toggle = (
        win11_toggle_row_cls(
            "fa5s.file-alt",
            "Включить лог-файл (--debug)",
            "Записывает логи winws в папку logs",
        )
        if win11_toggle_row_cls
        else None
    )
    if debug_log_toggle:
        debug_log_toggle.toggled.connect(on_debug_log_toggled)

    fakes_card = build_deferred_themed_push_setting_card_common(
        push_setting_card_cls=push_setting_card_cls,
        button_text=tr_fn("page.winws2_control.button.open", "Открыть"),
        icon_name="fa5s.file-code",
        icon_color="#b48ead",
        title_text=tr_fn("page.winws2_control.button.fakes", "Фейки"),
        content_text=tr_fn(
            "page.winws2_control.button.fakes.desc",
            "Встроенные фейки winws2 и свои .bin-файлы для стратегий",
        ),
        on_click=on_open_fakes,
        button_accessible_name=tr_fn(
            "page.winws2_control.button.fakes.accessible_name",
            "Открыть страницу фейков",
        ),
        parent=content_parent,
    )

    program_settings_card.addSettingCard(gui_autostart_toggle)
    program_settings_card.addSettingCard(auto_dpi_toggle)
    program_settings_card.addSettingCard(tray_close_mode_combo)
    if discord_restart_toggle is not None:
        program_settings_card.addSettingCard(discord_restart_toggle)
    enable_setting_card_group_auto_height(program_settings_card)

    # Вторая группа — что программа меняет в самой Windows.
    windows_settings_card = setting_card_group_cls(
        tr_fn("page.control.section.windows_blocks", "Windows и блокировки"),
        content_parent,
    )
    windows_settings_card.addSettingCard(windows_feature_toggles.defender_toggle)
    windows_settings_card.addSettingCard(windows_feature_toggles.max_block_toggle)
    windows_settings_card.addSettingCard(state_media_block_toggle)
    enable_setting_card_group_auto_height(windows_settings_card)

    # Третья группа — параметры движка для опытных, с предупреждением.
    additional_settings_card, additional_settings_notice = build_additional_settings_section(
        title=tr_fn("page.control.section.advanced_bypass", "Тонкая настройка обхода"),
        warning_text=tr_fn("page.winws2_control.advanced.warning", "Эти параметры лучше менять, только если уверены в результате"),
        parent=content_parent,
        toggle_rows=[wssize_toggle, debug_log_toggle],
        action_rows=[fakes_card],
    )

    return Zapret2SettingsBuildWidgets(
        program_settings_section_label=program_settings_section_label,
        program_settings_card=program_settings_card,
        windows_settings_card=windows_settings_card,
        gui_autostart_toggle=gui_autostart_toggle,
        auto_dpi_toggle=auto_dpi_toggle,
        tray_close_mode_combo=tray_close_mode_combo,
        defender_toggle=windows_feature_toggles.defender_toggle,
        max_block_toggle=windows_feature_toggles.max_block_toggle,
        additional_settings_card=additional_settings_card,
        additional_settings_notice=additional_settings_notice,
        fakes_card=fakes_card,
        discord_restart_toggle=discord_restart_toggle,
        wssize_toggle=wssize_toggle,
        debug_log_toggle=debug_log_toggle,
        state_media_block_toggle=state_media_block_toggle,
    )
