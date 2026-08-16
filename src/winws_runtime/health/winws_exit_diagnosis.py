# winws_runtime/health/winws_exit_diagnosis.py
"""Диагностика кодов завершения winws/winws2 (WinDivert exit-code diagnostics).

Handler-функции берут базовые тексты причин/решений из единой таблицы
``winws_runtime.health.windivert_diagnostics.WINDIVERT_ERROR_TABLE`` и
дополняют их динамическими probe-уточнениями (BFE, Secure Boot, файлы
драйвера, антивирус, сетевые адаптеры).
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from config.runtime_layout import APPLICATION_PATHS
from winws_runtime.health.antivirus_detection import (  # noqa: F401 (реэкспорт для фасада)
    _detect_active_antivirus,
    _find_known_antivirus_name,
    _is_windows_defender_active,
)
from winws_runtime.health.winws_output import (
    has_diagnostic_output,
    relevant_error_line,
)
from winws_runtime.health.windivert_diagnostics import (
    WINDIVERT_ERROR_TABLE,
    _ERROR_ACCESS_DENIED,
    _FWP_E_IN_USE,
    _ERROR_BAD_PATHNAME,
    _ERROR_DRIVER_BLOCKED,
    _ERROR_DRIVER_FAILED_PRIOR_UNLOAD,
    _ERROR_GEN_FAILURE,
    _ERROR_INVALID_IMAGE_HASH,
    _ERROR_INVALID_PARAMETER,
    _ERROR_NOT_ENOUGH_MEMORY,
    _ERROR_PROCESS_ABORTED,
    _ERROR_SERVICE_DEPENDENCY_FAIL,
    _ERROR_SERVICE_DISABLED,
    _ERROR_SERVICE_DOES_NOT_EXIST,
    _WINDIVERT_DRIVER_SERVICE_NAMES,
    describe_windivert_conflict_hint,
    format_windows_error_code,
)


@dataclass
class WinDivertDiagnosis:
    """Structured result of WinDivert error diagnosis."""
    cause: str                        # Human-readable cause
    solution: str                     # What the user should do
    auto_fix: Optional[str] = None   # Action ID: "enable_adapters", "enable_bfe", "enable_driver", None
    severity: str = "critical"        # "critical" | "warning"
    exit_code: int = 0                # Original exit code
    win32_error: Optional[int] = None # Mapped Win32 error (may differ from exit_code)
    # True, когда win32_error не измерен, а восстановлен эвристикой из
    # усечённого кода завершения (34 → 1058). Такой код и построенная на нём
    # причина обязаны показываться пользователю как предположение.
    win32_error_inferred: bool = False
    # False, когда известен сам тип сбоя, но исходный Win32-код уже потерян
    # внутри winws2. В таком случае нельзя писать пользователю «Найдена причина».
    cause_is_exact: bool = True


def format_winws_exit_diagnosis(
    diagnosis: WinDivertDiagnosis,
    *,
    exe_name: str = "winws",
) -> str:
    """Собирает подробную, но понятную ошибку для верхнего InfoBar.

    Код завершения процесса Windows иногда обрезает до одного байта: например,
    WinDivert возвращает 1058, а ``winws2.exe`` завершается с кодом 34. Поэтому
    пользователю важно показать оба значения и не подменять причину первой
    служебной строкой вывода ``winws``.

    Измеренное и предположенное разделены: если Win32-код не получен от
    процесса, а восстановлен эвристикой (``win32_error_inferred``), он
    показывается как предположение и стоит после фактического кода завершения,
    а причина подаётся как вероятная, а не как установленная.
    """
    executable = str(exe_name or "winws").strip() or "winws"
    cause = str(getattr(diagnosis, "cause", "") or "").strip().rstrip(".")
    solution = str(getattr(diagnosis, "solution", "") or "").strip().rstrip(".")
    inferred = bool(getattr(diagnosis, "win32_error_inferred", False))
    cause_is_exact = bool(getattr(diagnosis, "cause_is_exact", True))

    try:
        exit_code = int(getattr(diagnosis, "exit_code", 0))
    except (TypeError, ValueError):
        exit_code = 0
    try:
        raw_win32_error = getattr(diagnosis, "win32_error", None)
        win32_error = int(raw_win32_error) if raw_win32_error is not None else None
    except (TypeError, ValueError):
        win32_error = None

    code_parts: list[str] = []
    if win32_error is not None:
        win32_text = format_windows_error_code(win32_error)
        if exit_code and exit_code != win32_error:
            exit_text = format_windows_error_code(exit_code)
            if inferred:
                # Измеренный факт первым, восстановленный код — как догадка.
                code_parts.append(f"код завершения процесса {exit_text}")
                code_parts.append(f"предположительно код ошибки Windows {win32_text}")
            else:
                code_parts.append(f"код ошибки Windows {win32_text}")
                code_parts.append(f"код завершения процесса {exit_text}")
        else:
            code_parts.append(f"код ошибки {win32_text}")
    elif exit_code:
        code_parts.append(f"код завершения процесса {format_windows_error_code(exit_code)}")

    message = f"{executable} не запустился"
    if cause:
        if not cause_is_exact:
            cause_label = "Что известно"
        else:
            cause_label = "Вероятная причина" if inferred else "Найдена причина"
        message = f"{message}. {cause_label}: {cause}"
    if code_parts:
        message = f"{message} ({'; '.join(code_parts)})"
    if solution:
        message = f"{message}. Что сделать: {solution}"

    auto_fix = str(getattr(diagnosis, "auto_fix", "") or "").strip()
    if auto_fix:
        message = f"[AUTOFIX:{auto_fix}]{message}"
    return message


def _diagnosis_from_table(code: int, *, severity: str = "critical") -> WinDivertDiagnosis:
    """Базовый диагноз из единой таблицы кодов (без динамических уточнений)."""
    record = WINDIVERT_ERROR_TABLE[code]
    return WinDivertDiagnosis(
        cause=record.cause,
        solution=record.solution,
        auto_fix=record.auto_fix_action,
        severity=severity,
    )


# stderr patterns → Win32 error mapping (for when exit code is truncated)
_STDERR_TO_WIN32: List[Tuple[str, int]] = [
    ("the service cannot be started", _ERROR_SERVICE_DISABLED),
    ("service is disabled", _ERROR_SERVICE_DISABLED),
    ("no enabled devices", _ERROR_SERVICE_DISABLED),
    ("access is denied", _ERROR_ACCESS_DENIED),
    ("access denied", _ERROR_ACCESS_DENIED),
    ("hash for file is not valid", _ERROR_INVALID_IMAGE_HASH),
    ("invalid image hash", _ERROR_INVALID_IMAGE_HASH),
    ("disable secure boot", _ERROR_INVALID_IMAGE_HASH),
    ("driver blocked", _ERROR_DRIVER_BLOCKED),
    ("blocked from loading", _ERROR_DRIVER_BLOCKED),
    ("driver failed prior unload", _ERROR_DRIVER_FAILED_PRIOR_UNLOAD),
    # FWP_E_IN_USE: winws2 печатает текст ошибки, а кодом завершения отдаёт
    # усечённое значение, по которому этот случай не опознать.
    ("referenced by other objects", _FWP_E_IN_USE),
    ("bad pathname", _ERROR_BAD_PATHNAME),
    ("service does not exist", _ERROR_SERVICE_DOES_NOT_EXIST),
    ("dependency service", _ERROR_SERVICE_DEPENDENCY_FAIL),
    ("process terminated unexpectedly", _ERROR_PROCESS_ABORTED),
    ("not enough memory", _ERROR_NOT_ENOUGH_MEMORY),
    ("insufficient resources", _ERROR_NOT_ENOUGH_MEMORY),
    ("parameter is incorrect", _ERROR_INVALID_PARAMETER),
    ("a device attached to the system is not functioning", _ERROR_GEN_FAILURE),
]


def diagnose_winws_exit(exit_code: int, stderr: str = "") -> Optional[WinDivertDiagnosis]:
    """Diagnose winws2 exit code + stderr and return structured result.

    The exit code of winws2 equals the raw Win32 GetLastError() value after
    WinDivertOpen() fails.  However, the exit code may be truncated to 8 bits
    in some scenarios (e.g. 1058 → 34).  Therefore stderr text is parsed first
    as the primary signal, and exit_code is used as fallback.

    Returns None if exit_code is 0 (success) or diagnosis is not applicable.
    """
    if exit_code == 0:
        return None

    stderr_lower = (stderr or "").lower()

    # В zapret2 v1.0.3 и в текущем upstream после неудачного
    # GetOverlappedResult() не сохраняется новый GetLastError(). В результате
    # остаётся предыдущее штатное ERROR_IO_PENDING (997), а Cygwin-процесс
    # завершает работу усечённым кодом 229. Это не ERROR_PIPE_LOCAL и не
    # самостоятельная причина WinDivert — исходный Win32-код уже утрачен.
    if (
        int(exit_code) == 229
        and "windivert: recv failed" in stderr_lower
        and "errno 5" in stderr_lower
    ):
        return WinDivertDiagnosis(
            cause=(
                "winws2 сообщил «windivert: recv failed. errno 5»: "
                "асинхронное чтение пакетов из WinDivert завершилось ошибкой, "
                "но winws2 потерял исходный код Windows. Код 229 — усечённый "
                "остаток штатного ERROR_IO_PENDING (997), а не причина сбоя WinDivert"
            ),
            solution=(
                "Закройте другие программы, использующие WinDivert, и повторите запуск. "
                "Если активен только один winws2, перезагрузите Windows. "
                "Полный вывод winws2 сохранён в журнале программы"
            ),
            severity="critical",
            exit_code=int(exit_code),
            win32_error=None,
            cause_is_exact=False,
        )

    # 1. Resolve the real Win32 error from stderr text (more reliable)
    win32_error = exit_code
    win32_error_inferred = False
    for pattern, code in _STDERR_TO_WIN32:
        if pattern in stderr_lower:
            win32_error = code
            break

    # Winws2 can return the raw Win32 error truncated to one byte.
    # ERROR_SERVICE_DISABLED 1058 becomes process exit code 34, often without
    # stderr in GUI launch mode. Treat that as the same driver-service failure.
    # "Без stderr" здесь означает "без диагностики": служебный баннер версии
    # winws2 печатает всегда, и раньше он один ломал эту ветку.
    # Это единственная ветка, где Win32-код не измерен, а угадан, поэтому она
    # помечает диагноз как предположительный.
    if win32_error == 34 and not has_diagnostic_output(stderr):
        win32_error = _ERROR_SERVICE_DISABLED
        win32_error_inferred = True

    # 2. Dispatch to specific handlers
    handler = _EXIT_CODE_HANDLERS.get(win32_error)
    if handler:
        diag = handler(exit_code, stderr)
        diag.exit_code = exit_code
        diag.win32_error = win32_error
        diag.win32_error_inferred = win32_error_inferred
        return diag

    # 3. Fallback: generic WinDivert error
    if "windivert" in stderr_lower or "error opening filter" in stderr_lower:
        first_line = _extract_relevant_error_line(stderr)[:200]
        return WinDivertDiagnosis(
            cause=f"Ошибка WinDivert (код {format_windows_error_code(exit_code)})",
            solution=first_line or "Перезагрузите компьютер и попробуйте снова",
            severity="critical",
            exit_code=exit_code,
            win32_error=win32_error,
            win32_error_inferred=win32_error_inferred,
        )

    return None


def _extract_relevant_error_line(stderr: str) -> str:
    """Самая содержательная строка вывода (см. winws_output — единый разбор)."""
    return relevant_error_line(stderr, fallback="first")


# ---------------------------------------------------------------------------
#  Per-error-code handlers
# ---------------------------------------------------------------------------

def _handle_service_disabled(exit_code: int, stderr: str) -> WinDivertDiagnosis:
    """ERROR_SERVICE_DISABLED (1058) — the most common WinDivert error."""
    # Run sub-checks to narrow down the cause
    cause, solution, auto_fix = _probe_service_disabled_cause()
    return WinDivertDiagnosis(
        cause=cause, solution=solution, auto_fix=auto_fix, severity="critical",
    )


def _handle_invalid_image_hash(exit_code: int, stderr: str) -> WinDivertDiagnosis:
    secure_boot = _check_secure_boot()
    if secure_boot:
        return WinDivertDiagnosis(
            cause="Secure Boot блокирует загрузку драйвера WinDivert",
            solution="Отключите Secure Boot в BIOS/UEFI настройках",
            severity="critical",
        )
    return _diagnosis_from_table(_ERROR_INVALID_IMAGE_HASH)


def _handle_access_denied(exit_code: int, stderr: str) -> WinDivertDiagnosis:
    try:
        import ctypes
        if not ctypes.windll.shell32.IsUserAnAdmin():
            return WinDivertDiagnosis(
                cause="Программа запущена без прав администратора",
                solution="Запустите программу от имени администратора",
                severity="critical",
            )
    except Exception:
        pass
    try:
        from winws_runtime.health.launch_conflicts import build_launch_conflict_advice

        conflict_advice = build_launch_conflict_advice()
        if conflict_advice is not None:
            cause, solution = conflict_advice
            return WinDivertDiagnosis(
                cause=cause,
                solution=solution,
                severity="critical",
            )
    except Exception:
        pass
    return _diagnosis_from_table(_ERROR_ACCESS_DENIED)


def _handle_driver_blocked(exit_code: int, stderr: str) -> WinDivertDiagnosis:
    return _diagnosis_from_table(_ERROR_DRIVER_BLOCKED)


def _handle_driver_prior_unload(exit_code: int, stderr: str) -> WinDivertDiagnosis:
    return _diagnosis_from_table(_ERROR_DRIVER_FAILED_PRIOR_UNLOAD)


def _handle_service_not_exist(exit_code: int, stderr: str) -> WinDivertDiagnosis:
    # Check if WinDivert files exist
    missing = _check_windivert_files()
    if missing:
        return WinDivertDiagnosis(
            cause=f"Отсутствуют файлы WinDivert: {', '.join(missing)}",
            solution="Переустановите программу или восстановите файлы из архива",
            severity="critical",
        )
    return _diagnosis_from_table(_ERROR_SERVICE_DOES_NOT_EXIST)


def _handle_dependency_fail(exit_code: int, stderr: str) -> WinDivertDiagnosis:
    bfe_ok = _check_bfe_service()
    if not bfe_ok:
        return WinDivertDiagnosis(
            cause="Служба Base Filtering Engine (BFE) не запущена",
            solution="Включите BFE: sc config BFE start= auto && net start BFE",
            auto_fix="enable_bfe",
            severity="critical",
        )
    return _diagnosis_from_table(_ERROR_SERVICE_DEPENDENCY_FAIL)


def _handle_not_enough_memory(exit_code: int, stderr: str) -> WinDivertDiagnosis:
    return _diagnosis_from_table(_ERROR_NOT_ENOUGH_MEMORY, severity="warning")


def _handle_gen_failure(exit_code: int, stderr: str) -> WinDivertDiagnosis:
    adapters_ok = _check_network_adapters()
    if not adapters_ok:
        return WinDivertDiagnosis(
            cause="Все сетевые адаптеры отключены",
            solution="Включите хотя бы один сетевой адаптер",
            auto_fix="enable_adapters",
            severity="critical",
        )
    return _diagnosis_from_table(_ERROR_GEN_FAILURE)


def _handle_invalid_parameter(exit_code: int, stderr: str) -> WinDivertDiagnosis:
    import re
    stderr_lower = (stderr or "").lower()

    if "incompatible nfqws2_compat_ver" in stderr_lower:
        return WinDivertDiagnosis(
            cause="winws2.exe и Lua-скрипты от разных версий",
            solution="Обновите папку lua вместе с winws2.exe или переустановите программу",
            severity="critical",
        )

    if "windivert" in stderr_lower and "error opening filter" in stderr_lower:
        if "the service cannot be started" in stderr_lower or "service is disabled" in stderr_lower:
            return _handle_service_disabled(exit_code, stderr)
        return WinDivertDiagnosis(
            cause="WinDivert не смог открыть фильтр",
            solution=_extract_relevant_error_line(stderr) or "Проверьте состояние драйвера WinDivert",
            severity="critical",
        )

    # Lua desync function not found — lua-init auto-fix didn't help,
    # meaning the .lua file itself is missing from disk.
    m = re.search(r"desync function '([^']+)' does not exist", stderr or "")
    if m:
        func_name = m.group(1)
        return WinDivertDiagnosis(
            cause=f"Lua-функция '{func_name}' не найдена — файл .lua отсутствует на диске",
            solution="Переустановите программу — файлы в папке lua/ повреждены или удалены",
            severity="critical",
        )

    # Lua script syntax/runtime error
    if "lua" in stderr_lower and ("error" in stderr_lower or "syntax" in stderr_lower):
        return WinDivertDiagnosis(
            cause="Ошибка в Lua-скрипте",
            solution="Переустановите программу или проверьте файлы в папке lua/",
            severity="critical",
        )

    return _diagnosis_from_table(_ERROR_INVALID_PARAMETER, severity="warning")


def _handle_bad_pathname(exit_code: int, stderr: str) -> WinDivertDiagnosis:
    missing = _check_windivert_files()
    diagnosis = _diagnosis_from_table(_ERROR_BAD_PATHNAME)
    if missing:
        diagnosis.cause += f": {', '.join(missing)}"
    return diagnosis


def _handle_process_aborted(exit_code: int, stderr: str) -> WinDivertDiagnosis:
    return _diagnosis_from_table(_ERROR_PROCESS_ABORTED)


def _handle_fwp_in_use(exit_code: int, stderr: str) -> WinDivertDiagnosis:
    """FWP_E_IN_USE — WinDivert держат остатки прошлого запуска или чужая программа.

    Базовый текст говорит «закройте другие программы», а подсказка о конфликте
    называет виновника по имени, если его удалось найти.
    """
    diagnosis = _diagnosis_from_table(_FWP_E_IN_USE)
    hint = describe_windivert_conflict_hint()
    if hint:
        diagnosis.solution = f"{hint}. {diagnosis.solution}"
    return diagnosis


# Handler dispatch table
_EXIT_CODE_HANDLERS = {
    _ERROR_SERVICE_DISABLED: _handle_service_disabled,
    _ERROR_INVALID_IMAGE_HASH: _handle_invalid_image_hash,
    _ERROR_ACCESS_DENIED: _handle_access_denied,
    _ERROR_DRIVER_BLOCKED: _handle_driver_blocked,
    _ERROR_DRIVER_FAILED_PRIOR_UNLOAD: _handle_driver_prior_unload,
    _ERROR_SERVICE_DOES_NOT_EXIST: _handle_service_not_exist,
    _ERROR_SERVICE_DEPENDENCY_FAIL: _handle_dependency_fail,
    _ERROR_NOT_ENOUGH_MEMORY: _handle_not_enough_memory,
    _ERROR_GEN_FAILURE: _handle_gen_failure,
    _ERROR_INVALID_PARAMETER: _handle_invalid_parameter,
    _ERROR_BAD_PATHNAME: _handle_bad_pathname,
    _ERROR_PROCESS_ABORTED: _handle_process_aborted,
    _FWP_E_IN_USE: _handle_fwp_in_use,
}


# ---------------------------------------------------------------------------
#  System probe helpers
# ---------------------------------------------------------------------------

def _probe_service_disabled_cause() -> Tuple[str, str, Optional[str]]:
    """Narrow down why 'service cannot be started' (1058).

    Returns (cause, solution, auto_fix_action).
    """
    # Check 1: WinDivert files missing (AV quarantine)
    missing = _check_windivert_files()
    if missing:
        return (
            f"Файлы WinDivert отсутствуют (возможно удалены антивирусом): {', '.join(missing)}",
            "Добавьте папку программы в исключения антивируса и переустановите",
            None,
        )

    # Check 2: BFE service
    if not _check_bfe_service():
        return (
            "Служба Base Filtering Engine (BFE) отключена — WinDivert зависит от неё",
            "Включите BFE: sc config BFE start= auto && net start BFE",
            "enable_bfe",
        )

    # Check 3: WinDivert/Monkey service explicitly disabled
    disabled_driver = _find_disabled_windivert_driver_service()
    if disabled_driver:
        return (
            f"Служба драйвера WinDivert ({disabled_driver}) отключена в системе",
            "Выполните аварийную очистку драйвера и повторите запуск",
            "cleanup_driver",
        )

    # Check 4: Kaspersky after a real WinDivert start failure.
    try:
        from winws_runtime.health.launch_conflicts import build_launch_conflict_advice

        conflict_advice = build_launch_conflict_advice()
        if conflict_advice is not None:
            cause, solution = conflict_advice
            return cause, solution, None
    except Exception:
        pass

    # Check 5: Antivirus
    av = _detect_active_antivirus()
    if av:
        return (
            f"Антивирус ({av}) может блокировать загрузку драйвера WinDivert",
            "Добавьте папку программы в исключения антивируса",
            None,
        )

    # Check 6: Network adapters. This check must be late because Win32 1058
    # is a generic service-disabled error and otherwise easily turns into a
    # ложный диагноз про адаптеры.
    if not _check_network_adapters():
        return (
            "Не найден ни один активный сетевой адаптер — WinDivert не к чему привязаться",
            "Включите хотя бы один сетевой адаптер в системе и повторите запуск",
            "enable_adapters",
        )

    # Check 7: драйвер зарегистрирован, но фильтр открыть нельзя.
    #
    # Проверка стоит последней намеренно. Probe выполняется уже после смерти
    # winws2 и с флагом NO_INSTALL, а WinDivert по умолчанию снимает свою
    # службу при закрытии последнего дескриптора. Поэтому "службы нет"
    # (ERROR_SERVICE_DOES_NOT_EXIST) — это обычное состояние покоя, а не
    # причина отказа: раньше эта ветка стояла четвёртой, срабатывала почти на
    # каждом падении 34/1058 и глушила проверки Kaspersky/антивируса/адаптеров.
    # Диагностическую ценность имеет только обратный случай: служба есть, а
    # NETWORK layer всё равно не открывается.
    try:
        from winws_runtime.runtime.system_ops import probe_windivert_state_runtime

        probe = probe_windivert_state_runtime()
        if probe.installed and not probe.ready:
            probe_code_suffix = (
                f" (код {int(probe.error_code)})" if probe.error_code is not None else ""
            )
            return (
                f"WinDivert ещё не готов после предыдущего запуска или очистки{probe_code_suffix}",
                "Подождите пару секунд и попробуйте снова. Если повторяется — перезапустите программу или ПК",
                None,
            )
    except Exception:
        pass

    # Fallback: базовый текст 1058 из единой таблицы.
    record = WINDIVERT_ERROR_TABLE[_ERROR_SERVICE_DISABLED]
    return (record.cause, record.solution, record.auto_fix_action)


def _check_network_adapters() -> bool:
    """Return True if at least one network adapter is enabled/up."""
    try:
        from dns.public import get_network_adapters_native

        adapters = get_network_adapters_native()
        for adapter in adapters:
            adapter_type = int(adapter.get("type") or 0)
            if adapter_type == 24:  # MIB_IF_TYPE_LOOPBACK
                continue
            if adapter.get("index") or adapter.get("adapter_name") or adapter.get("name"):
                return True
        return False
    except Exception:
        return True  # assume OK on failure


def _check_windivert_files() -> List[str]:
    """Return list of missing critical WinDivert files."""
    import os

    # В поставке ZapretGUI драйвер обычно переименован в Monkey64.sys. Старое
    # имя WinDivert64.sys тоже поддерживаем, но наличие обоих файлов сразу не
    # требуется. Раньше здесь импортировалась несуществующая константа
    # WINDIVERT_FOLDER, из-за чего проверка целиком и незаметно пропускалась.
    required_groups = (
        ("WinDivert.dll", ("WinDivert.dll",)),
        (
            "драйвер WinDivert (Monkey64.sys или WinDivert64.sys)",
            ("Monkey64.sys", "WinDivert64.sys"),
        ),
    )
    missing: List[str] = []
    for label, candidates in required_groups:
        if not any(os.path.isfile(APPLICATION_PATHS.exe_dir / name) for name in candidates):
            missing.append(label)
    return missing


def _check_bfe_service() -> bool:
    """Return True if Base Filtering Engine service is running."""
    try:
        from startup.bfe_util import is_service_running

        return bool(is_service_running("BFE"))
    except Exception:
        return True  # assume OK


def _check_secure_boot() -> bool:
    """Return True if Secure Boot is ENABLED (via registry)."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\SecureBoot\State",
        )
        val, _ = winreg.QueryValueEx(key, "UEFISecureBootEnabled")
        winreg.CloseKey(key)
        return val == 1
    except Exception:
        return False  # key doesn't exist = Secure Boot not available


def _find_disabled_windivert_driver_service() -> Optional[str]:
    """Return disabled WinDivert-compatible service name, if present."""
    try:
        import winreg

        for service_name in _WINDIVERT_DRIVER_SERVICE_NAMES:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    fr"SYSTEM\CurrentControlSet\Services\{service_name}",
                    0,
                    winreg.KEY_READ,
                ) as key:
                    start_value, _ = winreg.QueryValueEx(key, "Start")
                    if int(start_value) == 4:
                        return service_name
            except FileNotFoundError:
                continue
        return None
    except Exception:
        return None


def _check_windivert_driver_disabled() -> bool:
    """Return True if any WinDivert-compatible service start type is DISABLED."""
    return _find_disabled_windivert_driver_service() is not None
