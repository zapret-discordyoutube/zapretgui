from __future__ import annotations

"""Состояние передачи управления установщику.

Приложение закрывается посреди обновления, поэтому «чем всё закончилось»
нельзя держать в памяти процесса. Запись живёт в каталоге, переживающем
переустановку, и её читают три участника: наблюдатель (PowerShell), само
приложение при следующем запуске и восстановление после перезагрузки.

Формат намеренно плоский JSON: его одинаково просто читать и из Python, и из
``ConvertFrom-Json``.
"""

import json
import os
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from log.log import log

from . import update_paths


SCHEMA_VERSION = 1


class HandoffState(StrEnum):
    PREPARED = "prepared"
    LAUNCHED = "launched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# Обновление идёт только вперёд, кроме повторной подготовки после провала:
# пользователь вправе попробовать ещё раз, и это единственный путь назад.
ALLOWED_TRANSITIONS: dict[HandoffState, frozenset[HandoffState]] = {
    HandoffState.PREPARED: frozenset({HandoffState.LAUNCHED, HandoffState.FAILED}),
    HandoffState.LAUNCHED: frozenset({HandoffState.SUCCEEDED, HandoffState.FAILED}),
    HandoffState.SUCCEEDED: frozenset({HandoffState.PREPARED}),
    HandoffState.FAILED: frozenset({HandoffState.PREPARED}),
}


def can_transition(current: HandoffState | str | None, target: HandoffState | str) -> bool:
    """Разрешён ли переход состояния. Из пустого состояния можно только готовить."""
    try:
        target_state = HandoffState(str(target))
    except ValueError:
        return False

    if current is None or str(current).strip() == "":
        return target_state is HandoffState.PREPARED

    try:
        current_state = HandoffState(str(current))
    except ValueError:
        return False

    return target_state in ALLOWED_TRANSITIONS[current_state]


@dataclass(frozen=True, slots=True)
class UpdateHandoffRecord:
    """Что именно передано установщику и чем это закончилось."""

    state: HandoffState
    version: str
    target_root: str
    installer_path: str
    arguments: tuple[str, ...] = ()
    gui_pid: int = 0
    installer_exit_code: int | None = None
    installed_version: str = ""
    error: str = ""
    updated_at: float = 0.0
    schema_version: int = SCHEMA_VERSION

    def to_payload(self) -> dict:
        return {
            "schema_version": int(self.schema_version),
            "state": str(self.state),
            "version": str(self.version),
            "target_root": str(self.target_root),
            "installer_path": str(self.installer_path),
            "arguments": [str(argument) for argument in self.arguments],
            "gui_pid": int(self.gui_pid),
            "installer_exit_code": self.installer_exit_code,
            "installed_version": str(self.installed_version),
            "error": str(self.error),
            "updated_at": float(self.updated_at),
        }

    @classmethod
    def from_payload(cls, payload: object) -> "UpdateHandoffRecord | None":
        if not isinstance(payload, dict):
            return None
        try:
            state = HandoffState(str(payload.get("state") or ""))
        except ValueError:
            return None

        exit_code = payload.get("installer_exit_code")
        try:
            normalized_exit_code = None if exit_code is None else int(exit_code)
        except (TypeError, ValueError):
            normalized_exit_code = None

        raw_arguments = payload.get("arguments")
        arguments = (
            tuple(str(argument) for argument in raw_arguments)
            if isinstance(raw_arguments, (list, tuple))
            else ()
        )

        try:
            gui_pid = int(payload.get("gui_pid") or 0)
        except (TypeError, ValueError):
            gui_pid = 0
        try:
            updated_at = float(payload.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            updated_at = 0.0

        return cls(
            state=state,
            version=str(payload.get("version") or ""),
            target_root=str(payload.get("target_root") or ""),
            installer_path=str(payload.get("installer_path") or ""),
            arguments=arguments,
            gui_pid=gui_pid,
            installer_exit_code=normalized_exit_code,
            installed_version=str(payload.get("installed_version") or ""),
            error=str(payload.get("error") or ""),
            updated_at=updated_at,
        )


def _state_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else update_paths.handoff_state_path()


def read_record(path: str | Path | None = None) -> UpdateHandoffRecord | None:
    """Последнее записанное состояние или None, если его нет либо оно битое."""
    state_path = _state_path(path)
    try:
        raw = state_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        log(f"Состояние обновления повреждено и будет проигнорировано: {state_path}", "WARNING")
        return None

    return UpdateHandoffRecord.from_payload(payload)


def write_record(
    record: UpdateHandoffRecord,
    path: str | Path | None = None,
    *,
    now: float | None = None,
) -> bool:
    """Атомарно сохраняет состояние: наблюдатель не должен прочитать полуфайл."""
    state_path = _state_path(path)
    stamped = UpdateHandoffRecord(
        state=record.state,
        version=record.version,
        target_root=record.target_root,
        installer_path=record.installer_path,
        arguments=tuple(record.arguments),
        gui_pid=record.gui_pid,
        installer_exit_code=record.installer_exit_code,
        installed_version=record.installed_version,
        error=record.error,
        updated_at=float(now if now is not None else time.time()),
        schema_version=SCHEMA_VERSION,
    )

    temporary_path = state_path.with_suffix(".json.new")
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            json.dumps(stamped.to_payload(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary_path, state_path)
        return True
    except OSError as exc:
        log(f"Не удалось сохранить состояние обновления: {exc}", "🔁❌ ERROR")
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def clear_record(path: str | Path | None = None) -> None:
    """Убирает состояние: обновление больше не считается незавершённым."""
    try:
        _state_path(path).unlink(missing_ok=True)
    except OSError as exc:
        log(f"Не удалось убрать состояние обновления: {exc}", "WARNING")


__all__ = [
    "ALLOWED_TRANSITIONS",
    "HandoffState",
    "SCHEMA_VERSION",
    "UpdateHandoffRecord",
    "can_transition",
    "clear_record",
    "read_record",
    "write_record",
]
