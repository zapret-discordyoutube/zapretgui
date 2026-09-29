from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HostsCommandResult:
    success: bool
    message: str = ""
    error: str = ""
    changed: bool = False


@dataclass(frozen=True, slots=True)
class HostsApplyResult:
    success: bool
    message: str = ""
    # Свежий HostsPageSnapshot после записи (None, если перечитать не удалось).
    snapshot: object | None = None


@dataclass(frozen=True, slots=True)
class HostsFileText:
    """Весь текст hosts для страницы «Файл hosts» и признаки доступа."""

    text: str
    path: str
    exists: bool = True
    readable: bool = True
    read_only: bool = False
