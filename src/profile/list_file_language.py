"""Поддержка языка в редакторе файла списка (hostlist) страницы профиля.

Что видит человек в поле «Ваши записи»:

- ошибки — строка, которую winws2 не поймёт (``*.сайт``, адрес с http://,
  IP-адрес в hostlist);
- предупреждения — запись, которая ничего не делает: повтор домена (движок
  берёт первую запись), исключение ``!``, которому нечего исключать;
- подсказки при наведении — что именно делает строка, на примерах;
- Ctrl+. — сделать запись исключением ``!`` / только этим доменом ``^`` и
  обратно; Ctrl+Пробел в начале строки — те же префиксы списком.

Правила строки и поиск домена — ``lists.core.hostlist_syntax`` (как движок).
Встроенная часть списка (``base_index``) строится в рабочем потоке при
загрузке списка: здесь по ней только ищут.
"""

from __future__ import annotations

from lists.core.hostlist_syntax import (
    EXACT_PREFIX,
    EXCEPTION_PREFIX,
    HostlistIndex,
    HostlistLine,
    IndexedEntry,
    format_hostlist_prefix,
    hostlist_line_problem,
    parse_hostlist_line,
)
from profile.winws2_language.model import (
    SEVERITY_ERROR,
    SEVERITY_HINT,
    SEVERITY_WARNING,
    CompletionItem,
    CompletionResult,
    Diagnostic,
    QuickFix,
    remove_whole_line,
    replace_in_line,
)

SOURCE_USER = "user"
SOURCE_BASE = "base"

# Короткая памятка о префиксах — её же показывает подпись над полем.
# «!» понимает только winws2 (Zapret 2); winws (Zapret 1) знает лишь «^»
# (zapret nfq/hostlist.c, проверено 01.11.2026), а папка lists у режимов общая.
HOSTLIST_PREFIX_HELP_ZAPRET1 = (
    "Домен ловит и свои поддомены. «^домен» — только сам домен. "
    "Исключения «!домен» работают только в режиме Zapret 2."
)
HOSTLIST_PREFIX_HELP = (
    "Домен ловит и свои поддомены. «^домен» — только сам домен. "
    "«!домен» — исключение: этот домен и его поддомены не считаются частью этого списка "
    "(например, «ru» и «!torrents.ru» — все сайты .ru, кроме torrents.ru)."
)


def _remove_line_fix(line: int, title: str = "Удалить строку") -> QuickFix:
    return QuickFix(title, (remove_whole_line(line),))


def _where(found: IndexedEntry) -> str:
    if found.source == SOURCE_BASE:
        return f"во встроенных записях (строка {found.line_number + 1})"
    return f"в строке {found.line_number + 1}"


def _entry_text(entry: HostlistLine) -> str:
    return f"{entry.prefix}{entry.domain}"


def _user_index(lines: list[str]) -> tuple[list[HostlistLine], HostlistIndex]:
    parsed = [parse_hostlist_line(line) for line in lines]
    index = HostlistIndex()
    index.add_text("\n".join(lines), source=SOURCE_USER)
    return parsed, index


class _Combined:
    """Список так, как его соберёт программа: сначала ваши записи, потом встроенные."""

    def __init__(self, user: HostlistIndex, base: HostlistIndex | None) -> None:
        self.user = user
        self.base = base

    def get(self, domain: str) -> IndexedEntry | None:
        found = self.user.get(domain)
        if found is None and self.base is not None:
            found = self.base.get(domain)
        return found

    def covering_parent(self, domain: str) -> IndexedEntry | None:
        # Тот же обход, что у движка, по двум слоям: на каждом уровне сначала
        # запись пользователя, потом встроенная.
        value = str(domain or "").strip().lower()
        if "." not in value:
            return None
        current = value.split(".", 1)[1]
        while current:
            found = self.get(current)
            if found is not None and not found.entry.exact:
                return found
            if "." not in current:
                return None
            current = current.split(".", 1)[1]
        return None


_ZAPRET1_EXCEPTION_MESSAGE = (
    "winws (Zapret 1) не понимает «!»: он прочитает «{entry}» как имя сайта, и исключение не сработает. "
    "Исключения работают только в режиме Zapret 2 (winws2)."
)


def diagnose_hostlist_text(
    text: str,
    *,
    base_index: HostlistIndex | None = None,
    exceptions: bool = True,
) -> tuple[Diagnostic, ...]:
    lines = str(text or "").split("\n")
    parsed, user = _user_index(lines)
    combined = _Combined(user, base_index)
    result: list[Diagnostic] = []
    for line_number, entry in enumerate(parsed):
        if not entry.is_entry:
            continue
        raw = lines[line_number]
        start, end = entry.start, max(entry.domain_end, entry.start + 1)
        problem = hostlist_line_problem(raw)
        if not problem and entry.exception and not exceptions:
            result.append(Diagnostic(
                line_number, start, end, SEVERITY_WARNING,
                _ZAPRET1_EXCEPTION_MESSAGE.format(entry=_entry_text(entry)),
            ))
            continue
        if problem:
            fixes: list[QuickFix] = []
            cleaned = entry.domain.lstrip("*.")
            if cleaned and cleaned != entry.domain and ("*" in entry.domain or entry.domain.startswith(".")):
                fixes.append(QuickFix(
                    f"Оставить «{entry.prefix}{cleaned}»",
                    (replace_in_line(line_number, entry.start, entry.domain_end, entry.prefix + cleaned),),
                ))
            fixes.append(_remove_line_fix(line_number))
            result.append(Diagnostic(line_number, start, len(raw.rstrip()) or end, SEVERITY_ERROR, problem, tuple(fixes)))
            continue

        first = user.get(entry.domain)
        if first is not None and first.line_number != line_number:
            same = first.entry.prefix == entry.prefix
            message = (
                f"Повтор: «{_entry_text(first.entry)}» уже есть в строке {first.line_number + 1}."
                if same else
                f"Домен {entry.domain} уже записан в строке {first.line_number + 1} как «{_entry_text(first.entry)}». "
                "В одном списке winws2 учитывает только первую запись домена — эта строка не действует."
            )
            result.append(Diagnostic(line_number, start, end, SEVERITY_WARNING, message,
                                     (_remove_line_fix(line_number),)))
            continue

        base_same = base_index.get(entry.domain) if base_index is not None else None
        if base_same is not None:
            if base_same.entry.prefix == entry.prefix:
                result.append(Diagnostic(
                    line_number, start, end, SEVERITY_HINT,
                    f"«{_entry_text(entry)}» уже есть {_where(base_same)} — строка лишняя.",
                    (_remove_line_fix(line_number, "Удалить лишнюю строку"),),
                ))
            else:
                result.append(Diagnostic(
                    line_number, start, end, SEVERITY_HINT,
                    f"Перебивает встроенную запись «{_entry_text(base_same.entry)}»: ваши записи идут первыми, "
                    "а winws2 учитывает первую запись домена.",
                ))
            continue

        if entry.exception:
            parent = combined.covering_parent(entry.domain)
            if parent is None:
                result.append(Diagnostic(
                    line_number, start, end, SEVERITY_WARNING,
                    f"Исключению нечего исключать: в этом списке нет записи, которая захватывает {entry.domain} "
                    f"(например «{_parent_example(entry.domain)}»). «!» действует только внутри своего списка — "
                    "на другие списки и профили оно не влияет.",
                    (_remove_line_fix(line_number),),
                ))
            elif parent.entry.exception:
                result.append(Diagnostic(
                    line_number, start, end, SEVERITY_HINT,
                    f"Уже исключено записью «{_entry_text(parent.entry)}» {_where(parent)}.",
                    (_remove_line_fix(line_number, "Удалить лишнюю строку"),),
                ))
            continue

        parent = combined.covering_parent(entry.domain)
        if parent is not None and not parent.entry.exception and not entry.exact:
            result.append(Diagnostic(
                line_number, start, end, SEVERITY_HINT,
                f"Уже входит в список через «{_entry_text(parent.entry)}» {_where(parent)}: поддомены учитываются сами.",
                (_remove_line_fix(line_number, "Удалить лишнюю строку"),),
            ))
    return tuple(result)


def _parent_example(domain: str) -> str:
    parts = str(domain or "").split(".")
    return ".".join(parts[1:]) if len(parts) > 1 else domain


def describe_hostlist_line(
    text: str,
    line: int,
    *,
    base_index: HostlistIndex | None = None,
    exceptions: bool = True,
) -> str:
    lines = str(text or "").split("\n")
    if not 0 <= line < len(lines):
        return ""
    entry = parse_hostlist_line(lines[line])
    if not entry.is_entry or not entry.domain or hostlist_line_problem(lines[line]):
        return ""
    domain = entry.domain
    if entry.exception and not exceptions:
        return _ZAPRET1_EXCEPTION_MESSAGE.format(entry=_entry_text(entry))
    if entry.exception:
        _parsed, user = _user_index(lines)
        parent = _Combined(user, base_index).covering_parent(domain)
        scope = f"сам {domain}" if entry.exact else f"{domain} и его поддомены (www.{domain} и другие)"
        text_parts = [
            f"«!» — исключение из этого списка: {scope} не считаются его частью.",
        ]
        if parent is not None and not parent.entry.exception:
            text_parts.append(f"Без этой строки домен попал бы сюда через «{_entry_text(parent.entry)}».")
        if entry.exact:
            text_parts.append(f"«^» — поддомены вроде www.{domain} остаются в списке.")
        text_parts.append(
            "Если это список сайтов для обхода — домен обходиться не будет. "
            "Если это список исключений (--hostlist-exclude) — домен исключён НЕ будет. "
            "На другие файлы списков «!» не влияет."
        )
        return " ".join(text_parts)
    if entry.exact:
        return f"«^» — только сам {domain}. Поддомены вроде www.{domain} в список не попадают."
    return f"{domain} и все его поддомены (www.{domain}, любой.{domain}). Чтобы взять только сам домен — «^{domain}»."


def quick_actions_hostlist(text: str, line: int, *, exceptions: bool = True) -> tuple[QuickFix, ...]:
    lines = str(text or "").split("\n")
    if not 0 <= line < len(lines):
        return ()
    entry = parse_hostlist_line(lines[line])
    if not entry.is_entry or not entry.domain or hostlist_line_problem(lines[line]):
        return ()

    def rewrite(title: str, *, exact: bool, exception: bool) -> QuickFix:
        new_prefix = format_hostlist_prefix(exact=exact, exception=exception)
        return QuickFix(title, (replace_in_line(line, entry.start, entry.domain_start, new_prefix),))

    actions = []
    if entry.exception:
        actions.append(rewrite("Убрать исключение «!»", exact=entry.exact, exception=False))
    elif exceptions:
        actions.append(rewrite(f"Сделать исключением: «!{entry.domain}»", exact=entry.exact, exception=True))
    actions.append(
        rewrite("Вместе с поддоменами: убрать «^»", exact=False, exception=entry.exception)
        if entry.exact else
        rewrite(f"Только сам домен, без поддоменов: «^{entry.domain}»", exact=True, exception=entry.exception)
    )
    return tuple(actions)


_PREFIX_ITEMS = (
    CompletionItem(EXCEPTION_PREFIX, EXCEPTION_PREFIX,
                   "исключение: домен и поддомены не считаются частью этого списка"),
    CompletionItem(EXACT_PREFIX, EXACT_PREFIX, "только сам домен, без поддоменов"),
)


def complete_hostlist(
    text: str,
    line: int,
    column: int,
    *,
    explicit: bool,
    exceptions: bool = True,
) -> CompletionResult | None:
    if not explicit:
        return None
    lines = str(text or "").split("\n")
    if not 0 <= line < len(lines):
        return None
    entry = parse_hostlist_line(lines[line])
    if entry.kind == "comment":
        return None
    if entry.is_entry and column > entry.domain_start:
        return None
    present = lines[line][entry.start:entry.domain_start] if entry.is_entry else ""
    items = tuple(
        item for item in _PREFIX_ITEMS
        if item.label not in present and (exceptions or item.label != EXCEPTION_PREFIX)
    )
    if not items:
        return None
    return CompletionResult(line, column, column, items)


class HostlistEditorLanguage:
    """Поддержка языка для ``CodeEditor`` (см. ``ui.code_editor.language``).

    Работает только для hostlist: у ipset-списков свой синтаксис, там
    редактор молчит. Страница передаёт тип списка и индекс встроенных
    записей после каждой загрузки (``set_list``).
    """

    def __init__(self, *, exceptions: bool = True) -> None:
        self.kind = ""
        self.base_index: HostlistIndex | None = None
        # False в режиме Zapret 1: winws не понимает «!».
        self.exceptions = bool(exceptions)

    def set_list(self, *, kind: str, base_index: HostlistIndex | None) -> None:
        self.kind = str(kind or "").strip().lower()
        self.base_index = base_index if isinstance(base_index, HostlistIndex) else None

    @property
    def active(self) -> bool:
        return self.kind == "hostlist"

    def diagnose(self, text: str):
        if not self.active:
            return ()
        return diagnose_hostlist_text(text, base_index=self.base_index, exceptions=self.exceptions)

    def complete(self, text: str, line: int, column: int, *, explicit: bool):
        if not self.active:
            return None
        return complete_hostlist(text, line, column, explicit=explicit, exceptions=self.exceptions)

    def describe(self, text: str, line: int, column: int) -> str:
        if not self.active:
            return ""
        return describe_hostlist_line(text, line, base_index=self.base_index, exceptions=self.exceptions)

    def quick_actions(self, text: str, line: int, column: int):
        return quick_actions_hostlist(text, line, exceptions=self.exceptions) if self.active else ()


__all__ = [
    "HOSTLIST_PREFIX_HELP",
    "HOSTLIST_PREFIX_HELP_ZAPRET1",
    "HostlistEditorLanguage",
    "complete_hostlist",
    "describe_hostlist_line",
    "diagnose_hostlist_text",
    "quick_actions_hostlist",
]
