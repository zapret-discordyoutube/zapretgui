from __future__ import annotations

import re
from pathlib import Path

from core.paths import AppPaths
from settings.mode import ENGINE_WINWS1, ENGINE_WINWS2
from settings.store import get_user_profiles_settings, update_user_profiles_settings

from lists.core.layered_files import (
    create_profile_user_list_file,
    delete_profile_user_list_file,
    rename_profile_user_list_file,
    safe_list_file_name,
    write_profile_user_list_text,
)

from ..models import EngineName, Profile
from ..parser import parse_preset_text
from ..template_catalog import load_profile_templates


_PORTS_RE = re.compile(r"^[0-9*,~-]+$")
_SLUG_RE = re.compile(r"[^a-z0-9а-яё]+", flags=re.IGNORECASE)
_DEFAULT_HOSTLIST_TEXT = "www.example.com\n"
_RU_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
})


def create_user_profile(paths: AppPaths, *, name: str, protocol: str, ports: str) -> str:
    clean_name = _clean_name(name)
    clean_protocol = _clean_protocol(protocol)
    clean_ports = _clean_ports(ports, clean_protocol)
    system_names = _system_profile_names(paths)
    lists_root = Path(paths.user_root) / "lists"
    created: dict[str, str] = {}

    def _mutate(section: dict) -> None:
        profiles = dict(section.get("profiles") or {})
        _validate_unique_profile_name(profiles, system_names, clean_name)
        profile_id = _unique_profile_id(profiles, _slugify(clean_name))
        # Имя файлов уникально среди файлов ВСЕХ профилей, а не только id:
        # профиль, переименованный из «a» в «b», уже владеет b.txt — новый
        # профиль «b» делил бы с ним списки, а удаление одного стирало бы их у обоих.
        file_stem = _unique_file_stem(profiles, profile_id)
        hostlist_had_entries = _user_list_file_has_entries(lists_root, f"{file_stem}.txt")
        create_profile_user_list_file(lists_root, f"{file_stem}.txt")
        create_profile_user_list_file(lists_root, f"ipset-{file_stem}.txt")
        if not hostlist_had_entries:
            write_profile_user_list_text(lists_root, f"{file_stem}.txt", _DEFAULT_HOSTLIST_TEXT)
        profiles[profile_id] = {
            "name": clean_name,
            "protocol": clean_protocol,
            "ports": clean_ports,
            "hostlist": f"lists/{file_stem}.txt",
            "ipset": f"lists/ipset-{file_stem}.txt",
        }
        section["version"] = 1
        section["profiles"] = profiles
        created["profile_id"] = profile_id

    update_user_profiles_settings(_mutate)
    return created["profile_id"]


def update_user_profile(
    paths: AppPaths,
    profile_id: str,
    *,
    name: str,
    protocol: str,
    ports: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Возвращает (строка до правки, строка после правки)."""
    clean_profile_id = str(profile_id or "").strip()
    if not clean_profile_id:
        raise ValueError("Profile id не должен быть пустым")
    clean_name = _clean_name(name)
    clean_protocol = _clean_protocol(protocol)
    clean_ports = _clean_ports(ports, clean_protocol)
    system_names = _system_profile_names(paths)
    rows: dict[str, dict[str, str]] = {}

    def _mutate(section: dict) -> None:
        profiles = dict(section.get("profiles") or {})
        row = profiles.get(clean_profile_id)
        if not isinstance(row, dict):
            raise ValueError("Пользовательский profile не найден")
        _validate_unique_profile_name(profiles, system_names, clean_name, exclude_profile_id=clean_profile_id)
        old_row = _row_fields(row)
        new_stem = _unique_file_stem(profiles, _slugify(clean_name), exclude_profile_id=clean_profile_id)
        updated_row = {
            "name": clean_name,
            "protocol": clean_protocol,
            "ports": clean_ports,
            "hostlist": f"lists/{new_stem}.txt",
            "ipset": f"lists/ipset-{new_stem}.txt",
        }
        _rename_user_list_file(paths, old_row["hostlist"], updated_row["hostlist"])
        _rename_user_list_file(paths, old_row["ipset"], updated_row["ipset"])
        profiles[clean_profile_id] = updated_row
        section["version"] = 1
        section["profiles"] = profiles
        rows["old"] = old_row
        rows["new"] = dict(updated_row)

    update_user_profiles_settings(_mutate)
    return rows["old"], rows["new"]


def delete_user_profile(paths: AppPaths, profile_id: str) -> dict[str, str]:
    """Удаляет пользовательский profile и его файлы; возвращает удалённую строку."""
    clean_profile_id = str(profile_id or "").strip()
    if not clean_profile_id:
        raise ValueError("Profile id не должен быть пустым")
    removed: dict[str, dict[str, str]] = {}

    def _mutate(section: dict) -> None:
        profiles = dict(section.get("profiles") or {})
        row = profiles.pop(clean_profile_id, None)
        if not isinstance(row, dict):
            raise ValueError("Пользовательский profile не найден")
        section["version"] = 1
        section["profiles"] = profiles
        removed["row"] = _row_fields(row)

    update_user_profiles_settings(_mutate)
    row = removed["row"]
    _delete_user_list_file(paths, row["hostlist"])
    _delete_user_list_file(paths, row["ipset"])
    return row


def _log_skipped_user_profile(profile_id: object, exc: Exception) -> None:
    try:
        from log.log import log

        log(f"Пользовательский profile {profile_id} пропущен: повреждённая запись ({exc})", "WARNING")
    except Exception:
        pass


def _row_fields(row: dict) -> dict[str, str]:
    return {
        field: str(row.get(field) or "").strip()
        for field in ("name", "protocol", "ports", "hostlist", "ipset")
    }


def load_user_profile_templates(paths: AppPaths, engine: EngineName | str) -> dict[str, Profile]:
    normalized_engine = str(engine or "").strip().lower()
    profiles = get_user_profiles_settings().get("profiles") or {}
    result: dict[str, Profile] = {}
    for profile_id, row in sorted(profiles.items()):
        # Одна повреждённая запись в настройках не должна ронять загрузку
        # всех шаблонов (а с ней и список профилей).
        try:
            text = _profile_text(row, engine=normalized_engine)
            if not text:
                continue
            preset = parse_preset_text(text, engine=normalized_engine, source_name="user_profiles")
        except Exception as exc:
            _log_skipped_user_profile(profile_id, exc)
            continue
        if preset.profiles:
            result[f"user:{profile_id}"] = preset.profiles[0]
    return result


def _profile_text(row: object, *, engine: str) -> str:
    if not isinstance(row, dict):
        return ""
    name = _clean_name(row.get("name"))
    protocol = _clean_protocol(row.get("protocol"))
    ports = _clean_ports(row.get("ports"), protocol)
    hostlist = str(row.get("hostlist") or "").strip()
    if not hostlist:
        return ""
    name_directive = "--comment" if engine == ENGINE_WINWS1 else "--name"
    lines = [
        f"{name_directive}={name}",
        f"--filter-{protocol}={ports}",
        f"--hostlist={hostlist}",
    ]
    # Новый profile ничего не делает с трафиком, пока пользователь сам не
    # выберет стратегию. winws2: явный `--lua-desync=pass`. winws1: profile
    # без `--dpi-desync` — в nfqws1 режим по умолчанию DESYNC_NONE, пакет
    # уходит как есть (тот же pass); подставлять «первую стратегию каталога»
    # значило бы молча выбрать её за пользователя.
    if engine == ENGINE_WINWS2:
        lines.append("--lua-desync=pass")
    return "\n".join(lines) + "\n"


def _clean_name(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Название profile не должно быть пустым")
    return text


def _clean_protocol(value: object) -> str:
    protocol = str(value or "").strip().lower()
    if protocol not in {"tcp", "udp", "l7"}:
        raise ValueError("Тип profile должен быть TCP, UDP или L7")
    return protocol


def validate_user_profile_filter(protocol: object, value: object) -> str | None:
    clean_protocol = str(protocol or "").strip().lower()
    text = str(value or "").strip().replace(" ", "")
    if not text:
        return "Значение фильтра profile не должно быть пустым"
    if clean_protocol == "l7":
        if not re.match(r"^[a-z0-9_,.-]+$", text, flags=re.IGNORECASE):
            return "L7 можно указать словами через запятую, например stun,discord"
        return None
    if clean_protocol not in {"tcp", "udp"}:
        return "Тип profile должен быть TCP, UDP или L7"
    if not _PORTS_RE.match(text):
        return "Порты можно указать числами, диапазонами и запятыми"
    for raw_token in text.split(","):
        error = _validate_port_token(raw_token)
        if error:
            return error
    return None


def _validate_port_token(token: str) -> str | None:
    if not token:
        return "Порты можно указать числами, диапазонами и запятыми"
    if token.startswith("~"):
        token = token[1:]
        if not token:
            return "Порты можно указать числами, диапазонами и запятыми"
        if token == "*":
            return "Порты можно указать числами, диапазонами и запятыми"
    if token == "*":
        return None
    if "-" in token:
        parts = token.split("-")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return "Порты можно указать числами, диапазонами и запятыми"
        start = _parse_port_number(parts[0])
        end = _parse_port_number(parts[1])
        if start is None or end is None:
            return "Порт должен быть от 1 до 65535."
        if start > end:
            return "Начало диапазона портов не должно быть больше конца."
        return None
    if _parse_port_number(token) is None:
        return "Порт должен быть от 1 до 65535."
    return None


def _parse_port_number(value: str) -> int | None:
    if not value.isdigit():
        return None
    port = int(value)
    if port < 1 or port > 65535:
        return None
    return port


def _clean_ports(value: object, protocol: str = "tcp") -> str:
    text = str(value or "").strip().replace(" ", "")
    error = validate_user_profile_filter(protocol, text)
    if error:
        raise ValueError(error)
    return text


def _slugify(value: str) -> str:
    text = value.strip().lower().translate(_RU_TRANSLIT)
    text = _SLUG_RE.sub("-", text).strip("-")
    text = text or "profile"
    # Имя профиля «con», «nul», «com1»… дало бы имя файла-устройства Windows:
    # такой список не создать. Меняем основу, а не отказываем пользователю.
    if not safe_list_file_name(f"{text}.txt"):
        text = f"{text}-profile"
    return text


def _unique_profile_id(profiles: dict, base: str) -> str:
    candidate = base
    counter = 2
    while candidate in profiles:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def _unique_file_stem(profiles: dict, base: str, *, exclude_profile_id: str = "") -> str:
    used: set[str] = set()
    excluded = str(exclude_profile_id or "").strip()
    for profile_id, row in profiles.items():
        if str(profile_id or "").strip() == excluded or not isinstance(row, dict):
            continue
        for field in ("hostlist", "ipset"):
            value = str(row.get(field) or "").replace("\\", "/").strip()
            if not value:
                continue
            name = Path(value).name
            stem = name[:-4] if name.lower().endswith(".txt") else Path(name).stem
            if stem.startswith("ipset-"):
                stem = stem[6:]
            if stem:
                used.add(stem.casefold())
    candidate = base
    counter = 2
    while candidate.casefold() in used:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def _validate_unique_profile_name(
    profiles: dict,
    system_names: set[str],
    name: str,
    *,
    exclude_profile_id: str = "",
) -> None:
    wanted = _name_key(name)
    if not wanted:
        return

    excluded = str(exclude_profile_id or "").strip()
    for profile_id, row in profiles.items():
        if str(profile_id or "").strip() == excluded:
            continue
        if isinstance(row, dict) and _name_key(row.get("name")) == wanted:
            raise ValueError("Пользовательский profile с таким названием уже есть")

    for system_name in system_names:
        if _name_key(system_name) == wanted:
            raise ValueError("Такое название уже занято системным profile-ом")


def _system_profile_names(paths: AppPaths) -> set[str]:
    names: set[str] = set()
    for engine in (ENGINE_WINWS2, ENGINE_WINWS1):
        for profile in load_profile_templates(paths, engine).values():
            name = str(getattr(profile, "name", "") or getattr(profile, "display_name", "") or "").strip()
            if name:
                names.add(name)
    return names


def _name_key(value: object) -> str:
    return str(value or "").strip().casefold()


def _user_list_file_has_entries(lists_root: Path, file_name: str) -> bool:
    """Осиротевший файл с доменами (профиль потерян, файл остался) не должен
    перезаписываться дефолтом при повторном создании профиля с тем же именем."""
    path = lists_root / "user" / file_name
    try:
        if not path.is_file():
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # Файл есть, но не читается (антивирус/индексатор): считаем, что записи
        # там ЕСТЬ — перезапись дефолтом при ошибке чтения была бы потерей.
        return True
    placeholder = _DEFAULT_HOSTLIST_TEXT.strip().casefold()
    return any(
        line.strip() and not line.strip().startswith("#") and line.strip().casefold() != placeholder
        for line in text.splitlines()
    )


def _rename_user_list_file(paths: AppPaths, old_value: str, new_value: str) -> None:
    old_name = safe_list_file_name(old_value)
    new_name = safe_list_file_name(new_value)
    if not new_name:
        return
    lists_root = Path(paths.user_root) / "lists"
    rename_profile_user_list_file(lists_root, old_name, new_name)


def _delete_user_list_file(paths: AppPaths, value: str) -> None:
    name = safe_list_file_name(value)
    if not name:
        return
    delete_profile_user_list_file(Path(paths.user_root) / "lists", name)
