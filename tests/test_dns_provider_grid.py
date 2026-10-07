from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication

from dns.dns_providers import STATUS_AT_RISK, STATUS_BLOCKED
from dns.ui.provider_grid import (
    ADD_TILE_KEY,
    DnsProviderGrid,
    DnsTile,
    GridTexts,
    latency_text,
    status_color,
    status_text,
    tile_accessible_text,
)


def _tiles() -> list[DnsTile]:
    return [
        DnsTile(kind="group", title="Популярные", counter="2"),
        DnsTile(kind="provider", key="Cloudflare", title="Cloudflare", note="Быстрый", address="1.1.1.1", selected=True),
        DnsTile(kind="provider", key="Google DNS", title="Google DNS", address="8.8.8.8", latency="ok", latency_ms=18.4, fastest=True),
        DnsTile(kind="group", title="Свои DNS"),
        DnsTile(kind="provider", key="Дом", title="Дом", address="192.168.1.1", custom=True),
        DnsTile(kind="add", key=ADD_TILE_KEY, title="Свой DNS", note="Добавить свой адрес"),
    ]


def _key(grid: DnsProviderGrid, key: Qt.Key, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    grid.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, modifiers))


class DnsProviderGridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _grid(self) -> DnsProviderGrid:
        grid = DnsProviderGrid()
        self.addCleanup(grid.deleteLater)
        grid.resize(560, 100)
        grid.set_tiles(_tiles())
        return grid

    def test_layout_places_groups_on_own_rows_and_grows_height(self) -> None:
        grid = self._grid()

        cloudflare = grid.tile_rect("Cloudflare")
        google = grid.tile_rect("Google DNS")
        custom = grid.tile_rect("Дом")
        self.assertEqual(cloudflare.top(), google.top())
        self.assertGreater(custom.top(), cloudflare.bottom())
        self.assertGreaterEqual(grid.height(), grid.tile_rect(ADD_TILE_KEY).bottom())

    def test_keyboard_moves_between_tiles_and_activates(self) -> None:
        grid = self._grid()
        activated: list[str] = []
        added: list[bool] = []
        grid.activated.connect(activated.append)
        grid.add_clicked.connect(lambda: added.append(True))

        grid._set_cursor(1)
        _key(grid, Qt.Key.Key_Right)
        _key(grid, Qt.Key.Key_Return)
        _key(grid, Qt.Key.Key_End)
        _key(grid, Qt.Key.Key_Space)

        self.assertEqual(activated, ["Google DNS"])
        self.assertEqual(added, [True])
        self.assertIn("Свой DNS", grid.accessibleDescription())

    def test_focus_starts_on_selected_tile_and_reads_state(self) -> None:
        grid = self._grid()

        grid._cursor = -1
        grid.focusInEvent(QFocusEvent(QEvent.Type.FocusIn))

        self.assertEqual(grid._key_at(grid._cursor), "Cloudflare")
        self.assertIn("Cloudflare, выбран", grid.accessibleDescription())

    def test_focus_scrolls_to_the_tile_only_when_it_came_from_keyboard(self) -> None:
        # Фокус, перескочивший сам (с выключенной кнопки, от щелчка мышью),
        # не должен уводить страницу к выбранной плитке.
        for reason, scrolls in (
            (Qt.FocusReason.OtherFocusReason, False),
            (Qt.FocusReason.MouseFocusReason, False),
            (Qt.FocusReason.TabFocusReason, True),
            (Qt.FocusReason.BacktabFocusReason, True),
        ):
            with self.subTest(reason=reason):
                grid = self._grid()
                grid._cursor = -1
                with patch.object(grid, "_ensure_visible") as reveal:
                    grid.focusInEvent(QFocusEvent(QEvent.Type.FocusIn, reason))

                self.assertEqual(grid._key_at(grid._cursor), "Cloudflare")
                self.assertEqual(reveal.called, scrolls)

    def test_context_menu_only_for_custom_servers(self) -> None:
        grid = self._grid()
        wanted: list[str] = []
        grid.context_menu_wanted.connect(lambda key, _pos: wanted.append(key))

        grid._set_cursor(1)
        _key(grid, Qt.Key.Key_Menu)
        grid._set_cursor(4)
        _key(grid, Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier)
        center = QPointF(grid.tile_rect("Дом").center())
        grid.mouseReleaseEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                center,
                center,
                Qt.MouseButton.RightButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

        self.assertEqual(wanted, ["Дом", "Дом"])

    def test_mouse_click_activates_tile_under_cursor(self) -> None:
        grid = self._grid()
        activated: list[str] = []
        grid.activated.connect(activated.append)
        point = QPointF(grid.tile_rect("Cloudflare").center())

        for event_type, buttons in (
            (QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
        ):
            event = QMouseEvent(event_type, point, point, Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
            if event_type == QEvent.Type.MouseButtonPress:
                grid.mousePressEvent(event)
            else:
                grid.mouseReleaseEvent(event)

        self.assertEqual(activated, ["Cloudflare"])
        self.assertEqual(grid.index_at(QPoint(-5, -5)), -1)

    def test_texts_follow_interface_language(self) -> None:
        tile = DnsTile(kind="provider", key="G", title="Google", address="8.8.8.8", latency="ok", latency_ms=0.4, fastest=True)
        english = GridTexts(ms="{ms} ms", selected="selected", not_selected="not selected", fastest="fastest")

        self.assertEqual(latency_text(tile), "1 мс")
        self.assertEqual(latency_text(tile, english), "1 ms")
        self.assertEqual(latency_text(DnsTile(kind="provider", latency="timeout")), "нет ответа")
        self.assertEqual(latency_text(DnsTile(kind="provider")), "")
        self.assertEqual(tile_accessible_text(tile, english), "Google, not selected, 8.8.8.8, 1 ms, fastest")

    def test_status_mark_is_named_and_read_aloud(self) -> None:
        blocked = DnsTile(kind="provider", key="G", title="Google", address="8.8.8.8", status=STATUS_BLOCKED, has_dnssec=True)
        at_risk = DnsTile(kind="provider", key="A", title="AdGuard", status=STATUS_AT_RISK)
        english = GridTexts(status_blocked="blocked", status_at_risk="at risk", not_selected="not selected")

        self.assertEqual(status_text(blocked), "блокируется")
        self.assertEqual(status_text(at_risk), "под угрозой")
        self.assertEqual(status_text(at_risk, english), "at risk")
        self.assertEqual(status_text(DnsTile(kind="provider")), "")
        self.assertEqual(tile_accessible_text(blocked, english), "Google, not selected, blocked, 8.8.8.8, DNSSEC")
        self.assertNotEqual(status_color(STATUS_BLOCKED, True), status_color(STATUS_AT_RISK, True))
        self.assertIsNone(status_color("", True))

    def test_status_mark_and_group_note_are_painted(self) -> None:
        """Пометка и пояснение группы меняют картинку, а не только данные."""

        def picture(status: str, note: str) -> bytes:
            grid = DnsProviderGrid()
            self.addCleanup(grid.deleteLater)
            grid.resize(560, 100)
            grid.set_tiles(
                [
                    DnsTile(kind="group", title="Для ИИ", counter="1", note=note),
                    DnsTile(kind="provider", key="G", title="Google", note="Надёжный", address="8.8.8.8", status=status),
                ]
            )
            image = grid.grab().toImage()
            return bytes(image.constBits().asarray(image.sizeInBytes()))

        plain = picture("", "")
        self.assertNotEqual(picture(STATUS_BLOCKED, ""), plain)
        self.assertNotEqual(picture(STATUS_AT_RISK, ""), picture(STATUS_BLOCKED, ""))
        self.assertNotEqual(picture("", "серверы сообщества"), plain)

    def test_narrow_tile_keeps_address_and_drops_extra_marks(self) -> None:
        grid = DnsProviderGrid()
        self.addCleanup(grid.deleteLater)
        grid.resize(270, 100)
        tile = DnsTile(
            kind="provider", key="Q", title="Quad9", address="149.112.112.112",
            has_dnssec=True, has_ipv6=True, has_doh=True, latency="timeout",
        )
        grid.set_tiles([tile])

        self.assertFalse(grid.grab().isNull())

    def test_paint_does_not_fail_for_every_tile_state(self) -> None:
        grid = self._grid()
        grid.set_tiles(
            [
                *_tiles(),
                DnsTile(kind="provider", key="P", title="P", pending=True, latency="measuring", has_ipv6=True, has_doh=True),
                DnsTile(kind="provider", key="T", title="T", latency="timeout", color="#673ab7"),
            ]
        )
        grid.flash("Cloudflare")

        image = grid.grab()

        self.assertFalse(image.isNull())


class DnsProviderGridMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _grid(self, enabled: bool = True) -> DnsProviderGrid:
        item = patch("dns.ui.provider_grid.are_live_animations_enabled", return_value=enabled)
        item.start()
        self.addCleanup(item.stop)
        grid = DnsProviderGrid()
        self.addCleanup(grid.deleteLater)
        grid.resize(560, 100)
        self.now = [100.0]
        grid._clock = lambda: self.now[0]
        return grid

    @staticmethod
    def _tiles(*, pending: str = "", selected: str = "") -> list[DnsTile]:
        return [
            DnsTile(kind="provider", key=key, title=key, pending=key == pending, selected=key in (pending, selected), color="#f48120")
            for key in ("Cloudflare", "Google DNS")
        ]

    def test_fast_apply_still_draws_full_circle_before_settle(self) -> None:
        from dns.ui.provider_grid import CHARGE_MS

        grid = self._grid()
        grid.set_tiles(self._tiles(selected="Cloudflare"))
        self.assertFalse(grid.is_charging())

        grid.set_tiles(self._tiles(pending="Google DNS"))
        self.assertTrue(grid.is_charging("Google DNS"))

        # Windows записала DNS за 0,1 с: комета ещё не замкнула круг — оборота нет.
        self.now[0] += 0.1
        grid.set_tiles(self._tiles(selected="Google DNS"))
        self.assertTrue(grid.is_charging("Google DNS"))
        self.assertEqual(grid.settling_keys(), [])
        self.assertEqual(grid.tile("Google DNS").pending, False)

        self.now[0] += CHARGE_MS / 1000.0
        grid._tick()
        self.assertFalse(grid.is_charging())
        self.assertEqual(grid.settling_keys(), ["Google DNS"])

    def test_slow_apply_keeps_circling_and_settles_right_away(self) -> None:
        grid = self._grid()
        grid.set_tiles(self._tiles(pending="Google DNS"))

        self.now[0] += 3.0
        grid._tick()
        self.assertTrue(grid.is_charging("Google DNS"))

        grid.set_tiles(self._tiles(selected="Google DNS"))
        self.assertFalse(grid.is_charging())
        self.assertEqual(grid.settling_keys(), ["Google DNS"])

    def test_failed_apply_does_not_play_settle(self) -> None:
        grid = self._grid()
        grid.set_tiles(self._tiles(pending="Google DNS"))

        grid.set_tiles(self._tiles(selected="Cloudflare"))

        self.assertEqual(grid.settling_keys(), [])
        self.assertFalse(grid.is_charging())

    def test_no_motion_when_live_animations_are_off(self) -> None:
        grid = self._grid(enabled=False)
        grid.set_tiles(self._tiles(pending="Google DNS"))
        self.assertFalse(grid.is_charging())

        grid.set_tiles(self._tiles(selected="Google DNS"))
        self.assertEqual(grid.settling_keys(), [])

    def test_comet_runs_one_full_circle_then_orbits(self) -> None:
        from dns.ui.provider_grid import CHARGE_MS, comet_geometry

        self.assertEqual(comet_geometry(0.0)[0], 0.0)
        self.assertAlmostEqual(comet_geometry(CHARGE_MS / 2)[0], 180.0, places=3)
        head, tail, looping = comet_geometry(CHARGE_MS - 1)
        self.assertGreater(head, 359.0)
        self.assertFalse(looping)
        self.assertEqual(comet_geometry(CHARGE_MS + 200)[1:], (140.0, True))

    def test_every_motion_frame_paints(self) -> None:
        grid = self._grid()
        grid.set_tiles(self._tiles(pending="Google DNS"))
        for step in (0.0, 0.2, 0.5, 0.9, 2.0):
            self.now[0] = 100.0 + step
            self.assertFalse(grid.grab().isNull())
        grid.set_tiles(self._tiles(selected="Google DNS"))
        for progress in (0.0, 0.3, 0.6, 1.0):
            grid._on_settle_value(progress)
            self.assertFalse(grid.grab().isNull())

    def test_check_mark_pops_with_overshoot(self) -> None:
        from dns.ui.provider_grid import pop_scale

        values = [pop_scale(step / 20) for step in range(21)]
        self.assertAlmostEqual(values[0], 0.0, places=6)
        self.assertGreater(max(values), 1.05)
        self.assertAlmostEqual(values[-1], 1.0, places=6)


class DnsNowBadgeMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _badge(self, enabled: bool = True):
        from dns.ui.now_panel import _Badge

        item = patch("dns.ui.now_panel.are_live_animations_enabled", return_value=enabled)
        item.start()
        self.addCleanup(item.stop)
        badge = _Badge()
        self.addCleanup(badge.deleteLater)
        badge.show()
        return badge

    def test_new_server_flips_badge_like_a_coin(self) -> None:
        badge = self._badge()

        badge.set_icon("fa5s.bolt", "#f48120")
        self.assertTrue(badge.is_flipping())
        for progress in (0.2, 0.5, 0.7, 0.95):
            badge._on_flip_value(progress)
            self.assertFalse(badge.grab().isNull())

        badge._flip_anim.stop()
        badge.set_icon("fa5s.bolt", "#f48120")
        self.assertFalse(badge.is_flipping())

    def test_comet_finishes_its_circle_after_busy_ends(self) -> None:
        from dns.ui.provider_grid import CHARGE_MS

        badge = self._badge()
        now = [10.0]
        badge._clock = lambda: now[0]

        badge.set_busy(True)
        self.assertTrue(badge.is_busy())
        now[0] += 0.1
        self.assertFalse(badge.grab().isNull())
        badge.set_busy(False)
        self.assertTrue(badge.is_busy())  # круг ещё не замкнут

        now[0] += CHARGE_MS / 1000.0
        badge._tick()
        self.assertFalse(badge.is_busy())

    def test_badge_stays_still_when_animations_are_off(self) -> None:
        badge = self._badge(enabled=False)

        badge.set_icon("fa5s.bolt", "#f48120")
        badge.set_busy(True)

        self.assertFalse(badge.is_flipping())
        self.assertFalse(badge.is_busy())


if __name__ == "__main__":
    unittest.main()
