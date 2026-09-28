"""Шаги обучающего тура: что подсвечиваем, на какой странице и какой текст.

Каждый шаг умеет сам найти свою цель в живом окне. Если цели нет
(другой режим программы, пункт меню скрыт или ещё не построен), шаг
тихо пропускается. Так один список шагов подходит и для Zapret 1,
и для Zapret 2, и для Оркестратора.

Цель — это виджет или пара (виджет, прямоугольник внутри него). Пара
нужна для строк списков: строки профилей и пресетов рисует делегат,
отдельных виджетов у них нет.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from PyQt6 import sip
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QWidget

from app.page_names import PageName
from config.urls import ONBOARDING_WIKI_URLS
from ui.window_ui_session import get_window_ui_session


CONTROL_PAGE_NAMES: tuple[PageName, ...] = (
    PageName.ZAPRET2_MODE_CONTROL,
    PageName.ZAPRET1_MODE_CONTROL,
    PageName.ORCHESTRA,
)

# Страницы тура для каждого режима: ключ шага → страница программы.
MODE_TOUR_PAGES: dict[PageName, dict[str, PageName]] = {
    PageName.ZAPRET2_MODE_CONTROL: {
        "control": PageName.ZAPRET2_MODE_CONTROL,
        "user_presets": PageName.ZAPRET2_USER_PRESETS,
        "preset_editor": PageName.ZAPRET2_PRESET_RAW_EDITOR,
        "preset_setup": PageName.ZAPRET2_PRESET_SETUP,
        "profile_order": PageName.ZAPRET2_PROFILE_ORDER,
        "profile_setup": PageName.ZAPRET2_PROFILE_SETUP,
    },
    PageName.ZAPRET1_MODE_CONTROL: {
        "control": PageName.ZAPRET1_MODE_CONTROL,
        "user_presets": PageName.ZAPRET1_USER_PRESETS,
        "preset_editor": PageName.ZAPRET1_PRESET_RAW_EDITOR,
        "preset_setup": PageName.ZAPRET1_PRESET_SETUP,
        "profile_order": PageName.ZAPRET1_PROFILE_ORDER,
        "profile_setup": PageName.ZAPRET1_PROFILE_SETUP,
    },
    PageName.ORCHESTRA: {
        "control": PageName.ORCHESTRA,
    },
}

# Страницы, которым нужен параметр (какой пресет, какой профиль). Их
# открывает страница-родитель своим обычным путём через
# onboarding_open_subpage(ключ): сама выбирает пресет или профиль.
TOUR_SUBPAGE_PARENTS: dict[str, str] = {
    "preset_editor": "user_presets",
    "profile_setup": "preset_setup",
}

TourTarget = QWidget | tuple[QWidget, QRect]


@dataclass(slots=True)
class TourContext:
    window: QWidget
    control_page_name: PageName | None = None
    # Ключ страницы тура → PageName текущего режима.
    pages: dict[str, PageName] = field(default_factory=dict)
    # Страница, открытая текущим шагом.
    current_page: QWidget | None = None
    current_page_key: str = ""


TargetResolver = Callable[[TourContext], list[TourTarget]]


@dataclass(frozen=True, slots=True)
class TourStep:
    key: str
    target: TargetResolver | None = None
    # Какую страницу тура открыть перед шагом ("control", "user_presets",
    # "preset_setup"). None — остаться там, где пользователь сейчас.
    page: str | None = None
    # Цель может появиться не сразу (список профилей грузится в фоне):
    # шаг показывается по центру и ждёт её, а не пропускается.
    target_optional: bool = False
    # Крупная карточка по центру: приветствие и объяснения без подсветки.
    hero: bool = False
    # Что страница показывает вживую на время шага: открытое меню, нужную
    # вкладку. Тур передаёт его в page.onboarding_set_state(), а при уходе
    # с шага — None, и страница возвращает всё как было.
    page_state: str | None = None
    # Статья вики по теме шага — кнопка «Подробнее в вики» на карточке.
    wiki_url: str = ""
    # Анимированная схема на карточке (ключ сцены из ui.onboarding.illustrations).
    illustration: str = ""


def is_alive_widget(widget) -> bool:
    return widget is not None and not sip.isdeleted(widget)


def is_widget_shown(widget) -> bool:
    if not is_alive_widget(widget):
        return False
    try:
        return bool(widget.isVisible()) and widget.width() > 0 and widget.height() > 0
    except RuntimeError:
        return False


def target_widget(target) -> QWidget | None:
    if isinstance(target, tuple):
        return target[0] if target else None
    return target


def is_target_shown(target) -> bool:
    if isinstance(target, tuple):
        if len(target) != 2 or not is_widget_shown(target[0]):
            return False
        rect = target[1]
        return isinstance(rect, QRect) and rect.isValid() and not rect.isEmpty()
    return is_widget_shown(target)


def resolve_control_page_name(window) -> PageName | None:
    """Главная страница текущего режима — та, чей пункт виден в меню."""
    session = get_window_ui_session(window)
    if session is None:
        return None
    for page_name in CONTROL_PAGE_NAMES:
        if is_widget_shown(session.nav_items.get(page_name)):
            return page_name
    return None


def build_tour_context(window) -> TourContext:
    control_page_name = resolve_control_page_name(window)
    pages = dict(MODE_TOUR_PAGES.get(control_page_name, {})) if control_page_name is not None else {}
    return TourContext(window=window, control_page_name=control_page_name, pages=pages)


def _nav_item(*page_names: PageName) -> TargetResolver:
    def _resolve(ctx: TourContext) -> list[TourTarget]:
        session = get_window_ui_session(ctx.window)
        if session is None:
            return []
        for page_name in page_names:
            item = session.nav_items.get(page_name)
            if is_widget_shown(item):
                return [item]
        return []

    return _resolve


def _nav_items(*page_names: PageName) -> TargetResolver:
    """Несколько пунктов бокового меню сразу — все, что видны."""

    def _resolve(ctx: TourContext) -> list[TourTarget]:
        session = get_window_ui_session(ctx.window)
        if session is None:
            return []
        items = [session.nav_items.get(page_name) for page_name in page_names]
        return [item for item in items if is_widget_shown(item)]

    return _resolve


def _nav_group(group_name: str) -> TargetResolver:
    """Заголовок группы бокового меню вместе со всеми её видимыми пунктами."""

    def _resolve(ctx: TourContext) -> list[TourTarget]:
        session = get_window_ui_session(ctx.window)
        if session is None:
            return []
        header = session.nav_header_by_group.get(group_name)
        widgets: list[TourTarget] = []
        if is_widget_shown(header):
            widgets.append(header)
        for entry_header, page_names, _header_key in session.nav_headers:
            if entry_header is not header:
                continue
            for page_name in page_names:
                item = session.nav_items.get(page_name)
                if is_widget_shown(item):
                    widgets.append(item)
        # Один заголовок без пунктов подсвечивать бессмысленно.
        return widgets if len(widgets) > 1 else []

    return _resolve


def _page_target(name: str) -> TargetResolver:
    def _resolve(ctx: TourContext) -> list[TourTarget]:
        page = ctx.current_page
        if not is_alive_widget(page):
            return []
        getter = getattr(page, "onboarding_target", None)
        if not callable(getter):
            return []
        target = getter(name)
        # Список — несколько виджетов, подсвечиваем их вместе.
        targets = target if isinstance(target, list) else [target]
        return [item for item in targets if is_target_shown(item)]

    return _resolve


_TOUR_STEPS: tuple[TourStep, ...] = (
    TourStep("welcome", hero=True),
    TourStep("how_it_works", hero=True),
    TourStep("building_blocks", hero=True),
    TourStep("control_nav", _nav_item(*CONTROL_PAGE_NAMES), page="control"),
    TourStep("start", _page_target("start"), page="control"),
    TourStep("status", _page_target("status"), page="control"),
    TourStep("preset", _page_target("preset"), page="control"),
    TourStep("presets_list", _page_target("presets_list"), page="user_presets", target_optional=True),
    TourStep("preset_menu", _page_target("preset_menu"), page="user_presets", page_state="preset_menu"),
    TourStep("preset_file", _page_target("editor"), page="preset_editor", target_optional=True),
    TourStep("presets_toolbar", _page_target("presets_toolbar"), page="user_presets"),
    TourStep("profiles_list", _page_target("profiles_list"), page="preset_setup", target_optional=True),
    TourStep("profile_row", _page_target("first_profile"), page="preset_setup"),
    TourStep("profile_menu", _page_target("profile_menu"), page="preset_setup", page_state="profile_menu"),
    TourStep("profiles_toolbar", _page_target("profiles_toolbar"), page="preset_setup"),
    TourStep("profile_order", _page_target("order_list"), page="profile_order", target_optional=True),
    TourStep("list_type", _page_target("list_type"), page="profile_setup", target_optional=True),
    TourStep("ranges", _page_target("ranges"), page="profile_setup"),
    TourStep("profile_tabs", _page_target("tabs"), page="profile_setup"),
    TourStep("strategy_choice", _page_target("strategies"), page="profile_setup", illustration="blocked"),
    TourStep("technique_fake", page="profile_setup", illustration="fake"),
    TourStep("technique_multisplit", page="profile_setup", illustration="multisplit"),
    TourStep("technique_multidisorder", page="profile_setup", illustration="multidisorder"),
    TourStep("technique_fakedsplit", page="profile_setup", illustration="fakedsplit"),
    TourStep("technique_hostfakesplit", page="profile_setup", illustration="hostfakesplit"),
    TourStep("technique_tcpseg", page="profile_setup", illustration="tcpseg"),
    TourStep("technique_oob", page="profile_setup", illustration="oob"),
    TourStep("technique_syndata", page="profile_setup", illustration="syndata"),
    TourStep("list_entries", _page_target("list_entries"), page="profile_setup", page_state="editor"),
    TourStep("fakes", _page_target("fakes"), page="control"),
    TourStep("dpi_mode", _nav_item(PageName.DPI_SETTINGS, PageName.ORCHESTRA_SETTINGS)),
    TourStep("program_settings", _page_target("program_settings"), page="control"),
    TourStep("tools", _nav_group("system")),
    TourStep("geo_blocks", _nav_items(PageName.NETWORK, PageName.HOSTS)),
    TourStep("diagnostics", _nav_group("diagnostics")),
    TourStep("appearance", _nav_group("appearance")),
    TourStep("finish", _page_target("tour_card"), page="control", target_optional=True),
)

# Ссылки на вики живут в config.urls вместе с остальными адресами.
TOUR_STEPS: tuple[TourStep, ...] = tuple(
    replace(step, wiki_url=ONBOARDING_WIKI_URLS.get(step.key, "")) for step in _TOUR_STEPS
)


__all__ = [
    "CONTROL_PAGE_NAMES",
    "MODE_TOUR_PAGES",
    "TOUR_STEPS",
    "TOUR_SUBPAGE_PARENTS",
    "TourContext",
    "TourStep",
    "TourTarget",
    "build_tour_context",
    "is_alive_widget",
    "is_target_shown",
    "is_widget_shown",
    "resolve_control_page_name",
    "target_widget",
]
