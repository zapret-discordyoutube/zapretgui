import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from ui.widgets import reveal as reveal_module
from ui.widgets.reveal import Reveal


class RevealTests(unittest.TestCase):
    """Части рисующего виджета проявляются по очереди, один раз и без постоянной нагрузки."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _widget(self) -> QWidget:
        widget = QWidget()
        self.addCleanup(widget.deleteLater)
        widget.resize(100, 40)
        return widget

    def test_parts_appear_one_after_another_and_all_stand_at_the_end(self) -> None:
        widget = self._widget()
        effect = Reveal(widget)
        # До показа и без анимации части стоят на месте.
        self.assertEqual([effect.part(index, 4) for index in range(4)], [1.0, 1.0, 1.0, 1.0])
        effect._on_value(0.3)
        shares = [effect.part(index, 4) for index in range(4)]
        # Первая часть уже видна сильнее следующих; последняя ещё не началась.
        self.assertEqual(shares, sorted(shares, reverse=True))
        self.assertGreater(shares[0], shares[1])
        self.assertEqual(shares[-1], 0.0)
        effect._on_value(1.0)
        self.assertEqual([effect.part(index, 4) for index in range(4)], [1.0, 1.0, 1.0, 1.0])
        self.assertEqual(effect.part(0, 1), 1.0)

    def test_plays_once_on_first_show_and_stops_when_hidden(self) -> None:
        widget = self._widget()
        effect = Reveal(widget)
        with patch.object(reveal_module, "are_live_animations_enabled", return_value=True):
            widget.show()
            self.assertTrue(effect.running())
            self.assertEqual(effect.part(0, 3), 0.0)
            # Спрятали посреди проявления — доигрывать некому: виджет готов.
            widget.hide()
            self.assertFalse(effect.running())
            self.assertEqual(effect.part(2, 3), 1.0)
            # Второй показ уже без анимации.
            widget.show()
            self.assertFalse(effect.running())

    def test_waits_while_its_block_is_flying_in(self) -> None:
        widget = self._widget()
        effect = Reveal(widget)
        flying = [True]
        with patch.object(reveal_module, "are_live_animations_enabled", return_value=True), patch(
            "ui.widgets.stagger_float_in.is_floating_in", lambda _widget: flying[0]
        ):
            widget.show()
            QTest.qWait(120)
            # Блок ещё летит картинкой — части ждут невидимыми, проявление не началось.
            self.assertEqual((effect.value(), effect._anim.state()), (0.0, effect._anim.State.Stopped))
            flying[0] = False
            QTest.qWait(120)
            self.assertEqual(effect._anim.state(), effect._anim.State.Running)
            effect.finish()

    def test_animations_switched_off_means_no_reveal(self) -> None:
        widget = self._widget()
        effect = Reveal(widget)
        with patch.object(reveal_module, "are_live_animations_enabled", return_value=False):
            widget.show()
        self.assertFalse(effect.running())
        self.assertEqual(effect.part(0, 5), 1.0)


if __name__ == "__main__":
    unittest.main()
