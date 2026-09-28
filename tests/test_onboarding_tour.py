from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget  # noqa: E402


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class OnboardingFlagSettingsTests(unittest.TestCase):
    def test_flag_defaults_to_not_shown_and_survives_normalize(self) -> None:
        from settings.normalize import normalize_settings
        from settings.schema import build_default_settings

        defaults = build_default_settings()
        self.assertIs(defaults["warnings"]["onboarding_tour_done"], False)

        normalized = normalize_settings({"warnings": {"onboarding_tour_done": True}})
        self.assertIs(normalized["warnings"]["onboarding_tour_done"], True)

    def test_flag_round_trips_through_sqlite_store(self) -> None:
        from settings import store as settings_store

        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(temp_dir)):
                try:
                    settings_store.prepare_settings_database()
                    self.assertFalse(settings_store.get_onboarding_tour_done())
                    settings_store.set_onboarding_tour_done(True)
                    self.assertTrue(settings_store.get_onboarding_tour_done())
                    self.assertTrue(settings_store.read_settings()["warnings"]["onboarding_tour_done"])
                finally:
                    settings_store.close_settings_database()


class OnboardingPostStartupTests(unittest.TestCase):
    def _install(self, *, done: bool, start_results: list[bool]):
        from main import post_startup_onboarding

        _app()
        startup_host = SimpleNamespace(
            startup_post_init_ready=object(),
            startup_state=SimpleNamespace(post_init_ready=True),
            is_alive=Mock(return_value=True),
            start_onboarding_tour=Mock(side_effect=list(start_results)),
        )
        mark_done = Mock()
        delays: list[int] = []

        def _schedule(delay_ms, callback):
            delays.append(int(delay_ms))
            if len(delays) <= len(start_results):
                callback()

        with (
            patch.object(post_startup_onboarding, "bind_startup_gate", side_effect=lambda _s, cb, **_k: cb()),
            patch.object(post_startup_onboarding, "schedule_after", side_effect=_schedule),
            patch.object(post_startup_onboarding, "enqueue_subsystem_task", side_effect=lambda _q, _n, target: target()),
            patch.object(post_startup_onboarding, "_read_tour_done", return_value=done),
            patch.object(post_startup_onboarding, "_mark_tour_done", mark_done),
            patch.object(post_startup_onboarding, "log"),
        ):
            post_startup_onboarding.install_onboarding_tour(startup_host)
        return startup_host, mark_done, delays

    def test_tour_is_not_shown_again_when_flag_is_set(self) -> None:
        startup_host, mark_done, delays = self._install(done=True, start_results=[True])

        startup_host.start_onboarding_tour.assert_not_called()
        mark_done.assert_not_called()
        self.assertEqual(delays, [])

    def test_first_start_shows_tour_and_remembers_it(self) -> None:
        startup_host, mark_done, _delays = self._install(done=False, start_results=[True])

        startup_host.start_onboarding_tour.assert_called_once_with()
        mark_done.assert_called_once_with()

    def test_waits_while_window_is_not_ready_and_marks_only_after_show(self) -> None:
        startup_host, mark_done, delays = self._install(done=False, start_results=[False, False, True])

        self.assertEqual(startup_host.start_onboarding_tour.call_count, 3)
        mark_done.assert_called_once_with()
        self.assertEqual(len(delays), 3)


class _FakePage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.start_btn = QPushButton("Запустить", self)
        layout.addWidget(self.start_btn)

    def onboarding_target(self, name):
        return self.start_btn if name == "start" else None


def _build_window():
    window = QWidget()
    window.resize(900, 640)
    layout = QVBoxLayout(window)
    page = _FakePage(window)
    layout.addWidget(page)

    class Host:
        def show_page(self, _name, allow_internal=False):
            return True

        def get_loaded_page(self, _name):
            return page

        def current_page(self):
            return page

    window.ui_session = SimpleNamespace(
        nav_items={},
        nav_header_by_group={},
        nav_headers=[],
        page_host=Host(),
    )
    return window, page


class OnboardingOverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def _overlay(self, steps):
        from app.page_names import PageName
        from ui.onboarding.overlay import OnboardingOverlay
        from ui.onboarding.steps import TourContext

        window, page = _build_window()
        window.show()
        context = TourContext(
            window=window,
            control_page_name=PageName.ZAPRET2_MODE_CONTROL,
            pages={"control": PageName.ZAPRET2_MODE_CONTROL},
            current_page=page,
        )
        overlay = OnboardingOverlay(window, context, steps)
        return window, overlay

    def test_steps_without_targets_are_skipped(self) -> None:
        from ui.onboarding.steps import TourStep, _page_target

        steps = (
            TourStep("welcome", hero=True),
            TourStep("start", _page_target("start"), page="control"),
            TourStep("missing", _page_target("nothing"), page="control"),
            TourStep("user_presets", _page_target("presets_list"), page="user_presets"),
            TourStep("finish", _page_target("tour_card"), page="control", target_optional=True),
        )
        window, overlay = self._overlay(steps)
        try:
            self.assertTrue(overlay.start())
            # Страницы user_presets в этом режиме нет — шаг убран сразу.
            self.assertEqual(overlay.step_keys(), ["welcome", "start", "missing", "finish"])
            self.assertEqual(overlay.current_step_key(), "welcome")
            overlay.go_next()
            self.assertEqual(overlay.current_step_key(), "start")
            overlay.go_next()
            # Цели «missing» на странице нет — шаг пропущен.
            self.assertEqual(overlay.current_step_key(), "finish")
            overlay.go_back()
            self.assertEqual(overlay.current_step_key(), "start")
        finally:
            window.close()
            window.deleteLater()

    def test_wiki_button_opens_step_article_and_hides_without_one(self) -> None:
        from ui.onboarding.steps import TourStep, _page_target

        url = "https://wiki.zapret.moe/Zapret2/preset"
        steps = (
            TourStep("welcome", hero=True),
            TourStep("start", _page_target("start"), page="control", wiki_url=url),
        )
        window, overlay = self._overlay(steps)
        try:
            overlay.start()
            button = overlay._card.wiki_button
            self.assertTrue(button.isHidden())
            overlay.go_next()
            self.assertFalse(button.isHidden())
            self.assertEqual(button.getUrl().toString(), url)
            self.assertEqual(button.text(), "Подробнее в вики")
            overlay.go_back()
            self.assertTrue(button.isHidden())
        finally:
            window.close()
            window.deleteLater()

    def test_overlay_is_child_of_window_not_separate_window(self) -> None:
        from ui.onboarding.steps import TourStep

        window, overlay = self._overlay((TourStep("welcome", hero=True),))
        try:
            overlay.start()
            self.assertFalse(overlay.isWindow())
            self.assertIs(overlay.parentWidget(), window)
            self.assertFalse(any(child.isWindow() for child in overlay.findChildren(QWidget)))
        finally:
            window.close()
            window.deleteLater()

    def test_skip_finishes_tour(self) -> None:
        from ui.onboarding.steps import TourStep

        window, overlay = self._overlay((TourStep("welcome", hero=True), TourStep("how", hero=True)))
        finished = Mock()
        overlay.finished.connect(finished)
        try:
            overlay.start()
            overlay.finish("skipped", immediate=True)
            finished.assert_called_once_with("skipped")
        finally:
            window.close()
            window.deleteLater()


class OnboardingKeyboardTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def _running_overlay(self):
        from PyQt6.QtTest import QTest

        from app.page_names import PageName
        from ui.onboarding.overlay import OnboardingOverlay
        from ui.onboarding.steps import TourContext, TourStep, _page_target

        window, page = _build_window()
        window.show()
        window.activateWindow()
        QTest.qWaitForWindowActive(window, 1000)
        context = TourContext(
            window=window,
            control_page_name=PageName.ZAPRET2_MODE_CONTROL,
            pages={"control": PageName.ZAPRET2_MODE_CONTROL},
            current_page=page,
        )
        steps = (
            TourStep("welcome", hero=True),
            TourStep("start", _page_target("start"), page="control"),
            TourStep("finish", hero=True),
        )
        overlay = OnboardingOverlay(window, context, steps)
        overlay.start()
        QApplication.processEvents()
        return window, page, overlay

    def test_arrow_keys_page_through_steps_while_card_button_has_focus(self) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        window, _page, overlay = self._running_overlay()
        try:
            focus = QApplication.focusWidget()
            self.assertTrue(focus is not None and overlay.isAncestorOf(focus))
            QTest.keyClick(focus, Qt.Key.Key_Right)
            self.assertEqual(overlay.current_step_key(), "start")
            QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Left)
            self.assertEqual(overlay.current_step_key(), "welcome")
        finally:
            window.close()
            window.deleteLater()

    def test_tab_does_not_leave_the_tour_card(self) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        window, page, overlay = self._running_overlay()
        try:
            overlay.go_next()
            for _ in range(6):
                QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
                focus = QApplication.focusWidget()
                self.assertTrue(focus is not None and overlay.isAncestorOf(focus))
                self.assertFalse(page.isAncestorOf(focus))
        finally:
            window.close()
            window.deleteLater()

    def test_backdrop_grab_does_not_break_a_click_in_progress(self) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        window, _page, overlay = self._running_overlay()
        try:
            button = overlay._card.next_button
            QTest.mousePress(button, Qt.MouseButton.LeftButton)
            overlay._refresh_blur()
            QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
            self.assertEqual(overlay.current_step_key(), "start")
            self.assertTrue(overlay.isVisible())
        finally:
            window.close()
            window.deleteLater()


class _ParentPage(QWidget):
    """Страница-родитель: открывает вложенную страницу и показывает «меню»."""

    def __init__(self, host, parent=None) -> None:
        super().__init__(parent)
        self._host = host
        layout = QVBoxLayout(self)
        self.row = QPushButton("Строка", self)
        self.second = QPushButton("Вторая", self)
        self.menu = QPushButton("Меню", self)
        layout.addWidget(self.row)
        layout.addWidget(self.second)
        layout.addWidget(self.menu)
        self.menu.hide()
        self.allow_subpage = True
        self.open_calls: list[str] = []
        self.states: list[str | None] = []

    def onboarding_target(self, name):
        if name == "menu":
            return [self.row, self.menu] if self.menu.isVisible() else None
        if name == "pair":
            return [self.row, None, self.second]
        return None

    def onboarding_set_state(self, state) -> None:
        self.states.append(state)
        self.menu.setVisible(state == "menu")

    def onboarding_open_subpage(self, key) -> bool:
        self.open_calls.append(key)
        if not self.allow_subpage:
            return False
        return self._host.show_page(self._host.child_name)


class _ChildPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.editor = QPushButton("Текст пресета", self)
        layout.addWidget(self.editor)

    def onboarding_target(self, name):
        return self.editor if name == "editor" else None


def _build_multi_page_window():
    from app.page_names import PageName

    window = QWidget()
    window.resize(900, 640)
    layout = QVBoxLayout(window)

    class Host:
        child_name = PageName.ZAPRET2_PRESET_RAW_EDITOR

        def __init__(self) -> None:
            self.pages = {}
            self.current = None
            self.shown: list = []

        def show_page(self, name, allow_internal=False):
            _ = allow_internal
            self.shown.append(name)
            for page_name, page in self.pages.items():
                page.setVisible(page_name == name)
            self.current = self.pages.get(name)
            return self.current is not None

        def get_loaded_page(self, name):
            return self.pages.get(name)

        def current_page(self):
            return self.current

    host = Host()
    control = _FakePage(window)
    parent = _ParentPage(host, window)
    child = _ChildPage(window)
    host.pages = {
        PageName.ZAPRET2_MODE_CONTROL: control,
        PageName.ZAPRET2_USER_PRESETS: parent,
        PageName.ZAPRET2_PRESET_RAW_EDITOR: child,
    }
    for page in host.pages.values():
        layout.addWidget(page)
    host.show_page(PageName.ZAPRET2_MODE_CONTROL)
    window.ui_session = SimpleNamespace(
        nav_items={},
        nav_header_by_group={},
        nav_headers=[],
        page_host=host,
    )
    return window, host, control, parent, child


class OnboardingSubpageAndStateTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def _overlay(self, steps):
        from app.page_names import PageName
        from ui.onboarding.overlay import OnboardingOverlay
        from ui.onboarding.steps import TourContext

        window, host, control, parent, child = _build_multi_page_window()
        window.show()
        context = TourContext(
            window=window,
            control_page_name=PageName.ZAPRET2_MODE_CONTROL,
            pages={
                "control": PageName.ZAPRET2_MODE_CONTROL,
                "user_presets": PageName.ZAPRET2_USER_PRESETS,
                "preset_editor": PageName.ZAPRET2_PRESET_RAW_EDITOR,
            },
            current_page=control,
        )
        overlay = OnboardingOverlay(window, context, steps)
        return window, host, parent, child, overlay

    def test_subpage_is_opened_by_its_parent_page_and_not_reopened(self) -> None:
        from ui.onboarding.steps import TourStep, _page_target

        steps = (
            TourStep("welcome", hero=True),
            TourStep("file", _page_target("editor"), page="preset_editor"),
            TourStep("file_again", _page_target("editor"), page="preset_editor"),
            TourStep("start", _page_target("start"), page="control"),
        )
        window, host, parent, child, overlay = self._overlay(steps)
        try:
            overlay.start()
            overlay.go_next()
            self.assertEqual(overlay.current_step_key(), "file")
            self.assertEqual(parent.open_calls, ["preset_editor"])
            self.assertIs(host.current, child)
            overlay.go_next()
            # Страница уже открыта — родителя второй раз не дёргаем.
            self.assertEqual(overlay.current_step_key(), "file_again")
            self.assertEqual(parent.open_calls, ["preset_editor"])
            overlay.go_next()
            overlay.go_back()
            # После ухода на другую страницу «Назад» снова открывает через родителя.
            self.assertEqual(overlay.current_step_key(), "file_again")
            self.assertEqual(parent.open_calls, ["preset_editor", "preset_editor"])
            self.assertIs(host.current, child)
        finally:
            window.close()
            window.deleteLater()

    def test_subpage_step_is_skipped_when_parent_cannot_open_it(self) -> None:
        from ui.onboarding.steps import TourStep, _page_target

        steps = (
            TourStep("welcome", hero=True),
            TourStep("file", _page_target("editor"), page="preset_editor", target_optional=True),
            TourStep("start", _page_target("start"), page="control"),
        )
        window, _host, parent, _child, overlay = self._overlay(steps)
        parent.allow_subpage = False
        try:
            overlay.start()
            overlay.go_next()
            self.assertEqual(parent.open_calls, ["preset_editor"])
            self.assertEqual(overlay.current_step_key(), "start")
        finally:
            window.close()
            window.deleteLater()

    def test_page_state_is_shown_on_its_step_and_reset_when_leaving(self) -> None:
        from ui.onboarding.steps import TourStep, _page_target

        steps = (
            TourStep("welcome", hero=True),
            TourStep("menu", _page_target("menu"), page="user_presets", page_state="menu"),
            TourStep("start", _page_target("start"), page="control"),
        )
        window, _host, parent, _child, overlay = self._overlay(steps)
        try:
            overlay.start()
            overlay.go_next()
            self.assertEqual(overlay.current_step_key(), "menu")
            self.assertEqual(parent.states, ["menu"])
            self.assertEqual(overlay._targets, [parent.row, parent.menu])
            overlay.go_next()
            self.assertEqual(parent.states, ["menu", None])
            overlay.go_back()
            self.assertEqual(parent.states, ["menu", None, "menu"])
            overlay.finish("skipped", immediate=True)
            self.assertEqual(parent.states, ["menu", None, "menu", None])
            self.assertFalse(parent.menu.isVisible())
        finally:
            window.close()
            window.deleteLater()

    def test_list_target_highlights_every_shown_widget(self) -> None:
        from ui.onboarding.steps import TourContext, _page_target

        window, _host, parent, _child, _overlay = self._overlay(())
        try:
            parent.show()
            context = TourContext(window=window, current_page=parent)
            self.assertEqual(_page_target("pair")(context), [parent.row, parent.second])
        finally:
            window.close()
            window.deleteLater()


class MenuPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def test_real_menu_is_shown_as_picture_inside_page_not_as_window(self) -> None:
        from PyQt6.QtCore import QRect
        from qfluentwidgets import Action, RoundMenu

        from ui.onboarding.menu_preview import create_menu_preview, place_menu_preview, remove_menu_preview

        host = QWidget()
        host.resize(600, 400)
        row = QPushButton("Строка", host)
        row.setGeometry(10, 10, 580, 40)
        host.show()
        try:
            menu = RoundMenu(parent=host)
            menu.addAction(Action("Открыть", menu))
            menu.addAction(Action("Дублировать", menu))
            preview = create_menu_preview(host, menu)
            self.assertIsNotNone(preview)
            self.assertFalse(preview.isWindow())
            self.assertFalse(menu.isVisible())
            place_menu_preview(preview, row, QRect(0, 0, row.width(), row.height()))
            self.assertTrue(preview.isVisible())
            self.assertTrue(host.rect().contains(preview.geometry()))
            self.assertFalse(any(w.isWindow() and w.isVisible() for w in QApplication.topLevelWidgets() if w is not host))
            remove_menu_preview(preview)
            self.assertFalse(preview.isVisible())
        finally:
            host.close()
            host.deleteLater()


class TourStepCatalogTests(unittest.TestCase):
    def test_wiki_links_belong_to_real_steps(self) -> None:
        from config.urls import ONBOARDING_WIKI_URLS
        from ui.onboarding.steps import TOUR_STEPS

        step_keys = {step.key for step in TOUR_STEPS}
        self.assertEqual(set(ONBOARDING_WIKI_URLS) - step_keys, set())
        for step in TOUR_STEPS:
            self.assertEqual(step.wiki_url, ONBOARDING_WIKI_URLS.get(step.key, ""))
            if step.wiki_url:
                self.assertTrue(step.wiki_url.startswith("https://wiki.zapret.moe/"), step.key)

    def test_every_step_has_title_and_body_in_both_languages(self) -> None:
        from app.ui_texts import TEXTS
        from ui.onboarding.steps import TOUR_STEPS

        # Прямо по словарю: tr() подставил бы русский текст вместо пропавшего английского.
        missing = []
        for step in TOUR_STEPS:
            for part in ("title", "body"):
                key = f"onboarding.step.{step.key}.{part}"
                for language in ("ru", "en"):
                    if not str((TEXTS.get(key) or {}).get(language) or "").strip():
                        missing.append(f"{key} [{language}]")
        self.assertEqual(missing, [])


class TechniqueIllustrationTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def _illustration(self, host):
        from ui.onboarding.illustrations import TechniqueIllustration

        illustration = TechniqueIllustration(host, tr_fn=lambda _key, default: default)
        illustration.resize(532, illustration.height())
        return illustration

    def test_every_scene_draws_something_at_every_moment(self) -> None:
        from ui.onboarding.illustrations import SCENES

        host = QWidget()
        try:
            illustration = self._illustration(host)
            for key in SCENES:
                illustration.set_scene(key)
                for phase in (0.05, 0.3, 0.6, 0.86):
                    illustration.set_phase(phase)
                    image = illustration.grab().toImage()
                    colors = {image.pixel(x, y) for x in range(0, image.width(), 7) for y in range(0, image.height(), 7)}
                    self.assertGreater(len(colors), 3, f"{key} @ {phase}")
        finally:
            host.deleteLater()

    def test_every_illustrated_step_uses_a_known_scene(self) -> None:
        from ui.onboarding.illustrations import SCENES
        from ui.onboarding.steps import TOUR_STEPS

        illustrated = [step for step in TOUR_STEPS if step.illustration]
        self.assertGreaterEqual(len(illustrated), 9)
        for step in illustrated:
            self.assertIn(step.illustration, SCENES, step.key)

    def test_animation_runs_only_while_visible(self) -> None:
        host = QWidget()
        host.resize(600, 300)
        host.show()
        try:
            illustration = self._illustration(host)
            illustration.set_scene("fake")
            illustration.show()
            with patch("ui.onboarding.illustrations.are_live_animations_enabled", return_value=True):
                illustration.set_scene("multisplit")
            self.assertTrue(illustration.is_animating())
            illustration.hide()
            self.assertFalse(illustration.is_animating())
        finally:
            host.close()
            host.deleteLater()

    def test_without_animations_shows_still_frame_with_result(self) -> None:
        from ui.onboarding.illustrations import STATIC_PHASE

        host = QWidget()
        host.resize(600, 300)
        host.show()
        try:
            illustration = self._illustration(host)
            illustration.show()
            with patch("ui.onboarding.illustrations.are_live_animations_enabled", return_value=False):
                illustration.set_scene("fake")
            self.assertFalse(illustration.is_animating())
            self.assertEqual(illustration.phase(), STATIC_PHASE)
        finally:
            host.close()
            host.deleteLater()

    def test_card_shows_illustration_only_on_its_steps(self) -> None:
        from app.page_names import PageName
        from ui.onboarding.overlay import CARD_WIDTH, HERO_CARD_WIDTH, OnboardingOverlay
        from ui.onboarding.steps import TourContext, TourStep, _page_target

        window, page = _build_window()
        window.resize(900, 760)
        window.show()
        context = TourContext(
            window=window,
            control_page_name=PageName.ZAPRET2_MODE_CONTROL,
            pages={"control": PageName.ZAPRET2_MODE_CONTROL},
            current_page=page,
        )
        steps = (
            TourStep("start", _page_target("start"), page="control"),
            TourStep("technique_fake", illustration="fake"),
        )
        overlay = OnboardingOverlay(window, context, steps)
        try:
            overlay.start()
            illustration = overlay._card.illustration
            self.assertTrue(illustration.isHidden())
            self.assertEqual(overlay.card_target_size().width(), CARD_WIDTH)
            overlay.go_next()
            self.assertFalse(illustration.isHidden())
            self.assertEqual(illustration.scene_key(), "fake")
            self.assertEqual(overlay.card_target_size().width(), HERO_CARD_WIDTH)
            overlay.go_back()
            self.assertTrue(illustration.isHidden())
        finally:
            window.close()
            window.deleteLater()


class _FocusGrabbingPage(QWidget):
    """Как редактор пресета: после открытия сам забирает фокус таймером."""

    def __init__(self, parent=None) -> None:
        from PyQt6.QtWidgets import QPlainTextEdit

        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.editor = QPlainTextEdit(self)
        self.editor.setPlainText("--lua-desync=fake")
        layout.addWidget(self.editor)
        self.values = {"count": "7"}

    def onboarding_target(self, name):
        return self.editor if name == "editor" else None

    def onboarding_text_values(self, _name):
        return dict(self.values)

    def grab_focus_later(self) -> None:
        from PyQt6.QtCore import QTimer

        QTimer.singleShot(0, self.editor.setFocus)


class OnboardingCardBehaviourTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def _overlay(self, steps, page_cls=_FocusGrabbingPage):
        from PyQt6.QtTest import QTest

        from app.page_names import PageName
        from ui.onboarding.overlay import OnboardingOverlay
        from ui.onboarding.steps import TourContext

        window = QWidget()
        window.resize(900, 700)
        layout = QVBoxLayout(window)
        page = page_cls(window)
        layout.addWidget(page)

        class Host:
            def show_page(self, _name, allow_internal=False):
                return True

            def get_loaded_page(self, _name):
                return page

            def current_page(self):
                return page

        window.ui_session = SimpleNamespace(nav_items={}, nav_header_by_group={}, nav_headers=[], page_host=Host())
        window.show()
        window.activateWindow()
        QTest.qWaitForWindowActive(window, 1000)
        context = TourContext(
            window=window,
            control_page_name=PageName.ZAPRET2_MODE_CONTROL,
            pages={"control": PageName.ZAPRET2_MODE_CONTROL},
            current_page=page,
        )
        overlay = OnboardingOverlay(window, context, steps)
        return window, page, overlay

    def test_arrows_keep_working_when_page_takes_focus(self) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        from ui.onboarding.steps import TourStep, _page_target

        steps = (
            TourStep("welcome", hero=True),
            TourStep("editor", _page_target("editor"), page="control"),
            TourStep("finish", hero=True),
        )
        window, page, overlay = self._overlay(steps)
        try:
            overlay.start()
            overlay.go_next()
            page.grab_focus_later()
            for _ in range(5):
                QApplication.processEvents()
            focus = QApplication.focusWidget()
            self.assertTrue(focus is not None and overlay.isAncestorOf(focus))
            QTest.keyClick(focus, Qt.Key.Key_A)
            self.assertEqual(page.editor.toPlainText(), "--lua-desync=fake")
            QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Right)
            self.assertEqual(overlay.current_step_key(), "finish")
        finally:
            overlay.finish("skipped", immediate=True)
            window.close()
            window.deleteLater()

    def test_many_dots_fit_the_card_and_a_click_opens_that_step(self) -> None:
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtTest import QTest

        from ui.onboarding.steps import TourStep

        steps = tuple(TourStep(f"step_{index}", hero=True) for index in range(60))
        window, _page, overlay = self._overlay(steps)
        try:
            overlay.start()
            for _ in range(30):
                QApplication.processEvents()
            dots = overlay._card.dots
            rects = dots.dot_rects()
            self.assertEqual(len(rects), 60)
            self.assertLessEqual(rects[-1].right(), dots.width() + 0.5)
            target = rects[41].center().toPoint()
            QTest.mouseClick(dots, Qt.MouseButton.LeftButton, pos=QPoint(target.x(), target.y()))
            self.assertEqual(overlay.current_step_key(), "step_41")
        finally:
            overlay.finish("skipped", immediate=True)
            window.close()
            window.deleteLater()

    def test_opacity_effect_is_off_once_text_is_fully_shown(self) -> None:
        import time

        from ui.onboarding.steps import TourStep

        window, _page, overlay = self._overlay((TourStep("welcome", hero=True), TourStep("next", hero=True)))
        try:
            overlay.start()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and overlay._card.content_effect.isEnabled():
                QApplication.processEvents()
                time.sleep(0.01)
            self.assertFalse(overlay._card.content_effect.isEnabled())
        finally:
            overlay.finish("skipped", immediate=True)
            window.close()
            window.deleteLater()

    def test_live_values_fill_the_text_and_missing_ones_become_a_dash(self) -> None:
        from ui.onboarding.overlay import _fill_placeholders, _unbreakable_options
        from ui.onboarding.steps import TourStep, _page_target

        self.assertEqual(_fill_placeholders("{count} и {example}", {"count": "3"}), "3 и —")
        self.assertEqual(_unbreakable_options("Строка --lua-desync тут"), "Строка \u2011\u2011lua\u2011desync тут")

        steps = (TourStep("preset_blobs", _page_target("editor"), page="control", text_key="section:blobs"),)
        window, page, overlay = self._overlay(steps)
        page.values = {"count": "42", "example": "tls_vk"}
        try:
            overlay.start()
            body = overlay._card.body_label.text()
            self.assertIn("42", body)
            self.assertIn("tls_vk", body)
            self.assertNotIn("{", body)
        finally:
            overlay.finish("skipped", immediate=True)
            window.close()
            window.deleteLater()


class OnboardingDecorTests(unittest.TestCase):
    """Полоска прогресса сверху и перетекающие точки."""

    def setUp(self) -> None:
        _app()

    def _overlay(self, count: int, *, animated: bool):
        from app.page_names import PageName
        from ui.onboarding import overlay as overlay_module
        from ui.onboarding.steps import TourContext, TourStep

        window, page = _build_window()
        window.show()
        context = TourContext(
            window=window,
            control_page_name=PageName.ZAPRET2_MODE_CONTROL,
            pages={"control": PageName.ZAPRET2_MODE_CONTROL},
            current_page=page,
        )
        steps = tuple(TourStep(f"step_{index}", hero=True) for index in range(count))
        with patch.object(overlay_module, "are_live_animations_enabled", return_value=animated):
            overlay = overlay_module.OnboardingOverlay(window, context, steps)
        overlay.start()
        return window, overlay

    @staticmethod
    def _run(overlay, seconds: float) -> None:
        import time

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.005)

    def test_progress_glides_to_the_new_share_and_back(self) -> None:
        window, overlay = self._overlay(4, animated=True)
        card = overlay._card
        try:
            self._run(overlay, 1.6)
            self.assertAlmostEqual(card.progress(), 0.25, places=3)
            overlay.go_next()
            overlay.go_next()
            self._run(overlay, 0.05)
            self.assertGreater(card.progress(), 0.25)
            self.assertLess(card.progress(), 0.75)
            self._run(overlay, 1.6)
            self.assertAlmostEqual(card.progress(), 0.75, places=3)
            overlay.go_back()
            self._run(overlay, 1.6)
            self.assertAlmostEqual(card.progress(), 0.5, places=3)
        finally:
            overlay.finish("skipped", immediate=True)
            window.close()
            window.deleteLater()

    def test_without_animations_everything_is_in_place_at_once(self) -> None:
        window, overlay = self._overlay(5, animated=False)
        try:
            overlay.go_to(3)
            self.assertAlmostEqual(overlay._card.progress(), 0.8, places=6)
            overlay._on_frame()
            self.assertEqual(overlay._card.dots.position(), 3.0)
        finally:
            overlay.finish("skipped", immediate=True)
            window.close()
            window.deleteLater()

    def test_pill_flows_to_a_far_step_and_clicks_hit_what_is_visible(self) -> None:
        window, overlay = self._overlay(12, animated=True)
        dots = overlay._card.dots
        try:
            self._run(overlay, 0.5)
            overlay.go_to(9)
            self._run(overlay, 0.04)
            self.assertGreater(dots.position(), 0.0)
            self.assertLess(dots.position(), 9.0)
            # Во время движения точки сдвинуты: нажатие по видимой точке 4 ведёт на шаг 4.
            rect = dots.dot_rects()[4]
            self.assertEqual(dots.index_at(rect.center().x()), 4)
            self._run(overlay, 1.2)
            self.assertEqual(dots.position(), 9.0)
        finally:
            overlay.finish("skipped", immediate=True)
            window.close()
            window.deleteLater()


class StartOnboardingTourTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def test_automatic_start_waits_for_visible_window(self) -> None:
        from ui.onboarding import start_onboarding_tour

        window, _page = _build_window()
        try:
            self.assertFalse(start_onboarding_tour(window, automatic=True))
        finally:
            window.deleteLater()

    def test_manual_replay_starts_and_second_call_reuses_running_tour(self) -> None:
        from ui.onboarding import find_onboarding_overlay, start_onboarding_tour

        window, _page = _build_window()
        window.show()
        try:
            self.assertTrue(start_onboarding_tour(window))
            overlay = find_onboarding_overlay(window)
            self.assertIsNotNone(overlay)
            self.assertTrue(start_onboarding_tour(window))
            self.assertIs(find_onboarding_overlay(window), overlay)
        finally:
            window.close()
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
