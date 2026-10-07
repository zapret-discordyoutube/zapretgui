"""Что делает стратегия — простыми словами, по её строкам ``--lua-desync``.

Описания функций и приёмов сверены с исходниками Zapret 2 (lua/zapret-antidpi.lua,
lua/zapret-lib.lua, docs/manual.md). Здесь только то, что следует из кода и
документации движка; чего там нет, тут не утверждается.
"""

from __future__ import annotations

from dataclasses import dataclass

_PREFIX = "--lua-desync="


@dataclass(frozen=True)
class StrategyStep:
    """Одна строка стратегии: что она делает и на что обратить внимание."""

    function: str
    title: str
    text: str
    caution: str = ""
    notes: tuple[str, ...] = ()
    line: str = ""
    # Анимированная схема приёма (ключ сцены из ui.onboarding.illustrations);
    # пусто — схемы для этой функции нет.
    scene: str = ""


# функция: (название, что делает, на что обратить внимание)
_FUNCTIONS: dict[str, tuple[str, str, str]] = {
    "fake": (
        "Подделка",
        "Перед настоящим запросом уходит отдельный поддельный пакет. Настоящий пакет не меняется.",
        "",
    ),
    "multisplit": (
        "Нарезка",
        "Запрос режется в указанных местах и уходит несколькими частями по порядку.",
        "",
    ),
    "multidisorder": (
        "Перестановка",
        "Запрос режется на части, и они уходят в обратном порядке: сначала последняя. Сервер сам собирает их как надо.",
        "",
    ),
    "multidisorder_legacy": (
        "Перестановка, прежний вариант",
        "Как перестановка, но части меняются местами внутри каждого пакета отдельно — так работал первый Zapret.",
        "",
    ),
    "fakedsplit": (
        "Нарезка с подделками",
        "Запрос делится на две части, и каждая уходит в окружении поддельных копий того же размера.",
        "Пакетов становится до шести вместо двух.",
    ),
    "fakeddisorder": (
        "Перестановка с подделками",
        "Запрос делится на две части в окружении поддельных копий, и вторая часть уходит первой.",
        "Пакетов становится до шести вместо двух.",
    ),
    "hostfakesplit": (
        "Подмена имени сайта",
        "Запрос режется вокруг имени сайта: настоящее имя уходит между двумя пакетами с поддельным именем.",
        "Работает только там, где имя сайта видно в запросе: HTTP и начало защищённого соединения.",
    ),
    "syndata": (
        "Данные в первом пакете",
        "В самый первый пакет соединения, где данных обычно нет, кладутся данные.",
        "Некоторые серверы такой пакет отбрасывают.",
    ),
    "send": ("Повтор пакета", "Текущий пакет отправляется ещё раз с изменёнными полями.", ""),
    "tcpseg": ("Отдельный кусок", "Выбранный кусок запроса уходит отдельным пакетом.", ""),
    "oob": (
        "Лишний байт",
        "В запрос вставляется один «срочный» байт, который сервер выбросит.",
        "Самый рискованный приём: если профиль сменится посреди соединения, оно оборвётся.",
    ),
    "wssize": (
        "Мелкое окно",
        "Серверу сообщается маленький размер окна, чтобы он отвечал мелкими частями.",
        "Снижает скорость. Автор Zapret советует применять, только когда другое не помогает.",
    ),
    "wsize": (
        "Мелкое окно",
        "В ответе сервера подменяется размер окна, чтобы он присылал данные мелкими частями.",
        "Снижает скорость.",
    ),
    "udplen": ("Длина пакета", "К UDP-пакету дописываются лишние байты, или он укорачивается.", ""),
    "pktmod": ("Правка пакета", "Меняются поля самого пакета, без подделок.", ""),
    "drop": ("Сброс пакета", "Пакет не отправляется.", ""),
    "pass": ("Без обхода", "Ничего не делает: трафик идёт как есть.", ""),
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
# «испортить», сервер примет её как настоящие данные (docs/manual.md Zapret 2).
_SENDS_FAKES = frozenset({"fake", "fakedsplit", "fakeddisorder", "hostfakesplit", "syndata"})
_UNSPOILED_FAKE_WARNING = (
    "Подделку нужно «испортить», чтобы сервер её отбросил: иначе она дойдёт до сайта и сломает соединение. "
    "В этой строке приёма порчи нет."
)
# Параметры, которые портят подделку для сервера.
_SPOILERS = frozenset(
    {"ip_ttl", "ip6_ttl", "ip_autottl", "ip6_autottl", "tcp_md5", "badsum", "tcp_seq", "tcp_ack", "tcp_ts", "tcp_flags_unset", "tcp_flags_set"}
)

# Функции, которых нет в оригинальном Zapret 2: их добавил ZapretGUI.
_OWN_FUNCTIONS: dict[str, tuple[str, str]] = {
    "fakemultisplit": ("Нарезка с подделками в нескольких местах", "Запрос режется в нескольких местах, части уходят вместе с поддельными."),
    "fakemultidisorder": ("Перестановка с подделками в нескольких местах", "Запрос режется в нескольких местах, части уходят в обратном порядке вместе с поддельными."),
    "hostfakesplit_multi": ("Подмена имени сайта, несколько имён", "Как подмена имени сайта, но поддельные имена берутся из списка."),
}

# параметр: (что он значит, ограничение)
_PARAMETERS: dict[str, tuple[str, str]] = {
    "ip_ttl": (
        "Подделке задан короткий путь: она должна дойти до фильтра провайдера, но не до сайта.",
        "Число подобрано под конкретное расстояние до сайта: у другого провайдера может не подойти.",
    ),
    "ip6_ttl": (
        "Подделке задан короткий путь (для IPv6): она должна дойти до фильтра провайдера, но не до сайта.",
        "Число подобрано под конкретное расстояние до сайта: у другого провайдера может не подойти.",
    ),
    "ip_autottl": (
        "Длину пути подделки программа прикидывает сама по ответу сервера.",
        "Это догадка: считается, что путь туда и обратно одинаков. Нужен хотя бы один ответ от сервера.",
    ),
    "ip6_autottl": (
        "Длину пути подделки (для IPv6) программа прикидывает сама по ответу сервера.",
        "Это догадка: считается, что путь туда и обратно одинаков.",
    ),
    "tcp_md5": ("В подделку добавлена подпись, которую сервер сочтёт неверной и отбросит пакет.", ""),
    "badsum": ("У подделки испорчена контрольная сумма: сервер выбросит её как повреждённую.", ""),
    "tcp_seq": ("У подделки сдвинут порядковый номер: сервер сочтёт её не относящейся к соединению.", ""),
    "tcp_ack": ("У подделки сдвинут номер подтверждения: сервер её отбросит.", ""),
    "tcp_ts": (
        "У подделки сдвинута метка времени: сервер сочтёт её устаревшей.",
        "Срабатывает, только если в соединении есть метки времени; иначе подделка уйдёт неиспорченной.",
    ),
    "tcp_flags_unset": ("У подделки снят служебный флаг: сервер её не примет.", ""),
    "tcp_flags_set": ("У подделки выставлен лишний служебный флаг: сервер её не примет.", ""),
    "seqovl": (
        "Части перекрываются: начало закрыто заглушкой, которую сервер отбросит сам — «скрытая подделка» без порчи.",
        "",
    ),
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


def _plural_times(count: int) -> str:
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return f"{count} раза"
    return f"{count} раз"


def _notes(parameters: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    notes: list[str] = []
    cautions: list[str] = []
    for key, value in parameters:
        known = _PARAMETERS.get(key)
        if known is not None:
            notes.append(known[0])
            if known[1]:
                cautions.append(known[1])
        elif key in ("blob", "fake_blob") and value:
            notes.append(f"Поддельные данные: {_BLOBS.get(value, value)}.")
        elif key == "pos" and value:
            notes.append(f"Места разреза: {value}.")
        elif key == "repeats" and value.isdigit() and int(value) > 1:
            notes.append(f"Отправляется {_plural_times(int(value))} подряд.")
        elif key == "tls_mod" and value:
            mods = [
                f"имя сайта {mod[4:]}" if mod.startswith("sni=") else _TLS_MODS.get(mod, mod)
                for mod in value.split(",")
                if mod
            ]
            notes.append("Подделка подправлена: " + ", ".join(mods) + ".")
        elif key in ("host", "hosts") and value:
            notes.append(f"Поддельное имя сайта: {value}.")
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
        if function in _FUNCTIONS:
            title, text, caution = _FUNCTIONS[function]
        elif function in _OWN_FUNCTIONS:
            title, text = _OWN_FUNCTIONS[function]
            caution = "Этой функции нет в оригинальном Zapret 2: её добавил ZapretGUI."
        else:
            title, text, caution = function, "Собственная функция: в оригинальном Zapret 2 её нет, описания для неё нет.", ""
        if function in _SENDS_FAKES and not any(key in _SPOILERS for key, _value in parameters):
            caution = " ".join(part for part in (caution, _UNSPOILED_FAKE_WARNING) if part)
        steps.append(
            StrategyStep(
                function=function,
                title=title,
                text=text,
                caution=" ".join(part for part in (caution, *dict.fromkeys(cautions)) if part),
                notes=tuple(notes),
                line=line,
                scene=_SCENES.get(function, ""),
            )
        )
    return tuple(steps)
