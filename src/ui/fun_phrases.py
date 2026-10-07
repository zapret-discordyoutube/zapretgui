"""Весёлые фразы для общих мест программы: запуск, остановка, загрузка.

Правило то же, что у проверок (``blockcheck/ui/fun_texts.py``): шутка
сопровождает работу, но не заменяет смысл. Поэтому каждая фраза начинается с
того, что сейчас происходит («Запускаем…», «Останавливаем…», «Загрузка…»), а
шутка идёт следом. Эти места — главные экраны, так что тон здесь спокойный.

Сами фразы лежат рядом: ``fun_phrases_ru.py`` и ``fun_phrases_en.py``.
"""

from __future__ import annotations

import random

from ui.fun_phrases_en import EN as _EN
from ui.fun_phrases_ru import RU as _RU

# Служебный текст занятости движка → набор фраз. Сам служебный текст остаётся
# в состоянии программы как есть: по нему страницы узнают, что именно идёт.
_BUSY_KINDS: tuple[tuple[str, str], ...] = (
    ("Применяем пресет", "preset_apply"),
    ("Запуск Zapret", "engine_start"),
    ("Остановка Zapret", "engine_stop"),
)

_rng = random.Random()


def phrases(kind: str, language: str | None = None) -> tuple[str, ...]:
    table = _EN if str(language or "").lower().startswith("en") else _RU
    return table.get(kind) or _RU.get(kind) or ()


def random_phrase(kind: str, language: str | None = None, *, default: str = "") -> str:
    pool = phrases(kind, language)
    return _rng.choice(pool) if pool else default


def busy_kind(busy_text: str) -> str:
    """Какой набор фраз подходит служебному тексту занятости; пусто — никакой."""
    text = str(busy_text or "")
    for needle, kind in _BUSY_KINDS:
        if needle in text:
            return kind
    return ""


class StickyPhrase:
    """Одна случайная фраза на всё время, пока исходный текст не меняется.

    Страницы перерисовывают подписи много раз подряд; без этого фраза
    менялась бы на каждой перерисовке и мигала.
    """

    def __init__(self) -> None:
        self._source = ""
        self._phrase = ""

    def reset(self) -> None:
        self._source = ""
        self._phrase = ""

    def loading(self, language: str | None = None, *, default: str = "") -> str:
        """Фраза «Загрузка…»; сбрасывается через ``reset``, когда данные пришли."""
        if self._source != "loading" or not self._phrase:
            self._source = "loading"
            self._phrase = random_phrase("loading", language, default=default)
        return self._phrase

    def busy(self, busy_text: str, language: str | None = None) -> str:
        """Фраза вместо служебного текста занятости; незнакомый текст — как есть."""
        source = str(busy_text or "")
        if not source:
            self.reset()
            return ""
        if source != self._source or not self._phrase:
            kind = busy_kind(source)
            self._source = source
            self._phrase = random_phrase(kind, language, default=source) if kind else source
        return self._phrase


def loading_phrase_for(owner, language: str | None = None, *, default: str = "") -> str:
    """Фраза «Загрузка…» для страницы: одна и та же, пока страница жива."""
    sticky = owner.__dict__.get("_fun_loading_phrase")
    if sticky is None:
        sticky = owner.__dict__["_fun_loading_phrase"] = StickyPhrase()
    return sticky.loading(language, default=default)


__all__ = ["StickyPhrase", "busy_kind", "loading_phrase_for", "phrases", "random_phrase"]
