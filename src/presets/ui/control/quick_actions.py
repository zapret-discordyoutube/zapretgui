"""Плитки «Быстрые действия» на главной странице режима.

Раньше это были строки с одинаковой кнопкой «Открыть» в самом низу
страницы. Теперь это ряд плиток сразу под сводкой: обучение, тест
соединения, сброс сети, папка программы и документация.

Список действий описан один раз (``quick_action_specs``): по нему плитки
строятся и по нему же переводятся при смене языка.
"""

from __future__ import annotations

from dataclasses import dataclass

from qfluentwidgets import BodyLabel
from qfluentwidgets.common.font import setFont

from ui.accessibility import set_state_text
from ui.widgets.action_tile import ActionTile
from ui.widgets.tile_grid import TileGrid


QUICK_ACTION_MIN_WIDTH = 190
QUICK_ACTIONS_TITLE_KEY = "page.control.section.quick_actions"
QUICK_ACTIONS_TITLE_DEFAULT = "Быстрые действия"


@dataclass(frozen=True, slots=True)
class QuickActionSpec:
    key: str
    icon_name: str
    icon_color: str
    title: tuple[str, str]
    content: tuple[str, str]
    accessible_name: tuple[str, str]


@dataclass(slots=True)
class QuickActionWidgets:
    title_label: object
    grid: object
    tour_card: object
    test_card: object
    internet_cleanup_card: object
    folder_card: object
    docs_card: object


def quick_action_specs(text_prefix: str) -> tuple[QuickActionSpec, ...]:
    """Действия по порядку. Каждый текст — пара (ключ каталога, текст по умолчанию)."""
    return (
        QuickActionSpec(
            key="tour",
            icon_name="fa5s.graduation-cap",
            icon_color="#b39ddb",
            title=("page.control.onboarding_tour.title", "Как пользоваться программой"),
            content=("page.control.onboarding_tour.desc", "Пошаговая экскурсия: пресеты, профили и стратегии"),
            accessible_name=("page.control.onboarding_tour.accessible_name", "Показать обучающий тур"),
        ),
        QuickActionSpec(
            key="test",
            icon_name="fa5s.wifi",
            icon_color="#60cdff",
            title=(f"{text_prefix}.button.connection_test", "Тест соединения"),
            content=(f"{text_prefix}.button.connection_test.desc", "Проверить доступность сети и состояние обхода"),
            accessible_name=(f"{text_prefix}.button.connection_test.accessible_name", "Открыть тест соединения"),
        ),
        QuickActionSpec(
            key="internet_cleanup",
            icon_name="fa5s.network-wired",
            icon_color="#4cc38a",
            title=("page.control.internet_cleanup.title", "Сбросить сеть Windows"),
            content=(
                "page.control.internet_cleanup.desc",
                "Очистить DNS, proxy и Winsock. Может понадобиться перезагрузка",
            ),
            accessible_name=("page.control.internet_cleanup.accessible_name", "Сбросить сеть Windows"),
        ),
        QuickActionSpec(
            key="folder",
            icon_name="fa5s.folder-open",
            icon_color="#f5c04d",
            title=(f"{text_prefix}.button.open_folder", "Открыть папку"),
            content=(f"{text_prefix}.button.open_folder.desc", "Перейти в папку программы и служебных файлов"),
            accessible_name=(f"{text_prefix}.button.open_folder.accessible_name", "Открыть папку программы"),
        ),
        QuickActionSpec(
            key="docs",
            icon_name="fa5s.book",
            icon_color="#8ab4f8",
            title=(f"{text_prefix}.button.documentation", "Документация"),
            content=(f"{text_prefix}.button.documentation.desc", "Открыть справку и описание возможностей"),
            accessible_name=(f"{text_prefix}.button.documentation.accessible_name", "Открыть документацию"),
        ),
    )


def build_quick_actions(
    *,
    tr_fn,
    text_prefix: str,
    on_open_onboarding_tour,
    on_open_connection_test,
    on_open_internet_cleanup,
    on_open_folder,
    on_open_docs,
    parent=None,
) -> QuickActionWidgets:
    handlers = {
        "tour": on_open_onboarding_tour,
        "test": on_open_connection_test,
        "internet_cleanup": on_open_internet_cleanup,
        "folder": on_open_folder,
        "docs": on_open_docs,
    }
    # Заголовок того же вида, что у групп настроек ниже по странице.
    title_text = tr_fn(QUICK_ACTIONS_TITLE_KEY, QUICK_ACTIONS_TITLE_DEFAULT)
    title_label = BodyLabel(title_text, parent)
    setFont(title_label, 20)
    set_state_text(title_label, f"Раздел страницы: {title_text}")
    grid = TileGrid(parent, min_tile_width=QUICK_ACTION_MIN_WIDTH, spacing=12)
    tiles: dict[str, ActionTile] = {}
    for spec in quick_action_specs(text_prefix):
        tile = ActionTile(
            grid,
            icon_name=spec.icon_name,
            icon_color=spec.icon_color,
            title=tr_fn(*spec.title),
            content=tr_fn(*spec.content),
            accessible_name=tr_fn(*spec.accessible_name),
        )
        tile.clicked.connect(handlers[spec.key])
        grid.add_tile(tile)
        tiles[spec.key] = tile
    return QuickActionWidgets(
        title_label=title_label,
        grid=grid,
        tour_card=tiles["tour"],
        test_card=tiles["test"],
        internet_cleanup_card=tiles["internet_cleanup"],
        folder_card=tiles["folder"],
        docs_card=tiles["docs"],
    )


def apply_quick_actions_language(
    *,
    tr_fn,
    text_prefix: str,
    title_label=None,
    tour_card=None,
    test_card=None,
    internet_cleanup_card=None,
    folder_card=None,
    docs_card=None,
) -> None:
    """Переводит плитки на текущий язык по тому же списку, по которому они строились."""
    if title_label is not None:
        title_text = tr_fn(QUICK_ACTIONS_TITLE_KEY, QUICK_ACTIONS_TITLE_DEFAULT)
        title_label.setText(title_text)
        set_state_text(title_label, f"Раздел страницы: {title_text}")
    tiles = {
        "tour": tour_card,
        "test": test_card,
        "internet_cleanup": internet_cleanup_card,
        "folder": folder_card,
        "docs": docs_card,
    }
    for spec in quick_action_specs(text_prefix):
        tile = tiles.get(spec.key)
        if tile is None:
            continue
        tile.set_texts(
            tr_fn(*spec.title),
            tr_fn(*spec.content),
            accessible_name=tr_fn(*spec.accessible_name),
        )


__all__ = [
    "QuickActionSpec",
    "QuickActionWidgets",
    "apply_quick_actions_language",
    "build_quick_actions",
    "quick_action_specs",
]
