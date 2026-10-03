"""Общий такт кадров для живых анимаций.

Раньше у каждой постоянной анимации был свой таймер: значок «Работает» в
заголовке, точка статуса, сцена на главной, глобус. Таймеры срабатывали в
разные моменты, и на каждый из них Qt отдельно перерисовывал свой кусок окна
и отдельно отправлял окно на экран, а Windows (DWM) отдельно собирала кадр.
Две анимации по 30 кадров давали 60 отправок в секунду вместо 30.

Здесь один таймер на всё приложение. Подписчики получают кадр в одном и том
же обороте цикла событий, поэтому их ``update()`` сливаются в одну
перерисовку и одну отправку окна. Картинка у каждой анимации та же: скорость
она по-прежнему считает по времени, а не по числу кадров.

Такт идёт, только пока есть хотя бы один запущенный подписчик и пока экран
вообще кто-то может видеть: при заблокированном сеансе и выключенном дисплее
(``set_paused``) таймер стоит, и программа не рисует кадры в пустоту.

Частота подписчика — кратная базовой (60 в секунду): 60, 30, 20, 15…
Запрошенный интервал округляется к ближайшему кратному, чтобы кадры разных
анимаций совпадали по времени, а не шли вразнобой.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from PyQt6.QtCore import QObject, Qt, QTimer


# Базовый кадр — 1/60 секунды. Интервалы подписчиков кратны ему.
BASE_FRAME_MS = 1000.0 / 60.0
_CLOCK_ATTR = "_zapret_frame_clock"


def _frames_for_interval(interval_ms: float) -> int:
    return max(1, round(float(interval_ms) / BASE_FRAME_MS))


class FrameSubscription:
    """Подписка одной анимации на общий такт.

    Интерфейс повторяет то, чем анимации пользовались у ``QTimer``:
    ``start`` / ``stop`` / ``isActive`` / ``setInterval``.
    """

    def __init__(self, clock: "FrameClock", callback: Callable[[], None], interval_ms: float) -> None:
        self._clock = clock
        self._callback = callback
        self._every = _frames_for_interval(interval_ms)
        self._active = False
        self._started_at = 0.0

    def setInterval(self, interval_ms: float) -> None:  # noqa: N802 - как у QTimer
        every = _frames_for_interval(interval_ms)
        if every == self._every:
            return
        self._every = every
        if self._active:
            self._clock._retune()

    def interval(self) -> float:
        return self._every * BASE_FRAME_MS

    def start(self) -> None:
        self._started_at = time.monotonic()
        if self._active:
            return
        self._active = True
        self._clock._add(self)

    def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        self._clock._remove(self)

    def isActive(self) -> bool:  # noqa: N802 - как у QTimer
        return self._active

    def elapsed_ms(self) -> float:
        """Сколько прошло с последнего ``start()``."""
        return (time.monotonic() - self._started_at) * 1000.0


class FrameClock(QObject):
    """Один таймер кадров на всё приложение."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._subscriptions: list[FrameSubscription] = []
        self._pause_reasons: set[str] = set()
        self._step = 1
        self._frame = 0
        self._timer = QTimer(self)
        # Точный таймер: на Windows Qt ведёт его мультимедийным таймером с
        # шагом 1 мс, и кадры идут ровно. Обычный таймер длиннее 20 мс Qt
        # отдаёт системному (SetCoalescableTimer), а тот привязан к шагу
        # системных часов 15,6 мс. Замер на Windows 10, Qt 6.11: точный
        # таймер на 33 мс даёт интервалы 32–34 мс (30 кадров в секунду),
        # обычный — 42–63 мс (20 кадров, рывками).
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    def subscribe(
        self,
        callback: Callable[[], None],
        *,
        interval_ms: float,
        owner: QObject | None = None,
    ) -> FrameSubscription:
        """Подписка на кадры. ``owner`` — виджет анимации: с его удалением подписка снимается сама."""
        subscription = FrameSubscription(self, callback, interval_ms)
        if owner is not None:
            owner.destroyed.connect(lambda *_: subscription.stop())
        return subscription

    # ---- пауза, когда экран никто не видит -----------------------------

    def set_paused(self, reason: str, paused: bool) -> None:
        """Останавливает такт, пока картинку некому смотреть (сеанс заблокирован, дисплей выключен)."""
        if paused:
            self._pause_reasons.add(str(reason))
        else:
            self._pause_reasons.discard(str(reason))
        self._retune()

    def resume_all(self) -> None:
        """Снимает все паузы. Страховка: пользователь работает с окном — значит, экран он видит."""
        if self._pause_reasons:
            self._pause_reasons.clear()
            self._retune()

    def is_paused(self) -> bool:
        return bool(self._pause_reasons)

    def is_running(self) -> bool:
        return self._timer.isActive()

    def frames_per_second(self) -> float:
        """Частота такта прямо сейчас (0, если такт стоит)."""
        if not self._timer.isActive():
            return 0.0
        return 1000.0 / (self._step * BASE_FRAME_MS)

    # ---- внутреннее ------------------------------------------------------

    def _add(self, subscription: FrameSubscription) -> None:
        if subscription not in self._subscriptions:
            self._subscriptions.append(subscription)
        self._retune()

    def _remove(self, subscription: FrameSubscription) -> None:
        try:
            self._subscriptions.remove(subscription)
        except ValueError:
            pass
        self._retune()

    def _retune(self) -> None:
        if not self._subscriptions or self._pause_reasons:
            self._timer.stop()
            return
        # Такт идёт с самой частой нужной частотой: если всем хватает 30
        # кадров, таймер не просыпается 60 раз в секунду.
        step = 0
        for subscription in self._subscriptions:
            step = math.gcd(step, subscription._every)
        step = max(1, step)
        interval = max(1, round(step * BASE_FRAME_MS))
        if step != self._step or not self._timer.isActive() or self._timer.interval() != interval:
            self._step = step
            # Счётчик кадров остаётся кратным шагу, иначе подписчик с более
            # редкой частотой перестал бы попадать на свои кадры.
            self._frame -= self._frame % step
            self._timer.start(interval)

    def _tick(self) -> None:
        self._frame += self._step
        frame = self._frame
        # Список копируем: подписчик вправе остановить себя прямо из кадра.
        for subscription in tuple(self._subscriptions):
            if subscription._active and frame % subscription._every == 0:
                try:
                    subscription._callback()
                except RuntimeError:
                    # Виджет уже удалён на стороне Qt: его анимации больше нет.
                    subscription.stop()


def frame_clock() -> FrameClock:
    """Общий такт приложения (создаётся при первом обращении)."""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    clock = getattr(app, _CLOCK_ATTR, None) if app is not None else None
    if clock is None:
        clock = FrameClock(app)
        if app is not None:
            setattr(app, _CLOCK_ATTR, clock)
    return clock


__all__ = ["BASE_FRAME_MS", "FrameClock", "FrameSubscription", "frame_clock"]
