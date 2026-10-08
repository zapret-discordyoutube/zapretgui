"""Экскурсия не расходится с программой.

Эти проверки падают, когда программу поменяли, а экскурсию «Как пользоваться
программой» — нет: появилась страница, о которой она молчит, или кнопку
переименовали, а текст шага зовёт её по-старому. Что делать в каждом случае,
написано в сообщении об ошибке и в навыке
``.codex/skills/zapretgui-guided-tour/SKILL.md``.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from app.page_names import PageName


SKILL_HINT = "Как править экскурсию: .codex/skills/zapretgui-guided-tour/SKILL.md"

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"

# В «ёлочках» экскурсия цитирует названия кнопок, вкладок и страниц. Здесь — то,
# что названием не является: примеры составных надписей с числами, технические
# примеры и чужие тексты. Название из программы сюда не добавлять — исправить
# текст шага, чтобы он звал вещь её настоящим именем.
QUOTES_NOT_FROM_THE_PROGRAM = frozenset(
    {
        # примеры надписей, которые программа собирает из чисел и названий
        "на этом сервисе в 13 пресетах",
        "на 28 сервисах",
        "ещё 2",
        "Снова открываются: Discord",
        # технические примеры
        "multisplit seqovl700",
        "you#tube.com",
        # надписи на чужих сайтах
        "недоступно в вашей стране",
        "not available in your region",
        # пояснения и образные слова
        "имя сайта → адрес",
        "стучится",
    }
)

_QUOTE = re.compile(r"«([^«»]+)»")


def _program_strings() -> str:
    """Все строки программы, кроме самой экскурсии: тексты интерфейса и строки из кода."""
    from app.ui_texts import TEXTS

    parts = [
        str(values.get("ru") or "")
        for key, values in TEXTS.items()
        # Кнопки самой карточки («Далее», «Назад») — тоже часть программы.
        if not key.startswith("onboarding.step.")
    ]
    for path in sorted(SRC_ROOT.rglob("*.py")):
        # Тексты интерфейса уже взяты выше, а в коде экскурсии лежат её же примеры.
        if path.name.startswith("ui_texts") or "onboarding" in path.relative_to(SRC_ROOT).as_posix():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            continue
        parts.extend(
            node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
    return "\n".join(parts)


class TourCoversEveryPageTests(unittest.TestCase):
    def test_every_page_of_the_program_is_in_the_tour_or_explicitly_left_out(self) -> None:
        from ui.onboarding.steps import COMMON_TOUR_PAGES, MODE_TOUR_PAGES, TOUR_PAGES_WITHOUT_STEPS

        in_tour = set(COMMON_TOUR_PAGES.values())
        for mode_pages in MODE_TOUR_PAGES.values():
            in_tour.update(mode_pages.values())

        forgotten = sorted(page.name for page in set(PageName) - in_tour - set(TOUR_PAGES_WITHOUT_STEPS))
        self.assertEqual(
            forgotten,
            [],
            "У программы появилась страница, о которой экскурсия не знает. Добавьте для неё шаги "
            "в ui/onboarding/steps.py или запишите в TOUR_PAGES_WITHOUT_STEPS, почему они не нужны. "
            + SKILL_HINT,
        )
        both = sorted(page.name for page in in_tour & set(TOUR_PAGES_WITHOUT_STEPS))
        self.assertEqual(both, [], "Страница уже есть в экскурсии — уберите её из TOUR_PAGES_WITHOUT_STEPS.")
        for page, reason in TOUR_PAGES_WITHOUT_STEPS.items():
            self.assertTrue(reason.strip(), page.name)

    def test_every_page_listed_for_the_tour_has_at_least_one_step(self) -> None:
        from ui.onboarding.steps import COMMON_TOUR_PAGES, MODE_TOUR_PAGES, TOUR_STEPS

        listed = set(COMMON_TOUR_PAGES)
        for mode_pages in MODE_TOUR_PAGES.values():
            listed.update(mode_pages)
        visited = {step.page for step in TOUR_STEPS if step.page}
        self.assertEqual(
            sorted(listed - visited),
            [],
            "Страница записана в таблицу экскурсии, но ни один шаг её не открывает. " + SKILL_HINT,
        )


class TourTextsNameRealThingsTests(unittest.TestCase):
    def test_names_quoted_in_tour_texts_exist_in_the_program(self) -> None:
        from app.ui_texts import TEXTS

        program = _program_strings()
        unknown: dict[str, list[str]] = {}
        used_exceptions: set[str] = set()
        for key, values in TEXTS.items():
            if not key.startswith("onboarding.step."):
                continue
            for quote in _QUOTE.findall(str(values.get("ru") or "")):
                if "{" in quote:
                    # Живое значение: его подставляет сама страница.
                    continue
                if quote in QUOTES_NOT_FROM_THE_PROGRAM:
                    used_exceptions.add(quote)
                    continue
                if quote not in program:
                    unknown.setdefault(quote, []).append(key)

        self.assertEqual(
            unknown,
            {},
            "Экскурсия называет то, чего в программе с таким именем нет: кнопку или вкладку переименовали "
            "или убрали. Поправьте текст шага (ru и en), а если шаг больше не нужен — удалите его. "
            + SKILL_HINT,
        )
        # Список исключений не копит мусор после правки текстов.
        self.assertEqual(sorted(QUOTES_NOT_FROM_THE_PROGRAM - used_exceptions), [])


if __name__ == "__main__":
    unittest.main()
