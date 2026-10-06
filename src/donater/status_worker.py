from __future__ import annotations

import traceback
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from log.log import log


class PremiumStatusCheckWorker(QObject):
    """Один проход проверки Premium-статуса вне GUI-потока."""

    # request_id, activation_info (или текст ошибки), success
    finished = pyqtSignal(int, object, bool)

    def __init__(
        self,
        request_id: int,
        *,
        get_premium_checker: Callable[[], object],
        check_device_activation: Callable[..., dict],
        use_cache: bool,
        parent=None,
    ):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._get_premium_checker = get_premium_checker
        self._check_device_activation = check_device_activation
        self._use_cache = bool(use_cache)

    def run(self) -> None:
        try:
            premium_checker = self._get_premium_checker()
            activation_info = self._check_device_activation(
                premium_checker,
                use_cache=self._use_cache,
            )
            self.finished.emit(self._request_id, activation_info, True)
        except Exception as e:
            log(f"Ошибка проверки Premium-статуса: {e}", "❌ ERROR")
            log(f"Traceback: {traceback.format_exc()}", "DEBUG")
            self.finished.emit(self._request_id, str(e), False)
