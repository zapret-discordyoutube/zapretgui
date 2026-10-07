"""Весёлые фразы окна обновления: пока качается — не скучно.

Правило то же, что у BlockCheck (``blockcheck/ui/fun_texts.py``): шутка
сопровождает работу, но не заменяет смысл. Проценты, скорость и ошибки
пишутся рядом точно и серьёзно, здесь — только настроение.

Сами фразы лежат рядом: ``fun_texts_ru.py`` и ``fun_texts_en.py``. Наборы
нарочно большие (не меньше 50 в каждом блоке): фразы идут в случайном порядке
без повторов (см. ``ui.widgets.fun.ticker``), и от обновления к обновлению
текст разный. Каждая фраза — одна короткая строка: окно-продолжение не
переносит текст.
"""

from __future__ import annotations

from updater.ui.fun_texts_en import EN as _EN
from updater.ui.fun_texts_ru import RU as _RU


def phrases(kind: str, language: str | None = None) -> tuple[str, ...]:
    table = _EN if str(language or "").lower().startswith("en") else _RU
    return table.get(kind) or _RU.get(kind) or _RU["downloading"]


__all__ = ["phrases"]
