"""Дирижёр зацикленных анимаций значков на странице.

Если анимировать все значки разом и постоянно, страница превратится в
мельтешение, а процессор будет рисовать кадры без перерыва. Поэтому
значки играют свои короткие жесты по очереди: раз в ``LOOP_STEP_MS``
дирижёр выбирает следующий значок, который сейчас виден на экране, и
просит его сыграть жест (меньше секунды). Между жестами кадров нет —
ждёт только один редкий таймер.

Дирижёр один на общего родителя (например, содержимое страницы), поэтому
плитки сводки и быстрых действий делят одну очередь. Он спит, пока
родитель скрыт, окно свёрнуто или выключены «Живые анимации».
"""

from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QTimer

from ui.animation_policy import are_live_animations_enabled
from ui.frame_clock import frame_clock


LOOP_STEP_MS = 2600
_CONDUCTOR_NAME = "zapretIconLoopConductor"


class IconLoopConductor(QObject):
    def __init__(self, host) -> None:
        super().__init__(host)
        self.setObjectName(_CONDUCTOR_NAME)
        self._host = host
        self._icons: list = []
        self._next = 0
        self._timer = QTimer(self)
        self._timer.setInterval(LOOP_STEP_MS)
        self._timer.timeout.connect(self._step)
        host.installEventFilter(self)
        self._sync()

    def register(self, icon) -> None:
        if icon not in self._icons:
            self._icons.append(icon)
            icon.destroyed.connect(lambda *_args, ref=icon: self._forget(ref))
        self._sync()

    def icons(self) -> list:
        return list(self._icons)

    def is_running(self) -> bool:
        return self._timer.isActive()

    def _forget(self, icon) -> None:
        try:
            self._icons.remove(icon)
        except ValueError:
            pass
        self._sync()

    def _host_can_animate(self) -> bool:
        host = self._host
        if host is None or sip.isdeleted(host) or not host.isVisible():
            return False
        window = host.window()
        if window is not None and window.isMinimized():
            return False
        return are_live_animations_enabled()

    def _sync(self) -> None:
        host = self._host
        if self._icons and host is not None and not sip.isdeleted(host) and host.isVisible():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    def _on_screen(self, icon) -> bool:
        if sip.isdeleted(icon) or not icon.isVisible():
            return False
        # Значок, прокрученный за край страницы, не анимируем.
        return not icon.visibleRegion().isEmpty()

    def _step(self) -> None:
        host = self._host
        if host is None or sip.isdeleted(host) or not host.isVisible():
            # Страницу ушли — таймер стоит до следующего показа (событие Show).
            self._timer.stop()
            return
        if not self._host_can_animate():
            # Окно свёрнуто или анимации выключены: о развороте окна родитель
            # не узнаёт, поэтому просто пропускаем ход, таймер редкий.
            return
        if frame_clock().is_paused():
            # Сеанс заблокирован или дисплей выключен: жест никто не увидит.
            # Ход пропускаем, а таймер идёт дальше — после возвращения жесты
            # продолжатся сами.
            return
        count = len(self._icons)
        for offset in range(count):
            icon = self._icons[(self._next + offset) % count]
            if self._on_screen(icon) and icon.play_loop():
                self._next = (self._next + offset + 1) % count
                return

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._host and event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            self._sync()
        return super().eventFilter(watched, event)


def icon_loop_conductor(host) -> IconLoopConductor:
    """Общий дирижёр для ``host``: создаётся при первом обращении."""
    found = host.findChild(IconLoopConductor, _CONDUCTOR_NAME)
    return found if found is not None else IconLoopConductor(host)


__all__ = ["LOOP_STEP_MS", "IconLoopConductor", "icon_loop_conductor"]
