from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


class DirectNetworkAccessError(RuntimeError):
    """winws2 could not provide and then safely restore a direct network window."""


def run_with_direct_network_access(operation: Callable[[], T]) -> T:
    """Run one network operation outside an active winws2 process.

    The active runner owns its process lifecycle.  This boundary only locates
    that owner; winws1 and an inactive runtime need no pause.
    """

    if not callable(operation):
        raise TypeError("operation must be callable")

    from winws_runtime.runners.runner_factory import get_current_runner

    runner = get_current_runner()
    execute_direct = getattr(runner, "run_with_direct_network_access", None)
    if not callable(execute_direct):
        return operation()
    return execute_direct(operation)


__all__ = ["DirectNetworkAccessError", "run_with_direct_network_access"]
