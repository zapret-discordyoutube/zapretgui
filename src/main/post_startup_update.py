from __future__ import annotations

from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal

from app_notifications import advisory_notification
from log.log import log
from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import enqueue_subsystem_task, schedule_after


class _UpdateCheckBridge(QObject):
    result_ready = pyqtSignal(object)
    whats_new_ready = pyqtSignal(object)


# «Что нового» ждёт, пока окно программы откроется и успокоится.
_WHATS_NEW_DELAY_MS = 1500
_WHATS_NEW_RETRY_MS = 3000
_WHATS_NEW_MAX_RETRIES = 20


def install_update_check(
    startup_host,
    *,
    updater_feature,
    notify,
    set_status,
) -> None:
    update_bridge = _UpdateCheckBridge(QCoreApplication.instance())
    startup_check_token: int | None = None

    def _on_update_found(version: str, user_skipped: bool) -> None:
        if not is_startup_host_alive(startup_host):
            return
        try:
            try:
                set_status(f"Доступно обновление v{version}")
            except Exception:
                pass
            if user_skipped:
                log(f"Обновление v{version} пропущено пользователем: окно не открывается", "🔁 UPDATE")
                return
            from app.page_names import PageName as StartupPageName

            # Окно обновления открывает сама страница «Серверы» по общему итогу
            # проверки — одно окно на любой путь. Здесь страницу только
            # создаём, не переходя на неё.
            startup_host.ensure_page(StartupPageName.SERVERS)
        except Exception as exc:
            log(f"Ошибка при показе окна обновления: {exc}", "❌ ERROR")

    def _on_no_update(current_version: str) -> None:
        if not is_startup_host_alive(startup_host):
            return
        try:
            try:
                set_status(f"Обновлений нет, установлена версия {current_version}")
            except Exception:
                pass
            notify(
                advisory_notification(
                    level="success",
                    title="Обновлений нет",
                    content=f"Установлена актуальная версия {current_version}",
                    source="startup.update_check",
                    presentation="infobar",
                    queue="immediate",
                    duration=4000,
                    dedupe_key=f"startup.update_check:{current_version}",
                )
            )
        except Exception as exc:
            log(f"Ошибка при показе InfoBar: {exc}", "❌ ERROR")

    def _on_update_check_error(error: str) -> None:
        if not is_startup_host_alive(startup_host):
            return
        try:
            set_status("Не удалось проверить обновления")
        except Exception:
            pass
        log(f"Не удалось проверить обновления при запуске: {error}", "⚠️ UPDATE")

    def _on_update_check_skipped(reason: str) -> None:
        if not is_startup_host_alive(startup_host):
            return
        try:
            set_status(str(reason or "Проверка обновлений сейчас не требуется"))
        except Exception:
            pass

    def _on_update_check_finished(result: object) -> None:
        nonlocal startup_check_token
        payload = dict(result or {}) if isinstance(result, dict) else {
            "has_update": False,
            "version": "",
            "release_notes": "",
            "error": "Некорректный результат проверки обновлений",
        }
        token = startup_check_token
        startup_check_token = None
        if token is None or not updater_feature.finish_update_check(
            payload,
            source="startup",
            token=token,
        ):
            return

        if payload.get("skipped"):
            skip_reason = payload.get("skip_reason") or "Проверка обновлений сейчас не требуется"
            log(
                f"Автопроверка обновлений пропущена: {skip_reason}",
                "🔁 UPDATE",
            )
            _on_update_check_skipped(str(skip_reason))
            return
        if payload.get("error"):
            _on_update_check_error(str(payload.get("error") or ""))
            return
        if payload.get("has_update"):
            _on_update_found(
                str(payload.get("version") or ""),
                bool(payload.get("user_skipped")),
            )
            return
        _on_no_update(str(payload.get("version") or ""))

    update_bridge.result_ready.connect(_on_update_check_finished)

    def _startup_update_worker() -> None:
        try:
            result = updater_feature.run_startup_update_check()
            update_bridge.result_ready.emit(dict(result or {}))
        except Exception as exc:
            log(f"Ошибка воркера проверки обновлений: {exc}", "❌ ERROR")
            update_bridge.result_ready.emit(
                {
                    "has_update": False,
                    "version": "",
                    "release_notes": "",
                    "error": str(exc),
                }
            )

    def _schedule_startup_update_check() -> None:
        nonlocal startup_check_token
        if not is_startup_host_alive(startup_host):
            return
        if not updater_feature.is_auto_update_enabled():
            log("Автопроверка обновлений при запуске отключена", "🔁 UPDATE")
            return
        token = updater_feature.begin_update_check(source="startup")
        if token is None:
            log(
                "Автопроверка при запуске не запущена: проверка уже идёт или выполнена в этой сессии",
                "🔁 UPDATE",
            )
            return
        startup_check_token = int(token)
        try:
            set_status("Проверка обновлений...")
        except Exception:
            pass

        enqueue_subsystem_task("update", "StartupUpdateCheckWorker", _startup_update_worker)

    def _report_interrupted_update() -> None:
        """Рассказывает про обновление, которое не довёл до конца прошлый запуск.

        Проверка не зависит от настройки автообновления: сорвавшаяся установка
        — это факт о состоянии программы, а не предложение обновиться.
        """
        if not is_startup_host_alive(startup_host):
            return
        try:
            from updater.install.interrupted import (
                describe_interrupted_update,
                detect_interrupted_update,
            )

            interrupted = detect_interrupted_update()
            if interrupted is None:
                return

            notify(
                advisory_notification(
                    level="warning",
                    title="Обновление не завершилось",
                    content=describe_interrupted_update(interrupted),
                    source="startup.update_recovery",
                    presentation="infobar",
                    queue="immediate",
                    duration=15000,
                    dedupe_key=(
                        f"startup.update_recovery:{interrupted.expected_version}"
                    ),
                )
            )
        except Exception as exc:
            log(f"Не удалось разобрать состояние прошлого обновления: {exc}", "❌ ERROR")

    def _retire_legacy_update_watchdog() -> None:
        try:
            from updater.install.watchdog import retire_legacy_watchdog

            retire_legacy_watchdog()
        except Exception as exc:
            log(f"Не удалось убрать прежний наблюдатель обновления: {exc}", "WARNING")

    def _whats_new_worker() -> None:
        try:
            from config.build_info import APP_VERSION

            history = tuple(updater_feature.startup_whats_new(APP_VERSION) or ())
        except Exception as exc:
            log(f"«Что нового»: не удалось подготовить окно: {exc}", "WARNING")
            return
        if history:
            update_bridge.whats_new_ready.emit(history)

    def _on_whats_new_ready(history: object, attempt: int = 0) -> None:
        if not is_startup_host_alive(startup_host):
            return
        from config.build_info import APP_VERSION

        try:
            shown = startup_host.show_whats_new(APP_VERSION, tuple(history or ()))
        except Exception as exc:
            log(f"«Что нового»: не удалось показать окно: {exc}", "❌ ERROR")
            return
        if not shown:
            # Окно программы свёрнуто в трей: покажем, когда его откроют.
            if attempt < _WHATS_NEW_MAX_RETRIES:
                schedule_after(_WHATS_NEW_RETRY_MS, lambda: _on_whats_new_ready(history, attempt + 1))
            return
        log(f"Показано «Что нового» для v{APP_VERSION}", "🔁 UPDATE")
        enqueue_subsystem_task(
            "update",
            "WhatsNewMarkSeen",
            lambda: updater_feature.mark_whats_new_seen(APP_VERSION),
        )

    update_bridge.whats_new_ready.connect(lambda history: _on_whats_new_ready(history))

    def _schedule_startup_update_check_deferred() -> None:
        if not is_startup_host_alive(startup_host):
            return
        _report_interrupted_update()
        schedule_after(
            _WHATS_NEW_DELAY_MS,
            lambda: is_startup_host_alive(startup_host)
            and enqueue_subsystem_task("update", "StartupWhatsNew", _whats_new_worker),
        )
        enqueue_subsystem_task(
            "update",
            "LegacyUpdateWatchdogCleanup",
            _retire_legacy_update_watchdog,
        )
        # Короткая пауза, чтобы первые секунды после показа окна достались
        # самому интерфейсу. Сама проверка идёт в фоне и стоит около секунды.
        delay_ms = 2000
        log(f"Автопроверка обновлений отложена на {delay_ms}ms после готовности UI", "DEBUG")
        schedule_after(
            delay_ms,
            lambda: is_startup_host_alive(startup_host) and _schedule_startup_update_check(),
        )

    bind_startup_gate(
        startup_host.startup_post_init_ready,
        _schedule_startup_update_check_deferred,
        is_ready=lambda: bool(startup_host.startup_state.post_init_ready),
    )
