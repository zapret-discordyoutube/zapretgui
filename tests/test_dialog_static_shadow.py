"""Тень диалогов рисуется готовой картинкой, а не живым размытием."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QRegion  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

from ui import dialog_static_shadow  # noqa: E402
from ui.dialog_static_shadow import DIALOG_CORNER_RADIUS, DialogStaticShadow, shadow_patch  # noqa: E402
from ui.fluent_dialog import ColorDialog, MessageBox, MessageBoxBase  # noqa: E402


_APP = QApplication.instance() or QApplication([])

_BLUR = 60
_OFFSET = (0, 10)


class _Dialog(MessageBoxBase):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        label = QLabel("текст", self.widget)
        label.setMinimumSize(360, 220)
        self.viewLayout.addWidget(label)


class DialogStaticShadowTests(unittest.TestCase):
    def _parent(self) -> QWidget:
        parent = QWidget()
        parent.resize(1000, 760)
        parent.show()
        self.addCleanup(parent.deleteLater)
        return parent

    def _shown_dialog(self) -> _Dialog:
        dialog = _Dialog(self._parent())
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        _APP.processEvents()
        return dialog

    def test_project_dialogs_have_no_live_shadow_effect(self) -> None:
        # Живой эффект заново размывает всю панель при перерисовке любого
        # виджета внутри неё: на win10 это 47 % ядра при анимации в диалоге.
        parent = self._parent()
        dialogs = (
            _Dialog(parent),
            MessageBox("Заголовок", "Текст", parent),
            ColorDialog(QColor(0, 120, 215), "Цвет", parent),
        )
        for dialog in dialogs:
            self.addCleanup(dialog.deleteLater)
            with self.subTest(dialog=type(dialog).__name__):
                self.assertIsNone(dialog.widget.graphicsEffect())
                self.assertIsInstance(dialog._static_shadow, DialogStaticShadow)

    def test_shadow_frame_surrounds_panel_with_library_offset(self) -> None:
        dialog = self._shown_dialog()
        panel = dialog.widget.geometry()

        self.assertEqual(
            dialog._static_shadow.geometry(),
            panel.adjusted(-_BLUR, -_BLUR, _BLUR, _BLUR).translated(*_OFFSET),
        )

    def test_shadow_frame_follows_moved_and_resized_panel(self) -> None:
        dialog = self._shown_dialog()

        dialog.widget.move(dialog.widget.pos() + QPoint(37, 21))
        dialog.widget.setFixedSize(dialog.widget.width() + 40, dialog.widget.height() + 30)
        _APP.processEvents()

        panel = dialog.widget.geometry()
        self.assertEqual(
            dialog._static_shadow.geometry(),
            panel.adjusted(-_BLUR, -_BLUR, _BLUR, _BLUR).translated(*_OFFSET),
        )

    def test_shadow_frame_is_cut_out_under_the_panel(self) -> None:
        # Без выреза рамка перерисовывалась бы на каждый кадр анимации
        # внутри диалога: Qt рисует всё, что лежит под изменившимся местом.
        dialog = self._shown_dialog()
        shadow = dialog._static_shadow
        panel = dialog.widget.geometry()
        mask = shadow.mask()

        centre = panel.center() - shadow.geometry().topLeft()
        below = QPoint(panel.center().x(), panel.bottom() + 20) - shadow.geometry().topLeft()
        corner = panel.topLeft() + QPoint(2, 2) - shadow.geometry().topLeft()

        self.assertFalse(mask.contains(centre))
        self.assertTrue(mask.contains(below))
        # Скруглённый угол панели прозрачен, под ним тень должна остаться.
        self.assertTrue(mask.contains(corner))
        self.assertGreater(DIALOG_CORNER_RADIUS, 2)

    def test_shadow_frame_sits_between_mask_and_panel(self) -> None:
        dialog = self._shown_dialog()
        children = [child for child in dialog.children() if isinstance(child, QWidget)]

        self.assertLess(children.index(dialog.windowMask), children.index(dialog._static_shadow))
        self.assertLess(children.index(dialog._static_shadow), children.index(dialog.widget))

    def test_shadow_is_drawn_below_the_panel_and_fades_out(self) -> None:
        dialog = self._shown_dialog()
        shadow = dialog._static_shadow
        # Рисуем рамку на прозрачный холст без фона окна: grab() залил бы его.
        image = QImage(shadow.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        shadow.render(image, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
        panel = dialog.widget.geometry().translated(-shadow.geometry().topLeft())
        x = panel.center().x()

        near = QColor.fromRgba(image.pixel(x, panel.bottom() + 3)).alpha()
        far = QColor.fromRgba(image.pixel(x, panel.bottom() + 40)).alpha()
        edge = QColor.fromRgba(image.pixel(x, image.height() - 1)).alpha()

        self.assertGreater(near, 10)
        self.assertGreater(near, far)
        self.assertLessEqual(edge, 1)

    def test_shadow_picture_is_blurred_once_per_process(self) -> None:
        dialog_static_shadow._PATCH_CACHE.clear()
        color = QColor(0, 0, 0, 50)

        first = shadow_patch(_BLUR, color, DIALOG_CORNER_RADIUS, 1.0)
        second = shadow_patch(_BLUR, QColor(0, 0, 0, 50), DIALOG_CORNER_RADIUS, 1.0)

        self.assertIs(first, second)
        self.assertEqual(len(dialog_static_shadow._PATCH_CACHE), 1)
        # Картинка маленькая и не зависит от размера диалога.
        self.assertLess(first.width(), 400)

    def test_shadow_does_not_take_mouse_clicks_from_the_mask(self) -> None:
        dialog = self._shown_dialog()
        panel = dialog.widget.geometry()
        below = QPoint(panel.center().x(), panel.bottom() + 20)

        self.assertIs(dialog.childAt(below), dialog.windowMask)


if __name__ == "__main__":
    unittest.main()
