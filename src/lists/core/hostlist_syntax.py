"""Строки hostlist так, как их читает движок winws2 (nfqws2).

Единственное место в программе, где описан синтаксис строки hostlist и поиск
домена по списку. Редактор списка (проверка, подсветка, подсказки), проверка
пресета и сохранение списка берут правила отсюда, а не разбирают строки сами.

Откуда правила — исходники zapret2 (bol-van), ``nfq2/hostlist.c``:
``addpool`` (разбор строки), ``SearchHostList`` (поиск домена),
``HostlistCheck_`` (include/exclude/автолист); описание —
``docs/manual.md``, раздел про хостлисты. Префикс ``!`` добавлен в движок
коммитом 48ec126 «nfqws2: ! support in hostlists» (31.10.2026, версия 1.0.7
в ``docs/changes.txt``). Если автор снова поменяет эти функции — сверить
этот модуль и ``tests/test_hostlist_syntax.py``; вкратце правила есть в
``.codex/skills/zapret2-original/SKILL.md``.

Правила строки:

- пробелы и табы в начале пропускаются; строка, которая начинается с ``#``,
  ``;`` или ``/``, — комментарий; всё после первого пробела — тоже;
- регистр не важен: движок переводит строку в нижний регистр;
- ``^`` в начале — только сам домен, без поддоменов;
- ``!`` в начале — локальное исключение: домен (и его поддомены) не считается
  частью ЭТОГО списка. В другие списки исключение не переходит. Пример:
  ``ru`` + ``!torrents.ru`` в одном списке ловят все *.ru, кроме torrents.ru;
- ``^`` и ``!`` сочетаются в любом порядке (``!^a.ru`` = ``^!a.ru``);
- ``*`` не поддерживается: ``*.example.com`` хранится как есть и не совпадёт
  ни с одним сайтом. Поддомены и так учитываются сами.

Поиск домена (``SearchHostList``): проверяется сам домен, затем родители по
очереди — www.a.ru → a.ru → ru. Решает первая найденная запись:

- запись с ``^`` на родительском уровне пропускается, поиск идёт выше;
- обычная запись — домен в списке;
- запись с ``!`` — домен НЕ в этом списке, поиск по нему останавливается.

Повторы: в одном списке домен хранится один раз, работает ПЕРВАЯ его запись,
следующие отбрасываются вместе с префиксами (``ru`` выше ``!ru`` — ``!ru``
не действует). Поэтому итоговый файл собирается «сначала записи
пользователя, потом встроенные» (``lists.core.builders``): так ``!домен`` у
пользователя перебивает встроенную запись.

Автолист (``--hostlist-auto``): исключение в нём работает как exclude —
стратегия к домену не применяется, неудачи по нему не считаются, и движок
не дописывает его в автолист повторно (HostlistCheck_ ставит excluded, а
nfq2/desync.c по нему выключает hostname_ah_check). Если тот же домен есть в
другом включающем списке профиля, тот список срабатывает как обычно. Писать
``!`` в автолист автор не рекомендует, но это не ошибка.

Ещё одно правило движка с того же дня (коммит abbe480): один и тот же файл
нельзя подключать в одном профиле дважды одного типа (``--hostlist`` и
``--hostlist-auto`` — один тип) — winws2 не запустится. Это проверяет
редактор пресета (``profile.winws2_language.diagnostics``).
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re

COMMENT_PREFIXES = ("#", ";", "/")
EXACT_PREFIX = "^"
EXCEPTION_PREFIX = "!"

LINE_BLANK = "blank"
LINE_COMMENT = "comment"
LINE_ENTRY = "entry"


@dataclass(frozen=True, slots=True)
class HostlistLine:
    """Одна строка файла списка.

    Колонки считаются от начала строки: ``start`` — первый префикс или
    домен, ``domain_start``/``domain_end`` — сам домен (``end`` не входит).
    ``domain`` уже в нижнем регистре, без префиксов.
    """

    kind: str
    domain: str = ""
    exact: bool = False
    exception: bool = False
    start: int = 0
    domain_start: int = 0
    domain_end: int = 0

    @property
    def is_entry(self) -> bool:
        return self.kind == LINE_ENTRY

    @property
    def prefix(self) -> str:
        return format_hostlist_prefix(exact=self.exact, exception=self.exception)


def parse_hostlist_line(raw_line: str) -> HostlistLine:
    """Разбирает строку так же, как ``addpool`` в nfq2/hostlist.c."""
    line = str(raw_line or "")
    position = 0
    length = len(line)
    while position < length and line[position] in " \t":
        position += 1
    if position >= length or line[position] in "\r\n":
        return HostlistLine(LINE_BLANK, start=position)
    if line[position] in COMMENT_PREFIXES:
        return HostlistLine(LINE_COMMENT, start=position)
    start = position
    exact = exception = False
    while position < length and line[position] in (EXACT_PREFIX, EXCEPTION_PREFIX):
        if line[position] == EXACT_PREFIX:
            exact = True
        else:
            exception = True
        position += 1
    domain_start = position
    while position < length and line[position] not in " \t\r\n":
        position += 1
    return HostlistLine(
        LINE_ENTRY,
        domain=line[domain_start:position].lower(),
        exact=exact,
        exception=exception,
        start=start,
        domain_start=domain_start,
        domain_end=position,
    )


def format_hostlist_prefix(*, exact: bool = False, exception: bool = False) -> str:
    """Префиксы в одном порядке: сначала ``!``, потом ``^``."""
    return (EXCEPTION_PREFIX if exception else "") + (EXACT_PREFIX if exact else "")


def format_hostlist_entry(domain: str, *, exact: bool = False, exception: bool = False) -> str:
    return format_hostlist_prefix(exact=exact, exception=exception) + str(domain or "").strip().lower()


def is_comment_or_blank(raw_line: str) -> bool:
    return not parse_hostlist_line(raw_line).is_entry


@dataclass(frozen=True, slots=True)
class IndexedEntry:
    line_number: int  # с нуля, в пределах текста, по которому строился индекс
    entry: HostlistLine
    source: str = ""


_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


def hostlist_line_problem(line: str) -> str:
    """Почему строка hostlist не сработает в winws2; пусто — строка в порядке.

    Строку разбирает ``lists.core.hostlist_syntax`` (как движок): префиксы
    ``^`` и ``!`` в любом порядке, комментарии ``#``, ``;``, ``/``.
    """
    entry = parse_hostlist_line(line)
    if not entry.is_entry:
        return ""
    value = entry.domain
    if not value:
        return "После префикса нет домена."
    if str(line)[entry.domain_end:].strip():
        return f"Пробел внутри записи: winws2 возьмёт только «{value}», остальное отбросит. Одна запись — одна строка."
    if "*" in value:
        return "Звёздочки winws2 не понимает: запись «*.сайт» не совпадёт ни с чем. Поддомены учитываются сами — пишите просто домен."
    if value.startswith("."):
        return "Точка в начале лишняя: такая запись не совпадёт ни с чем. Поддомены учитываются сами — пишите просто домен."
    if "://" in value or "/" in value or ":" in value:
        return "Нужен только домен, без http://, пути и порта."
    try:
        ipaddress.ip_address(value)
        return "IP-адрес — запись для ipset-списка, hostlist ищет только имена сайтов."
    except ValueError:
        pass
    try:
        ascii_domain = value.encode("idna").decode("ascii")
    except Exception:
        return "Это не похоже на домен."
    if len(ascii_domain) > 253:
        return "Слишком длинный домен."
    labels = ascii_domain.rstrip(".").split(".")
    # Оригинальный nfqws2 ищет hostname по суффиксам:
    # www.example.ru -> example.ru -> ru. Поэтому одиночная DNS-метка вроде
    # ru или su — допустимая и осмысленная запись hostlist.
    # Числового TLD не существует (RFC 3696): такое значение — IP-подобная
    # запись, которой место в ipset, а не в hostlist.
    if labels[-1].isdigit():
        return "IP-адрес — запись для ipset-списка, hostlist ищет только имена сайтов."
    if not all(_LABEL_RE.fullmatch(label or "") for label in labels):
        return "Это не похоже на домен."
    return ""


class HostlistIndex:
    """Записи одного списка в том виде, в каком их держит движок.

    Домен хранится один раз: первая запись побеждает (``ADD_HOSTLIST_POOL``
    в nfq2/pools.c не добавляет найденный ключ повторно).
    Внутри — домен → (номер строки, ^, !, источник): встроенный список бывает
    на сто тысяч строк, объект на каждую запись строился бы втрое дольше.
    """

    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: dict[str, tuple[int, bool, bool, str]] = {}

    @classmethod
    def from_texts(cls, *sources: tuple[str, str]) -> "HostlistIndex":
        """Строит индекс из нескольких текстов по порядку: (имя источника, текст)."""
        index = cls()
        for source, text in sources:
            index.add_text(text, source=source)
        return index

    def add_text(self, text: str, *, source: str = "") -> None:
        # Обычные строки без префиксов и пробелов идут коротким путём, без
        # посимвольного разбора. Результат тот же, что у parse_hostlist_line.
        entries = self._entries
        for line_number, raw in enumerate(str(text or "").splitlines()):
            head = raw[:1]
            if not head or head in "#;/!^ \t" or " " in raw or "\t" in raw:
                entry = parse_hostlist_line(raw)
                if entry.is_entry and entry.domain and entry.domain not in entries:
                    entries[entry.domain] = (line_number, entry.exact, entry.exception, source)
                continue
            domain = raw.lower()
            if domain not in entries:
                entries[domain] = (line_number, False, False, source)

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, domain: str) -> IndexedEntry | None:
        key = str(domain or "").lower()
        return self._indexed(key, self._entries.get(key))

    def lookup(self, host: str) -> IndexedEntry | None:
        """Запись, которая решает судьбу домена, или None.

        Повторяет ``SearchHostList``: вернулась обычная запись — домен в
        списке; вернулась запись с ``!`` — домен исключён из этого списка;
        None — список домен не касается.
        """
        return self._walk(str(host or "").strip().lower().rstrip("."), full=True)

    def contains(self, host: str) -> bool:
        found = self.lookup(host)
        return found is not None and not found.entry.exception

    def covering_parent(self, domain: str) -> IndexedEntry | None:
        """Запись уровнем выше, которая решает судьбу ``domain``.

        Нужна, чтобы понять, есть ли от исключения толк: ``!a.ru`` что-то
        меняет, только если выше стоит обычная запись (``ru``), которая
        захватила бы a.ru.
        """
        value = str(domain or "").strip().lower()
        if "." not in value:
            return None
        return self._walk(value.split(".", 1)[1], full=False)

    def _walk(self, host: str, *, full: bool) -> IndexedEntry | None:
        current = host
        while current:
            found = self._entries.get(current)
            # ^ на родительском уровне не срабатывает — идём выше.
            if found is not None and not (found[1] and not full):
                return self._indexed(current, found)
            if "." not in current:
                return None
            current = current.split(".", 1)[1]
            full = False
        return None

    @staticmethod
    def _indexed(domain: str, found: tuple[int, bool, bool, str] | None) -> IndexedEntry | None:
        if found is None:
            return None
        line_number, exact, exception, source = found
        return IndexedEntry(
            line_number,
            HostlistLine(LINE_ENTRY, domain=domain, exact=exact, exception=exception),
            source,
        )


__all__ = [
    "COMMENT_PREFIXES",
    "EXACT_PREFIX",
    "EXCEPTION_PREFIX",
    "HostlistIndex",
    "HostlistLine",
    "IndexedEntry",
    "LINE_BLANK",
    "LINE_COMMENT",
    "LINE_ENTRY",
    "format_hostlist_entry",
    "format_hostlist_prefix",
    "hostlist_line_problem",
    "is_comment_or_blank",
    "parse_hostlist_line",
]
