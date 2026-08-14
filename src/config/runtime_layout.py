from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys


RUNTIME_DIR_NAME = "_internal"
RUNTIME_EXE_NAME = "Zapret.exe"
UPDATE_STATE_VENDOR_DIR = "Zapret"
UPDATE_STATE_DIR_NAME = "update"


class SourceApplicationLaunchForbidden(RuntimeError):
    pass


def paths_overlap(first: str | Path, second: str | Path) -> bool:
    """True, если один путь содержит другой или пути совпадают."""
    try:
        first_parts = Path(first).resolve().parts
        second_parts = Path(second).resolve().parts
    except (OSError, ValueError):
        return False

    shared = min(len(first_parts), len(second_parts))
    if shared == 0:
        return False
    return [part.casefold() for part in first_parts[:shared]] == [
        part.casefold() for part in second_parts[:shared]
    ]


def resolve_update_state_dir(
    *,
    channel: str,
    application_root: str | Path,
    program_data: str | None,
    local_app_data: str | None,
) -> Path:
    """Каталог состояния обновления за пределами каталога установки.

    Сбойное обновление стирает каталог установки вместе с сохранённым
    установщиком и логами — именно поэтому после аварии не остаётся ни
    средства восстановления, ни диагностики. Состояние обновления поэтому
    живёт снаружи: в ``%ProgramData%``, при его недоступности — в
    ``%LOCALAPPDATA%``. Прежний каталог внутри установки остаётся последним
    запасным вариантом, когда обеих системных папок нет.
    """
    root = Path(application_root)
    leaf = (
        Path(UPDATE_STATE_VENDOR_DIR)
        / UPDATE_STATE_DIR_NAME
        / (str(channel or "").strip().lower() or "stable")
    )

    for base in (program_data, local_app_data):
        if not str(base or "").strip():
            continue
        candidate = Path(base) / leaf
        # Установка может лежать прямо в %ProgramData%: тогда «внешний»
        # каталог оказался бы внутри зоны удаления и потерял бы смысл.
        if paths_overlap(candidate, root):
            continue
        return candidate

    return root / "_update_cache"


@dataclass(frozen=True, slots=True)
class ApplicationPaths:
    """Все пути установленного приложения, вычисленные из одного корня."""

    root: Path

    @classmethod
    def from_root(cls, root: str | Path) -> "ApplicationPaths":
        return cls(root=Path(root).resolve())

    @property
    def runtime_dir(self) -> Path:
        return self.root / RUNTIME_DIR_NAME

    @property
    def executable(self) -> Path:
        return self.runtime_dir / RUNTIME_EXE_NAME

    @property
    def bin_dir(self) -> Path:
        return self.root / "bin"

    @property
    def exe_dir(self) -> Path:
        return self.root / "exe"

    @property
    def ico_dir(self) -> Path:
        return self.root / "ico"

    @property
    def json_dir(self) -> Path:
        return self.root / "json"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def hosts_catalog_database(self) -> Path:
        return self.data_dir / "hosts_catalog.sqlite3"

    @property
    def lists_dir(self) -> Path:
        return self.root / "lists"

    @property
    def lists_base_dir(self) -> Path:
        return self.lists_dir / "base"

    @property
    def lists_user_dir(self) -> Path:
        return self.lists_dir / "user"

    @property
    def lua_dir(self) -> Path:
        return self.root / "lua"

    @property
    def presets_dir(self) -> Path:
        return self.root / "presets"

    @property
    def profile_dir(self) -> Path:
        return self.root / "profile"

    @property
    def settings_dir(self) -> Path:
        return self.root / "settings"

    @property
    def settings_database(self) -> Path:
        return self.settings_dir / "settings.sqlite3"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def crash_logs_dir(self) -> Path:
        return self.logs_dir / "crashes"

    @property
    def tmp_dir(self) -> Path:
        return self.root / "tmp"

    @property
    def themes_dir(self) -> Path:
        return self.root / "themes"

    @property
    def sos_dir(self) -> Path:
        return self.root / "sos"

    @property
    def windivert_filter_dir(self) -> Path:
        return self.root / "windivert.filter"

    @property
    def update_cache_dir(self) -> Path:
        return self.root / "_update_cache"

    @property
    def stable_icon(self) -> Path:
        return self.ico_dir / "Zapret2.ico"

    @property
    def dev_icon(self) -> Path:
        return self.ico_dir / "ZapretDevLogo4.ico"

    @property
    def sidebar_icons_dir(self) -> Path:
        return self.ico_dir / "windows11_fluent" / "sidebar"


def is_packaged_runtime() -> bool:
    """True, только если процесс собран PyInstaller или Nuitka."""
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def require_packaged_application() -> None:
    """Запрещает вход в приложение из обычного Python-сценария."""
    if is_packaged_runtime():
        return
    raise SourceApplicationLaunchForbidden(
        "Запуск приложения из исходников запрещён. "
        f"Используйте установленный {RUNTIME_DIR_NAME}\\{RUNTIME_EXE_NAME}."
    )


def resolve_application_root(
    *,
    executable: str | Path,
    module_file: str | Path,
    packaged: bool,
) -> Path:
    """Возвращает единственный корень ресурсов и пользовательского состояния.

    Собранное приложение поддерживает только одну структуру::

        <app root>/_internal/Zapret.exe

    Тесты и сборочные инструменты могут импортировать модули из репозитория.
    Само приложение до импорта своих модулей обязано вызвать
    ``require_packaged_application``.
    """
    if packaged:
        runtime_root = Path(executable).resolve().parent
        if runtime_root.name.casefold() != RUNTIME_DIR_NAME.casefold():
            raise RuntimeError(
                "Некорректная структура установленного Zapret: "
                f"ожидался запуск из папки {RUNTIME_DIR_NAME}, получено {runtime_root}"
            )
        return runtime_root.parent

    # config/runtime_layout.py -> config -> src -> public_zapretgui
    return Path(module_file).resolve().parents[2]


def resolve_runtime_root(*, executable: str | Path, packaged: bool) -> Path | None:
    if not packaged:
        return None
    runtime_root = Path(executable).resolve().parent
    if runtime_root.name.casefold() != RUNTIME_DIR_NAME.casefold():
        raise RuntimeError(
            "Некорректная структура среды Zapret: "
            f"ожидалась папка {RUNTIME_DIR_NAME}, получено {runtime_root}"
        )
    return runtime_root


PACKAGED_RUNTIME = is_packaged_runtime()
APPLICATION_ROOT = resolve_application_root(
    executable=sys.executable,
    module_file=__file__,
    packaged=PACKAGED_RUNTIME,
)
RUNTIME_ROOT = resolve_runtime_root(
    executable=sys.executable,
    packaged=PACKAGED_RUNTIME,
)
APPLICATION_PATHS = ApplicationPaths.from_root(APPLICATION_ROOT)
# Импортируемые тесты и сборочные инструменты читают ресурсы из src. Само приложение
# из исходников всё равно останавливается require_packaged_application().
APPLICATION_RESOURCE_PATHS = (
    APPLICATION_PATHS
    if PACKAGED_RUNTIME
    else ApplicationPaths.from_root(APPLICATION_ROOT / "src")
)


__all__ = [
    "APPLICATION_ROOT",
    "APPLICATION_PATHS",
    "APPLICATION_RESOURCE_PATHS",
    "ApplicationPaths",
    "PACKAGED_RUNTIME",
    "RUNTIME_DIR_NAME",
    "RUNTIME_EXE_NAME",
    "RUNTIME_ROOT",
    "SourceApplicationLaunchForbidden",
    "is_packaged_runtime",
    "require_packaged_application",
    "resolve_application_root",
    "resolve_runtime_root",
]
