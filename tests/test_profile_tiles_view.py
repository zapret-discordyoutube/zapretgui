from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QStyleOptionViewItem


def _item(
    name: str,
    *,
    key: str,
    group: str,
    group_name: str,
    order: int = 0,
    in_preset: bool = True,
    enabled: bool = True,
    match_lines: tuple[str, ...] = ("--filter-tcp=443",),
):
    from profile.state import ProfileListItem

    return ProfileListItem(
        key=key,
        persistent_key=key,
        profile_index=order,
        display_name=name,
        enabled=enabled,
        in_preset=in_preset,
        strategy_id="pass" if in_preset else "none",
        strategy_name="pass" if in_preset else "Не добавлен",
        match_lines=match_lines,
        list_type="",
        rating="",
        favorite=False,
        group=group,
        group_name=group_name,
        order=order,
        profile_name=name,
    )


def _two_groups():
    youtube = tuple(
        _item(
            name,
            key=f"yt-{position}",
            group="youtube",
            group_name="YouTube",
            order=position,
            in_preset=in_preset,
        )
        for position, (name, in_preset) in enumerate(
            (
                ("googlevideo.com", True),
                ("youtube.com (интерфейс)", True),
                ("youtube.com (RTMPS)", False),
                ("youtube.com (QUIC)", True),
                ("i.ytimg.com", False),
            )
        )
    )
    discord = (
        _item("discord.com", key="dc-0", group="discord", group_name="Discord", order=0),
        _item(
            "Голосовые звонки/чаты",
            key="dc-1",
            group="discord",
            group_name="Discord",
            order=1,
            match_lines=("--filter-l7=stun,discord",),
        ),
    )
    return youtube + discord


def _view_state(items, **options):
    from profile.list_view_state import build_profile_list_view_state

    return build_profile_list_view_state(
        tuple(items),
        active_profile_types={"all"},
        search_query="",
        group_expanded={},
        **options,
    )


def _rows_by_kind(state, kind: str, group: str):
    return [row for row in state.rows if row["kind"] == kind and row["group"] == group]


class ProfileTileRowsTests(unittest.TestCase):
    def test_folder_row_counts_working_profiles_in_row_order(self) -> None:
        state = _view_state(_two_groups())
        folder = _rows_by_kind(state, "folder", "youtube")[0]

        self.assertEqual(folder["count"], 5)
        self.assertEqual(folder["active_count"], 3)
        self.assertEqual(folder["active_flags"], (True, True, False, True, False))

    def test_profile_disabled_by_skip_is_not_counted_as_working(self) -> None:
        items = (
            _item("youtube.com", key="yt-0", group="youtube", group_name="YouTube", order=0, enabled=False),
            _item("googlevideo.com", key="yt-1", group="youtube", group_name="YouTube", order=1),
        )
        folder = _rows_by_kind(_view_state(items), "folder", "youtube")[0]

        self.assertEqual(folder["active_count"], 1)
        self.assertEqual(folder["active_flags"], (False, True))

    def test_group_of_one_site_moves_its_icon_to_the_header(self) -> None:
        state = _view_state(_two_groups())
        folder = _rows_by_kind(state, "folder", "youtube")[0]
        profiles = _rows_by_kind(state, "profile", "youtube")

        self.assertTrue(folder["icon_name"].startswith("simple:youtube"))
        self.assertTrue(folder["icon_color"])
        self.assertTrue(all(row["icon_in_header"] for row in profiles))

    def test_main_icon_goes_to_header_and_odd_profile_keeps_its_own(self) -> None:
        # В группе Discord три профиля Discord и vencord со своим значком.
        items = tuple(
            _item(name, key=f"dc-{position}", group="discord", group_name="Discord", order=position)
            for position, name in enumerate(("discord.com", "discord.media", "updates.discord.com", "vencord.dev"))
        )
        state = _view_state(items)
        folder = _rows_by_kind(state, "folder", "discord")[0]
        profiles = _rows_by_kind(state, "profile", "discord")

        self.assertTrue(folder["icon_name"].startswith("simple:discord"))
        self.assertEqual([row["icon_in_header"] for row in profiles], [True, True, True, False])
        self.assertTrue(profiles[3]["icon_name"].startswith("simple:vencord"))

    def test_service_icon_never_outvotes_the_site_icon(self) -> None:
        # Один профиль сайта и два «голосовых» с микрофоном: в шапке всё равно
        # значок сайта, у строки сайта — точка, микрофон остаётся у своих строк.
        state = _view_state(_two_groups() + (
            _item(
                "Звонки",
                key="dc-2",
                group="discord",
                group_name="Discord",
                order=2,
                match_lines=("--filter-l7=stun",),
            ),
        ))
        folder = _rows_by_kind(state, "folder", "discord")[0]
        profiles = _rows_by_kind(state, "profile", "discord")

        self.assertTrue(folder["icon_name"].startswith("simple:discord"))
        self.assertEqual([row["icon_in_header"] for row in profiles], [True, False, False])
        self.assertEqual(profiles[1]["icon_name"], "fa5s.microphone")

    def test_group_of_different_sites_keeps_icons_on_its_rows(self) -> None:
        items = (
            _item("telegram.org", key="m-0", group="messengers", group_name="Мессенджеры", order=0),
            _item("whatsapp.com", key="m-1", group="messengers", group_name="Мессенджеры", order=1),
        )
        state = _view_state(items)
        folder = _rows_by_kind(state, "folder", "messengers")[0]
        profiles = _rows_by_kind(state, "profile", "messengers")

        self.assertEqual(len({row["icon_name"] for row in profiles}), 2)
        self.assertEqual(folder["icon_name"], "")
        self.assertFalse(any(row["icon_in_header"] for row in profiles))

    def test_one_known_site_among_plain_sites_does_not_name_the_group(self) -> None:
        items = (
            _item("github.com", key="s-0", group="sites", group_name="Сайты", order=0),
            _item("Первый сайт", key="s-1", group="sites", group_name="Сайты", order=1),
            _item("Второй сайт", key="s-2", group="sites", group_name="Сайты", order=2),
        )
        folder = _rows_by_kind(_view_state(items), "folder", "sites")[0]

        self.assertEqual(folder["icon_name"], "")

    def test_initials_icon_is_never_a_group_icon(self) -> None:
        # У обоих профилей значок из одних и тех же первых букв, но сайт он
        # не обозначает — в шапку плитки не выносится.
        items = (
            _item("Сайт первый", key="s-0", group="common", group_name="Общие", order=0),
            _item("Сайт первый", key="s-1", group="common", group_name="Общие", order=1),
        )
        state = _view_state(items)
        folder = _rows_by_kind(state, "folder", "common")[0]
        profiles = _rows_by_kind(state, "profile", "common")

        self.assertEqual(profiles[0]["icon_name"], profiles[1]["icon_name"])
        self.assertTrue(profiles[0]["icon_name"].startswith("profile-initials:"))
        self.assertEqual(folder["icon_name"], "")
        self.assertFalse(any(row["icon_in_header"] for row in profiles))

    def test_screen_reader_hears_the_working_count(self) -> None:
        from profile.ui.profile_list_model import ProfileListModel

        model = ProfileListModel()
        model.apply_view_state(_view_state(_two_groups()))
        folder_row = next(
            row
            for row in range(model.rowCount())
            if model.index(row, 0).data(ProfileListModel.KindRole) == "folder"
            and model.index(row, 0).data(ProfileListModel.GroupRole) == "youtube"
        )
        index = model.index(folder_row, 0)

        self.assertEqual(index.data(ProfileListModel.ActiveCountRole), 3)
        self.assertEqual(index.data(ProfileListModel.ActiveFlagsRole), (True, True, False, True, False))
        self.assertIn("5 профилей, включено 3, развернута", index.data(Qt.ItemDataRole.AccessibleTextRole))


class ProfileTilesViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _build(self, width: int, height: int = 500, items=None):
        from profile.ui.profiles_list import ProfilesList

        widget = ProfilesList()
        self.addCleanup(widget.deleteLater)
        widget.resize(width, height)
        widget.apply_view_state(_view_state(items if items is not None else _two_groups()))
        widget.show()
        self._app.processEvents()
        return widget

    def _row(self, widget, kind: str, group: str, position: int = 0) -> int:
        from profile.ui.profile_list_model import ProfileListModel

        model = widget._model
        rows = [
            row
            for row in range(model.rowCount())
            if model.index(row, 0).data(ProfileListModel.KindRole) == kind
            and model.index(row, 0).data(ProfileListModel.GroupRole) == group
        ]
        return rows[position]

    def test_groups_stand_side_by_side_on_a_wide_page(self) -> None:
        widget = self._build(1100)
        view = widget._view

        self.assertTrue(view.tile_layout_enabled())
        self.assertEqual(view.tile_geometry().column_count, 2)
        first_header = view.visualRect(widget._model.index(self._row(widget, "folder", "discord"), 0))
        second_header = view.visualRect(widget._model.index(self._row(widget, "folder", "youtube"), 0))
        self.assertEqual(first_header.top(), second_header.top())
        self.assertNotEqual(first_header.left(), second_header.left())
        self.assertFalse(first_header.intersects(second_header))

    def test_narrow_page_falls_back_to_one_column(self) -> None:
        widget = self._build(520)
        view = widget._view

        self.assertEqual(view.tile_geometry().column_count, 1)
        lefts = {view.visualRect(widget._model.index(row, 0)).left() for row in range(widget._model.rowCount())}
        self.assertEqual(len(lefts), 1)

    def test_index_at_matches_visual_rect_in_both_columns(self) -> None:
        widget = self._build(1100)
        view = widget._view

        for row in range(widget._model.rowCount()):
            rect = view.visualRect(widget._model.index(row, 0))
            self.assertTrue(rect.isValid())
            self.assertEqual(view.indexAt(rect.center()).row(), row)

    def test_scroll_range_follows_the_tallest_column(self) -> None:
        widget = self._build(1100, height=160)
        view = widget._view
        self._app.processEvents()

        geometry = view.tile_geometry()
        scrollbar = view.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        self.assertEqual(scrollbar.maximum(), geometry.content_height - view.viewport().height())

        last_row = widget._model.rowCount() - 1
        top_before = view.visualRect(widget._model.index(last_row, 0)).top()
        scrollbar.setValue(40)
        self.assertEqual(view.visualRect(widget._model.index(last_row, 0)).top(), top_before - 40)

    def test_tiles_use_compact_rows_and_plain_list_keeps_full_rows(self) -> None:
        from profile.ui.profile_list_delegate import ProfileListDelegate
        from profile.ui.profile_list_model import ProfileListModel
        from profile.ui.profile_list_view import ProfileListView

        widget = self._build(1100)
        profile_index = widget._model.index(self._row(widget, "profile", "youtube"), 0)
        folder_index = widget._model.index(self._row(widget, "folder", "youtube"), 0)
        tile_delegate = widget._delegate
        self.assertEqual(tile_delegate.sizeHint(QStyleOptionViewItem(), profile_index).height(), 32)
        self.assertEqual(tile_delegate.sizeHint(QStyleOptionViewItem(), folder_index).height(), 36)

        # «Порядок в пресете» берёт тот же вид и делегат без режима плиток.
        plain_view = ProfileListView()
        self.addCleanup(plain_view.deleteLater)
        model = ProfileListModel()
        model.apply_view_state(_view_state(_two_groups()))
        plain_view.setModel(model)
        plain_delegate = ProfileListDelegate(plain_view)
        plain_view.setItemDelegate(plain_delegate)
        self.assertFalse(plain_view.tile_layout_enabled())
        self.assertEqual(plain_delegate.sizeHint(QStyleOptionViewItem(), model.index(1, 0)).height(), 44)
        self.assertEqual(plain_delegate.sizeHint(QStyleOptionViewItem(), model.index(0, 0)).height(), 28)

    def test_page_paints_in_tile_mode(self) -> None:
        widget = self._build(1100)

        image = widget.grab().toImage()

        self.assertFalse(image.isNull())
        view = widget._view
        header_rect = view.visualRect(widget._model.index(self._row(widget, "folder", "youtube"), 0))
        origin = view.viewport().mapTo(widget, QPoint(0, 0))
        inside_card = image.pixelColor(origin + header_rect.center() + QPoint(0, 100))
        between_cards = image.pixelColor(origin + QPoint(header_rect.left() - 6, header_rect.center().y()))
        self.assertNotEqual(inside_card, between_cards, "подложка плитки должна отличаться от фона страницы")

    def test_drop_on_card_padding_targets_nearest_row_not_list_end(self) -> None:
        widget = self._build(1100)
        view = widget._view
        last_youtube_row = self._row(widget, "profile", "youtube", 4)
        rect = view.visualRect(widget._model.index(last_youtube_row, 0))
        below_row = QPoint(rect.center().x(), rect.bottom() + 3)

        self.assertFalse(view.indexAt(below_row).isValid())
        self.assertEqual(view.tile_index_near(below_row).row(), last_youtube_row)
        target, destination_id, destination_group = view._drop_target_at(below_row)
        self.assertEqual(target["destination_kind"], "profile_after")
        self.assertEqual(destination_id, "yt-4")
        self.assertEqual(destination_group, "youtube")

        far_below = QPoint(rect.center().x(), view.viewport().height() - 4)
        self.assertFalse(view.tile_index_near(far_below).isValid())
        self.assertEqual(view._drop_target_at(far_below)[0]["destination_kind"], "end")

    def test_left_and_right_arrows_jump_between_columns(self) -> None:
        widget = self._build(1100)
        view = widget._view
        discord_row = self._row(widget, "profile", "discord", 0)
        youtube_row = self._row(widget, "profile", "youtube", 0)
        view.setCurrentIndex(widget._model.index(discord_row, 0))

        view.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(view.currentIndex().row(), youtube_row)

        view.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(view.currentIndex().row(), discord_row)

    def test_top_visible_index_survives_scrolling_into_card_padding(self) -> None:
        many = tuple(
            _item(f"Сайт {position:02d}", key=f"s-{position}", group="common", group_name="Общие", order=position)
            for position in range(30)
        )
        widget = self._build(520, height=220, items=many)
        view = widget._view
        scrollbar = view.verticalScrollBar()
        # Прокрутка на пару пикселей: вверху видна шапка плитки, а в точке
        # (0, 0) — пустое поле перед ней.
        scrollbar.setValue(2)

        self.assertFalse(view.indexAt(QPoint(0, 0)).isValid())
        self.assertEqual(view.top_visible_index().row(), 0)

        fifth = view.visualRect(widget._model.index(5, 0))
        scrollbar.setValue(scrollbar.value() + fifth.top() + 10)
        self.assertEqual(view.top_visible_index().row(), 5)


class ProfileTileTooltipTextTests(unittest.TestCase):
    def test_counter_hint_says_that_not_all_profiles_are_needed(self) -> None:
        from profile.list_view_state import PROFILES_NOT_ALL_NEEDED_HINT, profile_group_counter_tooltip

        text = profile_group_counter_tooltip(3, 5)

        self.assertIn("Включено 3 из 5 профилей группы.", text)
        self.assertIn("Каждое деление полоски — один профиль", text)
        self.assertIn(PROFILES_NOT_ALL_NEEDED_HINT, text)
        self.assertIn("Включать все профили не нужно", PROFILES_NOT_ALL_NEEDED_HINT)
        self.assertIn("смените стратегию у включённого профиля", PROFILES_NOT_ALL_NEEDED_HINT)

    def test_long_group_explains_the_solid_bar_instead_of_segments(self) -> None:
        from profile.list_view_state import profile_group_counter_tooltip

        text = profile_group_counter_tooltip(8, 12)

        self.assertNotIn("Каждое деление", text)
        self.assertIn("какая часть профилей группы включена", text)

    def test_group_and_chevron_hints(self) -> None:
        from profile.list_view_state import profile_group_chevron_tooltip, profile_group_tooltip

        self.assertEqual(
            profile_group_tooltip("YouTube", 3, 5),
            "Группа «YouTube»: профили одного сайта или сервиса.\n"
            "Включено 3 из 5. Нажмите на шапку, чтобы свернуть или развернуть группу.",
        )
        self.assertEqual(profile_group_chevron_tooltip(True), "Свернуть группу")
        self.assertEqual(profile_group_chevron_tooltip(False), "Развернуть группу")

    def test_state_hint_names_the_marker_the_user_actually_sees(self) -> None:
        from profile.list_view_state import profile_state_tooltip

        self.assertTrue(
            profile_state_tooltip(in_preset=True, enabled=True, icon_in_header=True).startswith("Закрашенная точка: ")
        )
        ring = profile_state_tooltip(in_preset=False, enabled=False, icon_in_header=True)
        self.assertTrue(ring.startswith("Кольцо: профиля нет в пресете"))
        self.assertIn("Это нормально", ring)
        self.assertIn("Включать все профили не нужно", ring)
        self.assertTrue(
            profile_state_tooltip(in_preset=True, enabled=True, icon_in_header=False).startswith("Цветной значок: ")
        )
        self.assertTrue(
            profile_state_tooltip(in_preset=False, enabled=False, icon_in_header=False).startswith("Серый значок: ")
        )
        skipped = profile_state_tooltip(in_preset=True, enabled=False, icon_in_header=True)
        self.assertIn("выключен", skipped)
        self.assertIn("--skip", skipped)

    def test_strategy_hint_explains_marks_and_does_not_push_to_enable(self) -> None:
        from profile.list_view_state import profile_strategy_tooltip

        working = profile_strategy_tooltip(
            in_preset=True, enabled=True, strategy_name="hostfakesplit_multi", rating="work", favorite=True
        )
        self.assertIn("Стратегия обхода: hostfakesplit_multi.", working)
        self.assertIn("попробуйте другую стратегию", working)
        self.assertIn("Звезда: стратегия у вас в избранном.", working)
        self.assertIn("Галочка: вы отметили, что эта стратегия работает.", working)
        self.assertIn(
            "Крестик",
            profile_strategy_tooltip(in_preset=True, enabled=True, strategy_name="x", rating="notwork"),
        )
        missing = profile_strategy_tooltip(in_preset=False, enabled=False, strategy_name="Не добавлен")
        self.assertIn("только если этот сайт у вас не открывается", missing)
        self.assertNotIn("Стратегия обхода", missing)

    def test_rows_carry_group_hint_and_calm_not_added_hint(self) -> None:
        state = _view_state(_two_groups())
        folder = _rows_by_kind(state, "folder", "youtube")[0]
        missing = _rows_by_kind(state, "profile", "youtube")[2]

        self.assertIn("Группа «YouTube»", folder["tooltip"])
        self.assertIn("Включено 3 из 5", folder["tooltip"])
        self.assertIn("Профиля ещё нет в пресете. Включать его не обязательно", missing["tooltip"])
        self.assertNotIn("Включите его или выберите", missing["tooltip"])


class ProfileTileElementTooltipTests(unittest.TestCase):
    """Над каждым элементом плитки своя подсказка."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from profile.ui.profile_list_model import ProfileListModel
        from profile.ui.profiles_list import ProfilesList

        self.widget = ProfilesList()
        self.addCleanup(self.widget.deleteLater)
        self.widget.resize(1100, 500)
        self.widget.apply_view_state(_view_state(_two_groups()))
        self.widget.show()
        self._app.processEvents()
        self.model = self.widget._model
        self.roles = ProfileListModel

    def _index(self, kind: str, group: str, position: int = 0):
        rows = [
            row
            for row in range(self.model.rowCount())
            if self.model.index(row, 0).data(self.roles.KindRole) == kind
            and self.model.index(row, 0).data(self.roles.GroupRole) == group
        ]
        return self.model.index(rows[position], 0)

    def _hint(self, index, x: int) -> str:
        option = QStyleOptionViewItem()
        option.rect = self.widget._view.visualRect(index)
        option.font = self.widget._view.font()
        return self.widget._delegate._tile_element_tooltip(QPoint(x, option.rect.center().y()), option, index)

    def test_header_has_three_hints(self) -> None:
        index = self._index("folder", "youtube")
        rect = self.widget._view.visualRect(index)

        self.assertIn("Группа «YouTube»", self._hint(index, rect.left() + 60))
        counter = self._hint(index, rect.right() - 80)
        self.assertIn("Включено 3 из 5 профилей группы.", counter)
        self.assertIn("Включать все профили не нужно", counter)
        self.assertEqual(self._hint(index, rect.right() - 22), "Свернуть группу")

    def test_row_has_state_strategy_and_default_hints(self) -> None:
        working = self._index("profile", "youtube", 0)
        missing = self._index("profile", "youtube", 2)
        rect = self.widget._view.visualRect(working)

        self.assertTrue(self._hint(working, rect.left() + 24).startswith("Закрашенная точка: "))
        self.assertTrue(self._hint(missing, rect.left() + 24).startswith("Кольцо: "))
        self.assertIn("Стратегия обхода: pass.", self._hint(working, rect.right() - 30))
        self.assertIn("Профиль не добавлен в пресет.", self._hint(missing, rect.right() - 30))
        # Над именем остаётся подробная подсказка строки из модели.
        self.assertEqual(self._hint(working, rect.left() + 120), "")

    def test_row_with_its_own_icon_names_the_icon_not_the_dot(self) -> None:
        # В группе Discord значок Discord ушёл в шапку, а у профиля звонков
        # остался свой микрофон.
        index = self._index("profile", "discord", 1)
        rect = self.widget._view.visualRect(index)

        self.assertFalse(index.data(self.roles.IconInHeaderRole))
        self.assertTrue(self._hint(index, rect.left() + 24).startswith("Цветной значок: "))

    def test_help_event_shows_the_element_hint_and_falls_back_to_row_hint(self) -> None:
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QHelpEvent

        view = self.widget._view
        delegate = self.widget._delegate
        shown: list[str] = []
        delegate._tooltip.show_text = lambda text, _pos: shown.append(text)
        index = self._index("folder", "youtube")
        rect = view.visualRect(index)
        option = QStyleOptionViewItem()
        option.rect = rect
        option.font = view.font()

        def help_at(x: int, target_index, target_rect) -> None:
            option.rect = target_rect
            pos = QPoint(x, target_rect.center().y())
            event = QHelpEvent(QEvent.Type.ToolTip, pos, view.viewport().mapToGlobal(pos))
            self.assertTrue(delegate.helpEvent(event, view, option, target_index))

        help_at(rect.right() - 80, index, rect)
        self.assertIn("Включено 3 из 5 профилей группы.", shown[-1])

        row_index = self._index("profile", "youtube", 0)
        row_rect = view.visualRect(row_index)
        help_at(row_rect.left() + 120, row_index, row_rect)
        self.assertEqual(shown[-1], str(row_index.data(self.roles.TooltipRole)).replace("\n", "<br>"))

    def test_tour_can_point_at_the_first_tile_header(self) -> None:
        found = self.widget.first_visible_group_header()

        self.assertIsNotNone(found)
        viewport, rect = found
        self.assertIs(viewport, self.widget._view.viewport())
        first_header = min(
            (self.widget._view.visualRect(self._index("folder", group)) for group in ("youtube", "discord")),
            key=lambda header: (header.top(), header.left()),
        )
        self.assertEqual(rect.top(), first_header.top())
        self.assertEqual(rect.height(), 36)


class ProfileTileRowLayoutTests(unittest.TestCase):
    def test_strategy_takes_at_most_half_and_never_touches_the_name(self) -> None:
        from profile.ui.profile_list_delegate import _tile_row_layout

        layout = _tile_row_layout(QRect(8, 1, 500, 30), strategy_width=900)

        self.assertTrue(layout.strategy_rect.isValid())
        self.assertLessEqual(layout.strategy_rect.width(), (500 - 10 - 18 - 10 - 10) // 2 + 1)
        self.assertLess(layout.name_rect.right(), layout.strategy_rect.left())
        self.assertLess(layout.icon_rect.right(), layout.name_rect.left())
        self.assertEqual(layout.strategy_rect.right(), 8 + 500 - 1 - 10)

    def test_short_strategy_keeps_its_own_width(self) -> None:
        from profile.ui.profile_list_delegate import _tile_row_layout

        layout = _tile_row_layout(QRect(8, 1, 500, 30), strategy_width=64)

        self.assertEqual(layout.strategy_rect.width(), 64)

    def test_very_short_strategy_name_is_not_dropped(self) -> None:
        # «pass» уже сорока точек: прятать её как «обрывок» нельзя.
        from profile.ui.profile_list_delegate import _tile_row_layout

        layout = _tile_row_layout(QRect(8, 1, 500, 30), strategy_width=30)

        self.assertTrue(layout.strategy_rect.isValid())
        self.assertEqual(layout.strategy_rect.width(), 30)

    def test_very_narrow_row_drops_strategy_and_keeps_name(self) -> None:
        from profile.ui.profile_list_delegate import _tile_row_layout

        layout = _tile_row_layout(QRect(8, 1, 140, 30), strategy_width=200)

        self.assertFalse(layout.strategy_rect.isValid())
        self.assertGreater(layout.name_rect.width(), 64)

    def test_header_title_lines_up_with_row_names(self) -> None:
        from profile.ui.profile_list_delegate import _tile_header_layout, _tile_row_layout

        row_rect = QRect(8, 1, 500, 30)
        row = _tile_row_layout(row_rect, strategy_width=80)
        with_icon = _tile_header_layout(row_rect, has_icon=True, counter_width=40, meter_width=58)
        without_icon = _tile_header_layout(row_rect, has_icon=False, counter_width=40, meter_width=58)

        self.assertEqual(with_icon.icon_rect, row.icon_rect)
        self.assertEqual(with_icon.title_rect.left(), row.name_rect.left())
        self.assertFalse(without_icon.icon_rect.isValid())
        self.assertEqual(without_icon.title_rect.left(), row.icon_rect.left())
        self.assertLess(with_icon.title_rect.right(), with_icon.counter_rect.left())
        self.assertLess(with_icon.counter_rect.right(), with_icon.meter_rect.left())
        self.assertLess(with_icon.meter_rect.right(), with_icon.chevron_rect.left())

    def test_narrow_header_hides_counter_and_meter_before_the_title(self) -> None:
        from profile.ui.profile_list_delegate import _tile_header_layout

        layout = _tile_header_layout(QRect(8, 1, 190, 30), has_icon=True, counter_width=40, meter_width=58)

        self.assertFalse(layout.meter_rect.isValid())
        self.assertFalse(layout.counter_rect.isValid())
        self.assertGreater(layout.title_rect.width(), 90)
        self.assertLess(layout.title_rect.right(), layout.chevron_rect.left())


if __name__ == "__main__":
    unittest.main()
