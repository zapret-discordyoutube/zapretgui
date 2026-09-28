"""Весёлые фразы для экранов проверок: пока ждём — не скучно.

Правило: шутка сопровождает работу, но не заменяет смысл. Вердикты и советы
пишутся в других местах точно и серьёзно; здесь — только «что сейчас
происходит», рассказанное с улыбкой.
"""

from __future__ import annotations

_RU: dict[str, tuple[str, ...]] = {
    "scan_network": (
        "Проверяем, есть ли вообще интернет… а то мало ли",
        "Стучимся на обычные сайты: интернет, ты тут?",
    ),
    "scan_baseline": (
        "Смотрим, как сайт открывается без всяких хитростей…",
        "Честно пробуем открыть сайт напрямую. Вдруг пустят?",
        "Спрашиваем у провайдера: «А можно?»",
    ),
    "scan_control": (
        "Запускаем winws2 вхолостую: проверяем, не волшебный ли он сам по себе",
        "Контрольный выстрел холостыми…",
    ),
    "fake": (
        "Подсовываем DPI фальшивое письмо, пока настоящее проходит мимо…",
        "Показываем DPI муляж. Пусть изучает",
        "Отправляем двойника вперёд. Настоящий идёт следом на цыпочках",
    ),
    "split": (
        "Режем приветствие на аккуратные ломтики…",
        "Разбираем пакет на запчасти — DPI такое не собирает",
        "Нарезаем данные, как колбасу к бутерброду",
    ),
    "disorder": (
        "Тасуем пакеты, как колоду карт…",
        "Отправляем кусочки задом наперёд. DPI в замешательстве",
        "Сначала конец, потом начало — сервер разберётся, а DPI нет",
    ),
    "syndata": (
        "Прячем записку прямо в рукопожатие…",
        "Шепчем данные ещё до «здравствуйте»",
    ),
    "oob": (
        "Шлём пакет через чёрный ход…",
        "Срочная доставка вне очереди!",
    ),
    "seqovl": (
        "Накладываем пакеты внахлёст, как черепицу…",
        "Прикрываем важное куском мусора сверху",
    ),
    "hostfake": (
        "Переодеваем имя сайта в чужой костюм…",
        "Говорим DPI, что мы вообще-то google.com",
    ),
    "udp": (
        "Уговариваем UDP-пакеты проскочить незаметно…",
        "Раскрашиваем голосовые пакеты под обычный шум",
        "Маскируем игровые пакеты под что-то скучное",
    ),
    "scan_generic": (
        "Примеряем очередную стратегию, как новую шляпу…",
        "Перебираем отмычки одну за другой…",
        "DPI ещё держится. Но мы упрямые",
        "Пробуем. Если не сработает — у нас ещё много идей",
        "Колдуем над пакетами… почти без магии",
        "Ищем, где у DPI слепое пятно…",
    ),
    "scan_found": (
        "Есть контакт! Проверяем ещё раз, чтобы не показалось…",
        "Кажется, нащупали! Перепроверяем для надёжности",
    ),
    "blockcheck": (
        "Стучимся на сайты: тук-тук, кто там?",
        "Обходим сайты по списку, как почтальон…",
        "Проверяем, кого провайдер не пускает…",
        "Спрашиваем у каждого сайта: «Ты как?»",
        "Меряем, где интернет заканчивается…",
        "Проверяем сеть на честность…",
    ),
    "dns": (
        "Сверяем адреса с честным справочником…",
        "Спрашиваем адреса у двух разных соседей и сравниваем…",
        "Ищем, не подменяет ли кто-то адреса по дороге…",
        "Проверяем, не врёт ли DNS…",
    ),
}

_EN: dict[str, tuple[str, ...]] = {
    "scan_network": ("Checking there is internet at all… just in case",),
    "scan_baseline": (
        "Trying the site directly, no tricks…",
        "Politely asking the ISP: «May we?»",
    ),
    "scan_control": ("Firing winws2 with blanks: is it magic by itself?",),
    "fake": ("Handing DPI a decoy letter while the real one sneaks past…",),
    "split": ("Slicing the hello into neat little pieces…",),
    "disorder": ("Shuffling packets like a deck of cards…",),
    "syndata": ("Hiding a note right in the handshake…",),
    "oob": ("Sending a packet through the back door…",),
    "seqovl": ("Overlapping packets like roof tiles…",),
    "hostfake": ("Dressing the site name in someone else's clothes…",),
    "udp": ("Sneaking UDP packets past quietly…",),
    "scan_generic": (
        "Trying on the next strategy like a new hat…",
        "Picking locks one by one…",
        "DPI is still holding. We are stubborn",
    ),
    "scan_found": ("Contact! Checking again to be sure…",),
    "blockcheck": (
        "Knocking on sites: knock-knock, who's there?",
        "Asking every site: «How are you?»",
    ),
    "dns": ("Comparing addresses with an honest phonebook…",),
}

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
    kind = technique_kind(strategy_args)
    own = phrases(kind, language)
    if kind == "scan_generic":
        return own
    # Своих фраз мало — разбавляем общими, чтобы не приедались.
    return own + phrases("scan_generic", language)[:3]


__all__ = ["phrases", "strategy_phrases", "technique_kind"]
