"""Что показывает меню трея: страницы и строки.

Здесь нет ни одного виджета — только описание. Окошко меню
(``ui.tray_menu.popup``) рисует эти строки, а менеджер трея выполняет команду
выбранной строки. Поэтому состав меню проверяется тестами без показа окна.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.ui_texts import tr as tr_catalog
from ui.launch_control import (
    ACTIVE_LAUNCH_PHASES,
    mode_label_for_launch_method,
    normalize_launch_phase,
    phase_color,
    toggle_action_for_phase,
)


MAIN_PAGE = "main"
PRESETS_PAGE = "presets"
OPACITY_PAGE = "opacity"

# Виды строк.
ROW_ITEM = "item"
ROW_STATUS = "status"
ROW_SEPARATOR = "separator"
ROW_BACK = "back"
ROW_SEARCH = "search"
ROW_NOTE = "note"

# Поиск появляется, когда список уже не помещается на экран без прокрутки.
SEARCH_MIN_ROWS = 10

_STATUS_DEFAULTS = {
    "running": "работает",
    "starting": "запускается",
    "stopping": "останавливается",
    "stopped": "остановлен",
    "failed": "ошибка запуска",
}


@dataclass(frozen=True, slots=True)
class MenuRow:
    kind: str = ROW_ITEM
    text: str = ""
    detail: str = ""
    icon: str = ""
    command: str = ""
    arg: object = None
    page: str = ""
    enabled: bool = True
    checked: bool = False
    color: str = ""


@dataclass(frozen=True, slots=True)
class MenuPage:
    key: str
    rows: tuple[MenuRow, ...] = ()
    empty_text: str = ""


@dataclass(frozen=True, slots=True)
class TrayMenuModel:
    pages: dict[str, MenuPage] = field(default_factory=dict)

    def page(self, key: str) -> MenuPage:
        return self.pages.get(key) or self.pages.get(MAIN_PAGE) or MenuPage(MAIN_PAGE)


@dataclass(frozen=True, slots=True)
class TrayMenuState:
    """Всё, от чего зависит содержимое меню."""

    phase: str = "stopped"
    launch_method: str = ""
    preset_name: str = ""
    presets: tuple[tuple[str, str], ...] = ()
    selected_preset_file: str = ""
    has_presets: bool = False
    window_visible: bool = False
    telegram_label: str = "Telegram Proxy"
    windows_11: bool = False
    language: str | None = None


def tray_status_text(phase: str, *, language: str | None = None) -> str:
    key = normalize_launch_phase(phase)
    if key == "autostart_pending":
        key = "starting"
    return tr_catalog(f"tray.status.{key}", language=language, default=_STATUS_DEFAULTS[key])


def opacity_presets(windows_11: bool) -> list[tuple[int, str]]:
    if windows_11:
        return [
            (100, "100% (максимальный эффект)"),
            (75, "75%"),
            (50, "50%"),
            (25, "25%"),
            (0, "0% (минимальный эффект)"),
        ]
    return [
        (100, "100% (непрозрачное)"),
        (75, "75%"),
        (50, "50%"),
        (25, "25%"),
        (0, "0% (прозрачный фон)"),
    ]


def _launch_row(phase: str, language: str | None) -> MenuRow:
    next_step = toggle_action_for_phase(phase)
    if phase == "stopping":
        text = tr_catalog("tray.menu.stopping", language=language, default="Zapret останавливается…")
        icon = "stop"
    elif next_step == "stop":
        text = tr_catalog("tray.menu.stop", language=language, default="Остановить Zapret")
        icon = "stop"
    else:
        text = tr_catalog("tray.menu.start", language=language, default="Запустить Zapret")
        icon = "play"
    return MenuRow(text=text, icon=icon, command="toggle_dpi", enabled=bool(next_step))


def _main_page(state: TrayMenuState, phase: str) -> MenuPage:
    language = state.language
    mode = mode_label_for_launch_method(state.launch_method)
    rows: list[MenuRow] = [
        # Шапка: состояние Zapret и пресет. Щелчок по ней открывает окно.
        MenuRow(
            kind=ROW_STATUS,
            text=f"{mode} · {tray_status_text(phase, language=language)}",
            detail=state.preset_name,
            color=phase_color(phase) or "",
            command="show_window",
        ),
        MenuRow(kind=ROW_SEPARATOR),
        _launch_row(phase, language),
    ]
    if phase == "running":
        rows.append(
            MenuRow(
                text=tr_catalog("tray.menu.restart", language=language, default="Перезапустить"),
                icon="restart",
                command="restart_dpi",
            )
        )
    if state.has_presets:
        rows.append(
            MenuRow(
                text=tr_catalog("tray.menu.preset", language=language, default="Пресет"),
                detail=state.preset_name,
                icon="presets",
                page=PRESETS_PAGE,
            )
        )
    rows.append(MenuRow(kind=ROW_SEPARATOR))
    rows.append(
        MenuRow(
            text=tr_catalog("tray.menu.hide", language=language, default="Скрыть в трей")
            if state.window_visible
            else tr_catalog("tray.menu.show", language=language, default="Показать окно"),
            icon="window",
            command="toggle_window",
        )
    )
    rows.append(MenuRow(text=state.telegram_label, icon="send", command="toggle_telegram_proxy"))
    rows.append(
        MenuRow(
            text=tr_catalog("tray.menu.acrylic", language=language, default="Эффект акрилика окна")
            if state.windows_11
            else tr_catalog("tray.menu.opacity", language=language, default="Прозрачность окна"),
            icon="opacity",
            page=OPACITY_PAGE,
        )
    )
    rows.append(
        MenuRow(
            text=tr_catalog("tray.menu.console", language=language, default="Консоль"),
            icon="console",
            command="show_console",
        )
    )
    rows.append(MenuRow(kind=ROW_SEPARATOR))
    rows.append(
        MenuRow(
            text=tr_catalog("tray.menu.exit", language=language, default="Выход"),
            icon="exit",
            command="exit_only",
        )
    )
    rows.append(
        MenuRow(
            text=tr_catalog("tray.menu.exit_stop", language=language, default="Выход и остановить"),
            icon="power",
            command="exit_and_stop",
            enabled=phase in ACTIVE_LAUNCH_PHASES or phase == "stopping",
        )
    )
    return MenuPage(MAIN_PAGE, tuple(rows))


def _presets_page(state: TrayMenuState) -> MenuPage:
    language = state.language
    selected = state.selected_preset_file.casefold()
    rows: list[MenuRow] = [
        MenuRow(kind=ROW_BACK, text=tr_catalog("tray.menu.preset", language=language, default="Пресет"))
    ]
    if len(state.presets) >= SEARCH_MIN_ROWS:
        rows.append(
            MenuRow(
                kind=ROW_SEARCH,
                text=tr_catalog("tray.menu.search", language=language, default="Поиск — начните печатать"),
            )
        )
    if not state.presets:
        rows.append(
            MenuRow(
                kind=ROW_NOTE,
                text=tr_catalog("tray.menu.presets_empty", language=language, default="Пресетов пока нет"),
            )
        )
    for file_name, display_name in state.presets:
        rows.append(
            MenuRow(
                text=display_name,
                command="activate_preset",
                arg=(file_name, display_name),
                checked=bool(selected) and selected == str(file_name or "").casefold(),
            )
        )
    return MenuPage(
        PRESETS_PAGE,
        tuple(rows),
        empty_text=tr_catalog("tray.menu.search_empty", language=language, default="Ничего не найдено"),
    )


def _opacity_page(state: TrayMenuState) -> MenuPage:
    language = state.language
    title = (
        tr_catalog("tray.menu.acrylic", language=language, default="Эффект акрилика окна")
        if state.windows_11
        else tr_catalog("tray.menu.opacity", language=language, default="Прозрачность окна")
    )
    rows = [MenuRow(kind=ROW_BACK, text=title)]
    rows.extend(
        MenuRow(text=text, command="set_window_opacity", arg=value)
        for value, text in opacity_presets(state.windows_11)
    )
    return MenuPage(OPACITY_PAGE, tuple(rows))


def build_tray_menu(state: TrayMenuState) -> TrayMenuModel:
    phase = normalize_launch_phase(state.phase)
    pages = {MAIN_PAGE: _main_page(state, phase), OPACITY_PAGE: _opacity_page(state)}
    if state.has_presets:
        pages[PRESETS_PAGE] = _presets_page(state)
    return TrayMenuModel(pages)


__all__ = [
    "MAIN_PAGE",
    "OPACITY_PAGE",
    "PRESETS_PAGE",
    "ROW_BACK",
    "ROW_ITEM",
    "ROW_NOTE",
    "ROW_SEARCH",
    "ROW_SEPARATOR",
    "ROW_STATUS",
    "MenuPage",
    "MenuRow",
    "TrayMenuModel",
    "TrayMenuState",
    "build_tray_menu",
    "opacity_presets",
    "tray_status_text",
]
