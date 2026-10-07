"""Весёлые фразы для экранов проверок: пока ждём — не скучно.

Правило: шутка сопровождает работу, но не заменяет смысл. Вердикты и советы
пишутся в других местах точно и серьёзно; здесь — только «что сейчас
происходит», рассказанное с улыбкой.

Сами фразы лежат рядом: ``fun_texts_ru.py`` и ``fun_texts_en.py``. Наборы
нарочно большие (не меньше 50 в каждом блоке) и у каждого блока свои: фразы
идут в случайном порядке без повторов (см. ``ui.widgets.fun.ticker``), и от
проверки к проверке текст разный. Каждая фраза — одна короткая строка.
"""

from __future__ import annotations

from blockcheck.ui.fun_texts_en import EN as _EN
from blockcheck.ui.fun_texts_ru import RU as _RU

# Функции обхода → набор фраз. Первое совпадение по порядку выигрывает.
_TECHNIQUE_KEYS: tuple[tuple[str, str], ...] = (
    ("hostfake", "hostfake"),
    ("syndata", "syndata"),
    ("oob", "oob"),
    ("seqovl", "seqovl"),
    ("disorder", "disorder"),
    ("split", "split"),
    ("fake", "fake"),
    ("udplen", "udp"),
    ("quic", "udp"),
)


def phrases(kind: str, language: str | None = None) -> tuple[str, ...]:
    table = _EN if str(language or "").lower().startswith("en") else _RU
    return table.get(kind) or _RU.get(kind) or _RU["scan_generic"]


def technique_kind(strategy_args: str) -> str:
    """Какой набор фраз подходит стратегии (по её функциям ``--lua-desync``)."""
    text = str(strategy_args or "").lower()
    for needle, kind in _TECHNIQUE_KEYS:
        if needle in text:
            return kind
    return "scan_generic"


def strategy_phrases(strategy_args: str, language: str | None = None) -> tuple[str, ...]:
    return phrases(technique_kind(strategy_args), language)


__all__ = ["phrases", "strategy_phrases", "technique_kind"]
