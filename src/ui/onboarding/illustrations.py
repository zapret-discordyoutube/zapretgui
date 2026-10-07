"""Анимированные схемы техник обхода для обучающего тура.

Схема одна на все техники: слева «Вы», в середине проверка у провайдера,
справа сайт. По дорожке между ними бегут пакеты-плашки. Сцена описывает,
какие плашки отправить, когда, и что с ними станет:

- pass — плашка доходит до сайта;
- blocked — проверка узнаёт имя и обрывает соединение;
- die — поддельная плашка проходит проверку и гаснет, не дойдя до сайта;
- discard — мусор доезжает до сайта вместе с данными, и сайт его отбрасывает.

Плашки едут слева направо, поэтому правее — значит раньше: правая плашка
первой попадает в проверку и первой приходит на сайт. Просвет между плашками
означает отдельные пакеты. Части одного пакета нарисованы слитно, одной
плашкой из двух половин с подписью «один пакет» (мусор seqovl в tcpseg).

Так видно главное: проверка провайдера видит одно, а сайт получает другое.

Схема оформлена как окно анализатора трафика: сетка, моноширинный шрифт, под
каждым пакетом его номер. Под дорожкой — журнал пакетов, как в настоящем
анализаторе: строка появляется, когда пакет вышел, и дописывает, что с ним
сделали проверка провайдера и сайт. Текст журнала стоит на месте, поэтому его
можно спокойно прочитать — в отличие от подписи на движущемся пакете.

Главное в схеме — момент проверки. Поэтому каждый пакет, дойдя до ТСПУ,
останавливается: вся дорожка замирает, пакет под лучом проверки увеличен, а
в журнале появляется её решение. Потом движение продолжается. Остановки
добавляют времени к кругу, но не меняют расстановку пакетов: внутри схемы
счёт идёт в «фазе сцены», которая на время остановки просто не растёт.
Сцены повторяются по кругу. Все переходы (появление реплики, вспышка у
сайта, падение подделок) заканчиваются до STATIC_PHASE: когда анимации в
системе выключены, рисуется этот неподвижный кадр с итогом.

В углу схемы есть кнопка паузы: анимацию можно остановить на любом кадре и
спокойно рассмотреть. На следующем шаге схема снова идёт сама.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

from PyQt6.QtCore import QElapsedTimer, QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget
from qfluentwidgets import FluentIcon, TransparentToolButton, isDarkTheme, themeColor

from ui.animation_policy import are_live_animations_enabled
from ui.fluent_widgets import set_tooltip


# За это время фаза сцены проходит полный круг, не считая остановок у проверки.
PERIOD_MS = 10000
# На столько дорожка замирает, когда пакет дошёл до ТСПУ: это главный момент схемы.
HOLD_MS = 1700
# Во столько раз увеличен пакет, пока его проверяют.
HOLD_SCALE = 1.22
FRAME_MS = 33
# Высота дорожки с узлами; ниже неё идёт журнал пакетов.
TRACK_HEIGHT = 178
LOG_HEADER_HEIGHT = 26
LOG_ROW_HEIGHT = 22
LOG_BOTTOM_PAD = 8
# С этой ширины журнал встаёт справа от дорожки, а не под ней: схема занимает
# всю ширину страницы, но дорожка не растягивается и пакеты не летят через весь экран.
WIDE_FROM = 980
WIDE_TRACK_SHARE = 0.56
# Высота схемы, пока сцена не выбрана: дорожка и журнал на два пакета.
ILLUSTRATION_HEIGHT = TRACK_HEIGHT + LOG_HEADER_HEIGHT + LOG_ROW_HEIGHT * 2 + LOG_BOTTOM_PAD
# Сколько доли круга плашка идёт от «Вы» до сайта.
TRAVEL = 0.45
# С этого места круга всё плавно гаснет перед повтором.
FADE_FROM = 0.9
# Неподвижный кадр, когда анимации выключены: всё уже дошло.
STATIC_PHASE = 0.86
# Просвет между соседними плашками на дорожке, px.
CHIP_GAP = 10.0
# Длительности переходов, доля круга.
POP = 0.05  # появление реплики, итога и красного креста
APPEAR = 0.08  # плашка выходит от «Вы»
ABSORB = 0.1  # хвост пути, на котором настоящая плашка входит в сайт
PULSE = 0.08  # кольцо вокруг сайта, когда всё дошло
SHAKE = 0.07  # плашка трясётся, упёршись в блок
DISCARD = 0.1  # сайт отбрасывает мусор
PAUSE_BUTTON_SIZE = 28

BLOCK_RED = QColor(232, 17, 35)
PASS_GREEN = QColor(60, 179, 113)
FAKE_AMBER = QColor(240, 160, 48)
JUNK_GREY = QColor(140, 140, 140)
# Шаг сетки фона и шрифты «анализатора трафика».
GRID_STEP = 26
MONO_FAMILIES = ["Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", "monospace"]
# Радиус углов узлов и плашек: прямоугольные, как блоки в сниффере.
NODE_RADIUS = 4.0
CHIP_RADIUS = 2.0


@dataclass(frozen=True, slots=True)
class Packet:
    text: str
    # Номер части в исходном сообщении: видно, в каком порядке их режут.
    part: str = ""
    # Пауза перед отправкой сверх обычного просвета, доля круга.
    delay: float = 0.0
    kind: str = "real"  # real | fake | junk | syn
    fate: str = "pass"  # pass | blocked | die | discard
    # Текст после проверки: у сайта лишнее выпадает (oob, syndata).
    text_after_gate: str = ""
    # Плашка слита со следующей в один пакет и едет в нём первой (мусор seqovl в tcpseg).
    glued_to_next: bool = False


@dataclass(frozen=True, slots=True)
class Scene:
    packets: tuple[Packet, ...]
    # Реплика проверки и какая плашка её вызывает (номер в packets).
    bubble_key: str
    bubble_trigger: int
    bubble_tone: str = "ok"  # ok | block | confused
    # Что в итоге собрал сайт; пусто — сайт ничего не получил.
    site_result: str = "youtube.com"
    # Сколько доли круга плашка идёт от «Вы» до сайта. В длинной сцене пакеты
    # едут быстрее: иначе последний не успевал бы дойти до конца круга.
    travel: float = TRAVEL


SCENES: dict[str, Scene] = {
    "blocked": Scene(
        packets=(Packet("youtube.com", fate="blocked"),),
        bubble_key="blocked",
        bubble_trigger=0,
        bubble_tone="block",
        site_result="",
    ),
    "fake": Scene(
        packets=(
            Packet("google.com", kind="fake", fate="die"),
            Packet("youtube.com", delay=0.06),
        ),
        bubble_key="fake",
        bubble_trigger=0,
    ),
    "multisplit": Scene(
        packets=(Packet("you", part="1"), Packet("tube.com", part="2")),
        bubble_key="unknown",
        bubble_trigger=0,
        bubble_tone="confused",
    ),
    "multidisorder": Scene(
        packets=(Packet("tube.com", part="2"), Packet("you", part="1")),
        bubble_key="unknown",
        bubble_trigger=0,
        bubble_tone="confused",
    ),
    # Порядок пакетов — как в lua/zapret-antidpi.lua: каждая настоящая часть
    # идёт между двумя своими поддельными копиями того же размера.
    "fakedsplit": Scene(
        packets=(
            Packet("q7z", kind="fake", fate="die"),
            Packet("you", part="1"),
            Packet("q7z", kind="fake", fate="die"),
            Packet("xr1k.zzz", kind="fake", fate="die"),
            Packet("tube.com", part="2"),
            Packet("xr1k.zzz", kind="fake", fate="die"),
        ),
        travel=0.34,
        bubble_key="which_real",
        bubble_trigger=1,
        bubble_tone="confused",
    ),
    # То же, но вторая часть со своими копиями уходит первой.
    "fakeddisorder": Scene(
        packets=(
            Packet("xr1k.zzz", kind="fake", fate="die"),
            Packet("tube.com", part="2"),
            Packet("xr1k.zzz", kind="fake", fate="die"),
            Packet("q7z", kind="fake", fate="die"),
            Packet("you", part="1"),
            Packet("q7z", kind="fake", fate="die"),
        ),
        travel=0.34,
        bubble_key="which_real",
        bubble_trigger=1,
        bubble_tone="confused",
    ),
    # Запрос режется вокруг имени сайта: начало, поддельное имя, настоящее
    # имя, снова поддельное, остаток запроса.
    "hostfakesplit": Scene(
        packets=(
            Packet("…", part="1"),
            Packet("abc.ru", kind="fake", fate="die"),
            Packet("youtube.com", part="2"),
            Packet("abc.ru", kind="fake", fate="die"),
            Packet("…", part="3"),
        ),
        travel=0.34,
        bubble_key="fake_host",
        bubble_trigger=1,
    ),
    "tcpseg": Scene(
        packets=(
            Packet("junk", kind="junk", fate="discard", glued_to_next=True),
            Packet("youtube.com"),
        ),
        bubble_key="junk",
        bubble_trigger=0,
    ),
    "oob": Scene(
        # Срочный байт по умолчанию встаёт в начало данных (urp=b в zapret-antidpi.lua).
        packets=(Packet("#youtube.com", text_after_gate="youtube.com"),),
        bubble_key="unknown",
        bubble_trigger=0,
        bubble_tone="confused",
    ),
    "syndata": Scene(
        packets=(
            Packet("SYN", kind="syn", text_after_gate="SYN"),
            Packet("youtube.com", delay=0.08),
        ),
        bubble_key="odd",
        bubble_trigger=0,
    ),
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _ease_out(t: float) -> float:
    t = _clamp01(t)
    return 1.0 - (1.0 - t) ** 3


def _ease_in(t: float) -> float:
    t = _clamp01(t)
    return t * t


def _ease_out_back(t: float) -> float:
    """Быстро вырастает с лёгким перелётом — «выскакивает»."""
    t = _clamp01(t)
    c1 = 1.70158
    return 1.0 + (c1 + 1.0) * (t - 1.0) ** 3 + c1 * (t - 1.0) ** 2


@dataclass(frozen=True, slots=True)
class _Layout:
    you_rect: QRectF
    gate_rect: QRectF
    site_rect: QRectF
    start_x: float
    end_x: float
    track_y: float

    @property
    def gate_x(self) -> float:
        return self.gate_rect.center().x()


@dataclass(frozen=True, slots=True)
class ChipFrame:
    """Где и как нарисовать одну плашку в данный момент."""

    index: int
    label: str
    kind: str
    x: float
    dy: float = 0.0
    alpha: float = 1.0
    scale: float = 1.0
    angle: float = 0.0
    # Плашка-начало слитого пакета: следующая плашка едет вплотную за ней.
    glued: bool = False
    # С какой стороны плашка слита с соседней: там край прямой, без скругления.
    flat: str = ""  # "" | left | right
    # Ширина плашки на дорожке, px: по ней считаются просветы.
    width: float = 0.0


@dataclass(frozen=True, slots=True)
class LogRow:
    """Строка журнала пакетов под дорожкой."""

    number: int
    label: str
    kind: str
    length: int
    # Что сделала проверка провайдера и что сделал сайт: (текст, тон).
    # Тон: wait — ещё не дошёл, ok, pass, drop, block, none.
    gate: tuple[str, str]
    site: tuple[str, str]
    # Строка проявляется, когда пакет выходит от «Вы».
    alpha: float = 1.0


@dataclass(frozen=True, slots=True)
class SceneTimes:
    starts: tuple[float, ...]
    # Когда проверка подаёт реплику (и, для блока, обрывает соединение).
    verdict: float
    # Когда сайт собрал итог; None — не соберёт.
    site_done: float | None
    # Когда всё на схеме замирает до следующего круга.
    settled: float


def _readable_text_on(fill: QColor) -> QColor:
    luminance = 0.2126 * fill.redF() + 0.7152 * fill.greenF() + 0.0722 * fill.blueF()
    return QColor(20, 20, 20) if luminance > 0.55 else QColor(250, 250, 250)


class TechniqueIllustration(QWidget):
    """Схема «Вы → проверка провайдера → сайт» с бегущими пакетами."""

    def __init__(self, parent: QWidget | None = None, *, tr_fn: Callable[[str, str], str]) -> None:
        super().__init__(parent)
        self._tr = tr_fn
        self._scene_key = ""
        self._phase = STATIC_PHASE
        # Пакет, остановленный у проверки (-1 — дорожка движется).
        self._held_packet = -1
        self._paused = False
        # С какого места круга идёт отсчёт после снятия с паузы, мс.
        self._clock_offset_ms = 0.0
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._on_tick)
        self.setFixedHeight(ILLUSTRATION_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Пауза — в правом верхнем углу: там на схеме пусто.
        self.pause_button = TransparentToolButton(FluentIcon.PAUSE, self)
        self.pause_button.setFixedSize(PAUSE_BUTTON_SIZE, PAUSE_BUTTON_SIZE)
        self.pause_button.setIconSize(QSize(12, 12))
        self.pause_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pause_button.clicked.connect(self.toggle_paused)
        self.pause_button.hide()
        self._sync_pause_button()

    # ── публичное ─────────────────────────────────────────────────────

    def scene_key(self) -> str:
        return self._scene_key

    def set_scene(self, key: str) -> None:
        key = key if key in SCENES else ""
        if key == self._scene_key:
            return
        self._scene_key = key
        # Высота схемы зависит от сцены: в журнале по строке на пакет.
        self.setFixedHeight(self._fit_height())
        self._restart()

    @staticmethod
    def scene_height(key: str) -> int:
        scene = SCENES.get(key)
        if scene is None:
            return ILLUSTRATION_HEIGHT
        return TRACK_HEIGHT + LOG_HEADER_HEIGHT + LOG_ROW_HEIGHT * len(scene.packets) + LOG_BOTTOM_PAD

    def is_animating(self) -> bool:
        return self._timer.isActive()

    def is_paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        """Остановить схему на текущем кадре или пустить дальше с него же."""
        paused = bool(paused)
        if paused == self._paused or not self._can_animate():
            return
        self._paused = paused
        if paused:
            self._timer.stop()
        else:
            self._clock_offset_ms = self.ms_at(self._phase)
            self._clock.start()
            self._timer.start()
        self._sync_pause_button()

    def toggle_paused(self) -> None:
        self.set_paused(not self._paused)

    def phase(self) -> float:
        return self._phase

    def set_phase(self, phase: float) -> None:
        """Кадр в заданный момент круга (для снимков и тестов)."""
        self._held_packet = -1
        self._phase = max(0.0, min(0.999, float(phase)))
        self.update()

    # ── жизненный цикл ────────────────────────────────────────────────

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self._restart()

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().hideEvent(event)
        self._timer.stop()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self.pause_button.move(int(self._track_width()) - self.pause_button.width(), 0)
        if event.oldSize().width() != event.size().width() and self.height() != self._fit_height():
            self.setFixedHeight(self._fit_height())

    def _can_animate(self) -> bool:
        return bool(self._scene_key) and self.isVisible() and are_live_animations_enabled()

    def _restart(self) -> None:
        """Новая схема или новый показ: круг идёт с начала, пауза снята."""
        animate = self._can_animate()
        self._paused = False
        self._clock_offset_ms = 0.0
        # Без анимаций кадр один и ставить на паузу нечего — кнопка не нужна.
        self.pause_button.setVisible(animate)
        self._sync_pause_button()
        if not animate:
            self._timer.stop()
            self._phase = STATIC_PHASE
            self.update()
            return
        self._phase = 0.0
        self._clock.start()
        self._timer.start()
        self.update()

    def hold_points(self) -> list[tuple[float, int]]:
        """Где дорожка замирает: (фаза сцены, номер пакета у проверки).

        Половины одного слитого пакета проверяются вместе — остановка одна.
        Пакет, который проверка не пропустила, стоит у неё и так.
        """
        scene = SCENES.get(self._scene_key)
        if scene is None:
            return []
        layout = self._layout()
        chips = self._chip_widths(scene, QFontMetrics(self._chip_font()))
        times = self._scene_times(scene, layout, chips)
        span = max(1.0, layout.end_x - layout.start_x)
        at_gate = (layout.gate_x - layout.start_x) / span
        points: list[tuple[float, int]] = []
        for index, packet in enumerate(scene.packets):
            if index > 0 and scene.packets[index - 1].glued_to_next:
                continue
            moment = times.verdict if packet.fate == "blocked" else times.starts[index] + scene.travel * at_gate
            if 0.0 < moment < FADE_FROM:
                points.append((moment, index))
        return sorted(points)

    def cycle_ms(self) -> float:
        """Сколько длится круг вместе с остановками у проверки."""
        return PERIOD_MS + HOLD_MS * len(self.hold_points())

    def phase_at(self, ms: float) -> tuple[float, int]:
        """Фаза сцены в момент ms от начала круга и номер пакета, который сейчас
        проверяют (-1 — дорожка движется)."""
        points = self.hold_points()
        rest = float(ms) % (PERIOD_MS + HOLD_MS * len(points))
        for moment, index in points:
            reach = moment * PERIOD_MS
            if rest < reach:
                break
            if rest < reach + HOLD_MS:
                return moment, index
            rest -= HOLD_MS
        return min(0.999, rest / PERIOD_MS), -1

    def ms_at(self, phase: float) -> float:
        """Момент круга, в который фаза сцены впервые равна phase."""
        return phase * PERIOD_MS + HOLD_MS * sum(1 for moment, _index in self.hold_points() if moment < phase)

    def _on_tick(self) -> None:
        elapsed = self._clock.elapsed() if self._clock.isValid() else 0
        self._phase, self._held_packet = self.phase_at(self._clock_offset_ms + elapsed)
        self.update()

    def _sync_pause_button(self) -> None:
        if self._paused:
            icon, text = FluentIcon.PLAY, self._tr("onboarding.scene.resume", "Продолжить анимацию")
        else:
            icon, text = FluentIcon.PAUSE, self._tr("onboarding.scene.pause", "Остановить анимацию")
        self.pause_button.setIcon(icon)
        self.pause_button.setAccessibleName(text)
        set_tooltip(self.pause_button, text)

    # ── отрисовка ─────────────────────────────────────────────────────

    def paintEvent(self, event):  # noqa: N802 (Qt override)
        _ = event
        scene = SCENES.get(self._scene_key)
        if scene is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            self._paint_scene(painter, scene, self._phase)
        finally:
            painter.end()

    def _palette(self) -> dict[str, QColor]:
        dark = isDarkTheme()
        return {
            "text": QColor(235, 235, 235) if dark else QColor(30, 30, 30),
            "muted": QColor(160, 160, 160) if dark else QColor(110, 110, 110),
            "node": QColor(255, 255, 255, 16) if dark else QColor(0, 0, 0, 10),
            "node_border": QColor(255, 255, 255, 40) if dark else QColor(0, 0, 0, 40),
            "track": QColor(255, 255, 255, 36) if dark else QColor(0, 0, 0, 36),
            "accent": QColor(themeColor()),
        }

    def _is_wide(self) -> bool:
        return self.width() >= WIDE_FROM

    def _track_width(self) -> float:
        """Ширина дорожки с узлами: в широкой схеме справа от неё стоит журнал."""
        width = float(self.width())
        return round(width * WIDE_TRACK_SHARE) if self._is_wide() else width

    def _layout(self) -> _Layout:
        width = self._track_width()
        node_w, gate_w = 88.0, 116.0
        track_y = 86.0
        you_rect = QRectF(4, track_y - 30, node_w, 60)
        site_rect = QRectF(width - 4 - node_w, track_y - 30, node_w, 60)
        gate_rect = QRectF(width / 2 - gate_w / 2, track_y - 42, gate_w, 84)
        return _Layout(you_rect, gate_rect, site_rect, you_rect.right() + 8, site_rect.left() - 8, track_y)

    def _fit_height(self) -> int:
        """Высота под нынешнюю ширину: журнал либо под дорожкой, либо справа от неё."""
        scene = SCENES.get(self._scene_key)
        rows = len(scene.packets) if scene is not None else 2
        log_height = LOG_HEADER_HEIGHT + LOG_ROW_HEIGHT * rows + LOG_BOTTOM_PAD
        return max(TRACK_HEIGHT, log_height + 8) if self._is_wide() else TRACK_HEIGHT + log_height

    def _mono_font(self, shrink: float, *, bold: bool = False) -> QFont:
        font = QFont(self.font())
        font.setFamilies(MONO_FAMILIES)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSizeF(max(7.0, font.pointSizeF() - shrink))
        font.setBold(bold)
        return font

    def _chip_font(self) -> QFont:
        return self._mono_font(0.0, bold=True)

    def _detail_font(self) -> QFont:
        return self._mono_font(1.0)

    def log_rows(self, phase: float) -> list[LogRow]:
        """Журнал пакетов в момент phase: что уже вышло и что с этим сделали."""
        scene = SCENES.get(self._scene_key)
        if scene is None:
            return []
        layout = self._layout()
        chips = self._chip_widths(scene, QFontMetrics(self._chip_font()))
        times = self._scene_times(scene, layout, chips)
        span = max(1.0, layout.end_x - layout.start_x)
        at_gate = (layout.gate_x - layout.start_x) / span
        # Подделка гаснет на первой половине пути от проверки к сайту.
        gone = at_gate + (1.0 - at_gate) * 0.55
        wait = ("…", "wait")
        rows: list[LogRow] = []
        for index, packet in enumerate(scene.packets):
            raw = (phase - times.starts[index]) / scene.travel
            if raw <= 0.0:
                continue
            passed_gate = raw >= at_gate
            if packet.fate == "blocked":
                gate = (self._tr("onboarding.scene.log.blocked", "узнал имя — блок"), "block") if phase >= times.verdict else wait
                site = ("—", "none")
            elif packet.fate == "die":
                gate = (self._tr("onboarding.scene.log.fooled", "принял за настоящий"), "ok") if passed_gate else wait
                # Подделка либо не доходит до сайта (короткий путь), либо он её
                # отбрасывает (подпись, номер, метка времени): сайту она не достаётся.
                site = (self._tr("onboarding.scene.log.not_taken", "не принят"), "drop") if raw >= gone else wait
            elif packet.fate == "discard":
                gate = (self._tr("onboarding.scene.log.passed", "пропустил"), "pass") if passed_gate else wait
                site = (self._tr("onboarding.scene.log.dropped", "отброшен"), "drop") if raw >= 1.0 else wait
            else:
                gate = (self._tr("onboarding.scene.log.passed", "пропустил"), "pass") if passed_gate else wait
                site = (self._tr("onboarding.scene.log.accepted", "принят"), "ok") if raw >= 1.0 else wait
            label = self._packet_label(packet, False)
            rows.append(
                LogRow(
                    number=index + 1,
                    label=label,
                    kind=packet.kind,
                    length=len(label.split(". ", 1)[-1]),
                    gate=gate,
                    site=site,
                    alpha=_ease_out(raw * scene.travel / APPEAR),
                )
            )
        return rows

    def _paint_log(self, painter: QPainter, phase: float, fade: float, colors) -> None:
        """Журнал под дорожкой: номер, пакет, длина, проверка провайдера, сайт."""
        if self._is_wide():
            # Широкая схема: журнал стоит справа от дорожки.
            left = self._track_width() + 28.0
            top = 6.0
        else:
            left = 0.0
            top = float(TRACK_HEIGHT)
        width = float(self.width()) - left
        painter.save()
        painter.translate(left, 0)
        font = self._detail_font()
        painter.setFont(font)
        columns = (10.0, 48.0, width * 0.38, width * 0.5, width * 0.8)
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        headers = (
            "№",
            self._tr("onboarding.scene.log.packet", "пакет"),
            self._tr("onboarding.scene.log.length", "длина"),
            self._tr("onboarding.scene.log.gate", "ТСПУ"),
            self._tr("onboarding.scene.log.site", "сайт"),
        )
        painter.setPen(colors["muted"])
        for x, text in zip(columns, headers):
            painter.drawText(QRectF(x, top, width - x, LOG_HEADER_HEIGHT), flags, text)
        painter.setPen(QPen(colors["track"], 1))
        painter.drawLine(QPointF(8, top + LOG_HEADER_HEIGHT - 1), QPointF(width - 8, top + LOG_HEADER_HEIGHT - 1))
        tones = {
            "wait": colors["muted"],
            "none": colors["muted"],
            "pass": colors["text"],
            "ok": PASS_GREEN,
            "drop": FAKE_AMBER,
            "block": BLOCK_RED,
        }
        marks = {"fake": " FAKE", "junk": " JUNK", "syn": " SYN"}
        for row in self.log_rows(phase):
            y = top + LOG_HEADER_HEIGHT + (row.number - 1) * LOG_ROW_HEIGHT
            painter.setOpacity(row.alpha * fade)
            cells = (
                (f"#{row.number}", colors["muted"]),
                (row.label + marks.get(row.kind, ""), FAKE_AMBER if row.kind == "fake" else colors["text"]),
                (str(row.length), colors["muted"]),
                (row.gate[0], tones[row.gate[1]]),
                (row.site[0], tones[row.site[1]]),
            )
            for x, (text, color) in zip(columns, cells):
                painter.setPen(color)
                painter.drawText(QRectF(x, y, width - x, LOG_ROW_HEIGHT), flags, text)
        painter.setOpacity(1.0)
        painter.restore()

    def _paint_grid(self, painter: QPainter, layout: "_Layout", colors) -> None:
        """Сетка фона и пунктирные оси трёх узлов."""
        grid = QColor(colors["track"])
        grid.setAlpha(max(8, grid.alpha() // 3))
        painter.setPen(QPen(grid, 1))
        # Сетка лежит только под дорожкой: журнал ниже читается на ровном фоне.
        width, height = int(self._track_width()), TRACK_HEIGHT - 6
        for x in range(GRID_STEP, width, GRID_STEP):
            painter.drawLine(x, 0, x, height)
        for y in range(GRID_STEP, height, GRID_STEP):
            painter.drawLine(0, y, width, y)

    def _chip_widths(self, scene: Scene, metrics: QFontMetrics) -> list[float]:
        return [self._chip_width(metrics, self._packet_label(packet, False)) for packet in scene.packets]

    @staticmethod
    def _blocked_x(layout: _Layout, chip_width: float) -> float:
        return layout.gate_rect.left() - chip_width / 2 - 6

    def scene_times(self, scene: Scene | None = None) -> SceneTimes:
        """Ключевые моменты круга для сцены (по умолчанию — текущей)."""
        scene = scene or SCENES[self._scene_key]
        layout = self._layout()
        chips = self._chip_widths(scene, QFontMetrics(self._chip_font()))
        return self._scene_times(scene, layout, chips)

    def _scene_times(self, scene: Scene, layout: _Layout, chips: list[float]) -> SceneTimes:
        span = max(1.0, layout.end_x - layout.start_x)
        starts = tuple(self._start_times(scene, chips, span))
        trigger = scene.packets[scene.bubble_trigger]
        if trigger.fate == "blocked":
            # Реплика и обрыв — ровно когда плашка упирается в проверку.
            target = self._blocked_x(layout, chips[scene.bubble_trigger])
        else:
            # Реплика — когда середина плашки в середине проверки.
            target = layout.gate_x
        verdict = starts[scene.bubble_trigger] + scene.travel * _clamp01((target - layout.start_x) / span)
        arrivals = [starts[i] + scene.travel for i, packet in enumerate(scene.packets) if packet.fate == "pass"]
        site_done = max(arrivals) if (scene.site_result and arrivals) else None
        ends = [verdict + max(POP, SHAKE)]
        for index, packet in enumerate(scene.packets):
            if packet.fate == "die":
                # Подделка гаснет на полпути от проверки до сайта.
                ends.append(starts[index] + scene.travel)
            elif packet.fate == "discard":
                ends.append(starts[index] + scene.travel + DISCARD)
        if site_done is not None:
            ends.append(site_done + max(POP, PULSE))
        return SceneTimes(starts=starts, verdict=verdict, site_done=site_done, settled=max(ends))

    def chip_frames(self, phase: float) -> list[ChipFrame]:
        """Плашки текущей сцены в момент phase (для тестов и снимков)."""
        scene = SCENES.get(self._scene_key)
        if scene is None:
            return []
        layout = self._layout()
        chips = self._chip_widths(scene, QFontMetrics(self._chip_font()))
        times = self._scene_times(scene, layout, chips)
        return self._chip_frames(scene, phase, layout, chips, times)

    def _chip_frames(
        self, scene: Scene, phase: float, layout: _Layout, chips: list[float], times: SceneTimes
    ) -> list[ChipFrame]:
        span = layout.end_x - layout.start_x
        frames: list[ChipFrame] = []
        for index, packet in enumerate(scene.packets):
            raw = (phase - times.starts[index]) / scene.travel
            if raw <= 0.0:
                continue
            x = layout.start_x + span * min(raw, 1.0)
            appear = _ease_out(raw * scene.travel / APPEAR)
            alpha, scale, dy, angle = appear, 0.85 + 0.15 * appear, 0.0, 0.0
            # Пакет слит, пока его начало не доехало до сайта.
            glued = packet.glued_to_next and index + 1 < len(scene.packets) and raw < 1.0
            behind_glued = index > 0 and scene.packets[index - 1].glued_to_next
            flat = ""
            if glued:
                flat = "left"
            elif behind_glued and phase < times.starts[index - 1] + scene.travel:
                flat = "right"
            if glued or behind_glued:
                scale = 1.0  # половины одного пакета не расходятся при появлении
            if behind_glued:
                alpha = 1.0  # вторая половина выезжает из-за «Вы» сразу плотной, как первая
            if packet.fate == "blocked":
                wall = self._blocked_x(layout, chips[index])
                if x >= wall:
                    x = wall
                    shake = _clamp01((phase - times.verdict) / SHAKE)
                    if shake < 1.0:
                        x += math.sin(shake * math.pi * 5) * 5.0 * (1.0 - shake)
            elif packet.fate == "die" and x > layout.gate_x:
                # Подделка проседает, кренится и гаснет между проверкой и сайтом.
                gone = _ease_in((x - layout.gate_x) / max(1.0, (layout.end_x - layout.gate_x) * 0.55))
                alpha *= 1.0 - gone
                dy, scale, angle = 12.0 * gone, scale * (1.0 - 0.2 * gone), 14.0 * gone
            elif packet.fate == "discard" and raw >= 1.0:
                # Мусор приехал первым, и сайт его отбрасывает: он падает с дорожки
                # у входа, а данные следом входят внутрь.
                t = _clamp01((phase - times.starts[index] - scene.travel) / DISCARD)
                drop = _ease_out(t)
                alpha *= 1.0 - _ease_in(t)
                dy, scale, angle = 30.0 * drop, scale * (1.0 - 0.15 * t), 18.0 * drop
            elif packet.fate == "pass":
                if raw >= 1.0:
                    continue  # дошла — дальше её показывает итог у сайта
                absorb = _clamp01((raw - (1.0 - ABSORB)) / ABSORB)
                alpha *= 1.0 - _ease_in(absorb)
                scale *= 1.0 - 0.3 * absorb
            if alpha <= 0.01:
                continue
            label = self._packet_label(packet, x > layout.gate_x)
            frames.append(ChipFrame(index, label, packet.kind, x, dy, alpha, scale, angle, glued, flat, chips[index]))
        return frames

    def _paint_scene(self, painter: QPainter, scene: Scene, phase: float) -> None:
        colors = self._palette()
        layout = self._layout()
        chip_font = self._chip_font()
        metrics = QFontMetrics(chip_font)
        chips = self._chip_widths(scene, metrics)
        times = self._scene_times(scene, layout, chips)
        frames = self._chip_frames(scene, phase, layout, chips, times)
        fade = 1.0 if phase < FADE_FROM else max(0.0, 1.0 - (phase - FADE_FROM) / (1.0 - FADE_FROM))
        blocked = 0.0 if scene.site_result else _ease_out((phase - times.verdict) / POP)
        done = 0.0 if times.site_done is None else _ease_out((phase - times.site_done) / POP)

        self._paint_grid(painter, layout, colors)

        # Дорожка.
        painter.setPen(QPen(colors["track"], 2, Qt.PenStyle.DashLine))
        painter.drawLine(
            QPointF(layout.you_rect.right(), layout.track_y), QPointF(layout.site_rect.left(), layout.track_y)
        )

        self._paint_node(painter, layout.you_rect, self._tr("onboarding.scene.you", "Вы"), colors, None)
        # Подпись провайдера — в верхней части блока: плашки едут ниже и её не закрывают.
        self._paint_node(
            painter,
            layout.gate_rect,
            self._tr("onboarding.scene.provider", "ТСПУ"),
            colors,
            BLOCK_RED if blocked > 0.0 else None,
            tint_strength=blocked * fade,
            text_rect=QRectF(layout.gate_rect.left(), layout.gate_rect.top() + 3, layout.gate_rect.width(), 22),
        )
        self._paint_scan(painter, layout, frames, chips, phase, colors)
        painter.setPen(colors["muted"])
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, small.pointSizeF() - 1.5))
        painter.setFont(small)
        painter.drawText(
            QRectF(layout.gate_rect.left(), layout.gate_rect.bottom() + 2, layout.gate_rect.width(), 16),
            Qt.AlignmentFlag.AlignCenter,
            self._tr("onboarding.scene.check", "проверка"),
        )
        self._paint_node(
            painter,
            layout.site_rect,
            self._tr("onboarding.scene.site", "Сайт"),
            colors,
            PASS_GREEN if done > 0.0 else None,
            tint_strength=done * fade,
        )
        if times.site_done is not None:
            self._paint_pulse(painter, layout.site_rect, (phase - times.site_done) / PULSE)

        # Плашки выезжают из-за края «Вы», а не рисуются поверх него.
        painter.save()
        painter.setClipRect(QRectF(layout.you_rect.right() + 1, 0, self.width(), self.height()))
        painter.setFont(chip_font)
        held = self._held_packet
        if held >= 0:
            # Пакет под лучом проверки увеличен (и его слитая половина тоже):
            # это тот момент, ради которого схема останавливается.
            together = {held, held + 1} if scene.packets[held].glued_to_next else {held}
            frames = [
                replace(frame, scale=frame.scale * HOLD_SCALE) if frame.index in together else frame
                for frame in frames
            ]
            # Увеличенный пакет рисуется последним — поверх соседей.
            frames.sort(key=lambda frame: frame.index in together)
        for frame in frames:
            self._paint_chip(painter, metrics, frame, layout.track_y, frame.alpha * fade, colors)
        # Под пакетом — только его номер: по нему пакет находят в журнале ниже.
        painter.setFont(self._detail_font())
        glued = {frame.index for frame in frames if frame.glued}
        for frame in frames:
            # У слитых в один пакет плашек своя общая подпись «один пакет».
            if frame.index in glued or frame.index - 1 in glued:
                continue
            painter.setOpacity(frame.alpha * fade)
            painter.setPen(FAKE_AMBER if frame.kind == "fake" else colors["muted"])
            painter.drawText(
                QRectF(frame.x - 30, layout.track_y + frame.dy + 17, 60, 16),
                Qt.AlignmentFlag.AlignCenter,
                f"#{frame.index + 1}",
            )
        painter.setOpacity(1.0)
        painter.restore()

        # Реплика проверки.
        if phase >= times.verdict:
            self._paint_bubble(painter, scene, layout.gate_rect, colors, fade, (phase - times.verdict) / POP)

        # Итог у сайта.
        if done > 0.0:
            painter.setFont(chip_font)
            self._paint_result(painter, metrics, scene.site_result, layout.site_rect, colors, fade * done, done)
        if blocked > 0.0:
            self._paint_cross(
                painter, QPointF(layout.gate_rect.left() - 4, layout.track_y), fade, (phase - times.verdict) / POP
            )
        self._paint_log(painter, phase, fade, colors)

    @staticmethod
    def _start_times(scene: Scene, chips: list[float], track: float) -> list[float]:
        """Когда отправить каждую плашку, чтобы соседние не налезали друг на друга."""
        starts: list[float] = []
        moment = 0.0
        for index, packet in enumerate(scene.packets):
            previous = scene.packets[index - 1] if index else None
            if previous is not None:
                # Отдельные пакеты идут с просветом, части одного пакета — вплотную.
                gap = chips[index - 1] / 2 + chips[index] / 2
                if not previous.glued_to_next:
                    gap += CHIP_GAP
                moment += gap / max(1.0, track) * scene.travel
            moment += packet.delay
            starts.append(moment)
        return starts

    def _packet_label(self, packet: Packet, past_gate: bool) -> str:
        text = packet.text_after_gate if (past_gate and packet.text_after_gate) else packet.text
        if packet.kind == "junk":
            return self._tr("onboarding.scene.junk", "мусор")
        if packet.kind == "syn" and not past_gate:
            return self._tr("onboarding.scene.syn_data", "SYN + данные")
        return f"{packet.part}. {text}" if packet.part else text

    @staticmethod
    def _chip_width(metrics: QFontMetrics, text: str) -> float:
        return float(metrics.horizontalAdvance(text) + 20)

    def _paint_node(
        self,
        painter: QPainter,
        rect: QRectF,
        text: str,
        colors,
        tint: QColor | None,
        *,
        tint_strength: float = 1.0,
        text_rect: QRectF | None = None,
    ) -> None:
        fill = QColor(colors["node"])
        border = QColor(colors["node_border"])
        if tint is not None:
            fill = self._mix(fill, QColor(tint.red(), tint.green(), tint.blue(), 46), tint_strength)
            border = self._mix(border, QColor(tint), tint_strength)
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, NODE_RADIUS, NODE_RADIUS)
        painter.setPen(colors["text"])
        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(text_rect or rect, Qt.AlignmentFlag.AlignCenter, text)

    @staticmethod
    def _mix(a: QColor, b: QColor, k: float) -> QColor:
        k = _clamp01(k)
        return QColor(
            round(a.red() + (b.red() - a.red()) * k),
            round(a.green() + (b.green() - a.green()) * k),
            round(a.blue() + (b.blue() - a.blue()) * k),
            round(a.alpha() + (b.alpha() - a.alpha()) * k),
        )

    def _paint_scan(self, painter, layout: _Layout, frames, chips, phase: float, colors) -> None:
        """Пока плашка внутри проверки, блок подсвечен и по нему бегает луч."""
        gate = layout.gate_rect
        reach = gate.width() / 2
        glow = 0.0
        for frame in frames:
            near = 1.0 - abs(frame.x - layout.gate_x) / (reach + chips[frame.index] / 2)
            glow = max(glow, _clamp01(near) * frame.alpha)
        if glow <= 0.01:
            return
        accent = QColor(colors["accent"])
        painter.save()
        fill = QColor(accent)
        fill.setAlpha(round(34 * glow))
        border = QColor(accent)
        border.setAlpha(round(200 * glow))
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(fill)
        painter.drawRoundedRect(gate, NODE_RADIUS, NODE_RADIUS)
        sweep = 0.5 - 0.5 * math.cos(phase * math.tau * 9)
        x = gate.left() + 10 + (gate.width() - 20) * sweep
        beam = QLinearGradient(x, gate.top() + 22, x, gate.bottom() - 6)
        edge = QColor(accent)
        edge.setAlpha(0)
        mid = QColor(accent)
        mid.setAlpha(round(220 * glow))
        beam.setColorAt(0.0, edge)
        beam.setColorAt(0.5, mid)
        beam.setColorAt(1.0, edge)
        painter.setPen(QPen(QBrush(beam), 2))
        painter.drawLine(QPointF(x, gate.top() + 22), QPointF(x, gate.bottom() - 6))
        painter.restore()

    def _paint_pulse(self, painter, rect: QRectF, t: float) -> None:
        """Кольцо расходится от сайта, когда он собрал итог."""
        if not 0.0 < t < 1.0:
            return
        grow = 12.0 * _ease_out(t)
        ring = QColor(PASS_GREEN)
        ring.setAlpha(round(170 * (1.0 - t)))
        painter.save()
        painter.setPen(QPen(ring, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(-grow, -grow, grow, grow), 10 + grow, 10 + grow)
        painter.restore()

    def _paint_one_packet_caption(self, painter, frame: ChipFrame, chips, layout: _Layout, colors, fade) -> None:
        """Подпись под слитой плашкой: мусор и данные — один пакет."""
        right = frame.x + chips[frame.index] / 2
        left = frame.x - chips[frame.index] / 2 - chips[frame.index + 1]
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        painter.save()
        # Гаснет на подъезде к сайту: там пакет распадается.
        painter.setOpacity(frame.alpha * fade * _clamp01((layout.end_x - frame.x) / 24.0))
        painter.setFont(font)
        painter.setPen(colors["muted"])
        painter.drawText(
            QRectF(left, layout.track_y + 14, right - left, 16),
            Qt.AlignmentFlag.AlignCenter,
            self._tr("onboarding.scene.one_packet", "один пакет"),
        )
        painter.restore()

    @staticmethod
    def _chip_shape(rect: QRectF, radius: float, flat: str) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        if flat:
            half = QRectF(rect)
            if flat == "left":
                half.setRight(rect.center().x())
            else:
                half.setLeft(rect.center().x())
            square = QPainterPath()
            square.addRect(half)
            path = path.united(square)
        return path

    def _paint_chip(self, painter, metrics, frame: ChipFrame, track_y: float, alpha: float, colors) -> None:
        text, kind = frame.label, frame.kind
        width = self._chip_width(metrics, text)
        rect = QRectF(-width / 2, -14, width, 28)
        painter.save()
        painter.translate(frame.x, track_y + frame.dy)
        painter.rotate(frame.angle)
        painter.scale(frame.scale, frame.scale)
        painter.setOpacity(alpha)
        if kind == "real" or kind == "syn":
            fill = QColor(colors["accent"])
            # Мягкое свечение под настоящей плашкой.
            glow = QColor(fill)
            glow.setAlpha(50)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            halo = rect.adjusted(-3, -3, 3, 3)
            if frame.flat == "right":
                halo.setRight(rect.right())
            elif frame.flat == "left":
                halo.setLeft(rect.left())
            painter.drawPath(self._chip_shape(halo, CHIP_RADIUS + 3, frame.flat))
            painter.setBrush(fill)
            painter.drawPath(self._chip_shape(rect, CHIP_RADIUS, frame.flat))
            text_color = _readable_text_on(fill)
        else:
            base = FAKE_AMBER if kind == "fake" else JUNK_GREY
            fill = QColor(base)
            fill.setAlpha(60)
            painter.setBrush(fill)
            painter.setPen(QPen(base, 1.5, Qt.PenStyle.DashLine))
            painter.drawPath(self._chip_shape(rect, CHIP_RADIUS, frame.flat))
            text_color = colors["text"]
        if "#" in text:
            self._paint_oob_text(painter, metrics, text, rect, text_color)
        else:
            painter.setPen(text_color)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def _paint_oob_text(self, painter, metrics, text, rect: QRectF, text_color: QColor) -> None:
        """Лишний «срочный» байт подсвечен красным внутри имени."""
        x = rect.center().x() - metrics.horizontalAdvance(text) / 2
        baseline = rect.center().y() + (metrics.ascent() - metrics.descent()) / 2
        for char in text:
            painter.setPen(BLOCK_RED if char == "#" else text_color)
            painter.drawText(QPointF(x, baseline), char)
            x += metrics.horizontalAdvance(char)

    def _paint_bubble(self, painter, scene: Scene, gate_rect: QRectF, colors, fade: float, t: float) -> None:
        text = self._tr(f"onboarding.scene.bubble.{scene.bubble_key}", scene.bubble_key)
        tone = {"block": BLOCK_RED, "confused": FAKE_AMBER}.get(scene.bubble_tone, PASS_GREEN)
        font = QFont(self.font())
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1.0))
        painter.save()
        painter.setOpacity(fade * _ease_out(t))
        painter.setFont(font)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(text) + 18
        rect = QRectF(gate_rect.center().x() - width / 2, 2, width, 24)
        rect.moveLeft(max(0.0, min(rect.left(), self.width() - width)))
        tip_x = gate_rect.center().x()
        # Реплика выскакивает из блока проверки: растёт от кончика хвостика.
        grow = 0.6 + 0.4 * _ease_out_back(t)
        anchor = QPointF(tip_x, rect.bottom() + 6)
        painter.translate(anchor)
        painter.scale(grow, grow)
        painter.translate(-anchor)
        fill = QColor(tone)
        fill.setAlpha(40)
        painter.setBrush(fill)
        painter.setPen(QPen(tone, 1.2))
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        path.moveTo(tip_x - 5, rect.bottom())
        path.lineTo(tip_x, rect.bottom() + 6)
        path.lineTo(tip_x + 5, rect.bottom())
        painter.drawPath(path.simplified())
        painter.setPen(colors["text"])
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def _paint_result(self, painter, metrics, text, site_rect: QRectF, colors, alpha: float, t: float) -> None:
        label = f"✓ {text}"
        width = self._chip_width(metrics, label)
        # Итог выезжает снизу вверх под сайтом.
        rect = QRectF(site_rect.right() - width, site_rect.bottom() + 20 + 8 * (1.0 - t), width, 24)
        painter.save()
        painter.setOpacity(alpha)
        fill = QColor(PASS_GREEN)
        fill.setAlpha(50)
        painter.setBrush(fill)
        painter.setPen(QPen(PASS_GREEN, 1.2))
        painter.drawRoundedRect(rect, 7, 7)
        painter.setPen(colors["text"])
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()

    def _paint_cross(self, painter, center: QPointF, fade: float, t: float) -> None:
        size = 9.0 * _ease_out_back(t)
        if size <= 0.1:
            return
        painter.save()
        painter.setOpacity(fade * _ease_out(t))
        painter.setPen(QPen(BLOCK_RED, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        x, y = center.x(), center.y() + 30
        painter.drawLine(QPointF(x - size, y - size), QPointF(x + size, y + size))
        painter.drawLine(QPointF(x - size, y + size), QPointF(x + size, y - size))
        painter.restore()


__all__ = ["ILLUSTRATION_HEIGHT", "SCENES", "ChipFrame", "SceneTimes", "TechniqueIllustration"]
