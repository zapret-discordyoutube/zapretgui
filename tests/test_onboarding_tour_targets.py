"""Экскурсия показывает именно то, о чём рассказывает.

Прогон идёт по шагам на настоящих страницах и про каждый шаг записывает, что
подсвечено. Проверки ниже читают эти записи:

* подсветка обводит ровно тот блок, который отдала страница, и ничего лишнего;
* блок действительно виден: его ничто не закрывает и он не узкая полоска;
* карточка подсказки стоит в окне и не заслоняет то, о чём говорит;
* названия, которые шаг упоминает в «ёлочках», написаны в подсвеченном блоке.

Последнее — главное: шаг сам говорит, на что указывает, и проверка сверяет
его слова с тем, что на экране. Что делать при падении, написано в сообщении
и в навыке ``.codex/skills/zapretgui-guided-tour/SKILL.md``.
"""

from __future__ import annotations

import os
import re
import unittest
from dataclasses import dataclass, field
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QComboBox, QTabBar, QWidget

from app.page_names import PageName
from test_onboarding_tour_drift import QUOTES_NOT_FROM_THE_PROGRAM
from test_onboarding_tour_pages import _app, build_tour_window, collect_slot_errors


SKILL_HINT = "Как править экскурсию: .codex/skills/zapretgui-guided-tour/SKILL.md"

_QUOTE = re.compile(r"«([^«»]+)»")
_TEXT_GETTERS = ("text", "title", "placeholderText", "toolTip", "accessibleName", "accessibleDescription")


@dataclass(slots=True)
class StepRecord:
    """Что было на экране на одном шаге. Только простые данные: окно к концу прогона удалено."""

    key: str
    # Видимые части подсвеченных блоков в координатах окна.
    targets: list[QRect] = field(default_factory=list)
    hole: QRect | None = None
    card: QRect = field(default_factory=QRect)
    window: QRect = field(default_factory=QRect)
    # Что написано в подсвеченном блоке, на всей странице и в боковом меню.
    target_text: str = ""
    page_text: str = ""
    menu_text: str = ""
    # Части подсветки, поверх которых лежит посторонний виджет.
    covered: list[str] = field(default_factory=list)


def widget_texts(root: QWidget) -> str:
    """Всё, что пользователь может прочитать в блоке: надписи, подсказки, строки списков."""
    parts: list[str] = []
    for widget in (root, *root.findChildren(QWidget)):
        if widget is not root and not widget.isVisibleTo(root):
            continue
        for getter in _TEXT_GETTERS:
            read = getattr(widget, getter, None)
            if not callable(read):
                continue
            try:
                value = read()
            except (RuntimeError, TypeError):
                # Метод с обязательными аргументами или уже удалённый виджет.
                continue
            if isinstance(value, str) and value:
                parts.append(value)
        if isinstance(widget, QComboBox):
            parts.extend(widget.itemText(index) for index in range(widget.count()))
        if isinstance(widget, QTabBar):
            parts.extend(widget.tabText(index) for index in range(widget.count()))
        if isinstance(widget, QAbstractItemView) and widget.model() is not None:
            parts.extend(_model_texts(widget.model()))
    return "\n".join(parts)


def _model_texts(model) -> list[str]:
    try:
        columns = model.columnCount()
    except TypeError:
        columns = 1
    values: list[str] = []
    for column in range(min(columns, 12)):
        header = model.headerData(column, Qt.Orientation.Horizontal)
        if isinstance(header, str):
            values.append(header)
    for row in range(min(model.rowCount(), 80)):
        for column in range(min(columns, 12)):
            index = model.index(row, column)
            for role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleTextRole):
                value = index.data(role)
                if isinstance(value, str) and value:
                    values.append(value)
    return values


def names_in(text: str, quote: str) -> bool:
    """Есть ли на экране надпись ровно с таким названием.

    Надпись — целая строка текста виджета. «Подготовить обращение» не считается
    найденным в «Подготовить обращение в поддержку»: так переименованная кнопка
    не спрячется за похожей подсказкой. Допускается только обычное оформление
    надписи: значок перед ней, счётчик после («Все  36», «Быстро · 30») и
    подпись через двоеточие («Применять к: Ethernet», «Вкладка стратегий: Все»).
    """
    wanted = _plain(quote)
    for line in text.splitlines():
        forms = {_plain(line)}
        if ":" in line:
            forms.add(_plain(line.split(":", 1)[0]))
            forms.add(_plain(line.rsplit(":", 1)[1]))
        forms.update(_plain(_DECORATION.sub("", form)) for form in tuple(forms))
        if wanted in forms:
            return True
    return False


# Значок в начале надписи и счётчик в конце.
_DECORATION = re.compile(r"^[^\w«(]+|\s+\d+$|\s*·.*$")


def _plain(value: str) -> str:
    return " ".join(str(value or "").split()).strip(" .:…").casefold()


def step_quotes(key: str) -> list[str]:
    from app.ui_texts import TEXTS

    text = " ".join(
        str((TEXTS.get(f"onboarding.step.{key}.{part}") or {}).get("ru") or "") for part in ("title", "body")
    )
    return [
        quote
        for quote in dict.fromkeys(_QUOTE.findall(text))
        if "{" not in quote and quote not in QUOTES_NOT_FROM_THE_PROGRAM
    ]


def _widget_at(window: QWidget, overlay: QWidget, point: QPoint) -> QWidget | None:
    """Самый верхний виджет программы в этой точке окна — как если бы подсказки не было."""
    found: QWidget | None = None
    for child in window.children():
        if not isinstance(child, QWidget) or child is overlay or not child.isVisible():
            continue
        if not child.geometry().contains(point):
            continue
        found = child.childAt(child.mapFrom(window, point)) or child
    return found


def _is_inside(widget: QWidget | None, container: QWidget) -> bool:
    while widget is not None:
        if widget is container:
            return True
        widget = widget.parentWidget()
    return False


def walk_tour(test_case: unittest.TestCase) -> dict[str, StepRecord]:
    """Проходит всю экскурсию на настоящих страницах и записывает, что подсвечено на каждом шаге."""
    from ui.onboarding.overlay import OnboardingOverlay
    from ui.onboarding.steps import TOUR_STEPS, build_tour_context, target_widget

    # Как у пользователя с выключенными «живыми анимациями»: блоки страниц не
    # «выплывают» (на это время они спрятаны маской), подсказка встаёт сразу.
    still = patch("settings.appearance.peek_warmed_live_animations_enabled", return_value=False)
    still.start()
    test_case.addCleanup(still.stop)
    window, host = build_tour_window(test_case)
    window.show()
    host.show_page(PageName.ZAPRET2_MODE_CONTROL)
    QApplication.processEvents()
    context = build_tour_context(window)
    context.current_page = host.current_page()
    overlay = OnboardingOverlay(window, context, TOUR_STEPS)
    test_case.assertTrue(overlay.start())
    test_case.addCleanup(lambda: overlay.finish("skipped", immediate=True))

    records: dict[str, StepRecord] = {}
    for _ in range(len(TOUR_STEPS)):
        for _ in range(4):
            QApplication.processEvents()
            overlay._on_frame()
        record = StepRecord(key=overlay.current_step_key(), window=window.rect(), card=overlay._card.geometry())
        if overlay._hole is not None:
            record.hole = overlay._hole.toAlignedRect()
        texts: list[str] = []
        for target in overlay._targets:
            widget = target_widget(target)
            local = target[1] if isinstance(target, tuple) else widget.rect()
            visible = overlay._visible_part(widget, local)
            record.targets.append(visible)
            texts.append(widget_texts(widget))
            if visible.isEmpty() or widget.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents):
                # Картинка меню лежит поверх страницы и нажатий не ловит — её саму не закрыть.
                continue
            top = _widget_at(window, overlay, visible.center())
            if not _is_inside(top, widget):
                record.covered.append(f"{type(widget).__name__} закрыт виджетом {type(top).__name__}")
        record.target_text = "\n".join(texts)
        page = host.current_page()
        record.page_text = widget_texts(page) if page is not None else ""
        record.menu_text = "\n".join(widget_texts(item) for item in window.ui_session.nav_items.values())
        records[record.key] = record
        if overlay._index >= len(overlay._steps) - 1:
            break
        overlay.go_next()
    return records


# Названия, которые шаг упоминает, но в подсвеченном блоке их нет, — и почему это
# правильно. Сюда попадает только осознанное: шаг показывает одну часть страницы,
# а рассказывает и про соседнюю, или кнопка появляется позже. Если проверка упала
# на новом шаге, сначала посмотрите, тот ли блок подсвечен; запись сюда — когда
# подсветка верна. Названия страниц из бокового меню записывать не нужно.
NAMES_OUTSIDE_THE_HIGHLIGHT: dict[str, dict[str, str]] = {
    # ── шаг показывает часть страницы, а говорит и про соседнюю ──
    "preset_file": {"Открыть": "пункт меню пресета на прошлой странице, откуда пришли в редактор"},
    "profile_order": {"Порядок в пресете": "заголовок страницы над подсвеченным списком"},
    "strategy_scan": {"Найти рабочую стратегию": "название вкладки над подсвеченной панелью"},
    "telegram_cloudflare": {
        "Проверить": "кнопка появляется, когда путь через Cloudflare включён",
        "Сеть": "соседняя группа ниже подсвеченной",
    },
    "about_help": {
        "Спросить": "группа ниже первой: все ссылки на экран не помещаются",
        "Следить за новостями": "группа ниже первой: все ссылки на экран не помещаются",
    },
    "updates": {"Проверять обновления при запуске": "выключатель ниже подсвеченной карточки"},
    "finish": {"Запустить Zapret": "кнопка запуска на этой же странице; шаг подсвечивает плитку экскурсии"},
    "control_nav": {"Управление": "в меню раздел подписан полностью: «Управление Zapret 2» или «Управление Zapret 1»"},
    "list_type": {"Условия": "кнопка-значок: название видно в её подсказке при наведении"},
    "ranges": {"Условия": "кнопка-значок: название видно в её подсказке при наведении"},
    # ── другая страница ──
    "dns_custom": {"Свои DNS": "группа на странице «Настройка DNS», откуда пришли"},
    "geo_blocks": {"Для ИИ": "группа серверов на странице «Настройка DNS»"},
    "log_analyzer_source": {"Включить лог-файл (--debug)": "выключатель на главной странице"},
    # ── появляется при настоящем состоянии программы ──
    "dns_now": {"Применять к": "строка адаптеров видна, когда программа нашла сетевые адаптеры"},
    "telegram_hosts": {"Убрать": "так называется кнопка «Прописать» после того, как адреса прописаны"},
    "blockcheck_history": {"Показать все": "кнопка появляется, когда проверок больше шести"},
    "strategy_choice": {"Советуем для этого сервиса": "группа есть, когда стратегия стоит в готовых пресетах"},
    "strategy_find": {
        "Советуемые": "вкладка есть, когда для сервиса есть советуемые стратегии",
        "у вас работает": "метка есть у стратегий, которые пользователь отмечал рабочими",
        # Способы обхода словами — это запросы для поиска, а не надписи.
        "нарезка": "пример запроса для поиска",
        "подделка": "пример запроса для поиска",
    },
    # ── нарисовано на плитках, отдельных надписей-виджетов нет ──
    "dns_providers": {"Автоматически": "плитка сетки серверов"},
    "hosts_direct": {
        "Напрямую": "заголовок группы в сетке плиток",
        "напрямую": "значение на плитке сервиса",
        "Сохранить": "кнопка появляется после первой правки",
    },
    "hosts_ai": {
        "ИИ-сервисы": "заголовок группы в сетке плиток",
        "Остальные сервисы": "заголовок группы в сетке плиток",
    },
}

# Шаги, у которых на стенде нет цели: страница показывает блок только при
# настоящем состоянии программы. Шаг пока идёт подсказкой по центру.
STEPS_WITHOUT_A_TARGET_ON_THE_STAND: frozenset[str] = frozenset()

# Больше этой доли подсвеченного блока карточка подсказки закрывать не должна.
CARD_MAY_COVER = 0.4
# Подсветка уже этого — полоска, по которой не понять, о чём речь.
SMALLEST_HIGHLIGHT = 20


class TourPointsAtWhatItTalksAboutTests(unittest.TestCase):
    _records: dict[str, StepRecord] | None = None

    def setUp(self) -> None:
        _app()
        collect_slot_errors(self)
        if type(self)._records is None:
            type(self)._records = walk_tour(self)
        self.records = type(self)._records

    def _highlighted(self) -> list[StepRecord]:
        return [record for record in self.records.values() if record.hole is not None]

    def test_the_whole_tour_is_walked_on_real_pages(self) -> None:
        from ui.onboarding.steps import TOUR_STEPS

        self.assertEqual(
            [step.key for step in TOUR_STEPS if step.key not in self.records],
            [],
            "Шаг пропущен: страница не открылась или не отдала цель. " + SKILL_HINT,
        )
        with_target = {step.key for step in TOUR_STEPS if step.target is not None}
        not_lit = sorted(key for key in with_target if self.records[key].hole is None)
        self.assertEqual(
            not_lit,
            sorted(STEPS_WITHOUT_A_TARGET_ON_THE_STAND),
            "У шага есть цель в каталоге, но на странице ничего не подсветилось "
            "(или шаг из списка исключений теперь подсвечен — уберите его оттуда). " + SKILL_HINT,
        )

    def test_highlight_wraps_exactly_the_block_the_page_gave(self) -> None:
        from ui.onboarding.overlay import HOLE_PADDING

        pad = int(HOLE_PADDING)
        for record in self._highlighted():
            with self.subTest(step=record.key):
                block = QRect()
                for rect in record.targets:
                    block = block.united(rect)
                expected = block.adjusted(-pad, -pad, pad, pad).intersected(record.window.adjusted(2, 2, -2, -2))
                for got, want in zip(record.hole.getCoords(), expected.getCoords()):
                    self.assertLessEqual(abs(got - want), 1, f"подсветка {record.hole}, блок {block}")
                self.assertGreaterEqual(min(record.hole.width(), record.hole.height()), SMALLEST_HIGHLIGHT)

    def test_nothing_lies_on_top_of_the_highlighted_block(self) -> None:
        covered = {record.key: record.covered for record in self._highlighted() if record.covered}
        self.assertEqual(
            covered,
            {},
            "В середине подсвеченного блока сверху лежит другой виджет: пользователь видит не то, "
            "о чём говорит шаг. " + SKILL_HINT,
        )

    def test_card_stays_in_the_window_and_does_not_hide_the_block(self) -> None:
        for record in self.records.values():
            with self.subTest(step=record.key):
                self.assertTrue(record.window.contains(record.card), f"карточка {record.card} вне окна")
                if record.hole is None:
                    continue
                common = record.card.intersected(record.hole)
                share = common.width() * common.height() / (record.hole.width() * record.hole.height())
                self.assertLessEqual(share, CARD_MAY_COVER, "карточка закрывает слишком большую часть блока")

    def test_names_a_step_mentions_are_written_in_the_highlighted_block(self) -> None:
        missing: dict[str, list[str]] = {}
        stale: dict[str, list[str]] = {}
        for record in self._highlighted():
            allowed = NAMES_OUTSIDE_THE_HIGHLIGHT.get(record.key, {})
            for quote in step_quotes(record.key):
                in_block = names_in(record.target_text, quote)
                if quote in allowed:
                    if in_block:
                        stale.setdefault(record.key, []).append(quote)
                    continue
                if in_block or names_in(record.menu_text, quote):
                    continue
                where = "есть на странице, но вне подсветки" if names_in(record.page_text, quote) else "на экране нет"
                missing.setdefault(record.key, []).append(f"«{quote}» — {where}")
        self.assertEqual(
            missing,
            {},
            "Шаг называет то, чего нет в подсвеченном блоке. Либо подсвечен не тот блок (поправьте "
            "onboarding_target страницы), либо название устарело (поправьте текст шага), либо так и "
            "задумано — тогда запишите название в NAMES_OUTSIDE_THE_HIGHLIGHT с причиной. " + SKILL_HINT,
        )
        self.assertEqual(stale, {}, "Название теперь есть в подсветке — уберите его из NAMES_OUTSIDE_THE_HIGHLIGHT.")
        unknown_steps = sorted(set(NAMES_OUTSIDE_THE_HIGHLIGHT) - set(self.records))
        self.assertEqual(unknown_steps, [], "В NAMES_OUTSIDE_THE_HIGHLIGHT остались шаги, которых больше нет.")
        for key, names in NAMES_OUTSIDE_THE_HIGHLIGHT.items():
            self.assertEqual(sorted(set(names) - set(step_quotes(key))), [], f"{key}: шаг больше не упоминает это название")


if __name__ == "__main__":
    unittest.main()
