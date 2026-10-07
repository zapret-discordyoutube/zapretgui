"""Меню стратегии по правой кнопке: оценка и избранное.

Меню открывается на той стратегии, по которой щёлкнули, поэтому оценить
можно любую стратегию списка, не выбирая её для профиля. Меню только
сообщает, что выбрал человек; сохраняет это страница профиля.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from qfluentwidgets import RoundMenu

from ui.popup_menu import exec_popup_menu
from ui.presets_menu.common import fluent_icon, make_menu_action


COMMAND_RATING = "rating"
COMMAND_FAVORITE = "favorite"
COMMAND_DETAILS = "details"

# Стратегии без готового набора аргументов оценивать нечем.
_UNRATED_STRATEGY_IDS = frozenset({"", "none", "custom"})


def can_rate_strategy(strategy_id: str) -> bool:
    return str(strategy_id or "").strip() not in _UNRATED_STRATEGY_IDS


def build_strategy_context_menu(
    *,
    parent,
    strategy_name: str,
    rating: str,
    favorite: bool,
) -> tuple[RoundMenu, dict[object, tuple[str, object]]]:
    """Собирает меню стратегии, не показывая его.

    Пункт уже поставленной оценки снимает её: в названии пункта это сказано
    прямо, чтобы повторный выбор не был неожиданностью.
    """
    name = str(strategy_name or "").strip() or "стратегия"
    rating = str(rating or "").strip()
    menu = RoundMenu(parent=parent)
    action_map: dict[object, tuple[str, object]] = {}

    def _add_action(text: str, *, icon_name: str, command: str, payload: object) -> None:
        action = make_menu_action(text, icon=fluent_icon(icon_name), parent=menu)
        menu.addAction(action)
        menu_item = menu.view.item(menu.view.count() - 1)
        if menu_item is not None:
            accessible_text = f"{text}: {name}"
            menu_item.setData(Qt.ItemDataRole.AccessibleTextRole, accessible_text)
            menu_item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, accessible_text)
        action_map[action] = (command, payload)

    _add_action("Подробнее о стратегии", icon_name="INFO", command=COMMAND_DETAILS, payload=True)
    menu.addSeparator()
    if rating == "work":
        _add_action("Снять отметку «Работает»", icon_name="ACCEPT", command=COMMAND_RATING, payload="")
    else:
        _add_action("Работает", icon_name="ACCEPT", command=COMMAND_RATING, payload="work")
    if rating == "notwork":
        _add_action("Снять отметку «Не работает»", icon_name="CLOSE", command=COMMAND_RATING, payload="")
    else:
        _add_action("Не работает", icon_name="CLOSE", command=COMMAND_RATING, payload="notwork")
    menu.addSeparator()
    if favorite:
        _add_action("Убрать из избранного", icon_name="HEART", command=COMMAND_FAVORITE, payload=False)
    else:
        _add_action("В избранное", icon_name="HEART", command=COMMAND_FAVORITE, payload=True)
    return menu, action_map


def show_strategy_context_menu(
    *,
    parent,
    global_pos,
    strategy_name: str,
    rating: str,
    favorite: bool,
) -> tuple[str, object]:
    """Показывает меню и возвращает выбранное: (команда, значение) или ("", None)."""
    menu, action_map = build_strategy_context_menu(
        parent=parent,
        strategy_name=strategy_name,
        rating=rating,
        favorite=favorite,
    )
    chosen = exec_popup_menu(menu, global_pos, owner=parent, capture_action=True)
    return action_map.get(chosen, ("", None))


__all__ = [
    "COMMAND_DETAILS",
    "COMMAND_FAVORITE",
    "COMMAND_RATING",
    "build_strategy_context_menu",
    "can_rate_strategy",
    "show_strategy_context_menu",
]
