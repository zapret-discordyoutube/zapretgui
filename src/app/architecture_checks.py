from __future__ import annotations

import re
import sys
import ast
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
THIS_FILE = Path(__file__).resolve()


@dataclass(frozen=True, slots=True)
class Problem:
    path: Path
    line: int
    message: str
    text: str = ""

    def format(self) -> str:
        rel = self.path.relative_to(REPO_ROOT)
        suffix = f": {self.text.strip()}" if self.text.strip() else ""
        return f"{rel}:{self.line}: {self.message}{suffix}"


def _python_files() -> list[Path]:
    return [
        path
        for path in SRC_ROOT.rglob("*.py")
        if path.resolve() != THIS_FILE
    ]


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _page_name_dict_keys(path: Path, dict_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    for node in tree.body:
        value = None
        target_name = None
        if isinstance(node, ast.Assign):
            value = node.value
            for target in node.targets:
                if isinstance(target, ast.Name):
                    target_name = target.id
                    break
        elif isinstance(node, ast.AnnAssign):
            value = node.value
            if isinstance(node.target, ast.Name):
                target_name = node.target.id

        if target_name != dict_name or not isinstance(value, ast.Dict):
            continue

        keys: set[str] = set()
        for key in value.keys:
            if (
                isinstance(key, ast.Attribute)
                and isinstance(key.value, ast.Name)
                and key.value.id == "PageName"
            ):
                keys.add(key.attr)
        return keys
    return set()


def _under(path: Path, *parts: str) -> bool:
    rel = path.relative_to(REPO_ROOT).as_posix()
    return any(rel.startswith(part) for part in parts)


def _scan_lines(
    files: list[Path],
    pattern: re.Pattern[str],
    message: str,
    *,
    allowed_paths: set[str] | None = None,
) -> list[Problem]:
    allowed_paths = allowed_paths or set()
    problems: list[Problem] = []
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in allowed_paths:
            continue
        for index, line in enumerate(_lines(path), start=1):
            if pattern.search(line):
                problems.append(Problem(path, index, message, line))
    return problems


def check_removed_legacy_files() -> list[Problem]:
    removed_files = (
        "src/app_context.py",
        "src/ui/page_dependencies.py",
        "src/ui/page_method_dispatch.py",
        "src/ui/page_contracts.py",
        "src/ui/page_signals",
        "src/ui/window_display_state.py",
        "src/ui/window_signal_bindings.py",
        "src/ui/window_state_sync.py",
        "src/ui/state/main_window_state.py",
        "src/ui/state/app_runtime_state.py",
        # Проверкой Premium-статуса владеет donater/status_runtime.py.
        "src/donater/subscription_manager.py",
        "src/donater/subscription_worker.py",
        "src/donater/pairing_workflow.py",
    )
    problems: list[Problem] = []
    for rel in removed_files:
        path = REPO_ROOT / rel
        if path.exists():
            problems.append(Problem(path, 1, "старый файл не должен возвращаться"))
    return problems


def check_no_app_context(files: list[Path]) -> list[Problem]:
    return _scan_lines(
        files,
        re.compile(r"\b(app_context|AppContext|build_app_context|install_app_context|require_page_app_context|_require_app_context)\b"),
        "старый app_context/helper не должен использоваться",
    )


def check_no_app_runtime_context(files: list[Path]) -> list[Problem]:
    return _scan_lines(
        files,
        re.compile(r"\b(?:app_runtime|AppRuntime|window\.app_runtime|page\.window\(\)\.app_runtime)\.context\b"),
        "AppRuntime.context нельзя добавлять как новый общий app_context",
    )


def check_app_features_is_registry_only() -> list[Problem]:
    path = SRC_ROOT / "app" / "features.py"
    if not path.exists():
        return [Problem(path, 1, "app/features.py не найден")]
    return _scan_lines(
        [path],
        re.compile(r"\b(?:def\s+build_app_features|build_[a-z_]+feature|build_[a-z_]+_feature)\b"),
        "app/features.py должен быть registry dataclass; сборка feature живёт в app/feature_assembly.py",
    )


def check_no_page_signal_layer(files: list[Path]) -> list[Problem]:
    return _scan_lines(
        files,
        re.compile(
            r"\b(page_signals|connect_lazy_page_signals|connect_window_page_signals|"
            r"connect_page_signals|PageSignal|window_signal_bindings|"
            r"window_bindings_connected|_window_page_signals_connected|"
            r"_page_signal_bootstrap_complete|lazy_signal_connections|"
            r"finalize_page_signal_bootstrap)\b"
        ),
        "старый page_signals/window_signal_bindings слой не должен использоваться",
    )


def check_switches_use_aligned_switch_button(files: list[Path]) -> list[Problem]:
    """Переключатели создаются только через ui.widgets.aligned_switch.

    У стандартного SwitchButton ширина зависит от подписи «Вкл.»/«Выкл.»,
    и ползунок сдвигается при смене состояния.
    """
    return _scan_lines(
        files,
        re.compile(
            r"(?<![\w.\"'])SwitchButton\s*[(,)]"
            r"|=\s*SwitchButton\b"
            r"|\bimport\b.*(?<![\w.\"'])SwitchButton\b"
        ),
        "используйте AlignedSwitchButton из ui/widgets/aligned_switch.py вместо SwitchButton",
        allowed_paths={"src/ui/widgets/aligned_switch.py"},
    )


def check_skip_float_in_is_allowlisted(files: list[Path]) -> list[Problem]:
    """Появление страниц не выключают: skip_float_in — только для виджетов,
    у которых вход уже есть свой (вкладки «О программе», девиз, шапка).

    Большому виджету с ручной отрисовкой дают play_float_in(delay_ms):
    общий модуль ui.widgets.stagger_float_in вызывает его в своей очереди.
    """
    return _scan_lines(
        files,
        re.compile(r"\bskip_float_in\s*\("),
        "не выключайте появление страницы — дайте виджету play_float_in (свой вход через ui.widgets.stagger_float_in)",
        allowed_paths={
            "src/ui/widgets/stagger_float_in.py",
            "src/ui/pages/about_page.py",
            "src/ui/pages/about_page_help_build.py",
            "src/ui/pages/about_page_kvn_build.py",
        },
    )


def check_no_window_level_state_subscriptions(files: list[Path]) -> list[Problem]:
    scopes = []
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == "src/ui/window_state_binder.py":
            continue
        if rel.startswith("src/main/") or (
            rel.startswith("src/ui/window_") and rel.endswith(".py")
        ):
            scopes.append(path)

    return _scan_lines(
        scopes,
        re.compile(r"\.subscribe\s*\("),
        "подписка окна на store/state должна жить в отдельном ui/window_state_binder.py",
    )


def check_runtime_feedback_uses_ui_bridge(files: list[Path]) -> list[Problem]:
    scopes = [
        path
        for path in files
        if _under(path, "src/winws_runtime/runtime/", "src/app/feature_facades/runtime")
    ]
    return _scan_lines(
        scopes,
        re.compile(r"\b(?:notify_threadsafe|notify_runner_launch_error_threadsafe|set_status_callback)\b"),
        "runtime feedback должен идти через RuntimeEvents/RuntimeUiBridge, а не отдельный callback окна",
    )


def check_runtime_ui_bridge_is_feature_neutral() -> list[Problem]:
    path = SRC_ROOT / "ui" / "runtime_ui_bridge.py"
    if not path.exists():
        return [Problem(path, 1, "runtime_ui_bridge.py не найден")]
    return _scan_lines(
        [path],
        re.compile(
            r"\b(?:premium|dns|hosts|presets|preset_|WindowUiSession|"
            r"get_window_ui_session|app_runtime|window\.)\b",
            re.IGNORECASE,
        ),
        "runtime_ui_bridge должен быть нейтральным runtime -> UI мостом без feature-логики",
    )


def check_preset_display_state_not_in_window_layer(files: list[Path]) -> list[Problem]:
    scopes = []
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("src/main/") or (
            rel.startswith("src/ui/window_") and rel.endswith(".py")
        ):
            scopes.append(path)

    return _scan_lines(
        scopes,
        re.compile(
            r"\b(?:set_current_strategy_summary|resolve_profile_strategy_display_state|"
            r"ProfileStrategyDisplayState|from presets\.display_state|import presets\.display_state)\b"
        ),
        "profile/preset display state должен считаться в presets/display_state.py, а не в window/main",
    )


def check_window_state_sync_is_window_only() -> list[Problem]:
    path = SRC_ROOT / "main" / "window_state_sync.py"
    if not path.exists():
        return [Problem(path, 1, "main/window_state_sync.py не найден")]
    return _scan_lines(
        [path],
        re.compile(
            r"\b(?:app_runtime\.features|features\.|get_premium_state|subscription_manager|status_runtime|"
            r"load_premium_effects|init_holiday_effects_from_settings|load_background_preset|"
            r"HolidayEffectsManager|apply_aero_effect|apply_window_background)\b"
        ),
        "window_state_sync.py должен применять состояние окна, а не ходить в feature-сервисы",
    )


def check_page_navigation_uses_page_host(files: list[Path]) -> list[Problem]:
    scopes = []
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == "src/ui/page_host.py":
            continue
        if rel.startswith("src/main/") or rel.startswith("src/ui/"):
            scopes.append(path)

    return _scan_lines(
        scopes,
        re.compile(
            r"(?:\bstackedWidget\.(?:addWidget|setCurrentWidget|currentWidget)\b|"
            r"\bnavigationInterface\.setCurrentItem\b|"
            r"\bpage_stack_bootstrap_complete\s*=)"
        ),
        "page/navigation lifecycle должен идти через WindowPageHost/WindowUiSession",
    )


def check_main_window_not_business_container(files: list[Path]) -> list[Problem]:
    scopes = [
        path for path in files
        if _under(path, "src/main/", "src/ui/") or path.relative_to(REPO_ROOT).as_posix() == "src/tray.py"
    ]
    return _scan_lines(
        scopes,
        re.compile(
            r"\b(?:window|self|app)\."
            r"(?:app_context|app_runtime_state|launch_controller|"
            r"launch_runtime|launch_runtime_api|process_monitor_manager|"
            r"subscription_manager|status_runtime|tray_manager)\b"
        ),
        "MainWindow/UI не должны хранить бизнес-сервис или старое состояние",
    )


def check_window_not_store_access_point(files: list[Path]) -> list[Problem]:
    scopes = []
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("src/main/window") or (
            rel.startswith("src/ui/window_") and rel.endswith(".py")
        ):
            scopes.append(path)

    return _scan_lines(
        scopes,
        re.compile(
            r"(?:\b(?:window|self|app)\._app_runtime_state\b|"
            r"\bdef\s+_?app_runtime_state\b)"
        ),
        "окно не должно возвращать старый app_runtime_state access point",
    )


def check_window_does_not_build_app_runtime() -> list[Problem]:
    scopes = [
        SRC_ROOT / "main" / "window.py",
        SRC_ROOT / "main" / "window_startup.py",
    ]
    return _scan_lines(
        [path for path in scopes if path.exists()],
        re.compile(r"\b(?:from app\.runtime import build_app_runtime|build_app_runtime\s*\()"),
        "главное окно не должно собирать AppRuntime; это делает ApplicationController",
    )


def check_window_has_no_bootstrap_wrapper() -> list[Problem]:
    scopes = [
        SRC_ROOT / "main" / "window.py",
        SRC_ROOT / "main" / "window_startup.py",
    ]
    return _scan_lines(
        [path for path in scopes if path.exists()],
        re.compile(r"\bdef\s+window_bootstrap"),
        "создание окна должно идти напрямую через ApplicationController",
    )


def check_app_runtime_access_is_narrow(files: list[Path]) -> list[Problem]:
    scopes = [
        path for path in files
        if _under(path, "src/main/", "src/ui/")
    ]
    return _scan_lines(
        scopes,
        re.compile(r"\b(?:self|window)\.app_runtime\b"),
        "окно не должно хранить AppRuntime; передавайте нужные зависимости явно",
    )


def check_window_feature_aliases_not_used(files: list[Path]) -> list[Problem]:
    scopes = []
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("src/main/window") or rel.startswith("src/ui/window_"):
            scopes.append(path)

    return _scan_lines(
        scopes,
        re.compile(
            r"\b(?:self|window)\."
            r"(?:runtime_feature|premium_feature|presets_feature|profile_feature|"
            r"dns_feature|hosts_feature|lists_feature|telegram_proxy_feature|"
            r"tray_feature|updater_feature|orchestra_feature)\b"
        ),
        "окно не должно хранить feature как свои поля; используйте явные deps",
    )


def check_window_hidden_dependency_bags_not_used(files: list[Path]) -> list[Problem]:
    scopes = [
        path for path in files
        if _under(path, "src/main/window", "src/ui/window_")
    ]
    return _scan_lines(
        scopes,
        re.compile(
            r"\b(?:WindowStartupDeps|WindowStateSyncDeps|"
            r"attach_window_[a-z_]+deps|_window_[a-z_]+deps)\b"
        ),
        "окно не должно хранить скрытые deps-контейнеры; подключайте явные callbacks/объекты из сборочного слоя",
    )


def check_tray_uses_window_port() -> list[Problem]:
    scopes = [
        SRC_ROOT / "tray.py",
        SRC_ROOT / "tray_commands.py",
        SRC_ROOT / "main" / "tray_window_port.py",
    ]
    existing_scopes = [path for path in scopes if path.exists()]
    if not existing_scopes:
        return []
    return _scan_lines(
        existing_scopes,
        re.compile(
            r"\b(?:def __init__\(self,\s*parent|self\.parent|"
            r"parent\s*=\s*(?:host|qt_parent|window)|"
            r"window_port\.qt_parent\s*\(|def\s+qt_parent\s*\()\b"
        ),
        "SystemTrayManager должен работать через методы TrayWindowPort, а не получать главное окно/qt_parent наружу",
    )


def check_application_icon_has_single_owner(files: list[Path]) -> list[Problem]:
    scopes = [
        path
        for path in files
        if path.relative_to(REPO_ROOT).as_posix() != "src/main/qt_runtime.py"
    ]
    return _scan_lines(
        scopes,
        re.compile(r"\.setWindowIcon\s*\("),
        "общий значок задаёт только main/qt_runtime.py до создания окна",
    )


def check_window_runtime_setup_is_thin() -> list[Problem]:
    path = SRC_ROOT / "main" / "window_runtime_setup.py"
    if not path.exists():
        return []
    return _scan_lines(
        [path],
        re.compile(
            r"\b(?:"
            r"WindowNotificationCenter|WindowGeometryRuntime|WindowCloseFlow|"
            r"ApplicationLifecycle|PageDepsContext|WindowUiRoot|"
            r"on_open_profile_setup|on_background_refresh_needed|show_active_mode_control_page"
            r")\b"
        ),
        "window_runtime_setup.py должен быть тонким координатором; подробная сборка живёт в отдельных setup-файлах",
    )


def check_window_feature_deps_use_explicit_port() -> list[Problem]:
    path = SRC_ROOT / "main" / "window_feature_deps.py"
    if not path.exists():
        return [Problem(path, 1, "window_feature_deps.py не найден")]
    return _scan_lines(
        [path],
        re.compile(
            r"(?:def\s+build_window_feature_deps\s*\(\s*window\s*(?:,|\))|"
            r"\bwindow\.|getattr\(\s*window\b)"
        ),
        "window_feature_deps.py должен строиться из FeatureWindowDeps, а не вытаскивать зависимости из полного окна",
    )


def check_application_controller_uses_port_builders() -> list[Problem]:
    path = SRC_ROOT / "main" / "application_controller.py"
    if not path.exists():
        return [Problem(path, 1, "application_controller.py не найден")]
    return _scan_lines(
        [path],
        re.compile(r"\bFeatureWindowDeps\s*\("),
        "ApplicationController не должен вручную собирать FeatureWindowDeps из window; используйте отдельный port builder",
    )


def check_window_page_actions_is_callback_bag() -> list[Problem]:
    path = SRC_ROOT / "main" / "window_page_actions.py"
    if not path.exists():
        return [Problem(path, 1, "window_page_actions.py не найден")]
    return _scan_lines(
        [path],
        re.compile(r"(?:^\s*window\s*:|\bself\.window\b|\bpage_actions\.window\b|\bpage_actions\.appearance_actions\b)"),
        "WindowPageActions не должен хранить полное окно; он должен быть набором явных callback-ов",
    )


def check_application_lifecycle_uses_window_port() -> list[Problem]:
    path = SRC_ROOT / "main" / "application_lifecycle.py"
    if not path.exists():
        return [Problem(path, 1, "application_lifecycle.py не найден")]
    return _scan_lines(
        [path],
        re.compile(r"(?:^\s*window\s*,|\bself\._window\s*=|\bself\._window\b)"),
        "ApplicationLifecycle не должен хранить главное окно напрямую; используйте lifecycle window port",
    )


def check_window_page_deps_setup_uses_actions() -> list[Problem]:
    path = SRC_ROOT / "main" / "window_page_deps_setup.py"
    if not path.exists():
        return [Problem(path, 1, "window_page_deps_setup.py не найден")]
    return _scan_lines(
        [path],
        re.compile(
            r"(?:build_window_page_deps_context\s*\(\s*window\s*(?:,|\))|"
            r"\bwindow\.(?:set_status|window_notification_center|app_runtime|ui_state_store|"
            r"runtime_feature|premium_feature|presets_feature|profile_feature|dns_feature|"
            r"hosts_feature|lists_feature|telegram_proxy_feature|tray_feature|updater_feature|"
            r"orchestra_feature)\b|"
            r"from ui\.(?:workflows|profile_setup_workflow|window_appearance_state))"
        ),
        "window_page_deps_setup.py должен получать callbacks через WindowPageActions, а не доставать их из окна",
    )


def check_post_startup_uses_explicit_host(files: list[Path]) -> list[Problem]:
    scopes = [
        path for path in files
        if path.relative_to(REPO_ROOT).as_posix().startswith("src/main/post_startup")
        or path.relative_to(REPO_ROOT).as_posix() == "src/main/application_post_startup.py"
    ]
    return _scan_lines(
        scopes,
        re.compile(
            r"(?:startup_host\s*=\s*window\b|"
            r"\bis_window_alive\b|"
            r"\bstartup_host\.qt_parent\s*\(|"
            r"\bdef\s+qt_parent\s*\(|"
            r"\b(?:window|startup_host)\.[a-z_]+_feature\b)"
        ),
        "post-startup задачи должны получать PostStartupHost и явные deps, а не окно как контейнер feature",
    )


def check_post_startup_builds_ui_only_in_user_pause(files: list[Path]) -> list[Problem]:
    """Сборка страницы или окна занимает GUI-поток на десятки миллисекунд.

    По таймеру она попадала на действия пользователя и давала рывки в первые
    секунды после запуска, поэтому поздние задачи строят интерфейс только
    через очередь пауз пользователя (main/post_startup_idle_tasks.py).
    """
    builds_ui = re.compile(r"\bstartup_host\.(?:ensure_page|show_whats_new)\s*\(")
    owners = {
        "src/main/post_startup_host.py",
        "src/main/post_startup_idle_tasks.py",
    }
    problems: list[Problem] = []
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if not rel.startswith("src/main/post_startup") or rel in owners:
            continue
        lines = _lines(path)
        if any("idle_tasks.add(" in line for line in lines):
            continue
        for index, line in enumerate(lines, start=1):
            if builds_ui.search(line):
                problems.append(
                    Problem(
                        path,
                        index,
                        "поздняя задача строит страницу или окно: это должно идти через idle_tasks.add(...), "
                        "а не по таймеру",
                        line,
                    )
                )
    return problems


# Создание элементов окна, которое запрещено фоновому коду.
_WIDGET_CREATION_RE = re.compile(
    r"(?:\b(?:InfoBar|InfoBarHelper|TeachingTip|Flyout)\s*\.\s*\w+\s*\(|"
    r"\b(?:MessageBox|MessageBoxBase|Dialog)\s*\(|"
    r"^\s*(?:from\s+qfluentwidgets\b|import\s+qfluentwidgets\b))"
)
_QTHREAD_SUBCLASS_RE = re.compile(r"^\s*class\s+\w+\s*\([^)]*\bQThread\b[^)]*\)\s*:")


def is_background_code_module(rel_path: str, source: str) -> bool:
    """Модуль, чей код по назначению выполняется вне потока окна.

    Это фоновые задачи запуска (main/post_startup*), модули работников и
    загрузчиков (в имени файла worker или loader) и любой модуль, где объявлен
    наследник QThread.
    """
    name = rel_path.rsplit("/", 1)[-1]
    if rel_path.startswith("src/main/post_startup"):
        return True
    if "worker" in name or "loader" in name:
        return True
    return any(_QTHREAD_SUBCLASS_RE.match(line) for line in source.splitlines())


def check_background_code_does_not_create_widgets(sources: list[tuple[Path, str]]) -> list[Problem]:
    """Фоновый код не создаёт элементы окна.

    Создание элемента окна из фонового потока на Windows останавливает
    программу целиком: вызывающий поток ждёт ответа потока окна, а тот не
    может ответить, пока вызывающий держит GIL (Dev 21.1.7.19 — уведомление
    из фоновой задачи запуска). По коду не видно, в каком потоке окажется
    вызов, поэтому модулям, которые по назначению работают в фоне, плашки,
    диалоги и виджеты qfluentwidgets запрещены вовсе. Уведомление показывается
    через notify центра уведомлений (ui/window_notification_center.py),
    остальное — сигналом Qt, слот которого живёт в потоке окна.
    """
    problems: list[Problem] = []
    for path, source in sources:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if not is_background_code_module(rel, source):
            continue
        for index, line in enumerate(source.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if _WIDGET_CREATION_RE.search(line):
                problems.append(
                    Problem(
                        path,
                        index,
                        "фоновый код создаёт элемент окна: из фонового потока это останавливает программу. "
                        "Уведомление показывать через notify центра уведомлений, остальное — сигналом Qt "
                        "в поток окна",
                        line,
                    )
                )
    return problems


def check_discord_tray_command_does_not_receive_window() -> list[Problem]:
    scopes = [
        SRC_ROOT / "tray.py",
        SRC_ROOT / "tray_commands.py",
        SRC_ROOT / "discord" / "discord_restart.py",
        SRC_ROOT / "app" / "feature_facades" / "tray.py",
    ]
    return _scan_lines(
        [path for path in scopes if path.exists()],
        re.compile(r"toggle_discord_restart\s*\([^)]*(?:qt_parent|window_port|window|host|parent)"),
        "Discord tray command не должен получать главное окно/Qt-parent; это отдельная команда настройки",
    )


def check_post_startup_does_not_use_window_as_feature_container() -> list[Problem]:
    path = SRC_ROOT / "main" / "post_startup.py"
    if not path.exists():
        return []
    return _scan_lines(
        [path],
        re.compile(r"\b(?:window|startup_host)\.[a-z_]+_feature\b|def\s+build_post_startup_deps\s*\("),
        "post_startup.py должен получать готовые PostStartupDeps, а не доставать feature из окна",
    )


def check_page_deps_context_not_stored_on_window(files: list[Path]) -> list[Problem]:
    scopes = [
        path for path in files
        if _under(path, "src/main/", "src/ui/")
    ]
    return _scan_lines(
        scopes,
        re.compile(
            r"(?:\bwindow\.page_deps_context\b|"
            r"\bself\.page_deps_context\b|"
            r"getattr\([^)]*page_deps_context)"
        ),
        "PageDepsContext должен передаваться в UiPageFactory/WindowUiRoot явно, а не храниться на окне",
    )


def check_no_qfluentwidgets_fallbacks(files: list[Path]) -> list[Problem]:
    ui_roots = (
        "src/ui/",
        "src/presets/ui/",
        "src/profile/ui/",
        "src/updater/ui/",
        "src/hosts/ui/",
        "src/fakes/ui/",
        "src/dns/ui/",
        "src/donater/ui/",
        "src/lists/ui/",
        "src/blockcheck/ui/",
        "src/autostart/ui/",
        "src/settings/dpi/",
        "src/orchestra/ui/",
    )
    scopes = [
        path for path in files
        if _under(path, *ui_roots) or path.relative_to(REPO_ROOT).as_posix() == "src/tray.py"
    ]
    fluent_flags = "|".join(
        re.escape(value)
        for value in (
            "HAS_" + "FLUENT",
            "_HAS_" + "FLUENT",
            "_USE_" + "FLUENT",
        )
    )
    return _scan_lines(
        scopes,
        re.compile(
            rf"\b(?:{fluent_flags})\b|"
            r"BreadcrumbBar\s*=\s*None|"
            r"except\s+ImportError\s*:"
        ),
        "production UI должен требовать qfluentwidgets, а не уходить в обычный Qt fallback",
    )


def check_no_legacy_toggle_widgets_in_production_ui(files: list[Path]) -> list[Problem]:
    ui_roots = (
        "src/ui/",
        "src/presets/ui/",
        "src/profile/ui/",
        "src/updater/ui/",
        "src/hosts/ui/",
        "src/fakes/ui/",
        "src/dns/ui/",
        "src/donater/ui/",
        "src/lists/ui/",
        "src/blockcheck/ui/",
        "src/autostart/ui/",
        "src/settings/dpi/",
        "src/orchestra/ui/",
    )
    scopes = [
        path for path in files
        if _under(path, *ui_roots)
    ]
    return _scan_lines(
        scopes,
        re.compile(
            r"(?:from\s+PyQt6\.QtWidgets\s+import[^\n]*\bQCheckBox\b|"
            r"\bQCheckBox\b|"
            r"\bWin11ToggleSwitch\b|"
            r"\bcheckbox_cls\s*=\s*QCheckBox\b)"
        ),
        "production UI должен использовать stock qfluentwidgets toggle/check widgets, а не QCheckBox/Win11ToggleSwitch",
    )


def check_no_raw_text_edit_in_production_ui(files: list[Path]) -> list[Problem]:
    ui_roots = (
        "src/ui/",
        "src/presets/ui/",
        "src/profile/ui/",
        "src/updater/ui/",
        "src/hosts/ui/",
        "src/fakes/ui/",
        "src/dns/ui/",
        "src/donater/ui/",
        "src/lists/ui/",
        "src/blockcheck/ui/",
        "src/autostart/ui/",
        "src/settings/dpi/",
        "src/orchestra/ui/",
        "src/log/ui/",
        "src/telegram_proxy/ui/",
    )
    scopes = [
        path for path in files
        if _under(path, *ui_roots)
    ]
    return _scan_lines(
        scopes,
        re.compile(
            r"(?:from\s+PyQt6\.QtWidgets\s+import[^\n]*\bQTextEdit\b|"
            r"\bQTextEdit\s*\(|"
            r"\bqtextedit_cls\s*=\s*QTextEdit\b)"
        ),
        "production UI должен использовать проектную fluent-обёртку TextEdit, например ScrollBlockingTextEdit, а не обычный QTextEdit",
    )


def check_pages_have_explicit_dependencies(files: list[Path]) -> list[Problem]:
    page_roots = (
        "src/profile/ui/",
        "src/presets/ui/",
        "src/orchestra/ui/",
        "src/blockcheck/ui/",
        "src/dns/ui/",
        "src/hosts/ui/",
        "src/fakes/ui/",
        "src/donater/ui/",
        "src/log/ui/",
        "src/telegram_proxy/ui/",
        "src/lists/ui/",
        "src/ui/pages/",
    )
    scopes = [path for path in files if _under(path, *page_roots)]
    return _scan_lines(
        scopes,
        re.compile(
            r"(?:window\.app_runtime|window\.app_context|window\.ui_state_store|"
            r"self\.window\(\)\.|require_page_app_context|_require_app_context|page_dependencies)"
        ),
        "страница не должна искать зависимости через окно/parent",
    )


def check_external_imports(files: list[Path]) -> list[Problem]:
    external_roots = (
        "src/main/",
        "src/ui/",
        "src/presets/ui/",
        "src/profile/ui/",
        "src/donater/ui/",
        "src/dns/ui/",
        "src/hosts/ui/",
        "src/fakes/ui/",
        "src/blockcheck/ui/",
        "src/lists/ui/",
        "src/orchestra/ui/",
        "src/telegram_proxy/ui/",
        "src/updater/ui/",
        "src/log/ui/",
    )
    feature_names = (
        "autostart|blockcheck|diagnostics|dns|donater|hosts|lists|"
        "log|orchestra|presets|profile|settings\\.dpi|telegram_proxy|"
        "updater|winws_runtime"
    )
    internals = "commands|public|service|manager|worker|runtime"
    pattern = re.compile(
        rf"\b(?:from (?:{feature_names})\.(?:{internals})\b|"
        rf"import (?:{feature_names})\.(?:{internals})\b)"
    )
    scopes = [
        path for path in files
        if _under(path, *external_roots) or path.relative_to(REPO_ROOT).as_posix() == "src/tray.py"
    ]
    return _scan_lines(
        scopes,
        pattern,
        "внешний слой не должен импортировать внутренний feature API",
    )


def check_no_feature_internals_from_external(files: list[Path]) -> list[Problem]:
    external_roots = (
        "src/main/",
        "src/ui/",
        "src/presets/ui/",
        "src/profile/ui/",
        "src/donater/ui/",
        "src/dns/ui/",
        "src/hosts/ui/",
        "src/fakes/ui/",
        "src/blockcheck/ui/",
        "src/lists/ui/",
        "src/orchestra/ui/",
        "src/telegram_proxy/ui/",
        "src/updater/ui/",
        "src/log/ui/",
    )
    scopes = [
        path for path in files
        if _under(path, *external_roots) or path.relative_to(REPO_ROOT).as_posix() == "src/tray.py"
    ]
    return _scan_lines(
        scopes,
        re.compile(
            r"(?:app_runtime\.features\.[a-z_]+\.(?:objects|commands|_.*)|"
            r"features\.[a-z_]+\.(?:manager|worker|service|store|objects|commands|_.*)|"
            r"getattr\([^\n]*(?:app_runtime|features|runtime_feature|presets_feature|"
            r"profile_feature|premium_feature|dns_feature|hosts_feature|logs_feature|telegram_proxy_feature))"
        ),
        "внешний слой не должен лезть во внутренности feature",
    )


def check_startup_coordinator_boundary() -> list[Problem]:
    path = SRC_ROOT / "main" / "startup_coordinator.py"
    if not path.exists():
        return [Problem(path, 1, "startup_coordinator.py не найден")]
    return _scan_lines(
        [path],
        re.compile(r"\bself\.(?:app|window|app_runtime)\b"),
        "StartupCoordinator не должен держать всё окно или весь AppRuntime",
    )


def check_runtime_state_writers(files: list[Path]) -> list[Problem]:
    allowed = {
        "src/app/state_store.py",
        "src/winws_runtime/state/launch_runtime_service.py",
    }
    pattern = re.compile(
        r"\b(?:update|replace|setattr)\s*\([^)]*(?:launch_phase|launch_running|"
        r"launch_running_since|launch_busy|launch_busy_text|launch_last_error)"
    )
    return _scan_lines(
        files,
        pattern,
        "runtime-state DPI должен записываться только через state_store/LaunchRuntimeService",
        allowed_paths=allowed,
    )


def check_ui_state_store_writer_ownership(files: list[Path]) -> list[Problem]:
    rules: tuple[tuple[re.Pattern[str], str, set[str]], ...] = (
        (
            re.compile(r"\.set_launch_busy\s*\("),
            "launch busy state должен писать только LaunchRuntimeService",
            {"src/winws_runtime/state/launch_runtime_service.py"},
        ),
        (
            re.compile(r"\.set_subscription\s*\("),
            "Premium state должен писать только Premium/Subscription слой",
            {"src/donater/subscription_ui.py"},
        ),
        (
            re.compile(r"\bcheck_device_activation\s*\("),
            "Premium-статус проверяет только donater/status_runtime.py: остальные просят обновление через request_status_refresh",
            {"src/donater/commands.py", "src/donater/service.py"},
        ),
        (
            re.compile(r"\.set_current_strategy_summary\s*\("),
            "current strategy summary должен писать только presets/display_state.py",
            {"src/presets/display_state.py"},
        ),
        (
            re.compile(r"\.set_window_opacity_value\s*\("),
            "window opacity state должен писать только window_state_actions.py",
            {"src/main/window_state_actions.py"},
        ),
        (
            re.compile(r"\.bump_active_preset_revision\b"),
            "active preset revision должен писать только preset runtime coordinator",
            {"src/core/runtime/preset_runtime_coordinator.py"},
        ),
        (
            re.compile(r"\.bump_preset_content_revision\b"),
            "preset content revision должен писать только preset runtime/runtime UI bridge setup",
            {"src/core/runtime/preset_runtime_coordinator.py", "src/ui/window_bootstrap_runtime.py"},
        ),
        (
            re.compile(r"\.bump_preset_structure_revision\b"),
            "preset structure revision должен писать только presets UI subpage layer",
            {"src/presets/ui/common/preset_subpage_base.py"},
        ),
        (
            re.compile(r"\.bump_mode_revision\b"),
            "mode revision должен писать только runtime method switch flow",
            {"src/winws_runtime/runtime/method_switch_flow.py"},
        ),
        (
            re.compile(r"\bset_selected_source_preset_file_name\s*\("),
            "выбранный пресет пишет только PresetSelectionService: он же единственный источник событий о смене выбора",
            {"src/presets/selection_service.py", "src/settings/store.py"},
        ),
        (
            re.compile(r"\b_selected_manifest_cache\b"),
            "копия выбранного пресета запрещена: источник — PresetSelectionService поверх настроек",
            set(),
        ),
    )

    problems: list[Problem] = []
    for pattern, message, allowed_paths in rules:
        problems.extend(
            _scan_lines(
                files,
                pattern,
                message,
                allowed_paths=allowed_paths,
            )
        )
    return problems


def check_blockcheck_runtime_boundary(files: list[Path]) -> list[Problem]:
    scopes = [path for path in files if _under(path, "src/blockcheck/")]
    return _scan_lines(
        scopes,
        re.compile(r"\b(?:from|import)\s+winws_runtime\.runners\b|winws_runtime\.runners\."),
        "blockcheck должен брать runtime-константы через winws_runtime.public, а не через runners",
    )


def check_premium_public_boundary() -> list[Problem]:
    path = SRC_ROOT / "donater" / "public.py"
    if not path.exists():
        return [Problem(path, 1, "donater/public.py не найден")]
    return _scan_lines(
        [path],
        re.compile(r"\b(?:PremiumCheckerBundle|get_premium_checker|resolve_checker_bundle|PremiumStatusRuntime|storage)\b"),
        "donater.public не должен экспортировать внутренний Premium checker/storage/runtime",
    )


def check_page_deps_builder_coverage() -> list[Problem]:
    schema_path = SRC_ROOT / "ui" / "navigation" / "schema.py"
    composition_path = SRC_ROOT / "ui" / "page_composition.py"
    if not schema_path.exists():
        return [Problem(schema_path, 1, "navigation schema не найден")]
    if not composition_path.exists():
        return [Problem(composition_path, 1, "page_composition.py не найден")]

    route_pages = _page_name_dict_keys(schema_path, "PAGE_ROUTE_SPECS")
    deps_pages = _page_name_dict_keys(composition_path, "PAGE_DEPS_BUILDERS")
    missing = sorted(route_pages - deps_pages)
    if not missing:
        return []
    return [
        Problem(
            composition_path,
            1,
            "для каждой route-страницы нужен явный builder зависимостей",
            ", ".join(missing),
        )
    ]


def check_page_composition_is_registry_only() -> list[Problem]:
    path = SRC_ROOT / "ui" / "page_composition.py"
    if not path.exists():
        return [Problem(path, 1, "page_composition.py не найден")]
    return _scan_lines(
        [path],
        re.compile(r"\b(?:def\s+build_.*page_kwargs|window\.app_runtime|runtime_parts)\b"),
        "page_composition.py должен быть общей картой; builder-ы живут в ui/page_deps",
    )


def check_translated_pages_require_deps() -> list[Problem]:
    targets = (
        (
            SRC_ROOT / "dns" / "ui" / "page.py",
            re.compile(r"def __init__\(self, parent=None, \*, dns_feature\)"),
            "NetworkPage должен принимать deps, а не dns_feature",
        ),
        (
            SRC_ROOT / "hosts" / "ui" / "page.py",
            re.compile(r"def __init__\(self, parent=None, \*, hosts_feature\)"),
            "HostsPage должен принимать deps, а не hosts_feature",
        ),
        (
            SRC_ROOT / "donater" / "ui" / "page.py",
            re.compile(r"def __init__\(self, parent=None, \*, (?:premium_feature|subscription_state_store)"),
            "PremiumPage должен принимать deps, а не premium_feature/subscription_state_store",
        ),
    )
    problems: list[Problem] = []
    for path, pattern, message in targets:
        if not path.exists():
            problems.append(Problem(path, 1, "переведённая страница не найдена"))
            continue
        problems.extend(_scan_lines([path], pattern, message))
    return problems


def check_translated_pages_have_no_command_signals() -> list[Problem]:
    page_paths = (
        SRC_ROOT / "dns" / "ui" / "page.py",
        SRC_ROOT / "hosts" / "ui" / "page.py",
        SRC_ROOT / "fakes" / "ui" / "page.py",
        SRC_ROOT / "donater" / "ui" / "page.py",
    )
    return _scan_lines(
        [path for path in page_paths if path.exists()],
        re.compile(
            r"\b(?:start|stop|apply|refresh|open|switch|reset|create|check|activate|"
            r"start_dpi|stop_dpi|apply_dns|apply_hosts|refresh_subscription|"
            r"open_profile|open_preset)[a-z_]*\s*=\s*pyqtSignal\b"
        ),
        "переведённая страница не должна объявлять command-сигнал вместо deps",
    )


def check_pages_have_no_command_request_signals(files: list[Path]) -> list[Problem]:
    page_roots = (
        "src/profile/ui/",
        "src/presets/ui/",
        "src/orchestra/ui/",
        "src/blockcheck/ui/",
        "src/dns/ui/",
        "src/hosts/ui/",
        "src/fakes/ui/",
        "src/donater/ui/",
        "src/log/ui/",
        "src/telegram_proxy/ui/",
        "src/lists/ui/",
        "src/ui/pages/",
        "src/updater/ui/",
    )
    scopes = [path for path in files if _under(path, *page_roots)]
    return _scan_lines(
        scopes,
        re.compile(
            r"\b(?:start|stop|apply|refresh|check|activate|switch|reset|create|"
            r"delete|remove|save|clear|flush|install|download|update|restart|run)"
            r"[a-z_]*_requested\s*=\s*pyqtSignal\b"
        ),
        "команда страницы должна идти через feature/deps/callback, а не через request-сигнал",
    )


def check_pages_have_no_navigation_request_signals(files: list[Path]) -> list[Problem]:
    page_roots = (
        "src/profile/ui/",
        "src/presets/ui/",
        "src/orchestra/ui/",
        "src/blockcheck/ui/",
        "src/dns/ui/",
        "src/hosts/ui/",
        "src/fakes/ui/",
        "src/donater/ui/",
        "src/log/ui/",
        "src/telegram_proxy/ui/",
        "src/lists/ui/",
        "src/ui/pages/",
        "src/updater/ui/",
    )
    scopes = [path for path in files if _under(path, *page_roots)]
    return _scan_lines(
        scopes,
        re.compile(
            r"\b(?:open_[a-z_]+_requested|navigate_[a-z_]+_requested|"
            r"back_clicked|profile_setup_[a-z_]*requested|"
            r"preset_raw_editor_[a-z_]*requested|user_presets_[a-z_]*requested)"
            r"\s*=\s*pyqtSignal\b"
        ),
        "навигация страницы должна идти через callback/deps из page_composition, а не через request-сигнал",
    )


def check_nested_preset_pages_use_breadcrumbs() -> list[Problem]:
    scopes = [
        SRC_ROOT / "presets" / "ui" / "zapret1" / "user_presets_page.py",
        SRC_ROOT / "presets" / "ui" / "zapret2" / "user_presets_page.py",
        SRC_ROOT / "presets" / "ui" / "common" / "preset_subpage_base.py",
    ]
    forbidden_back_var = "back_" + "btn"
    forbidden_back_camel = "back" + "Button"
    forbidden_back_text = "Назад" + " к списку"
    forbidden_chevron = "chevron" + "-left"
    return _scan_lines(
        [path for path in scopes if path.exists()],
        re.compile(
            r"\b(?:"
            + re.escape(forbidden_back_var)
            + r"|"
            + re.escape(forbidden_back_camel)
            + r"|"
            + re.escape(forbidden_back_text)
            + r"|"
            + re.escape(forbidden_chevron)
            + r")\b"
        ),
        "вложенные preset-страницы должны использовать BreadcrumbBar, а не одиночную кнопку назад",
    )


def check_settings_sqlite_is_canonical_app_storage(files: list[Path]) -> list[Problem]:
    problems: list[Problem] = []
    source_scopes = [
        path
        for path in files
        if _under(path, "src/") and path != SRC_ROOT / "app" / "architecture_checks.py"
    ]
    problems.extend(
        _scan_lines(
            source_scopes,
            re.compile(r"settings\.json"),
            "settings.json выведен из эксплуатации; runtime должен использовать только settings.sqlite3",
        )
    )
    problems.extend(
        _scan_lines(
            files,
            re.compile(
                r"\b(?:"
                r"premium\.ini|user_hosts\.ini|post_activate|activation_key|RegistryWindowGeometryStore|"
                r"\.update_cache\.json|\.update_rate_limit\.json|\.server_pool_stats\.json|"
                r"\.selected_server\.json|"
                r"\.vps_block\.json|\.server_stats\.json|strategy_scan_resume\.json|"
                r"blockcheck_user_domains\.txt"
                r")\b"
            ),
            "обычные настройки должны жить в settings.sqlite3; отдельные state-файлы запрещены",
        )
    )

    app_storage_scopes = [
        path
        for path in files
        if _under(path, "src/donater/", "src/hosts/", "src/settings/")
    ]
    problems.extend(
        _scan_lines(
            app_storage_scopes,
            re.compile(r"\b(?:ConfigParser|configparser)\b"),
            "для рабочих настроек приложения нельзя возвращать ini-парсер; используйте settings.sqlite3",
        )
    )
    return problems


def check_page_deps_context_has_explicit_fields(files: list[Path]) -> list[Problem]:
    scopes = [
        path for path in files
        if _under(path, "src/ui/page_deps/", "src/main/window_page_deps_setup.py")
    ]
    return _scan_lines(
        scopes,
        re.compile(
            r"\b(?:PageDepsContext|def\s+build_[a-z_]+_page_kwargs\s*\(\s*context\b|"
            r"context\.|features\s*:\s*object|state\s*:\s*object)\b"
        ),
        "page deps builder не должен получать общий context; зависимости страницы задаются через PageDepsSpec",
    )


def check_preset_switch_has_no_full_start_fallback() -> list[Problem]:
    path = SRC_ROOT / "winws_runtime" / "runtime" / "control_workers.py"
    if not path.exists():
        return [Problem(path, 1, "control_workers.py не найден")]
    return _scan_lines(
        [path],
        re.compile(r"\b(?:getattr\([^)]*switch_preset_file_fast|runner\.start_from_preset_file\s*\()"),
        "PresetSwitchWorker должен использовать только switch_preset_file_fast без fallback на полный запуск",
    )


def check_fast_switch_runners_do_not_call_full_start_pipeline() -> list[Problem]:
    targets = (
        (SRC_ROOT / "winws_runtime" / "runners" / "zapret1_runner.py", "Winws1StrategyRunner"),
        (SRC_ROOT / "winws_runtime" / "runners" / "zapret2_runner.py", "Winws2StrategyRunner"),
    )
    problems: list[Problem] = []
    for path, class_name in targets:
        if not path.exists():
            problems.append(Problem(path, 1, f"{path.name} не найден"))
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        runner = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef) and node.name == class_name
            ),
            None,
        )
        if runner is None:
            problems.append(Problem(path, 1, f"{class_name} не найден"))
            continue
        switch_method = next(
            (
                node
                for node in runner.body
                if isinstance(node, ast.FunctionDef) and node.name == "switch_preset_file_fast"
            ),
            None,
        )
        if switch_method is None:
            problems.append(Problem(path, getattr(runner, "lineno", 1), "switch_preset_file_fast не найден"))
            continue
        for node in ast.walk(switch_method):
            if isinstance(node, ast.Attribute) and node.attr == "_start_from_preset_file_locked":
                problems.append(
                    Problem(
                        path,
                        getattr(node, "lineno", getattr(switch_method, "lineno", 1)),
                        "switch_preset_file_fast не должен откатываться в полный start pipeline",
                        "_start_from_preset_file_locked",
                    )
                )
    return problems


def check_no_running_preset_pid_probe(files: list[Path]) -> list[Problem]:
    scopes = [
        path for path in files
        if _under(path, "src/winws_runtime/")
    ]
    return _scan_lines(
        scopes,
        re.compile(r"\bfind_running_preset_pid\b"),
        "проверка запущенного preset через @preset больше не соответствует текущему запуску args и не должна использоваться",
    )


def check_ui_workflows_do_not_call_page_methods(files: list[Path]) -> list[Problem]:
    scopes = [
        path for path in files
        if _under(path, "src/ui/workflows/")
    ]
    return _scan_lines(
        scopes,
        re.compile(r"\.(?:show_profile|set_preset_file_name|apply_profile_setup_change)\s*\("),
        "UI workflow не должен вызывать методы конкретной страницы; используйте явное WindowPageActions/page presenter действие",
    )


def check_no_runtime_launch_preset_files(files: list[Path]) -> list[Problem]:
    return _scan_lines(
        files,
        re.compile(r"\b(?:runtime[/\\]launch_presets|launch_presets|\.launch\.txt|_launch_preset_artifact)\b"),
        "source preset должен оставаться единственным состоянием выбранного preset; не создавайте runtime/*.launch.txt копии. Узкий winws2 @config-адаптер допустим только как временный файл запуска, если его путь не попадает в UI/настройки",
    )


# --- Пресет — точка истины (presets.preset_contract) ------------------------
#
# Проверки ниже работают по AST и принимают пары (путь, исходник), чтобы
# тесты могли подать им синтетическое нарушение и убедиться, что оно ловится.

SourceFile = tuple[Path, str]

_WINWS_EXE_EXPR_RE = re.compile(r"winws\w*_exe\b")
_PRESET_NAMED_TARGET_RE = re.compile(r"preset", re.IGNORECASE)
_RAW_WRITE_ATTRS = frozenset({"write_text", "write_bytes"})
_RAW_COPY_FUNCS = frozenset({"replace", "rename", "copy", "copy2", "copyfile", "move"})


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _contract_scope_sources(files: list[Path]) -> list[SourceFile]:
    from presets.preset_contract import PRESET_CONTRACT_SCOPE

    return [
        (path, path.read_text(encoding="utf-8", errors="replace"))
        for path in files
        if _under(path, *PRESET_CONTRACT_SCOPE)
    ]


def _parse(path: Path, source: str) -> ast.Module | None:
    try:
        return ast.parse(source, filename=str(path))
    except SyntaxError:
        return None


def _functions_with_nodes(tree: ast.AST):
    """(имя ближайшей функции, узел) для каждого узла дерева."""

    def _walk(node: ast.AST, function_name: str):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function_name
            yield name, child
            yield from _walk(child, name)

    yield from _walk(tree, "")


def _is_at_config_fstring(node: ast.AST) -> bool:
    if not isinstance(node, ast.JoinedStr) or not node.values:
        return False
    first = node.values[0]
    return isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.startswith("@")


def check_winws_launch_command_is_exe_plus_at_config(sources: list[SourceFile]) -> list[Problem]:
    """Команда запуска winws — ровно [exe, @config], и @config — из артефакта пресета.

    Допустимо: ``[exe, *artifact.launch_args]``; ``[exe, f"@..."]`` — только в
    функции процесса проверки (``DRY_RUN_FUNCTION_MARKER`` в имени) или в модуле
    из ``GENERATED_CONFIG_EXEMPTIONS``. ``PreparedPresetArtifact.launch_args`` —
    пусто или ровно один ``f"@..."``: всё, что запускается, лежит в @config.
    """
    from presets.preset_contract import DRY_RUN_FUNCTION_MARKER, GENERATED_CONFIG_EXEMPTIONS

    problems: list[Problem] = []
    for path, source in sources:
        tree = _parse(path, source)
        if tree is None:
            continue
        rel = _rel(path)
        generated_config_module = rel in GENERATED_CONFIG_EXEMPTIONS
        for function_name, node in _functions_with_nodes(tree):
            if isinstance(node, ast.List) and node.elts and _WINWS_EXE_EXPR_RE.search(ast.unparse(node.elts[0])):
                second = node.elts[1] if len(node.elts) == 2 else None
                from_artifact = (
                    isinstance(second, ast.Starred)
                    and isinstance(second.value, ast.Attribute)
                    and second.value.attr == "launch_args"
                )
                generated_at_config = _is_at_config_fstring(second) and (
                    generated_config_module or DRY_RUN_FUNCTION_MARKER in function_name
                )
                if not (from_artifact or generated_at_config):
                    problems.append(
                        Problem(
                            path,
                            node.lineno,
                            "команда winws должна быть ровно [exe, *artifact.launch_args] — всё, что запускается, "
                            "видно в пресете (presets.preset_contract)",
                            ast.unparse(node),
                        )
                    )
            if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("PreparedPresetArtifact"):
                launch_args = next((kw.value for kw in node.keywords if kw.arg == "launch_args"), None)
                if launch_args is None and len(node.args) > 3:
                    launch_args = node.args[3]
                if launch_args is None:
                    continue
                empty = (
                    isinstance(launch_args, ast.Tuple) and not launch_args.elts
                ) or (
                    isinstance(launch_args, ast.Call)
                    and ast.unparse(launch_args.func) == "tuple"
                    and not launch_args.args
                )
                single_at_config = (
                    isinstance(launch_args, ast.Tuple)
                    and len(launch_args.elts) == 1
                    and _is_at_config_fstring(launch_args.elts[0])
                )
                if not (empty or single_at_config):
                    problems.append(
                        Problem(
                            path,
                            node.lineno,
                            "launch_args артефакта — пусто или ровно один @config, собранный из текста пресета",
                            ast.unparse(launch_args),
                        )
                    )
    return problems


def check_preset_files_written_only_by_owners(sources: list[SourceFile]) -> list[Problem]:
    """Файл пресета пишут только модули-владельцы, и только нормализованный текст.

    - ``create_preset`` / ``update_preset`` зовут только ``PRESET_FILE_WRITE_OWNERS``,
      ``_write_source`` — только само хранилище;
    - владелец (кроме хранилища) перед записью в той же функции пропускает
      текст через нормализацию сохранения (``PRESET_SAVE_NORMALIZER_NAMES``);
    - прямая запись на диск (write_text/write_bytes/open на запись/копирование)
      в файл, названный как пресет, — только у хранилища и у
      ``GENERATED_CONFIG_EXEMPTIONS``.
    """
    from presets.preset_contract import (
        GENERATED_CONFIG_EXEMPTIONS,
        PRESET_FILE_STORE_MODULE,
        PRESET_FILE_WRITE_METHODS,
        PRESET_FILE_WRITE_OWNERS,
        PRESET_SAVE_NORMALIZER_NAMES,
    )

    problems: list[Problem] = []
    for path, source in sources:
        tree = _parse(path, source)
        if tree is None:
            continue
        rel = _rel(path)
        is_store = rel == PRESET_FILE_STORE_MODULE
        is_owner = rel in PRESET_FILE_WRITE_OWNERS
        functions = [
            node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for function in functions:
            normalizer_lines = [
                node.lineno
                for node in ast.walk(function)
                if isinstance(node, ast.Call)
                and (
                    (isinstance(node.func, ast.Attribute) and node.func.attr in PRESET_SAVE_NORMALIZER_NAMES)
                    or (isinstance(node.func, ast.Name) and node.func.id in PRESET_SAVE_NORMALIZER_NAMES)
                )
            ]
            for node in ast.walk(function):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in PRESET_FILE_WRITE_METHODS
                ):
                    continue
                if node.func.attr == "_write_source" and not is_store:
                    problems.append(
                        Problem(path, node.lineno, "_write_source вызывает только хранилище пресетов", ast.unparse(node.func))
                    )
                elif is_store and node.func.attr != "_write_source":
                    # Хранилище — примитив записи: текст для create/update приходит
                    # только от владельцев, которые его уже нормализовали.
                    problems.append(
                        Problem(
                            path,
                            node.lineno,
                            "хранилище не создаёт и не обновляет пресеты само — только по вызову владельца",
                            ast.unparse(node.func),
                        )
                    )
                elif not is_owner:
                    problems.append(
                        Problem(
                            path,
                            node.lineno,
                            "файл пресета пишут только PRESET_FILE_WRITE_OWNERS (presets.preset_contract)",
                            ast.unparse(node.func),
                        )
                    )
                elif not is_store and not any(line <= node.lineno for line in normalizer_lines):
                    problems.append(
                        Problem(
                            path,
                            node.lineno,
                            "перед записью файла пресета текст должен пройти normalize_preset_source_for_save",
                            ast.unparse(node.func),
                        )
                    )
        if is_store or rel in GENERATED_CONFIG_EXEMPTIONS:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            target: ast.AST | None = None
            if isinstance(func, ast.Attribute) and func.attr in _RAW_WRITE_ATTRS:
                target = func.value
            elif isinstance(func, ast.Name) and func.id == "open" and node.args:
                mode = node.args[1] if len(node.args) > 1 else next(
                    (kw.value for kw in node.keywords if kw.arg == "mode"), None
                )
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and set(mode.value) & set("wax+"):
                    target = node.args[0]
            elif (
                isinstance(func, ast.Attribute)
                and func.attr in _RAW_COPY_FUNCS
                and isinstance(func.value, ast.Name)
                and func.value.id in {"os", "shutil"}
                and len(node.args) > 1
            ):
                target = node.args[1]
            if target is not None and _PRESET_NAMED_TARGET_RE.search(ast.unparse(target)):
                problems.append(
                    Problem(
                        path,
                        node.lineno,
                        "файл пресета пишется только через PresetFileService/PresetFileStore; "
                        "подготовка запуска готовит текст в памяти и не меняет пресет",
                        ast.unparse(node),
                    )
                )
    return problems


def check_launch_preparation_returns_source_text(sources: list[SourceFile]) -> list[Problem]:
    """Подготовка winws2 к запуску только проверяет текст и возвращает его как есть.

    В ``winws_runtime/preset_launch_text.py`` каждое ``PreparedLaunchPresetText(text=...)``
    получает имя, которое в функции присвоено ровно один раз из
    ``str(<параметр> or "")`` (или сам параметр): никакой скрытой нормализации.
    """
    problems: list[Problem] = []
    for path, source in sources:
        if _rel(path) != "src/winws_runtime/preset_launch_text.py":
            continue
        tree = _parse(path, source)
        if tree is None:
            continue
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = {arg.arg for arg in (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs)}
            assignments: dict[str, list[ast.AST]] = {}
            for node in ast.walk(function):
                targets: list[ast.AST] = []
                if isinstance(node, ast.Assign):
                    targets = list(node.targets)
                elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                    targets = [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        assignments.setdefault(target.id, []).append(node)
            for node in ast.walk(function):
                if not (isinstance(node, ast.Call) and ast.unparse(node.func) == "PreparedLaunchPresetText"):
                    continue
                text_arg = next((kw.value for kw in node.keywords if kw.arg == "text"), None)
                if text_arg is None and node.args:
                    text_arg = node.args[0]
                ok = False
                if isinstance(text_arg, ast.Name):
                    bound = assignments.get(text_arg.id, [])
                    if not bound and text_arg.id in params:
                        ok = True
                    elif len(bound) == 1 and isinstance(bound[0], ast.Assign):
                        value = ast.unparse(bound[0].value)
                        ok = any(value in {f'str({param} or "")', f"str({param} or '')"} for param in params)
                if not ok:
                    problems.append(
                        Problem(
                            path,
                            node.lineno,
                            "подготовка запуска не должна менять текст пресета: text= только исходный текст",
                            ast.unparse(node),
                        )
                    )
    return problems


def check_preset_source_of_truth_contract(files: list[Path]) -> list[Problem]:
    sources = _contract_scope_sources(files)
    problems: list[Problem] = []
    problems.extend(check_winws_launch_command_is_exe_plus_at_config(sources))
    problems.extend(check_preset_files_written_only_by_owners(sources))
    problems.extend(check_launch_preparation_returns_source_text(sources))
    return problems


def check_preset_source_changes_have_single_runtime_owner(files: list[Path]) -> list[Problem]:
    """UI/profile layers may save source presets, but runtime apply belongs to coordinator."""

    scopes = [
        path
        for path in files
        if _under(path, "src/presets/", "src/profile/", "src/blockcheck/")
    ]
    problems = _scan_lines(
        scopes,
        re.compile(
            r"\b(?:apply_preset_content|request_preset_runtime_content_apply|switch_presets_async)\s*\("
        ),
        "изменение source preset не должно применять runtime напрямую; путь должен быть save -> preset_content_changed -> PresetRuntimeCoordinator -> LaunchRuntime",
    )
    user_presets_boundary = [
        path
        for path in files
        if path.relative_to(REPO_ROOT).as_posix() == "src/presets/ui/common/user_presets_page.py"
    ]
    problems.extend(
        _scan_lines(
            user_presets_boundary,
            re.compile(r"\bruntime_feature\b"),
            "страница «Мои пресеты» не должна получать runtime_feature; она меняет только source preset и UI-список",
        )
    )
    deps_path = SRC_ROOT / "ui" / "page_deps" / "presets.py"
    if deps_path.exists():
        tree = ast.parse(deps_path.read_text(encoding="utf-8", errors="replace"))
        builder = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "build_user_presets_page_kwargs"
            ),
            None,
        )
        if builder is None:
            problems.append(Problem(deps_path, 1, "build_user_presets_page_kwargs не найден"))
        else:
            arg_names = [arg.arg for arg in builder.args.args]
            arg_names.extend(arg.arg for arg in builder.args.kwonlyargs)
            if "runtime_feature" in arg_names:
                problems.append(
                    Problem(
                        deps_path,
                        getattr(builder, "lineno", 1),
                        "build_user_presets_page_kwargs не должен получать runtime_feature",
                        "runtime_feature",
                    )
                )
            for node in ast.walk(builder):
                if isinstance(node, ast.Dict):
                    for key in node.keys:
                        if isinstance(key, ast.Constant) and key.value == "runtime_feature":
                            problems.append(
                                Problem(
                                    deps_path,
                                    getattr(key, "lineno", getattr(builder, "lineno", 1)),
                                    "kwargs страницы «Мои пресеты» не должны передавать runtime_feature",
                                    '"runtime_feature"',
                                )
                            )
    return problems


def run_checks() -> list[Problem]:
    files = _python_files()
    problems: list[Problem] = []
    problems.extend(check_removed_legacy_files())
    problems.extend(check_no_app_context(files))
    problems.extend(check_no_app_runtime_context(files))
    problems.extend(check_app_features_is_registry_only())
    problems.extend(check_no_page_signal_layer(files))
    problems.extend(check_no_window_level_state_subscriptions(files))
    problems.extend(check_switches_use_aligned_switch_button(files))
    problems.extend(check_skip_float_in_is_allowlisted(files))
    problems.extend(check_runtime_feedback_uses_ui_bridge(files))
    problems.extend(check_runtime_ui_bridge_is_feature_neutral())
    problems.extend(check_preset_display_state_not_in_window_layer(files))
    problems.extend(check_window_state_sync_is_window_only())
    problems.extend(check_page_navigation_uses_page_host(files))
    problems.extend(check_main_window_not_business_container(files))
    problems.extend(check_window_not_store_access_point(files))
    problems.extend(check_window_does_not_build_app_runtime())
    problems.extend(check_window_has_no_bootstrap_wrapper())
    problems.extend(check_app_runtime_access_is_narrow(files))
    problems.extend(check_window_feature_aliases_not_used(files))
    problems.extend(check_window_hidden_dependency_bags_not_used(files))
    problems.extend(check_tray_uses_window_port())
    problems.extend(check_application_icon_has_single_owner(files))
    problems.extend(check_window_runtime_setup_is_thin())
    problems.extend(check_window_feature_deps_use_explicit_port())
    problems.extend(check_application_controller_uses_port_builders())
    problems.extend(check_window_page_actions_is_callback_bag())
    problems.extend(check_application_lifecycle_uses_window_port())
    problems.extend(check_window_page_deps_setup_uses_actions())
    problems.extend(check_post_startup_uses_explicit_host(files))
    problems.extend(check_post_startup_builds_ui_only_in_user_pause(files))
    problems.extend(
        check_background_code_does_not_create_widgets(
            [(path, path.read_text(encoding="utf-8", errors="replace")) for path in files]
        )
    )
    problems.extend(check_discord_tray_command_does_not_receive_window())
    problems.extend(check_post_startup_does_not_use_window_as_feature_container())
    problems.extend(check_page_deps_context_not_stored_on_window(files))
    problems.extend(check_no_qfluentwidgets_fallbacks(files))
    problems.extend(check_no_legacy_toggle_widgets_in_production_ui(files))
    problems.extend(check_no_raw_text_edit_in_production_ui(files))
    problems.extend(check_pages_have_explicit_dependencies(files))
    problems.extend(check_external_imports(files))
    problems.extend(check_no_feature_internals_from_external(files))
    problems.extend(check_startup_coordinator_boundary())
    problems.extend(check_runtime_state_writers(files))
    problems.extend(check_ui_state_store_writer_ownership(files))
    problems.extend(check_blockcheck_runtime_boundary(files))
    problems.extend(check_premium_public_boundary())
    problems.extend(check_page_deps_builder_coverage())
    problems.extend(check_page_composition_is_registry_only())
    problems.extend(check_translated_pages_require_deps())
    problems.extend(check_translated_pages_have_no_command_signals())
    problems.extend(check_pages_have_no_command_request_signals(files))
    problems.extend(check_pages_have_no_navigation_request_signals(files))
    problems.extend(check_nested_preset_pages_use_breadcrumbs())
    problems.extend(check_settings_sqlite_is_canonical_app_storage(files))
    problems.extend(check_page_deps_context_has_explicit_fields(files))
    problems.extend(check_preset_switch_has_no_full_start_fallback())
    problems.extend(check_fast_switch_runners_do_not_call_full_start_pipeline())
    problems.extend(check_no_running_preset_pid_probe(files))
    problems.extend(check_ui_workflows_do_not_call_page_methods(files))
    problems.extend(check_no_runtime_launch_preset_files(files))
    problems.extend(check_preset_source_of_truth_contract(files))
    problems.extend(check_preset_source_changes_have_single_runtime_owner(files))
    return problems


def main() -> int:
    problems = run_checks()
    if problems:
        print("Architecture boundary check failed:")
        for problem in problems:
            print(problem.format())
        return 1
    print("Architecture boundary check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
