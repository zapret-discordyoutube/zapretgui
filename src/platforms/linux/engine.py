from __future__ import annotations

from platforms.factory_engine import FactoryEngine


class LinuxEngine(FactoryEngine):
    """Linux: still the shared factory; the nfqws2 runner and the root helper replace it in the engine phase."""
