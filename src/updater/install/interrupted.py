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

import time
from dataclasses import dataclass
from pathlib import Path

from config.build_info import APP_VERSION
from log.log import log

from ..versions import compare_versions
from .handoff import HandoffState, UpdateHandoffRecord, clear_record, read_record
from .recovery_hook import clear_recovery_hook


UPDATE_LOG_LEVEL = "🔁 UPDATE"

# Сколько запись об установке считается «идёт прямо сейчас». «Подготовлена» —
# программа передала установщик наблюдателю и закрывается; «запущена» —
# установщик работает. Обычно всё вместе занимает секунды, на медленном
# компьютере с антивирусом — минуты. Дольше этого запись уже ничего не
# доказывает: установка могла оборваться, и программа обязана запускаться.
PREPARED_IN_PROGRESS_SECONDS = 2 * 60.0
LAUNCHED_IN_PROGRESS_SECONDS = 5 * 60.0


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


def _forget(state_path: str | Path | None) -> None:
    """Закрывает вопрос о прошлом обновлении: запись и страховка больше не нужны.

    Страховку снимаем и здесь: наблюдатель прежних версий зависал и не успевал
    убрать её сам.
    """
    clear_record(state_path)
    clear_recovery_hook()


def installation_in_progress(
    *,
    current_version: str = APP_VERSION,
    record: UpdateHandoffRecord | None = None,
    state_path: str | Path | None = None,
    now: float | None = None,
) -> UpdateHandoffRecord | None:
    """Запись об обновлении, которое ставится прямо сейчас, либо None.

    Нужна запуску программы. Пока установщик заменяет файлы, прежней версии
    на экране нет, и человек щёлкает по ярлыку ещё раз (при тихом обновлении
    из трея он и не знает, что оно идёт). Запустившаяся в этот момент старая
    версия мешала установке: начинала второе обновление и не давала открыться
    новой — та видела «программа уже запущена» и выходила.

    Запись касается только версии старше той, что ставится: новая версия,
    которую открыл сам установщик, стартует, когда запись ещё «запущена».
    """
    known = record if record is not None else read_record(state_path)
    if known is None:
        return None
    if known.state is HandoffState.PREPARED:
        limit = PREPARED_IN_PROGRESS_SECONDS
    elif known.state is HandoffState.LAUNCHED:
        limit = LAUNCHED_IN_PROGRESS_SECONDS
    else:
        return None
    age = float(now if now is not None else time.time()) - float(known.updated_at or 0.0)
    # Отрицательный возраст — часы перевели назад: записи не верим.
    if age < 0 or age > limit:
        return None
    try:
        if compare_versions(current_version, known.version) >= 0:
            return None
    except ValueError:
        return None
    return known


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
    if installation_in_progress(current_version=current_version, record=known) is not None:
        # Установщик ещё работает: это не сорвавшееся обновление, и запись
        # наблюдателю ещё нужна — стирать её нельзя.
        return None

    try:
        settled = known.state is HandoffState.SUCCEEDED or compare_versions(
            current_version, known.version
        ) >= 0
    except ValueError:
        # Запись с неверной версией ничего не расскажет пользователю, а
        # оставленная на диске, ломала бы разбор при каждом запуске.
        log(f"В записи обновления неверная версия: {known.version!r}", "WARNING")
        settled = True
    if settled:
        if forget:
            _forget(state_path)
        return None

    if forget:
        _forget(state_path)

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
    "installation_in_progress",
]
