"""Объявления фейков (``--blob=``) для выбранной стратегии.

Фейк — это набор байтов, который стратегия отправляет вместо настоящего пакета
(например, ClientHello на www.google.com). Стратегия ссылается на фейк по имени
(``--lua-desync=fake:blob=tls_google``), а сам фейк объявляется в пресете
строкой ``--blob=tls_google:@bin/tls_clienthello_www_google_com.bin``.

Пресет — точка истины: при запуске ничего не подставляется. Поэтому когда
пользователь ЯВНО выбирает стратегию (готовая стратегия на странице profile-а,
«Применить» в blockcheck), программа дописывает в преамбулу пресета строки
``--blob=`` для тех фейков стратегии, которые в пресете ещё не объявлены, и
сохраняет пресет обычным путём — пользователь видит эти строки в тексте.

Правила (см. nfq2/nfqws.c: ``item_name``, ``load_blob_to_collection``):

- имя фейка — часть значения ``--blob=`` до первого ``:``, сравнивается с
  учётом регистра;
- winws2 завершается ошибкой «duplicate blob name», если имя объявлено дважды,
  поэтому имя, уже объявленное где угодно в пресете (любым файлом или hex),
  повторно не добавляется: объявление в пресете всегда главнее реестра;
- встроенные фейки winws2 (``NFQWS2_BUILTIN_BLOBS``) не объявляются никогда;
- фейки, которые создаёт сам lua-код из обязательного блока ``--lua-init``
  (``LUA_DEFINED_BLOB_NAMES``), объявлять не нужно.

Модуль чистый: реестр фейков приходит снаружи функцией загрузки, файлов он не
читает.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Iterable, Mapping, Protocol

from fakes.public import BLOB_REFERENCE_ARG_NAMES, NFQWS2_BUILTIN_BLOBS

from .models import Preset


# Глобальные переменные-фейки, которые задаёт lua/custom_funcs.lua
# (``fake_unknown_256 = fake_unknown_256 or string.rep(...)``). Этот файл входит
# в обязательный блок --lua-init каждого winws2-пресета, поэтому такие имена
# объявлять через --blob= не нужно. Единственное место этого списка в коде;
# при правке custom_funcs.lua сверять вручную (тесты реестра фейков вычисляют
# такие имена прямо из lua-файлов).
LUA_DEFINED_BLOB_NAMES: frozenset[str] = frozenset({"fake_unknown_256", "fake_zero64"})

# Функция, у которой ``blob=`` — не ссылка на фейк, а имя, под которым она
# сохраняет собственный результат (выходной слот). ``fallback=`` у неё остаётся
# обычной ссылкой.
_BLOB_OUTPUT_SLOT_FUNCTIONS: frozenset[str] = frozenset({"tls_client_hello_clone"})

_BLOB_OPTION = "--blob="
_LUA_DESYNC_OPTION = "--lua-desync="
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Та же граница, по которой запуск делит строку «--a --b» на отдельные аргументы.
_INLINE_OPTION_SPLIT_RE = re.compile(r"(?<=\S)\s+(?=--)")


class FakeEntryLike(Protocol):
    def blob_line(self) -> str: ...


class FakesCatalogLike(Protocol):
    @property
    def entries(self) -> Mapping[str, FakeEntryLike]: ...


FakesCatalogLoader = Callable[[], FakesCatalogLike]


@dataclass(frozen=True)
class BlobDeclarationReport:
    """Что сделано с фейками выбранной стратегии.

    - ``added`` — имена, для которых в пресет дописана строка ``--blob=``;
    - ``unknown`` — имена, которых нет ни в пресете, ни в реестре (стратегия
      сошлётся на необъявленный фейк);
    - ``conflicts`` — имена, которые пресет объявляет иначе, чем реестр:
      оставлено объявление пресета;
    - ``catalog_error`` — реестр не прочитан, фейки не дописаны.
    """

    added: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    catalog_error: str = ""

    def user_warnings(self) -> tuple[str, ...]:
        """Короткие сообщения для пользователя; пусто — всё в порядке."""
        warnings: list[str] = []
        if self.catalog_error:
            names = ", ".join(self.unknown)
            warnings.append(
                f"Реестр фейков недоступен ({self.catalog_error}); в пресет не добавлены фейки: {names}"
            )
        elif self.unknown:
            warnings.append(
                "Стратегия ссылается на фейки, которых нет в пресете и в реестре: "
                + ", ".join(self.unknown)
            )
        return tuple(warnings)


def _split_inline_options(line: str) -> list[str]:
    stripped = str(line or "").strip()
    if not stripped or stripped.startswith("#"):
        return []
    return [part for part in _INLINE_OPTION_SPLIT_RE.split(stripped) if part]


def _blob_references(lua_desync_value: str) -> set[str]:
    parts = str(lua_desync_value or "").split(":")
    function_name = parts[0].strip()
    names: set[str] = set()
    for raw_arg in parts[1:]:
        key, separator, value = raw_arg.partition("=")
        key = key.strip()
        if not separator or key not in BLOB_REFERENCE_ARG_NAMES:
            continue
        if key == "blob" and function_name in _BLOB_OUTPUT_SLOT_FUNCTIONS:
            continue
        value = value.strip().strip('"').strip("'")
        if value[:2].lower() == "0x" or not _IDENTIFIER_RE.match(value):
            continue
        names.add(value)
    return names


def required_blob_names(strategy_lines: Iterable[str]) -> set[str]:
    """Имена фейков, на которые ссылаются строки ``--lua-desync`` стратегии."""
    names: set[str] = set()
    for line in strategy_lines or ():
        for option in _split_inline_options(line):
            if option.lower().startswith(_LUA_DESYNC_OPTION):
                names |= _blob_references(option[len(_LUA_DESYNC_OPTION):])
    return names


def _blob_declaration(option: str) -> tuple[str, str] | None:
    """``--blob=NAME:VALUE`` -> (NAME, NAME:VALUE) по правилу item_name из nfqws2."""
    if not option.lower().startswith(_BLOB_OPTION):
        return None
    spec = option[len(_BLOB_OPTION):].strip()
    name = spec.split(":", 1)[0].strip()
    if not name:
        return None
    return name, spec


def declared_blobs(preset_text: str) -> dict[str, str]:
    """Объявленные в тексте пресета фейки: имя -> значение ``NAME:...``.

    Смотрит на каждую строку, кроме комментариев: и в преамбуле, и после
    ``--new``. При повторе имени запоминается первое объявление.
    """
    declared: dict[str, str] = {}
    for line in str(preset_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        for option in _split_inline_options(line):
            parsed = _blob_declaration(option)
            if parsed is not None:
                declared.setdefault(parsed[0], parsed[1])
    return declared


def declared_blob_names(preset_text: str) -> set[str]:
    """Имена фейков, объявленных где угодно в тексте пресета."""
    return set(declared_blobs(preset_text))


def plan_blob_declaration_lines(
    strategy_lines: Iterable[str],
    declared: Mapping[str, str],
    load_catalog: FakesCatalogLoader | None,
    *,
    lua_defined_names: Iterable[str] = LUA_DEFINED_BLOB_NAMES,
) -> tuple[list[str], BlobDeclarationReport]:
    """Строки ``--blob=``, которых не хватает стратегии, в порядке имён.

    Реестр читается только если стратегия вообще ссылается на фейки. Ошибка
    чтения не прерывает применение стратегии: строки не добавляются, а причина
    попадает в отчёт (если чего-то действительно не хватает).
    """
    skipped = set(NFQWS2_BUILTIN_BLOBS) | set(lua_defined_names)
    needed = sorted(required_blob_names(strategy_lines) - skipped)
    if not needed:
        return [], BlobDeclarationReport()
    missing = [name for name in needed if name not in declared]

    try:
        if load_catalog is None:
            raise RuntimeError("реестр не подключён")
        entries = load_catalog().entries
    except Exception as exc:
        if not missing:
            return [], BlobDeclarationReport()
        return [], BlobDeclarationReport(unknown=tuple(missing), catalog_error=str(exc) or type(exc).__name__)

    lines: list[str] = []
    added: list[str] = []
    unknown: list[str] = []
    conflicts: list[str] = []
    for name in needed:
        entry = entries.get(name)
        parsed = _blob_declaration(str(entry.blob_line()).strip()) if entry is not None else None
        if parsed is not None and parsed[0] != name:
            parsed = None
        if name in declared:
            if parsed is not None and parsed[1] != declared[name]:
                conflicts.append(name)
            continue
        if parsed is None:
            unknown.append(name)
            continue
        lines.append(f"{_BLOB_OPTION}{parsed[1]}")
        added.append(name)
    return lines, BlobDeclarationReport(
        added=tuple(added),
        unknown=tuple(unknown),
        conflicts=tuple(conflicts),
    )


def _is_blob_line(line: str) -> bool:
    return any(_blob_declaration(option) is not None for option in _split_inline_options(line))


def _is_lua_init_line(line: str) -> bool:
    return any(option.lower().startswith("--lua-init=") for option in _split_inline_options(line))


def _blob_insert_index(preamble_lines: list[str]) -> int:
    for index in range(len(preamble_lines) - 1, -1, -1):
        if _is_blob_line(preamble_lines[index]):
            return index + 1
    for index in range(len(preamble_lines) - 1, -1, -1):
        if _is_lua_init_line(preamble_lines[index]):
            return index + 1
    index = len(preamble_lines)
    while index > 0 and not str(preamble_lines[index - 1]).strip():
        index -= 1
    return index


def with_blob_lines_in_preamble(preamble_lines: list[str], blob_lines: list[str]) -> list[str]:
    """Новая преамбула со строками ``--blob=``.

    Место: после последней строки ``--blob=`` преамбулы, иначе после блока
    ``--lua-init``, иначе в конце преамбулы. Новая группа отделяется пустой
    строкой от соседних строк другого вида.
    """
    if not blob_lines:
        return list(preamble_lines)
    lines = list(preamble_lines)
    index = _blob_insert_index(lines)
    block = list(blob_lines)
    if index > 0 and str(lines[index - 1]).strip() and not _is_blob_line(lines[index - 1]):
        block.insert(0, "")
    if index < len(lines) and str(lines[index]).strip() and not _is_blob_line(lines[index]):
        block.append("")
    lines[index:index] = block
    return lines


def with_declared_blobs(
    preset: Preset,
    strategy_lines: Iterable[str],
    load_catalog: FakesCatalogLoader | None,
    *,
    lua_defined_names: Iterable[str] = LUA_DEFINED_BLOB_NAMES,
) -> tuple[Preset, BlobDeclarationReport]:
    """Пресет с объявлениями недостающих фейков стратегии.

    Меняется только преамбула; profile-ы не трогаются. Если добавлять нечего,
    возвращается тот же объект пресета. Повторный вызов ничего не добавляет.
    """
    from copy import deepcopy

    from .serializer import serialize_preset

    lines, report = plan_blob_declaration_lines(
        strategy_lines,
        declared_blobs(serialize_preset(preset)),
        load_catalog,
        lua_defined_names=lua_defined_names,
    )
    if not lines:
        return preset, report
    updated = deepcopy(preset)
    updated.preamble_lines = with_blob_lines_in_preamble(list(updated.preamble_lines), lines)
    return updated, report


__all__ = [
    "BlobDeclarationReport",
    "FakesCatalogLoader",
    "LUA_DEFINED_BLOB_NAMES",
    "declared_blob_names",
    "declared_blobs",
    "plan_blob_declaration_lines",
    "required_blob_names",
    "with_blob_lines_in_preamble",
    "with_declared_blobs",
]
