"""Шуточное окно перед установкой плитки «НСДИ» (группа «Для смеха»).

Плитка настоящая: это адреса национальной системы доменных имён. Но перед
установкой страница «Настройка DNS» спрашивает ещё раз, и каждый раз другой
фразой: их PHRASE_COUNT штук, тексты лежат в app.ui_texts под ключами
page.network.joke.NN.title / .content (NN от 01). Какую фразу показать,
решает pick(): та же подряд не выпадает.

Окно только спрашивает: «Да» ставит DNS как обычную плитку, «Нет» ничего не меняет.
"""

from __future__ import annotations

import random

# Ключ записи каталога (dns.dns_providers), которому нужно окно перед установкой.
CONFIRM_KEY = "confirm_joke"
# Сколько разных фраз написано в app.ui_texts; не меньше десяти.
PHRASE_COUNT = 12


def needs_confirmation(data: dict) -> bool:
    return bool(data.get(CONFIRM_KEY))


def phrase_keys(index: int) -> tuple[str, str]:
    """Ключи заголовка и текста фразы номер index (от 1 до PHRASE_COUNT)."""
    if not 1 <= index <= PHRASE_COUNT:
        raise ValueError(f"нет фразы номер {index}")
    return f"page.network.joke.{index:02d}.title", f"page.network.joke.{index:02d}.content"


def pick(avoid: int = 0, rng: random.Random | None = None) -> int:
    """Номер случайной фразы, не равный avoid (прошлой показанной)."""
    chooser = rng or random
    choices = [number for number in range(1, PHRASE_COUNT + 1) if number != avoid]
    return chooser.choice(choices)


__all__ = ["CONFIRM_KEY", "PHRASE_COUNT", "needs_confirmation", "phrase_keys", "pick"]
