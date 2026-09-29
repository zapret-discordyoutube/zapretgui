"""Весёлые фразы окна обновления: пока качается — не скучно.

Правило то же, что у BlockCheck (``blockcheck/ui/fun_texts.py``): шутка
сопровождает работу, но не заменяет смысл. Проценты, скорость и ошибки
пишутся рядом точно и серьёзно, здесь — только настроение.
"""

from __future__ import annotations

_RU: dict[str, tuple[str, ...]] = {
    "preparing": (
        "Будим сервер обновлений: «Эй, у тебя тут новая версия завалялась?»",
        "Ищем, с какой полки взять свежую версию…",
        "Разминаемся перед забегом…",
        "Проверяем шнурки. Сейчас побежим",
        "Выдра надевает кроссовки…",
    ),
    "downloading": (
        "Выгружаем свежие байты прямо из печи…",
        "Выдра несёт обновление в зубах. Аккуратно, не уронить!",
        "Байты бегут строем, раз-два, раз-два…",
        "Качаем новую версию. Старая делает вид, что не ревнует",
        "Несём новые фичи. Баги оставили дома",
        "Складываем байтики в коробочку по одному…",
        "Скорость хорошая, провайдер пока не заметил",
        "Каждый байт проверен лично выдрой",
        "Почти как пицца: горячее, свежее, скоро будет у вас",
        "DPI провожает обновление грустным взглядом",
        "Если прислушаться, слышно, как шуршат пакеты",
        "Тянем-потянем — вытянули ещё мегабайт",
    ),
    "installing": (
        "Сверяем контрольную сумму: всё своё, ничего чужого",
        "Передаём эстафету установщику…",
        "Программа сейчас закроется — это не баг, это переезд",
        "Собираем чемоданы. До встречи в новой версии!",
    ),
    "failed": (
        "Выдра споткнулась. Бывает с лучшими из нас",
        "Байты разбежались. Попробуем собрать ещё раз?",
    ),
    "whats_new": (
        "Листаем свежий список изменений…",
        "Достаём заметки к выпуску…",
    ),
}

_EN: dict[str, tuple[str, ...]] = {
    "preparing": (
        "Waking up the update server: «Got a new version lying around?»",
        "Stretching before the run…",
        "The otter is lacing up its sneakers…",
    ),
    "downloading": (
        "Unloading fresh bytes straight from the oven…",
        "The otter carries the update in its teeth. Careful!",
        "Bytes are marching in line, left-right, left-right…",
        "Bringing new features. Bugs stayed at home",
        "Every byte personally checked by the otter",
        "DPI watches the update leave with a sad look",
    ),
    "installing": (
        "Checking the checksum: everything is ours",
        "Passing the baton to the installer…",
        "The app is about to close — it is moving, not crashing",
    ),
    "failed": (
        "The otter tripped. Happens to the best of us",
        "Bytes ran away. Shall we try again?",
    ),
    "whats_new": (
        "Flipping through the fresh changelog…",
    ),
}


def phrases(kind: str, language: str | None = None) -> tuple[str, ...]:
    table = _EN if str(language or "").lower().startswith("en") else _RU
    return table.get(kind) or _RU.get(kind) or _RU["downloading"]


__all__ = ["phrases"]
