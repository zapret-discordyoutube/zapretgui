from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Callable

from log.log import log
from settings.mode import ENGINE_WINWS2, is_orchestra_launch_method, is_preset_launch_method
from winws_runtime.health.installation_preflight import check_installation_before_launch
from winws_runtime.health.process_health_check import (
    diagnose_startup_error,
    publish_startup_diagnosis,
)
from winws_runtime.runtime.sync_shutdown import shutdown_runtime_sync
from winws_runtime.runtime.system_ops import force_kill_all_winws_processes


STARTUP_AUTOSTART_STABLE_WINDOW_SECONDS = 0.35


def ensure_required_files_fast(*, active_preset_path: str = "") -> bool:
    from lists.file_manager import ensure_required_files_fast as _ensure_required_files_fast

    return bool(_ensure_required_files_fast(active_preset_path=active_preset_path))


@dataclass(frozen=True)
class PresetLaunchResult:
    success: bool
    error_message: str = ""
    selected_mode: object = None
    pid: int | None = None


class PresetLaunchService:
    """Отдельный слой запуска preset-а.

    UI-worker только запускает этот сервис в фоне. Сам сервис решает порядок:
    проверить preset, остановить старый процесс, подготовить файлы и вызвать runner.
    """

    def __init__(
        self,
        *,
        selected_mode,
        launch_method,
        runtime_feature,
        runtime_api,
        startup_autostart: bool = False,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.selected_mode = selected_mode
        self.launch_method = launch_method
        self._runtime_feature = runtime_feature
        self.launch_runtime_api = runtime_api
        self._startup_autostart = bool(startup_autostart)
        self._progress_callback = progress_callback
        self.last_error_message = ""
        self.started_pid: int | None = None

    def _progress(self, message: str) -> None:
        callback = self._progress_callback
        if callable(callback):
            callback(str(message or ""))

    def _result(self, success: bool, error_message: str = "") -> PresetLaunchResult:
        return PresetLaunchResult(
            success=bool(success),
            error_message=str(error_message or "").strip(),
            selected_mode=self.selected_mode,
            pid=self.started_pid,
        )

    def _fail(self, message: str, *, progress_message: str | None = None) -> PresetLaunchResult:
        text = str(message or "").strip() or "Не удалось запустить DPI"
        self.last_error_message = text
        self._progress(progress_message if progress_message is not None else text)
        return self._result(False, text)

    def _get_winws_exe(self) -> str:
        from settings.mode import exe_path_for_launch_method

        return exe_path_for_launch_method(self.launch_method)

    def _configure_runner_runtime_callbacks(self, runner) -> None:
        configurator = getattr(runner, "configure_runtime_callbacks", None)
        if not callable(configurator):
            return

        def _publish_runner_failure(*, launch_method: str, error: str = "") -> None:
            try:
                self._runtime_feature.events.publish_runner_failure(
                    launch_method=launch_method,
                    error=error,
                )
            except Exception:
                return

        def _notify_launch_error(message: str) -> None:
            try:
                self._runtime_feature.events.publish_launch_error(str(message or ""))
            except Exception:
                return

        def _publish_active_preset_content_changed(path: str) -> None:
            normalized_path = str(path or "").strip()
            if not normalized_path:
                return
            try:
                self._runtime_feature.events.publish_active_preset_content_changed(normalized_path)
            except Exception:
                return

        def _publish_unexpected_process_exit() -> None:
            try:
                # Callback вызывается exit-watcher потоком. Чтение post-mortem
                # файла остаётся здесь, а UI получает готовое решение.
                from winws_runtime.health.post_mortem import resolve_unexpected_exit

                resolution = resolve_unexpected_exit()
                self._runtime_feature.events.publish_unexpected_process_exit(resolution)
            except Exception:
                return

        configurator(
            transition_in_progress=self._runtime_feature.objects.transition_pipeline_in_progress,
            runner_failure=_publish_runner_failure,
            launch_error=_notify_launch_error,
            active_preset_content_changed=_publish_active_preset_content_changed,
            unexpected_process_exit=_publish_unexpected_process_exit,
        )

    def _extract_preset_launch_input(self) -> tuple[bool, str]:
        mode_param = self.selected_mode
        if (
            isinstance(mode_param, dict)
            and mode_param.get("is_preset_file")
            and is_preset_launch_method(self.launch_method)
        ):
            return True, str(mode_param.get("preset_path") or "").strip()
        return False, ""

    def _resolve_startup_preset_snapshot_if_needed(self) -> PresetLaunchResult | None:
        if not (
            self._startup_autostart
            and is_preset_launch_method(self.launch_method)
            and (self.selected_mode is None or self.selected_mode == "default")
        ):
            return None

        try:
            snapshot = self._runtime_feature.dependencies.presets_feature.get_launch_snapshot(
                self.launch_method,
                require_filters=False,
            )
            self.selected_mode = snapshot.to_selected_mode()
            return None
        except Exception as e:
            message = f"Не удалось подготовить стартовый preset для {self.launch_method}: {e}"
            log(message, "ERROR")
            return self._fail(message)

    def _validate_preset_before_stop(self, *, is_preset_file: bool, preset_path: str, skip_stop: bool) -> bool:
        if not is_preset_file or not preset_path or skip_stop:
            return True
        try:
            from profile.launch_validation import preset_has_enabled_profiles_for_launch
            from winws_runtime.runners.runner_factory import get_strategy_runner

            try:
                preset_text = Path(preset_path).read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                self.last_error_message = f"Ошибка чтения preset: {e}"
                self._progress(self.last_error_message)
                return False

            if not preset_has_enabled_profiles_for_launch(self.launch_method, preset_text):
                message = "В выбранном preset нет включённых profile для запуска"
                log(message, "❌ ERROR")
                self.last_error_message = message
                self._progress(message)
                return False

            runner = get_strategy_runner(self._get_winws_exe())
            self._configure_runner_runtime_callbacks(runner)
            if hasattr(runner, "validate_preset_file"):
                ok, report = runner.validate_preset_file(preset_path)
                if ok:
                    return True

                lines = (report or "").splitlines()
                if lines:
                    log(lines[0], "❌ ERROR")
                    for extra in lines[1:]:
                        if extra.strip():
                            log(extra, "INFO")
                    short = lines[0].strip()
                else:
                    short = "Некорректный preset файл"

                self.last_error_message = short
                self._progress(short)
                return False
        except Exception as e:
            log(f"Не удалось проверить preset перед остановкой предыдущего процесса: {e}", "DEBUG")
        return True

    def _has_previous_process(self) -> bool:
        try:
            return bool(self.launch_runtime_api.has_residual_processes(silent=True))
        except Exception as e:
            log(f"Не удалось проверить предыдущий процесс перед стартом: {e}", "DEBUG")
            return False

    def _stop_previous_process_if_needed(self, *, skip_stop: bool, process_running: bool | None = None) -> None:
        process_running = self._has_previous_process() if process_running is None else bool(process_running)
        if (not process_running) or skip_stop:
            return

        self._progress("Останавливаем предыдущий процесс...")
        shutdown_result = shutdown_runtime_sync(
            runtime_feature=self._runtime_feature,
            reason=f"preset_launch_service_prelaunch:{self.launch_method}",
            include_cleanup=False,
            cleanup_services=False,
            update_runtime_state=False,
        )
        if not shutdown_result.still_running:
            time.sleep(0.5)
            return

        max_wait = 10
        for attempt in range(max_wait):
            time.sleep(0.5)
            if not self.launch_runtime_api.has_residual_processes(silent=True):
                log(f"✅ Предыдущий процесс остановлен (попытка {attempt + 1})", "DEBUG")
                break
        else:
            log("⚠️ Процесс не остановился за 5 секунд, принудительное завершение...", "WARNING")
            try:
                force_kill_all_winws_processes()
                time.sleep(1)
            except Exception as e:
                log(f"Ошибка kill_winws_force: {e}", "DEBUG")

        time.sleep(0.5)

    def _resolve_presets_payload(self) -> tuple[str, str] | None:
        mode_param = self.selected_mode
        if not (isinstance(mode_param, dict) and mode_param.get("is_preset_file")):
            log(f"Ожидался preset файл для {self.launch_method}: {type(mode_param)}", "❌ ERROR")
            self.last_error_message = "Для режима профилей нужен preset файл"
            self._progress("❌ Для режима профилей нужен preset файл")
            return None

        preset_path = str(mode_param.get("preset_path", "") or "").strip()
        strategy_name = str(mode_param.get("name", "Пресет") or "Пресет")

        log(f"Запуск из preset файла: {preset_path}", "INFO")

        if not preset_path:
            log("Путь к preset файлу не указан", "❌ ERROR")
            self.last_error_message = "Не указан путь к preset файлу"
            self._progress("❌ Ошибка: не указан путь к preset файлу")
            return None

        if not os.path.exists(preset_path):
            log(f"Preset файл не найден: {preset_path}", "❌ ERROR")
            self.last_error_message = f"Preset файл не найден: {preset_path}"
            self._progress("❌ Preset файл не найден")
            return None

        return preset_path, strategy_name

    def _start_presets_with_runner(self, preset_path: str, strategy_name: str) -> bool:
        from winws_runtime.runners.runner_factory import get_strategy_runner

        runner = get_strategy_runner(self._get_winws_exe())
        self._configure_runner_runtime_callbacks(runner)
        start_kwargs = {}
        if self._startup_autostart:
            start_kwargs["_stable_start_window_seconds"] = STARTUP_AUTOSTART_STABLE_WINDOW_SECONDS
        success = runner.start_from_preset_file(preset_path, strategy_name, **start_kwargs)

        if success:
            try:
                snapshot = runner.get_runner_state_snapshot()
                pid = getattr(snapshot, "pid", None)
                self.started_pid = pid if isinstance(pid, int) else None
            except Exception:
                self.started_pid = None
            log(f"Пресет '{strategy_name}' успешно запущен", "✅ SUCCESS")
            return True

        details = str(getattr(runner, "last_error", "") or "").strip()
        short = (details.splitlines()[0].strip() if details else "")
        if short:
            log(f"Не удалось запустить пресет: {short}", "❌ ERROR")
            self.last_error_message = short
            self._progress(f"❌ {short}")
        else:
            log("Не удалось запустить пресет", "❌ ERROR")
            self.last_error_message = "Не удалось запустить пресет (см. логи)"
            self._progress("❌ Не удалось запустить пресет. Проверьте логи")
        return False

    def _start_preset_mode(self) -> bool:
        try:
            payload = self._resolve_presets_payload()
            if payload is None:
                return False
            preset_path, strategy_name = payload
            return self._start_presets_with_runner(preset_path, strategy_name)

        except Exception as e:
            exe_path = self._get_winws_exe()
            summary = publish_startup_diagnosis(diagnose_startup_error(e, exe_path))
            log(summary, "❌ ERROR")
            self.last_error_message = summary
            self._progress(summary)
            return False

    def _start_orchestra(self) -> bool:
        try:
            log("Запуск оркестратора...", "INFO")

            runner = self._runtime_feature.dependencies.orchestra_feature.ensure_runner()

            attempts = 2
            for attempt in range(1, attempts + 1):
                if runner.start():
                    log("Оркестратор успешно запущен", "✅ SUCCESS")
                    return True

                start_reason = str(getattr(runner, "last_start_error", "") or "").strip()
                if attempt < attempts:
                    if start_reason:
                        log(
                            f"Оркестратор не стартовал (попытка {attempt}/{attempts}): {start_reason}. Повторяем...",
                            "WARNING",
                        )
                    else:
                        log(
                            f"Оркестратор не стартовал (попытка {attempt}/{attempts}). Повторяем...",
                            "WARNING",
                        )
                    time.sleep(0.7)
                    continue

                if start_reason:
                    log(f"Не удалось запустить оркестратор: {start_reason}", "❌ ERROR")
                else:
                    log("Не удалось запустить оркестратор", "❌ ERROR")

                try:
                    exit_info = getattr(runner, "last_exit_info", None) or {}
                    config_path = str(exit_info.get("config_path") or "").strip()
                    command = exit_info.get("command") or []
                    recent_output = exit_info.get("recent_output") or []

                    if config_path:
                        log(f"Диагностика старта: конфиг={config_path}", "INFO")
                    if command:
                        cmd_preview = " ".join(str(x) for x in command)
                        if len(cmd_preview) > 500:
                            cmd_preview = cmd_preview[:500] + " ..."
                        log(f"Диагностика старта: команда={cmd_preview}", "INFO")

                    if recent_output:
                        log(f"Диагностика старта: последние строки {ENGINE_WINWS2}:", "INFO")
                        for line in recent_output[-6:]:
                            clean = str(line or "").strip()
                            if clean:
                                if len(clean) > 300:
                                    clean = clean[:300] + " ..."
                                log(f"  {clean}", "INFO")
                except Exception as e:
                    log(f"Не удалось записать диагностику старта оркестратора: {e}", "DEBUG")

                return False

        except Exception as e:
            exe_path = self._get_winws_exe()
            summary = publish_startup_diagnosis(diagnose_startup_error(e, exe_path))
            log(summary, "❌ ERROR")
            import traceback

            log(traceback.format_exc(), "DEBUG")
            self.last_error_message = summary
            return False

    def _run_launch_method(self) -> bool:
        if is_orchestra_launch_method(self.launch_method):
            return self._start_orchestra()
        if is_preset_launch_method(self.launch_method):
            return self._start_preset_mode()
        log(f"Неизвестный метод запуска: {self.launch_method}", "❌ ERROR")
        self.last_error_message = f"Неизвестный метод запуска: {self.launch_method}"
        return False

    def _check_installation(self) -> PresetLaunchResult | None:
        """Отказ запуска, если поставке не хватает своих же файлов.

        Одновременно просит слой приложения починить установку: движок чаще
        всего пропадает не сам по себе, а после обновления или карантина
        антивируса, и пользователю нечего чинить руками.
        """
        preflight = check_installation_before_launch(self.launch_method)
        if preflight.ok:
            return None

        log(
            f"Запуск остановлен проверкой целостности ({preflight.cause}): "
            f"{', '.join(preflight.missing) or 'нет данных'}",
            "WARNING",
        )
        try:
            self._runtime_feature.events.publish_installation_damaged(preflight.report)
        except Exception:
            pass

        log(preflight.message, "❌ ERROR")
        return self._fail(preflight.message)

    def run(self) -> PresetLaunchResult:
        try:
            self._progress("Подготовка к запуску...")

            integrity_failure = self._check_installation()
            if integrity_failure is not None:
                return integrity_failure

            deferred_result = self._resolve_startup_preset_snapshot_if_needed()
            if deferred_result is not None:
                return deferred_result

            is_preset_file, preset_path = self._extract_preset_launch_input()
            skip_stop = False
            previous_process_running = self._has_previous_process()

            if is_preset_file:
                ensure_required_files_fast(active_preset_path=preset_path)

            if previous_process_running:
                if not self._validate_preset_before_stop(
                    is_preset_file=is_preset_file,
                    preset_path=preset_path,
                    skip_stop=skip_stop,
                ):
                    return self._result(False, self.last_error_message)

            self._stop_previous_process_if_needed(
                skip_stop=skip_stop,
                process_running=previous_process_running,
            )

            self._progress("Запуск DPI...")

            success = self._run_launch_method()
            if success:
                return self._result(True, "")

            fatal_reason = ""
            mode_param = self.selected_mode
            if isinstance(mode_param, dict) and is_preset_launch_method(self.launch_method):
                if mode_param.get("is_preset_file"):
                    preset_path = str(mode_param.get("preset_path") or "").strip()
                    if not preset_path:
                        fatal_reason = "❌ Ошибка: не указан путь к preset файлу"
                else:
                    fatal_reason = "❌ Для режима профилей нужен preset файл"

            error_msg = fatal_reason or (self.last_error_message or "Не удалось запустить DPI. Проверьте логи")
            self._progress(error_msg)
            return self._result(False, error_msg)

        except Exception as e:
            exe_path = getattr(self.launch_runtime_api, "expected_exe_path", self._get_winws_exe())
            summary = publish_startup_diagnosis(diagnose_startup_error(e, exe_path))
            log(summary, "❌ ERROR")
            return self._result(False, summary)
