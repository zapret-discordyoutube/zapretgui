from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from dns import joke_confirm, joke_effects


class ShakeMathTests(unittest.TestCase):
    def test_shake_starts_and_ends_at_rest_and_stays_inside_the_amplitude(self) -> None:
        self.assertAlmostEqual(joke_effects.shake_offset(0.0), 0.0)
        self.assertAlmostEqual(joke_effects.shake_offset(1.0), 0.0, places=6)
        values = [joke_effects.shake_offset(step / 200) for step in range(201)]
        self.assertTrue(all(abs(value) <= joke_effects.SHAKE_AMPLITUDE_PX for value in values))
        # Окно качается в обе стороны несколько раз.
        signs = [1 if value > 0 else -1 for value in values if abs(value) > 0.5]
        swings = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
        self.assertGreaterEqual(swings, joke_effects.SHAKE_SWINGS)

    def test_every_phrase_has_its_own_title_emoji(self) -> None:
        emojis = [joke_effects.title_emoji(number) for number in range(1, joke_confirm.PHRASE_COUNT + 1)]
        self.assertEqual(len(set(emojis)), joke_confirm.PHRASE_COUNT)
        self.assertEqual(len(joke_effects.TITLE_EMOJI), joke_confirm.PHRASE_COUNT)


class PlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _box(self):
        from ui.fluent_dialog import MessageBox

        parent = QWidget()
        parent.resize(900, 600)
        self.addCleanup(parent.deleteLater)
        box = MessageBox("Заголовок", "Текст", parent)
        self.addCleanup(box.deleteLater)
        return box

    def test_effects_are_off_when_live_animations_are_off(self) -> None:
        box = self._box()
        margins = box.layout().contentsMargins()
        with patch.object(joke_effects, "are_live_animations_enabled", return_value=False):
            joke_effects.play(box, index=1, title="Заголовок")
        self.assertEqual(box.findChildren(joke_effects.EmojiRain), [])
        QTest.qWait(150)
        self.assertEqual(box.layout().contentsMargins(), margins)
        self.assertEqual(box.titleLabel.text(), "Заголовок")

    def test_effects_play_then_leave_the_window_exactly_as_it_was(self) -> None:
        box = self._box()
        margins = box.layout().contentsMargins()
        with patch.object(joke_effects, "are_live_animations_enabled", return_value=True), patch.object(
            joke_effects, "SHAKE_MS", 120
        ), patch.object(joke_effects, "BLINK_MS", 20), patch.object(joke_effects, "RAIN_MS", 200):
            joke_effects.play(box, index=1, title="Заголовок")
            rain = box.findChildren(joke_effects.EmojiRain)
            self.assertEqual(len(rain), 1)
            # Слой украшения не перехватывает мышь: кнопки окна остаются доступными.
            from PyQt6.QtCore import Qt

            self.assertTrue(rain[0].testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
            QTest.qWait(60)
            self.assertNotEqual(box.layout().contentsMargins(), margins)
            QTest.qWait(500)
        self.assertEqual(box.layout().contentsMargins(), margins)
        self.assertEqual(box.titleLabel.text(), f"{joke_effects.title_emoji(1)} Заголовок")
        # Слой убрал себя сам.
        self.assertEqual(box.findChildren(joke_effects.EmojiRain), [])


if __name__ == "__main__":
    unittest.main()
