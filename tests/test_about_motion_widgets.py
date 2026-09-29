from __future__ import annotations

import os
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QScrollArea, QVBoxLayout, QWidget

import ui.widgets.stagger_float_in as float_module
import ui.widgets.turning_globe as globe_module
from ui.widgets.stagger_float_in import attach_stagger_float_in, skip_float_in
from ui.widgets.turning_globe import GLOBE_ICONS, TurningGlobe


def _wait(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QApplication.processEvents()


class StaggerFloatInTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        patcher = mock.patch.object(float_module, "are_live_animations_enabled", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.container = QWidget()
        self.addCleanup(self.container.deleteLater)
        layout = QVBoxLayout(self.container)
        self.cards = [QLabel(f"card {index}") for index in range(3)]
        self.own_entrance = skip_float_in(QLabel("motto"))
        layout.addWidget(self.own_entrance)
        for card in self.cards:
            layout.addWidget(card)
        self.container.resize(300, 200)
        self.controller = attach_stagger_float_in(self.container)

    def test_cards_float_in_one_after_another_then_effects_are_removed(self) -> None:
        self.container.show()
        _wait(0.12)

        self.assertTrue(self.controller.is_running())
        self.assertIsNone(self.own_entrance.graphicsEffect())
        progress = [card.graphicsEffect()._progress for card in self.cards]
        self.assertGreater(progress[0], progress[2])
        self.container.grab()

        _wait((float_module.FLOAT_IN_DURATION_MS + 3 * float_module.FLOAT_IN_STEP_MS) / 1000 + 0.2)
        self.assertFalse(self.controller.is_running())
        self.assertTrue(all(card.graphicsEffect() is None for card in self.cards))

    def test_nothing_floats_when_live_animations_are_off(self) -> None:
        with mock.patch.object(float_module, "are_live_animations_enabled", return_value=False):
            self.container.show()
            _wait(0.05)
        self.assertFalse(self.controller.is_running())
        self.assertTrue(all(card.graphicsEffect() is None for card in self.cards))

    def test_cards_below_the_window_edge_stay_in_place(self) -> None:
        scroll = QScrollArea()
        self.addCleanup(scroll.deleteLater)
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        top = QLabel("top")
        top.setFixedHeight(40)
        below = QLabel("below")
        below.setFixedHeight(40)
        layout.addWidget(top)
        layout.addSpacing(600)
        layout.addWidget(below)
        scroll.setWidget(content)
        controller = attach_stagger_float_in(content)
        scroll.resize(300, 200)
        scroll.show()
        _wait(0.05)

        self.assertTrue(controller.is_running())
        self.assertIsNotNone(top.graphicsEffect())
        self.assertIsNone(below.graphicsEffect())

    def test_hiding_finishes_immediately(self) -> None:
        self.container.show()
        _wait(0.05)
        self.container.hide()
        QApplication.processEvents()
        self.assertFalse(self.controller.is_running())
        self.assertTrue(all(card.graphicsEffect() is None for card in self.cards))


class TurningGlobeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_globe_turns_to_next_continent_and_stops_when_hidden(self) -> None:
        with mock.patch.object(globe_module, "are_live_animations_enabled", return_value=True), \
                mock.patch.object(globe_module, "TURN_PERIOD_MS", 1000), \
                mock.patch.object(globe_module, "TURN_DURATION_MS", 300):
            globe = TurningGlobe(size=32)
            self.addCleanup(globe.deleteLater)
            globe.show()
            self.assertTrue(globe.is_animating())
            self.assertEqual(globe.current_icon(), GLOBE_ICONS[0])
            _wait(1.2)
            globe.grab()
            self.assertEqual(globe.current_icon(), GLOBE_ICONS[1])
            globe.hide()
            self.assertFalse(globe.is_animating())

    def test_globe_stands_still_when_live_animations_are_off(self) -> None:
        with mock.patch.object(globe_module, "are_live_animations_enabled", return_value=False):
            globe = TurningGlobe(size=32)
            self.addCleanup(globe.deleteLater)
            globe.show()
            self.assertFalse(globe.is_animating())


class KvnLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_groups_do_not_stretch_in_a_tall_tab(self) -> None:
        # Вкладки лежат в QStackedWidget и получают высоту самой длинной;
        # группы KVN не должны делить лишнее место и оставлять дыру.
        from ui.pages.about_page_kvn_build import build_about_page_kvn_content
        from ui.theme import get_theme_tokens

        tab = QWidget()
        self.addCleanup(tab.deleteLater)
        layout = QVBoxLayout(tab)
        widgets = build_about_page_kvn_content(
            layout,
            tokens=get_theme_tokens(),
            content_parent=tab,
            on_open_kvn_channel=lambda: None,
            on_open_kvn_bot=lambda: None,
            on_open_kvn_github=lambda: None,
        )
        tab.resize(900, 2000)
        tab.show()
        _wait(0.05)

        gap = widgets.links_group.y() - widgets.features_group.geometry().bottom()
        self.assertLess(gap, 40)
        self.assertLess(widgets.features_group.height(), 260)


class PageOpenFloatInTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_every_page_floats_its_cards_in_when_opened(self) -> None:
        from ui.pages.base_page import BasePage

        with mock.patch.object(float_module, "are_live_animations_enabled", return_value=True):
            page = BasePage("Страница", "Описание")
            self.addCleanup(page.deleteLater)
            card = QLabel("card")
            page.add_widget(card)
            page.resize(500, 400)
            page.show()
            _wait(0.05)

            self.assertTrue(float_module.stagger_float_in(page.content).is_running())
            self.assertIsNotNone(card.graphicsEffect())

            page.hide()
            QApplication.processEvents()
            self.assertIsNone(card.graphicsEffect())


class AboutPageMotionWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_every_about_tab_floats_in_and_premium_star_twinkles(self) -> None:
        from app.state_store import MainWindowStateStore
        from donater.premium_display import PREMIUM_TIERS, TIER_UNKNOWN, PremiumDisplay
        from ui.pages.about_page import AboutPage
        from ui.widgets.stagger_float_in import stagger_float_in

        page = AboutPage(
            open_premium=lambda: None,
            open_updates=lambda: None,
            create_open_action_worker=lambda *_args, **_kwargs: None,
            ui_state_store=MainWindowStateStore(),
        )
        self.addCleanup(page.cleanup)
        self.addCleanup(page.deleteLater)

        for tab in (page._about_tab, page._help_tab, page._kvn_tab):
            self.assertIsNotNone(stagger_float_in(tab))
        # Стопку вкладок целиком не двигаем: её карточки выплывают сами.
        self.assertTrue(page.stacked_widget.__dict__.get(float_module.NO_FLOAT_IN_ATTR))

        page.update_subscription_status(PremiumDisplay(tier=next(iter(PREMIUM_TIERS)), days=37))
        self.assertGreater(page.sub_status_icon._twinkle_interval_ms, 0)
        page.update_subscription_status(PremiumDisplay(tier=TIER_UNKNOWN))
        self.assertEqual(page.sub_status_icon._twinkle_interval_ms, 0)


if __name__ == "__main__":
    unittest.main()
