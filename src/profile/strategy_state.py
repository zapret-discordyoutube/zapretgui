from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from profile.strategy_list.plan import GROUPING_METHOD, normalize_strategy_grouping
from settings import store as settings_store


VALID_RATINGS = frozenset({"", "work", "notwork"})
# Ключ группы стратегий (profile.strategy_families): "fake", "fake_split", ...
_OPEN_GROUP_KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

# Составная мутация этой feature-модели остаётся под локальным lock, а
# settings.store дополнительно сериализует её с другими процессами через
# SQLite write-транзакцию.
_PROFILE_STRATEGY_STATE_LOCK = threading.RLock()


@dataclass(frozen=True)
class ProfileStrategyState:
    rating: str = ""
    favorite: bool = False


class ProfileStrategyStateStore:
    """Хранит в общей SQLite-базе настроек то, что человек отметил у профиля в
    списке готовых стратегий: оценки, избранное и открытую группу."""

    @property
    def path(self) -> Path:
        return settings_store.get_settings_database_path()

    def get_strategy_state(self, profile_key: str, strategy_id: str) -> ProfileStrategyState:
        states = self.get_strategy_states(profile_key, (strategy_id,))
        return states.get(_normalize_strategy_id(strategy_id), ProfileStrategyState())

    def get_strategy_states(self, profile_key: str, strategy_ids) -> dict[str, ProfileStrategyState]:
        return self.get_strategy_list_marks(profile_key, strategy_ids)[0]

    def get_strategy_list_marks(
        self,
        profile_key: str,
        strategy_ids,
    ) -> tuple[dict[str, ProfileStrategyState], str | None]:
        """Всё для списка стратегий профиля за одно чтение настроек.

        Возвращает оценки стратегий и группу, оставленную открытой (см.
        get_open_group).
        """
        clean_profile_key = _normalize_profile_key(profile_key)
        if not clean_profile_key:
            return {}, None
        clean_strategy_ids = tuple(
            strategy_id
            for strategy_id in (_normalize_strategy_id(value) for value in tuple(strategy_ids or ()))
            if strategy_id
        )
        data = self._read()
        profiles = data.get("profiles")
        profile_row = profiles.get(clean_profile_key) if isinstance(profiles, dict) else None
        states = {
            strategy_id: _state_from_row(_strategy_row(data, clean_profile_key, strategy_id))
            for strategy_id in clean_strategy_ids
        }
        return states, _open_group_from_row(profile_row)

    def get_strategy_experience(self, profile_key: str) -> dict[str, tuple[int, int]]:
        """Что человек отметил у ДРУГИХ профилей: {стратегия: (работает, не работает)}.

        По этому список стратегий учится на отметках человека: стратегия,
        которая уже помогла на двух сервисах, на третьем пробуется первой.
        Отметки самого профиля сюда не входят — они у него и так на виду.
        """
        own_key = _normalize_profile_key(profile_key)
        profiles = self._read().get("profiles")
        experience: dict[str, list[int]] = {}
        if not isinstance(profiles, dict):
            return {}
        for other_key, profile_row in profiles.items():
            if other_key == own_key or not isinstance(profile_row, dict):
                continue
            strategies = profile_row.get("strategies")
            if not isinstance(strategies, dict):
                continue
            for strategy_id, row in strategies.items():
                rating = _normalize_rating(row.get("rating")) if isinstance(row, dict) else ""
                if rating not in ("work", "notwork"):
                    continue
                counts = experience.setdefault(str(strategy_id), [0, 0])
                counts[0 if rating == "work" else 1] += 1
        return {strategy_id: (counts[0], counts[1]) for strategy_id, counts in experience.items()}

    def set_strategy_state(
        self,
        profile_key: str,
        strategy_id: str,
        *,
        rating: str | None = None,
        favorite: bool | None = None,
    ) -> ProfileStrategyState:
        clean_profile_key = _normalize_profile_key(profile_key)
        clean_strategy_id = _normalize_strategy_id(strategy_id)
        if not clean_profile_key:
            raise ValueError("profile key is required")
        if not clean_strategy_id:
            raise ValueError("strategy id is required")

        with _PROFILE_STRATEGY_STATE_LOCK:
            data = self._read()
            current_state = _state_from_row(_strategy_row(data, clean_profile_key, clean_strategy_id))
            next_state = ProfileStrategyState(
                rating=_normalize_rating(rating) if rating is not None else current_state.rating,
                favorite=bool(favorite) if favorite is not None else current_state.favorite,
            )
            if next_state == current_state:
                return current_state

            profiles = data.setdefault("profiles", {})
            if not isinstance(profiles, dict):
                profiles = {}
                data["profiles"] = profiles

            profile_row = profiles.setdefault(clean_profile_key, {})
            if not isinstance(profile_row, dict):
                profile_row = {}
                profiles[clean_profile_key] = profile_row

            strategies = profile_row.setdefault("strategies", {})
            if not isinstance(strategies, dict):
                strategies = {}
                profile_row["strategies"] = strategies

            row = strategies.setdefault(clean_strategy_id, {})
            if not isinstance(row, dict):
                row = {}
                strategies[clean_strategy_id] = row

            if rating is not None:
                row["rating"] = _normalize_rating(rating)
            if favorite is not None:
                row["favorite"] = bool(favorite)
            row["updated_at"] = _now_iso()

            if not row.get("rating") and not bool(row.get("favorite")):
                strategies.pop(clean_strategy_id, None)
            _drop_profile_row_if_empty(profiles, clean_profile_key)

            self._write(data)
            return self.get_strategy_state(clean_profile_key, clean_strategy_id)

    def get_open_group(self, profile_key: str) -> str | None:
        """Группа стратегий, которую человек оставил открытой у профиля.

        "" — он свернул все группы, None — ещё ничего не открывал.
        """
        return self.get_strategy_list_marks(profile_key, ())[1]

    def set_open_group(self, profile_key: str, group_key: str) -> bool:
        """Запоминает открытую группу профиля; "" — все группы свёрнуты."""
        clean_profile_key = _normalize_profile_key(profile_key)
        if not clean_profile_key:
            raise ValueError("profile key is required")
        clean_group_key = _normalize_open_group(group_key)
        if clean_group_key is None:
            raise ValueError("unknown strategy group key")

        with _PROFILE_STRATEGY_STATE_LOCK:
            data = self._read()
            profiles = data.setdefault("profiles", {})
            if not isinstance(profiles, dict):
                profiles = {}
                data["profiles"] = profiles
            profile_row = profiles.get(clean_profile_key)
            if _open_group_from_row(profile_row) == clean_group_key:
                return False
            if not isinstance(profile_row, dict):
                profile_row = {}
                profiles[clean_profile_key] = profile_row
            profile_row["open_group"] = clean_group_key
            self._write(data)
            return True

    def get_grouping(self) -> str:
        """По чему человек сгруппировал списки готовых стратегий."""
        return normalize_strategy_grouping(self._read().get("grouping"))

    def set_grouping(self, grouping: str) -> bool:
        """Запоминает группировку списков стратегий; True — значение изменилось."""
        clean_grouping = normalize_strategy_grouping(grouping)
        with _PROFILE_STRATEGY_STATE_LOCK:
            data = self._read()
            if normalize_strategy_grouping(data.get("grouping")) == clean_grouping:
                return False
            data["grouping"] = clean_grouping
            self._write(data)
            return True

    def clear_strategy_state(self, profile_key: str, strategy_id: str) -> None:
        clean_profile_key = _normalize_profile_key(profile_key)
        clean_strategy_id = _normalize_strategy_id(strategy_id)
        if not clean_profile_key or not clean_strategy_id:
            return

        with _PROFILE_STRATEGY_STATE_LOCK:
            data = self._read()
            profiles = data.get("profiles")
            if not isinstance(profiles, dict):
                return
            profile_row = profiles.get(clean_profile_key)
            if not isinstance(profile_row, dict):
                return
            strategies = profile_row.get("strategies")
            if not isinstance(strategies, dict):
                return
            if clean_strategy_id not in strategies:
                return

            strategies.pop(clean_strategy_id, None)
            _drop_profile_row_if_empty(profiles, clean_profile_key)
            self._write(data)

    def migrate_profile_keys(self, key_mapping: dict[str, str]) -> bool:
        """Переносит записи с legacy-ключей (name:/sig:) на uid-ключи.

        Уже существующая uid-запись побеждает: legacy-строка просто
        удаляется, чтобы не воскрешать устаревшие оценки.
        """
        mapping = {
            _normalize_profile_key(old): _normalize_profile_key(new)
            for old, new in dict(key_mapping or {}).items()
        }
        mapping = {
            old: new
            for old, new in mapping.items()
            if old and new and old != new and not old.startswith("uid:")
        }
        if not mapping:
            return False
        with _PROFILE_STRATEGY_STATE_LOCK:
            data = self._read()
            profiles = data.get("profiles")
            if not isinstance(profiles, dict):
                return False
            changed = False
            for old_key, new_key in mapping.items():
                row = profiles.pop(old_key, None)
                if row is None:
                    continue
                changed = True
                if new_key not in profiles:
                    profiles[new_key] = row
            if changed:
                self._write(data)
            return changed

    def _read(self) -> dict[str, Any]:
        raw = settings_store.get_profile_strategy_state_settings()
        if not isinstance(raw, dict):
            return _empty_state()
        raw["version"] = 1
        raw.setdefault("profiles", {})
        return raw

    def _write(self, data: dict[str, Any]) -> None:
        settings_store.set_profile_strategy_state_settings(_normalize_data(data))


def _empty_state() -> dict[str, Any]:
    return {
        "version": 1,
        "profiles": {},
    }


def _normalize_data(data: dict[str, Any]) -> dict[str, Any]:
    raw_profiles = data.get("profiles")
    profiles: dict[str, Any] = {}
    if isinstance(raw_profiles, dict):
        for raw_profile_key, raw_profile_row in raw_profiles.items():
            profile_key = _normalize_profile_key(raw_profile_key)
            if not profile_key or not isinstance(raw_profile_row, dict):
                continue
            raw_strategies = raw_profile_row.get("strategies")
            if not isinstance(raw_strategies, dict):
                raw_strategies = {}
            strategies: dict[str, Any] = {}
            for raw_strategy_id, raw_strategy_row in raw_strategies.items():
                strategy_id = _normalize_strategy_id(raw_strategy_id)
                if not strategy_id or not isinstance(raw_strategy_row, dict):
                    continue
                rating = _normalize_rating(raw_strategy_row.get("rating"))
                favorite = bool(raw_strategy_row.get("favorite"))
                if not rating and not favorite:
                    continue
                row: dict[str, Any] = {
                    "favorite": favorite,
                    "rating": rating,
                }
                updated_at = str(raw_strategy_row.get("updated_at") or "").strip()
                if updated_at:
                    row["updated_at"] = updated_at
                strategies[strategy_id] = row
            profile_row: dict[str, Any] = {}
            if strategies:
                profile_row["strategies"] = strategies
            open_group = _open_group_from_row(raw_profile_row)
            if open_group is not None:
                profile_row["open_group"] = open_group
            if profile_row:
                profiles[profile_key] = profile_row

    normalized: dict[str, Any] = {
        "version": 1,
        "profiles": profiles,
    }
    grouping = normalize_strategy_grouping(data.get("grouping"))
    if grouping != GROUPING_METHOD:
        normalized["grouping"] = grouping
    return normalized


def _drop_profile_row_if_empty(profiles: dict[str, Any], profile_key: str) -> None:
    profile_row = profiles.get(profile_key)
    if not isinstance(profile_row, dict):
        profiles.pop(profile_key, None)
        return
    if not profile_row.get("strategies"):
        profile_row.pop("strategies", None)
    if not profile_row:
        profiles.pop(profile_key, None)


def _normalize_open_group(value: object) -> str | None:
    """Ключ группы, "" (всё свёрнуто) или None, если значение не годится."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or _OPEN_GROUP_KEY.match(text):
        return text
    return None


def _open_group_from_row(profile_row: object) -> str | None:
    if not isinstance(profile_row, dict) or "open_group" not in profile_row:
        return None
    return _normalize_open_group(profile_row.get("open_group"))


def _strategy_row(data: dict[str, Any], profile_key: str, strategy_id: str) -> dict[str, Any]:
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        return {}
    profile_row = profiles.get(_normalize_profile_key(profile_key))
    if not isinstance(profile_row, dict):
        return {}
    strategies = profile_row.get("strategies")
    if not isinstance(strategies, dict):
        return {}
    row = strategies.get(_normalize_strategy_id(strategy_id))
    return row if isinstance(row, dict) else {}


def _state_from_row(row: dict[str, Any]) -> ProfileStrategyState:
    return ProfileStrategyState(
        rating=_normalize_rating(row.get("rating") if isinstance(row, dict) else ""),
        favorite=bool(row.get("favorite")) if isinstance(row, dict) else False,
    )


def _normalize_profile_key(value: object) -> str:
    text = str(value or "").strip()
    # uid: — стабильная идентичность из реестра; name:/sig: — legacy-ключи
    # старых сохранений, мигрируются на uid при resolve.
    if text.startswith("uid:") or text.startswith("name:") or text.startswith("sig:"):
        return text
    return ""


def _normalize_strategy_id(value: object) -> str:
    text = str(value or "").strip()
    if text in {"", "none", "custom"}:
        return ""
    return text


def _normalize_rating(value: object) -> str:
    rating = str(value or "").strip().lower()
    return rating if rating in VALID_RATINGS else ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
