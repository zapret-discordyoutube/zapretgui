from __future__ import annotations

"""Распознавание обновления, которое не довели до конца.

Наблюдатель обновления записывает исход в ``handoff.json`` уже после того,
как приложение закрылось. Поэтому первое, что должен сделать следующий
запуск, — прочитать эту запись и сравнить её с версией, которая реально
работает. Расхождение означает: обновление сорвалось, и пользователю нужно
сказать об этом словами, а не оставить гадать, почему версия прежняя.

Модуль не знает ни про Qt, ни про окна: он только отвечает на вопрос
«сорвалось ли прошлое обновление и что об этом известно».
"""

from dataclasses import dataclass
from pathlib import Path

from config.build_info import APP_VERSION
from log.log import log

from .handoff_state import HandoffState, UpdateHandoffRecord, clear_record, read_record
from .update import compare_versions


UPDATE_LOG_LEVEL = "🔁 UPDATE"


@dataclass(frozen=True, slots=True)
class InterruptedUpdate:
    """Факты о сорвавшемся обновлении, пригодные для показа пользователю."""

    expected_version: str
    running_version: str
    installer_path: str
    reason: str
    installer_available: bool
    state: HandoffState


def _installer_exists(installer_path: str) -> bool:
    try:
        return Path(installer_path).is_file()
    except OSError:
        return False


def detect_interrupted_update(
    *,
    current_version: str = APP_VERSION,
    record: UpdateHandoffRecord | None = None,
    forget: bool = True,
    state_path: str | Path | None = None,
) -> InterruptedUpdate | None:
    """Возвращает данные сорвавшегося обновления либо None.

    Состояние снимается сразу же, как только вопрос закрыт: обновление либо
    признано состоявшимся, либо описано вызывающему коду один раз. Иначе
    пользователь получал бы одно и то же сообщение при каждом запуске.
    """
    known = record if record is not None else read_record(state_path)
    if known is None:
        return None

    if known.state is HandoffState.PREPARED:
        # Установка ещё не начиналась: либо её запускают прямо сейчас, либо
        # пользователь закрыл приложение до передачи управления.
        return None

    if known.state is HandoffState.SUCCEEDED or compare_versions(
        current_version, known.version
    ) >= 0:
        if forget:
            clear_record(state_path)
        return None

    if forget:
        clear_record(state_path)

    reason = known.error.strip() or "установка прервалась без объяснения"
    log(
        f"Прошлое обновление до v{known.version} не завершилось: {reason}",
        UPDATE_LOG_LEVEL,
    )
    return InterruptedUpdate(
        expected_version=str(known.version),
        running_version=str(current_version),
        installer_path=str(known.installer_path),
        reason=reason,
        installer_available=_installer_exists(known.installer_path),
        state=known.state,
    )


def describe_interrupted_update(interrupted: InterruptedUpdate) -> str:
    """Текст для пользователя: что случилось и что с этим делать."""
    lines = [
        f"Обновление до версии {interrupted.expected_version} не завершилось: "
        f"{interrupted.reason}.",
        f"Продолжает работать версия {interrupted.running_version}.",
    ]
    if interrupted.installer_available:
        lines.append(
            "Повторите обновление на странице «Серверы» или запустите "
            f"сохранённый установщик: {interrupted.installer_path}"
        )
    else:
        lines.append("Повторите обновление на странице «Серверы».")
    return "\n".join(lines)


__all__ = [
    "InterruptedUpdate",
    "describe_interrupted_update",
    "detect_interrupted_update",
]
