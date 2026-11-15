from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys


# Running from source is allowed only outside Windows and only on purpose: the variable
# points at a folder prepared by scripts/prepare_dev_root.py (the same layout an install has).
DEV_ROOT_ENV = "ZAPRET_DEV_ROOT"

RUNTIME_DIR_NAME = "_internal"
RUNTIME_EXE_NAME = "Zapret.exe"
UPDATE_STATE_VENDOR_DIR = "Zapret"
UPDATE_STATE_DIR_NAME = "update"


class SourceApplicationLaunchForbidden(RuntimeError):
    pass


class InvalidInstallLayout(RuntimeError):
    """Собранный Zapret.exe лежит не в папке _internal внутри папки установки."""


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
    fallback_dir: str | Path | None = None,
) -> Path:
    """Каталог состояния обновления за пределами каталога установки.

    Сбойное обновление стирает каталог установки вместе с сохранённым
    установщиком и логами — именно поэтому после аварии не остаётся ни
    средства восстановления, ни диагностики. Состояние обновления поэтому
    живёт снаружи: в ``%ProgramData%``, при его недоступности — в
    ``%LOCALAPPDATA%``. Если обеих системных папок нет, последним запасным
    вариантом служит пользовательский каталог ``user\\update_cache``.
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

    # Обычно это ``APPLICATION_PATHS.update_cache_dir``: в Linux он в ~/.cache/zapretgui, а не в корне программы.
    return Path(fallback_dir) if fallback_dir is not None else root / "user" / "update_cache"


XDG_LAYOUT_ENV = "ZAPRET_XDG_LAYOUT"
XDG_APP_DIR_NAME = "zapretgui"


@dataclass(frozen=True, slots=True)
class ApplicationPaths:
    """Все пути установленного приложения, вычисленные из одного корня.

    Windows и запуск из подготовленной папки: всё под одним корнем (``root``), изменяемое состояние
    в ``root/user``, ``root/lists/user`` и ``root/presets``.

    Установка в Linux (``for_xdg_layout``): ``root`` (например ``/opt/zapretgui``) только для чтения,
    а то, что программа пишет, лежит в папках пользователя по XDG:

    * ``data_root``   (``~/.local/share/zapretgui``): пресеты (в том числе встроенные копии, которые программа
      обновляет сама), свои списки, Lua и фейки пользователя, память движка, премиум-данные;
    * ``config_root`` (``~/.config/zapretgui``): ``settings.sqlite3``;
    * ``state_root``  (``~/.local/state/zapretgui``): журналы и отчёты о падениях;
    * ``cache_root``  (``~/.cache/zapretgui``): временные файлы и скачанные обновления.
    """

    root: Path
    data_root: Path | None = None
    config_root: Path | None = None
    state_root: Path | None = None
    cache_root: Path | None = None

    @classmethod
    def from_root(cls, root: str | Path) -> "ApplicationPaths":
        return cls(root=Path(root).resolve())

    @classmethod
    def for_xdg_layout(cls, root: str | Path, environ: dict | None = None) -> "ApplicationPaths":
        """Корень только для чтения + папки пользователя по XDG Base Directory."""
        env = os.environ if environ is None else environ
        home = Path(str(env.get("HOME") or "").strip() or Path.home())

        def _base(variable: str, fallback: str) -> Path:
            value = str(env.get(variable) or "").strip()
            # По спецификации относительный путь в XDG-переменной игнорируется.
            return Path(value) if value and os.path.isabs(value) else home / fallback

        return cls(
            root=Path(root).resolve(),
            data_root=_base("XDG_DATA_HOME", ".local/share") / XDG_APP_DIR_NAME,
            config_root=_base("XDG_CONFIG_HOME", ".config") / XDG_APP_DIR_NAME,
            state_root=_base("XDG_STATE_HOME", ".local/state") / XDG_APP_DIR_NAME,
            cache_root=_base("XDG_CACHE_HOME", ".cache") / XDG_APP_DIR_NAME,
        )

    @property
    def user_data_root(self) -> Path:
        """Корень, под которым лежат ``presets/`` (программа пишет туда и свои, и встроенные пресеты)."""
        return self.data_root if self.data_root is not None else self.root

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
        return self.system_dir / "ico"

    @property
    def system_dir(self) -> Path:
        """Каталог, который целиком кладёт и заменяет установщик."""
        return self.root / "system"

    @property
    def user_dir(self) -> Path:
        """Каталог пользовательского состояния; установщик его не трогает."""
        return self.data_root if self.data_root is not None else self.root / "user"

    @property
    def user_lua_dir(self) -> Path:
        """Рантайм-файлы оркестратора (генерируются приложением)."""
        return self.user_dir / "lua"

    @property
    def user_fakes_dir(self) -> Path:
        """Свои фейки пользователя (--blob=ИМЯ:@user/fakes/<файл>); установщик не трогает."""
        return self.user_dir / "fakes"

    @property
    def engine_state_dir(self) -> Path:
        """Файлы, которые пишет сам winws2 (память подбора стратегий).

        Движок после запуска понижает себе права и может писать только в
        каталог из параметра ``--writable=user/winws2``; остальная ``user``
        (настройки, базы) ему на запись закрыта.
        """
        return self.user_dir / "winws2"

    @property
    def hosts_catalog_database(self) -> Path:
        return self.system_dir / "hosts_catalog.sqlite3"

    @property
    def fakes_catalog_database(self) -> Path:
        """Реестр фейков winws2: только чтение, кладёт установщик."""
        return self.system_dir / "fakes_catalog.sqlite3"

    @property
    def strategy_catalogs_dir(self) -> Path:
        return self.system_dir / "strategy_catalogs"

    @property
    def profile_templates_dir(self) -> Path:
        return self.system_dir / "templates"

    @property
    def lists_dir(self) -> Path:
        return self.root / "lists"

    @property
    def lists_base_dir(self) -> Path:
        return self.lists_dir / "base"

    @property
    def lists_user_dir(self) -> Path:
        if self.data_root is not None:
            return self.data_root / "lists"
        return self.lists_dir / "user"

    @property
    def lua_dir(self) -> Path:
        return self.root / "lua"

    @property
    def presets_dir(self) -> Path:
        """Папка пресетов, которую программа читает и пишет (в Linux — в данных пользователя)."""
        return self.user_data_root / "presets"

    @property
    def shipped_presets_dir(self) -> Path:
        """Пресеты, которые положила поставка (только чтение). Из них при установке с XDG-папками
        программа заполняет ``presets_dir`` (см. ``config.xdg_seed``)."""
        return self.root / "presets"

    @property
    def settings_database(self) -> Path:
        base = self.config_root if self.config_root is not None else self.user_dir
        return base / "settings.sqlite3"

    @property
    def premium_database(self) -> Path:
        base = self.config_root if self.config_root is not None else self.user_dir
        return base / "premium.sqlite3"

    @property
    def logs_dir(self) -> Path:
        base = self.state_root if self.state_root is not None else self.user_dir
        return base / "logs"

    @property
    def crash_logs_dir(self) -> Path:
        return self.logs_dir / "crashes"

    @property
    def tmp_dir(self) -> Path:
        base = self.cache_root if self.cache_root is not None else self.user_dir
        return base / "tmp"

    @property
    def themes_dir(self) -> Path:
        return self.system_dir / "themes"

    @property
    def windivert_filter_dir(self) -> Path:
        return self.root / "windivert.filter"

    @property
    def update_cache_dir(self) -> Path:
        base = self.cache_root if self.cache_root is not None else self.user_dir
        return base / "update_cache"

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


def resolve_dev_root(*, environ: dict | None = None, platform: str | None = None) -> tuple[Path | None, str]:
    """Папка для запуска из исходников вне Windows: (путь, текст ошибки).

    Переменная ``ZAPRET_DEV_ROOT`` действует только вне Windows и не у собранной
    программы. Без неё запуск из исходников по-прежнему запрещён. Если переменная
    задана, но папки нет, возвращается понятная ошибка, а не молчаливый отказ.
    """
    env = os.environ if environ is None else environ
    if (sys.platform if platform is None else platform) == "win32":
        return None, ""
    value = str(env.get(DEV_ROOT_ENV) or "").strip()
    if not value:
        return None, ""
    path = Path(value).expanduser()
    if not path.is_dir():
        return None, f"{DEV_ROOT_ENV} указывает на несуществующую папку: {path}"
    return path.resolve(), ""


def is_dev_root_run() -> bool:
    """True, если приложение запущено из исходников через ZAPRET_DEV_ROOT."""
    return DEV_ROOT is not None


def require_packaged_application() -> None:
    """Запрещает вход в приложение из обычного Python-сценария."""
    if is_packaged_runtime() or DEV_ROOT is not None:
        return
    if DEV_ROOT_ERROR:
        raise SourceApplicationLaunchForbidden(DEV_ROOT_ERROR)
    from platforms import current as current_platform

    raise SourceApplicationLaunchForbidden(current_platform().launch.source_launch_message())


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
            raise InvalidInstallLayout(
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
        raise InvalidInstallLayout(
            "Некорректная структура среды Zapret: "
            f"ожидалась папка {RUNTIME_DIR_NAME}, получено {runtime_root}"
        )
    return runtime_root


def require_valid_install_layout() -> None:
    """Останавливает запуск, если Zapret.exe лежит не в папке _internal."""
    if INSTALL_LAYOUT_ERROR is not None:
        raise INSTALL_LAYOUT_ERROR


PACKAGED_RUNTIME = is_packaged_runtime()
DEV_ROOT, DEV_ROOT_ERROR = (None, "") if PACKAGED_RUNTIME else resolve_dev_root()
INSTALL_LAYOUT_ERROR: InvalidInstallLayout | None = None
try:
    APPLICATION_ROOT = resolve_application_root(
        executable=sys.executable,
        module_file=__file__,
        packaged=PACKAGED_RUNTIME,
    )
    RUNTIME_ROOT = resolve_runtime_root(
        executable=sys.executable,
        packaged=PACKAGED_RUNTIME,
    )
    if DEV_ROOT is not None:
        APPLICATION_ROOT = DEV_ROOT
except InvalidInstallLayout as exc:
    # Импорт этого модуля не должен падать: иначе main.launch_gate не успеет
    # показать пользователю окно. Эти пути в работе не используются —
    # проверка запуска останавливает программу раньше.
    INSTALL_LAYOUT_ERROR = exc
    APPLICATION_ROOT = Path(sys.executable).resolve().parent
    RUNTIME_ROOT = None
def uses_xdg_layout(*, environ: dict | None = None, platform: str | None = None, packaged: bool | None = None) -> bool:
    """True, если изменяемое состояние живёт в XDG-папках пользователя, а корень программы только для чтения.

    Всегда в собранной программе под Linux. Из подготовленной папки (``ZAPRET_DEV_ROOT``) — по желанию:
    ``ZAPRET_XDG_LAYOUT=1`` проверяет раскладку без установки. Windows всегда в одной папке.
    """
    env = os.environ if environ is None else environ
    system = sys.platform if platform is None else platform
    if not system.startswith("linux"):
        return False
    if (PACKAGED_RUNTIME if packaged is None else packaged):
        return True
    return str(env.get(XDG_LAYOUT_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


APPLICATION_PATHS = (
    ApplicationPaths.for_xdg_layout(APPLICATION_ROOT) if uses_xdg_layout() else ApplicationPaths.from_root(APPLICATION_ROOT)
)
# Импортируемые тесты и сборочные инструменты читают ресурсы из src. Само приложение
# из исходников всё равно останавливается require_packaged_application().
APPLICATION_RESOURCE_PATHS = (
    APPLICATION_PATHS
    if PACKAGED_RUNTIME or DEV_ROOT is not None
    else ApplicationPaths.from_root(APPLICATION_ROOT / "src")
)


__all__ = [
    "APPLICATION_ROOT",
    "APPLICATION_PATHS",
    "APPLICATION_RESOURCE_PATHS",
    "ApplicationPaths",
    "DEV_ROOT",
    "DEV_ROOT_ENV",
    "DEV_ROOT_ERROR",
    "INSTALL_LAYOUT_ERROR",
    "InvalidInstallLayout",
    "PACKAGED_RUNTIME",
    "RUNTIME_DIR_NAME",
    "RUNTIME_EXE_NAME",
    "RUNTIME_ROOT",
    "SourceApplicationLaunchForbidden",
    "XDG_LAYOUT_ENV",
    "uses_xdg_layout",
    "is_dev_root_run",
    "is_packaged_runtime",
    "resolve_dev_root",
    "require_packaged_application",
    "require_valid_install_layout",
    "resolve_application_root",
    "resolve_runtime_root",
]
