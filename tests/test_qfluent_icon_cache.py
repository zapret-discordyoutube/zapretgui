"""Значки qfluentwidgets не разбираются заново на каждую перерисовку."""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication

import qfluentwidgets.common.icon as fluent_icon
from qfluentwidgets import FluentIcon

from ui import qfluent_icon_cache


def _render(draw) -> QImage:
    image = QImage(48, 48, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        draw(painter)
    finally:
        painter.end()
    return image


class QFluentIconCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])
        # Картинки «как было» снимаем до установки запаса.
        cls._plain_before = _render(
            lambda painter: FluentIcon.GAME.render(painter, QRectF(11.5, 10, 16, 16))
        )
        cls._colored_before = _render(
            lambda painter: FluentIcon.UP.render(painter, QRectF(4, 4, 8, 8), fill="#ff8800")
        )
        qfluent_icon_cache.install_qfluent_icon_cache(cls._app)

    def setUp(self) -> None:
        qfluent_icon_cache.clear_qfluent_icon_cache()

    def test_install_is_idempotent(self) -> None:
        patched = fluent_icon.drawSvgIcon
        qfluent_icon_cache.install_qfluent_icon_cache(self._app)
        self.assertIs(fluent_icon.drawSvgIcon, patched)

    def test_picture_is_pixel_identical(self) -> None:
        for _ in range(2):  # первый раз — разбор, второй — из запаса
            plain = _render(lambda painter: FluentIcon.GAME.render(painter, QRectF(11.5, 10, 16, 16)))
            colored = _render(
                lambda painter: FluentIcon.UP.render(painter, QRectF(4, 4, 8, 8), fill="#ff8800")
            )
            self.assertEqual(plain, self._plain_before)
            self.assertEqual(colored, self._colored_before)

    def test_plain_icon_is_parsed_once(self) -> None:
        for _ in range(10):
            _render(lambda painter: FluentIcon.GAME.render(painter, QRectF(0, 0, 16, 16)))
        self.assertEqual(len(qfluent_icon_cache._renderers), 1)
        renderer = next(iter(qfluent_icon_cache._renderers.values()))
        _render(lambda painter: FluentIcon.GAME.render(painter, QRectF(0, 0, 16, 16)))
        self.assertIs(next(iter(qfluent_icon_cache._renderers.values())), renderer)

    def test_recolored_icon_reads_file_once(self) -> None:
        opened: list[str] = []

        class _CountingFile(fluent_icon.QFile):
            def __init__(self, path) -> None:
                opened.append(path)
                super().__init__(path)

        with mock.patch.object(fluent_icon, "QFile", _CountingFile):
            for _ in range(10):
                _render(
                    lambda painter: FluentIcon.UP.render(painter, QRectF(0, 0, 8, 8), fill="#123456")
                )
        self.assertEqual(len(opened), 1)
        # Другой цвет — другой значок.
        fluent_icon.writeSvg(FluentIcon.UP.path(), fill="#654321")
        self.assertEqual(len(qfluent_icon_cache._svg_texts), 2)

    def test_qicon_is_built_once_and_copies_are_independent(self) -> None:
        first = FluentIcon.GAME.icon()
        second = FluentIcon.GAME.icon()
        self.assertFalse(first.isNull())
        self.assertEqual(len(qfluent_icon_cache._icons), 1)
        self.assertIsNot(first, second)
        self.assertEqual(first.cacheKey(), second.cacheKey())

        # Изменённая копия не портит запас.
        second.addFile(FluentIcon.UP.path())
        self.assertNotEqual(second.cacheKey(), FluentIcon.GAME.icon().cacheKey())
        self.assertEqual(first.cacheKey(), FluentIcon.GAME.icon().cacheKey())

    def test_files_from_disk_are_not_cached(self) -> None:
        import tempfile

        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M0 0h16v16H0z"/></svg>'
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "own.svg")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(svg)
            _render(lambda painter: fluent_icon.drawSvgIcon(path, painter, QRectF(0, 0, 16, 16)))
            self.assertIn('fill="#ff0000"', fluent_icon.writeSvg(path, fill="#ff0000"))
        self.assertEqual(len(qfluent_icon_cache._renderers), 0)
        self.assertEqual(len(qfluent_icon_cache._svg_texts), 0)

    def test_runtime_installs_the_cache(self) -> None:
        import inspect

        from main import qt_runtime

        self.assertIn("install_qfluent_icon_cache(app)", inspect.getsource(qt_runtime.ensure_qt_runtime))


if __name__ == "__main__":
    unittest.main()
