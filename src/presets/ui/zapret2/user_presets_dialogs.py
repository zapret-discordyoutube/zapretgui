"""Dialog-окна страницы preset-ов Zapret 2."""

from __future__ import annotations

from presets.ui.common.preset_import_dialog import ImportPresetDialog as _ImportPresetDialog
from presets.ui.common.user_presets_dialogs import (
    CreatePresetDialog as _CreatePresetDialog,
    RenamePresetDialog as _RenamePresetDialog,
    ResetAllPresetsDialog as _ResetAllPresetsDialog,
)


class CreatePresetDialog(_CreatePresetDialog):
    tr_prefix = "page.winws2_user_presets"


class RenamePresetDialog(_RenamePresetDialog):
    tr_prefix = "page.winws2_user_presets"


class ResetAllPresetsDialog(_ResetAllPresetsDialog):
    tr_prefix = "page.winws2_user_presets"


class ImportPresetDialog(_ImportPresetDialog):
    tr_prefix = "page.winws2_user_presets"


__all__ = ["CreatePresetDialog", "ImportPresetDialog", "RenamePresetDialog", "ResetAllPresetsDialog"]
