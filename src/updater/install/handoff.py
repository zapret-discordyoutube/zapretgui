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
import tempfile
import time
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from log.log import log

from . import paths


SCHEMA_VERSION = 2


class HandoffState(StrEnum):
    PREPARED = "prepared"
    LAUNCHED = "launched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class UpdateHandoffRecord:
    """Что именно передано установщику и чем это закончилось."""

    state: HandoffState
    version: str
    target_root: str
    installer_path: str
    installer_sha256: str = ""
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
            "installer_sha256": str(self.installer_sha256),
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

        raw_arguments = payload.get("arguments")
        arguments = (
            tuple(str(argument) for argument in raw_arguments)
            if isinstance(raw_arguments, (list, tuple))
            else ()
        )

        return cls(
            state=state,
            version=str(payload.get("version") or ""),
            target_root=str(payload.get("target_root") or ""),
            installer_path=str(payload.get("installer_path") or ""),
            installer_sha256=str(payload.get("installer_sha256") or ""),
            arguments=arguments,
            gui_pid=_as_int(payload.get("gui_pid")) or 0,
            installer_exit_code=_as_int(payload.get("installer_exit_code")),
            installed_version=str(payload.get("installed_version") or ""),
            error=str(payload.get("error") or ""),
            updated_at=_as_float(payload.get("updated_at")),
            schema_version=_as_int(payload.get("schema_version")) or 1,
        )


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _state_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else paths.handoff_state_path()


def read_record(path: str | Path | None = None) -> UpdateHandoffRecord | None:
    """Последнее записанное состояние или None, если его нет либо оно битое.

    Читается как ``utf-8-sig``: Windows PowerShell 5.1 пишет UTF-8 с BOM, а
    наблюдатель прежних версий писал запись именно так.
    """
    state_path = _state_path(path)
    try:
        raw = state_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        log(f"Состояние обновления не читается ({state_path}): {exc}", "WARNING")
        return None

    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        log(f"Состояние обновления повреждено и будет удалено: {state_path}", "WARNING")
        clear_record(state_path)
        return None

    return UpdateHandoffRecord.from_payload(payload)


def write_record(
    record: UpdateHandoffRecord,
    path: str | Path | None = None,
    *,
    now: float | None = None,
) -> bool:
    """Атомарно сохраняет состояние: наблюдатель не должен прочитать полуфайл.

    Временный файл создаётся со случайным именем и флагом «только новый»:
    заранее подложенный файл с предсказуемым именем не перехватит запись.
    """
    state_path = _state_path(path)
    stamped = replace(
        record,
        updated_at=float(now if now is not None else time.time()),
        schema_version=SCHEMA_VERSION,
    )
    data = json.dumps(stamped.to_payload(), ensure_ascii=False, indent=2).encode("utf-8")

    temporary_path: str | None = None
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f"{state_path.name}.",
            suffix=".tmp",
            dir=str(state_path.parent),
        )
        with os.fdopen(descriptor, "wb") as file_obj:
            file_obj.write(data)
        os.replace(temporary_path, state_path)
        return True
    except OSError as exc:
        log(f"Не удалось сохранить состояние обновления: {exc}", "🔁❌ ERROR")
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
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
    "HandoffState",
    "SCHEMA_VERSION",
    "UpdateHandoffRecord",
    "clear_record",
    "read_record",
    "write_record",
]
