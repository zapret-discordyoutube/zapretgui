"""Оверлей обучающего тура.

Как это выглядит: окно под оверлеем затемняется, над нужной кнопкой или
пунктом меню вырезается светлое «окошко» (прожектор) с пульсирующей
обводкой акцентного цвета. Рядом стоит карточка с текстом на размытом
снимке окна и кнопками «Назад / Далее / Пропустить». При переходе
к следующему шагу прожектор и карточка плавно переезжают на новое место.

Как устроено: оверлей — обычный дочерний виджет главного окна, отдельного
окна Windows он не создаёт. Покрывает всё окно ниже заголовка, чтобы
кнопки «свернуть» и «закрыть» оставались доступны. Движение считает
собственный таймер кадров: каждый кадр заново меряет, где сейчас цель,
поэтому прожектор не отстаёт при прокрутке и изменении размера окна.
QVariantAnimation и QPropertyAnimation не используются: при выключенных
анимациях WinUI общий fallback обнуляет их длительность. Выключен
переключатель «живых» анимаций — всё сразу встаёт на место без
пульсации.

Фильтров событий на окно оверлей не ставит, сигналы темы не слушает:
цвета берутся в момент отрисовки, а qfluentwidgets-подписи сами следят
за темой.
"""

from __future__ import annotations

import math
import re

from PyQt6 import sip
from PyQt6.QtCore import QElapsedTimer, QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient, QShortcut
from PyQt6.QtWidgets import QApplication, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    HyperlinkButton,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TitleLabel,
    TransparentPushButton,
    isDarkTheme,
    themeColor,
)

from app.ui_texts import tr as tr_catalog
from log.log import log
from ui.animation_policy import are_live_animations_enabled
from ui.onboarding.blur import blur_pixmap
from ui.onboarding.illustrations import TechniqueIllustration
from ui.onboarding.steps import (
    TOUR_SUBPAGE_PARENTS,
    TourContext,
    TourStep,
    TourTarget,
    is_alive_widget,
    is_target_shown,
    is_widget_shown,
    target_widget,
)
from ui.widgets.fluent_item_tooltip import FluentItemToolTipController


OVERLAY_OBJECT_NAME = "onboardingTourOverlay"

FRAME_MS = 16
IDLE_FRAME_MS = 33
STATIC_FRAME_MS = 120
MOTION_TAU_MS = 90.0
FADE_MS = 260.0
CONTENT_FADE_MS = 240.0
PULSE_PERIOD_MS = 1700.0
# Украшения карточки: прогресс сверху и точки шагов.
PROGRESS_TAU_MS = 180.0
DOTS_TAU_MS = 110.0
SHINE_PERIOD_MS = 2600.0
SHINE_SHARE = 0.45  # доля периода, пока по полоске бежит блик
FLASH_MS = 450.0
BREATH_PERIOD_MS = 1800.0
TOP_STRIP_HEIGHT = 3.0
DECOR_REGION_HEIGHT = 18

CARD_WIDTH = 500
HERO_CARD_WIDTH = 620
CARD_MARGIN = 16
CARD_GAP = 20
CARD_RADIUS = 12.0
HOLE_PADDING = 6.0
HOLE_RADIUS = 10.0
DIM_ALPHA = 140
HERO_DIM_ALPHA = 175
BLUR_DELAY_MS = 280
BLUR_DELAY_AFTER_PAGE_MS = 520


def _lerp(a: float, b: float, k: float) -> float:
    return a + (b - a) * k


def _lerp_rect(a: QRectF, b: QRectF, k: float) -> QRectF:
    return QRectF(
        _lerp(a.x(), b.x(), k),
        _lerp(a.y(), b.y(), k),
        _lerp(a.width(), b.width(), k),
        _lerp(a.height(), b.height(), k),
    )


def _rect_distance(a: QRectF, b: QRectF) -> float:
    return max(
        abs(a.x() - b.x()),
        abs(a.y() - b.y()),
        abs(a.width() - b.width()),
        abs(a.height() - b.height()),
    )


_PLACEHOLDER = re.compile(r"\{(\w+)\}")


_OPTION = re.compile(r"--[\w][\w-]*|--…")


def _unbreakable_options(text: str) -> str:
    """Опции вида --lua-desync не рвутся при переносе строки.

    Обычный дефис разрешает перенос, и «--» оставалось на одной строке,
    а «lua-desync» уезжало на другую. Неразрывный дефис выглядит так же.
    """
    return _OPTION.sub(lambda match: match.group(0).replace("-", "\u2011"), str(text or ""))


def _fill_placeholders(text: str, values: dict) -> str:
    """{имя} → значение со страницы; нет значения — прочерк, текст не ломается."""
    return _PLACEHOLDER.sub(lambda match: str(values.get(match.group(1)) or "—"), str(text or ""))


class _ProgressDots(QWidget):
    """Точки прогресса: текущий шаг — вытянутая акцентная «таблетка».

    По точке можно нажать и перейти на любой шаг. Когда шагов много, точки
    ужимаются под ширину карточки, а не вылезают за неё.
    """

    stepClicked = pyqtSignal(int)

    DOT = 6.0
    ACTIVE = 18.0
    SPACING = 6.0
    MIN_DOT = 3.0
    MIN_SPACING = 2.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._count = 0
        self._current = 0
        # Плавающий «текущий шаг»: таблетка перетекает к нему, а не прыгает.
        self._position = 0.0
        self._glow = 0.0
        self._hover = -1
        self._titles: list[str] = []
        self._tooltip = FluentItemToolTipController(self)
        self.setFixedHeight(16)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_progress(self, current: int, count: int, titles: list[str] | None = None) -> None:
        count = max(0, int(count))
        if count != self._count:
            # Новый тур или другое число шагов — встаём сразу, без перетекания.
            self._position = float(max(0, int(current)))
        self._current = max(0, int(current))
        self._count = count
        if titles is not None:
            self._titles = list(titles)
        self.update()

    def position(self) -> float:
        return self._position

    def advance(self, dt: float, animated: bool) -> bool:
        """Шаг анимации; True — таблетка ещё в пути."""
        target = float(self._current)
        if not animated:
            self._position = target
            return False
        self._glow = (self._glow + dt / BREATH_PERIOD_MS) % 1.0
        delta = target - self._position
        if abs(delta) < 0.004:
            self._position = target
            return False
        self._position += delta * (1.0 - math.exp(-dt / DOTS_TAU_MS))
        return True

    def dot_rects(self) -> list[QRectF]:
        """Где нарисована каждая точка; ужимает шаг, если все не влезают."""
        if not self._count:
            return []
        dot, spacing = self.DOT, self.SPACING
        others = self._count - 1
        natural = others * (dot + spacing) + self.ACTIVE
        available = float(max(1, self.width()))
        if others and natural > available:
            scale = max(0.0, (available - self.ACTIVE) / (others * (dot + spacing)))
            dot = max(self.MIN_DOT, dot * scale)
            spacing = max(self.MIN_SPACING, spacing * scale)
        rects: list[QRectF] = []
        x = 0.0
        y = (self.height() - dot) / 2
        for index in range(self._count):
            # Чем ближе к плавающему текущему шагу, тем шире: на полпути
            # обе соседние точки наполовину таблетки — она «перетекает».
            share = max(0.0, 1.0 - abs(index - self._position))
            width = dot + (self.ACTIVE - dot) * share
            rects.append(QRectF(x, y, width, dot))
            x += width + spacing
        return rects

    def index_at(self, x: float) -> int:
        rects = self.dot_rects()
        if not rects:
            return -1
        best = min(range(len(rects)), key=lambda index: abs(rects[index].center().x() - x))
        rect = rects[best]
        tolerance = max(4.0, rect.width())
        return best if rect.left() - tolerance <= x <= rect.right() + tolerance else -1

    def paintEvent(self, event):  # noqa: N802 (Qt override)
        _ = event
        if not self._count:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        muted = QColor(255, 255, 255, 70) if isDarkTheme() else QColor(0, 0, 0, 60)
        accent = QColor(themeColor())
        rects = self.dot_rects()
        # Мягкий пульсирующий ореол вокруг таблетки.
        if rects:
            low = max(0, min(len(rects) - 1, int(math.floor(self._position))))
            high = min(len(rects) - 1, low + 1)
            k = self._position - low
            center = rects[low].center().x() * (1.0 - k) + rects[high].center().x() * k
            breath = 0.5 + 0.5 * math.sin(self._glow * 2 * math.pi)
            halo = QColor(accent)
            halo.setAlpha(int(34 + 30 * breath))
            painter.setBrush(halo)
            half_width = self.ACTIVE / 2 + 3 + 1.5 * breath
            half_height = rects[low].height() / 2 + 3 + 1.5 * breath
            painter.drawRoundedRect(
                QRectF(center - half_width, self.height() / 2 - half_height, 2 * half_width, 2 * half_height),
                half_height,
                half_height,
            )
        for index, rect in enumerate(rects):
            if index == self._hover and index != self._current:
                rect = rect.adjusted(-1.5, -1.5, 1.5, 1.5)
            # Точки закрашиваются по мере того, как до них доходит таблетка.
            fill = max(0.0, min(1.0, self._position - index + 1.0))
            if index == self._hover:
                fill = 1.0
            color = QColor(
                int(muted.red() + (accent.red() - muted.red()) * fill),
                int(muted.green() + (accent.green() - muted.green()) * fill),
                int(muted.blue() + (accent.blue() - muted.blue()) * fill),
                int(muted.alpha() + (255 - muted.alpha()) * fill),
            )
            painter.setBrush(color)
            radius = rect.height() / 2
            painter.drawRoundedRect(rect, radius, radius)
        painter.end()

    def mouseMoveEvent(self, event):  # noqa: N802 (Qt override)
        index = self.index_at(event.position().x())
        if index != self._hover:
            self._hover = index
            self.update()
            if 0 <= index < len(self._titles):
                self._tooltip.show_text(f"{index + 1}. {self._titles[index]}", event.globalPosition().toPoint())
            else:
                self._tooltip.hide()
        event.accept()

    def leaveEvent(self, event):  # noqa: N802 (Qt override)
        super().leaveEvent(event)
        if self._hover != -1:
            self._hover = -1
            self.update()

    def mousePressEvent(self, event):  # noqa: N802 (Qt override)
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802 (Qt override)
        event.accept()
        if event.button() != Qt.MouseButton.LeftButton:
            return
        index = self.index_at(event.position().x())
        if index >= 0:
            self.stepClicked.emit(index)


class _TourCard(QWidget):
    """Карточка с текстом шага на размытом снимке окна."""

    def __init__(self, overlay: "OnboardingOverlay") -> None:
        super().__init__(overlay)
        self._overlay = overlay
        # Прогресс тура в полоске сверху: доля, блик, вспышка, дыхание.
        self._progress = 0.0
        self._progress_target = 0.0
        self._shine = 0.0
        self._flash = 0.0
        self._breath = 0.0
        self.setObjectName("onboardingTourCard")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.content = QWidget(self)
        self.content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.content_effect = QGraphicsOpacityEffect(self.content)
        self.content_effect.setOpacity(0.0)
        self.content.setGraphicsEffect(self.content_effect)

        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)
        self.content_layout = layout

        self.hero_icon = QLabel(self.content)
        self.hero_icon.setFixedSize(56, 56)
        self.hero_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hero_icon, 0, Qt.AlignmentFlag.AlignLeft)

        self.counter_label = CaptionLabel(self.content)
        layout.addWidget(self.counter_label)

        self.hero_title = TitleLabel(self.content)
        self.hero_title.setWordWrap(True)
        layout.addWidget(self.hero_title)

        self.title_label = SubtitleLabel(self.content)
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        # Схема техники обхода: пакеты бегут от «Вы» через проверку к сайту.
        self.illustration = TechniqueIllustration(self.content, tr_fn=overlay._tr)
        self.illustration.hide()
        layout.addWidget(self.illustration)

        self.body_label = BodyLabel(self.content)
        self.body_label.setWordWrap(True)
        self.body_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.body_label)

        # Статья вики по теме шага: открывается сразу, искать не нужно.
        self.wiki_button = HyperlinkButton(FluentIcon.LINK, "", "", self.content)
        self.wiki_button.hide()
        layout.addWidget(self.wiki_button, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addSpacing(8)
        self.dots = _ProgressDots(self.content)
        layout.addWidget(self.dots)
        layout.addSpacing(4)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        self.skip_button = TransparentPushButton(self.content)
        self.back_button = PushButton(self.content)
        self.next_button = PrimaryPushButton(self.content)
        self.next_button.setMinimumWidth(112)
        buttons.addWidget(self.skip_button)
        buttons.addStretch(1)
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.next_button)
        layout.addLayout(buttons)

    def progress(self) -> float:
        return self._progress

    def set_progress(self, fraction: float, *, animate: bool) -> None:
        fraction = max(0.0, min(1.0, float(fraction)))
        if animate and abs(fraction - self._progress_target) > 1e-6:
            self._flash = 1.0
        self._progress_target = fraction
        if not animate:
            self._progress = fraction
            self._flash = 0.0

    def advance_decor(self, dt: float, animated: bool) -> bool:
        """Шаг украшений: прогресс сверху и точки. True — ещё есть движение."""
        dots_moving = self.dots.advance(dt, animated)
        if not animated:
            self._progress = self._progress_target
            self._flash = 0.0
            return dots_moving
        self._shine = (self._shine + dt / SHINE_PERIOD_MS) % 1.0
        self._breath = (self._breath + dt / BREATH_PERIOD_MS) % 1.0
        self._flash = max(0.0, self._flash - dt / FLASH_MS)
        delta = self._progress_target - self._progress
        if abs(delta) < 0.0005:
            self._progress = self._progress_target
        else:
            self._progress += delta * (1.0 - math.exp(-dt / PROGRESS_TAU_MS))
        return dots_moving or self._flash > 0.0 or self._progress != self._progress_target

    def update_decor(self) -> None:
        """Перерисовать только полоску сверху и ряд точек."""
        self.update(0, 0, self.width(), DECOR_REGION_HEIGHT)
        self.dots.update()

    def _paint_progress_strip(self, painter: QPainter, accent: QColor) -> None:
        width = float(self.width())
        height = TOP_STRIP_HEIGHT
        track = QColor(accent)
        track.setAlpha(48)
        painter.fillRect(QRectF(0, 0, width, height), track)
        filled = width * self._progress
        if filled <= 0.5:
            return
        head = QColor(
            accent.red() + (255 - accent.red()) * 2 // 5,
            accent.green() + (255 - accent.green()) * 2 // 5,
            accent.blue() + (255 - accent.blue()) * 2 // 5,
        )
        gradient = QLinearGradient(0, 0, filled, 0)
        body = QColor(accent)
        body.setAlpha(235)
        gradient.setColorAt(0.0, body)
        gradient.setColorAt(1.0, head)
        fill_rect = QRectF(0, 0, filled, height)
        painter.fillRect(fill_rect, gradient)
        if self._flash > 0.0:
            painter.fillRect(fill_rect, QColor(255, 255, 255, int(110 * self._flash)))

        # Блик пробегает по заливке слева направо и пропадает до следующего раза.
        run = self._shine / SHINE_SHARE
        if run <= 1.0:
            band = 70.0
            center = -band + run * (filled + 2 * band)
            glint = QLinearGradient(center - band / 2, 0, center + band / 2, 0)
            strength = int(170 * math.sin(math.pi * run))
            glint.setColorAt(0.0, QColor(255, 255, 255, 0))
            glint.setColorAt(0.5, QColor(255, 255, 255, strength))
            glint.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillRect(fill_rect, glint)

        # Голова кометы: мягкое свечение, дышит и вспыхивает при смене шага.
        # Вытянута вдоль полоски, чтобы не свисать в карточку круглым пятном.
        breath = 0.5 + 0.5 * math.sin(self._breath * 2 * math.pi)
        radius = 5.0 + 2.0 * breath + 5.0 * self._flash
        glow = QRadialGradient(QPointF(0, 0), radius)
        core = QColor(head)
        core.setAlpha(int(150 + 70 * self._flash))
        glow.setColorAt(0.0, core)
        edge = QColor(accent)
        edge.setAlpha(0)
        glow.setColorAt(1.0, edge)
        painter.save()
        painter.translate(filled, height / 2)
        painter.scale(2.6, 0.75)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(0, 0), radius, radius)
        painter.restore()

    def content_height_for_width(self, width: int) -> int:
        self.content.ensurePolished()
        layout = self.content_layout
        if layout.hasHeightForWidth():
            height = layout.heightForWidth(width)
        else:
            height = layout.sizeHint().height()
        return max(int(height), layout.minimumSize().height())

    def resizeEvent(self, event):  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        # Содержимое сразу получает конечный размер; сама карточка
        # догоняет его плавно и просто обрезает то, что ещё не влезло.
        target = self._overlay.card_target_size()
        self.content.setGeometry(0, 0, max(self.width(), target.width()), max(self.height(), target.height()))

    def paintEvent(self, event):  # noqa: N802 (Qt override)
        _ = event
        overlay = self._overlay
        if overlay.is_grabbing_backdrop():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(max(0.0, min(1.0, overlay.visual_opacity())))
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)
        dark = isDarkTheme()

        painter.save()
        painter.setClipPath(path)
        blurred = overlay.blurred_backdrop()
        if blurred is not None and overlay.width() > 0 and overlay.height() > 0:
            sx = blurred.width() / overlay.width()
            sy = blurred.height() / overlay.height()
            source = QRectF(self.x() * sx, self.y() * sy, self.width() * sx, self.height() * sy)
            painter.drawPixmap(QRectF(self.rect()), blurred, source)
        tint = QColor(34, 34, 38, 212) if dark else QColor(251, 251, 253, 218)
        painter.fillPath(path, tint)
        self._paint_progress_strip(painter, QColor(themeColor()))
        painter.restore()

        border = QColor(255, 255, 255, 30) if dark else QColor(0, 0, 0, 26)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.end()


class OnboardingOverlay(QWidget):
    finished = pyqtSignal(str)

    def __init__(
        self,
        window: QWidget,
        context: TourContext,
        steps: tuple[TourStep, ...] | list[TourStep],
        *,
        language: str = "ru",
    ) -> None:
        super().__init__(window)
        self.setObjectName(OVERLAY_OBJECT_NAME)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._window = window
        self._ctx = context
        self._language = language
        self.setAccessibleName(self._tr("onboarding.accessible_name", "Обучающий тур по программе"))
        self._all_steps = tuple(steps)
        self._steps: list[TourStep] = []
        self._index = -1
        self._targets: list[TourTarget] = []
        self._page_changed = False
        # Страница, которая сейчас что-то показывает для шага (меню, вкладку).
        self._state_page: QWidget | None = None
        self._failed_target_step = ""
        self._finishing = False
        self._finish_reason = ""
        self._animated = are_live_animations_enabled()

        self._opacity = 0.0
        self._opacity_target = 1.0
        self._dim = float(HERO_DIM_ALPHA)
        self._dim_target = float(HERO_DIM_ALPHA)
        self._hole: QRectF | None = None
        self._card_rect: QRectF | None = None
        self._card_size = QSize(CARD_WIDTH, 200)
        self._content_opacity = 0.0
        self._pulse = 0.0
        self._blurred = None
        self._grabbing = False

        self._card = _TourCard(self)
        self._card.skip_button.clicked.connect(self.skip)
        self._card.back_button.clicked.connect(self.go_back)
        self._card.next_button.clicked.connect(self.go_next)
        self._card.dots.stepClicked.connect(self.go_to)
        self._card.hide()

        self._clock = QElapsedTimer()
        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._on_frame)
        # Страница под затемнением может забрать фокус (редактор пресета
        # делает это сам после открытия) — тогда стрелки ушли бы в неё.
        self._focus_timer = QTimer(self)
        self._focus_timer.setSingleShot(True)
        self._focus_timer.setInterval(0)
        self._focus_timer.timeout.connect(self._reclaim_focus)
        self._blur_timer = QTimer(self)
        self._blur_timer.setSingleShot(True)
        self._blur_timer.timeout.connect(self._refresh_blur)
        self._install_shortcuts()
        self.hide()

    def _install_shortcuts(self) -> None:
        # Ярлыки срабатывают раньше, чем кнопка карточки успеет увести
        # фокус стрелкой на виджеты под затемнением.
        bindings = (
            (Qt.Key.Key_Right, self.go_next),
            (Qt.Key.Key_PageDown, self.go_next),
            (Qt.Key.Key_Left, self.go_back),
            (Qt.Key.Key_PageUp, self.go_back),
            (Qt.Key.Key_Escape, self.skip),
        )
        self._shortcuts = []
        for key, handler in bindings:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)

    # ── публичное API ────────────────────────────────────────────────

    def start(self) -> bool:
        self._steps = [step for step in self._all_steps if self._is_step_available(step)]
        if not self._steps:
            self.deleteLater()
            return False
        self._step_titles = [self._tr(f"onboarding.step.{step.key}.title", step.key) for step in self._steps]
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.sync_geometry()
        self.show()
        self.raise_()
        self._card.show()
        self._card.raise_()
        self._clock.start()
        self._enter_step(0, direction=1, first=True)
        self._restart_frame_timer(FRAME_MS)
        self._schedule_blur(BLUR_DELAY_AFTER_PAGE_MS)
        return True

    def is_finishing(self) -> bool:
        return self._finishing

    def current_step_key(self) -> str:
        if 0 <= self._index < len(self._steps):
            return self._steps[self._index].key
        return ""

    def step_keys(self) -> list[str]:
        return [step.key for step in self._steps]

    def go_next(self) -> None:
        if self._finishing:
            return
        if self._index >= len(self._steps) - 1:
            self.finish("done")
            return
        self._enter_step(self._index + 1, direction=1)

    def go_to(self, index: int) -> None:
        """Переход на любой шаг — по нажатию на его точку."""
        index = int(index)
        if self._finishing or index == self._index or not 0 <= index < len(self._steps):
            return
        self._enter_step(index, direction=1 if index > self._index else -1)

    def go_back(self) -> None:
        if self._finishing or self._index <= 0:
            return
        self._enter_step(self._index - 1, direction=-1)

    def skip(self) -> None:
        self.finish("skipped")

    def finish(self, reason: str = "done", *, immediate: bool = False) -> None:
        if self._finishing and not immediate:
            return
        self._finishing = True
        self._finish_reason = reason
        self._leave_page_state()
        self._opacity_target = 0.0
        self._blur_timer.stop()
        if immediate or not self._animated:
            self._complete_finish()
            return
        self._restart_frame_timer(FRAME_MS)

    def sync_geometry(self) -> None:
        window = self._window
        if not is_alive_widget(window):
            return
        top = 0
        title_bar = getattr(window, "titleBar", None)
        if is_widget_shown(title_bar):
            top = max(0, title_bar.y() + title_bar.height())
        self.setGeometry(0, top, window.width(), max(0, window.height() - top))
        self._schedule_blur(BLUR_DELAY_MS)

    def visual_opacity(self) -> float:
        return self._opacity

    def blurred_backdrop(self):
        return self._blurred

    def card_target_size(self) -> QSize:
        return QSize(self._card_size)

    def is_grabbing_backdrop(self) -> bool:
        return self._grabbing

    # ── шаги ─────────────────────────────────────────────────────────

    def _is_step_available(self, step: TourStep) -> bool:
        if step.page is not None:
            # Цель на странице проверим, когда откроем страницу.
            return step.page in self._ctx.pages
        if step.target is None or step.target_optional:
            return True
        try:
            return bool(step.target(self._ctx))
        except Exception:
            return False

    def _resolve_targets(self, step: TourStep) -> list[TourTarget]:
        if step.target is None:
            return []
        try:
            return [target for target in step.target(self._ctx) if is_target_shown(target)]
        except Exception as exc:
            # Цели ищутся каждый кадр: пишем в лог один раз на шаг.
            if self._failed_target_step != step.key:
                self._failed_target_step = step.key
                log(f"Обучающий тур: не удалось найти цель шага {step.key}: {exc!r}", "WARNING")
            return []

    def _open_page(self, page_key: str | None) -> bool:
        """Открывает страницу шага штатным путём через page host.

        False — страницу открыть не удалось, шаг надо пропустить.
        """
        if not page_key:
            return True
        page_name = self._ctx.pages.get(page_key)
        if page_name is None:
            return False
        from ui.window_adapter import get_current_page, get_loaded_page, show_page

        window = self._window
        page = get_loaded_page(window, page_name)
        if page is None or get_current_page(window) is not page:
            parent_key = TOUR_SUBPAGE_PARENTS.get(page_key)
            if parent_key is not None:
                if not self._open_subpage(parent_key, page_key):
                    return False
            else:
                show_page(window, page_name, allow_internal=True)
            page = get_loaded_page(window, page_name)
            self._page_changed = True
        self._ctx.current_page = page
        self._ctx.current_page_key = page_key
        return is_alive_widget(page)

    def _open_subpage(self, parent_key: str, page_key: str) -> bool:
        """Страницу с параметром открывает родитель: он знает, какой
        пресет или профиль показать."""
        if not self._open_page(parent_key):
            return False
        opener = getattr(self._ctx.current_page, "onboarding_open_subpage", None)
        if not callable(opener):
            return False
        try:
            return bool(opener(page_key))
        except Exception as exc:
            log(f"Обучающий тур: не удалось открыть страницу {page_key}: {exc!r}", "WARNING")
            return False

    def _enter_page_state(self, step: TourStep) -> None:
        if not step.page_state:
            return
        page = self._ctx.current_page
        setter = getattr(page, "onboarding_set_state", None) if is_alive_widget(page) else None
        if not callable(setter):
            return
        self._state_page = page
        try:
            setter(step.page_state)
        except Exception as exc:
            log(f"Обучающий тур: страница не показала {step.page_state} для шага {step.key}: {exc!r}", "WARNING")
            self._leave_page_state()

    def _leave_page_state(self) -> None:
        page, self._state_page = self._state_page, None
        if not is_alive_widget(page):
            return
        setter = getattr(page, "onboarding_set_state", None)
        if not callable(setter):
            return
        try:
            setter(None)
        except Exception as exc:
            log(f"Обучающий тур: страница не вернула вид после шага: {exc!r}", "WARNING")

    def _enter_step(self, index: int, *, direction: int, first: bool = False) -> None:
        self._page_changed = False
        previous_index = self._index
        self._leave_page_state()
        targets: list[TourTarget] = []
        while 0 <= index < len(self._steps):
            step = self._steps[index]
            if self._open_page(step.page):
                self._enter_page_state(step)
                targets = self._resolve_targets(step)
                if targets or step.target is None or step.target_optional:
                    break
                self._leave_page_state()
            index += direction
        if index >= len(self._steps):
            self.finish("done")
            return
        if index < 0:
            # Назад идти некуда: остаёмся на текущем шаге как было.
            if 0 <= previous_index < len(self._steps) and previous_index != index:
                self._enter_step(previous_index, direction=1, first=first)
            return

        self._index = index
        step = self._steps[index]
        self._targets = targets
        self._ensure_targets_visible(targets)
        self._apply_step_texts(step, has_target=bool(targets))
        self._dim_target = float(HERO_DIM_ALPHA if (step.hero or not targets) else DIM_ALPHA)

        self._content_opacity = 0.0 if self._animated else 1.0
        self._apply_content_opacity(self._content_opacity * self._opacity)
        if not first:
            self._schedule_blur(BLUR_DELAY_AFTER_PAGE_MS if self._page_changed else BLUR_DELAY_MS)
        self._restart_frame_timer(FRAME_MS)
        self._card.next_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _apply_step_texts(self, step: TourStep, *, has_target: bool) -> None:
        card = self._card
        total = len(self._steps)
        is_last = self._index >= total - 1
        prefix = f"onboarding.step.{step.key}"
        title = self._tr(f"{prefix}.title", step.key)
        body_key = f"{prefix}.body" if has_target or step.target is None else f"{prefix}.body_no_target"
        body = self._tr(body_key, self._tr(f"{prefix}.body", ""))
        if step.text_key:
            body = _fill_placeholders(body, self._page_text_values(step))

        card.hero_icon.setVisible(step.hero)
        if step.hero:
            icon = self._window.windowIcon() if is_alive_widget(self._window) else None
            if icon is None or icon.isNull():
                icon = QApplication.windowIcon()
            card.hero_icon.setPixmap(icon.pixmap(QSize(56, 56)) if icon is not None and not icon.isNull() else icon_placeholder())
        card.counter_label.setVisible(not step.hero)
        card.counter_label.setText(
            self._tr("onboarding.counter", "Шаг {current} из {total}").format(current=self._index + 1, total=total)
        )
        card.hero_title.setVisible(step.hero)
        card.title_label.setVisible(not step.hero)
        title = _unbreakable_options(title)
        body = _unbreakable_options(body)
        card.hero_title.setText(title)
        card.title_label.setText(title)
        card.body_label.setText(body)
        card.illustration.set_scene(step.illustration)
        card.illustration.setVisible(bool(step.illustration))
        card.wiki_button.setVisible(bool(step.wiki_url))
        if step.wiki_url:
            wiki_text = self._tr("onboarding.button.wiki", "Подробнее в вики")
            card.wiki_button.setUrl(step.wiki_url)
            card.wiki_button.setText(wiki_text)
            card.wiki_button.setAccessibleName(f"{wiki_text}: {title}")
        card.dots.set_progress(self._index, total, self.__dict__.get("_step_titles"))
        card.set_progress((self._index + 1) / max(1, total), animate=self._animated)

        card.back_button.setVisible(self._index > 0)
        card.back_button.setText(self._tr("onboarding.button.back", "Назад"))
        card.skip_button.setVisible(not is_last)
        card.skip_button.setText(self._tr("onboarding.button.skip", "Пропустить"))
        if is_last:
            next_text = self._tr("onboarding.button.done", "Готово")
        elif self._index == 0:
            next_text = self._tr("onboarding.button.start", "Начнём")
        else:
            next_text = self._tr("onboarding.button.next", "Далее")
        card.next_button.setText(next_text)
        card.next_button.setAccessibleDescription(body)

        width = HERO_CARD_WIDTH if (step.hero or step.illustration) else CARD_WIDTH
        width = int(max(260, min(width, self.width() - 2 * CARD_MARGIN)))
        height = card.content_height_for_width(width)
        self._card_size = QSize(width, height)
        card.content.setGeometry(0, 0, width, height)

    def _page_text_values(self, step: TourStep) -> dict:
        getter = getattr(self._ctx.current_page, "onboarding_text_values", None)
        if not callable(getter):
            return {}
        try:
            values = getter(step.text_key)
        except Exception as exc:
            log(f"Обучающий тур: страница не дала значения для шага {step.key}: {exc!r}", "WARNING")
            return {}
        return dict(values) if isinstance(values, dict) else {}

    def _ensure_targets_visible(self, targets: list[TourTarget]) -> None:
        widgets = [target_widget(target) for target in targets]
        widgets = [widget for widget in widgets if is_alive_widget(widget)]
        if not widgets:
            return
        scroll_areas = []
        page = self._ctx.current_page
        if is_alive_widget(page) and hasattr(page, "ensureWidgetVisible"):
            scroll_areas.append(page)
        navigation = getattr(self._window, "navigationInterface", None)
        panel = getattr(navigation, "panel", None)
        nav_scroll = getattr(panel, "scrollArea", None)
        if is_alive_widget(nav_scroll):
            scroll_areas.append(nav_scroll)
        for area in scroll_areas:
            try:
                content = area.widget()
            except (AttributeError, RuntimeError):
                continue
            if content is None:
                continue
            for widget in (widgets[-1], widgets[0]):
                if content.isAncestorOf(widget):
                    area.ensureWidgetVisible(widget, 0, 96 if area is page else 12)

    def _tr(self, key: str, default: str) -> str:
        return tr_catalog(key, language=self._language, default=default)

    # ── кадры ────────────────────────────────────────────────────────

    def _restart_frame_timer(self, interval: int) -> None:
        if not self._animated:
            interval = STATIC_FRAME_MS
        if self._frame_timer.interval() != interval or not self._frame_timer.isActive():
            self._frame_timer.start(interval)

    def _current_target_rect(self) -> QRectF | None:
        if 0 <= self._index < len(self._steps) and not self._finishing:
            # Цель ищем каждый кадр: список может догрузиться, страница —
            # прокрутиться, пункт меню — появиться позже.
            self._targets = self._resolve_targets(self._steps[self._index])
        rect: QRectF | None = None
        window = self._window
        for target in self._targets:
            if not is_target_shown(target):
                continue
            widget = target_widget(target)
            local = target[1] if isinstance(target, tuple) else widget.rect()
            top_left = widget.mapTo(window, local.topLeft()) - self.pos()
            widget_rect = QRectF(top_left.x(), top_left.y(), local.width(), local.height())
            rect = widget_rect if rect is None else rect.united(widget_rect)
        if rect is None:
            return None
        rect = rect.adjusted(-HOLE_PADDING, -HOLE_PADDING, HOLE_PADDING, HOLE_PADDING)
        rect = rect.intersected(QRectF(self.rect()).adjusted(2, 2, -2, -2))
        if rect.width() < 4 or rect.height() < 4:
            return None
        return rect

    def _card_target_rect(self, hole: QRectF | None) -> QRectF:
        width = float(self._card_size.width())
        height = float(self._card_size.height())
        bounds = QRectF(self.rect()).adjusted(CARD_MARGIN, CARD_MARGIN, -CARD_MARGIN, -CARD_MARGIN)

        def _clamp(value: float, low: float, high: float) -> float:
            return max(low, min(value, high)) if high >= low else low

        if hole is None:
            return QRectF(
                bounds.center().x() - width / 2,
                _clamp(bounds.center().y() - height / 2, bounds.top(), bounds.bottom() - height),
                width,
                height,
            )

        centered_y = _clamp(hole.center().y() - height / 2, bounds.top(), bounds.bottom() - height)
        centered_x = _clamp(hole.center().x() - width / 2, bounds.left(), bounds.right() - width)
        candidates = (
            QRectF(hole.right() + CARD_GAP, centered_y, width, height),
            QRectF(centered_x, hole.bottom() + CARD_GAP, width, height),
            QRectF(centered_x, hole.top() - CARD_GAP - height, width, height),
            QRectF(hole.left() - CARD_GAP - width, centered_y, width, height),
        )
        for candidate in candidates:
            if bounds.contains(candidate) and not candidate.intersects(hole):
                return candidate
        # Места рядом нет (крупная цель, например целый список): ставим
        # карточку туда же, но вдвигаем в окно. Лучше чуть зайти на край
        # цели под карточкой, чем уехать в угол поверх бокового меню.
        def _fit(candidate: QRectF) -> QRectF:
            return QRectF(
                _clamp(candidate.x(), bounds.left(), bounds.right() - width),
                _clamp(candidate.y(), bounds.top(), bounds.bottom() - height),
                width,
                height,
            )

        def _overlap(candidate: QRectF) -> float:
            common = candidate.intersected(hole)
            return max(0.0, common.width()) * max(0.0, common.height())

        fitted = [_fit(candidate) for candidate in (candidates[1], candidates[2], candidates[0], candidates[3])]
        card_area = max(1.0, width * height)
        for candidate in fitted:
            if _overlap(candidate) <= card_area * 0.2:
                return candidate
        return min(fitted, key=_overlap)

    def _on_frame(self) -> None:
        window = self._window
        if not is_alive_widget(window) or sip.isdeleted(self):
            self._frame_timer.stop()
            return
        if not window.isVisible():
            # Окно спрятали в трей — тур заканчиваем молча.
            self.finish("hidden", immediate=True)
            return
        if window.isMinimized():
            return

        elapsed = self._clock.restart() if self._clock.isValid() else FRAME_MS
        dt = float(max(1, min(int(elapsed), 100)))
        animated = self._animated
        k = 1.0 - math.exp(-dt / MOTION_TAU_MS) if animated else 1.0
        fade_step = dt / FADE_MS if animated else 1.0

        moving = False

        if self._opacity != self._opacity_target:
            if self._opacity < self._opacity_target:
                self._opacity = min(self._opacity_target, self._opacity + fade_step)
            else:
                self._opacity = max(self._opacity_target, self._opacity - fade_step)
            moving = True

        if self._finishing and self._opacity <= 0.0:
            self._complete_finish()
            return

        target_hole = self._current_target_rect()
        if target_hole is None:
            if self._hole is not None:
                moving = True
            self._hole = None
        elif self._hole is None:
            # Прожектор «наезжает» на цель из широкой рамки.
            self._hole = target_hole.adjusted(-90, -90, 90, 90) if animated else QRectF(target_hole)
            moving = True
        elif _rect_distance(self._hole, target_hole) > 0.4:
            self._hole = _lerp_rect(self._hole, target_hole, k)
            moving = True
        else:
            self._hole = QRectF(target_hole)

        target_card = self._card_target_rect(target_hole)
        if self._card_rect is None:
            self._card_rect = target_card.translated(0, 28) if animated else QRectF(target_card)
            moving = True
        elif _rect_distance(self._card_rect, target_card) > 0.4:
            self._card_rect = _lerp_rect(self._card_rect, target_card, k)
            moving = True
        else:
            self._card_rect = QRectF(target_card)
        new_geometry = self._card_rect.toRect()
        if self._card.geometry() != new_geometry:
            self._card.setGeometry(new_geometry)
            # Карточка переехала: снимок эффекта прозрачности надо перерисовать,
            # иначе Windows рисует текст по старому месту — со сдвигом.
            if self._card.content_effect.isEnabled():
                self._card.content_effect.update()

        if abs(self._dim - self._dim_target) > 0.5:
            self._dim = _lerp(self._dim, self._dim_target, k)
            moving = True
        else:
            self._dim = self._dim_target

        if self._content_opacity < 1.0:
            self._content_opacity = min(1.0, self._content_opacity + (dt / CONTENT_FADE_MS if animated else 1.0))
            moving = True
        self._apply_content_opacity(self._content_opacity * self._opacity)

        pulsing = animated and self._hole is not None and not self._finishing
        if pulsing:
            self._pulse = (self._pulse + dt / PULSE_PERIOD_MS) % 1.0
        # Полоска сверху и точки живут, пока тур открыт: блик, дыхание, перетекание.
        decor = animated and not self._finishing
        decor_moving = self._card.advance_decor(dt, animated)

        if moving:
            self.update()
            self._card.update()
            self._restart_frame_timer(FRAME_MS)
        elif pulsing or decor:
            # В покое перерисовываем только кольцо вокруг цели и украшения карточки.
            if pulsing:
                self.update(self._hole.adjusted(-26, -26, 26, 26).toAlignedRect())
            self._card.update_decor()
            self._restart_frame_timer(FRAME_MS if decor_moving else IDLE_FRAME_MS)
        else:
            self._card.update_decor()
            self._restart_frame_timer(STATIC_FRAME_MS)

    def _complete_finish(self) -> None:
        self._frame_timer.stop()
        self._blur_timer.stop()
        self._focus_timer.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        reason = self._finish_reason or "done"
        try:
            self.hide()
        except RuntimeError:
            return
        self.finished.emit(reason)
        self.deleteLater()

    def _apply_content_opacity(self, value: float) -> None:
        """Прозрачность текста карточки.

        Эффект включён только пока текст проявляется или гаснет: полностью
        видимый текст рисуется напрямую. Иначе на Windows кэш эффекта после
        переезда карточки иногда рисует содержимое со сдвигом.
        """
        effect = self._card.content_effect
        fading = value < 0.999
        changed = False
        if effect.isEnabled() != fading:
            effect.setEnabled(fading)
            changed = True
        if fading and abs(effect.opacity() - value) > 0.001:
            effect.setOpacity(value)
            changed = True
        if changed:
            self._card.update()

    # ── фокус ────────────────────────────────────────────────────────

    def eventFilter(self, obj, event):  # noqa: N802 (Qt override)
        if event.type() == QEvent.Type.FocusIn and not self._finishing and isinstance(obj, QWidget):
            try:
                foreign = obj is not self and not self.isAncestorOf(obj) and obj.window() is self._window
            except RuntimeError:
                foreign = False
            if foreign:
                self._focus_timer.start()
        return False

    def _reclaim_focus(self) -> None:
        if self._finishing or not self.isVisible():
            return
        focus = QApplication.focusWidget()
        if focus is not None and (focus is self or self.isAncestorOf(focus) or focus.window() is not self._window):
            return
        self._card.next_button.setFocus(Qt.FocusReason.OtherFocusReason)

    # ── размытый фон ─────────────────────────────────────────────────

    def _schedule_blur(self, delay_ms: int) -> None:
        if self._finishing:
            return
        self._blur_timer.start(int(delay_ms))

    def _refresh_blur(self) -> None:
        window = self._window
        if self._finishing or not is_alive_widget(window) or not self.isVisible():
            return
        if window.isMinimized() or self.width() <= 0 or self.height() <= 0:
            return
        # Снимаем окно так, будто тура нет: оверлей и карточка на время
        # снимка ничего не рисуют. Видимость и фокус не трогаем, иначе
        # сорвётся нажатие на кнопку карточки, пришедшееся на этот момент.
        effect = self._card.content_effect
        content_opacity = effect.opacity()
        effect_enabled = effect.isEnabled()
        self._grabbing = True
        effect.setEnabled(True)
        effect.setOpacity(0.0)
        try:
            snapshot = window.grab(self.geometry())
        finally:
            self._grabbing = False
            effect.setOpacity(content_opacity)
            effect.setEnabled(effect_enabled)
        blurred = blur_pixmap(snapshot)
        if blurred is not None:
            self._blurred = blurred
            self._card.update()

    # ── отрисовка и ввод ─────────────────────────────────────────────

    def paintEvent(self, event):  # noqa: N802 (Qt override)
        _ = event
        if self._grabbing:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        opacity = max(0.0, min(1.0, self._opacity))

        shade = QPainterPath()
        shade.addRect(QRectF(self.rect()))
        hole = self._hole
        if hole is not None:
            hole_path = QPainterPath()
            hole_path.addRoundedRect(hole, HOLE_RADIUS, HOLE_RADIUS)
            shade = shade.subtracted(hole_path)
        painter.fillPath(shade, QColor(0, 0, 0, int(self._dim * opacity)))

        card_rect = self._card_rect
        if card_rect is not None:
            # Мягкая тень под карточкой.
            for spread, alpha in ((14, 10), (9, 16), (5, 22), (2, 28)):
                shadow = QPainterPath()
                shadow.addRoundedRect(
                    card_rect.adjusted(-spread, -spread + 4, spread, spread + 6),
                    CARD_RADIUS + spread,
                    CARD_RADIUS + spread,
                )
                painter.fillPath(shadow, QColor(0, 0, 0, int(alpha * opacity)))

        if hole is not None:
            accent = QColor(themeColor())
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if self._animated and not self._finishing:
                phase = self._pulse
                grow = 3.0 + phase * 16.0
                pulse_color = QColor(accent)
                pulse_color.setAlpha(int(170 * (1.0 - phase) ** 2 * opacity))
                painter.setPen(QPen(pulse_color, 2.0))
                painter.drawRoundedRect(
                    hole.adjusted(-grow, -grow, grow, grow),
                    HOLE_RADIUS + grow,
                    HOLE_RADIUS + grow,
                )
            glow = QColor(accent)
            glow.setAlpha(int(70 * opacity))
            painter.setPen(QPen(glow, 6.0))
            painter.drawRoundedRect(hole.adjusted(-2, -2, 2, 2), HOLE_RADIUS + 2, HOLE_RADIUS + 2)
            accent.setAlpha(int(255 * opacity))
            painter.setPen(QPen(accent, 2.0))
            painter.drawRoundedRect(hole, HOLE_RADIUS, HOLE_RADIUS)
        painter.end()

    def keyPressEvent(self, event):  # noqa: N802 (Qt override)
        key = event.key()
        if key in (Qt.Key.Key_Right, Qt.Key.Key_PageDown, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.go_next()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_PageUp, Qt.Key.Key_Backspace):
            self.go_back()
        elif key == Qt.Key.Key_Escape:
            self.skip()
        event.accept()

    def focusNextPrevChild(self, next: bool) -> bool:  # noqa: N802,A002 (Qt override)
        """Tab ходит только по кнопкам карточки, а не по окну под туром."""
        card = self._card
        buttons = [
            button
            for button in (card.skip_button, card.back_button, card.next_button)
            if button.isVisible() and button.isEnabled()
        ]
        if not buttons:
            self.setFocus(Qt.FocusReason.TabFocusReason)
            return True
        focus = QApplication.focusWidget()
        index = buttons.index(focus) if focus in buttons else -1
        step = 1 if next else -1
        target = buttons[(index + step) % len(buttons)] if index >= 0 else buttons[-1]
        target.setFocus(Qt.FocusReason.TabFocusReason if next else Qt.FocusReason.BacktabFocusReason)
        return True

    def mousePressEvent(self, event):  # noqa: N802 (Qt override)
        # Пока идёт тур, интерфейс под ним не нажимается.
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802 (Qt override)
        event.accept()

    def mouseDoubleClickEvent(self, event):  # noqa: N802 (Qt override)
        event.accept()

    def wheelEvent(self, event):  # noqa: N802 (Qt override)
        event.accept()

    def resizeEvent(self, event):  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        if 0 <= self._index < len(self._steps):
            step = self._steps[self._index]
            self._apply_step_texts(step, has_target=bool(self._targets))
            self._content_opacity = 1.0
        self._restart_frame_timer(FRAME_MS)


def icon_placeholder():
    from PyQt6.QtGui import QPixmap

    pixmap = QPixmap(56, 56)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(themeColor()))
    painter.drawEllipse(QRectF(4, 4, 48, 48))
    painter.end()
    return pixmap


__all__ = ["OVERLAY_OBJECT_NAME", "OnboardingOverlay"]
