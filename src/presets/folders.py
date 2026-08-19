from __future__ import annotations

from typing import Any

from folders.defaults import COMMON_FOLDER_KEY, PINNED_FOLDER_KEY, build_default_preset_folders, classify_preset_folder
from folders.ordering import build_folder_rows, plan_item_move
from folders.store import FolderLibraryStore, normalize_folder_state
from settings import store as settings_store
from settings.mode import ENGINE_WINWS1, ENGINE_WINWS2


def load_preset_folder_state(scope_key: str) -> dict[str, Any]:
    scope = _normalize_scope(scope_key)
    folders = settings_store.get_folders_settings()
    presets = folders.get("presets", {}) if isinstance(folders, dict) else {}
    raw_state = presets.get(scope) if isinstance(presets, dict) else None
    state = normalize_folder_state(raw_state, build_default_preset_folders(scope))
    _repair_virtual_pinned_item_folders(state)
    return state


def save_preset_folder_state(scope_key: str, state: dict[str, Any]) -> dict[str, Any]:
    scope = _normalize_scope(scope_key)
    default_state = build_default_preset_folders(scope)
    next_state = normalize_folder_state(state, default_state)
    _repair_virtual_pinned_item_folders(next_state)
    folders = settings_store.get_folders_settings()
    presets = folders.get("presets", {}) if isinstance(folders, dict) else {}
    if not isinstance(presets, dict):
        presets = {}
    current_state = normalize_folder_state(presets.get(scope), default_state)
    _repair_virtual_pinned_item_folders(current_state)
    if current_state == next_state:
        return current_state
    presets[scope] = next_state
    folders["presets"] = presets
    return settings_store.set_folders_settings(folders)["presets"][scope]


def create_preset_folder(scope_key: str, name: str) -> str:
    state = load_preset_folder_state(scope_key)
    scope = _normalize_scope(scope_key)
    store = FolderLibraryStore(state, default_state=build_default_preset_folders(scope))
    folder_key = store.create_folder_after(name, COMMON_FOLDER_KEY)
    save_preset_folder_state(scope_key, store.to_dict())
    return folder_key


def rename_preset_folder(scope_key: str, folder_key: str, name: str) -> bool:
    state = load_preset_folder_state(scope_key)
    scope = _normalize_scope(scope_key)
    store = FolderLibraryStore(state, default_state=build_default_preset_folders(scope))
    if not store.rename_folder(folder_key, name):
        return False
    save_preset_folder_state(scope_key, store.to_dict())
    return True


def delete_preset_folder(scope_key: str, folder_key: str) -> bool:
    state = load_preset_folder_state(scope_key)
    scope = _normalize_scope(scope_key)
    store = FolderLibraryStore(state, default_state=build_default_preset_folders(scope))
    if not store.delete_folder(folder_key):
        return False
    save_preset_folder_state(scope_key, store.to_dict())
    return True


def move_preset_folder_by_step(scope_key: str, folder_key: str, direction: int) -> bool:
    state = load_preset_folder_state(scope_key)
    scope = _normalize_scope(scope_key)
    store = FolderLibraryStore(state, default_state=build_default_preset_folders(scope))
    if not store.move_folder_by_step(folder_key, direction):
        return False
    save_preset_folder_state(scope_key, store.to_dict())
    return True


def set_preset_folder_collapsed(scope_key: str, folder_key: str, collapsed: bool) -> bool:
    state = load_preset_folder_state(scope_key)
    if str(folder_key or "").strip() == PINNED_FOLDER_KEY:
        next_collapsed = bool(collapsed)
        folder = state.setdefault("folders", {}).get(PINNED_FOLDER_KEY)
        if not isinstance(folder, dict) and not next_collapsed:
            return False
        if isinstance(folder, dict) and bool(folder.get("collapsed", False)) == next_collapsed:
            return False
        state.setdefault("folders", {})[PINNED_FOLDER_KEY] = {
            "name": "Закрепленные",
            "order": -1,
            "collapsed": next_collapsed,
            "system": True,
        }
        save_preset_folder_state(scope_key, state)
        return True
    scope = _normalize_scope(scope_key)
    store = FolderLibraryStore(state, default_state=build_default_preset_folders(scope))
    if not store.set_folder_collapsed(folder_key, collapsed):
        return False
    save_preset_folder_state(scope_key, store.to_dict())
    return True


def reset_preset_folders(scope_key: str) -> dict[str, Any] | bool:
    scope = _normalize_scope(scope_key)
    default_state = build_default_preset_folders(scope)
    current_state = load_preset_folder_state(scope)
    if current_state == normalize_folder_state(default_state, default_state):
        return False
    return save_preset_folder_state(scope, default_state)


def move_preset_to_folder(
    scope_key: str,
    file_name: str,
    folder_key: str,
    *,
    live_items: list[dict[str, Any]] | None = None,
) -> bool:
    state = load_preset_folder_state(scope_key)
    key = str(file_name or "").strip()
    if not key:
        return False
    folders = state.get("folders", {})
    target_folder = str(folder_key or "").strip() or COMMON_FOLDER_KEY
    if target_folder == PINNED_FOLDER_KEY:
        # `pinned` is a virtual service folder, not a real destination. The
        # pin button owns that state transition; an ordinary folder move must
        # never make an item disappear from the visible list.
        return False
    if not isinstance(folders, dict) or target_folder not in folders:
        target_folder = COMMON_FOLDER_KEY
    if _plan_and_save_preset_move(
        scope_key,
        state,
        live_items,
        action="folder",
        source_key=key,
        destination_folder_key=target_folder,
    ):
        return True
    # Пресет без meta, уже отображаемый в целевой папке: отображаемый порядок
    # не меняется (для планировщика это no-op), но привязку нужно
    # материализовать — иначе повторные операции считают её неизвестной.
    items = state.get("items") if isinstance(state.get("items"), dict) else {}
    if key in items:
        return False
    next_state = normalize_folder_state(state, build_default_preset_folders(_normalize_scope(scope_key)))
    next_state.setdefault("items", {})[key] = {"folder_key": target_folder, "order": None, "rating": 0}
    save_preset_folder_state(scope_key, next_state)
    return True


def move_preset_before(
    scope_key: str,
    source_file_name: str,
    destination_file_name: str,
    *,
    destination_folder_key: str = "",
    live_items: list[dict[str, Any]] | None = None,
) -> bool:
    return _plan_and_save_preset_move(
        scope_key,
        load_preset_folder_state(scope_key),
        live_items,
        action="before",
        source_key=str(source_file_name or "").strip(),
        destination_key=str(destination_file_name or "").strip(),
        destination_folder_key=str(destination_folder_key or "").strip(),
    )


def move_preset_after(
    scope_key: str,
    source_file_name: str,
    destination_file_name: str,
    *,
    destination_folder_key: str = "",
    live_items: list[dict[str, Any]] | None = None,
) -> bool:
    return _plan_and_save_preset_move(
        scope_key,
        load_preset_folder_state(scope_key),
        live_items,
        action="after",
        source_key=str(source_file_name or "").strip(),
        destination_key=str(destination_file_name or "").strip(),
        destination_folder_key=str(destination_folder_key or "").strip(),
    )


def move_preset_to_end(
    scope_key: str,
    file_name: str,
    *,
    live_items: list[dict[str, Any]] | None = None,
) -> bool:
    return _plan_and_save_preset_move(
        scope_key,
        load_preset_folder_state(scope_key),
        live_items,
        action="end",
        source_key=str(file_name or "").strip(),
    )


def move_preset_by_step(
    scope_key: str,
    file_name: str,
    direction: int,
    *,
    live_items: list[dict[str, Any]],
) -> bool:
    source = str(file_name or "").strip()
    if not source:
        return False
    step = 1 if int(direction or 0) > 0 else -1
    state = load_preset_folder_state(scope_key)
    rows = build_folder_rows(
        state,
        live_items=live_items,
        include_pinned_folder=True,
    )
    item_rows = [
        row
        for row in rows
        if row.get("kind") == "item" and str(row.get("key") or "").strip()
    ]
    source_row = next(
        (row for row in item_rows if str(row.get("key") or "").strip() == source),
        None,
    )
    if source_row is None:
        return False

    # `Закрепленные` is a virtual group above real folders. Its items must
    # move only among other pinned items; feeding a pinned source into the
    # ordinary planner would silently change its real folder and the next
    # render would put it back at the top.
    source_is_pinned = bool(source_row.get("pinned", False))
    if source_is_pinned:
        pinned_rows = [row for row in item_rows if bool(row.get("pinned", False))]
        return _move_pinned_preset_by_step(
            scope_key,
            state,
            pinned_rows,
            source,
            step,
        )

    # Обычная последовательность начинается после виртуальной pinned-группы.
    # Первый обычный preset не может «подняться» внутрь Закреплённых без
    # отдельного действия закрепления.
    ordered_rows = [row for row in item_rows if not bool(row.get("pinned", False))]
    ordered = [str(row.get("key") or "").strip() for row in ordered_rows]
    if source not in ordered:
        return False
    index = ordered.index(source)
    target_index = index + step
    if target_index < 0 or target_index >= len(ordered):
        return False
    if step < 0:
        return move_preset_before(scope_key, source, ordered[target_index], live_items=live_items)

    without_source = [key for key in ordered if key != source]
    target = ordered[target_index]
    after_target_index = without_source.index(target) + 1
    if after_target_index < len(without_source):
        return move_preset_before(scope_key, source, without_source[after_target_index], live_items=live_items)
    target_row = ordered_rows[target_index]
    target_folder = str(target_row.get("folder_key") or "").strip()
    return move_preset_after(
        scope_key,
        source,
        target,
        destination_folder_key=target_folder,
        live_items=live_items,
    )


def _plan_and_save_preset_move(
    scope_key: str,
    state: dict[str, Any],
    live_items: list[dict[str, Any]] | None,
    *,
    action: str,
    source_key: str,
    destination_key: str = "",
    destination_folder_key: str = "",
) -> bool:
    """Единый путь перемещения пресетов: та же база (отображаемый порядок) и
    тот же планировщик, что у папок профилей."""
    if str(destination_folder_key or "").strip() == PINNED_FOLDER_KEY:
        return False
    plan_live_items = _live_items_for_plan(
        state,
        live_items,
        required_keys=(source_key, destination_key),
    )
    source_is_pinned = _preset_is_pinned(state, plan_live_items, source_key)
    destination_is_pinned = _preset_is_pinned(state, plan_live_items, destination_key)
    if source_is_pinned or destination_is_pinned:
        if action in {"before", "after"} and source_is_pinned and destination_is_pinned:
            return _move_pinned_preset_relative(
                scope_key,
                state,
                plan_live_items,
                action=action,
                source_key=source_key,
                destination_key=destination_key,
            )
        if action == "end" and source_is_pinned:
            return _move_pinned_preset_relative(
                scope_key,
                state,
                plan_live_items,
                action=action,
                source_key=source_key,
            )
        # Границу виртуальной группы меняет только явное закрепление или
        # открепление. Обычный folder/before/after не должен давать ложный
        # успех и сохранять скрытый порядок.
        return False
    planned = plan_item_move(
        state,
        plan_live_items,
        action=action,
        source_key=source_key,
        destination_key=destination_key,
        destination_folder_key=destination_folder_key,
    )
    if planned is None:
        return False
    save_preset_folder_state(scope_key, planned)
    return True


def _move_pinned_preset_by_step(
    scope_key: str,
    state: dict[str, Any],
    pinned_rows: list[dict[str, Any]],
    source: str,
    step: int,
) -> bool:
    ordered = [str(row.get("key") or "").strip() for row in pinned_rows]
    if source not in ordered:
        return False
    source_index = ordered.index(source)
    target_index = source_index + step
    if target_index < 0 or target_index >= len(ordered):
        return False

    ordered[source_index], ordered[target_index] = ordered[target_index], ordered[source_index]
    return _save_pinned_preset_order(scope_key, state, pinned_rows, ordered)


def _move_pinned_preset_relative(
    scope_key: str,
    state: dict[str, Any],
    live_items: list[dict[str, Any]],
    *,
    action: str,
    source_key: str,
    destination_key: str = "",
) -> bool:
    rows = build_folder_rows(
        state,
        live_items=live_items,
        include_pinned_folder=True,
    )
    pinned_rows = [
        row
        for row in rows
        if row.get("kind") == "item" and bool(row.get("pinned", False))
    ]
    ordered = [str(row.get("key") or "").strip() for row in pinned_rows]
    source = str(source_key or "").strip()
    destination = str(destination_key or "").strip()
    if source not in ordered:
        return False

    next_order = [key for key in ordered if key != source]
    if action == "end":
        next_order.append(source)
    elif action in {"before", "after"} and destination in next_order:
        insert_at = next_order.index(destination) + (1 if action == "after" else 0)
        next_order.insert(insert_at, source)
    else:
        return False
    if next_order == ordered:
        return False
    return _save_pinned_preset_order(scope_key, state, pinned_rows, next_order)


def _save_pinned_preset_order(
    scope_key: str,
    state: dict[str, Any],
    pinned_rows: list[dict[str, Any]],
    ordered: list[str],
) -> bool:
    next_state = normalize_folder_state(state, build_default_preset_folders(_normalize_scope(scope_key)))
    _repair_virtual_pinned_item_folders(next_state)
    items = next_state.setdefault("items", {})
    rows_by_key = {str(row.get("key") or "").strip(): row for row in pinned_rows}
    for order, key in enumerate(ordered):
        if not key:
            continue
        meta = items.get(key)
        if not isinstance(meta, dict):
            row = rows_by_key.get(key) or {}
            meta = {
                "folder_key": str(row.get("folder_key") or COMMON_FOLDER_KEY).strip() or COMMON_FOLDER_KEY,
                "order": None,
                "rating": int(row.get("rating", 0) or 0),
                "pinned": True,
            }
            items[key] = meta
        # The virtual reorder must not rehome an item to another real folder.
        meta["order"] = order

    if next_state == state:
        return False
    save_preset_folder_state(scope_key, next_state)
    return True


def _preset_is_pinned(
    state: dict[str, Any],
    live_items: list[dict[str, Any]],
    item_key: str,
) -> bool:
    key = str(item_key or "").strip()
    if not key:
        return False
    items = state.get("items") if isinstance(state, dict) else None
    meta = items.get(key) if isinstance(items, dict) else None
    if isinstance(meta, dict) and bool(meta.get("pinned", False)):
        return True
    return any(
        str(item.get("key") or "").strip() == key and bool(item.get("pinned", False))
        for item in live_items
    )


def _live_items_for_plan(
    state: dict[str, Any],
    live_items: list[dict[str, Any]] | None,
    *,
    required_keys: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for live_item in live_items or []:
        key = str(live_item.get("key") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(live_item)
    # Пресеты, известные только по meta (нет в live-списке), всё равно должны
    # участвовать в базе порядка — иначе перенумерация их «перепрыгнет».
    items = state.get("items") if isinstance(state, dict) else None
    if isinstance(items, dict):
        for key in items:
            clean_key = str(key or "").strip()
            if clean_key and clean_key not in seen:
                seen.add(clean_key)
                result.append({"key": clean_key, "name": clean_key})
    # Источник/цель перемещения могут ещё не иметь meta (первое действие
    # с пресетом) — они обязаны присутствовать в базе.
    for key in required_keys:
        clean_key = str(key or "").strip()
        if clean_key and clean_key not in seen:
            seen.add(clean_key)
            result.append({"key": clean_key, "name": clean_key})
    return result


def get_preset_item_meta(scope_key: str, file_name: str) -> dict[str, Any]:
    state = load_preset_folder_state(scope_key)
    key = str(file_name or "").strip()
    meta = state.get("items", {}).get(key) if key else None
    if not isinstance(meta, dict):
        return {"folder_key": COMMON_FOLDER_KEY, "order": None, "rating": 0}
    return {
        "folder_key": str(meta.get("folder_key") or COMMON_FOLDER_KEY),
        "order": meta.get("order"),
        "rating": int(meta.get("rating", 0) or 0),
        **({"pinned": True} if bool(meta.get("pinned", False)) else {}),
    }


def set_preset_rating(scope_key: str, file_name: str, rating: int, *, display_name: str = "") -> bool:
    state = load_preset_folder_state(scope_key)
    key = str(file_name or "").strip()
    if not key:
        return False
    try:
        normalized = int(rating)
    except Exception:
        normalized = 0
    next_rating = max(0, min(10, normalized))
    items = state.setdefault("items", {})
    meta = items.get(key)
    if not isinstance(meta, dict):
        if next_rating == 0:
            return False
        meta = _ensure_item_meta(state, key, str(display_name or key), _normalize_scope(scope_key))
    if int(meta.get("rating", 0) or 0) == next_rating:
        return False
    meta["rating"] = next_rating
    save_preset_folder_state(scope_key, state)
    return True


def toggle_preset_pin(scope_key: str, file_name: str, *, display_name: str = "") -> bool:
    meta = get_preset_item_meta(scope_key, file_name)
    next_value = not bool(meta.get("pinned", False))
    set_preset_pin(scope_key, file_name, next_value, display_name=display_name)
    return next_value


def set_preset_pin(scope_key: str, file_name: str, pinned: bool, *, display_name: str = "") -> bool:
    state = load_preset_folder_state(scope_key)
    key = str(file_name or "").strip()
    if not key:
        return False
    next_pinned = bool(pinned)
    items = state.setdefault("items", {})
    meta = items.get(key)
    if not isinstance(meta, dict):
        if not next_pinned:
            return False
        meta = _ensure_item_meta(state, key, str(display_name or key), _normalize_scope(scope_key))
    if bool(meta.get("pinned", False)) == next_pinned:
        return False
    if next_pinned:
        meta["pinned"] = True
    else:
        meta.pop("pinned", None)
    save_preset_folder_state(scope_key, state)
    return True


def rename_preset_item_meta(scope_key: str, old_file_name: str, new_file_name: str) -> bool:
    old_key = str(old_file_name or "").strip()
    new_key = str(new_file_name or "").strip()
    if not old_key or not new_key or old_key == new_key:
        return False
    state = load_preset_folder_state(scope_key)
    items = state.setdefault("items", {})
    raw = items.pop(old_key, None)
    if not isinstance(raw, dict):
        return False
    items[new_key] = raw
    save_preset_folder_state(scope_key, state)
    return True


def copy_preset_item_meta(
    scope_key: str,
    source_file_name: str,
    new_file_name: str,
    *,
    reset_pin: bool = True,
    reset_rating: bool = True,
) -> bool:
    source = str(source_file_name or "").strip()
    new_key = str(new_file_name or "").strip()
    if not source or not new_key or source == new_key:
        return False
    state = load_preset_folder_state(scope_key)
    source_meta = state.setdefault("items", {}).get(source)
    if not isinstance(source_meta, dict):
        source_meta = {"folder_key": COMMON_FOLDER_KEY, "order": None, "rating": 0}
    copied = dict(source_meta)
    copied["order"] = None
    if reset_rating:
        copied["rating"] = 0
    if reset_pin:
        copied.pop("pinned", None)
    state["items"][new_key] = copied
    save_preset_folder_state(scope_key, state)
    return True


def delete_preset_item_meta(scope_key: str, file_name: str) -> bool:
    key = str(file_name or "").strip()
    if not key:
        return False
    state = load_preset_folder_state(scope_key)
    removed = state.setdefault("items", {}).pop(key, None)
    if removed is None:
        return False
    save_preset_folder_state(scope_key, state)
    return True


def build_preset_folder_rows(
    *,
    all_presets: dict[str, dict[str, Any]],
    visible_entries: list[dict[str, Any]],
    active_file_name: str,
    folder_state: dict[str, Any] | None = None,
    scope_key: str = ENGINE_WINWS2,
    query: str = "",
) -> list[dict[str, Any]]:
    scope = _normalize_scope(scope_key)
    state = normalize_folder_state(folder_state, build_default_preset_folders(scope))
    try:
        from presets.remote_bindings import load_remote_preset_bindings

        remote_bindings = load_remote_preset_bindings(scope)
    except Exception:
        remote_bindings = {}
    live_items: list[dict[str, Any]] = []
    for entry in visible_entries:
        file_name = str(entry.get("file_name") or "").strip()
        if not file_name:
            continue
        preset = all_presets.get(file_name) or {}
        display_name = str(preset.get("display_name") or entry.get("display_name") or file_name).strip()
        meta = _ensure_item_meta(state, file_name, display_name, scope)
        folder_key = str(meta.get("folder_key") or classify_preset_folder(display_name or file_name, scope))
        live_items.append(
            {
                "key": file_name,
                "name": display_name,
                "folder_key": folder_key,
                "rating": int(meta.get("rating", 0) or 0),
                "pinned": bool(meta.get("pinned", False)),
            }
        )

    folder_rows = build_folder_rows(
        state,
        live_items=live_items,
        include_pinned_folder=True,
        query=query,
    )
    rows: list[dict[str, Any]] = []
    current_folder_name = ""
    for row in folder_rows:
        if row.get("kind") == "folder":
            current_folder_name = str(row.get("name") or "")
            rows.append(
                {
                    "kind": "folder",
                    "folder_key": str(row.get("key") or ""),
                    "name": str(row.get("name") or ""),
                    "text": str(row.get("name") or ""),
                    "is_collapsed": bool(row.get("collapsed", False)),
                    "is_system": bool(row.get("system", False)),
                    "is_service": bool(row.get("service", False)),
                    "count": int(row.get("count", 0) or 0),
                    "depth": 0,
                }
            )
            continue

        file_name = str(row.get("key") or "").strip()
        preset = all_presets.get(file_name) or {}
        meta = state.get("items", {}).get(file_name) or {}
        remote_binding = remote_bindings.get(file_name)
        # Пауза («Отвязать») сохраняет URL в настройках, но в списке пресет
        # выглядит обычным: облачко только у активных привязок.
        if remote_binding is not None and not bool(remote_binding.get("auto", True)):
            remote_binding = None
        if remote_binding is None:
            remote_state = ""
        elif bool(remote_binding.get("detached", False)):
            remote_state = "detached"
        elif str(remote_binding.get("error") or "").strip():
            remote_state = "error"
        else:
            remote_state = "ok"
        rows.append(
            {
                "kind": "preset",
                "name": str(preset.get("display_name") or row.get("name") or file_name).strip(),
                "file_name": file_name,
                "description": str(preset.get("description") or ""),
                "date": str(preset.get("modified_display") or ""),
                "is_active": bool(file_name and file_name == str(active_file_name or "").strip()),
                "is_builtin": bool(preset.get("is_builtin", False)),
                "can_reset_to_builtin": bool(preset.get("can_reset_to_builtin", False)),
                "icon_color": str(preset.get("icon_color") or ""),
                "depth": 1,
                "folder_key": str(row.get("folder_key") or ""),
                "folder_name": current_folder_name,
                "is_pinned": bool(meta.get("pinned", False)),
                "rating": int(meta.get("rating", 0) or 0),
                "is_remote": remote_binding is not None,
                "remote_state": remote_state,
            }
        )
    return rows


def _ensure_item_meta(
    state: dict[str, Any],
    file_name: str,
    display_name: str,
    scope_key: str = ENGINE_WINWS2,
) -> dict[str, Any]:
    items = state.setdefault("items", {})
    item = items.setdefault(
        file_name,
        {
            "folder_key": classify_preset_folder(display_name or file_name, scope_key),
            "order": None,
            "rating": 0,
        },
    )
    return item


def _normalize_scope(scope_key: str) -> str:
    scope = str(scope_key or "").strip().lower()
    return scope if scope in {ENGINE_WINWS1, ENGINE_WINWS2} else ENGINE_WINWS2


def _repair_virtual_pinned_item_folders(state: dict[str, Any]) -> None:
    """Возвращает старые ошибочные элементы из виртуальной папки в Общие.

    `pinned` — служебная папка только для отображения. Она никогда не должна
    попадать в `items[*].folder_key`: иначе обычный preset группируется под
    служебной папкой, а затем пропускается обычным рендерером.
    """
    items = state.get("items") if isinstance(state, dict) else None
    if not isinstance(items, dict):
        return
    for meta in items.values():
        if not isinstance(meta, dict):
            continue
        if str(meta.get("folder_key") or "").strip() == PINNED_FOLDER_KEY:
            meta["folder_key"] = COMMON_FOLDER_KEY


__all__ = [
    "build_preset_folder_rows",
    "copy_preset_item_meta",
    "create_preset_folder",
    "delete_preset_folder",
    "delete_preset_item_meta",
    "get_preset_item_meta",
    "load_preset_folder_state",
    "move_preset_by_step",
    "move_preset_folder_by_step",
    "move_preset_before",
    "move_preset_after",
    "move_preset_to_end",
    "move_preset_to_folder",
    "rename_preset_folder",
    "rename_preset_item_meta",
    "reset_preset_folders",
    "save_preset_folder_state",
    "set_preset_pin",
    "set_preset_rating",
    "set_preset_folder_collapsed",
    "toggle_preset_pin",
]
