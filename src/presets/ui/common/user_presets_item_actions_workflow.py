"""Общие действия элементов списка пользовательских preset-ов."""

from __future__ import annotations


def _tr_key(tr_prefix: str, suffix: str) -> str:
    return f"{tr_prefix}.{suffix}"


def open_edit_preset_menu_action(
    *,
    page,
    name: str,
    global_pos,
    is_builtin_preset_file_fn,
    is_selected_preset_file_fn,
    can_reset_preset_to_builtin_fn=None,
    is_remote_bound_preset_file_fn=None,
    tr_fn,
    make_menu_action,
    fluent_icon,
    round_menu_cls,
    on_preset_list_action_fn,
    show_preset_actions_menu_fn,
    tr_prefix: str,
) -> None:
    is_builtin = is_builtin_preset_file_fn(name)
    can_reset_to_builtin = False
    if not is_builtin and callable(can_reset_preset_to_builtin_fn):
        can_reset_to_builtin = bool(can_reset_preset_to_builtin_fn(name))
    is_remote_bound = False
    if not is_builtin and callable(is_remote_bound_preset_file_fn):
        is_remote_bound = bool(is_remote_bound_preset_file_fn(name))
    disabled_actions = {"delete"} if (not is_builtin and is_selected_preset_file_fn(name)) else set()
    chosen = show_preset_actions_menu_fn(
        page,
        global_pos=global_pos,
        is_builtin=is_builtin,
        can_reset_to_builtin=can_reset_to_builtin,
        is_remote_bound=is_remote_bound,
        disabled_actions=disabled_actions,
        labels={
            "open": tr_fn(_tr_key(tr_prefix, "menu.open"), "Открыть"),
            "rating": tr_fn(_tr_key(tr_prefix, "menu.rating"), "Рейтинг"),
            "move_up": tr_fn(_tr_key(tr_prefix, "menu.move_up"), "Переместить выше"),
            "move_down": tr_fn(_tr_key(tr_prefix, "menu.move_down"), "Переместить ниже"),
            "rename": tr_fn(_tr_key(tr_prefix, "menu.rename"), "Переименовать"),
            "duplicate": tr_fn(_tr_key(tr_prefix, "menu.duplicate"), "Дублировать"),
            "export": tr_fn(_tr_key(tr_prefix, "menu.export"), "Экспорт"),
            "reset": tr_fn(_tr_key(tr_prefix, "menu.reset"), "Вернуть встроенный"),
            "update_remote": tr_fn(_tr_key(tr_prefix, "menu.update_remote"), "Обновить из источника"),
            "unlink_remote": tr_fn(_tr_key(tr_prefix, "menu.unlink_remote"), "Отвязать от источника"),
            "delete": tr_fn(_tr_key(tr_prefix, "menu.delete"), "Удалить"),
        },
        make_menu_action=make_menu_action,
        icon_resolver=fluent_icon,
        round_menu_cls=round_menu_cls,
    )
    if chosen:
        on_preset_list_action_fn(chosen, name)


def rename_preset_action(*, name: str, is_builtin_preset_file_fn, show_inline_action_rename_fn, info_bar_cls, tr_fn, parent_window) -> None:
    if is_builtin_preset_file_fn(name):
        info_bar_cls.warning(
            title=tr_fn("common.error.title", "Ошибка"),
            content="Встроенный пресет нельзя переименовать. Создайте копию, если нужен свой вариант.",
            parent=parent_window,
        )
        return
    show_inline_action_rename_fn(name)
