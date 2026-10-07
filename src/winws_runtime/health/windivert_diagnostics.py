# winws_runtime/health/windivert_diagnostics.py
"""Единый центр диагностики WinDivert.

Здесь живут:
- канонические определения Win32-кодов ошибок WinDivert (единственная точка
  определения числовых литералов, остальные модули импортируют имена);
- декларативная таблица код → (причина, решение, auto_fix action);
- ``describe_windivert_error`` — пользовательский текст с номером кода;
- проверка службы драйвера перед запуском с подсказкой о конфликте WinDivert.

Модуль намеренно не импортирует ``winws_runtime.runtime.system_ops`` на
уровне модуля: system_ops сам импортирует отсюда канонический код 1072,
поэтому все обращения к runtime-функциям выполняются лениво.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional



# --- Канонические Win32-коды ошибок WinDivert (единственная точка определения) ---
_ERROR_ACCESS_DENIED = 5
_ERROR_NOT_ENOUGH_MEMORY = 8
_ERROR_GEN_FAILURE = 31
_ERROR_INVALID_PARAMETER = 87
_ERROR_BAD_PATHNAME = 161
# ERROR_NO_SUCH_DEVICE: сетевой стек/WFP ещё переинициализируется — типично
# сразу после переключения сети или выхода из сна.
_ERROR_NO_SUCH_DEVICE = 433
_ERROR_INVALID_IMAGE_HASH = 577
_ERROR_DRIVER_FAILED_PRIOR_UNLOAD = 654
_ERROR_SERVICE_DISABLED = 1058
_ERROR_SERVICE_DOES_NOT_EXIST = 1060
_ERROR_PROCESS_ABORTED = 1067
_ERROR_SERVICE_DEPENDENCY_FAIL = 1068
_ERROR_SERVICE_MARKED_FOR_DELETE = 1072
_ERROR_DRIVER_BLOCKED = 1275
# EPT_S_NOT_REGISTERED: плавающая гонка SCM сразу после stop/cleanup.
_ERROR_EPT_S_NOT_REGISTERED = 1753
# Коды платформы фильтрации Windows (WFP), значения — из winerror.h.
# Каждый winws открывает в WFP свой сеанс и ставит в нём фильтры; когда процесс
# завершается, Windows убирает эти фильтры сама и не мгновенно. Пока они не
# убраны (или пока их держит чужая программа на WinDivert), новый запуск
# получает один из двух кодов:
# - FWP_E_ALREADY_EXISTS: "An object with that GUID or LUID already exists";
# - FWP_E_IN_USE: "The object is referenced by other objects so cannot be deleted".
# Сборка winws на Cygwin отдаёт кодом завершения только младший байт: 9 и 10.
_FWP_E_ALREADY_EXISTS = 0x80320009
_FWP_E_IN_USE = 0x8032000A

_WFP_LEFTOVERS_SOLUTION = (
    "Подождите несколько секунд и запустите снова. Если не помогает — закройте другие "
    "программы обхода блокировок (GoodbyeDPI, другой Zapret, VPN на базе WinDivert) "
    "или перезагрузите компьютер"
)

# Сколько ждать перед запуском, пока служба драйвера закончит выгружаться.
_WINDIVERT_PRESPAWN_WAIT_SECONDS = 3.0


@dataclass(frozen=True, slots=True)
class WinDivertErrorRecord:
    """Декларативное описание одного Win32-кода ошибки WinDivert."""

    code: int
    cause: str                  # базовая причина (для diagnose_winws_exit fallback)
    solution: str               # базовое решение
    auto_fix_action: Optional[str] = None  # безопасное авто-действие или None


WINDIVERT_ERROR_TABLE: dict[int, WinDivertErrorRecord] = {
    record.code: record
    for record in (
        WinDivertErrorRecord(
            code=_ERROR_ACCESS_DENIED,
            cause="Отказано в доступе к WinDivert",
            solution="Проверьте антивирус и запустите от имени администратора",
        ),
        WinDivertErrorRecord(
            code=_ERROR_NOT_ENOUGH_MEMORY,
            cause="Недостаточно системной памяти для WinDivert",
            solution="Закройте лишние программы и перезагрузите компьютер",
        ),
        WinDivertErrorRecord(
            code=_ERROR_GEN_FAILURE,
            cause="Общая ошибка устройства",
            solution="Перезагрузите компьютер и проверьте сетевые адаптеры",
        ),
        WinDivertErrorRecord(
            code=_ERROR_INVALID_PARAMETER,
            cause="Ошибка в параметрах фильтра или Lua-скрипта",
            solution="Проверьте настройки стратегии — возможно повреждён пресет",
        ),
        WinDivertErrorRecord(
            code=_ERROR_BAD_PATHNAME,
            cause="Не найден файл драйвера WinDivert",
            solution="Переустановите программу или проверьте антивирус",
        ),
        WinDivertErrorRecord(
            code=_ERROR_NO_SUCH_DEVICE,
            cause="Сетевое устройство для WinDivert временно недоступно",
            solution=(
                "Подождите несколько секунд после смены сети и повторите запуск. "
                "Если не помогает — перезагрузите компьютер"
            ),
        ),
        WinDivertErrorRecord(
            code=_ERROR_INVALID_IMAGE_HASH,
            cause="Подпись драйвера WinDivert не прошла проверку",
            solution="Отключите Secure Boot или включите тестовый режим: bcdedit /set testsigning on",
        ),
        WinDivertErrorRecord(
            code=_ERROR_DRIVER_FAILED_PRIOR_UNLOAD,
            cause="Старая версия драйвера WinDivert всё ещё загружена в память",
            solution="Перезагрузите компьютер для выгрузки старого драйвера",
        ),
        WinDivertErrorRecord(
            code=_ERROR_SERVICE_DISABLED,
            cause="WinDivert не может запустить службу драйвера",
            solution="Перезагрузите компьютер. Если не помогает — проверьте Secure Boot и антивирус",
        ),
        WinDivertErrorRecord(
            code=_ERROR_SERVICE_DOES_NOT_EXIST,
            cause="Служба WinDivert не найдена в системе",
            solution="Переустановите программу",
        ),
        WinDivertErrorRecord(
            code=_ERROR_PROCESS_ABORTED,
            cause="Драйвер WinDivert аварийно завершился при запуске",
            solution="Переустановите программу и перезагрузите компьютер",
        ),
        WinDivertErrorRecord(
            code=_ERROR_SERVICE_DEPENDENCY_FAIL,
            cause="Зависимая служба Windows Filtering Platform не запущена",
            solution="Перезагрузите компьютер",
        ),
        WinDivertErrorRecord(
            code=_ERROR_SERVICE_MARKED_FOR_DELETE,
            cause="Служба WinDivert помечена на удаление",
            solution="Подождите несколько секунд и повторите запуск, либо перезагрузите компьютер",
        ),
        WinDivertErrorRecord(
            code=_ERROR_DRIVER_BLOCKED,
            cause="Политика безопасности Windows блокирует загрузку драйвера",
            solution="Проверьте настройки Device Guard / WDAC или отключите Secure Boot",
        ),
        WinDivertErrorRecord(
            code=_FWP_E_ALREADY_EXISTS,
            cause=(
                "Windows ещё не убрала фильтры WinDivert от прошлого запуска, "
                "либо их держит другая программа"
            ),
            solution=_WFP_LEFTOVERS_SOLUTION,
        ),
        WinDivertErrorRecord(
            code=_FWP_E_IN_USE,
            cause=(
                "Фильтры WinDivert от прошлого запуска ещё заняты: Windows не успела "
                "их убрать, либо их держит другая программа"
            ),
            solution=_WFP_LEFTOVERS_SOLUTION,
        ),
        WinDivertErrorRecord(
            code=_ERROR_EPT_S_NOT_REGISTERED,
            cause="Служба WinDivert ещё не зарегистрирована в системе (временная гонка SCM)",
            solution="Подождите пару секунд и повторите запуск",
        ),
    )
}

def format_windows_error_code(code: int) -> str:
    """Код Windows в виде, пригодном для показа пользователю.

    Обычные Win32-коды остаются десятичными, а HRESULT/NTSTATUS (FWP_E_*,
    STATUS_*) дополняются шестнадцатеричной записью: в таком виде их узнают и
    ищут в документации, тогда как голое ``2151092240`` не говорит ничего.
    """
    value = int(code)
    if value < 0 or value > 0xFFFF:
        return f"{value} / 0x{value & 0xFFFFFFFF:08X}"
    return str(value)


def describe_windivert_error(code: int) -> str:
    """Пользовательский текст (ru) для Win32-кода ошибки WinDivert.

    Всегда содержит номер кода.
    """
    error_code = int(code)
    record = WINDIVERT_ERROR_TABLE.get(error_code)
    code_text = format_windows_error_code(error_code)
    if record is not None:
        return f"{record.cause} (код {code_text}). {record.solution}"
    return f"Ошибка WinDivert (код {code_text})"


# ---------------------------------------------------------------------------
#  Проверка службы драйвера перед запуском
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WinDivertReadinessResult:
    """Итог проверки службы драйвера WinDivert перед запуском движка."""

    ready: bool
    blocker: str = ""                    # что мешает: stop_pending / stuck_entry
    service: str = ""                    # имя службы драйвера, если она мешает
    description: str = ""                # человеко-читаемое описание провала
    conflict_hint: str = ""              # подсказка о найденном конфликте, если есть


def describe_windivert_conflict_hint() -> str:
    """Ищет программу, реально держащую WinDivert, только в ветке ошибки."""
    try:
        from winws_runtime.health.launch_conflicts import build_windivert_conflict_hint

        return str(build_windivert_conflict_hint() or "")
    except Exception:
        return ""


def ensure_windivert_ready_before_spawn(
    *,
    max_wait_seconds: float = _WINDIVERT_PRESPAWN_WAIT_SECONDS,
) -> WinDivertReadinessResult:
    """Проверяет перед запуском, что служба драйвера не застряла.

    Проверка только читает состояние службы у диспетчера служб. Сам драйвер
    программа не открывает и не ставит: это делает winws, у которого есть
    своя защита от гонок запуска.

    Запуску мешают ровно два состояния (оба воспроизведены на живой Windows):
    драйвер «останавливается», потому что его остановили при живом winws, и
    запись «остановлена и помечена на удаление», потому что её хэндл держит
    другая программа. Оба проходят сами, как только исчезает причина, поэтому
    проверка даёт им до ``max_wait_seconds``. Отсутствие службы и работающий
    драйвер — нормальные состояния.
    """
    try:
        from winws_runtime.runtime.system_ops import ensure_windivert_driver_startable_runtime

        preflight = ensure_windivert_driver_startable_runtime(wait_seconds=max_wait_seconds)
    except Exception:
        return WinDivertReadinessResult(ready=True)

    if preflight.ok:
        return WinDivertReadinessResult(ready=True)

    conflict_hint = describe_windivert_conflict_hint()
    base_message = str(preflight.message or "Служба драйвера WinDivert не готова к запуску")
    description = f"{base_message}. {conflict_hint}" if conflict_hint else base_message
    return WinDivertReadinessResult(
        ready=False,
        blocker=str(preflight.blocker or ""),
        service=str(preflight.service or ""),
        description=description,
        conflict_hint=conflict_hint,
    )
