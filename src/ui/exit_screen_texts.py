"""Тексты прощального экрана при закрытии программы.

Правило то же, что у весёлых фраз BlockCheck и окна обновления: шутка
сопровождает работу, но не заменяет смысл. Заголовок прямо говорит, что
сейчас происходит, и задан здесь. Строка под ним — только настроение, и её
владелец — общий набор фраз программы (``ui.fun_phrases``, блоки ``exit_*``):
оттуда берётся одна случайная. Простые строки ниже показываются, только пока
в общем наборе нужного блока нет.

Строки короткие: экран виден около секунды.
"""

from __future__ import annotations

import random

from ui.fun_phrases import random_phrase


# Что происходит: окно закрывается, обход остаётся работать.
KIND_CLOSING_KEEP = "exit_keep"
# Окно закрывается, обход и не был запущен.
KIND_CLOSING_IDLE = "exit_idle"
# Обход останавливается, программа закроется следом.
KIND_STOPPING = "exit_stopping"
# Обход остановлен.
KIND_STOPPED = "exit_stopped"

_RU_TITLES = {
    KIND_CLOSING_KEEP: "Окно закрывается",
    KIND_CLOSING_IDLE: "Окно закрывается",
    KIND_STOPPING: "Останавливаем обход",
    KIND_STOPPED: "Обход остановлен",
}
_EN_TITLES = {
    KIND_CLOSING_KEEP: "Closing the window",
    KIND_CLOSING_IDLE: "Closing the window",
    KIND_STOPPING: "Stopping the bypass",
    KIND_STOPPED: "Bypass stopped",
}

# Запасные строки: пока в ui.fun_phrases нет блока с таким именем.
_RU_PLAIN = {
    KIND_CLOSING_KEEP: (
        "Обход остаётся на посту",
        "Медоед продолжает работать в фоне",
        "Окно уходит, обход остаётся",
        "До встречи! Обход работает и без окна",
    ),
    KIND_CLOSING_IDLE: (
        "До встречи!",
        "Медоед пошёл отдыхать",
        "Увидимся в следующий раз",
        "Хорошего дня!",
    ),
    KIND_STOPPING: (
        "Медоед складывает молнию",
        "Отпускаем пакеты по домам",
        "Сворачиваем лагерь",
        "Ещё мгновение",
    ),
    KIND_STOPPED: (
        "До встречи!",
        "Медоед ушёл спать",
        "Хорошего дня!",
        "Увидимся в следующий раз",
    ),
}
_EN_PLAIN = {
    KIND_CLOSING_KEEP: (
        "The bypass stays on duty",
        "The badger keeps working in the background",
        "The window leaves, the bypass stays",
        "See you! The bypass runs without the window",
    ),
    KIND_CLOSING_IDLE: (
        "See you!",
        "The badger is off to rest",
        "Until next time",
        "Have a nice day!",
    ),
    KIND_STOPPING: (
        "The badger is folding its lightning",
        "Sending the packets home",
        "Packing up the camp",
        "Just a moment",
    ),
    KIND_STOPPED: (
        "See you!",
        "The badger went to sleep",
        "Have a nice day!",
        "Until next time",
    ),
}

EXIT_KINDS = (KIND_CLOSING_KEEP, KIND_CLOSING_IDLE, KIND_STOPPING, KIND_STOPPED)


def _is_english(language: str | None) -> bool:
    return str(language or "").lower().startswith("en")


def exit_screen_title(kind: str, language: str | None = None) -> str:
    table = _EN_TITLES if _is_english(language) else _RU_TITLES
    return table[kind]


def plain_exit_phrases(kind: str, language: str | None = None) -> tuple[str, ...]:
    table = _EN_PLAIN if _is_english(language) else _RU_PLAIN
    return table[kind]


def exit_screen_text(kind: str, language: str | None = None) -> tuple[str, str]:
    """Заголовок и одна случайная лёгкая строка для прощального экрана."""
    plain = random.choice(plain_exit_phrases(kind, language))
    return exit_screen_title(kind, language), random_phrase(kind, language, default=plain)


__all__ = [
    "EXIT_KINDS",
    "KIND_CLOSING_IDLE",
    "KIND_CLOSING_KEEP",
    "KIND_STOPPED",
    "KIND_STOPPING",
    "exit_screen_text",
    "exit_screen_title",
    "plain_exit_phrases",
]
