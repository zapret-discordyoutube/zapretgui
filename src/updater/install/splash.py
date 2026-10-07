from __future__ import annotations

"""Окно-продолжение: обновление видно на экране от загрузки до новой версии.

Цепочка:

1. Старая программа передала установку наблюдателю и вызывает
   ``show_restart_splash``: здесь раскладываются ``restart_splash.json``,
   PNG логотипа и скрипт, запускается PowerShell, и приложение коротко ждёт
   метку «окно показано». Только после неё старое окно закрывается — пустого
   промежутка нет.
2. Окно само следит за ``handoff.json`` наблюдателя и меняет этапы.
3. Новая версия после показа своего окна вызывает ``mark_update_app_ready``,
   и окно-продолжение гаснет.

Любая неудача здесь только пишется в журнал: обновление важнее красоты.
Модуль без Qt — его вызывает фоновый поток установки.
"""

import json
import os
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from log.log import log

from . import paths, watchdog
from .splash_script import render_splash_script


SPLASH_LOG_LEVEL = "🔁 UPDATE"
# Сколько старая программа ждёт, пока окно-продолжение встанет на место.
# Холодный PowerShell обычно укладывается в секунду; дольше не держим —
# установка важнее.
SPLASH_SHOWN_TIMEOUT_SECONDS = 4.0
SPLASH_SHOWN_POLL_SECONDS = 0.1
APP_PROCESS_NAME = "Zapret"


@dataclass(frozen=True, slots=True)
class RestartSplashSpec:
    """Что и где показать. Координаты — физические пиксели экрана."""

    x: int
    y: int
    width: int
    height: int
    title: str
    subtitle: str
    stages: tuple[str, str, str]
    footer: str
    window_title: str
    # «{done} из {total} файлов» — ход копирования на этапе установки.
    files_template: str = "{done} из {total} файлов"
    # Пояснение под названием этапа: готов, идёт сейчас, ещё впереди.
    statuses: tuple[str, str, str] = ("Готово", "Выполняется", "Ожидает")
    jokes: tuple[str, ...] = ()
    colors: dict = field(default_factory=dict)
    font_family: str = "Segoe UI"
    logo_png: bytes = b""

    def to_payload(
        self,
        *,
        logo_path: str,
        shown_path: str,
        ready_path: str,
        log_path: str,
        setup_log_path: str = "",
        expected_files: int = 0,
    ) -> dict:
        return {
            "x": int(self.x),
            "y": int(self.y),
            "width": int(self.width),
            "height": int(self.height),
            "texts": {
                "title": str(self.title),
                "subtitle": str(self.subtitle),
                "stages": [str(item) for item in self.stages],
                "footer": str(self.footer),
                "window_title": str(self.window_title),
                "files_template": str(self.files_template),
                "statuses": [str(item) for item in self.statuses],
            },
            "jokes": [str(item) for item in self.jokes if str(item or "").strip()],
            "colors": {str(key): str(value) for key, value in dict(self.colors).items()},
            "font_family": str(self.font_family or "Segoe UI"),
            "logo_path": str(logo_path),
            "shown_path": str(shown_path),
            "ready_path": str(ready_path),
            "log_path": str(log_path),
            "app_process_name": APP_PROCESS_NAME,
            "old_pid": int(os.getpid()),
            # Журнал установщика: по нему окно считает скопированные файлы.
            "setup_log_path": str(setup_log_path),
            "expected_files": max(int(expected_files), 0),
        }


def _atomic_write(target: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f"{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


INSTALLER_FILE_ENTRY = "-- File entry --"
INSTALLER_SUCCESS_LINE = "Installation process succeeded."
# Папки с данными пользователя и журналами установщик не ставит — в оценку
# числа файлов установки они не входят.
_NOT_INSTALLED_DIRS = frozenset({"user", "logs", "log", "update_cache"})


def _count_log_file_entries(setup_log: Path) -> int:
    """Сколько файлов поставила прошлая удачная установка (0 — неизвестно)."""
    try:
        text = setup_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    if INSTALLER_SUCCESS_LINE not in text:
        return 0
    return text.count(INSTALLER_FILE_ENTRY)


def _count_installed_files(root: Path, *, limit: int = 20000) -> int:
    count = 0
    for base, dirs, files in os.walk(root):
        if Path(base) == root:
            dirs[:] = [name for name in dirs if name.lower() not in _NOT_INSTALLED_DIRS]
        count += len(files)
        if count >= limit:
            break
    return count


def estimate_installer_files(*, setup_log: Path, install_root: Path | None) -> int:
    """Сколько файлов скопирует установщик — для честной полосы прогресса.

    Точнее всего журнал прошлого автообновления: установщик тот же. Иначе —
    число файлов программы на диске. 0 — неизвестно: окно покажет бегущий
    отрезок вместо процентов.
    """
    from_log = _count_log_file_entries(setup_log)
    if from_log > 0:
        return from_log
    if install_root is None:
        return 0
    try:
        return _count_installed_files(install_root)
    except OSError:
        return 0


def build_splash_command(*, script_path: Path, spec_path: Path, state_path: Path) -> tuple[str, ...]:
    return (
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-STA",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-SpecPath",
        str(spec_path),
        "-StatePath",
        str(state_path),
    )


def prepare_restart_splash(spec: RestartSplashSpec) -> tuple[str, ...]:
    """Раскладывает файлы окна и возвращает команду запуска."""
    state_dir = paths.update_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    script_path = paths.restart_splash_script_path()
    spec_path = paths.restart_splash_spec_path()
    logo_path = paths.restart_splash_logo_path()
    shown_path = paths.restart_splash_shown_path()
    ready_path = paths.update_app_ready_path()

    # Старые метки от прошлого обновления не должны ни погасить окно, ни
    # обмануть ожидание «показано».
    _remove(shown_path)
    _remove(ready_path)
    if spec.logo_png:
        _atomic_write(logo_path, bytes(spec.logo_png))
    else:
        _remove(logo_path)
    setup_log = paths.setup_log_path()
    payload = spec.to_payload(
        logo_path=str(logo_path) if spec.logo_png else "",
        shown_path=str(shown_path),
        ready_path=str(ready_path),
        log_path=str(paths.restart_splash_log_path()),
        setup_log_path=str(setup_log),
        expected_files=estimate_installer_files(setup_log=setup_log, install_root=_install_root()),
    )
    _atomic_write(spec_path, json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"))
    # С BOM: Windows PowerShell 5.1 без него читает русский текст в ANSI.
    _atomic_write(script_path, render_splash_script().encode("utf-8-sig"))
    return build_splash_command(
        script_path=script_path,
        spec_path=spec_path,
        state_path=paths.handoff_state_path(),
    )


def _install_root() -> Path | None:
    try:
        from config.runtime_layout import APPLICATION_PATHS

        return Path(APPLICATION_PATHS.root)
    except Exception:
        return None


def wait_for_splash_shown(
    shown_path: Path,
    *,
    timeout_seconds: float = SPLASH_SHOWN_TIMEOUT_SECONDS,
    poll_seconds: float = SPLASH_SHOWN_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> bool:
    deadline = monotonic() + float(timeout_seconds)
    while monotonic() < deadline:
        if shown_path.exists():
            return True
        sleep(poll_seconds)
    return shown_path.exists()


def _allow_splash_foreground() -> None:
    """Разрешает окну-продолжению выйти на передний план.

    Защита Windows от кражи фокуса ставит окно фонового процесса позади
    активного: окно-продолжение оказалось бы за Zapret, и после её закрытия
    наверх вышло бы чужое окно. Активное окно (наше) вправе передать фокус.
    """
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except Exception:
        pass


def show_restart_splash(
    spec: RestartSplashSpec | None,
    *,
    spawn: Callable[[Sequence[str]], bool] = watchdog.spawn_background,
    wait: Callable[[Path], bool] = wait_for_splash_shown,
) -> bool:
    """Показывает окно-продолжение. True — окно подтвердило, что стоит на экране."""
    if spec is None:
        return False
    try:
        command = prepare_restart_splash(spec)
    except Exception as exc:
        log(f"Окно-продолжение не подготовлено: {exc}", SPLASH_LOG_LEVEL)
        return False
    _allow_splash_foreground()
    if not spawn(command):
        log("Окно-продолжение не запустилось", SPLASH_LOG_LEVEL)
        return False
    if wait(paths.restart_splash_shown_path()):
        log("Окно-продолжение на экране: старая версия закрывается", SPLASH_LOG_LEVEL)
        return True
    log("Окно-продолжение не отозвалось вовремя — закрываемся без него", SPLASH_LOG_LEVEL)
    return False


def mark_update_app_ready(version: str) -> bool:
    """Новая версия открылась: окно-продолжение может гаснуть.

    Пишется, только если окно-продолжение ждёт (лежит его spec) — в обычный
    запуск программы каталог состояния не трогается.
    """
    try:
        if not paths.restart_splash_spec_path().exists():
            return False
        ready_path = paths.update_app_ready_path()
        _atomic_write(
            ready_path,
            json.dumps({"version": str(version or ""), "pid": os.getpid(), "at": time.time()}).encode("utf-8"),
        )
        # Отметка времени — главный сигнал для окна: сверяется с его запуском.
        os.utime(ready_path, None)
        return True
    except Exception as exc:
        log(f"Метка «новая версия открылась» не записана: {exc}", SPLASH_LOG_LEVEL)
        return False


__all__ = [
    "RestartSplashSpec",
    "build_splash_command",
    "estimate_installer_files",
    "mark_update_app_ready",
    "prepare_restart_splash",
    "show_restart_splash",
    "wait_for_splash_shown",
]
