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

from PyQt6.QtCore import QPoint, QRect  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton, QScrollArea, QVBoxLayout, QWidget  # noqa: E402


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

    def test_tile_counter_step_stands_between_profiles_overview_and_profile_row(self) -> None:
        from app.ui_texts import TEXTS as UI_TEXTS
        from ui.onboarding.steps import TOUR_STEPS

        keys = [step.key for step in TOUR_STEPS]
        position = keys.index("profile_group")
        self.assertEqual(keys[position - 1 : position + 2], ["profiles_list", "profile_group", "profile_row"])
        self.assertEqual(TOUR_STEPS[position].page, "preset_setup")

        # Главная мысль для новичка: включать все профили не нужно.
        overview = UI_TEXTS["onboarding.step.profiles_list.body"]["ru"]
        counter = UI_TEXTS["onboarding.step.profile_group.body"]["ru"]
        row = UI_TEXTS["onboarding.step.profile_row.body"]["ru"]
        self.assertIn("Включать все профили подряд не надо", overview)
        self.assertIn("«3 из 5»", counter)
        self.assertIn("включать их все обычно бесполезно", counter)
        self.assertIn("Наведите мышь", counter)
        self.assertIn("закрашенная — профиль включён", row)
        self.assertNotIn("Hostlist или IPset", row)

    def test_step_keys_are_unique_and_pages_exist_in_zapret2_mode(self) -> None:
        from app.page_names import PageName
        from ui.onboarding.steps import COMMON_TOUR_PAGES, MODE_TOUR_PAGES, TOUR_STEPS, TOUR_SUBPAGE_PARENTS

        keys = [step.key for step in TOUR_STEPS]
        self.assertEqual(len(keys), len(set(keys)))
        pages = {**COMMON_TOUR_PAGES, **MODE_TOUR_PAGES[PageName.ZAPRET2_MODE_CONTROL]}
        self.assertEqual(sorted({step.page for step in TOUR_STEPS if step.page} - set(pages)), [])
        for child, parent in TOUR_SUBPAGE_PARENTS.items():
            self.assertIn(child, pages)
            self.assertIn(parent, pages)
        # Общие страницы есть в каждом режиме, а фейки и разбор лога — только у winws2.
        for mode_pages in MODE_TOUR_PAGES.values():
            self.assertEqual(set(mode_pages) & set(COMMON_TOUR_PAGES), set())
        self.assertNotIn("fakes_page", MODE_TOUR_PAGES[PageName.ZAPRET1_MODE_CONTROL])
        self.assertNotIn("log_analyzer", MODE_TOUR_PAGES[PageName.ZAPRET1_MODE_CONTROL])

    def test_every_step_belongs_to_a_named_chapter_and_chapters_do_not_mix(self) -> None:
        from app.ui_texts import TEXTS
        from ui.onboarding.steps import TOUR_STEPS

        order: list[str] = []
        for step in TOUR_STEPS:
            self.assertTrue(step.chapter, step.key)
            if not order or order[-1] != step.chapter:
                order.append(step.chapter)
        # Глава идёт одним куском: вернуться к уже пройденной нельзя.
        self.assertEqual(len(order), len(set(order)))
        self.assertEqual(order[0], "intro")
        self.assertEqual(order[-1], "finish")
        for chapter in order:
            for language in ("ru", "en"):
                self.assertTrue(str((TEXTS.get(f"onboarding.chapter.{chapter}") or {}).get(language) or "").strip(), chapter)

    def test_tour_visits_the_pages_added_after_presets_and_profiles(self) -> None:
        from ui.onboarding.steps import TOUR_STEPS

        pages = {step.page for step in TOUR_STEPS}
        for page in (
            "fakes_page",
            "dpi_settings",
            "dns",
            "custom_dns",
            "hosts",
            "hosts_file",
            "telegram",
            "telegram_advanced",
            "blockcheck",
            "log_analyzer",
            "appearance",
            "logs",
            "about",
            "updates",
        ):
            self.assertIn(page, pages)

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

    def test_still_frame_shows_settled_scene(self) -> None:
        # Без анимаций виден один кадр STATIC_PHASE: к нему все переходы
        # (реплика, вспышка у сайта, падение подделок и мусора) уже закончились.
        from ui.onboarding.illustrations import FADE_FROM, SCENES, STATIC_PHASE

        host = QWidget()
        try:
            illustration = self._illustration(host)
            for width in (460, 532, 580):
                illustration.resize(width, illustration.height())
                for key in SCENES:
                    illustration.set_scene(key)
                    self.assertLessEqual(illustration.scene_times().settled, STATIC_PHASE, f"{key} @ {width}")
                    illustration.set_phase(STATIC_PHASE)
                    still = illustration.grab().toImage()
                    illustration.set_phase(FADE_FROM - 0.001)
                    self.assertEqual(still, illustration.grab().toImage(), f"{key} @ {width}")
        finally:
            host.deleteLater()

    def test_tcpseg_junk_leads_one_packet_and_site_drops_it(self) -> None:
        # seqovl: мусор приклеен в начало того же пакета. На схеме правее —
        # значит раньше, поэтому мусор едет справа от данных, вплотную к ним,
        # первым входит в проверку, а отбрасывает его уже сайт.
        from ui.onboarding.illustrations import DISCARD, TRAVEL

        host = QWidget()
        try:
            illustration = self._illustration(host)
            illustration.set_scene("tcpseg")
            times = illustration.scene_times()
            junk_arrival = times.starts[0] + TRAVEL
            data_arrival = times.starts[1] + TRAVEL
            self.assertLess(junk_arrival, data_arrival)

            # У проверки: реплика звучит, когда в середине проверки мусор, а данные ещё не дошли.
            frames = {frame.index: frame for frame in illustration.chip_frames(times.verdict)}
            junk, data = frames[0], frames[1]
            self.assertAlmostEqual(junk.x, illustration.width() / 2, delta=1.0)
            self.assertLess(data.x, junk.x)
            # Один пакет: половины слиты без просвета, не как отдельные пакеты.
            self.assertTrue(junk.glued)
            self.assertEqual((junk.flat, data.flat), ("left", "right"))
            self.assertAlmostEqual(junk.x - data.x, (junk.width + data.width) / 2)

            # После проверки мусор не гаснет по дороге, как подделка.
            frames = {frame.index: frame for frame in illustration.chip_frames(junk_arrival - TRAVEL * 0.1)}
            self.assertAlmostEqual(frames[0].alpha, 1.0)
            self.assertAlmostEqual(frames[0].dy, 0.0)

            # У сайта: мусор падает с дорожки, данные входят следом.
            frames = {frame.index: frame for frame in illustration.chip_frames(junk_arrival + DISCARD * 0.5)}
            self.assertGreater(frames[0].dy, 0.0)
            self.assertFalse(frames[0].glued)
            self.assertEqual(frames[1].flat, "")
            self.assertEqual(illustration.chip_frames(max(junk_arrival + DISCARD, data_arrival) + 0.01), [])
        finally:
            host.deleteLater()

    def test_separate_packets_keep_a_gap(self) -> None:
        # Просвет на схеме = отдельные пакеты: подделка в fake не слита с настоящим.
        from ui.onboarding.illustrations import CHIP_GAP

        host = QWidget()
        try:
            illustration = self._illustration(host)
            illustration.set_scene("fake")
            times = illustration.scene_times()
            frames = {frame.index: frame for frame in illustration.chip_frames(times.starts[1] + 0.1)}
            fake, real = frames[0], frames[1]
            self.assertGreaterEqual(fake.x - real.x - (fake.width + real.width) / 2, CHIP_GAP)
            self.assertEqual((fake.flat, real.flat, fake.glued), ("", "", False))
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

    def test_pause_button_freezes_frame_and_resumes_from_it(self) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        from ui.onboarding import illustrations

        host = QWidget()
        host.resize(600, 300)
        host.show()
        try:
            illustration = self._illustration(host)
            illustration.show()
            with patch("ui.onboarding.illustrations.are_live_animations_enabled", return_value=True):
                illustration.set_scene("tcpseg")
                button = illustration.pause_button
                self.assertTrue(button.isVisible())
                self.assertEqual(button.geometry().topRight(), illustration.rect().topRight())

                illustration.set_phase(0.3)
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                self.assertTrue(illustration.is_paused())
                self.assertFalse(illustration.is_animating())
                self.assertEqual(illustration.phase(), 0.3)
                self.assertEqual(button.toolTip(), "Продолжить анимацию")

                # Продолжение идёт с того же кадра, а не с начала круга.
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                self.assertFalse(illustration.is_paused())
                self.assertTrue(illustration.is_animating())
                self.assertEqual(button.toolTip(), "Остановить анимацию")
                with patch.object(illustration._clock, "elapsed", return_value=int(0.1 * illustrations.PERIOD_MS)):
                    illustration._on_tick()
                self.assertAlmostEqual(illustration.phase(), 0.4, places=3)

                # Следующий шаг: новая схема идёт сама, с начала.
                illustration.set_paused(True)
                illustration.set_scene("fake")
                self.assertFalse(illustration.is_paused())
                self.assertTrue(illustration.is_animating())
                self.assertEqual(illustration.phase(), 0.0)
        finally:
            host.close()
            host.deleteLater()

    def test_pause_button_hidden_when_animations_are_off(self) -> None:
        host = QWidget()
        host.resize(600, 300)
        host.show()
        try:
            illustration = self._illustration(host)
            illustration.show()
            with patch("ui.onboarding.illustrations.are_live_animations_enabled", return_value=False):
                illustration.set_scene("fake")
                self.assertFalse(illustration.pause_button.isVisible())
                illustration.set_paused(True)
                self.assertFalse(illustration.is_paused())
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

    def test_click_on_a_chapter_segment_opens_its_first_step(self) -> None:
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtTest import QTest

        from ui.onboarding.steps import TourStep

        # Шесть глав по десять шагов: отрезков шесть, а не шестьдесят точек.
        steps = tuple(TourStep(f"step_{index}", hero=True, chapter=f"chapter_{index // 10}") for index in range(60))
        window, _page, overlay = self._overlay(steps)
        try:
            overlay.start()
            for _ in range(30):
                QApplication.processEvents()
            bar = overlay._card.chapter_bar
            rects = bar.segment_rects()
            self.assertEqual(len(rects), 6)
            self.assertLessEqual(rects[-1].right(), bar.width() + 0.5)
            # Нажимается вся высота строки, а не тонкая полоска.
            self.assertGreaterEqual(min(rect.width() for rect in rects), 40)
            self.assertEqual(rects[0].height(), bar.height())
            QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(int(rects[4].center().x()), 2))
            self.assertEqual(overlay.current_step_key(), "step_40")
            # Щелчок в промежуток между отрезками тоже куда-то ведёт: мимо не нажать.
            between = int((rects[1].right() + rects[2].left()) / 2)
            QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(between, bar.height() - 2))
            self.assertIn(overlay.current_step_key(), ("step_10", "step_20"))
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


class _TallGridPage(QScrollArea):
    """Прокручиваемая страница с одной высокой «сеткой»: цель — прямоугольник внутри неё."""

    GRID_HEIGHT = 3000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        content = QWidget(self)
        layout = QVBoxLayout(content)
        self.panel = QPushButton("Панель над сеткой", content)
        self.panel.setFixedHeight(60)
        layout.addWidget(self.panel)
        self.grid = QWidget(content)
        self.grid.setFixedHeight(self.GRID_HEIGHT)
        layout.addWidget(self.grid)
        self.late_button = QPushButton("Появится позже", content)
        self.late_button.hide()
        layout.addWidget(self.late_button)
        self.setWidget(content)
        self.tile_rect = QRect(20, 2400, 300, 90)

    def onboarding_target(self, name):
        if name == "tile":
            return (self.grid, self.tile_rect)
        if name == "late":
            return self.late_button
        if name == "panel":
            return self.panel
        if name == "grid":
            return self.grid
        return None


class OnboardingChapterAndScrollTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def _overlay(self, steps, page_cls=_FakePage):
        from app.page_names import PageName
        from ui.onboarding import overlay as overlay_module
        from ui.onboarding.steps import TourContext

        window = QWidget()
        self.addCleanup(window.deleteLater)
        window.resize(900, 640)
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
        QApplication.processEvents()
        context = TourContext(
            window=window,
            control_page_name=PageName.ZAPRET2_MODE_CONTROL,
            pages={"control": PageName.ZAPRET2_MODE_CONTROL},
            current_page=page,
        )
        with patch.object(overlay_module, "are_live_animations_enabled", return_value=False):
            overlay = overlay_module.OnboardingOverlay(window, context, steps)
        self.addCleanup(lambda: overlay.finish("skipped", immediate=True))
        return window, page, overlay

    def test_counter_and_segment_hint_name_the_chapter(self) -> None:
        from ui.onboarding.steps import TourStep, _page_target

        steps = (
            TourStep("start", _page_target("start"), page="control", chapter="control"),
            TourStep("status", _page_target("start"), page="control", chapter="control"),
            TourStep("preset", _page_target("start"), page="control"),
        )
        _window, _page, overlay = self._overlay(steps)
        overlay.start()

        self.assertEqual(overlay._card.counter_label.text(), "Главная страница · Шаг 1 из 3")
        first, second = overlay._card.chapter_bar.chapters()
        self.assertEqual((first.first, first.count, first.hint), (0, 2, "Главная страница · шагов: 2"))
        # Шаги без главы — отдельный отрезок без подсказки, счётчик у них как раньше.
        self.assertEqual((second.first, second.count, second.hint), (2, 1, ""))
        overlay.go_to(2)
        self.assertEqual(overlay._card.counter_label.text(), "Шаг 3 из 3")

    def test_chapters_of_the_whole_tour_stay_easy_to_click_in_a_narrow_card(self) -> None:
        from ui.onboarding.overlay import TourChapter, _ChapterBar
        from ui.onboarding.steps import TOUR_STEPS

        chapters: list[TourChapter] = []
        for index, step in enumerate(TOUR_STEPS):
            if chapters and chapters[-1].title == step.chapter:
                chapters[-1] = TourChapter(step.chapter, chapters[-1].first, chapters[-1].count + 1)
            else:
                chapters.append(TourChapter(step.chapter, index, 1))
        self.assertGreater(len(TOUR_STEPS), 90)
        self.assertLessEqual(len(chapters), 12)
        bar = _ChapterBar()
        self.addCleanup(bar.deleteLater)
        bar.set_chapters(chapters)

        # 212 точек — строка в самой узкой карточке (окно шириной под 300).
        for width in (212, 452):
            bar.resize(width, bar.HEIGHT)
            rects = bar.segment_rects()
            self.assertLessEqual(rects[-1].right(), width + 0.5)
            self.assertGreaterEqual(min(rect.width() for rect in rects), 12, width)
            # Глава из одного шага («Финиш») нажимается так же, как длинная.
            self.assertAlmostEqual(rects[-1].width(), rects[0].width(), places=3)
            self.assertEqual(bar.chapter_at(rects[-1].center().x()), len(chapters) - 1)

        # Пройденные главы закрашены целиком, текущая — по доле шагов, дальние пустые.
        diagnostics = next(index for index, chapter in enumerate(chapters) if chapter.title == "diagnostics")
        bar.set_step(chapters[diagnostics].first)
        bar.advance(16.0, animated=False)
        self.assertEqual(bar.current_chapter(), diagnostics)
        self.assertEqual(bar.fill_share(diagnostics - 1), 1.0)
        self.assertAlmostEqual(bar.fill_share(diagnostics), 1 / chapters[diagnostics].count, places=6)
        self.assertEqual(bar.fill_share(diagnostics + 1), 0.0)

    def test_tile_deep_in_a_tall_grid_is_scrolled_into_view(self) -> None:
        from ui.onboarding.steps import TourStep, _page_target

        steps = (TourStep("tile", _page_target("tile"), page="control"),)
        window, page, overlay = self._overlay(steps, _TallGridPage)
        overlay.start()
        overlay._on_frame()

        tile_top = page.grid.mapTo(window, page.tile_rect.topLeft()).y()
        self.assertGreater(tile_top, 0)
        self.assertLess(tile_top + page.tile_rect.height(), window.height())
        self.assertIsNotNone(overlay._hole)

    def test_target_that_slid_under_the_edge_is_brought_back_whole(self) -> None:
        """Страница сама прокрутилась после входа в шаг — от панели осталась полоска."""
        from ui.onboarding.overlay import HOLE_PADDING
        from ui.onboarding.steps import TourStep, _page_target

        steps = (TourStep("panel", _page_target("panel"), page="control"),)
        _window, page, overlay = self._overlay(steps, _TallGridPage)
        overlay.start()
        overlay._on_frame()
        whole = page.panel.height() + 2 * HOLE_PADDING
        self.assertAlmostEqual(overlay._hole.height(), whole, delta=1)

        page.verticalScrollBar().setValue(page.panel.y() + page.panel.height() - 6)
        self.assertLess(page.panel.visibleRegion().boundingRect().height(), 10)
        overlay._on_frame()

        self.assertEqual(page.panel.visibleRegion().boundingRect().height(), page.panel.height())
        self.assertAlmostEqual(overlay._hole.height(), whole, delta=1)

    def test_target_taller_than_the_page_is_shown_from_its_top_and_lit_only_where_seen(self) -> None:
        from ui.onboarding.overlay import HOLE_PADDING
        from ui.onboarding.steps import TourStep, _page_target

        steps = (TourStep("grid", _page_target("grid"), page="control"),)
        window, page, overlay = self._overlay(steps, _TallGridPage)
        # Под страницей — полоса окна, которая к ней не относится.
        footer = QPushButton("Низ окна", window)
        footer.setFixedHeight(200)
        window.layout().addWidget(footer)
        QApplication.processEvents()
        overlay.start()
        overlay._on_frame()

        # Qt сам поставил бы в центр середину сетки, и её начало уехало бы за край.
        top = page.grid.mapTo(page.viewport(), QPoint(0, 0)).y()
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(top, 16)
        # Окошко — только видимая часть сетки: на полосу под страницей оно не заходит.
        page_bottom = page.viewport().mapTo(window, QPoint(0, page.viewport().height())).y() - overlay.y()
        self.assertLessEqual(overlay._hole.bottom(), page_bottom + HOLE_PADDING + 1)
        self.assertLess(overlay._hole.bottom(), footer.y() - overlay.y() + footer.height() / 2)

    def test_target_that_appears_later_below_the_window_is_scrolled_to(self) -> None:
        from ui.onboarding.steps import TourStep, _page_target

        steps = (TourStep("late", _page_target("late"), page="control", target_optional=True),)
        _window, page, overlay = self._overlay(steps, _TallGridPage)
        overlay.start()
        overlay._on_frame()
        self.assertIsNone(overlay._hole)

        page.late_button.show()
        QApplication.processEvents()
        overlay._on_frame()

        self.assertIsNotNone(overlay._hole)
        self.assertGreater(page.verticalScrollBar().value(), 0)


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
            self.assertEqual(overlay._card.chapter_bar.progress(), 4.0)
        finally:
            overlay.finish("skipped", immediate=True)
            window.close()
            window.deleteLater()

    def test_chapter_fill_flows_to_a_far_step(self) -> None:
        window, overlay = self._overlay(12, animated=True)
        bar = overlay._card.chapter_bar
        try:
            self._run(overlay, 0.5)
            self.assertEqual(bar.progress(), 1.0)
            overlay.go_to(9)
            self._run(overlay, 0.04)
            # Заливка дотекает до нового шага, а не прыгает.
            self.assertGreater(bar.progress(), 1.0)
            self.assertLess(bar.progress(), 10.0)
            self._run(overlay, 1.5)
            self.assertEqual(bar.progress(), 10.0)
            # Тур без глав — один отрезок на всю ширину, как обычная полоса хода.
            self.assertEqual(len(bar.segment_rects()), 1)
            self.assertAlmostEqual(bar.fill_share(0), 10 / 12, places=6)
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
