"""Run the local copy of the sources as if it were the installed Zapret.

Started by the ``ZapretFromSource`` scheduled task (administrator rights) after
``start.py`` copied ``src`` next to this file. See ``start.py`` for the whole
chain.

The program refuses to start from sources and takes its install folder from
``sys.executable``. Here both point at the installed build, so the sources
use the live folder ``C:\\Zapret\\Dev``: its settings, logs, presets, lists
and engine. The automatic update is switched off: otherwise a newer published
Dev version would install itself over ``C:\\Zapret\\Dev`` and close this run.
"""

from __future__ import annotations

import importlib.abc
import runpy
import sys
from pathlib import Path

INSTALLED_EXE = r"C:\Zapret\Dev\_internal\Zapret.exe"
SRC = Path(__file__).resolve().parent / "src"


def _no_update_check(module) -> None:
    if not hasattr(module, "install_update_check"):
        raise RuntimeError(
            "main.post_startup.install_update_check is gone: "
            "update scripts/dev_from_source/run_app.py"
        )
    module.install_update_check = lambda *args, **kwargs: None


# Patches applied right after a module is imported, so nothing is imported
# earlier than the program itself would import it.
_AFTER_IMPORT = {"main.post_startup": _no_update_check}


class _PatchAfterImport(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        patch = _AFTER_IMPORT.get(name)
        if patch is None:
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(name, path, target)
            if spec is not None:
                break
        else:
            return None
        original_exec = spec.loader.exec_module

        def exec_module(module):
            original_exec(module)
            patch(module)

        spec.loader.exec_module = exec_module
        return spec


def main() -> None:
    # pywin32 finds its DLLs differently when sys.frozen is set (next to the
    # exe, which has none for this Python): load it while the flag is off.
    import pythoncom  # noqa: F401
    import pywintypes  # noqa: F401

    sys.frozen = True
    sys.executable = INSTALLED_EXE
    sys.path[0] = str(SRC)
    sys.meta_path.insert(0, _PatchAfterImport())
    runpy.run_path(str(SRC / "main.py"), run_name="__main__")


if __name__ == "__main__":
    main()
