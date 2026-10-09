from __future__ import annotations

import random
import time

from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal

from app_notifications import advisory_notification
from log.log import log
from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import enqueue_subsystem_task, schedule_after


class _UpdateCheckBridge(QObject):
    result_ready = pyqtSignal(object)
    whats_new_ready = pyqtSignal(object)
    # (ожидавшаяся версия, готовый текст уведомления)
    interrupted_update_found = pyqtSignal(str, str)
    # Сервер разрешил обновиться до версии новее установленной.
    release_signalled = pyqtSignal(str)
    # Версия вышла, но очередь на скачивание до программы ещё не дошла.
    release_queued = pyqtSignal(str)


# «Что нового» ждёт, пока окно программы откроется и успокоится.
_WHATS_NEW_DELAY_MS = 1500
# Окно «Что нового» строится в GUI-потоке, поэтому идёт через очередь пауз
# пользователя: она сама ждёт, пока окно программы откроют из трея и человек
# перестанет водить мышью. Повтор нужен только на редкий случай, когда окно
# успели убрать между проверкой очереди и показом.
_WHATS_NEW_RETRY_MS = 3000
_WHATS_NEW_MAX_RETRIES = 400

# О новой версии программе сообщает сервер (updater.release.watch): он же
# ведёт очередь на скачивание и разрешает обновляться. Проверка по расписанию
# — запасной путь на случай, когда сервер с очередью недоступен: пока он
# отвечает, расписание молчит и не нагружает Forgejo лишними запросами.
BACKGROUND_CHECK_INTERVAL_MS = 30 * 60 * 1000
# Случайная добавка к интервалу: установки не приходят на сервер одной волной.
BACKGROUND_CHECK_JITTER_MS = 5 * 60 * 1000
# После неудачной проверки (сети не было) следующая попытка — раньше срока.
BACKGROUND_CHECK_RETRY_MS = 5 * 60 * 1000

# Сигнал сервера застал идущую проверку (она могла начаться до выхода
# версии) либо проверка по сигналу ещё не увидела выпуск: она запускается
# снова через эту паузу.
SIGNAL_CHECK_RETRY_MS = 15 * 1000
SIGNAL_CHECK_MAX_RETRIES = 4
# Сколько действует разрешение сервера обновиться. В этот срок программа
# ставит находку сама и при повторной проверке — например, если скачивание
# с первого раза не удалось.
SIGNAL_GRANT_SECONDS = 60 * 60
# Сколько проверка при запуске ждёт ответа сервера с очередью, прежде чем
# решить, что его нет, и предложить находку окном.
SIGNAL_PROBE_WAIT_SECONDS = 10.0
# Сервер разрешил обновиться, а человек занят (игра на весь экран, проверка
# сети, подбор стратегии): установка закрыла бы программу и оборвала дело.
# Она ждёт простоя, заглядывая с этой паузой.
BUSY_RECHECK_MS = 30 * 1000

_BUSY_TEXTS = {
    "fullscreen": "идёт игра или видео на весь экран",
    "blockcheck": "идёт проверка сети",
    "strategy_scan": "идёт подбор стратегии",
}

_SOURCE_STARTUP = "startup"
_SOURCE_BACKGROUND = "background"


def install_update_check(
    startup_host,
    *,
    updater_feature,
    notify,
    set_status,
    idle_tasks,
    ui_state_store=None,
) -> None:
    update_bridge = _UpdateCheckBridge(QCoreApplication.instance())
    check_token: int | None = None
    check_source = _SOURCE_STARTUP
    check_signalled = False
    signal_granted_until = 0.0
    signal_retries_left = 0
    # Версия, установку которой программа уже начала по разрешению сервера.
    install_started_for = ""
    release_watcher = None
    # Версия, установка которой отложена, пока человек занят.
    deferred_version = ""

    def _on_update_found(version: str, user_skipped: bool, auto_install: bool) -> None:
        nonlocal install_started_for
        if not is_startup_host_alive(startup_host):
            return
        try:
            try:
                set_status(f"Доступно обновление v{version}")
            except Exception:
                pass
            if user_skipped:
                log(f"Обновление v{version} пропущено пользователем: не ставится и не предлагается", "🔁 UPDATE")
                return
            if auto_install:
                install_started_for = version
                notify(
                    advisory_notification(
                        level="info",
                        title=f"Обновление до v{version}",
                        content="Скачиваем новую версию. Программа перезапустится сама.",
                        source="update.auto_install",
                        presentation="infobar",
                        queue="immediate",
                        duration=8000,
                        dedupe_key=f"update.auto_install:{version}",
                    )
                )
            from app.page_names import PageName as StartupPageName

            # Установкой владеет страница «Серверы»: по общему итогу проверки
            # она сама ставит находку либо открывает окно обновления — один
            # путь на любой случай. Здесь страницу только создаём, не переходя
            # на неё. Сборка страницы занимает GUI-поток (~100 мс на быстром
            # компьютере), поэтому ждёт паузы пользователя. При окне в трее
            # ждать нечего — страница строится сразу.
            idle_tasks.add(
                "UpdateWindowPage",
                lambda: _ensure_update_page(StartupPageName.SERVERS),
                needs_shown_window=False,
            )
        except Exception as exc:
            log(f"Ошибка при передаче обновления странице «Серверы»: {exc}", "❌ ERROR")

    def _ensure_update_page(page_name) -> None:
        if not is_startup_host_alive(startup_host):
            return
        try:
            startup_host.ensure_page(page_name)
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

    def _on_background_check_finished(payload: dict, *, signalled: bool) -> None:
        """Фоновая проверка молчит: говорит только находка."""
        nonlocal signal_retries_left
        if payload.get("skipped"):
            return
        if payload.get("error"):
            log(f"Фоновая проверка обновлений не удалась: {payload.get('error')}", "⚠️ UPDATE")
            return
        if signalled and not payload.get("has_update") and signal_retries_left > 0:
            # Сервер уже объявил версию, а список выпусков её ещё не показал.
            signal_retries_left -= 1
            schedule_after(SIGNAL_CHECK_RETRY_MS, lambda: _start_update_check(_SOURCE_BACKGROUND))
            return
        if payload.get("has_update"):
            _on_update_found(
                str(payload.get("version") or ""),
                bool(payload.get("user_skipped")),
                bool(payload.get("auto_install")),
            )

    def _on_update_check_skipped(reason: str) -> None:
        if not is_startup_host_alive(startup_host):
            return
        try:
            set_status(str(reason or "Проверка обновлений сейчас не требуется"))
        except Exception:
            pass

    def _on_update_check_finished(result: object) -> None:
        nonlocal check_token
        payload = dict(result or {}) if isinstance(result, dict) else {
            "has_update": False,
            "version": "",
            "release_notes": "",
            "error": "Некорректный результат проверки обновлений",
        }
        token = check_token
        check_token = None
        source = check_source
        signalled = check_signalled
        if token is None or not updater_feature.finish_update_check(
            payload,
            source=source,
            token=token,
        ):
            return

        if payload.get("error") and not payload.get("skipped") and (signalled or not _server_queue_reachable()):
            # Повтор нужен, когда от него что-то зависит: разрешение сервера
            # ещё не использовано либо сервера с очередью нет вовсе.
            schedule_after(
                BACKGROUND_CHECK_RETRY_MS,
                lambda: _start_update_check(_SOURCE_BACKGROUND),
            )
        if source != _SOURCE_STARTUP:
            _on_background_check_finished(payload, signalled=signalled)
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
                bool(payload.get("auto_install")),
            )
            return
        _on_no_update(str(payload.get("version") or ""))

    update_bridge.result_ready.connect(_on_update_check_finished)

    def _awaits_server_queue(result: dict) -> bool:
        """Находка ждёт очереди на сервере: программа поставит её сама.

        Вызывается в фоновом потоке проверки и ждёт первый ответ сервера с
        очередью. Если сервера нет, находка предлагается окном, как раньше.
        """
        if not result.get("has_update") or result.get("auto_install") or result.get("user_skipped"):
            return False
        watcher = release_watcher
        if watcher is None:
            return False
        try:
            return bool(watcher.wait_until_probed(SIGNAL_PROBE_WAIT_SECONDS))
        except Exception:
            return False

    def _update_check_worker() -> None:
        try:
            result = dict(updater_feature.run_startup_update_check(signalled=check_signalled) or {})
            if _awaits_server_queue(result):
                result["awaiting_signal"] = True
            update_bridge.result_ready.emit(result)
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

    def _start_update_check(source: str) -> bool:
        """Одна автоматическая проверка: при запуске либо фоновая.

        Выключатель автообновления читается перед каждой проверкой: его могли
        переключить, пока программа работала. False — проверка не началась.

        Пока действует разрешение сервера обновиться, любая проверка идёт
        «по сигналу»: её находка ставится без вопроса.
        """
        nonlocal check_token, check_source, check_signalled
        at_startup = source == _SOURCE_STARTUP
        if not is_startup_host_alive(startup_host):
            return False
        if at_startup and install_started_for:
            # Разрешение сервера пришло раньше проверки при запуске, версия
            # уже скачивается: вторая проверка ничего не добавит.
            return False
        if not updater_feature.is_auto_update_enabled():
            if at_startup:
                log("Автообновление отключено: программа сама обновления не проверяет", "🔁 UPDATE")
            return False
        token = updater_feature.begin_update_check(source=source)
        if token is None:
            if at_startup:
                log(
                    "Автопроверка при запуске не запущена: проверка уже идёт или выполнена в этой сессии",
                    "🔁 UPDATE",
                )
            return False
        check_token = int(token)
        check_source = source
        check_signalled = time.monotonic() < signal_granted_until
        if at_startup:
            try:
                set_status("Проверка обновлений...")
            except Exception:
                pass

        enqueue_subsystem_task(
            "update",
            "StartupUpdateCheckWorker" if at_startup else "BackgroundUpdateCheckWorker",
            _update_check_worker,
        )
        return True

    def _on_release_signalled(version: str, attempt: int = 0) -> None:
        """Сервер разрешил обновиться: проверяем и ставим сразу.

        Если человек занят, установка ждёт простоя: разрешение сервера не
        теряется, срок его действия отсчитывается с момента, когда программа
        освободилась.
        """
        nonlocal signal_granted_until, signal_retries_left, deferred_version
        if not is_startup_host_alive(startup_host):
            return
        if attempt == 0 and not updater_feature.is_auto_update_enabled():
            deferred_version = ""
            return
        busy = _busy_reason() if attempt == 0 else ""
        if busy:
            if deferred_version != version:
                deferred_version = version
                _on_update_deferred(version, busy)
            schedule_after(BUSY_RECHECK_MS, lambda: _on_release_signalled(version))
            return
        if attempt == 0:
            if deferred_version == version:
                log(f"Программа освободилась: ставим обновление v{version}", "🔁 UPDATE")
            deferred_version = ""
            signal_granted_until = time.monotonic() + SIGNAL_GRANT_SECONDS
            signal_retries_left = SIGNAL_CHECK_MAX_RETRIES
        if _start_update_check(_SOURCE_BACKGROUND):
            return
        if attempt < SIGNAL_CHECK_MAX_RETRIES:
            schedule_after(
                SIGNAL_CHECK_RETRY_MS,
                lambda: _on_release_signalled(version, attempt + 1),
            )

    update_bridge.release_signalled.connect(_on_release_signalled)

    def _busy_reason() -> str:
        try:
            return str(updater_feature.update_busy_reason() or "")
        except Exception:
            return ""

    def _on_update_deferred(version: str, busy: str) -> None:
        what = _BUSY_TEXTS.get(busy, "программа занята")
        log(f"Обновление v{version} отложено: {what}", "🔁 UPDATE")
        try:
            set_status(f"Обновление v{version} поставится, когда программа освободится")
        except Exception:
            pass
        try:
            notify(
                advisory_notification(
                    level="info",
                    title=f"Вышла версия v{version}",
                    content=(
                        f"Сейчас {what}, поэтому обновление подождёт и поставится само, "
                        "когда вы закончите."
                    ),
                    source="update.deferred",
                    presentation="infobar",
                    queue="immediate",
                    duration=10000,
                    dedupe_key=f"update.deferred:{version}",
                )
            )
        except Exception as exc:
            log(f"Не удалось показать уведомление об отложенном обновлении: {exc}", "❌ ERROR")

    def _on_release_queued(version: str) -> None:
        """Версия вышла, но очередь на скачивание ещё не дошла: говорим, что будет."""
        if not is_startup_host_alive(startup_host):
            return
        try:
            set_status(f"Обновление v{version} ждёт очереди на скачивание")
        except Exception:
            pass
        try:
            notify(
                advisory_notification(
                    level="info",
                    title=f"Вышла версия v{version}",
                    content=(
                        "Программа обновится сама, когда подойдёт её очередь на скачивание. "
                        "Не хотите ждать — «Серверы» → «Подробнее» → «Обновить»."
                    ),
                    source="update.queued",
                    presentation="infobar",
                    queue="immediate",
                    duration=12000,
                    dedupe_key=f"update.queued:{version}",
                )
            )
        except Exception as exc:
            log(f"Не удалось показать уведомление об очереди обновления: {exc}", "❌ ERROR")

    update_bridge.release_queued.connect(_on_release_queued)

    def _note_window_presence() -> None:
        """Программа запущена сразу в трей: окно ни разу не показывалось и само
        о себе ничего не отметило. Говорим за него, иначе она промолчала бы,
        чем занята."""
        try:
            from core.runtime import presence

            if presence.window_shown() is None:
                presence.note_window_shown(bool(startup_host.is_window_shown()))
        except Exception:
            pass

    def _start_release_watcher() -> None:
        nonlocal release_watcher
        _note_window_presence()
        try:
            watcher = updater_feature.create_release_watcher(
                on_release=lambda version: update_bridge.release_signalled.emit(str(version or "")),
                on_queued=lambda version: update_bridge.release_queued.emit(str(version or "")),
                is_bypass_running=_bypass_probe(),
            )
            watcher.start()
            release_watcher = watcher
        except Exception as exc:
            # Без сервера с очередью программа проверяет обновления сама.
            log(f"Слушатель новых версий не запущен: {exc}", "WARNING")

    def _bypass_probe():
        """Включён ли обход: снимок состояния читается из любого потока."""
        if ui_state_store is None:
            return None
        return lambda: bool(ui_state_store.snapshot().launch_running)

    def _server_queue_reachable() -> bool:
        watcher = release_watcher
        return watcher is not None and watcher.reachable is True

    def _schedule_background_check() -> None:
        delay_ms = BACKGROUND_CHECK_INTERVAL_MS + random.randint(0, BACKGROUND_CHECK_JITTER_MS)
        schedule_after(delay_ms, _run_background_check)

    def _run_background_check() -> None:
        if not is_startup_host_alive(startup_host):
            return
        # Сервер с очередью на связи — он и сообщит о версии: проверка по
        # расписанию от миллиона программ только нагружала бы Forgejo.
        if not _server_queue_reachable():
            _start_update_check(_SOURCE_BACKGROUND)
        _schedule_background_check()

    def _interrupted_update_worker() -> None:
        """Ищет обновление, которое не довёл до конца прошлый запуск.

        Проверка не зависит от настройки автообновления: сорвавшаяся установка
        — это факт о состоянии программы, а не предложение обновиться.

        Идёт в фоне: первый импорт updater.install и чтение его файлов в
        GUI-потоке задерживали кадр на ~45 мс сразу после появления окна.
        """
        try:
            from updater.install.interrupted import (
                describe_interrupted_update,
                detect_interrupted_update,
            )

            interrupted = detect_interrupted_update()
            if interrupted is None:
                return
            # Если эту версию программа ставила сама, сервер узнает о неудаче
            # и придержит раздачу версии остальным.
            updater_feature.note_auto_install_failed(str(interrupted.expected_version or ""))
            update_bridge.interrupted_update_found.emit(
                str(interrupted.expected_version or ""),
                str(describe_interrupted_update(interrupted) or ""),
            )
        except Exception as exc:
            log(f"Не удалось разобрать состояние прошлого обновления: {exc}", "❌ ERROR")

    def _on_interrupted_update_found(expected_version: str, description: str) -> None:
        if not is_startup_host_alive(startup_host):
            return
        try:
            notify(
                advisory_notification(
                    level="warning",
                    title="Обновление не завершилось",
                    content=description,
                    source="startup.update_recovery",
                    presentation="infobar",
                    queue="immediate",
                    duration=15000,
                    dedupe_key=f"startup.update_recovery:{expected_version}",
                )
            )
        except Exception as exc:
            log(f"Не удалось показать уведомление о прошлом обновлении: {exc}", "❌ ERROR")

    update_bridge.interrupted_update_found.connect(_on_interrupted_update_found)

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
                idle_tasks.add(
                    "WhatsNewDialog",
                    lambda: _on_whats_new_ready(history, attempt + 1),
                    delay_ms=_WHATS_NEW_RETRY_MS,
                )
            return
        log(f"Показано «Что нового» для v{APP_VERSION}", "🔁 UPDATE")
        enqueue_subsystem_task(
            "update",
            "WhatsNewMarkSeen",
            lambda: updater_feature.mark_whats_new_seen(APP_VERSION),
        )

    update_bridge.whats_new_ready.connect(
        lambda history: idle_tasks.add("WhatsNewDialog", lambda: _on_whats_new_ready(history))
    )

    def _schedule_startup_update_check_deferred() -> None:
        if not is_startup_host_alive(startup_host):
            return
        enqueue_subsystem_task("update", "InterruptedUpdateCheck", _interrupted_update_worker)
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
        # Слушатель стартует первым: проверка при запуске спросит у него,
        # ведёт ли сервер очередь.
        _start_release_watcher()
        schedule_after(delay_ms, lambda: _start_update_check(_SOURCE_STARTUP))
        _schedule_background_check()

    def _mark_update_app_ready() -> None:
        # Если эту версию только что поставило обновление, его окно-продолжение
        # ещё на экране: сообщаем, что новая версия открылась, и оно гаснет.
        from config.build_info import APP_VERSION

        try:
            if updater_feature.mark_update_app_ready(APP_VERSION):
                log("Окну обновления сообщено: новая версия открылась", "🔁 UPDATE")
        except Exception as exc:
            log(f"Не удалось сообщить окну обновления о запуске: {exc}", "WARNING")

    bind_startup_gate(
        startup_host.startup_interactive_ready,
        lambda: is_startup_host_alive(startup_host)
        and enqueue_subsystem_task("update", "UpdateAppReadyMark", _mark_update_app_ready),
        is_ready=lambda: bool(startup_host.startup_state.interactive_logged),
    )

    bind_startup_gate(
        startup_host.startup_post_init_ready,
        _schedule_startup_update_check_deferred,
        is_ready=lambda: bool(startup_host.startup_state.post_init_ready),
    )
