"""Справочник Lua-функций для ``--lua-desync`` и их аргументов.

Функция доступна winws2, только если её файл подключён через ``--lua-init``.
Поэтому справочник разбит по файлам (``files``). Какие файлы подключаются
всегда, задаёт ``WINWS2_LUA_INIT_PATHS`` (обязательный блок пресета).

Источник — поставляемые файлы ``lua/*.lua`` (zapret2 и собственные файлы
программы). Полноту справочника (каждая desync-функция и каждый ключ
``desync.arg.X``) проверяет ``tests/test_winws2_language_lua_coverage.py``.

Стандартные наборы аргументов (``std``) читает общая обвязка ``zapret-lib.lua``:
направление (``dir``), тип данных (``payload``), «обман» DPI (fooling),
отправка (rawsend), IP ID, IP-фрагментация (ipfrag), пересборка (reconstruct).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True, slots=True)
class LuaArgSpec:
    name: str
    summary: str
    kind: str = "text"
    values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LuaFunctionSpec:
    name: str
    files: tuple[str, ...]
    summary: str
    args: tuple[str, ...] = ()
    std: tuple[str, ...] = ()
    ignored_std: tuple[str, ...] = ()
    strict_args: bool = False


# Виды значений аргументов (для проверки и подсказок):
#   blob      — имя фейка (--blob=) или 0xHEX;
#   pos       — маркер позиции или список маркеров через запятую;
#   payload   — список типов данных, ~ в начале — отрицание;
#   flag      — аргумент без значения;
#   int       — целое число;
#   tls_mod   — список модификаций ClientHello;
#   enum      — одно из values;
#   callback  — имя Lua-функции из values.
_POS_VALUES = ("host", "endhost", "sld", "midsld", "endsld", "method", "extlen", "sniext", "abs", "1", "2")
_TLS_MOD_VALUES = ("rnd", "rndsni", "dupsid", "padencap", "sni=", "none")
_DIR_VALUES = ("out", "in", "any")
_COND_CALLBACKS = ("cond_true", "cond_false", "cond_random", "cond_payload_str", "cond_tcp_has_ts", "cond_lua")
_DETECTOR_CALLBACKS_FAIL = (
    "standard_failure_detector",
    "combined_failure_detector",
    "udp_aggressive_failure_detector",
    "white_failure_detector",
)
_DETECTOR_CALLBACKS_SUCCESS = (
    "standard_success_detector",
    "combined_success_detector",
    "udp_protocol_success_detector",
    "white_success_detector",
)
_HOSTKEY_CALLBACKS = ("standard_hostkey", "udp_global_hostkey", "subnet_hostkey")


def _a(name: str, summary: str, kind: str = "text", values: tuple[str, ...] = ()) -> LuaArgSpec:
    return LuaArgSpec(name=name, summary=summary, kind=kind, values=values)


LUA_ARGS: dict[str, LuaArgSpec] = {
    spec.name: spec
    for spec in (
        # --- стандартные: направление и тип данных
        _a("dir", "Направление пакетов: out (по умолчанию), in или any.", "enum", _DIR_VALUES),
        _a("payload", "Типы данных для функции через запятую; ~ в начале — все, кроме перечисленных. "
           "По умолчанию known.", "payload"),
        # --- стандартные: fooling (как сделать фейк невидимым для сервера)
        _a("ip_ttl", "TTL фейка для IPv4 (фейк «умирает» в пути до сервера).", "int"),
        _a("ip6_ttl", "Hop limit фейка для IPv6.", "int"),
        _a("ip_autottl", "Автоматический TTL для IPv4: delta,min-max (например -1,3-20).", "text"),
        _a("ip6_autottl", "Автоматический hop limit для IPv6: delta,min-max.", "text"),
        _a("ip6_hopbyhop", "Добавить IPv6-заголовок hop-by-hop.", "flag"),
        _a("ip6_hopbyhop2", "Добавить второй IPv6-заголовок hop-by-hop.", "flag"),
        _a("ip6_destopt", "Добавить IPv6-заголовок destination options.", "flag"),
        _a("ip6_destopt2", "Добавить второй IPv6-заголовок destination options.", "flag"),
        _a("ip6_routing", "Добавить IPv6-заголовок routing.", "flag"),
        _a("ip6_ah", "Добавить IPv6-заголовок AH.", "flag"),
        _a("tcp_seq", "Сдвинуть TCP sequence фейка на N (плохой seq — сервер отбросит).", "int"),
        _a("tcp_ack", "Сдвинуть TCP acknowledgement фейка на N.", "int"),
        _a("tcp_ts", "Сдвинуть TCP timestamp фейка на N (например -1000).", "int"),
        _a("tcp_md5", "Добавить фейку опцию TCP MD5 (сервер без MD5 его отбросит).", "flag"),
        _a("tcp_flags_set", "Установить TCP-флаги фейку: FIN,SYN,RST,PSH,ACK,URG,ECE,CWR.", "text"),
        _a("tcp_flags_unset", "Снять TCP-флаги у фейка.", "text"),
        _a("tcp_ts_up", "Поднять опцию timestamp в начало TCP-опций.", "flag"),
        _a("tcp_nop_del", "Удалить из TCP-опций пустые NOP.", "flag"),
        _a("fool", "Своя Lua-функция «обмана» для пакета.", "text"),
        # --- стандартные: отправка, пересборка, IP ID
        _a("repeats", "Сколько раз повторить отправку.", "int"),
        _a("ifout", "Интерфейс для отправки.", "text"),
        _a("fwmark", "Метка отправляемых пакетов.", "text"),
        _a("badsum", "Испортить контрольную сумму (сервер отбросит пакет).", "flag"),
        _a("ip_id", "IP ID отправляемых пакетов: seq, rnd, zero или none.", "enum", ("seq", "rnd", "zero", "none")),
        _a("ip_id_conn", "Счётчик IP ID отдельно для каждого соединения.", "flag"),
        # --- стандартные: IP-фрагментация
        _a("ipfrag", "Резать пакет на IP-фрагменты (можно указать свою функцию, по умолчанию ipfrag2).",
           "text", ("ipfrag2",)),
        _a("ipfrag_disorder", "Отправлять IP-фрагменты в обратном порядке.", "flag"),
        _a("ipfrag_pos_tcp", "Где резать TCP-пакет на IP-фрагменты (байт, кратно 8).", "int"),
        _a("ipfrag_pos_udp", "Где резать UDP-пакет на IP-фрагменты (байт, кратно 8).", "int"),
        _a("ipfrag_pos_icmp", "Где резать ICMP-пакет на IP-фрагменты (байт, кратно 8).", "int"),
        _a("ipfrag_pos", "Где резать пакет на IP-фрагменты (байт, кратно 8).", "int"),
        _a("ipfrag_next", "Номер следующего протокола во фрагментах IPv6.", "int"),
        # --- фейки и нарезка
        _a("blob", "Фейк: имя из --blob= или 0xHEX.", "blob"),
        _a("fake_blob", "Фейк для ложных частей: имя из --blob= или 0xHEX.", "blob"),
        _a("fallback", "Запасной фейк, если основного нет.", "blob"),
        _a("optional", "Пропустить, если фейка нет, вместо ошибки.", "flag"),
        _a("tls_mod", "Изменить TLS ClientHello фейка: rnd, rndsni, dupsid, padencap, sni=домен.",
           "tls_mod", _TLS_MOD_VALUES),
        _a("pos", "Где резать: маркеры host, midsld, sniext, число или список через запятую.", "pos", _POS_VALUES),
        _a("pos1", "Первая точка разреза (маркер позиции).", "pos", _POS_VALUES),
        _a("pos2", "Вторая точка разреза (маркер позиции).", "pos", _POS_VALUES),
        _a("seqovl", "Перекрытие sequence: сдвинуть начало части на N байт и заполнить шаблоном.", "text"),
        _a("seqovl_pattern", "Шаблон для заполнения seqovl: имя фейка или 0xHEX.", "blob"),
        _a("pattern", "Шаблон байтов (имя фейка или 0xHEX).", "blob"),
        _a("nodrop", "Не отбрасывать исходный пакет после отправки частей.", "flag"),
        _a("nofake1", "Не отправлять 1-й фейк.", "flag"),
        _a("nofake2", "Не отправлять 2-й фейк.", "flag"),
        _a("nofake3", "Не отправлять 3-й фейк.", "flag"),
        _a("nofake4", "Не отправлять 4-й фейк.", "flag"),
        _a("host", "Имя сервера, которое подставляется в фейк.", "text"),
        _a("midhost", "Дополнительно резать внутри имени сервера (маркер позиции).", "pos", _POS_VALUES),
        _a("disorder_after", "Отправлять части в обратном порядке после этой позиции.", "pos", _POS_VALUES),
        _a("rstack", "Отправлять RST+ACK вместо RST.", "flag"),
        _a("delay", "Задержка перед отправкой, мс.", "int"),
        _a("spell", "Как написать заголовок Host (ровно 4 символа, например hoSt).", "text"),
        _a("mode", "Режим работы функции.", "text"),
        _a("wsize", "Размер TCP-окна.", "int"),
        _a("scale", "Множитель TCP-окна (window scale).", "int"),
        _a("forced_cutoff", "Типы данных, после которых функция перестаёт работать.", "payload"),
        _a("sni_snt", "Имя сервера для клона ClientHello.", "text"),
        _a("sni_snt_new", "Новое имя сервера для клона ClientHello.", "text"),
        _a("sni_del_ext", "Удалить расширение SNI в клоне.", "flag"),
        _a("sni_del", "Удалить имя сервера в клоне.", "flag"),
        _a("sni_first", "Поставить SNI первым расширением.", "flag"),
        _a("sni_last", "Поставить SNI последним расширением.", "flag"),
        _a("char", "Символ для байта OOB.", "text"),
        _a("byte", "Код байта OOB (0..255).", "int"),
        _a("urp", "Указатель срочных данных: b, e или маркер позиции.", "text"),
        _a("min", "Минимальная длина UDP.", "int"),
        _a("max", "Максимальная длина UDP.", "int"),
        _a("increment", "На сколько увеличить длину UDP (по умолчанию 2).", "int"),
        _a("pattern_offset", "Смещение внутри шаблона.", "int"),
        _a("dn", "Номер варианта DHT (по умолчанию 3).", "int"),
        _a("code", "Lua-код для выполнения.", "text"),
        _a("undetected", "Тип данных, если шаблон не найден.", "text"),
        # --- оркестраторы и условия (zapret-auto, combined-detector)
        _a("fails", "Сколько неудач переключают стратегию.", "int"),
        _a("time", "Через сколько секунд сбрасывать счётчик неудач.", "int"),
        _a("unlock_fails", "Сколько неудач снимают закрепление стратегии.", "int"),
        _a("success_detector", "Функция, которая решает, что стратегия сработала.", "callback",
           _DETECTOR_CALLBACKS_SUCCESS),
        _a("failure_detector", "Функция, которая решает, что стратегия не сработала.", "callback",
           _DETECTOR_CALLBACKS_FAIL),
        _a("hostkey", "Функция, которая выбирает ключ сайта для запоминания стратегии.", "callback",
           _HOSTKEY_CALLBACKS),
        _a("key", "Имя общего хранилища стратегий.", "text"),
        _a("strategy", "Номер стратегии внутри circular (1, 2, 3…).", "int"),
        _a("final", "Последняя стратегия circular: на ней перебор останавливается.", "flag"),
        _a("maxseq", "Детектор: смотреть только до этой относительной позиции.", "int"),
        _a("retrans", "Детектор: сколько повторов считать неудачей.", "int"),
        _a("reset", "Детектор: считать RST неудачей.", "flag"),
        _a("inseq", "Детектор: успех, если входящих данных больше этого.", "int"),
        _a("no_http_redirect", "Детектор: не считать HTTP-редирект неудачей.", "flag"),
        _a("no_rst", "Не считать RST неудачей / не отправлять RST.", "flag"),
        _a("udp_out", "Детектор: UDP-неудача — отправлено не меньше стольких пакетов.", "int"),
        _a("udp_in", "Детектор: UDP-неудача — получено не больше стольких пакетов.", "int"),
        _a("reqhost", "Ключ сайта — имя из запроса.", "flag"),
        _a("nld", "Ключ сайта — домен N-го уровня.", "int"),
        _a("mask", "Ключ subnet_hostkey: длина префикса подсети IPv4 (по умолчанию 24).", "int"),
        _a("mask6", "Ключ subnet_hostkey: длина префикса подсети IPv6 (по умолчанию 48).", "int"),
        _a("stall_lo", "Детектор white: зависание, если от сервера пришло не меньше стольких байт.", "int"),
        _a("stall_hi", "Детектор white: зависание, если от сервера пришло не больше стольких байт. 0 — не искать.", "int"),
        _a("stall_time", "Детектор white: сколько секунд тишины перед закрытием считать зависанием.", "int"),
        _a("stall_any", "Детектор white: считать зависанием и оборванный на конце ответа поток.", "flag"),
        _a("stall_out", "Детектор: сколько исходящих без ответа считать зависанием.", "int"),
        _a("udp_fail_out", "Детектор UDP: отправлено не меньше стольких пакетов.", "int"),
        _a("udp_fail_in", "Детектор UDP: получено не больше стольких пакетов.", "int"),
        _a("iff", "Функция-условие.", "callback", _COND_CALLBACKS),
        _a("neg", "Обратить условие.", "flag"),
        _a("instances", "Сколько следующих инстансов затрагивает условие.", "int"),
        _a("cond", "Условие для этого инстанса (для per_instance_condition).", "callback", _COND_CALLBACKS),
        _a("cond_neg", "Обратить условие этого инстанса.", "flag"),
        _a("cond_code", "Lua-код условия для cond_lua.", "text"),
        _a("percent", "Процент срабатывания для cond_random.", "int"),
        _a("stop", "Остановить обработку следующих инстансов.", "flag"),
        _a("clear", "Очистить состояние повторов.", "flag"),
        # --- obfs / pcap
        _a("secret", "Секретный ключ обфускации.", "text"),
        _a("padmin", "Минимальная длина добивки.", "int"),
        _a("padmax", "Максимальная длина добивки.", "int"),
        _a("ippxor", "XOR-маска номера IP-протокола.", "int"),
        _a("dataxor", "XOR-маска данных.", "text"),
        _a("rebuild", "Пересобрать пакет.", "flag"),
        _a("ctype", "Тип ICMP клиента (0..255).", "int"),
        _a("ccode", "Код ICMP клиента (0..255).", "int"),
        _a("stype", "Тип ICMP сервера (0..255).", "int"),
        _a("scode", "Код ICMP сервера (0..255).", "int"),
        _a("server", "Режим сервера (0 — клиент).", "text"),
        _a("ghost", "Ghost-пакет для IPv4.", "text"),
        _a("ghost6", "Ghost-пакет для IPv6.", "text"),
        _a("synack", "Также обрабатывать SYN+ACK.", "flag"),
        _a("magic", "Метка скрытого SYN: x2, urp, opt или tsecr.", "enum", ("x2", "urp", "opt", "tsecr")),
        _a("x2", "Значение поля x2.", "int"),
        _a("kind", "Номер TCP-опции.", "int"),
        _a("opt", "Данные TCP-опции.", "text"),
        _a("xorseq", "XOR-маска sequence.", "text"),
        _a("file", "Файл для записи пакетов (pcap).", "text"),
        _a("keep", "Дописывать в существующий файл.", "flag"),
        # --- собственные функции программы
        _a("fakes", "Сколько фейков отправить.", "int"),
        _a("fake_count", "Сколько фейков отправить.", "int"),
        _a("fake_all", "Отправлять фейк перед каждой частью.", "flag"),
        _a("unsafe_fake", "Разрешить фейки, которые могут дойти до сервера.", "flag"),
        _a("ttl", "TTL фейков.", "int"),
        _a("ttl_start", "Начальный TTL фейков.", "int"),
        _a("ttl_step", "На сколько увеличивать TTL каждого следующего фейка.", "int"),
        _a("ttl_min", "Минимальный TTL.", "int"),
        _a("ttl_max", "Максимальный TTL.", "int"),
        _a("ttl_fallback", "TTL, если автоматический не определился (по умолчанию 8).", "int"),
        _a("split_host", "Дополнительно резать внутри имени сервера.", "flag"),
        _a("split_sni", "Дополнительно резать внутри SNI.", "flag"),
        _a("disorder", "Отправлять части в обратном порядке.", "flag"),
        _a("fake_sni", "Имя сервера в фейках.", "text"),
        _a("sni", "Имя сервера в фейках.", "text"),
        _a("badseq", "Портить sequence у фейков.", "flag"),
        _a("md5sig", "Добавлять фейкам опцию TCP MD5.", "flag"),
        _a("fake_host", "Ложное имя сервера в HTTP.", "text"),
        _a("add_x_host", "Добавить заголовок X-Host с настоящим именем.", "flag"),
        _a("frag_size", "Размер первого IP-фрагмента.", "int"),
        _a("case", "Регистр заголовка Host: lower, upper, mixed, space.", "enum", ("lower", "upper", "mixed", "space")),
        _a("amount", "Сколько байтов мусора добавить.", "int"),
        _a("method", "Как исказить HTTP-метод: lowercase, padding, fake, case.", "enum",
           ("lowercase", "padding", "fake", "case")),
        _a("max_parts", "Максимум частей имени сервера.", "int"),
        _a("version", "Версия HTTP: 1.0 или 0.9.", "enum", ("1.0", "0.9")),
        _a("count", "Сколько повторов/пакетов.", "int"),
        _a("prefix", "Добавить префикс перед запросом.", "flag"),
        _a("hostcase", "Менять регистр Host.", "flag"),
        _a("split", "Дополнительно резать пакет.", "flag"),
        _a("offset_sec", "Сдвиг времени в секундах.", "int"),
        _a("urgent_pos", "Позиция срочных данных.", "pos", _POS_VALUES),
        _a("urgent_byte", "Байт срочных данных.", "int"),
        _a("tls_rnd", "Случайные поля в TLS-фейке.", "flag"),
        _a("tls_dupsid", "Копировать session id в TLS-фейк.", "flag"),
        _a("shift", "Добавить N к sequence RST.", "int"),
        _a("ack", "Отправлять RST+ACK.", "flag"),
        _a("rst_repeats", "Сколько ложных RST отправить (0 — без RST).", "int"),
        _a("decoy_repeats", "Сколько копий приманки отправить.", "int"),
        _a("tlsrec_pos", "Где делить TLS-запись (маркер позиции).", "pos", _POS_VALUES),
        _a("min_sni", "Минимальная длина SNI.", "int"),
        _a("order", "Порядок отправки частей.", "text"),
        _a("hosts", "Список имён серверов через запятую.", "text"),
        _a("parts", "На сколько частей резать.", "int"),
        _a("decoys", "Сколько приманок.", "int"),
        _a("decoy_size", "Размер приманки.", "int"),
        _a("white_blob", "«Белый» фейк: имя из --blob= или 0xHEX.", "blob"),
        _a("before", "Сколько фейков до настоящих данных.", "int"),
        _a("after", "Сколько фейков после настоящих данных.", "int"),
        _a("ovl_size", "Размер перекрытия.", "int"),
        _a("no_pollution", "Пропустить первую фазу.", "flag"),
        _a("flood_count", "Сколько пакетов в потоке (по умолчанию 30).", "int"),
        _a("src_range", "Диапазон адресов источника: rfc1918 или cgn.", "enum", ("rfc1918", "cgn")),
        _a("rst_interval", "Интервал RST в секундах (по умолчанию 2).", "int"),
        _a("rst_bytes", "Размер RST-пакетов.", "int"),
        _a("fin_mode", "Чередовать RST и FIN.", "flag"),
    )
}

_STD_SETS: dict[str, tuple[str, ...]] = {
    "dir": ("dir",),
    "payload": ("payload",),
    "fooling": (
        "ip_ttl", "ip6_ttl", "ip_autottl", "ip6_autottl",
        "ip6_hopbyhop", "ip6_hopbyhop2", "ip6_destopt", "ip6_destopt2", "ip6_routing", "ip6_ah",
        "tcp_seq", "tcp_ack", "tcp_ts", "tcp_md5", "tcp_flags_set", "tcp_flags_unset",
        "tcp_ts_up", "tcp_nop_del", "fool",
    ),
    "rawsend": ("repeats", "ifout", "fwmark"),
    "reconstruct": ("badsum",),
    "ip_id": ("ip_id", "ip_id_conn"),
    "ipfrag": (
        "ipfrag", "ipfrag_disorder", "ipfrag_pos_tcp", "ipfrag_pos_udp",
        "ipfrag_pos_icmp", "ipfrag_pos", "ipfrag_next",
    ),
}
STD_SET_TITLES: dict[str, str] = {
    "dir": "направление",
    "payload": "тип данных",
    "fooling": "обман DPI",
    "rawsend": "отправка",
    "reconstruct": "пересборка",
    "ip_id": "IP ID",
    "ipfrag": "IP-фрагментация",
}

# Метки, которые читают оркестраторы (circular, per_instance_condition) у
# ЧУЖИХ инстансов: у любой функции это не ошибка.
ORCHESTRATOR_LABELS: frozenset[str] = frozenset({"strategy", "final", "cond", "cond_neg"})

_ALL = ("dir", "payload", "fooling", "ip_id", "rawsend", "reconstruct", "ipfrag")
_NOFRAG = ("dir", "payload", "fooling", "ip_id", "rawsend", "reconstruct")
_CUSTOM = _NOFRAG

_LIB = ("zapret-lib.lua",)
_ANTIDPI = ("zapret-antidpi.lua",)
_AUTO = ("zapret-auto.lua",)
_OBFS = ("zapret-obfs.lua",)
_CUSTOM_FILE = ("custom_funcs.lua",)
_MULTISHAKE = ("zapret-multishake.lua",)
_16KB = ("zapret-16kb.lua",)
_SPLIT_ARGS = ("pos", "seqovl", "seqovl_pattern", "blob", "optional", "nodrop")
_16KB_ARGS = ("blob", "white_blob", "tls_mod")


def _f(
    name: str,
    files: tuple[str, ...],
    summary: str,
    args: tuple[str, ...] = (),
    std: tuple[str, ...] = (),
    *,
    ignored_std: tuple[str, ...] = (),
    strict: bool = False,
) -> LuaFunctionSpec:
    return LuaFunctionSpec(
        name=name,
        files=files,
        summary=summary,
        args=args,
        std=std,
        ignored_std=ignored_std,
        strict_args=strict,
    )


LUA_FUNCTIONS: tuple[LuaFunctionSpec, ...] = (
    # ------------------------------------------------------- zapret-lib.lua
    _f("luaexec", _LIB, "Выполнить Lua-код из аргумента code.", ("code",), strict=True),
    _f("pass", _LIB, "Ничего не делать: пропустить пакет как есть.", strict=True),
    _f("pktdebug", _LIB, "Записать в журнал отладки содержимое пакета.", strict=True),
    _f("argdebug", _LIB, "Записать в журнал отладки аргументы инстанса.", strict=True),
    _f("posdebug", _LIB, "Записать в журнал отладки позиции маркеров.", strict=True),
    _f("detect_payload_str", _LIB, "Пометить данные типом payload, если в них есть строка pattern.",
       ("pattern", "payload", "undetected"), strict=True),
    _f("orchestrate", _LIB, "Служебная: вызвать следующие инстансы как единый план.", strict=True),
    _f("desync_orchestrator_example", _LIB, "Пример оркестратора из zapret2.", strict=True),
    # ---------------------------------------------------- zapret-antidpi.lua
    _f("drop", _ANTIDPI, "Отбросить пакет.", (), ("dir", "payload"), strict=True),
    _f("send", _ANTIDPI, "Отправить копию пакета с изменениями.", ("delay",),
       ("dir", "fooling", "ip_id", "ipfrag", "rawsend", "reconstruct"), strict=True),
    _f("pktmod", _ANTIDPI, "Изменить сам пакет (fooling, IP ID) без отправки копий.", (),
       ("dir", "fooling", "ip_id"), strict=True),
    _f("http_domcase", _ANTIDPI, "Менять регистр букв в имени сервера HTTP.", (), ("dir", "payload"), strict=True),
    _f("http_hostcase", _ANTIDPI, "Изменить написание заголовка Host.", ("spell",), ("dir", "payload"), strict=True),
    _f("http_methodeol", _ANTIDPI, "Добавить перевод строки перед HTTP-методом.", (), ("dir", "payload"), strict=True),
    _f("http_unixeol", _ANTIDPI, "Заменить \\r\\n на \\n в HTTP-заголовках.", (), ("dir", "payload"), strict=True),
    _f("synack_split", _ANTIDPI, "Разделить SYN+ACK: mode=syn, synack или acksyn.", ("mode",),
       ("rawsend", "reconstruct", "ipfrag"), strict=True),
    _f("synack", _ANTIDPI, "Ответить на SYN пакетом SYN+ACK.", (), ("rawsend", "reconstruct", "ipfrag"), strict=True),
    _f("wsize", _ANTIDPI, "Изменить размер TCP-окна в SYN+ACK.", ("wsize", "scale"), strict=True),
    _f("wssize", _ANTIDPI, "Изменить размер TCP-окна в исходящих пакетах до первых данных.",
       ("wsize", "scale", "forced_cutoff"), ("dir",), strict=True),
    _f("tls_client_hello_clone", _ANTIDPI, "Сохранить клон TLS ClientHello в фейк blob.",
       ("blob", "fallback", "sni_snt", "sni_snt_new", "sni_del_ext", "sni_del", "sni_first", "sni_last"),
       strict=True),
    _f("syndata", _ANTIDPI, "Отправить данные прямо в SYN-пакете.", ("blob", "tls_mod"),
       ("fooling", "rawsend", "reconstruct", "ipfrag"), strict=True),
    _f("rst", _ANTIDPI, "Отправить ложный RST.", ("rstack",), _ALL, strict=True),
    _f("fake", _ANTIDPI, "Отправить фейк перед настоящими данными.", ("blob", "optional", "tls_mod"), _ALL,
       strict=True),
    _f("multisplit", _ANTIDPI, "Разрезать данные на части по позициям pos и отправить по порядку.",
       _SPLIT_ARGS, _ALL, strict=True),
    _f("multidisorder", _ANTIDPI, "Разрезать данные по pos и отправить части в обратном порядке.",
       _SPLIT_ARGS, _ALL, strict=True),
    _f("multidisorder_legacy", _ANTIDPI, "Старый вариант multidisorder как в Zapret 1.",
       ("pos", "seqovl", "seqovl_pattern", "optional"), _ALL, strict=True),
    _f("hostfakesplit", _ANTIDPI, "Разрезать вокруг имени сервера и вставить фейковое имя (host).",
       ("host", "midhost", "nofake1", "nofake2", "disorder_after", "blob", "optional", "nodrop"),
       _NOFRAG, ignored_std=("ipfrag",), strict=True),
    _f("fakedsplit", _ANTIDPI, "Разрезать по pos и окружить части фейками.",
       ("pos", "nofake1", "nofake2", "nofake3", "nofake4", "pattern", "seqovl", "seqovl_pattern",
        "blob", "optional", "nodrop"),
       _NOFRAG, ignored_std=("ipfrag",), strict=True),
    _f("fakeddisorder", _ANTIDPI, "Как fakedsplit, но части идут в обратном порядке.",
       ("pos", "nofake1", "nofake2", "nofake3", "nofake4", "pattern", "seqovl", "seqovl_pattern",
        "blob", "optional", "nodrop"),
       _NOFRAG, ignored_std=("ipfrag",), strict=True),
    _f("tcpseg", _ANTIDPI, "Отправить отрезок данных между двумя позициями pos.",
       ("pos", "seqovl", "seqovl_pattern", "blob", "optional"), _ALL, strict=True),
    _f("oob", _ANTIDPI, "Вставить срочный байт TCP (OOB).", ("char", "byte", "urp"),
       ("fooling", "ip_id", "rawsend", "reconstruct", "ipfrag"), strict=True),
    _f("udplen", _ANTIDPI, "Изменить длину UDP-пакета добивкой.",
       ("min", "max", "increment", "pattern", "pattern_offset"), ("dir", "payload"), strict=True),
    _f("dht_dn", _ANTIDPI, "Исказить DHT-пакет.", ("dn",), ("dir",), strict=True),
    # ------------------------------------------------------- zapret-auto.lua
    _f("circular", _AUTO, "Перебирать стратегии (метки strategy=N) и переключаться при неудаче.",
       ("fails", "time", "success_detector", "failure_detector", "hostkey", "key",
        "maxseq", "retrans", "reset", "inseq", "no_http_redirect", "no_rst", "udp_out", "udp_in",
        "reqhost", "nld", "mask", "mask6", "stall_lo", "stall_hi", "stall_time", "stall_any")),
    _f("condition", _AUTO, "Выполнить следующие инстансы, только если выполнено условие iff.",
       ("iff", "neg", "instances", "pattern", "percent", "cond_code"), strict=True),
    _f("per_instance_condition", _AUTO, "Проверять условие cond у каждого следующего инстанса.",
       ("instances",), strict=True),
    _f("stopif", _AUTO, "Остановить обработку следующих инстансов, если выполнено условие iff.",
       ("iff", "neg", "pattern", "percent", "cond_code"), strict=True),
    _f("repeater", _AUTO, "Повторить следующие инстансы repeats раз.",
       ("repeats", "instances", "iff", "neg", "stop", "clear", "pattern", "percent", "cond_code"), strict=True),
    # ------------------------------------------------------ zapret-obfs.lua
    _f("wgobfs", ("zapret-obfs.lua", "zapret-wgobfs.lua"), "Обфускация WireGuard.",
       ("secret", "padmin", "padmax")),
    _f("ippxor", _OBFS, "XOR номера IP-протокола и данных.", ("ippxor", "dataxor", "rebuild")),
    _f("udp2icmp", _OBFS, "Завернуть UDP в ICMP.", ("ctype", "ccode", "stype", "scode", "dataxor", "server")),
    _f("synhide", _OBFS, "Спрятать SYN-пакет.", ("ghost", "ghost6", "synack", "magic", "x2", "kind", "opt", "xorseq")),
    _f("pcap", ("zapret-pcap.lua",), "Записывать пакеты в файл pcap.", ("file", "keep")),
    # ------------------------------------------------------ custom_funcs.lua
    _f("http_aggressive", _CUSTOM_FILE, "HTTP: серия фейков с растущим TTL и нарезка.",
       ("fakes", "ttl_start", "ttl_step", "split_host", "disorder", "tcp_ts_up"), _CUSTOM),
    _f("http_syndata", _CUSTOM_FILE, "HTTP-запрос прямо в SYN-пакете.", ("blob",),
       ("fooling", "rawsend", "reconstruct", "ipfrag")),
    _f("http_multidisorder", _CUSTOM_FILE, "HTTP: много разрезов в обратном порядке.", ("tcp_ts_up",), _CUSTOM),
    _f("http_methodeol_v2", _CUSTOM_FILE, "Улучшенный methodeol: больше мусора в начале.", (), ("dir", "payload")),
    _f("http_methodeol_hostcase", _CUSTOM_FILE, "methodeol и изменение регистра Host.", (), ("dir", "payload")),
    _f("tls_aggressive", _CUSTOM_FILE, "TLS: серия фейков с растущим TTL и нарезка.",
       ("fakes", "ttl_start", "ttl_step", "fake_sni", "blob", "tls_mod", "badseq", "seqovl",
        "seqovl_pattern", "tcp_ts_up", "split_sni", "md5sig"), _CUSTOM),
    _f("tls_multisplit_sni", _CUSTOM_FILE, "TLS: много мелких разрезов вокруг SNI.", ("seqovl", "seqovl_pattern"),
       _CUSTOM),
    _f("tls_fake_flood", _CUSTOM_FILE, "TLS: много фейков, затем multidisorder.",
       ("fakes", "blob", "badseq", "md5sig", "pos", "tcp_ts_up"), _CUSTOM),
    _f("tls_fake_simple", _CUSTOM_FILE, "TLS: только фейки перед данными, без нарезки.",
       ("fakes", "ttl", "sni", "blob"), _CUSTOM),
    _f("tls_split_gentle", _CUSTOM_FILE, "TLS: мягкий разрез на 2 части.", ("pos", "tcp_ts_up"), _CUSTOM),
    _f("tls_fake_split", _CUSTOM_FILE, "TLS: фейки и один разрез.", ("fakes", "ttl", "pos", "blob", "tcp_ts_up"),
       _CUSTOM),
    _f("tls_disorder_gentle", _CUSTOM_FILE, "TLS: 3 части в обратном порядке.", ("pos1", "pos2", "tcp_ts_up"),
       _CUSTOM),
    _f("http_seqovl_host", _CUSTOM_FILE, "HTTP: фейковый Host с перекрытием sequence.", ("fake_host",), _CUSTOM),
    _f("http_ipfrag", _CUSTOM_FILE, "HTTP: IP-фрагментация.", ("frag_size",), _CUSTOM),
    _f("http_hostmod", _CUSTOM_FILE, "HTTP: регистр заголовка Host.", ("case",), ("dir", "payload")),
    _f("http_absolute_url", _CUSTOM_FILE, "HTTP: абсолютный URL в запросе.", ("fake_host",), ("dir", "payload")),
    _f("http_triple_seqovl", _CUSTOM_FILE, "HTTP: три варианта Host с одним sequence.", ("fake_host",), _CUSTOM),
    _f("http_mgts_combo", _CUSTOM_FILE, "HTTP: disorder и seqovl для заголовка Host.", ("fake_host",), _CUSTOM),
    _f("tls_fake_disorder_gentle", _CUSTOM_FILE, "TLS: фейки и мягкий disorder.",
       ("fakes", "ttl", "blob", "tcp_ts_up"), _CUSTOM),
    _f("multisplit_tls", _CUSTOM_FILE, "TLS: multisplit с изменённым фейком.",
       ("pos", "seqovl", "sni", "tls_rnd", "tls_dupsid", "fallback", "nodrop"), _CUSTOM),
    _f("http_garbage_prefix", _CUSTOM_FILE, "HTTP: мусор перед запросом.", ("mode", "amount"), ("dir", "payload")),
    _f("http_pipeline_fake", _CUSTOM_FILE, "HTTP: два запроса, первый фейковый.", ("fake_host",), _CUSTOM),
    _f("http_header_shuffle", _CUSTOM_FILE, "HTTP: фейковый Host перед настоящим.", ("fake_host", "add_x_host"),
       ("dir", "payload")),
    _f("http_method_obfuscate", _CUSTOM_FILE, "HTTP: искажение метода.", ("method",), ("dir", "payload")),
    _f("http_absolute_uri_v2", _CUSTOM_FILE, "HTTP: абсолютный URI и фейковый Host.", ("fake_host",),
       ("dir", "payload")),
    _f("http_host_bytesplit", _CUSTOM_FILE, "HTTP: побайтовый разрез имени сервера.", ("max_parts",), _CUSTOM),
    _f("http_fake_continuation", _CUSTOM_FILE, "HTTP: фейковое продолжение соединения.", ("fake_host",), _CUSTOM),
    _f("http_version_downgrade", _CUSTOM_FILE, "HTTP: понизить версию протокола.", ("version",), ("dir", "payload")),
    _f("http_pipeline_fake_v2", _CUSTOM_FILE, "HTTP: фейковый запрос с badsum или низким TTL.",
       ("fake_host", "ttl", "badsum"), _CUSTOM),
    _f("http_fake_xhost", _CUSTOM_FILE, "HTTP: фейковый заголовок X-Host.", ("fake_host",), ("dir", "payload")),
    _f("http_oob_prefix", _CUSTOM_FILE, "HTTP: срочный байт TCP перед запросом.", (), ("dir", "payload")),
    _f("http_inject_safe_header", _CUSTOM_FILE, "HTTP: пустой X-заголовок внутри заголовков.", (), ("dir", "payload")),
    _f("http_methodeol_safe", _CUSTOM_FILE, "HTTP: \\r\\n перед методом.", (), ("dir", "payload")),
    _f("http_space_prefix", _CUSTOM_FILE, "HTTP: пробел перед методом.", (), ("dir", "payload")),
    _f("http_lf_prefix", _CUSTOM_FILE, "HTTP: \\n вместо \\r\\n.", (), ("dir", "payload")),
    _f("http_tab_prefix", _CUSTOM_FILE, "HTTP: табуляция перед методом.", (), ("dir", "payload")),
    _f("http_xpadding", _CUSTOM_FILE, "HTTP: заголовок X-Padding.", (), ("dir", "payload")),
    _f("http_multi_crlf", _CUSTOM_FILE, "HTTP: несколько \\r\\n подряд.", ("count",), ("dir", "payload")),
    _f("http_mixed_prefix", _CUSTOM_FILE, "HTTP: \\r\\n и пробелы перед методом.", (), ("dir", "payload")),
    _f("http_combo_bypass", _CUSTOM_FILE, "HTTP: комбинированный обход.", ("fake_host", "repeats", "prefix", "hostcase"),
       _CUSTOM),
    _f("http_simple_bypass", _CUSTOM_FILE, "HTTP: \\r\\n и регистр Host без нарезки.", ("prefix", "hostcase"),
       ("dir", "payload")),
    _f("discord_window_collapse", _CUSTOM_FILE, "Discord: схлопывание TCP-окна.", ("pos",), ("dir", "payload")),
    _f("discord_router_alert", _CUSTOM_FILE, "Discord: опция IP Router Alert.", ("split", "pos"), ("dir", "payload")),
    _f("discord_ecn_exploit", _CUSTOM_FILE, "Discord: флаги ECN.", ("split", "pos", "disorder"), ("dir", "payload")),
    _f("discord_timestamp_travel", _CUSTOM_FILE, "Discord: сдвиг TCP timestamp.", ("offset_sec", "split", "pos"),
       ("dir", "payload")),
    _f("discord_urgent_sni", _CUSTOM_FILE, "Discord: срочный байт в районе SNI.", ("urgent_pos", "urgent_byte"),
       ("dir", "payload")),
    _f("discord_ultimate_combo", _CUSTOM_FILE, "Discord: комбинированный обход.", ("pos", "pos2", "fakes", "ttl"),
       ("dir", "payload")),
    _f("multisplitdisorder", _CUSTOM_FILE, "Нарезка: mode=interlace, random или disorder.",
       ("pos", "mode", "seqovl", "seqovl_pattern", "blob", "optional", "nodrop"), _CUSTOM),
    _f("decoy_hello", _CUSTOM_FILE, "Отправить ClientHello-приманку с другим SNI.",
       ("blob", "optional", "tls_mod", "repeats"), _ALL),
    _f("white_seqovl", _CUSTOM_FILE, "Белый ClientHello в перекрытии (seqovl) перед настоящим, дальше multisplit.",
       ("sni", "blob", "pos", "seqovl", "seqovl_pattern", "optional", "nodrop"), _ALL),
    _f("tlsrec", _CUSTOM_FILE, "Разделить одну TLS-запись на две.", ("pos",), ("dir", "payload")),
    _f("rst_desync", _CUSTOM_FILE, "Ложные RST с низким TTL.", ("repeats", "shift", "ack"), _ALL),
    _f("desync_combo", _CUSTOM_FILE, "RST, приманки, деление TLS-записи и disorder вместе.",
       ("rst_repeats", "decoy_repeats", "blob", "optional", "tls_mod", "tlsrec_pos", "pos", "nodrop"), _ALL),
    # ------------------------------------------------------ custom_diag.lua
    _f("diag_once", ("custom_diag.lua",), "Записать в журнал первый пакет каждого соединения."),
    _f("diag_always", ("custom_diag.lua",), "Записать в журнал каждый пакет (очень много строк)."),
    # ----------------------------------------------- zapret-multishake.lua
    _f("hostfakesplit_stealth", _MULTISHAKE, "hostfakesplit с режимами soft, blend, minimal, random.",
       ("mode", "min_sni", "host", "midhost", "nofake1", "nofake2", "blob", "optional", "tcp_ts_up", "nodrop"),
       _CUSTOM),
    _f("hostfakesplit_chaos", _MULTISHAKE, "hostfakesplit со случайным порядком частей.",
       ("host", "order", "nofake1", "nofake2", "blob", "optional", "tcp_ts_up", "nodrop"), _CUSTOM),
    _f("hostfakesplit_multi", _MULTISHAKE, "hostfakesplit с несколькими фейковыми именами (hosts).",
       ("hosts", "midhost", "blob", "tcp_ts_up", "nodrop"), _CUSTOM),
    _f("hostfakesplit_gradual", _MULTISHAKE, "hostfakesplit с постепенной нарезкой имени.",
       ("host", "parts", "blob", "tcp_ts_up", "nodrop"), _CUSTOM),
    _f("hostfakesplit_decoy", _MULTISHAKE, "hostfakesplit с приманками.",
       ("host", "decoys", "decoy_size", "midhost", "nofake1", "nofake2", "blob", "tcp_ts_up", "nodrop"), _CUSTOM),
    _f("snifakesplit", _MULTISHAKE, "То же, что hostfakesplit.",
       ("host", "midhost", "nofake1", "nofake2", "disorder_after", "blob", "optional", "nodrop"), _NOFRAG),
    _f("hostfakesplit_soft", _MULTISHAKE, "hostfakesplit_stealth в режиме soft.",
       ("mode", "min_sni", "host", "midhost", "nofake1", "nofake2", "blob", "optional", "tcp_ts_up", "nodrop"),
       _CUSTOM),
    _f("hostfakesplit_blend", _MULTISHAKE, "hostfakesplit_stealth в режиме blend.",
       ("mode", "min_sni", "host", "midhost", "nofake1", "nofake2", "blob", "optional", "tcp_ts_up", "nodrop"),
       _CUSTOM),
    # ------------------------------------------------ fakemultisplit / disorder
    _f("fakemultisplit", ("fakemultisplit.lua",), "multisplit с фейком перед каждой частью.",
       ("fake_blob", "pos", "pattern", "seqovl", "seqovl_pattern", "blob", "optional", "tls_mod",
        "tcp_ts_up", "nodrop"), _CUSTOM),
    _f("fakemultidisorder", ("fakemultidisorder.lua",), "multidisorder с фейком перед каждой частью.",
       ("fake_blob", "pos", "pattern", "seqovl", "seqovl_pattern", "blob", "optional", "tls_mod",
        "tcp_ts_up", "nodrop", "fake_count", "fake_all", "unsafe_fake"), _CUSTOM),
    # ------------------------------------------------------- zapret-16kb.lua
    _f("flood_white", _16KB, "Поток «белых» фейков перед данными.", ("count", "pos") + _16KB_ARGS, _CUSTOM),
    _f("ttl_ladder", _16KB, "Фейки с TTL от ttl_min до ttl_max.", ("ttl_min", "ttl_max", "pos") + _16KB_ARGS, _CUSTOM),
    _f("white_sandwich", _16KB, "Настоящие данные между «белыми» фейками.", ("before", "after", "pos") + _16KB_ARGS,
       _CUSTOM),
    _f("seqovl_white", _16KB, "Перекрытие sequence «белыми» данными.", ("ovl_size", "pos") + _16KB_ARGS, _CUSTOM),
    # ---------------------------------------------------- zapret-rst-flood.lua
    _f("rst_flood", ("zapret-rst-flood.lua",), "Поток ложных RST с подменой источника.",
       ("no_pollution", "flood_count", "src_range", "no_rst", "rst_interval", "rst_bytes", "fin_mode",
        "ttl_fallback"), ("dir", "fooling", "rawsend", "reconstruct")),
    # ---------------------------------------------- combined-detector / stats
    _f("circular_quality", ("combined-detector.lua",), "circular с учётом качества и закреплением стратегии.",
       ("strategy", "hostkey", "failure_detector", "success_detector", "key", "unlock_fails", "fails", "time",
        "stall_out", "udp_fail_out", "udp_fail_in", "nld")),
    _f("circular_with_preload", ("strategy-stats.lua",), "circular с загрузкой сохранённой стратегии.", ("key",)),
)

LUA_FUNCTIONS_BY_NAME: dict[str, LuaFunctionSpec] = {spec.name: spec for spec in LUA_FUNCTIONS}

# Функции, которые пишут в аргументы значения-callback (iff=, hostkey= …).
LUA_CALLBACK_NAMES: frozenset[str] = frozenset(
    _COND_CALLBACKS + _DETECTOR_CALLBACKS_FAIL + _DETECTOR_CALLBACKS_SUCCESS + _HOSTKEY_CALLBACKS + ("ipfrag2",)
)

# Файлы, про которые справочник знает всё. Если пресет подключает другой
# lua-файл (или lua-код строкой), неизвестные функции не считаются ошибкой.
KNOWN_LUA_FILES: frozenset[str] = frozenset(
    name for spec in LUA_FUNCTIONS for name in spec.files
) | frozenset(
    {
        "zapret-tests.lua",
        "domain-grouping.lua",
        "silent-drop-detector.lua",
        "strategy-lock-manager.lua",
        "strategies.lua",
    }
)


def std_arg_names(std_sets) -> tuple[str, ...]:
    names: list[str] = []
    for set_name in std_sets:
        names.extend(_STD_SETS.get(set_name, ()))
    return tuple(names)


@lru_cache(maxsize=None)
def function_arg_names(spec: LuaFunctionSpec) -> tuple[str, ...]:
    """Все аргументы функции: собственные, затем стандартные."""
    seen: set[str] = set()
    names: list[str] = []
    for name in tuple(spec.args) + std_arg_names(spec.std):
        if name not in seen:
            seen.add(name)
            names.append(name)
    return tuple(names)


@lru_cache(maxsize=None)
def ignored_arg_names(spec: LuaFunctionSpec) -> frozenset[str]:
    return frozenset(std_arg_names(spec.ignored_std)) - frozenset(spec.args)


def std_set_of(arg_name: str) -> str:
    for set_name, names in _STD_SETS.items():
        if arg_name in names:
            return set_name
    return ""


ALL_KNOWN_ARG_NAMES: frozenset[str] = frozenset(LUA_ARGS)


def closest_lua_function_names(name: str, *, limit: int = 3) -> list[str]:
    """Похожие имена функций для подсказки «возможно, имелось в виду…»."""
    return difflib.get_close_matches(str(name or ""), list(LUA_FUNCTIONS_BY_NAME), n=limit, cutoff=0.6)


__all__ = [
    "ALL_KNOWN_ARG_NAMES",
    "KNOWN_LUA_FILES",
    "LUA_ARGS",
    "LUA_CALLBACK_NAMES",
    "LUA_FUNCTIONS",
    "LUA_FUNCTIONS_BY_NAME",
    "LuaArgSpec",
    "LuaFunctionSpec",
    "ORCHESTRATOR_LABELS",
    "STD_SET_TITLES",
    "closest_lua_function_names",
    "function_arg_names",
    "ignored_arg_names",
    "std_arg_names",
    "std_set_of",
]
