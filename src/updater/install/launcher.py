from __future__ import annotations

"""Передача проверенного установщика наблюдателю.

Единственный путь запуска установщика и для обновления, и для починки
поставки. Порядок важен:

1. каталог состояния закрывается от записи обычными пользователями;
2. установщик и его метаданные кладутся туда же;
3. прежние наблюдатели останавливаются, пишется запись ``prepared``;
4. ставится страховка ``RunOnce`` и запускается наблюдатель;
5. если наблюдатель не отозвался, запись и страховка снимаются — опоздавший
   наблюдатель увидит отмену и ничего не запустит.
"""

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from config.runtime_layout import APPLICATION_PATHS
from log.log import log

from . import paths, watchdog
from .handoff import HandoffState, UpdateHandoffRecord, clear_record, write_record
from .recovery_hook import build_recovery_command, clear_recovery_hook, set_recovery_hook


UPDATE_LOG_LEVEL = "🔁 UPDATE"

# Просьба к установщику открыть новую версию в трее, а не окном. Её понимает
# установщик той версии, на которую идёт обновление; более старый незнакомый
# параметр просто не заметит и откроет окно, как раньше.
START_IN_TRAY_ARGUMENT = "/STARTINTRAY"

_state_dir_hardened = False


@dataclass(frozen=True, slots=True)
class InstallerHandoff:
    """Проверенный установщик, готовый к передаче наблюдателю."""

    version: str
    installer_path: str
    installer_sha256: str
    arguments: tuple[str, ...]

    def starting_in_tray(self) -> "InstallerHandoff":
        """Та же передача, но новая версия откроется в трее.

        Нужна обновлению, которое программа ставит сама, пока свёрнута в
        трей: окно, которого на экране не было, не должно появиться само.
        """
        if START_IN_TRAY_ARGUMENT in self.arguments:
            return self
        return InstallerHandoff(
            version=self.version,
            installer_path=self.installer_path,
            installer_sha256=self.installer_sha256,
            arguments=(*self.arguments, START_IN_TRAY_ARGUMENT),
        )


def ensure_private_state_dir() -> None:
    """Один раз за запуск закрывает каталог состояния от записи.

    Неудача не останавливает обновление: права каталога тогда остаются
    прежними, а наблюдатель всё равно сверит SHA-256 перед запуском.
    """
    global _state_dir_hardened

    if _state_dir_hardened:
        return
    _state_dir_hardened = paths.harden_state_dir()


def reset_state_dir_hardening() -> None:
    """Сбрасывает отметку о закрытом каталоге. Только для тестов."""
    global _state_dir_hardened

    _state_dir_hardened = False


def installer_arguments(*, log_name: str = paths.SETUP_LOG_NAME) -> tuple[str, ...]:
    """Единственный набор аргументов Inno Setup для установки без вопросов.

    ``/SUPPRESSMSGBOXES`` здесь недопустим: в Inno он означает ответ Abort в
    ситуациях Abort/Retry, то есть превращает сбой распаковки в молчаливый
    выход без единого сообщения. Без него сообщения об ошибках установщик
    показывает и в ``/VERYSILENT``. Своё окно прогресса ему не нужно: ход
    обновления показывает окно-продолжение (``splash``), и вторая полоска
    поверх него только мешала бы.
    """
    setup_log = paths.setup_log_path(log_name)
    return (
        "/AUTOUPDATE",
        "/VERYSILENT",
        "/NORESTART",
        "/NOCANCEL",
        "/CLOSEAPPLICATIONS",
        f"/DIR={APPLICATION_PATHS.root}",
        f"/LOG={setup_log}",
    )


def read_cached_installer_meta() -> dict:
    """Метаданные последнего сохранённого установщика.

    Нужны восстановлению поставки: по ним видно, какой версии лежит файл и
    какой у него SHA-256, поэтому починка возможна без сети — а без движка
    сеть у пользователя как раз может не работать.
    """
    try:
        raw = paths.cached_installer_meta_path().read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f"{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as file_obj:
            file_obj.write(data)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_copy(source: Path, target: Path, checkpoint: Callable[[], None]) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f"{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as output, open(source, "rb") as source_file:
            shutil.copyfileobj(source_file, output, length=1024 * 1024)
        checkpoint()
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def stage_installer(
    downloaded_path: str | Path,
    *,
    version: str,
    sha256: str,
    size: int,
    checkpoint: Callable[[], None] = lambda: None,
    move: bool = False,
) -> InstallerHandoff:
    """Кладёт уже проверенный установщик в каталог, переживающий переустановку.

    ``move=True`` — файл уже скачан в этот же каталог: достаточно переименовать
    его, без второй записи на диск.
    """
    ensure_private_state_dir()
    checkpoint()
    source = Path(downloaded_path)
    target = paths.cached_installer_path()
    if os.path.normcase(os.path.abspath(source)) != os.path.normcase(os.path.abspath(target)):
        if move:
            os.replace(source, target)
        else:
            _atomic_copy(source, target, checkpoint)

    try:
        _atomic_write_bytes(
            paths.cached_installer_meta_path(),
            json.dumps(
                {"version": str(version), "sha256": str(sha256), "size": int(size)},
                ensure_ascii=False,
            ).encode("utf-8"),
        )
    except OSError as exc:
        log(f"Не удалось сохранить метаданные установщика: {exc}", "WARNING")

    return InstallerHandoff(
        version=str(version),
        installer_path=str(target),
        installer_sha256=str(sha256),
        arguments=installer_arguments(),
    )


def start_supervised_installation(
    handoff: InstallerHandoff,
    *,
    gui_pid: int | None = None,
    is_admin: Callable[[], bool] = watchdog.is_admin,
    spawn_background: Callable[..., bool] = watchdog.spawn_background,
    spawn_elevated: Callable[..., bool] = watchdog.spawn_elevated,
    stop_watchdogs: Callable[..., int] = watchdog.stop_watchdogs,
    wait_for_start: Callable[[Path], bool] = watchdog.wait_for_watchdog_start,
) -> bool:
    """Передаёт установку наблюдателю и ставит системную страховку.

    ``gui_pid`` — процесс, закрытия которого наблюдатель ждёт перед запуском
    установщика. Обновление передаёт свой PID и закрывается само; починка
    передаёт 0: приложение остаётся открытым, его закроет установщик.

    Возвращает True, только если наблюдатель подтвердил запуск своим журналом:
    иначе закрывать приложение нельзя — установку никто не доведёт.
    """
    ensure_private_state_dir()
    state_path = paths.handoff_state_path()
    script_path = paths.watchdog_script_path()

    stop_watchdogs((script_path, paths.legacy_watchdog_script_path()))

    record = UpdateHandoffRecord(
        state=HandoffState.PREPARED,
        version=handoff.version,
        target_root=str(APPLICATION_PATHS.root),
        installer_path=handoff.installer_path,
        installer_sha256=handoff.installer_sha256,
        arguments=tuple(handoff.arguments),
        gui_pid=int(os.getpid() if gui_pid is None else gui_pid),
    )
    if not write_record(record, state_path):
        return False

    try:
        watchdog.install_watchdog_script(script_path)
    except OSError as exc:
        log(f"❌ Не удалось разложить наблюдателя обновления: {exc}", "🔁❌ ERROR")
        clear_record(state_path)
        return False

    hook_set = set_recovery_hook(
        build_recovery_command(
            watchdog.build_watchdog_command(
                script_path=script_path,
                state_path=state_path,
                recovery=True,
            )
        )
    )

    log_path = watchdog.rotate_watchdog_log()
    command = watchdog.build_watchdog_command(script_path=script_path, state_path=state_path)
    spawned = spawn_background(command) if is_admin() else spawn_elevated(command)

    if spawned and wait_for_start(log_path):
        log(f"✅ Наблюдатель обновления следит за установкой {handoff.version}", UPDATE_LOG_LEVEL)
        return True

    if spawned:
        log("❌ Наблюдатель обновления не подал признаков жизни", "🔁❌ ERROR")
    # Установка не начнётся: запись снимается, чтобы опоздавший наблюдатель
    # ничего не запустил, а страховка — чтобы вход в систему не «восстановил»
    # то, чего никто не ломал.
    clear_record(state_path)
    if hook_set:
        clear_recovery_hook()
    return False


__all__ = [
    "START_IN_TRAY_ARGUMENT",
    "InstallerHandoff",
    "ensure_private_state_dir",
    "installer_arguments",
    "read_cached_installer_meta",
    "reset_state_dir_hardening",
    "stage_installer",
    "start_supervised_installation",
]
