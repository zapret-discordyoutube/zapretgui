"""Сборка групп настроек для Zapret2ModeControlPage.

Три группы настроек собираются по отдельности: каждая — свой блок страницы,
который достраивается позже неё (см. ui.block_build). Группа «Windows и
блокировки» общая для обоих режимов: presets.ui.control.windows_features.build.
"""

from __future__ import annotations

from dataclasses import dataclass

from presets.ui.control.shared_builders import build_deferred_themed_push_setting_card_common
from ui.fluent_widgets import build_additional_settings_section, enable_setting_card_group_auto_height


@dataclass(slots=True)
class ProgramSettingsGroupWidgets:
    card: object
    gui_autostart_toggle: object
    auto_dpi_toggle: object
    tray_close_mode_combo: object
    discord_restart_toggle: object


def build_program_settings_group(
    *,
    tr_fn,
    content_parent,
    setting_card_group_cls,
    win11_toggle_row_cls,
    win11_combo_row_cls,
    on_gui_autostart_toggled,
    on_auto_dpi_toggled,
    on_tray_close_mode_changed,
    on_discord_restart_changed,
) -> ProgramSettingsGroupWidgets:
    """Группа «Запуск и поведение»: когда программа стартует и как ведёт себя окно."""
    card = setting_card_group_cls(
        tr_fn("page.control.section.launch_behavior", "Запуск и поведение"),
        content_parent,
    )

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
    # Полная ширина под самый длинный вариант, а в узком окне список уже.
    tray_close_mode_combo.set_combo_width_range(170, 320)
    tray_close_mode_combo.combo.currentIndexChanged.connect(
        lambda _index: on_tray_close_mode_changed(tray_close_mode_combo.currentData())
    )

    discord_restart_toggle = win11_toggle_row_cls(
        "fa5b.discord",
        "Перезапуск Discord",
        "Автоперезапуск при смене стратегии",
        "#7289da",
    )
    discord_restart_toggle.toggled.connect(on_discord_restart_changed)

    card.addSettingCard(gui_autostart_toggle)
    card.addSettingCard(auto_dpi_toggle)
    card.addSettingCard(tray_close_mode_combo)
    card.addSettingCard(discord_restart_toggle)
    enable_setting_card_group_auto_height(card)
    return ProgramSettingsGroupWidgets(
        card=card,
        gui_autostart_toggle=gui_autostart_toggle,
        auto_dpi_toggle=auto_dpi_toggle,
        tray_close_mode_combo=tray_close_mode_combo,
        discord_restart_toggle=discord_restart_toggle,
    )


@dataclass(slots=True)
class FineTuningGroupWidgets:
    card: object
    notice: object
    wssize_toggle: object
    debug_log_toggle: object
    fakes_card: object


def build_fine_tuning_group(
    *,
    tr_fn,
    content_parent,
    push_setting_card_cls,
    win11_toggle_row_cls,
    on_wssize_toggled,
    on_debug_log_toggled,
    on_open_fakes,
) -> FineTuningGroupWidgets:
    """Группа «Тонкая настройка обхода»: параметры движка для опытных, с предупреждением."""
    wssize_toggle = win11_toggle_row_cls(
        "fa5s.ruler-horizontal",
        "Включить --wssize",
        "Добавляет параметр размера окна TCP",
    )
    wssize_toggle.toggled.connect(on_wssize_toggled)

    debug_log_toggle = win11_toggle_row_cls(
        "fa5s.file-alt",
        "Включить лог-файл (--debug)",
        "Записывает логи winws в папку logs",
    )
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

    card, notice = build_additional_settings_section(
        title=tr_fn("page.control.section.advanced_bypass", "Тонкая настройка обхода"),
        warning_text=tr_fn("page.winws2_control.advanced.warning", "Эти параметры лучше менять, только если уверены в результате"),
        parent=content_parent,
        toggle_rows=[wssize_toggle, debug_log_toggle],
        action_rows=[fakes_card],
    )
    return FineTuningGroupWidgets(
        card=card,
        notice=notice,
        wssize_toggle=wssize_toggle,
        debug_log_toggle=debug_log_toggle,
        fakes_card=fakes_card,
    )
