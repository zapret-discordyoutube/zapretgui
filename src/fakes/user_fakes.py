"""Свои фейки пользователя: файлы в ``user/fakes/`` и строки в settings.sqlite3.

Фейк — набор байтов, который стратегия отправляет вместо настоящего пакета.
Поставляемые фейки описывает реестр (``system/fakes_catalog.sqlite3``, только
чтение). Пользователь может добавить свой: файл копируется в
``<установка>/user/fakes/<имя>.bin``, а имя, тип и описание записываются в
секцию ``user_fakes`` настроек. В пресете такой фейк объявляется так же явно,
как встроенный: ``--blob=<имя>:@user/fakes/<имя>.bin`` (путь относительно папки
установки — рабочей папки winws2).

Пресет остаётся точкой истины: при запуске ничего не подставляется. Строка
``--blob=`` своего фейка попадает в пресет только при явном выборе стратегии,
которая на него ссылается (см. ``profile.preset_blob_declarations``).

Модуль не зависит от Qt. Чтение и запись файлов и базы — только из фонового
потока.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from fakes.catalog_repository import FAKE_KINDS, FakesCatalog, load_fakes_catalog
from fakes.names import NFQWS2_BUILTIN_BLOBS, NFQWS2_LUA_RESERVED_NAMES


USER_FAKES_REFERENCE_PREFIX = "@user/fakes/"
USER_FAKE_FILE_SUFFIX = ".bin"
# Политика программы: фейк — один пакет, 64 КБ с большим запасом.
USER_FAKE_MAX_BYTES = 64 * 1024
USER_FAKE_NAME_MAX_LENGTH = 64
USER_FAKE_DESCRIPTION_MAX_LENGTH = 200

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LUA_FUNCTION_RE = re.compile(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE)
_LUA_TOP_LEVEL_GLOBAL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)", re.MULTILINE)
_HTTP_METHODS = (b"GET ", b"POST ", b"HEAD ", b"PUT ", b"DELETE ", b"OPTIONS ", b"PATCH ", b"CONNECT ")
_QUIC_VERSIONS = frozenset({0x00000001, 0x6B3343CF})


class UserFakeError(ValueError):
    """Понятная пользователю причина, по которой свой фейк не добавлен или не удалён."""


# ---------------------------------------------------------------- записи


@dataclass(frozen=True, slots=True)
class UserFakeEntry:
    """Свой фейк: имя blob-а и файл в ``user/fakes/``."""

    name: str
    file_name: str
    kind: str
    sni: str | None
    description: str
    created_at: str

    def blob_value(self) -> str:
        """Текст после ``--blob=<name>:`` — путь относительно папки установки."""
        return f"{USER_FAKES_REFERENCE_PREFIX}{self.file_name}"

    def blob_line(self) -> str:
        return f"--blob={self.name}:{self.blob_value()}"


def user_fake_file_name(name: str) -> str:
    """Имя файла своего фейка: совпадает с именем blob-а."""
    return f"{name}{USER_FAKE_FILE_SUFFIX}"


def _is_plain_bin_file_name(value: str) -> bool:
    if not value or any(sep in value for sep in ("/", "\\", ":")) or value in {".", ".."}:
        return False
    return value.lower().endswith(USER_FAKE_FILE_SUFFIX) and len(value) > len(USER_FAKE_FILE_SUFFIX)


def normalize_user_fake_row(name: str, raw: object) -> dict[str, Any] | None:
    """Нормальная строка секции ``user_fakes`` или None, если запись испорчена."""
    if not _NAME_RE.match(str(name or "")) or not isinstance(raw, Mapping):
        return None
    file_name = str(raw.get("file_name") or "").strip()
    if not _is_plain_bin_file_name(file_name):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in FAKE_KINDS:
        kind = "other"
    sni_value = raw.get("sni")
    sni = str(sni_value).strip() if isinstance(sni_value, str) else ""
    description = str(raw.get("description") or "").strip()[:USER_FAKE_DESCRIPTION_MAX_LENGTH]
    created_at = str(raw.get("created_at") or "").strip()
    return {
        "file_name": file_name,
        "kind": kind,
        "sni": sni or None,
        "description": description,
        "created_at": created_at,
    }


def user_fakes_from_section(section: object) -> tuple[UserFakeEntry, ...]:
    """Свои фейки из секции настроек ``user_fakes`` (в порядке добавления)."""
    fakes = section.get("fakes") if isinstance(section, Mapping) else None
    entries: list[UserFakeEntry] = []
    for name, raw in (fakes.items() if isinstance(fakes, Mapping) else ()):
        row = normalize_user_fake_row(str(name), raw)
        if row is None:
            continue
        entries.append(UserFakeEntry(name=str(name), **row))
    entries.sort(key=lambda entry: (entry.created_at, entry.name.casefold()))
    return tuple(entries)


def read_user_fakes() -> tuple[UserFakeEntry, ...]:
    from settings.store import get_user_fakes_settings

    return user_fakes_from_section(get_user_fakes_settings())


# ------------------------------------------------------ что внутри файла


def _parse_client_hello_sni(handshake: bytes) -> str | None:
    """SNI из TLS Handshake ClientHello; None, если его нет или данные битые."""
    data = handshake
    if len(data) < 4 or data[0] != 0x01:
        return None
    pos = 4 + 2 + 32  # тип+длина, версия, random
    if pos + 1 > len(data):
        return None
    pos += 1 + data[pos]  # session id
    if pos + 2 > len(data):
        return None
    pos += 2 + int.from_bytes(data[pos:pos + 2], "big")  # cipher suites
    if pos + 1 > len(data):
        return None
    pos += 1 + data[pos]  # compression methods
    if pos + 2 > len(data):
        return None
    extensions_end = min(len(data), pos + 2 + int.from_bytes(data[pos:pos + 2], "big"))
    pos += 2
    while pos + 4 <= extensions_end:
        ext_type = int.from_bytes(data[pos:pos + 2], "big")
        ext_len = int.from_bytes(data[pos + 2:pos + 4], "big")
        body = data[pos + 4:pos + 4 + ext_len]
        pos += 4 + ext_len
        if ext_type != 0:
            continue
        # server_name_list: длина(2), затем записи тип(1) длина(2) имя
        item = 2
        while item + 3 <= len(body):
            name_type = body[item]
            name_len = int.from_bytes(body[item + 1:item + 3], "big")
            name = body[item + 3:item + 3 + name_len]
            item += 3 + name_len
            if name_type == 0 and len(name) == name_len and name_len > 0:
                try:
                    return name.decode("ascii")
                except UnicodeDecodeError:
                    return None
        return None
    return None


def _http_host(data: bytes) -> str | None:
    head = data.split(b"\r\n\r\n", 1)[0]
    for line in head.split(b"\r\n")[1:]:
        key, sep, value = line.partition(b":")
        if sep and key.strip().lower() == b"host":
            try:
                return value.strip().decode("ascii") or None
            except UnicodeDecodeError:
                return None
    return None


def detect_fake_kind(data: bytes) -> tuple[str, str | None]:
    """Тип содержимого фейка и имя сервера внутри (SNI для TLS, Host для HTTP).

    Никогда не бросает исключение: непонятные данные — ``("other", None)``.
    """
    data = bytes(data or b"")
    if not data:
        return "other", None
    if not any(data):
        return "zeros", None
    # TLS: запись Handshake (0x16 0x03 xx) или голый Handshake ClientHello.
    if len(data) >= 5 and data[0] == 0x16 and data[1] == 0x03:
        record_len = int.from_bytes(data[3:5], "big")
        handshake = data[5:5 + record_len]
        if handshake[:1] == b"\x01":
            return "tls", _parse_client_hello_sni(handshake)
        return "tls", None
    if len(data) >= 6 and data[0] == 0x01 and data[4] == 0x03:
        return "tls", _parse_client_hello_sni(data)
    # QUIC: длинный заголовок с известной версией (v1, v2 и черновики ff0000xx).
    if len(data) >= 5 and (data[0] & 0xC0) == 0xC0:
        version = int.from_bytes(data[1:5], "big")
        if version in _QUIC_VERSIONS or (version >> 8) == 0xFF0000:
            return "quic", None
    if data.startswith(_HTTP_METHODS) and b" HTTP/1." in data.split(b"\r\n", 1)[0]:
        return "http", _http_host(data)
    return "other", None


# ------------------------------------------------------------ имена


def lua_init_names(install_root: Path) -> frozenset[str]:
    """Функции и верхнеуровневые глобалы файлов обязательного блока --lua-init.

    Файлы берутся из установки; отсутствующий файл просто пропускается.
    """
    from profile.winws2_preset_source import WINWS2_LUA_INIT_PATHS

    names: set[str] = set()
    for init_path in WINWS2_LUA_INIT_PATHS:
        path = Path(install_root) / init_path
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        names |= set(_LUA_FUNCTION_RE.findall(text))
        names |= set(_LUA_TOP_LEVEL_GLOBAL_RE.findall(text))
    return frozenset(names)


def _casefold_set(names: Iterable[str]) -> frozenset[str]:
    return frozenset(str(name).casefold() for name in names)


@dataclass(frozen=True, slots=True)
class FakeNameRules:
    """Какие имена заняты: реестр, движок и lua, свои фейки.

    Сравнение без учёта регистра: файл своего фейка называется по имени, а
    Windows не различает ``Foo.bin`` и ``foo.bin``.
    """

    shipped: frozenset[str] = frozenset()
    engine: frozenset[str] = frozenset()
    user: frozenset[str] = frozenset()
    _shipped_cf: frozenset[str] = field(init=False, repr=False, compare=False)
    _engine_cf: frozenset[str] = field(init=False, repr=False, compare=False)
    _user_cf: frozenset[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_shipped_cf", _casefold_set(self.shipped))
        object.__setattr__(self, "_engine_cf", _casefold_set(self.engine))
        object.__setattr__(self, "_user_cf", _casefold_set(self.user))

    def problem(self, name: str) -> str:
        """Почему имя нельзя взять; пустая строка — имя подходит."""
        value = str(name or "").strip()
        if not value:
            return "Введите имя фейка."
        if len(value) > USER_FAKE_NAME_MAX_LENGTH:
            return f"Имя слишком длинное: не больше {USER_FAKE_NAME_MAX_LENGTH} символов."
        if not _NAME_RE.match(value):
            return "Имя: только латинские буквы, цифры и «_», и не с цифры в начале."
        folded = value.casefold()
        if folded in self._shipped_cf:
            return "Такое имя уже есть среди встроенных фейков."
        if folded in self._engine_cf:
            return "Это имя уже занято в winws2 или его lua-скриптах."
        if folded in self._user_cf:
            return "Свой фейк с таким именем уже есть."
        return ""

    def suggest(self, file_name: str) -> str:
        """Свободное имя по имени файла: ``my file-1.bin`` -> ``my_file_1``."""
        stem = Path(str(file_name or "")).stem
        base = re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_")[:USER_FAKE_NAME_MAX_LENGTH - 4]
        if not base or base[0].isdigit():
            base = f"fake_{base}".rstrip("_")
        candidate = base
        index = 2
        while self.problem(candidate):
            candidate = f"{base}_{index}"
            index += 1
            if index > 999:
                return ""
        return candidate


def build_fake_name_rules(
    *,
    shipped_names: Iterable[str],
    lua_names: Iterable[str],
    user_names: Iterable[str],
) -> FakeNameRules:
    from profile.preset_blob_declarations import LUA_DEFINED_BLOB_NAMES

    engine = set(NFQWS2_BUILTIN_BLOBS) | set(NFQWS2_LUA_RESERVED_NAMES) | set(LUA_DEFINED_BLOB_NAMES)
    engine |= set(lua_names)
    return FakeNameRules(
        shipped=frozenset(shipped_names),
        engine=frozenset(engine),
        user=frozenset(user_names),
    )


# ------------------------------------------------------ добавить / удалить


def _default_update_settings(mutator):
    from settings.store import update_user_fakes_settings

    return update_user_fakes_settings(mutator)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_user_fake_source(source_path: str | Path) -> bytes:
    """Байты выбранного файла с проверкой политики размера."""
    path = Path(source_path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise UserFakeError(f"Не удалось открыть файл: {exc}") from exc
    if size > USER_FAKE_MAX_BYTES:
        raise UserFakeError(
            f"Файл слишком большой ({size} байт). Фейк — это один пакет, "
            f"допустимо не больше {USER_FAKE_MAX_BYTES // 1024} КБ."
        )
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise UserFakeError(f"Не удалось прочитать файл: {exc}") from exc
    if not data:
        raise UserFakeError("Файл пустой: в фейке должен быть хотя бы один байт.")
    if len(data) > USER_FAKE_MAX_BYTES:
        raise UserFakeError(
            f"Файл слишком большой ({len(data)} байт), допустимо не больше "
            f"{USER_FAKE_MAX_BYTES // 1024} КБ."
        )
    return data


def import_user_fake(
    source_path: str | Path,
    *,
    name: str,
    description: str = "",
    user_fakes_dir: Path,
    rules: FakeNameRules,
    update_settings: Callable = _default_update_settings,
    now: Callable[[], str] = _now_iso,
) -> UserFakeEntry:
    """Скопировать файл в ``user/fakes/<имя>.bin`` и записать строку в настройки.

    Файл сначала пишется во временный файл той же папки. Внутри одной
    транзакции настроек имя проверяется ещё раз, временный файл атомарно
    заменяет итоговый (``os.replace``) и добавляется строка. Если что-то пошло
    не так, транзакция откатывается, а временный файл удаляется.
    """
    clean_name = str(name or "").strip()
    problem = rules.problem(clean_name)
    if problem:
        raise UserFakeError(problem)
    data = read_user_fake_source(source_path)
    kind, sni = detect_fake_kind(data)
    if kind not in {"tls", "http"}:
        sni = None
    entry = UserFakeEntry(
        name=clean_name,
        file_name=user_fake_file_name(clean_name),
        kind=kind,
        sni=sni,
        description=str(description or "").strip()[:USER_FAKE_DESCRIPTION_MAX_LENGTH],
        created_at=now(),
    )

    target_dir = Path(user_fakes_dir)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UserFakeError(f"Не удалось создать папку своих фейков: {exc}") from exc
    try:
        handle = tempfile.NamedTemporaryFile(
            dir=target_dir, prefix=".import-", suffix=".tmp", delete=False
        )
    except OSError as exc:
        raise UserFakeError(f"Не удалось записать файл фейка: {exc}") from exc
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

        def _mutate(section: dict[str, Any]) -> None:
            fakes = section.get("fakes")
            if not isinstance(fakes, dict):
                fakes = {}
                section["fakes"] = fakes
            folded = clean_name.casefold()
            if any(str(existing).casefold() == folded for existing in fakes):
                raise UserFakeError("Свой фейк с таким именем уже есть.")
            os.replace(temp_path, target_dir / entry.file_name)
            fakes[clean_name] = {
                "file_name": entry.file_name,
                "kind": entry.kind,
                "sni": entry.sni,
                "description": entry.description,
                "created_at": entry.created_at,
            }

        update_settings(_mutate)
    except OSError as exc:
        raise UserFakeError(f"Не удалось сохранить фейк: {exc}") from exc
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
    return entry


def delete_user_fake(
    name: str,
    *,
    user_fakes_dir: Path,
    update_settings: Callable = _default_update_settings,
) -> None:
    """Удалить свой фейк: сначала строку настроек, потом файл.

    Встроенные фейки в секции ``user_fakes`` не хранятся, поэтому удалить их
    нельзя. Если файл уже пропал — это не ошибка.
    """
    clean_name = str(name or "").strip()
    removed: dict[str, str] = {}

    def _mutate(section: dict[str, Any]) -> None:
        fakes = section.get("fakes")
        if not isinstance(fakes, dict) or clean_name not in fakes:
            raise UserFakeError("Удалить можно только свой фейк.")
        row = fakes.pop(clean_name)
        removed["file_name"] = str((row or {}).get("file_name") or "") if isinstance(row, dict) else ""

    update_settings(_mutate)
    file_name = removed.get("file_name", "")
    if _is_plain_bin_file_name(file_name):
        try:
            (Path(user_fakes_dir) / file_name).unlink(missing_ok=True)
        except OSError:
            # Строки уже нет: оставшийся файл ни на что не влияет.
            pass


def open_user_fakes_folder(user_fakes_dir: Path) -> None:
    folder = Path(user_fakes_dir)
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(folder))  # type: ignore[attr-defined]  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(folder)])  # noqa: S603,S607 - действие пользователя


# ------------------------------------------------ общий реестр фейков


def merge_fakes_catalogs(
    shipped: FakesCatalog,
    user_entries: Iterable[UserFakeEntry],
    *,
    user_fakes_dir: Path | None = None,
) -> FakesCatalog:
    """Реестр + свои фейки. При совпадении имени главнее реестр.

    Если передана папка, свой фейк без файла пропускается: иначе выбор
    стратегии дописал бы в пресет строку, с которой winws2 не запустится.
    """
    entries: dict[str, object] = dict(shipped.entries)
    shipped_folded = _casefold_set(entries)
    for entry in user_entries:
        if entry.name.casefold() in shipped_folded or entry.name in entries:
            continue
        if user_fakes_dir is not None and not (Path(user_fakes_dir) / entry.file_name).is_file():
            continue
        entries[entry.name] = entry
    return FakesCatalog(
        entries=entries,
        catalog_version=shipped.catalog_version,
        content_sha256=shipped.content_sha256,
    )


def load_effective_fakes_catalog(application_paths=None) -> FakesCatalog:
    """Реестр фейков, которым пользуется явный выбор стратегии: реестр + свои.

    Ошибка чтения реестра поднимается как ``FakesCatalogError``. Диск и SQLite:
    вызывать вне GUI-потока.
    """
    if application_paths is None:
        from config.runtime_layout import APPLICATION_PATHS

        application_paths = APPLICATION_PATHS
    shipped = load_fakes_catalog(application_paths.fakes_catalog_database)
    return merge_fakes_catalogs(
        shipped,
        read_user_fakes(),
        user_fakes_dir=application_paths.user_fakes_dir,
    )


# ------------------------------------------------ данные страницы «Фейки»


def count_fake_usage(strategy_catalogs: Mapping[str, Mapping[str, object]]) -> dict[str, int]:
    """Сколько готовых стратегий winws2 ссылается на каждый фейк."""
    from profile.preset_blob_declarations import required_blob_names

    counts: dict[str, int] = {}
    for strategies in strategy_catalogs.values():
        for strategy in strategies.values():
            args = str(getattr(strategy, "args", "") or "")
            for name in required_blob_names(args.splitlines()):
                counts[name] = counts.get(name, 0) + 1
    return counts


@dataclass(frozen=True, slots=True)
class FakeRow:
    name: str
    file_label: str
    kind: str
    sni: str
    description: str
    used_by: int | None
    is_user: bool
    blob_line: str
    file_missing: bool = False


@dataclass(frozen=True, slots=True)
class FakesPageSnapshot:
    rows: tuple[FakeRow, ...] = ()
    rules: FakeNameRules = field(default_factory=FakeNameRules)
    catalog_version: str = ""
    catalog_error: str = ""
    usage_error: str = ""


def build_fakes_page_snapshot(
    *,
    application_paths,
    load_strategy_catalogs: Callable[[], Mapping[str, Mapping[str, object]]] | None,
) -> FakesPageSnapshot:
    """Строки таблицы «Фейки» и правила имён. Диск и SQLite — фоновый поток."""
    catalog_error = ""
    shipped_entries: Mapping[str, object] = {}
    catalog_version = ""
    try:
        shipped = load_fakes_catalog(application_paths.fakes_catalog_database)
        shipped_entries = shipped.entries
        catalog_version = shipped.catalog_version
    except Exception as exc:
        catalog_error = str(exc) or type(exc).__name__

    usage_error = ""
    usage: dict[str, int] | None = None
    if load_strategy_catalogs is not None:
        try:
            usage = count_fake_usage(load_strategy_catalogs())
        except Exception as exc:
            usage_error = str(exc) or type(exc).__name__

    user_entries = read_user_fakes()
    user_dir = Path(application_paths.user_fakes_dir)

    rows: list[FakeRow] = []
    for entry in shipped_entries.values():
        file_label = entry.file_name if entry.source_kind == "file" else f"0x{entry.hex_value}"
        rows.append(
            FakeRow(
                name=entry.name,
                file_label=str(file_label or ""),
                kind=entry.kind,
                sni=str(entry.sni or ""),
                description=str(entry.description or ""),
                used_by=(usage.get(entry.name, 0) if usage is not None else None),
                is_user=False,
                blob_line=entry.blob_line(),
            )
        )
    shipped_folded = _casefold_set(shipped_entries)
    for entry in user_entries:
        if entry.name.casefold() in shipped_folded:
            continue
        rows.append(
            FakeRow(
                name=entry.name,
                file_label=entry.file_name,
                kind=entry.kind,
                sni=str(entry.sni or ""),
                description=entry.description,
                used_by=None,
                is_user=True,
                blob_line=entry.blob_line(),
                file_missing=not (user_dir / entry.file_name).is_file(),
            )
        )

    rules = build_fake_name_rules(
        shipped_names=shipped_entries.keys(),
        lua_names=lua_init_names(application_paths.root),
        user_names=(entry.name for entry in user_entries),
    )
    return FakesPageSnapshot(
        rows=tuple(rows),
        rules=rules,
        catalog_version=catalog_version,
        catalog_error=catalog_error,
        usage_error=usage_error,
    )


def current_fake_name_rules(application_paths) -> FakeNameRules:
    """Свежие правила имён для последней проверки перед добавлением."""
    try:
        shipped_names = tuple(load_fakes_catalog(application_paths.fakes_catalog_database).entries)
    except Exception as exc:
        raise UserFakeError(f"Реестр фейков недоступен: {exc}") from exc
    return build_fake_name_rules(
        shipped_names=shipped_names,
        lua_names=lua_init_names(application_paths.root),
        user_names=(entry.name for entry in read_user_fakes()),
    )


__all__ = [
    "USER_FAKES_REFERENCE_PREFIX",
    "USER_FAKE_MAX_BYTES",
    "FakeNameRules",
    "FakeRow",
    "FakesPageSnapshot",
    "UserFakeEntry",
    "UserFakeError",
    "build_fake_name_rules",
    "build_fakes_page_snapshot",
    "count_fake_usage",
    "current_fake_name_rules",
    "delete_user_fake",
    "detect_fake_kind",
    "import_user_fake",
    "load_effective_fakes_catalog",
    "lua_init_names",
    "merge_fakes_catalogs",
    "normalize_user_fake_row",
    "open_user_fakes_folder",
    "read_user_fake_source",
    "read_user_fakes",
    "user_fake_file_name",
    "user_fakes_from_section",
]
