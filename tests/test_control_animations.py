from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

import presets.ui.control.status_hero_card as hero_module
import presets.ui.control.top_summary_widget as summary_module
import ui.pulsing_dot as dot_module
import ui.widgets.bypass_scene as scene_module
import ui.widgets.fun.mascot as mascot_module
import ui.widgets.motion_icon as motion_module
import ui.widgets.soft_visibility as soft_module
import ui.widgets.tile_grid as tile_module
from ui.widgets.soft_visibility import set_visible_softly, soft_visibility_target
from donater.premium_display import TIER_ACTIVE, TIER_FREE, PremiumDisplay
from presets.ui.control.status_hero_card import StatusHeroCard
from presets.ui.control.top_summary_widget import ControlTopSummaryWidget
from ui.pulsing_dot import PulsingDot
from ui.widgets.bypass_scene import BypassScene, mascot_mood_for_phase
from ui.widgets.fun.mascot import GESTURE_TOSS, MOOD_ALARM, MOOD_BUSY, MOOD_HAPPY, MOOD_IDLE, MOOD_SAD
from ui.widgets.motion_icon import GESTURE_BOUNCE, MotionIcon


def _host(test: unittest.TestCase, child: QWidget) -> QWidget:
    host = QWidget()
    QVBoxLayout(host).addWidget(child)
    test.addCleanup(host.deleteLater)
    return host


def _animations(enabled: bool, *modules):
    patches = [mock.patch.object(module, "are_live_animations_enabled", return_value=enabled) for module in modules]
    for patcher in patches:
        patcher.start()
    return patches


class PulsingDotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._patches = _animations(True, dot_module)
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    def test_beat_then_rest_without_frames(self) -> None:
        dot = PulsingDot()
        _host(self, dot).show()

        dot.start_pulse()
        self.assertTrue(dot.is_beating())
        self.assertFalse(dot._rest_timer.isActive())

        dot._finish_beat()
        # После удара кадры не рисуются: ждёт только одиночный таймер паузы.
        self.assertFalse(dot.is_beating())
        self.assertTrue(dot._rest_timer.isActive())

    def test_hidden_or_stopped_dot_does_not_animate(self) -> None:
        dot = PulsingDot()
        host = _host(self, dot)

        dot.start_pulse()
        self.assertTrue(dot._is_pulsing)
        self.assertFalse(dot.is_beating())

        host.show()
        self.assertTrue(dot.is_beating())

        host.hide()
        self.assertFalse(dot.is_beating())
        self.assertFalse(dot._rest_timer.isActive())

        host.show()
        dot.stop_pulse()
        self.assertFalse(dot.is_beating())
        self.assertFalse(dot._rest_timer.isActive())

    def test_beat_is_drawn_at_modest_frame_rate(self) -> None:
        dot = PulsingDot()
        self.addCleanup(dot.deleteLater)
        self.assertGreaterEqual(dot._beat.interval(), 30)
        duty = dot_module.BEAT_DURATION_MS / (dot_module.BEAT_DURATION_MS + dot_module.BEAT_REST_MS)
        frames_per_second = duty * 1000 / dot._beat.interval()
        self.assertLess(frames_per_second, 10.0)

    def test_no_beat_when_animations_are_disabled(self) -> None:
        for patcher in self._patches:
            patcher.stop()
        self._patches = _animations(False, dot_module)
        dot = PulsingDot()
        _host(self, dot).show()

        dot.start_pulse()

        self.assertTrue(dot._is_pulsing)
        self.assertFalse(dot.is_beating())

    def test_color_fades_when_visible_and_jumps_when_hidden(self) -> None:
        dot = PulsingDot()
        host = _host(self, dot)
        dot.set_color("#808080")
        self.assertEqual(dot._color, QColor("#808080"))

        host.show()
        dot.set_color("#4caf50")
        self.assertEqual(dot._fade.state(), dot._fade.State.Running)
        self.assertEqual(dot._color, QColor("#4caf50"))
        self.assertNotEqual(dot._shown_color, QColor("#4caf50"))
        dot._fade.setCurrentTime(dot._fade.duration())
        self.assertEqual(dot._shown_color, QColor("#4caf50"))


class BypassSceneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._enabled = True
        for module in (dot_module, scene_module, hero_module):
            patcher = mock.patch.object(module, "are_live_animations_enabled", side_effect=lambda: self._enabled)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _scene(self, phase: str = "") -> BypassScene:
        scene = BypassScene()
        _host(self, scene).show()
        if phase:
            scene.set_phase(phase)
        return scene

    def test_running_flow_is_continuous_without_rest_pause(self) -> None:
        scene = self._scene("running")
        self.assertTrue(scene.is_beating())

        with mock.patch.object(scene._beat_clock, "elapsed", return_value=scene_module.BLOCKED_BURST_MS * 3):
            scene._on_beat_frame()

        self.assertTrue(scene.is_beating())
        self.assertFalse(scene._rest_timer.isActive())
        self.assertGreater(scene._flow_time, 0.0)

    def test_stopped_scene_fires_a_burst_and_then_rests_without_frames(self) -> None:
        scene = self._scene("stopped")
        self.assertTrue(scene.is_beating())

        with mock.patch.object(scene._beat_clock, "elapsed", return_value=scene_module.BLOCKED_BURST_MS // 2):
            scene._on_beat_frame()
        self.assertTrue(scene.is_beating())
        self.assertAlmostEqual(scene._pulse_phase, 0.5, places=2)

        with mock.patch.object(scene._beat_clock, "elapsed", return_value=scene_module.BLOCKED_BURST_MS + 1):
            scene._on_beat_frame()
        self.assertFalse(scene.is_beating())
        self.assertTrue(scene._rest_timer.isActive())
        self.assertEqual(scene._rest_timer.interval(), scene_module.BLOCKED_REST_MS)

    def test_stop_pulse_keeps_the_stopped_scene_alive(self) -> None:
        scene = self._scene("stopped")
        scene.stop_pulse()
        self.assertTrue(scene.is_beating() or scene._rest_timer.isActive())

    def test_no_frames_before_the_phase_is_known(self) -> None:
        scene = self._scene()
        scene.start_pulse()
        self.assertFalse(scene.is_beating())
        self.assertFalse(scene._rest_timer.isActive())

    def test_scene_stops_when_hidden(self) -> None:
        scene = BypassScene()
        host = _host(self, scene)
        scene.set_phase("running")
        self.assertFalse(scene.is_beating())

        host.show()
        self.assertTrue(scene.is_beating())
        host.hide()
        self.assertFalse(scene.is_beating())
        self.assertFalse(scene._rest_timer.isActive())

    def test_scene_halts_on_next_frame_when_animations_turn_off(self) -> None:
        scene = self._scene("running")
        self.assertTrue(scene.is_beating())

        self._enabled = False
        scene._on_beat_frame()

        self.assertFalse(scene.is_beating())
        self.assertFalse(scene._rest_timer.isActive())

    def test_no_frames_when_animations_are_disabled(self) -> None:
        self._enabled = False
        for phase in ("running", "stopped", "starting"):
            with self.subTest(phase=phase):
                scene = self._scene(phase)
                self.assertFalse(scene.is_beating())
                self.assertFalse(scene._rest_timer.isActive())
                self.assertEqual(scene._open_t, 1.0 if phase == "running" else 0.0)

    def test_flow_frames_are_announced_only_while_running(self) -> None:
        frames: list[float] = []
        scene = self._scene("starting")
        scene.flowFrame.connect(frames.append)
        with mock.patch.object(scene._beat_clock, "elapsed", return_value=500):
            scene._on_beat_frame()
        self.assertEqual(frames, [])

        scene.set_phase("running")
        with mock.patch.object(scene._beat_clock, "elapsed", return_value=500):
            scene._on_beat_frame()
        self.assertEqual(len(frames), 1)
        self.assertAlmostEqual(frames[0], scene._flow_time)

        scene.set_phase("stopped")
        with mock.patch.object(scene._beat_clock, "elapsed", return_value=500):
            scene._on_beat_frame()
        self.assertEqual(len(frames), 1)

    def test_frames_repaint_only_lanes_wall_and_button(self) -> None:
        scene = self._scene("running")
        region = scene._motion_region().boundingRect()
        self.assertGreater(region.left(), 0)
        self.assertLess(region.right(), scene.width() - 1)

    def test_wall_opens_only_while_running(self) -> None:
        scene = self._scene("stopped")
        self.assertEqual(scene._open_target(), 0.0)
        scene.set_phase("starting")
        self.assertEqual(scene._open_target(), 0.0)
        scene.set_phase("running")
        self.assertEqual(scene._open_target(), 1.0)
        self.assertEqual(scene._wall_fade.state(), scene._wall_fade.State.Running)

    def test_failure_shakes_only_when_it_happens_on_screen(self) -> None:
        first = self._scene("failed")
        self.assertNotEqual(first._shake.state(), first._shake.State.Running)

        scene = self._scene("starting")
        scene.set_phase("failed")
        self.assertEqual(scene._shake.state(), scene._shake.State.Running)

    def test_only_the_button_is_clickable(self) -> None:
        from PyQt6.QtCore import QPointF

        scene = self._scene("stopped")
        scene.set_clickable(True)
        clicks = []
        scene.clicked.connect(lambda: clicks.append(1))

        self.assertTrue(scene._is_over_button(QPointF(scene.gate_center())))
        self.assertFalse(scene._is_over_button(QPointF(12, scene.height() / 2)))

        scene.set_click_locked(True)
        scene.click()
        self.assertEqual(clicks, [])
        scene.set_click_locked(False)
        scene.set_click_enabled(False)
        scene.click()
        self.assertEqual(clicks, [])
        scene.set_click_enabled(True)
        scene.click()
        self.assertEqual(clicks, [1])

    def test_signals_report_phase_and_final_color_once(self) -> None:
        scene = self._scene()
        phases, colors = [], []
        scene.phaseChanged.connect(phases.append)
        scene.colorChanged.connect(colors.append)

        scene.set_color("#6ccb5f")
        scene.set_color("#6ccb5f")
        scene.set_phase("running")
        scene.set_phase("running")
        scene.set_phase("что-то новое")

        self.assertEqual(colors, ["#6ccb5f"])
        self.assertEqual(phases, ["running", "stopped"])

    def test_mascot_mood_follows_phase(self) -> None:
        self.assertEqual(mascot_mood_for_phase("running"), MOOD_IDLE)
        self.assertEqual(mascot_mood_for_phase("running", "starting"), MOOD_HAPPY)
        self.assertEqual(mascot_mood_for_phase("running", "running"), MOOD_IDLE)
        self.assertEqual(mascot_mood_for_phase("starting", "stopped"), MOOD_BUSY)
        self.assertEqual(mascot_mood_for_phase("stopping", "running"), MOOD_BUSY)
        self.assertEqual(mascot_mood_for_phase("failed", "starting"), MOOD_SAD)
        self.assertEqual(mascot_mood_for_phase("stopped", "stopping"), MOOD_ALARM)

        scene = self._scene("starting")
        self.assertEqual(scene.mascot().mood(), MOOD_BUSY)
        scene.set_phase("running")
        self.assertEqual(scene.mascot().mood(), MOOD_HAPPY)

    def test_mascot_stands_big_on_the_left_facing_the_wall(self) -> None:
        scene = self._scene("stopped")
        mascot = scene.mascot()
        left, right, top, bottom = scene._lanes()

        self.assertIs(mascot.parent(), scene)
        # Медоед у левого края и стоит на «полу»: лапы у нижнего края сцены.
        self.assertEqual(mascot.x(), scene_module.MASCOT_MARGIN)
        self.assertLessEqual(mascot.geometry().bottom(), scene.height())
        self.assertGreaterEqual(mascot.geometry().top(), 0)
        self.assertGreaterEqual(scene.height() - mascot.geometry().bottom(), 1)
        # Он занимает бо́льшую часть высоты сцены, а не теряется в ней.
        self.assertGreater(scene_module.MASCOT_SIZE / scene.height(), 0.65)
        # Дорожки начинаются у морды: сверху пасть, снизу лапа с «Z».
        self.assertEqual(left, mascot.geometry().right() + 1)
        self.assertLess(top, bottom)
        # Кнопка в стене напротив морды, на середине между дорожками, а не в центре сцены.
        gate = scene.gate_center()
        self.assertEqual(gate.x(), round(left + scene_module.GATE_OFFSET))
        self.assertEqual(gate.y(), round((top + bottom) / 2))
        self.assertLess(gate.x(), scene.width() / 2)
        # Справа от стены остаётся длинный путь к сайтам.
        self.assertGreater(right - gate.x(), 2 * (gate.x() - left))

    def test_mascot_keeps_its_place_when_the_scene_is_stretched(self) -> None:
        scene = self._scene("stopped")
        before = scene.mascot().pos()
        gate_before = scene.gate_center()
        scene.resize(scene_module.SCENE_MIN_WIDTH + 100, scene.height())
        self.assertEqual(scene.mascot().pos(), before)
        self.assertEqual(scene.gate_center(), gate_before)

    def test_power_arc_links_the_bolt_and_the_button_only_while_working(self) -> None:
        scene = self._scene("stopped")
        center = scene_module.QPointF(scene.gate_center())
        start, end = scene._link_ends(center)
        # Дуга короткая: от острия молнии до края кнопки.
        self.assertLess(start.x(), end.x())
        self.assertLess(end.x(), center.x())

        def drawn(phase: str) -> int:
            scene.set_phase(phase)
            scene._flow_time = 0.7
            calls = []
            painter = mock.Mock()
            painter.drawLine.side_effect = lambda a, b: calls.append((a, b))
            scene._paint_link(painter, center, QColor("#6ccb5f"), scene._open_target())
            return len(calls)

        self.assertEqual(drawn("stopped"), 0)
        self.assertEqual(drawn("failed"), 0)
        for phase in ("starting", "stopping", "running"):
            with self.subTest(phase=phase):
                self.assertGreater(drawn(phase), 0)

    def test_mascot_tosses_packets_when_a_burst_starts(self) -> None:
        scene = self._scene("stopped")
        self.assertEqual(scene.mascot().gesture(), GESTURE_TOSS)

    def test_mouth_catches_the_first_packet_and_paw_swats_the_second(self) -> None:
        scene = self._scene("stopped")
        mascot = scene.mascot()
        starts, span = scene_module.BLOCKED_PACKET_STARTS, scene_module.BLOCKED_PACKET_SPAN
        back = scene_module.BLOCKED_BACK_END

        # Вот-вот поймает: пасть раскрыта, лапа спокойна.
        near = starts[0] + back * span - 0.01
        with mock.patch.object(scene._beat_clock, "elapsed", return_value=int(scene_module.BLOCKED_BURST_MS * near)):
            scene._on_beat_frame()
        jaw, paw, _glow = mascot.scene_pose()
        self.assertGreater(jaw, 0.5)
        self.assertLessEqual(paw, 1.0)

        # Второй пакет возвращается к лапе: она хлопает, по молнии бежит блик, пасть не работает.
        swat = starts[1] + (back + (1.0 - back) * 0.3) * span
        with mock.patch.object(scene._beat_clock, "elapsed", return_value=int(scene_module.BLOCKED_BURST_MS * swat)):
            scene._on_beat_frame()
        jaw, paw, glow = mascot.scene_pose()
        self.assertGreater(paw, 10.0)
        self.assertGreater(glow, 0.0)
        self.assertLess(abs(jaw), 0.5)

    def test_scene_pose_resets_when_the_burst_ends_or_frames_stop(self) -> None:
        scene = self._scene("stopped")
        mascot = scene.mascot()
        mascot.set_scene_pose(1.0, 20.0, 0.5)
        with mock.patch.object(scene._beat_clock, "elapsed", return_value=scene_module.BLOCKED_BURST_MS + 1):
            scene._on_beat_frame()
        self.assertEqual(mascot.scene_pose(), (0.0, 0.0, 0.0))

        mascot.set_scene_pose(1.0, 20.0, 0.5)
        scene._halt()
        self.assertEqual(mascot.scene_pose(), (0.0, 0.0, 0.0))

    def test_sad_mascot_does_not_toss_after_a_failed_start(self) -> None:
        scene = self._scene("starting")
        scene.set_phase("failed")
        mascot = scene.mascot()
        self.assertEqual(mascot.mood(), MOOD_SAD)
        self.assertNotEqual(mascot.gesture(), GESTURE_TOSS)

        with mock.patch.object(scene._beat_clock, "elapsed", return_value=int(scene_module.BLOCKED_BURST_MS * 0.5)):
            scene._on_beat_frame()
        self.assertEqual(mascot.scene_pose(), (0.0, 0.0, 0.0))

    def test_mouth_opens_for_a_response_and_paw_flicks_for_an_outgoing_packet(self) -> None:
        scene = self._scene("running")
        left, right, _top, _bottom = scene._lanes()
        scene._flow_time = 0.0
        speed, offsets = scene_module.FLOW_LANES[0][1:]
        # Ответ у самой пасти: пасть раскрыта.
        scene._flow_time = ((right - (left + 6.0)) / (right - left + scene_module.EAT_DEPTH) - offsets[0]) * (right - left + scene_module.EAT_DEPTH) / speed
        jaw, _paw, _glow = scene._flow_pose(left, right, 1.0)
        self.assertGreater(jaw, 0.5)
        # Закрытые ворота: ответов нет, пасть не раскрывается.
        self.assertLessEqual(scene._flow_pose(left, right, 0.0)[0], 0.0)

        scene._flow_time = 0.0
        paw_offsets = scene_module.FLOW_LANES[1][2]
        self.assertTrue(any(
            (setattr(scene, "_flow_time", step * 0.05), scene._flow_pose(left, right, 1.0)[1])[1] > 5.0
            for step in range(120)
        ), paw_offsets)

    def test_calm_mascot_breathes_with_the_flow_frames(self) -> None:
        scene = self._scene("running")
        mascot = scene.mascot()
        quarter = int(scene_module.BREATH_PERIOD_S * 250)
        with mock.patch.object(scene._beat_clock, "elapsed", return_value=quarter):
            scene._on_beat_frame()
        self.assertAlmostEqual(mascot._breath, 1.0, places=2)
        _dy, _angle, scale_x, scale_y = mascot.pose()
        self.assertGreater(scale_y, 1.0)
        self.assertLess(scale_x, 1.0)

    def test_gate_opens_inner_bricks_first_with_overshoot(self) -> None:
        scene = self._scene("starting")
        scene.set_phase("running")
        self.assertTrue(scene.is_gate_moving())
        self.assertTrue(scene._gate_opening)
        self.assertEqual(scene._wall_fade.duration(), scene_module.GATE_OPEN_MS)

        scene._open_t = 0.3
        self.assertGreater(scene._brick_progress(0), scene._brick_progress(2))
        # Пружинка: где-то по ходу кирпич перелетает дальше конечного места.
        self.assertTrue(any(
            (setattr(scene, "_open_t", value), scene._brick_shift(0))[1] > 1.0
            for value in (0.6, 0.7, 0.8, 0.9)
        ))
        scene._open_t = 1.0
        self.assertEqual([scene._brick_shift(row) for row in range(scene_module.WALL_BLOCKS)], [1.0, 1.0, 1.0])

    def test_gate_closes_outer_bricks_first_and_lands_with_a_bounce(self) -> None:
        scene = self._scene("running")
        scene._wall_fade.stop()
        scene._open_t = 1.0
        scene.set_phase("stopping")
        self.assertFalse(scene._gate_opening)
        self.assertEqual(scene._wall_fade.duration(), scene_module.GATE_CLOSE_MS)

        scene._open_t = 0.3
        self.assertLess(scene._brick_progress(2), scene._brick_progress(0))
        scene._open_t = 0.02
        self.assertGreater(scene._brick_shift(0), 0.0)
        scene._open_t = 0.0
        self.assertEqual(scene._brick_shift(0), 0.0)

    def test_button_breathes_out_on_start_and_in_on_stop(self) -> None:
        scene = self._scene("running")
        self.assertFalse(scene.is_popping())

        scene.set_phase("stopping")
        self.assertTrue(scene.is_popping())
        self.assertFalse(scene._pop_up)
        scene._pop.setCurrentTime(scene._pop.duration() // 4)
        self.assertLess(scene._pop_scale(), 1.0)

        scene.set_phase("stopped")
        scene.set_phase("starting")
        scene.set_phase("running")
        self.assertTrue(scene._pop_up)
        scene._pop.setCurrentTime(scene._pop.duration() // 4)
        self.assertGreater(scene._pop_scale(), 1.0)
        scene._pop.setCurrentTime(scene._pop.duration())
        self.assertEqual(scene._pop_scale(), 1.0)

    def test_hidden_scene_jumps_to_the_final_look(self) -> None:
        scene = BypassScene()
        host = _host(self, scene)
        host.show()
        scene.set_phase("starting")
        scene.set_phase("running")
        self.assertTrue(scene.is_gate_moving() and scene.is_popping())

        host.hide()
        self.assertFalse(scene.is_gate_moving())
        self.assertFalse(scene.is_popping())
        self.assertEqual(scene._open_t, 1.0)

    def _stream(self, scene, through: float) -> list[tuple[float, float, int]]:
        drawn: list[tuple[float, float, int]] = []
        center = scene_module.QPointF(scene.gate_center())
        left, right, top, bottom = scene._lanes()
        with mock.patch.object(
            scene, "_paint_packet", side_effect=lambda _p, x, _y, _c, alpha, direction: drawn.append((x, alpha, direction))
        ):
            scene._paint_stream(None, center, left, right, (top, bottom), scene_module.BUTTON_RADIUS + 3.0, QColor("#6ccb5f"), through)
        return drawn

    def test_packets_keep_their_places_when_the_gate_opens(self) -> None:
        scene = self._scene("starting")
        scene._flow_time = 1.7
        closed = self._stream(scene, 0.0)
        opened = self._stream(scene, 1.0)

        self.assertEqual([x for x, _a, _d in closed], [x for x, _a, _d in opened])
        center_x = scene.gate_center().x()
        for x, alpha, direction in closed:
            if direction < 0 or x > center_x:
                # Ворота закрыты: за стеной и на обратной дорожке пакетов не видно.
                self.assertEqual(alpha, 0.0)
        self.assertTrue(any(alpha > 0.0 for x, alpha, direction in opened if direction < 0))

    def test_comet_spins_both_ways_and_paints(self) -> None:
        scene = BypassScene()
        scene.set_clickable(True)
        for phase in ("starting", "stopping"):
            scene.set_phase(phase)
            scene._flow_time = 0.4
            scene._pop_t = 0.3
            image = QPixmap(scene.sizeHint())
            image.fill(QColor(0, 0, 0, 0))
            scene.render(image)
            self.assertFalse(image.isNull())

    def test_paints_every_phase(self) -> None:
        scene = BypassScene()
        scene.set_clickable(True)
        scene.set_color("#6ccb5f")
        for phase in ("stopped", "failed", "autostart_pending", "starting", "running", "stopping"):
            scene.set_phase(phase)
            scene._pulse_phase = 0.7
            scene._flow_time = 1.3
            image = QPixmap(scene.sizeHint())
            image.fill(QColor(0, 0, 0, 0))
            scene.render(image)
            self.assertFalse(image.isNull())


class StatusHeroCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._enabled = True
        for module in (dot_module, scene_module, hero_module, mascot_module):
            patcher = mock.patch.object(module, "are_live_animations_enabled", side_effect=lambda: self._enabled)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _card(self):
        card = StatusHeroCard()
        scene = BypassScene(card)
        card.bind_scene(scene)
        card.resize(900, 104)
        self.addCleanup(card.deleteLater)
        return card, scene, scene.mascot()

    def test_card_takes_color_from_scene(self) -> None:
        card, scene, mascot = self._card()
        scene.set_color("#f5a623")
        scene.set_phase("starting")

        self.assertEqual(card.tint(), QColor("#f5a623"))

    def test_wave_plays_only_when_bypass_turns_on_in_view(self) -> None:
        card, scene, mascot = self._card()
        card.show()
        # Программа открылась, а Zapret уже работает: это не «только что включился».
        scene.set_phase("running")
        self.assertFalse(card.is_wave_playing())
        self.assertEqual(mascot.mood(), MOOD_IDLE)

        scene.set_phase("starting")
        self.assertFalse(card.is_wave_playing())
        scene.set_color("#6ccb5f")
        scene.set_phase("running")
        self.assertTrue(card.is_wave_playing())
        self.assertEqual(mascot.mood(), MOOD_HAPPY)

        card.hide()
        self.assertFalse(card.is_wave_playing())

    def test_no_wave_and_instant_color_without_live_animations(self) -> None:
        self._enabled = False
        card, scene, _mascot = self._card()
        card.show()
        scene.set_color("#f5a623")
        scene.set_phase("starting")
        scene.set_color("#6ccb5f")
        scene.set_phase("running")

        self.assertFalse(card.is_wave_playing())
        self.assertEqual(card.tint(), QColor("#6ccb5f"))

    def _flow_frame(self, scene, elapsed_ms: int) -> None:
        with mock.patch.object(scene._beat_clock, "elapsed", return_value=elapsed_ms):
            scene._on_beat_frame()

    def test_glow_shimmers_on_scene_frames_only_while_running(self) -> None:
        card, scene, _mascot = self._card()
        card.show()
        scene.set_phase("starting")
        self._flow_frame(scene, 400)
        self.assertFalse(card.is_shimmering())

        scene.set_phase("running")
        self._flow_frame(scene, 400)
        self.assertTrue(card.is_shimmering())
        self.assertAlmostEqual(card._shimmer_t, scene._flow_time)

        scene.set_phase("stopping")
        self.assertFalse(card.is_shimmering())

    def test_glow_lives_only_behind_the_scene(self) -> None:
        card, scene, _mascot = self._card()
        scene.move(16, 14)
        card.show()
        area = card.shimmer_rect()

        self.assertEqual(area.left(), 0)
        self.assertGreater(area.right(), scene.geometry().right())
        self.assertLess(area.right(), card.width() // 2)
        self.assertEqual(area.height(), card.height())

    def test_glow_stops_when_hidden_or_animations_are_off(self) -> None:
        card, scene, _mascot = self._card()
        card.show()
        scene.set_phase("running")
        self._flow_frame(scene, 400)
        self.assertTrue(card.is_shimmering())
        card.hide()
        self.assertFalse(card.is_shimmering())

        self._enabled = False
        card.show()
        self._flow_frame(scene, 800)
        self.assertFalse(card.is_shimmering())

    def test_glow_spots_are_drawn_once_and_reused(self) -> None:
        hero_module._SPOT_CACHE.clear()
        first = hero_module._spot_pixmap(QColor("#6ccb5f"), 120, 1.0)
        again = hero_module._spot_pixmap(QColor("#6ccb5f"), 120, 1.0)

        self.assertEqual(first.cacheKey(), again.cacheKey())
        self.assertEqual(len(hero_module._SPOT_CACHE), 1)
        self.assertEqual(first.width(), 240)

        card, scene, _mascot = self._card()
        card.show()
        scene.set_color("#6ccb5f")
        scene.set_phase("running")
        for elapsed in (400, 2400, 5200):
            self._flow_frame(scene, elapsed)
            image = QPixmap(card.size())
            image.fill(QColor(0, 0, 0, 0))
            card.render(image)
        # Два пятна — два оттенка, сколько бы кадров ни прошло.
        self.assertEqual(len(hero_module._SPOT_CACHE), 3)

    def test_narrow_card_puts_the_scene_on_its_own_stretched_row(self) -> None:
        from PyQt6.QtWidgets import QBoxLayout, QHBoxLayout

        card, scene, _mascot = self._card()
        layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, card)
        layout.addWidget(scene)
        layout.addLayout(QHBoxLayout(), 1)
        card.set_stacking_layout(layout)
        card.show()

        card.resize(hero_module.STACK_BELOW_WIDTH + 100, 104)
        QApplication.processEvents()
        self.assertFalse(card.is_stacked())
        self.assertFalse(scene.is_stretched())

        card.resize(hero_module.STACK_BELOW_WIDTH - 100, 200)
        QApplication.processEvents()
        self.assertTrue(card.is_stacked())
        self.assertTrue(scene.is_stretched())
        self.assertGreater(scene.width(), scene_module.SCENE_WIDTH)

        card.resize(hero_module.STACK_BELOW_WIDTH + 100, 104)
        QApplication.processEvents()
        self.assertFalse(card.is_stacked())
        self.assertLessEqual(scene.width(), scene_module.SCENE_WIDTH)

    def test_paints_tint_and_wave(self) -> None:
        card, scene, _mascot = self._card()
        card.show()
        scene.set_color("#6ccb5f")
        card._wave_t = 0.4
        image = QPixmap(card.size())
        image.fill(QColor(0, 0, 0, 0))
        card.render(image)
        self.assertFalse(image.isNull())


class MotionIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_keeps_label_like_pixmap_api(self) -> None:
        icon = MotionIcon(size=24)
        self.addCleanup(icon.deleteLater)
        self.assertTrue(icon.pixmap().isNull())
        pixmap = QPixmap(22, 22)
        icon.setPixmap(pixmap)
        self.assertFalse(icon.pixmap().isNull())

    def test_twinkle_timer_runs_only_while_visible(self) -> None:
        patches = _animations(True, motion_module)
        self.addCleanup(lambda: [p.stop() for p in patches])
        icon = MotionIcon(size=24)
        host = _host(self, icon)

        icon.set_idle_twinkle(9000)
        self.assertFalse(icon._twinkle_timer.isActive())

        host.show()
        self.assertTrue(icon._twinkle_timer.isActive())

        host.hide()
        self.assertFalse(icon._twinkle_timer.isActive())

        host.show()
        icon.set_idle_twinkle(0)
        self.assertFalse(icon._twinkle_timer.isActive())


class ControlTopSummaryAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._patches = _animations(True, summary_module, motion_module)
        self.addCleanup(lambda: [p.stop() for p in self._patches])
        self.summary = ControlTopSummaryWidget(language="ru", mode_value="Zapret 2")
        _host(self, self.summary).show()

    def test_first_profile_count_does_not_animate(self) -> None:
        self.summary.set_profile_count(70)

        self.assertFalse(self.summary._profile_roll.state() == self.summary._profile_roll.State.Running)
        self.assertEqual(self.summary.profiles_item._icon_label.gesture(), "")

    def test_changed_profile_count_rolls_and_bounces(self) -> None:
        self.summary.set_profile_count(70)
        self.summary.set_profile_count(74)

        roll = self.summary._profile_roll
        self.assertEqual(roll.state(), roll.State.Running)
        self.assertEqual(self.summary.profiles_item._icon_label.gesture(), GESTURE_BOUNCE)

        roll.setCurrentTime(roll.duration())
        self.assertEqual(self.summary.profiles_item._value_label.text(), "74 включено")

    def test_premium_star_glows_and_twinkles(self) -> None:
        star = self.summary.premium_item._icon_label
        self.assertIsNone(star._glow)
        self.assertEqual(star._twinkle_interval_ms, 0)

        self.summary.set_premium(PremiumDisplay(tier=TIER_FREE))
        self.assertIsNone(star._glow)
        self.assertGreater(star._twinkle_interval_ms, 0)

        self.summary.set_premium(PremiumDisplay(tier=TIER_ACTIVE))
        self.assertIsNotNone(star._glow)
        self.assertEqual(star.gesture(), GESTURE_BOUNCE)


class ControlTopSummaryTilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._enabled = True
        for module in (summary_module, motion_module, tile_module):
            patcher = mock.patch.object(module, "are_live_animations_enabled", side_effect=lambda: self._enabled)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.summary = ControlTopSummaryWidget(language="ru", mode_value="Zapret 2")
        self.host = _host(self, self.summary)
        self.host.resize(1200, 200)
        self.summary.set_preset("Default v1")
        self.summary.set_profile_count(70)

    ICONS = (
        ("simple:youtube:YT", "#FF0000"),
        ("simple:discord:DI", "#5865F2"),
        ("simple:telegram:TG", "#229ED9"),
    )

    def test_summary_items_are_tiles_and_all_of_them_lead_somewhere(self) -> None:
        self.host.show()
        QApplication.processEvents()
        items = (self.summary.preset_item, self.summary.profiles_item, self.summary.mode_item, self.summary.premium_item)

        self.assertEqual([item.is_clickable() for item in items], [True, True, True, True])
        self.assertEqual(self.summary.columns(), 4)
        self.assertEqual({item.height() for item in items}, {summary_module.TILE_ROW_HEIGHT})
        self.assertGreater(self.summary.preset_item.width(), self.summary.mode_item.width())

    def test_current_mode_tile_opens_the_mode_page(self) -> None:
        opened = []
        self.summary.modeClicked.connect(lambda: opened.append("mode"))
        self.summary.mode_item.clicked.emit()
        self.assertEqual(opened, ["mode"])

    def test_tiles_wrap_into_two_rows_in_a_narrow_window(self) -> None:
        self.host.resize(560, 300)
        self.host.show()
        QApplication.processEvents()

        self.assertEqual(self.summary.columns(), 2)
        self.assertEqual(self.summary.mode_item.y(), summary_module.TILE_ROW_HEIGHT + 12)

    def test_hidden_profiles_tile_frees_its_place(self) -> None:
        self.host.show()
        QApplication.processEvents()
        self.summary.set_profiles_visible(False)
        QApplication.processEvents()

        self.assertEqual(self.summary.columns(), 3)

    def test_long_preset_name_is_cut_with_ellipsis_and_kept_in_tooltip(self) -> None:
        long_name = "Очень длинное имя пресета для проверки обрезки " * 3
        self.host.show()
        QApplication.processEvents()
        self.summary.set_preset(long_name.strip())
        item = self.summary.preset_item

        self.assertTrue(item._value_label.text().endswith("…"))
        self.assertEqual(item.toolTip(), long_name.strip())
        self.assertIn(long_name.strip(), item.accessibleName())

        self.summary.set_preset("Default v1")
        self.assertEqual(item._value_label.text(), "Default v1")
        self.assertEqual(item.toolTip(), "")

    def test_profile_icons_pop_in_when_visible(self) -> None:
        self.host.show()
        QApplication.processEvents()
        self.summary.set_profile_icons(self.ICONS)
        item = self.summary.profiles_item

        self.assertEqual(item.icon_strip(), self.ICONS)
        self.assertEqual(item._strip_pop.state(), item._strip_pop.State.Running)
        self.assertEqual(item._strip_t, 0.0)

        # Тот же набор значков заново не анимируется.
        item._strip_pop.setCurrentTime(item._strip_pop.duration())
        self.summary.set_profile_icons(list(self.ICONS))
        self.assertNotEqual(item._strip_pop.state(), item._strip_pop.State.Running)
        self.assertEqual(item._strip_t, 1.0)

    def test_profile_icons_appear_at_once_when_hidden_or_animations_off(self) -> None:
        self.summary.set_profile_icons(self.ICONS)
        self.assertIsNone(self.summary.profiles_item._strip_pop)
        self.assertEqual(self.summary.profiles_item._strip_t, 1.0)

        self._enabled = False
        self.host.show()
        self.summary.set_profile_icons(self.ICONS[:2])
        self.assertIsNone(self.summary.profiles_item._strip_pop)
        self.assertEqual(self.summary.profiles_item._strip_t, 1.0)

    def test_strip_takes_only_the_free_room_and_paints(self) -> None:
        self.host.show()
        QApplication.processEvents()
        item = self.summary.profiles_item
        many = tuple((f"profile-initials:{index}", "#3B82F6") for index in range(12))
        self.summary.set_profile_icons(many)

        capacity = item.strip_capacity()
        self.assertGreater(capacity, 0)
        self.assertLessEqual(capacity, summary_module.STRIP_MAX_ICONS)

        item._strip_t = 0.5
        image = QPixmap(item.size())
        image.fill(QColor(0, 0, 0, 0))
        item.render(image)
        self.assertFalse(image.isNull())


class ControlTopSummaryPendingChangeTests(unittest.TestCase):
    """Пресет переключили на другой странице — изменение видно при возврате."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._patches = _animations(True, summary_module, motion_module)
        self.addCleanup(lambda: [p.stop() for p in self._patches])
        self.summary = ControlTopSummaryWidget(language="ru", mode_value="Zapret 2")
        self.host = _host(self, self.summary)
        self.host.show()
        self.summary.set_preset("Default v1")
        self.summary.set_profile_count(70)

    def test_change_while_hidden_plays_after_return(self) -> None:
        self.host.hide()
        self.summary.set_preset("general ALT3")
        self.summary.set_profile_count(None)
        self.summary.set_profile_count(64)

        self.assertTrue(self.summary.has_pending_changes())
        self.assertFalse(self.summary.preset_item.is_change_playing())

        self.host.show()
        self.assertTrue(self.summary._pending_timer.isActive())
        self.summary._pending_timer.stop()
        self.summary._play_pending_changes()

        self.assertFalse(self.summary.has_pending_changes())
        self.assertTrue(self.summary.preset_item.is_change_playing())
        self.assertTrue(self.summary.profiles_item.is_change_playing())
        roll = self.summary._profile_roll
        # Счёт идёт от числа, которое было до переключения, а не от «Проверяем...».
        self.assertEqual(roll.startValue(), 70.0)
        self.assertEqual(roll.endValue(), 64.0)

    def test_visible_change_plays_immediately_and_cleans_up(self) -> None:
        self.summary.set_preset("general ALT3")

        item = self.summary.preset_item
        self.assertTrue(item.is_change_playing())
        self.assertIsNotNone(item._value_label.graphicsEffect())

        pop = item._change_pop
        pop.setCurrentTime(pop.duration())
        self.assertFalse(item.is_change_playing())
        self.assertIsNone(item._value_label.graphicsEffect())

    def test_nothing_is_remembered_when_live_animations_are_off(self) -> None:
        for patcher in self._patches:
            patcher.stop()
        self._patches = _animations(False, summary_module, motion_module)
        self.host.hide()
        self.summary.set_preset("general ALT3")
        self.summary.set_profile_count(64)

        self.assertFalse(self.summary.has_pending_changes())


class LiveAnimationsSettingTests(unittest.TestCase):
    def test_live_animations_are_on_by_default_and_separate_from_winui(self) -> None:
        from settings import schema
        from settings.normalize import normalize_settings

        defaults = schema.default_appearance()
        self.assertTrue(defaults["live_animations_enabled"])
        self.assertFalse(defaults["animations_enabled"])
        normalized = normalize_settings({"appearance": {}})
        self.assertTrue(normalized["appearance"]["live_animations_enabled"])
        normalized = normalize_settings({"appearance": {"live_animations_enabled": False}})
        self.assertFalse(normalized["appearance"]["live_animations_enabled"])

    def test_policy_reads_warmed_value_and_defaults_to_on(self) -> None:
        from settings import appearance
        from ui.animation_policy import are_live_animations_enabled

        self.addCleanup(appearance.clear_warmed_live_animations_enabled_cache)
        appearance.clear_warmed_live_animations_enabled_cache()
        self.assertTrue(are_live_animations_enabled())
        appearance.store_warmed_live_animations_enabled(False)
        self.assertFalse(are_live_animations_enabled())


class SoftVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _make(self, enabled: bool = True):
        patches = _animations(enabled, soft_module)
        self.addCleanup(lambda: [p.stop() for p in patches])
        button = QWidget()
        host = _host(self, button)
        host.show()
        return button

    def test_hide_fades_out_before_hiding(self) -> None:
        button = self._make()

        self.assertTrue(set_visible_softly(button, False))

        # Пока затухает, кнопка ещё на экране, но цель уже «скрыта».
        self.assertFalse(button.isHidden())
        self.assertFalse(soft_visibility_target(button))
        self.assertFalse(set_visible_softly(button, False))

        anim = button._zapret_soft_visibility.anim
        anim.setCurrentTime(anim.duration())
        self.assertTrue(button.isHidden())
        self.assertIsNone(button.graphicsEffect())

    def test_show_waits_for_neighbour_then_fades_in(self) -> None:
        button = self._make()
        button.hide()

        self.assertTrue(set_visible_softly(button, True))
        state = button._zapret_soft_visibility
        self.assertTrue(button.isHidden())
        self.assertTrue(state.delay.isActive())

        state.delay.stop()
        soft_module._begin_fade_in(button)
        self.assertFalse(button.isHidden())
        self.assertIsNotNone(button.graphicsEffect())
        state.anim.setCurrentTime(state.anim.duration())
        self.assertIsNone(button.graphicsEffect())

    def test_switching_back_during_fade_out_keeps_widget(self) -> None:
        button = self._make()
        set_visible_softly(button, False)
        set_visible_softly(button, True)

        anim = button._zapret_soft_visibility.anim
        anim.setCurrentTime(anim.duration())
        self.assertFalse(button.isHidden())

    def test_immediate_when_live_animations_are_off(self) -> None:
        button = self._make(enabled=False)

        set_visible_softly(button, False)

        self.assertTrue(button.isHidden())
        self.assertIsNone(button.graphicsEffect())


if __name__ == "__main__":
    unittest.main()
