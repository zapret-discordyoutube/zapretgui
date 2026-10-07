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


_SITE_DROPS_FAKES = "Подделки до сайта не доходят или он их отбрасывает, а ваш запрос получает целым."

# функция: (название, что происходит, зачем, о чём стоит знать)
_FUNCTIONS: dict[str, tuple[str, str, str, str]] = {
    "fake": (
        "Подделка",
        "Перед вашим настоящим запросом программа отправляет ещё один — поддельный, с чужим именем сайта.",
        "Фильтр провайдера первым читает подделку, видит разрешённое имя и пропускает соединение. "
        "Подделка до сайта не доходит или он её отбрасывает, и отвечает он на настоящий запрос.",
        "",
    ),
    "multisplit": (
        "Нарезка",
        "Ваш запрос разрезается на несколько частей, и они уходят отдельными пакетами по порядку.",
        "Когда разрез проходит внутри имени сайта, имя оказывается разорвано между пакетами. Фильтр, "
        "который смотрит каждый пакет по отдельности, целого имени не видит. Сайт склеивает части обратно.",
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
        "размера с ненастоящим содержимым.",
        "Фильтр видит шесть пакетов вместо двух и не знает, какие из них настоящие. " + _SITE_DROPS_FAKES,
        "Пакетов уходит шесть вместо двух.",
    ),
    "fakeddisorder": (
        "Перестановка с подделками",
        "Ваш запрос делится на две части, и вторая уходит первой. Каждая часть идёт между двумя "
        "поддельными копиями того же размера.",
        "Фильтр видит шесть пакетов в непривычном порядке и не знает, какие из них настоящие. "
        + _SITE_DROPS_FAKES,
        "Пакетов уходит шесть вместо двух.",
    ),
    "hostfakesplit": (
        "Подмена имени сайта",
        "Ваш запрос режется вокруг имени сайта. Настоящее имя уходит отдельным пакетом, а перед ним "
        "и после него — пакеты со случайным поддельным именем той же длины.",
        "Фильтр видит не то имя и пропускает соединение. " + _SITE_DROPS_FAKES,
        "Работает только там, где имя сайта видно в запросе: обычный HTTP и начало защищённого соединения.",
    ),
    "syndata": (
        "Данные в первом пакете",
        "Соединение начинается со служебного пакета, в котором данных обычно нет. Программа кладёт в него "
        "данные и отправляет вместо обычного.",
        "",
        "",
    ),
    "send": (
        "Повтор пакета",
        "Отправляется копия текущего пакета с изменёнными служебными полями.",
        "",
        "",
    ),
    "tcpseg": (
        "Отдельный кусок",
        "Выбранный кусок вашего запроса уходит отдельным пакетом.",
        "Позволяет поставить перед куском заглушку, которую сайт отбросит сам, а фильтр примет за данные.",
        "Исходный пакет этот шаг не заменяет: он уйдёт следом, если в стратегии нет шага «сброс пакета».",
    ),
    "oob": (
        "Лишний байт",
        "В начало вашего запроса вставляется один «срочный» байт, который сайт выбросит.",
        "Фильтр читает запрос вместе с лишним байтом и не узнаёт в нём имя сайта.",
        "В обычном режиме работает только с сайтами на Linux, остальным ломает соединение. "
        "С шагами нарезки не сочетается. Если профиль сменится посреди соединения, оно оборвётся.",
    ),
    "wssize": (
        "Мелкое окно",
        "Сайту сообщается, что ваш компьютер готов принимать данные только маленькими порциями.",
        "Ответ сайта приходит мелкими кусками, и фильтр не может прочитать его целиком. "
        "Рассчитано на соединения, где имя сайта видно в ответе (TLS 1.2).",
        "Снижает скорость. Автор Zapret советует применять, только когда другое не помогает.",
    ),
    "wsize": (
        "Мелкое окно",
        "В ответе сайта подменяется размер порции, которую он готов принять, и ваш компьютер сам "
        "отправляет запрос мелкими частями.",
        "Запрос уходит кусками, и фильтр не может прочитать его целиком.",
        "Снижает скорость. Автор Zapret считает приём устаревшим и советует вместо него нарезку.",
    ),
    "udplen": (
        "Длина пакета",
        "К пакету дописываются лишние байты, или он укорачивается.",
        "",
        "При укорочении конец данных обрезается и теряется; дописанные байты терпит не каждая программа.",
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

# Если подделку ничем не защитить, сайт примет её как настоящие данные
# (docs/manual.md Zapret 2). Это касается подделок в TCP: данные в первом
# пакете (syndata) — «скрытая подделка», а в UDP (QUIC, звонки) автор Zapret
# сам шлёт подделки без защиты.
_SENDS_FAKES = frozenset({"fake", "fakedsplit", "fakeddisorder", "hostfakesplit"})
_UDP_BLOB_HINTS = ("quic", "stun", "discord", "wireguard", "dht", "udp")
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
        "Длина пути — оценка, а не точное знание. Если определить её не удалось, подделка уйдёт без этой защиты.",
    ),
    "tcp_md5": ("Лишняя подпись", "В пакет добавлена подпись, которой сайт не ждёт, и он пакет отбрасывает.", ""),
    "badsum": ("Испорченная контрольная сумма", "Сайт считает пакет повреждённым и отбрасывает.", ""),
    "tcp_seq": ("Неверный порядковый номер", "Номер вне ожидаемого диапазона: сайт отбрасывает пакет как лишний.", ""),
    "tcp_ack": (
        "Неверный номер подтверждения",
        "Сайт такой пакет отбрасывает.",
        "Надёжно работает вместе с переносом метки времени в начало заголовка (tcp_ts_up).",
    ),
    "tcp_ts": (
        "Старая метка времени",
        "Сайт считает пакет устаревшим и отбрасывает.",
        "Метки времени есть не в каждом соединении. Если их нет, подделка уйдёт без этой защиты.",
    ),
    "tcp_flags_unset": ("Снятый служебный флаг", "Обычно сайт такой пакет не принимает.", ""),
    "tcp_flags_set": ("Лишний служебный флаг", "Обычно сайт такой пакет не принимает.", ""),
    "ip6_hopbyhop": ("Дополнительный заголовок IPv6", "В пакет вставлен лишний служебный заголовок.", ""),
    "ip6_destopt": ("Дополнительный заголовок IPv6", "В пакет вставлен лишний служебный заголовок.", ""),
    "ip6_routing": ("Дополнительный заголовок IPv6", "В пакет вставлен лишний служебный заголовок.", ""),
    "ip6_ah": ("Дополнительный заголовок IPv6", "В пакет вставлен лишний служебный заголовок.", ""),
    "fool": ("Свой приём порчи", "Пакет портит отдельная функция, заданная в стратегии.", ""),
}
_PROTECTIONS["ip6_ttl"] = _PROTECTIONS["ip_ttl"]
_PROTECTIONS["ip6_autottl"] = _PROTECTIONS["ip_autottl"]
# Метка времени, сдвинутая вперёд, устаревшей не выглядит.
_TS_FORWARD = (
    "Метка времени сдвинута вперёд",
    "Пакет не выглядит устаревшим.",
    "Сдвиг метки времени вперёд сайт может и не отбросить: обычно её сдвигают назад.",
)
# Отдельные поддельные пакеты шлют только эти функции. У остальных те же
# параметры меняют настоящие пакеты шага (docs/manual.md Zapret 2).
_HAS_FAKES = _SENDS_FAKES
# Перестановка кладёт перекрытие иначе, чем нарезка.
_DISORDER = frozenset({"multidisorder", "multidisorder_legacy", "fakeddisorder"})

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
    # Основная часть имени — «youtube» в www.youtube.com.
    "midsld": "посередине основной части имени сайта",
    "host": "в начале имени сайта",
    "endhost": "в конце имени сайта",
    "sld": "в начале основной части имени сайта",
    "endsld": "в конце основной части имени сайта",
    "sniext": "у поля с именем сайта",
    "method": "в начале названия запроса (GET, POST)",
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
        if token[0] == "-" and token[1:].isdigit():
            words.append(f"за {token[1:]} байт до конца")
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


def _notes(function: str, parameters: list[tuple[str, str]]) -> tuple[list[StepNote], list[str]]:
    notes: list[StepNote] = []
    cautions: list[str] = []
    has_fakes = function in _HAS_FAKES
    for key, value in parameters:
        protection = _PROTECTIONS.get(key)
        if key == "tcp_ts" and value and not value.startswith("-"):
            protection = _TS_FORWARD
        if protection is not None:
            if has_fakes:
                notes.append(StepNote("protection", "Защита подделки от сайта", protection[0], protection[1]))
            else:
                # У шага нет отдельных подделок: параметр меняет его собственные пакеты.
                notes.append(
                    StepNote("protection", "Изменение пакетов шага", protection[0], "Применяется к пакетам этого шага, а не к подделке.")
                )
            if protection[2] and has_fakes:
                cautions.append(protection[2])
        elif key == "seqovl":
            if function in _DISORDER:
                notes.append(
                    StepNote(
                        "overlap",
                        "Перекрытие",
                        "Ложные байты перед второй частью",
                        "Настоящая первая часть потом перезапишет их у сайта.",
                    )
                )
                cautions.append("Перекрытие при перестановке не работает с сайтами на Windows.")
            else:
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
            label = "Какой кусок" if function == "tcpseg" else "Где режется запрос"
            notes.append(StepNote("cut", label, _position_words(value)))
        elif key == "repeats" and value.isdigit() and int(value) > 1:
            notes.append(StepNote("repeats", "Повторы", f"{_plural_times(int(value))} подряд"))
        elif key == "tls_mod" and value:
            mods = [
                f"имя сайта {mod[4:]}" if mod.startswith("sni=") else _TLS_MODS.get(mod, mod)
                for mod in value.split(",")
                if mod
            ]
            notes.append(StepNote("tweak", "Подделка подправлена", ", ".join(mods)))
        elif key == "hosts" and value:
            names = ", ".join(name.strip() for name in value.split(",") if name.strip())
            notes.append(StepNote("names", "Поддельные имена", names))
        elif key == "host" and value:
            notes.append(StepNote("names", "Образец поддельного имени", value, "Перед ним подставляется случайная часть."))
    return notes, cautions


def explain_strategy(args: str, *, udp: bool = False) -> tuple[StrategyStep, ...]:
    """Шаги стратегии по порядку строк; строки не про ``--lua-desync`` пропускаются.

    udp — стратегия для UDP (QUIC, звонки): там подделкам защита не нужна.
    """
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
        base_function = _OWN_FUNCTIONS[function][0] if function in _OWN_FUNCTIONS else function
        notes, cautions = _notes(base_function, parameters)
        if function in _OWN_FUNCTIONS:
            base_function, title, text = _OWN_FUNCTIONS[function]
            _title, _text, why, caution = _FUNCTIONS[base_function]
            notes.append(StepNote("origin", "Откуда приём", "Добавлен в ZapretGUI", "В оригинальном Zapret 2 его нет."))
        elif function in _FUNCTIONS:
            title, text, why, caution = _FUNCTIONS[function]
        else:
            title, text, why, caution = function, "Собственная функция: в оригинальном Zapret 2 её нет, описания для неё нет.", "", ""
        own_cautions = [caution] if caution else []
        udp_fake = udp or any(
            key in ("blob", "fake_blob") and any(hint in value.lower() for hint in _UDP_BLOB_HINTS)
            for key, value in parameters
        )
        if base_function in _SENDS_FAKES and not udp_fake and not any(key in _PROTECTIONS for key, _value in parameters):
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
