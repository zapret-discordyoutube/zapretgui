"""Справочник всех опций командной строки winws2 (Windows-сборка nfqws2).

Источник — массив ``long_options[]`` и разбор опций в
``zapret2/nfq2/nfqws.c``. Там, где код расходится с документацией, записано
поведение кода (например, ``--cookie`` требует значения, ``--wf-tcp-empty``
принимает только 0|1). Полноту справочника проверяет
``tests/test_winws2_language_option_coverage.py``.

Поля:

- ``arg`` — как опция принимает значение: ``none`` (без значения),
  ``required`` (обязательно), ``optional`` (только через ``=``);
- ``scope`` — ``global`` (действует на весь запуск), ``profile`` (относится к
  текущему профилю), ``any`` (не важно где);
- ``platform`` — ``all``/``windows`` есть в winws2; ``linux``, ``bsd``,
  ``unix`` — только в других сборках, winws2 такую опцию не знает;
- ``value`` — вид значения для проверки и подсказок (см. ``values.py``);
- ``repeat`` — что будет при повторе: ``accumulate`` (копится),
  ``last`` (последняя перезаписывает), ``once`` (повтор — ошибка/бессмыслен).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OptionSpec:
    name: str
    arg: str
    scope: str
    value: str
    summary: str
    platform: str = "all"
    repeat: str = "last"
    int_min: int | None = None
    int_max: int | None = None
    values: tuple[str, ...] = ()
    placeholder: str = ""

    @property
    def flag(self) -> str:
        return f"--{self.name}"

    @property
    def available_on_windows(self) -> bool:
        return self.platform in {"all", "windows"}


_BOOL01 = ("0", "1")


def _o(name: str, arg: str, scope: str, value: str, summary: str, **kwargs) -> OptionSpec:
    return OptionSpec(name=name, arg=arg, scope=scope, value=value, summary=summary, **kwargs)


WINWS2_OPTIONS: tuple[OptionSpec, ...] = (
    # ---------------------------------------------------------------- общие
    _o("debug", "optional", "global", "debug",
       "Журнал отладки: 1 — в консоль, 0 — выключить, @файл — в файл, syslog.",
       values=("0", "1", "@logs/winws2_debug.log", "syslog"), placeholder="@logs/winws2_debug.log"),
    _o("dry-run", "none", "global", "none",
       "Только проверить параметры и выйти (код 0, если всё верно)."),
    _o("intercept", "optional", "global", "bool01",
       "Перехват трафика: 0 — только выполнить --lua-init и выйти.", values=_BOOL01),
    _o("fuzz", "required", "global", "int",
       "Скрытая отладочная опция разработчика; перехват при ней выключается.", int_min=0),
    _o("version", "none", "global", "none",
       "Показать версию и сразу выйти — остальные опции не выполняются."),
    _o("comment", "optional", "any", "text",
       "Комментарий, winws2 его игнорирует.", repeat="accumulate"),
    _o("qnum", "required", "global", "int", "Номер очереди NFQUEUE.", platform="linux"),
    _o("bind-fix4", "none", "global", "none", "Исправление выбора интерфейса для IPv4.", platform="linux"),
    _o("bind-fix6", "none", "global", "none", "Исправление выбора интерфейса для IPv6.", platform="linux"),
    _o("port", "required", "global", "int", "Порт divert-сокета.", platform="bsd"),
    _o("daemon", "none", "global", "none", "Уйти в фон (демон)."),
    _o("chdir", "optional", "global", "path",
       "Сменить рабочую папку; без значения — папка exe. Влияет на все следующие относительные пути."),
    _o("pidfile", "required", "global", "file", "Записать номер процесса в файл."),
    _o("user", "required", "global", "text", "Сбросить права до пользователя.", platform="unix"),
    _o("uid", "required", "global", "text", "Сбросить права до uid[:gid,...].", platform="unix"),
    _o("ctrack-timeouts", "required", "global", "ctrack_timeouts",
       "Таймауты отслеживания соединений SYN:ESTABLISHED:FIN[:UDP] в секундах (по умолчанию 60:300:60:60).",
       placeholder="60:300:60:60"),
    _o("ctrack-disable", "optional", "global", "bool01",
       "Выключить отслеживание соединений (1 или без значения).", values=_BOOL01),
    _o("payload-disable", "optional", "global", "payload_list",
       "Не распознавать эти типы данных; без значения — не распознавать ничего."),
    _o("server", "optional", "global", "bool01",
       "Режим сервера: входящие соединения обрабатываются как исходящие.", values=_BOOL01),
    _o("ipcache-lifetime", "required", "global", "int",
       "Сколько секунд помнить имя сервера и число хопов для IP (0 — вечно, по умолчанию 7200).",
       int_min=0, values=("7200", "84600", "0")),
    _o("ipcache-hostname", "optional", "global", "bool01",
       "Запоминать имя сервера по IP, чтобы hostlist срабатывал и без SNI (1 или без значения).",
       values=_BOOL01),
    _o("reasm-disable", "optional", "global", "payload_list",
       "Не собирать из частей эти типы данных (tls_client_hello, quic_initial); без значения — ничего."),
    _o("fwmark", "required", "global", "text", "Метка fwmark для своих пакетов.", platform="linux"),
    _o("sockarg", "required", "global", "text", "SO_USER_COOKIE для своих пакетов.", platform="bsd"),
    _o("writable", "optional", "global", "path",
       "Создать папку, доступную Lua-скриптам на запись (переменная WRITABLE)."),
    _o("blob", "required", "global", "blob",
       "Объявить фейк (набор байтов): имя:@файл, имя:+смещение@файл или имя:0xHEX.",
       repeat="accumulate", placeholder="имя:@bin/файл.bin"),
    _o("lua-init", "required", "global", "lua_init",
       "Загрузить Lua-файл (@файл) или выполнить Lua-код. Порядок строк сохраняется.",
       repeat="accumulate", placeholder="@lua/файл.lua"),
    _o("lua-gc", "required", "global", "int",
       "Принудительная сборка мусора Lua каждые N секунд (0 — выключить).", int_min=0),
    # ------------------------------------------------------ списки хостов/IP
    _o("hostlist", "required", "profile", "file",
       "Профиль работает только для доменов из файла (поддомены тоже).",
       repeat="accumulate", placeholder="lists/файл.txt"),
    _o("hostlist-domains", "required", "profile", "text",
       "Домены прямо в строке через запятую.", repeat="accumulate", placeholder="example.com,example.org"),
    _o("hostlist-exclude", "required", "profile", "file",
       "Не применять профиль к доменам из файла.", repeat="accumulate", placeholder="lists/файл.txt"),
    _o("hostlist-exclude-domains", "required", "profile", "text",
       "Исключаемые домены прямо в строке через запятую.", repeat="accumulate"),
    _o("hostlist-auto", "required", "profile", "file",
       "Автосписок: winws2 сам дописывает в файл домены, где заметил блокировку. Один на профиль.",
       repeat="once", placeholder="lists/autohostlist.txt"),
    _o("hostlist-auto-fail-threshold", "required", "profile", "int",
       "Сколько неудач подряд добавляют домен в автосписок (1..20).", int_min=1, int_max=20),
    _o("hostlist-auto-fail-time", "required", "profile", "int",
       "За сколько секунд должны случиться все неудачи (не меньше 1).", int_min=1),
    _o("hostlist-auto-retrans-threshold", "required", "profile", "int",
       "Сколько повторов запроса считать неудачей (2..10).", int_min=2, int_max=10),
    _o("hostlist-auto-retrans-maxseq", "required", "profile", "int",
       "Считать повторы только в пределах этой относительной позиции.", int_min=0),
    _o("hostlist-auto-retrans-reset", "optional", "profile", "bool01",
       "Отправлять RST при долгих повторах (по умолчанию 1).", values=_BOOL01),
    _o("hostlist-auto-incoming-maxseq", "required", "profile", "int",
       "Считать соединение успешным, если входящих данных больше этого порога.", int_min=0),
    _o("hostlist-auto-udp-in", "required", "profile", "int",
       "UDP-неудача: пришло не больше стольких пакетов.", int_min=0),
    _o("hostlist-auto-udp-out", "required", "profile", "int",
       "UDP-неудача: отправлено не меньше стольких пакетов.", int_min=0),
    _o("hostlist-auto-debug", "required", "global", "file",
       "Файл журнала срабатываний автосписка (одна на весь запуск)."),
    # ----------------------------------------------------------- профили
    _o("new", "optional", "profile", "text",
       "Начать новый профиль; имя можно задать как --new=имя.", repeat="accumulate"),
    _o("skip", "none", "profile", "none",
       "Отключить этот профиль (он не участвует в обработке)."),
    _o("name", "required", "profile", "text", "Имя профиля."),
    _o("template", "optional", "profile", "text",
       "Сделать профиль шаблоном для --import (у шаблона должно быть имя)."),
    _o("import", "required", "profile", "template",
       "Скопировать в профиль настройки объявленного ранее шаблона."),
    _o("cookie", "required", "profile", "text", "Строка профиля, которую видят Lua-функции."),
    # ----------------------------------------------------- фильтры профиля
    _o("filter-l3", "required", "profile", "l3",
       "Версия IP: ipv4, ipv6 или обе через запятую.", repeat="accumulate", values=("ipv4", "ipv6", "ipv4,ipv6")),
    _o("filter-tcp", "required", "profile", "ports",
       "TCP-порты профиля: 443, 80,443, 1024-65535, ~ — отрицание, * — все.",
       repeat="accumulate", values=("80,443", "443", "*"), placeholder="443"),
    _o("filter-udp", "required", "profile", "ports",
       "UDP-порты профиля: 443, 50000-50099, ~ — отрицание, * — все.",
       repeat="accumulate", values=("443", "443-65535", "*"), placeholder="443"),
    _o("filter-icmp", "required", "profile", "icmp",
       "Тип[:код] ICMP через запятую, * — все.", repeat="accumulate"),
    _o("filter-ipp", "required", "profile", "ipp",
       "Номер IP-протокола через запятую, * — все.", repeat="accumulate"),
    _o("filter-l7", "required", "profile", "l7_list",
       "Протоколы профиля: tls, http, quic, discord, stun, wireguard и др."),
    _o("filter-ssid", "required", "profile", "text", "Фильтр профиля по Wi-Fi SSID.", platform="linux"),
    _o("filter-ssid-neg", "optional", "profile", "bool01", "Обратить фильтр SSID.", platform="linux"),
    _o("filter-mark", "required", "profile", "text", "Фильтр по fwmark.", platform="linux"),
    _o("ipset", "required", "profile", "file",
       "Профиль работает только для IP/подсетей из файла.", repeat="accumulate", placeholder="lists/ipset-файл.txt"),
    _o("ipset-ip", "required", "profile", "text",
       "IP и подсети прямо в строке через запятую.", repeat="accumulate"),
    _o("ipset-exclude", "required", "profile", "file",
       "Не применять профиль к IP/подсетям из файла.", repeat="accumulate", placeholder="lists/ipset-exclude.txt"),
    _o("ipset-exclude-ip", "required", "profile", "text",
       "Исключаемые IP и подсети прямо в строке через запятую.", repeat="accumulate"),
    # --------------------------------------------- что и когда делает Lua
    _o("payload", "required", "profile", "payload_list",
       "Типы данных для следующих --lua-desync (до следующего --payload).",
       values=("tls_client_hello", "http_req", "quic_initial", "all")),
    _o("in-range", "required", "profile", "range",
       "Какие входящие пакеты видят следующие --lua-desync (x — никакие, a — все, n2-n5 …).",
       values=("x", "a", "-d8", "-n3")),
    _o("out-range", "required", "profile", "range",
       "Какие исходящие пакеты видят следующие --lua-desync (например -d8, -n8, a).",
       values=("-d8", "-n8", "-d10", "a", "x")),
    _o("lua-desync", "required", "profile", "lua_desync",
       "Вызвать Lua-функцию обхода: функция:аргумент=значение:…", repeat="accumulate"),
    # ------------------------------------------------ фильтр WinDivert (Windows)
    _o("wf-iface", "required", "global", "wf_iface",
       "Номер сетевого интерфейса[.подинтерфейса] для перехвата.", platform="windows"),
    _o("wf-l3", "required", "global", "l3",
       "Версия IP для перехвата. В текущей версии winws2 ни на что не влияет.", platform="windows",
       values=("ipv4", "ipv6", "ipv4,ipv6")),
    _o("wf-tcp-in", "required", "global", "ports", "Перехватывать входящий TCP с этих портов.",
       platform="windows", values=("80,443",)),
    _o("wf-tcp-out", "required", "global", "ports", "Перехватывать исходящий TCP на эти порты.",
       platform="windows", values=("80,443", "80,443,2053,2083,2087,2096,8443")),
    _o("wf-udp-in", "required", "global", "ports", "Перехватывать входящий UDP с этих портов.",
       platform="windows", values=("443",)),
    _o("wf-udp-out", "required", "global", "ports", "Перехватывать исходящий UDP на эти порты.",
       platform="windows", values=("443", "443-65535")),
    _o("wf-tcp-empty", "optional", "global", "bool01",
       "Перехватывать пустые TCP-пакеты без SYN/RST/FIN (по умолчанию 0).", platform="windows", values=_BOOL01),
    _o("wf-icmp-in", "required", "global", "icmp", "Перехватывать входящий ICMP: тип[:код], *, -.",
       platform="windows"),
    _o("wf-icmp-out", "required", "global", "icmp", "Перехватывать исходящий ICMP: тип[:код], *, -.",
       platform="windows"),
    _o("wf-ipp-in", "required", "global", "ipp", "Перехватывать входящие пакеты с этим номером IP-протокола.",
       platform="windows"),
    _o("wf-ipp-out", "required", "global", "ipp", "Перехватывать исходящие пакеты с этим номером IP-протокола.",
       platform="windows"),
    _o("wf-raw", "required", "global", "wf_filter",
       "Полный фильтр WinDivert (текст или @файл); заменяет все остальные --wf-*.", platform="windows"),
    _o("wf-raw-part", "required", "global", "wf_filter",
       "Часть фильтра WinDivert (текст или @файл), части объединяются через ИЛИ.",
       platform="windows", repeat="accumulate", placeholder="@windivert.filter/файл.txt"),
    _o("wf-raw-filter", "required", "global", "wf_filter",
       "Общее условие WinDivert (текст или @файл), добавляется через И. Только одно.",
       platform="windows"),
    _o("wf-filter-lan", "required", "global", "bool01",
       "Не перехватывать локальные адреса (по умолчанию 1).", platform="windows", values=_BOOL01),
    _o("wf-filter-loopback", "required", "global", "bool01",
       "Не перехватывать loopback (по умолчанию 1).", platform="windows", values=_BOOL01),
    _o("wf-save", "required", "global", "file",
       "Сохранить итоговый фильтр WinDivert в файл и выйти — обход не запустится.", platform="windows"),
    _o("wf-dup-check", "optional", "global", "bool01",
       "1 (по умолчанию) — не запускать вторую копию winws2 с тем же фильтром.",
       platform="windows", values=_BOOL01),
    _o("ssid-filter", "required", "global", "text",
       "Работать, только если подключена одна из этих Wi-Fi сетей (через запятую).", platform="windows"),
    _o("ssid-filter-neg", "optional", "global", "bool01", "Обратить фильтр Wi-Fi сетей.",
       platform="windows", values=_BOOL01),
    _o("nlm-filter", "required", "global", "text",
       "Работать, только если подключена одна из этих сетей Windows (имя или GUID).", platform="windows"),
    _o("nlm-filter-neg", "optional", "global", "bool01", "Обратить фильтр сетей Windows.",
       platform="windows", values=_BOOL01),
    _o("nlm-list", "optional", "global", "text",
       "Показать список сетей Windows и сразу выйти — обход не запустится.", platform="windows",
       values=("all",)),
)

OPTIONS_BY_NAME: dict[str, OptionSpec] = {spec.name: spec for spec in WINWS2_OPTIONS}
WINDOWS_OPTIONS: tuple[OptionSpec, ...] = tuple(spec for spec in WINWS2_OPTIONS if spec.available_on_windows)
WINDOWS_OPTION_NAMES: frozenset[str] = frozenset(spec.name for spec in WINDOWS_OPTIONS)

# Опции, после которых winws2 завершается, не запуская обход.
EXITING_OPTION_NAMES: frozenset[str] = frozenset({"version", "dry-run", "wf-save", "nlm-list"})

# Опции, задающие фильтр перехвата WinDivert (без них winws2 не стартует).
WF_CAPTURE_OPTION_NAMES: frozenset[str] = frozenset(
    {
        "wf-tcp-in",
        "wf-tcp-out",
        "wf-udp-in",
        "wf-udp-out",
        "wf-icmp-in",
        "wf-icmp-out",
        "wf-ipp-in",
        "wf-ipp-out",
        "wf-raw-part",
        "wf-raw",
    }
)

_PLATFORM_TEXT = {
    "linux": "есть только в Linux-версии (nfqws2)",
    "bsd": "есть только в BSD-версии",
    "unix": "есть только в Linux/BSD-версиях",
}


def platform_note(spec: OptionSpec) -> str:
    return _PLATFORM_TEXT.get(spec.platform, "")


def resolve_windows_option(name: str) -> tuple[OptionSpec | None, tuple[OptionSpec, ...]]:
    """Как getopt_long_only winws2 понимает имя опции.

    Возвращает (опция, варианты): точное имя или единственный префикс даёт
    опцию; неоднозначный префикс — (None, все подходящие); неизвестное —
    (None, ()).
    """
    key = str(name or "")
    exact = OPTIONS_BY_NAME.get(key)
    if exact is not None and exact.available_on_windows:
        return exact, ()
    if not key:
        return None, ()
    candidates = tuple(spec for spec in WINDOWS_OPTIONS if spec.name.startswith(key))
    if len(candidates) == 1:
        return candidates[0], ()
    return None, candidates


__all__ = [
    "EXITING_OPTION_NAMES",
    "OPTIONS_BY_NAME",
    "OptionSpec",
    "WF_CAPTURE_OPTION_NAMES",
    "WINDOWS_OPTIONS",
    "WINDOWS_OPTION_NAMES",
    "WINWS2_OPTIONS",
    "platform_note",
    "resolve_windows_option",
]
