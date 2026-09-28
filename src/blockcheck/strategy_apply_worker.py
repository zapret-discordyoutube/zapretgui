from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log


class StrategyApplyWorker(QThread):
    completed = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(
        self,
        request_id: int,
        *,
        apply_strategy,
        strategy_args: str,
        strategy_name: str,
        scan_target: str,
        scan_protocol: str,
        apply_lines=(),
        parent=None,
    ):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._apply_strategy = apply_strategy
        self._strategy_args = str(strategy_args or "")
        self._strategy_name = str(strategy_name or "")
        self._scan_target = str(scan_target or "")
        self._scan_protocol = str(scan_protocol or "")
        self._apply_lines = tuple(str(line) for line in (apply_lines or ()))

    def run(self) -> None:
        try:
            result = self._apply_strategy(
                strategy_args=self._strategy_args,
                strategy_name=self._strategy_name,
                scan_target=self._scan_target,
                scan_protocol=self._scan_protocol,
                apply_lines=self._apply_lines,
            )
        except Exception as exc:
            log(f"StrategyApplyWorker: не удалось применить стратегию: {exc}", "WARNING")
            self.failed.emit(self._request_id, str(exc))
            return
        self.completed.emit(self._request_id, result)
