from __future__ import annotations

"""Обучающий тур при первом запуске.

Тур показывается каждому пользователю один раз: и после новой установки,
и после обновления со старой версии. Флаг «тур уже показан» лежит в
settings.sqlite3 (warnings.onboarding_tour_done); читаем и пишем его в
фоновой очереди, чтобы не трогать базу из GUI-потока.

Сам тур стартует, только когда окно видно на экране и поверх него нет
модального диалога. Если программа стартовала в трей, ждём, пока окно
откроют. Флаг ставится в момент, когда тур реально появился: даже если
программа упадёт посреди тура, он не будет всплывать на каждом запуске.
Повторить тур можно кнопкой на главной.
"""

from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal

from log.log import log
from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import enqueue_subsystem_task, schedule_after


# Пауза после окончания старта: даём достроиться группам бокового меню
# и не накрываем окно в первую же секунду.
ONBOARDING_START_DELAY_MS = 1200
# Как часто пробовать снова, пока окно скрыто или открыт диалог.
ONBOARDING_RETRY_FAST_MS = 1500
ONBOARDING_RETRY_SLOW_MS = 5000
ONBOARDING_FAST_RETRIES = 40


class _OnboardingFlagBridge(QObject):
    result_ready = pyqtSignal(bool)


def _read_tour_done() -> bool:
    from settings.store import get_onboarding_tour_done

    return bool(get_onboarding_tour_done())


def _mark_tour_done() -> None:
    try:
        from settings.store import set_onboarding_tour_done

        set_onboarding_tour_done(True)
    except Exception as exc:
        log(f"Не удалось запомнить, что обучающий тур показан: {exc}", "WARNING")


def install_onboarding_tour(startup_host, *, log_startup_metric=None) -> None:
    bridge = _OnboardingFlagBridge(QCoreApplication.instance())
    attempts = 0

    def _try_start() -> None:
        nonlocal attempts
        if not is_startup_host_alive(startup_host):
            return
        try:
            started = bool(startup_host.start_onboarding_tour())
        except Exception as exc:
            log(f"Не удалось показать обучающий тур: {exc}", "❌ ERROR")
            return
        if started:
            log("Показан обучающий тур первого запуска", "INFO")
            if callable(log_startup_metric):
                try:
                    log_startup_metric("OnboardingTourShown", f"attempt={attempts}")
                except Exception:
                    pass
            enqueue_subsystem_task("onboarding", "OnboardingTourMarkDone", _mark_tour_done)
            return
        attempts += 1
        delay = ONBOARDING_RETRY_FAST_MS if attempts < ONBOARDING_FAST_RETRIES else ONBOARDING_RETRY_SLOW_MS
        schedule_after(delay, _try_start)

    def _on_flag_read(done: bool) -> None:
        if done or not is_startup_host_alive(startup_host):
            return
        schedule_after(ONBOARDING_START_DELAY_MS, _try_start)

    bridge.result_ready.connect(_on_flag_read)

    def _flag_worker() -> None:
        try:
            done = _read_tour_done()
        except Exception as exc:
            # Не смогли прочитать флаг — лучше не показывать тур, чем
            # показывать его на каждом запуске.
            log(f"Не удалось прочитать флаг обучающего тура: {exc}", "WARNING")
            done = True
        bridge.result_ready.emit(done)

    def _begin() -> None:
        if not is_startup_host_alive(startup_host):
            return
        enqueue_subsystem_task("onboarding", "OnboardingTourFlagRead", _flag_worker)

    bind_startup_gate(
        startup_host.startup_post_init_ready,
        _begin,
        is_ready=lambda: bool(startup_host.startup_state.post_init_ready),
    )


__all__ = ["install_onboarding_tour"]
