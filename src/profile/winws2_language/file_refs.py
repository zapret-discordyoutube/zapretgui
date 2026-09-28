"""Ссылки пресета на файлы и проверка, что эти файлы есть на диске.

Пути разрешаются так же, как их откроет winws2 и как их проверяет запуск
(``Winws2StrategyRunner._collect_missing_preset_references_from_text``):
``@`` в начале снимается, относительный путь считается от корня программы
(рабочая папка winws2), абсолютный Windows-путь берётся как есть.

``collect_winws2_file_facts`` читает диск, поэтому вызывается только из
рабочего потока. Остальные функции чистые.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import ntpath
import os
from pathlib import Path

from .analysis import PresetAnalysis, analyze_winws2_text
from .values import parse_blob_value

_LIST_OPTIONS = frozenset({"hostlist", "hostlist-exclude", "ipset", "ipset-exclude"})
_AT_FILE_OPTIONS = frozenset({"lua-init", "wf-raw", "wf-raw-part", "wf-raw-filter"})

# Папки программы, из которых редактор подсказывает файлы.
LISTING_FOLDERS: dict[str, tuple[str, ...]] = {
    "lists": (".txt", ".lst", ".gz"),
    "bin": (".bin",),
    "lua": (".lua",),
    "windivert.filter": (".txt",),
}


@dataclass(frozen=True, slots=True)
class FileReference:
    option: str
    path: str
    line: int
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class FileFacts:
    """Что известно о файлах на диске на момент последней проверки."""

    root: str = ""
    existing: frozenset[str] = frozenset()
    missing: dict[str, str] = field(default_factory=dict)
    listings: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def status(self, path: str) -> bool | None:
        """True — файл есть, False — нет, None — ещё не проверялся."""
        key = reference_key(path)
        if key in self.existing:
            return True
        if key in self.missing:
            return False
        return None

    def expected_path(self, path: str) -> str:
        return self.missing.get(reference_key(path), "")


def _strip_quotes(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    return text


def reference_key(path: str) -> str:
    return _strip_quotes(path).replace("\\", "/").lower()


def _clean_path(value: str) -> str:
    text = _strip_quotes(value)
    if text.startswith("@"):
        text = _strip_quotes(text[1:])
    return text


def file_references(analysis: PresetAnalysis) -> list[FileReference]:
    references: list[FileReference] = []
    for option in analysis.options:
        spec = option.spec
        if spec is None or option.value is None:
            continue
        value = option.value
        start, end = option.value_start, option.value_end
        if spec.name in _LIST_OPTIONS:
            path = _clean_path(value)
        elif spec.name in _AT_FILE_OPTIONS:
            if not _strip_quotes(value).startswith("@"):
                continue
            path = _clean_path(value)
        elif spec.name == "blob":
            declaration = parse_blob_value(value)
            if declaration.error or not declaration.file:
                continue
            path = _clean_path(declaration.file)
            offset = value.find(declaration.file)
            if offset >= 0:
                start = option.value_start + offset
        else:
            continue
        if path:
            references.append(FileReference(spec.name, path, option.value_line, start, end))
    return references


def _is_absolute(path: str) -> bool:
    return os.path.isabs(path) or ntpath.isabs(path) or bool(ntpath.splitdrive(path)[0])


def resolve_reference(root: str, path: str) -> str:
    cleaned = _clean_path(path)
    if _is_absolute(cleaned):
        return os.path.normpath(cleaned)
    return os.path.normpath(os.path.join(str(root or ""), cleaned.replace("\\", os.sep).replace("/", os.sep)))


def _list_folder(root: str, folder: str, suffixes: tuple[str, ...]) -> tuple[str, ...]:
    directory = Path(root) / folder
    try:
        names = sorted(
            entry.name
            for entry in directory.iterdir()
            if entry.is_file() and entry.name.lower().endswith(suffixes)
        )
    except OSError:
        return ()
    return tuple(names)


def collect_winws2_file_facts(text: str, root: str, *, fragment: bool = False) -> FileFacts:
    """Проверяет файлы из пресета и собирает списки файлов для подсказок.

    Читает диск: вызывать только вне UI-потока.
    """
    root_text = str(root or "")
    existing: set[str] = set()
    missing: dict[str, str] = {}
    for reference in file_references(analyze_winws2_text(text, fragment=fragment)):
        key = reference_key(reference.path)
        if key in existing or key in missing:
            continue
        resolved = resolve_reference(root_text, reference.path)
        try:
            present = os.path.exists(resolved)
        except OSError:
            present = False
        if present:
            existing.add(key)
        else:
            missing[key] = resolved
    listings = {
        folder: _list_folder(root_text, folder, suffixes)
        for folder, suffixes in LISTING_FOLDERS.items()
    } if root_text else {}
    return FileFacts(root=root_text, existing=frozenset(existing), missing=missing, listings=listings)


__all__ = [
    "FileFacts",
    "FileReference",
    "LISTING_FOLDERS",
    "collect_winws2_file_facts",
    "file_references",
    "reference_key",
    "resolve_reference",
]
