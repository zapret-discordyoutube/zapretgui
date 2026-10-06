from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from settings.mode import (
    BUILTIN_PRESETS_DIR_NAME_WINWS1,
    BUILTIN_PRESETS_DIR_NAME_WINWS2,
    ENGINE_WINWS1,
    ENGINE_WINWS2,
    PRESETS_DIR_NAME_WINWS1,
    PRESETS_DIR_NAME_WINWS2,
)


@dataclass(frozen=True)
class EnginePaths:
    engine: str
    presets_root_dir: Path
    user_presets_dir: Path
    builtin_presets_dir: Path

    @property
    def replaced_presets_dir(self) -> Path:
        """Прежние копии встроенных пресетов, заменённые при их обновлении
        (presets.preset_contract, пункт 7). В список пресетов не попадают."""
        return self.presets_root_dir / "replaced" / self.user_presets_dir.name

    def ensure_directories(self) -> "EnginePaths":
        self.presets_root_dir.mkdir(parents=True, exist_ok=True)
        self.user_presets_dir.mkdir(parents=True, exist_ok=True)
        self.builtin_presets_dir.mkdir(parents=True, exist_ok=True)
        return self


@dataclass(frozen=True)
class AppPaths:
    user_root: Path
    local_root: Path

    @lru_cache(maxsize=None)
    def engine_paths(self, engine: str) -> EnginePaths:
        engine_key = str(engine or "").strip().lower()
        presets_root_dir = self.user_root / "presets"

        if engine_key == ENGINE_WINWS2:
            user_presets_dir = presets_root_dir / PRESETS_DIR_NAME_WINWS2
            builtin_presets_dir = presets_root_dir / BUILTIN_PRESETS_DIR_NAME_WINWS2
        elif engine_key == ENGINE_WINWS1:
            user_presets_dir = presets_root_dir / PRESETS_DIR_NAME_WINWS1
            builtin_presets_dir = presets_root_dir / BUILTIN_PRESETS_DIR_NAME_WINWS1
        else:
            user_presets_dir = presets_root_dir / engine_key
            builtin_presets_dir = presets_root_dir / f"{engine_key}_builtin"

        return EnginePaths(
            engine=engine_key,
            presets_root_dir=presets_root_dir,
            user_presets_dir=user_presets_dir,
            builtin_presets_dir=builtin_presets_dir,
        )
