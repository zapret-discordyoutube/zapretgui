"""Build-helper нижних секций Appearance page."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QHBoxLayout

from ui.fluent_widgets import SettingsCard, build_premium_badge
from app.ui_texts import tr as tr_catalog
from ui.accessibility import set_control_accessibility, set_state_text


@dataclass(slots=True)
class AppearanceHolidayWidgets:
    garland_icon_label: object
    garland_checkbox: object
    snowflakes_icon_label: object
    snowflakes_checkbox: object


@dataclass(slots=True)
class AppearanceOpacityWidgets:
    opacity_row: object


# Выпадающий список, а не ползунок: ползунок ловил колесо мыши при прокрутке
# страницы и случайно менял прозрачность окна.
OPACITY_CHOICES = (100, 95, 90, 85, 80, 75, 70, 60, 50, 40, 30)


def opacity_choice_items(current: object | None = None) -> list[tuple[str, int]]:
    """Пункты списка прозрачности; сохранённое нестандартное значение не теряется."""
    values = set(OPACITY_CHOICES)
    try:
        current_value = int(current)
    except (TypeError, ValueError):
        current_value = None
    if current_value is not None and 0 <= current_value <= 100:
        values.add(current_value)
    return [(f"{value}%", value) for value in sorted(values, reverse=True)]


@dataclass(slots=True)
class AppearancePerformanceWidgets:
    performance_card: object
    performance_group: object | None
    animations_switch: object
    smooth_scroll_switch: object
    editor_smooth_scroll_switch: object
    live_animations_switch: object = None


def update_holiday_checkbox_accessibility(checkbox, *, title: str) -> None:
    if checkbox is None:
        return
    title_text = str(title or "").strip() or "Праздничный эффект"
    if not checkbox.isEnabled():
        state = "недоступно без Premium"
    elif checkbox.isChecked():
        state = "включено"
    else:
        state = "выключено"
    text = f"{title_text}, {state}"
    set_state_text(checkbox, text)
    set_control_accessibility(
        checkbox,
        name=text,
        description=(
            f"{title_text}. Переключатель праздничного эффекта оформления. "
            "Доступно только для подписчиков Premium."
        ),
    )


def build_holiday_sections(
    *,
    page,
    tr_language: str,
    settings_card_cls,
    caption_label_cls,
    body_label_cls,
    checkbox_cls,
    get_icon_pixmap,
    on_garland_changed,
    on_snowflakes_changed,
):
    page.add_section_title(text_key="page.appearance.section.holiday")

    garland_card = settings_card_cls()
    garland_layout = QVBoxLayout()
    garland_layout.setSpacing(12)

    garland_desc = caption_label_cls(
        tr_catalog(
            "page.appearance.holiday.garland.description",
            language=tr_language,
            default=(
                "Праздничная гирлянда с мерцающими огоньками в верхней части окна. "
                "Доступно только для подписчиков Premium."
            ),
        )
    )
    garland_desc.setWordWrap(True)
    garland_layout.addWidget(garland_desc)

    garland_row = QHBoxLayout()
    garland_row.setSpacing(12)

    garland_icon = QLabel()
    garland_icon.setPixmap(get_icon_pixmap('fa5s.holly-berry', 20))
    garland_row.addWidget(garland_icon)

    garland_title_text = tr_catalog(
        "page.appearance.holiday.garland.title",
        language=tr_language,
        default="Новогодняя гирлянда",
    )
    garland_label = body_label_cls(garland_title_text)
    garland_row.addWidget(garland_label)
    garland_row.addWidget(build_premium_badge(tr_catalog("common.badge.premium", language=tr_language, default="⭐ Premium")))
    garland_row.addStretch()

    garland_checkbox = checkbox_cls()
    garland_checkbox.setEnabled(False)
    garland_checkbox.setObjectName("garlandSwitch")
    update_holiday_checkbox_accessibility(garland_checkbox, title=garland_title_text)
    garland_checkbox.stateChanged.connect(
        lambda _state, checkbox=garland_checkbox, title=garland_title_text: update_holiday_checkbox_accessibility(
            checkbox,
            title=title,
        )
    )
    garland_checkbox.stateChanged.connect(on_garland_changed)
    garland_row.addWidget(garland_checkbox)

    garland_layout.addLayout(garland_row)
    garland_card.add_layout(garland_layout)
    page.add_widget(garland_card)

    snowflakes_card = settings_card_cls()
    snowflakes_layout = QVBoxLayout()
    snowflakes_layout.setSpacing(12)

    snowflakes_desc = caption_label_cls(
        tr_catalog(
            "page.appearance.holiday.snowflakes.description",
            language=tr_language,
            default=("Мягко падающие снежинки по всему окну. Создаёт уютную зимнюю атмосферу."),
        )
    )
    snowflakes_desc.setWordWrap(True)
    snowflakes_layout.addWidget(snowflakes_desc)

    snowflakes_row = QHBoxLayout()
    snowflakes_row.setSpacing(12)

    snowflakes_icon = QLabel()
    snowflakes_icon.setPixmap(get_icon_pixmap('fa5s.snowflake', 20))
    snowflakes_row.addWidget(snowflakes_icon)

    snowflakes_title_text = tr_catalog(
        "page.appearance.holiday.snowflakes.title",
        language=tr_language,
        default="Снежинки",
    )
    snowflakes_label = body_label_cls(snowflakes_title_text)
    snowflakes_row.addWidget(snowflakes_label)
    snowflakes_row.addWidget(build_premium_badge(tr_catalog("common.badge.premium", language=tr_language, default="⭐ Premium")))
    snowflakes_row.addStretch()

    snowflakes_checkbox = checkbox_cls()
    snowflakes_checkbox.setEnabled(False)
    snowflakes_checkbox.setObjectName("snowflakesSwitch")
    update_holiday_checkbox_accessibility(snowflakes_checkbox, title=snowflakes_title_text)
    snowflakes_checkbox.stateChanged.connect(
        lambda _state, checkbox=snowflakes_checkbox, title=snowflakes_title_text: update_holiday_checkbox_accessibility(
            checkbox,
            title=title,
        )
    )
    snowflakes_checkbox.stateChanged.connect(on_snowflakes_changed)
    snowflakes_row.addWidget(snowflakes_checkbox)

    snowflakes_layout.addLayout(snowflakes_row)
    snowflakes_card.add_layout(snowflakes_layout)
    page.add_widget(snowflakes_card)
    page.add_spacing(16)

    return AppearanceHolidayWidgets(
        garland_icon_label=garland_icon,
        garland_checkbox=garland_checkbox,
        snowflakes_icon_label=snowflakes_icon,
        snowflakes_checkbox=snowflakes_checkbox,
    )


def build_opacity_section(
    *,
    page,
    tr_language: str,
    combo_row_cls,
    initial_opacity: int,
    on_opacity_changed,
):
    is_win11_plus = sys.platform == "win32" and sys.getwindowsversion().build >= 22000
    if is_win11_plus:
        opacity_title_text = tr_catalog(
            "page.appearance.opacity.win11.title",
            language=tr_language,
            default="Эффект акрилика окна",
        )
        opacity_desc_text = tr_catalog(
            "page.appearance.opacity.win11.description",
            language=tr_language,
            default=(
                "Настройка интенсивности акрилового эффекта всего окна приложения. "
                "При 0% эффект минимальный, при 100% — максимальный."
            ),
        )
    else:
        opacity_title_text = tr_catalog(
            "page.appearance.opacity.standard.title",
            language=tr_language,
            default="Прозрачность окна",
        )
        opacity_desc_text = tr_catalog(
            "page.appearance.opacity.standard.description",
            language=tr_language,
            default=(
                "Настройка прозрачности всего окна приложения. "
                "При 0% окно полностью прозрачное, при 100% — непрозрачное."
            ),
        )

    opacity_row = combo_row_cls(
        "fa5s.adjust",
        opacity_title_text,
        opacity_desc_text,
        items=opacity_choice_items(initial_opacity),
    )
    opacity_row.setCurrentData(int(initial_opacity), block_signals=True)
    opacity_row.currentIndexChanged.connect(
        lambda _index, row=opacity_row: on_opacity_changed(int(row.currentData()))
    )
    page.add_widget(opacity_row)
    page.add_spacing(16)

    return AppearanceOpacityWidgets(opacity_row=opacity_row)


def select_opacity_choice(row, value: object) -> None:
    """Показывает значение в списке без сигнала; нестандартное добавляет пунктом."""
    if row is None:
        return
    try:
        current_value = int(value)
    except (TypeError, ValueError):
        return
    combo = row.combo
    if combo.findData(current_value) < 0:
        position = combo.count()
        for index in range(combo.count()):
            if int(combo.itemData(index)) < current_value:
                position = index
                break
        combo.blockSignals(True)
        try:
            combo.insertItem(position, f"{current_value}%", userData=current_value)
        finally:
            combo.blockSignals(False)
    row.setCurrentData(current_value, block_signals=True)


def build_performance_section(
    *,
    page,
    tr_language: str,
    settings_card_group_cls,
    toggle_row_cls,
    on_animations_changed,
    on_smooth_scroll_changed,
    on_editor_smooth_scroll_changed,
    on_live_animations_changed=None,
):
    performance_group = settings_card_group_cls(
        tr_catalog("page.appearance.section.performance", language=tr_language, default="Производительность"),
        page.content,
    )
    perf_card = performance_group

    live_animations_switch = toggle_row_cls(
        "fa5s.magic",
        tr_catalog("page.appearance.performance.live_animations.title", language=tr_language, default="Живые анимации"),
        tr_catalog(
            "page.appearance.performance.live_animations.description",
            language=tr_language,
            default="Логотип, точка статуса, сводка на главной и кнопки запуска коротко оживают при изменениях. Почти не нагружает процессор",
        ),
    )
    if on_live_animations_changed is not None:
        live_animations_switch.toggled.connect(on_live_animations_changed)
    perf_card.addSettingCard(live_animations_switch)

    animations_switch = toggle_row_cls(
        "fa5s.film",
        tr_catalog("page.appearance.performance.animations.title", language=tr_language, default="Анимации интерфейса"),
        tr_catalog(
            "page.appearance.performance.animations.description",
            language=tr_language,
            default="Анимации кнопок, переходов и элементов WinUI",
        ),
    )
    animations_switch.toggled.connect(on_animations_changed)
    perf_card.addSettingCard(animations_switch)

    smooth_scroll_switch = toggle_row_cls(
        "fa5s.mouse",
        tr_catalog("page.appearance.performance.scroll.title", language=tr_language, default="Плавная прокрутка"),
        tr_catalog(
            "page.appearance.performance.scroll.description",
            language=tr_language,
            default="Инерционная прокрутка страниц настроек",
        ),
    )
    smooth_scroll_switch.toggled.connect(on_smooth_scroll_changed)
    perf_card.addSettingCard(smooth_scroll_switch)

    editor_smooth_scroll_switch = toggle_row_cls(
        "fa5s.file-alt",
        tr_catalog(
            "page.appearance.performance.editor_scroll.title",
            language=tr_language,
            default="Плавная прокрутка редакторов",
        ),
        tr_catalog(
            "page.appearance.performance.editor_scroll.description",
            language=tr_language,
            default="Плавная прокрутка внутри больших текстовых полей и редакторов. Работает только при включённых анимациях интерфейса.",
        ),
    )
    editor_smooth_scroll_switch.toggled.connect(on_editor_smooth_scroll_changed)
    perf_card.addSettingCard(editor_smooth_scroll_switch)

    page.add_widget(perf_card)
    page.add_spacing(16)

    return AppearancePerformanceWidgets(
        performance_card=perf_card,
        performance_group=performance_group,
        animations_switch=animations_switch,
        smooth_scroll_switch=smooth_scroll_switch,
        editor_smooth_scroll_switch=editor_smooth_scroll_switch,
        live_animations_switch=live_animations_switch,
    )


