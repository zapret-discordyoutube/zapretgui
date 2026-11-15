"""The engine interface answered by ``winws_runtime.runners.runner_factory``.

That factory and the runners behind it are the Windows engine (winws / winws2 with WinDivert). The Linux
runner (nfqws2 + nftables through the root helper) replaces this on Linux in the engine phase of the
multi-platform plan; until then every backend uses the one factory, so behaviour is unchanged.
"""

from __future__ import annotations


class FactoryEngine:
    def runner(self, engine_exe_path: str):
        from winws_runtime.runners import runner_factory

        return runner_factory.get_strategy_runner(engine_exe_path)

    def current_runner(self):
        from winws_runtime.runners import runner_factory

        return runner_factory.get_current_runner()

    def reset_runner(self) -> None:
        from winws_runtime.runners import runner_factory

        runner_factory.reset_strategy_runner()

    def invalidate_runner(self) -> None:
        from winws_runtime.runners import runner_factory

        runner_factory.invalidate_strategy_runner()

    def running_pids(self) -> list[int]:
        return []  # the engine is found by its program name and path (winws_runtime.runtime.process_probe)
