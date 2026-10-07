"""Что делает стратегия — простыми словами, по её строкам ``--lua-desync``.

Описания функций и приёмов сверены с исходниками Zapret 2 (lua/zapret-antidpi.lua,
lua/zapret-lib.lua, docs/manual.md). Здесь только то, что следует из кода и
документации движка; чего там нет, тут не утверждается.
"""

from __future__ import annotations

from dataclasses import dataclass

_PREFIX = "--lua-desync="


@dataclass(frozen=True)
class StepNote:
    """Одна настройка шага: страница показывает её плиткой со значком.

    kind — вид настройки (по нему выбирается значок): protection, names,
    content, cut, repeats, overlap, tweak, origin.
    """

    kind: str
    label: str
    value: str
    detail: str = ""

    @property
    def text(self) -> str:
        return f"{self.label}: {self.value}" + (f" — {self.detail}" if self.detail else "")

    @property
    def chip(self) -> str:
        """Короткая подпись для метки на странице; пояснение уходит в подсказку."""
        if self.kind == "protection":
            return f"Защита: {self.value[:1].lower()}{self.value[1:]}"
        if self.kind in ("origin", "overlap"):
            return self.value
        if self.kind == "content":
            return f"В подделке: {self.value}"
        if self.kind == "cut":
            return f"Разрез: {self.value}"
        return f"{self.label}: {self.value}"

    @property
    def hint(self) -> str:
        return " ".join(part for part in (f"{self.label}.", self.detail) if part)


@dataclass(frozen=True)
class StrategyStep:
    """Одна строка стратегии, объяснённая человеку без подготовки.

    Каждая часть отвечает на свой вопрос и показывается отдельной строкой:
    что происходит, зачем это нужно, какие у шага настройки, о чём стоит знать.
    """

    function: str
    title: str
    # Что происходит с вашим запросом.
    text: str
    # Зачем это нужно: чем шаг сбивает фильтр провайдера.
    why: str = ""
    # Настройки шага: название, значение простыми словами и пояснение.
    notes: tuple[StepNote, ...] = ()
    # Группа способа обхода (profile.strategy_families) — для значка шага.
    family: str = "other"
    # О чём стоит знать: каждое предупреждение — отдельное.
    cautions: tuple[str, ...] = ()
    line: str = ""
    # Анимированная схема приёма (ключ сцены из ui.onboarding.illustrations);
    # пусто — схемы для этой функции нет.
    scene: str = ""

    @property
    def caution(self) -> str:
        return " ".join(self.cautions)


_SITE_DROPS_FAKES = "Сайт подделки отбрасывает и получает ваш запрос целым."

# функция: (название, что происходит, зачем, о чём стоит знать)
_FUNCTIONS: dict[str, tuple[str, str, str, str]] = {
    "fake": (
        "Подделка",
        "Перед вашим настоящим запросом программа отправляет ещё один — поддельный, с чужим именем сайта.",
        "Фильтр провайдера первым читает подделку, видит разрешённое имя и пропускает соединение. "
        "Сайт подделку отбрасывает и отвечает на настоящий запрос.",
        "",
    ),
    "multisplit": (
        "Нарезка",
        "Ваш запрос разрезается на несколько частей, и они уходят отдельными пакетами по порядку.",
        "Имя сайта оказывается разорвано между пакетами. Фильтр, который смотрит каждый пакет по "
        "отдельности, целого имени не видит. Сайт склеивает части обратно.",
        "",
    ),
    "multidisorder": (
        "Перестановка",
        "Ваш запрос разрезается на части, и они уходят в обратном порядке: сначала конец, потом начало.",
        "Фильтр ждёт начало запроса первым и не может сложить имя сайта. "
        "Сайт расставляет части по номерам и получает запрос целым.",
        "",
    ),
    "multidisorder_legacy": (
        "Перестановка, прежний вариант",
        "Ваш запрос разрезается на части, и они уходят в обратном порядке. Части меняются местами "
        "внутри каждого пакета отдельно — так работал первый Zapret.",
        "Фильтр ждёт начало запроса первым и не может сложить имя сайта. "
        "Сайт расставляет части по номерам и получает запрос целым.",
        "",
    ),
    "fakedsplit": (
        "Нарезка с подделками",
        "Ваш запрос делится на две части. Каждая уходит между двумя поддельными копиями того же "
        "размера, заполненными мусором.",
        "Фильтр видит шесть пакетов вместо двух и не знает, какие из них настоящие. " + _SITE_DROPS_FAKES,
        "Пакетов становится втрое больше обычного.",
    ),
    "fakeddisorder": (
        "Перестановка с подделками",
        "Ваш запрос делится на две части, и вторая уходит первой. Каждая часть идёт между двумя "
        "поддельными копиями того же размера.",
        "Фильтр видит шесть пакетов в непривычном порядке и не знает, какие из них настоящие. "
        + _SITE_DROPS_FAKES,
        "Пакетов становится втрое больше обычного.",
    ),
    "hostfakesplit": (
        "Подмена имени сайта",
        "Ваш запрос режется вокруг имени сайта. Настоящее имя уходит отдельным пакетом, а перед ним "
        "и после него — пакеты с поддельным именем.",
        "Фильтр видит поддельное имя и пропускает соединение. " + _SITE_DROPS_FAKES,
        "Работает только там, где имя сайта видно в запросе: обычный HTTP и начало защищённого соединения.",
    ),
    "syndata": (
        "Данные в первом пакете",
        "Соединение начинается со служебного пакета, в котором данных обычно нет. Программа кладёт в него данные.",
        "Часть фильтров, увидев данные в неожиданном месте, перестаёт следить за соединением.",
        "Некоторые сайты такой пакет не принимают — тогда соединение с ними не установится.",
    ),
    "send": (
        "Повтор пакета",
        "Текущий пакет отправляется ещё раз, с изменёнными служебными полями.",
        "Обычно работает вместе с другими шагами: лишний пакет сбивает фильтр со счёта.",
        "",
    ),
    "tcpseg": (
        "Отдельный кусок",
        "Выбранный кусок вашего запроса уходит отдельным пакетом.",
        "Позволяет поставить перед куском заглушку, которую сайт отбросит сам, а фильтр примет за данные.",
        "",
    ),
    "oob": (
        "Лишний байт",
        "В ваш запрос вставляется один «срочный» байт, который сайт выбросит.",
        "Фильтр читает запрос вместе с лишним байтом и не узнаёт в нём имя сайта.",
        "Самый рискованный приём: если профиль сменится посреди соединения, оно оборвётся.",
    ),
    "wssize": (
        "Мелкое окно",
        "Сайту сообщается, что ваш компьютер готов принимать данные только маленькими порциями.",
        "Ответ сайта приходит мелкими кусками, и фильтр не может прочитать его целиком.",
        "Снижает скорость. Автор Zapret советует применять, только когда другое не помогает.",
    ),
    "wsize": (
        "Мелкое окно",
        "В ответе сайта подменяется размер порции, которой он готов обмениваться данными.",
        "Данные идут мелкими кусками, и фильтр не может прочитать их целиком.",
        "Снижает скорость.",
    ),
    "udplen": (
        "Длина пакета",
        "К пакету дописываются лишние байты, или он укорачивается.",
        "Фильтр, который узнаёт трафик по длине пакетов, перестаёт его узнавать.",
        "",
    ),
    "pktmod": ("Правка пакета", "Меняются служебные поля самого пакета, без подделок.", "", ""),
    "drop": ("Сброс пакета", "Пакет не отправляется.", "", ""),
    "pass": ("Без обхода", "Ничего не делает: трафик идёт как есть.", "", ""),
}
# Функции, которых нет в оригинальном Zapret 2 — их добавил ZapretGUI:
# функция: (на какую функцию похожа, чем отличается).
_OWN_FUNCTIONS: dict[str, tuple[str, str, str]] = {
    "fakemultisplit": (
        "fakedsplit",
        "Нарезка с подделками в нескольких местах",
        "Ваш запрос режется в нескольких местах, и части уходят вперемешку с поддельными копиями.",
    ),
    "fakemultidisorder": (
        "fakeddisorder",
        "Перестановка с подделками в нескольких местах",
        "Ваш запрос режется в нескольких местах, и части уходят в обратном порядке вперемешку с поддельными копиями.",
    ),
    "hostfakesplit_multi": (
        "hostfakesplit",
        "Подмена имени сайта, несколько имён",
        "Ваш запрос режется вокруг имени сайта. Настоящее имя уходит отдельным пакетом, а перед ним "
        "и после него — пакеты с поддельными именами, которые берутся по очереди из списка.",
    ),
}

# Какой анимированной схемой показать функцию.
_SCENES = {
    "fake": "fake",
    "multisplit": "multisplit",
    "multidisorder": "multidisorder",
    "multidisorder_legacy": "multidisorder",
    "fakedsplit": "fakedsplit",
    "fakemultisplit": "fakedsplit",
    "fakeddisorder": "fakeddisorder",
    "fakemultidisorder": "fakeddisorder",
    "hostfakesplit": "hostfakesplit",
    "hostfakesplit_multi": "hostfakesplit",
    "tcpseg": "tcpseg",
    "oob": "oob",
    "syndata": "syndata",
}

# Эти функции шлют отдельные поддельные пакеты. Если подделку ничем не
# защитить, сайт примет её как настоящие данные (docs/manual.md Zapret 2).
_SENDS_FAKES = frozenset({"fake", "fakedsplit", "fakeddisorder", "hostfakesplit", "syndata"})
_UNPROTECTED_FAKE_WARNING = (
    "У подделки нет защиты от сайта: она может дойти до него вместе с настоящим запросом и сломать соединение."
)

# параметр: (как называется защита, как она работает, о чём стоит знать)
_PROTECTIONS: dict[str, tuple[str, str, str]] = {
    "ip_ttl": (
        "Короткий путь",
        "Пакету разрешено пройти только несколько узлов сети: до фильтра провайдера он доходит, до сайта — нет.",
        "Число узлов подобрано под конкретного провайдера. У другого подделка может дойти до сайта "
        "или, наоборот, не дойти до фильтра.",
    ),
    "ip_autottl": (
        "Короткий путь, подобранный автоматически",
        "Длину пути программа прикидывает сама по ответу сайта.",
        "Длина пути — оценка, а не точное знание: считается, что дорога туда и обратно одинакова.",
    ),
    "tcp_md5": ("Лишняя подпись", "В пакете подпись, которой сайт не ждёт: увидев её, он пакет отбрасывает.", ""),
    "badsum": ("Испорченная контрольная сумма", "Сайт считает пакет повреждённым и отбрасывает.", ""),
    "tcp_seq": ("Неверный порядковый номер", "Сайт считает, что пакет не из этого соединения.", ""),
    "tcp_ack": ("Неверный номер подтверждения", "Сайт такой пакет отбрасывает.", ""),
    "tcp_ts": (
        "Старая метка времени",
        "Сайт считает пакет устаревшим и отбрасывает.",
        "Метки времени есть не в каждом соединении. Если их нет, подделка уйдёт без этой защиты.",
    ),
    "tcp_flags_unset": ("Снятый служебный флаг", "Без этого флага сайт пакет не принимает.", ""),
    "tcp_flags_set": ("Лишний служебный флаг", "С этим флагом сайт пакет не принимает.", ""),
}
_PROTECTIONS["ip6_ttl"] = _PROTECTIONS["ip_ttl"]
_PROTECTIONS["ip6_autottl"] = _PROTECTIONS["ip_autottl"]

# Значок шага — группа способа обхода, к которой относится функция.
_FAMILIES = {
    "fake": "fake",
    "multisplit": "split",
    "tcpseg": "split",
    "multidisorder": "disorder",
    "multidisorder_legacy": "disorder",
    "fakedsplit": "fake_split",
    "fakemultisplit": "fake_split",
    "fakeddisorder": "fake_disorder",
    "fakemultidisorder": "fake_disorder",
    "hostfakesplit": "host",
    "hostfakesplit_multi": "host",
    "syndata": "send",
    "send": "send",
    "udplen": "fake_udplen",
}

_TLS_MODS = {
    "rnd": "случайные служебные поля",
    "rndsni": "случайное имя сайта",
    "dupsid": "номер сеанса скопирован из настоящего запроса",
    "padencap": "настоящие данные спрятаны внутри поля-заполнителя",
}
_BLOBS = {
    "fake_default_tls": "стандартный запрос защищённого соединения к www.microsoft.com",
    "fake_default_http": "стандартный HTTP-запрос к www.iana.org",
    "fake_default_quic": "заглушка вместо начала соединения QUIC",
}
# Места разреза запроса словами.
_POSITIONS = {
    "midsld": "посередине имени сайта",
    "host": "в начале имени сайта",
    "endhost": "в конце имени сайта",
    "sld": "в начале основной части имени сайта",
    "endsld": "в конце основной части имени сайта",
    "sniext": "у поля с именем сайта",
    "method": "после названия запроса",
}


def _plural_times(count: int) -> str:
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return f"{count} раза"
    return f"{count} раз"


def _position_words(value: str) -> str:
    """Места разреза словами: «1,midsld» → «после 1-го байта; посередине имени сайта»."""
    words: list[str] = []
    for token in (part.strip() for part in value.split(",")):
        if not token:
            continue
        if token.isdigit():
            words.append(f"после {token}-го байта")
            continue
        base = token.replace("+", " ").replace("-", " ").split()[0] if token[0].isalpha() else ""
        phrase = _POSITIONS.get(base)
        if phrase is None:
            words.append(token)
        elif token == base:
            words.append(phrase)
        else:
            # Сдвиг от места оставлен как в стратегии: «у поля с именем сайта (sniext+1)».
            words.append(f"{phrase} ({token})")
    return "; ".join(words)


def _notes(parameters: list[tuple[str, str]]) -> tuple[list[StepNote], list[str]]:
    notes: list[StepNote] = []
    cautions: list[str] = []
    for key, value in parameters:
        protection = _PROTECTIONS.get(key)
        if protection is not None:
            notes.append(StepNote("protection", "Защита подделки от сайта", protection[0], protection[1]))
            if protection[2]:
                cautions.append(protection[2])
        elif key == "seqovl":
            notes.append(
                StepNote(
                    "overlap",
                    "Перекрытие",
                    "Заглушка перед первой частью",
                    "Сайт отбросит её сам, а фильтр примет за данные. Отдельная защита ей не нужна.",
                )
            )
        elif key in ("blob", "fake_blob") and value:
            notes.append(StepNote("content", "Что в подделке", _BLOBS.get(value, value)))
        elif key == "pos" and value:
            notes.append(StepNote("cut", "Где режется запрос", _position_words(value)))
        elif key == "repeats" and value.isdigit() and int(value) > 1:
            notes.append(
                StepNote("repeats", "Повторы", f"{_plural_times(int(value))} подряд", "На случай если первую фильтр пропустит.")
            )
        elif key == "tls_mod" and value:
            mods = [
                f"имя сайта {mod[4:]}" if mod.startswith("sni=") else _TLS_MODS.get(mod, mod)
                for mod in value.split(",")
                if mod
            ]
            notes.append(StepNote("tweak", "Подделка подправлена", ", ".join(mods)))
        elif key in ("host", "hosts") and value:
            names = ", ".join(name.strip() for name in value.split(",") if name.strip())
            notes.append(StepNote("names", "Поддельные имена", names))
    return notes, cautions


def explain_strategy(args: str) -> tuple[StrategyStep, ...]:
    """Шаги стратегии по порядку строк; строки не про ``--lua-desync`` пропускаются."""
    steps: list[StrategyStep] = []
    for raw in str(args or "").splitlines():
        line = raw.strip()
        if not line.lower().startswith(_PREFIX):
            continue
        parts = line[len(_PREFIX) :].split(":")
        function = parts[0].strip().lower()
        if not function:
            continue
        parameters = [(key.strip().lower(), value.strip()) for key, _sep, value in (part.partition("=") for part in parts[1:])]
        notes, cautions = _notes(parameters)
        base_function = function
        if function in _OWN_FUNCTIONS:
            base_function, title, text = _OWN_FUNCTIONS[function]
            _title, _text, why, caution = _FUNCTIONS[base_function]
            notes.append(StepNote("origin", "Откуда приём", "Добавлен в ZapretGUI", "В оригинальном Zapret 2 его нет."))
        elif function in _FUNCTIONS:
            title, text, why, caution = _FUNCTIONS[function]
        else:
            title, text, why, caution = function, "Собственная функция: в оригинальном Zapret 2 её нет, описания для неё нет.", "", ""
        own_cautions = [caution] if caution else []
        if base_function in _SENDS_FAKES and not any(key in _PROTECTIONS for key, _value in parameters):
            own_cautions.append(_UNPROTECTED_FAKE_WARNING)
        steps.append(
            StrategyStep(
                function=function,
                title=title,
                text=text,
                why=why,
                notes=tuple(notes),
                cautions=tuple(dict.fromkeys((*own_cautions, *cautions))),
                line=line,
                scene=_SCENES.get(function, ""),
                family=_FAMILIES.get(function, "other"),
            )
        )
    return tuple(steps)
