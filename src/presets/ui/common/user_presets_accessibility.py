"""Accessibility helpers for user presets page controls."""

from __future__ import annotations

from ui.accessibility import remove_line_edit_buttons_from_tab_order, set_control_accessibility, set_state_text


def apply_user_presets_accessibility(
    *,
    tr_fn,
    tr_prefix: str,
    get_configs_btn=None,
    create_btn=None,
    import_btn=None,
    open_folder_btn=None,
    reset_all_btn=None,
    presets_info_btn=None,
    info_btn=None,
    preset_search_input=None,
    presets_list=None,
) -> None:
    """Задаёт понятные имена элементам пользовательских пресетов для экранного диктора."""

    def _set_named_control(widget, *, name_key: str, name_default: str, description_key: str, description_default: str) -> None:
        name = tr_fn(name_key, name_default)
        set_control_accessibility(
            widget,
            name=name,
            description=tr_fn(description_key, description_default),
        )
        set_state_text(widget, name)

    _set_named_control(
        get_configs_btn,
        name_key=f"{tr_prefix}.configs.accessible_name",
        name_default="Открыть Forgejo Issues с конфигами",
        description_key=f"{tr_prefix}.configs.title",
        description_default="Обменивайтесь пресетами и профилями в разделе Forgejo Issues",
    )
    _set_named_control(
        create_btn,
        name_key=f"{tr_prefix}.create.accessible_name",
        name_default="Создать новый пресет",
        description_key=f"{tr_prefix}.tooltip.create",
        description_default="Создать новый пресет",
    )
    _set_named_control(
        import_btn,
        name_key=f"{tr_prefix}.import.accessible_name",
        name_default="Импортировать пресет из файла",
        description_key=f"{tr_prefix}.tooltip.import",
        description_default="Импорт пресета из файла",
    )
    _set_named_control(
        open_folder_btn,
        name_key=f"{tr_prefix}.open_folder.accessible_name",
        name_default="Открыть папку пресетов",
        description_key=f"{tr_prefix}.tooltip.open_folder",
        description_default="Открыть папку, где лежат ваши пресеты",
    )
    _set_named_control(
        reset_all_btn,
        name_key=f"{tr_prefix}.reset_all.accessible_name",
        name_default="Вернуть встроенные пресеты",
        description_key=f"{tr_prefix}.tooltip.reset_all",
        description_default="Возвращает встроенные пресеты. Ваши изменения во встроенных пресетах будут потеряны.",
    )
    _set_named_control(
        presets_info_btn,
        name_key=f"{tr_prefix}.wiki.accessible_name",
        name_default="Открыть вики по пресетам",
        description_key=f"{tr_prefix}.button.wiki",
        description_default="Вики по пресетам",
    )
    _set_named_control(
        info_btn,
        name_key=f"{tr_prefix}.info.accessible_name",
        name_default="Показать справку о пресетах",
        description_key=f"{tr_prefix}.button.what_is_this",
        description_default="Что это такое?",
    )
    _set_named_control(
        preset_search_input,
        name_key=f"{tr_prefix}.search.accessible_name",
        name_default="Поиск пресетов",
        description_key=f"{tr_prefix}.search.accessible_description",
        description_default=(
            "Поиск пресетов по имени. Ctrl+F переводит курсор в поле поиска. "
            "После ввода перейдите в список клавишей Tab или нажмите Стрелка вниз, "
            "выберите пресет стрелками вверх и вниз, затем нажмите Enter или Пробел."
        ),
    )
    remove_line_edit_buttons_from_tab_order(preset_search_input)
    list_name = tr_fn(f"{tr_prefix}.list.accessible_name", "Список пользовательских пресетов")
    set_control_accessibility(
        presets_list,
        name=list_name,
        description=tr_fn(
            f"{tr_prefix}.list.accessible_description",
            (
                "Стрелки выбирают пресет или папку, "
                "Enter или Пробел активирует пресет или сворачивает и разворачивает папку, "
                "PageUp и PageDown перемещают пресет, клавиша меню открывает действия"
            ),
        ),
    )
    try:
        presets_list.set_screen_reader_list_name(list_name)
        if not presets_list.currentIndex().isValid():
            set_state_text(presets_list, f"{list_name}: список пока загружается")
    except Exception:
        pass


__all__ = ["apply_user_presets_accessibility"]
