# donater/types.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ActivationStatus:
    is_activated: bool
    days_remaining: Optional[int]
    expires_at: Optional[str]
    status_message: str
    # When known from server response: whether device is linked/recognized.
    # None means "unknown" (client will fallback to local token presence).
    is_linked: Optional[bool] = None
    subscription_level: str = "–"
    source: str = "api"
