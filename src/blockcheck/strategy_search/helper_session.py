"""Одна проба стратегии там, где движок запускает служба-помощник (Linux), а не сама программа.

Так же, как ``WinwsSession``, это «запустить, спросить жив ли, остановить». Запуск идёт через общий запускатель
движка платформы: помощник заменяет прежний запуск новым, поэтому пробы идут строго одна за другой, а правила
перехвата поднимаются и снимаются вместе с движком. Процесса и его вывода у программы нет: причина отказа берётся
из ответа помощника.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from blockcheck.strategy_search.winws_session import LUA_SETTLE_SECONDS, SessionStart


class HelperSession:
    def __init__(
        self,
        config_path: str,
        *,
        runner_getter: Callable[[], object],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> None:
        self._config_path = config_path
        self._runner_getter = runner_getter
        self._clock = clock
        self._sleep = sleep
        self._cancelled = cancelled
        self._started = False

    def _runner(self):
        return self._runner_getter()

    def start(self) -> SessionStart:
        started = self._clock()
        if self._cancelled():
            return SessionStart(False, output_tail="остановлено")
        runner = self._runner()
        ok = bool(runner.start_from_preset_file(self._config_path, "BlockCheck"))
        if not ok:
            return SessionStart(False, output_tail=self.output_tail())
        self._started = True
        # Правила уже стоят; ждём, пока движок загрузит Lua стратегии: с ошибкой в ней он завершается сразу.
        self._sleep(LUA_SETTLE_SECONDS)
        if not runner.is_running():
            return SessionStart(False, exit_code=1, output_tail=self.output_tail() or "движок завершился сразу после запуска")
        return SessionStart(True, ready_ms=(self._clock() - started) * 1000)

    def alive(self) -> bool:
        return self._started and bool(self._runner().is_running())

    def output_tail(self, lines: int = 6) -> str:
        text = str(getattr(self._runner(), "last_error_message", "") or "")
        return "\n".join(text.splitlines()[-lines:])

    def stop(self) -> bool:
        if not self._started:
            return True
        self._started = False
        try:
            return bool(self._runner().stop())
        except Exception:
            return False
