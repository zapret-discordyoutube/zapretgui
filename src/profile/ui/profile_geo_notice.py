"""Всплывающая подсказка на странице профиля: это гео-сервис, стратегия ему не поможет.

Гео-сервис сам не пускает посетителей из России (ChatGPT, Gemini, Spotify).
Провайдер его не режет, поэтому перебор готовых стратегий ничего не даст —
помогает DNS-профиль в «Редакторе hosts» или другой DNS.

Подсказка — отдельная карточка поверх страницы, в правом нижнем углу: её
видно сразу, но места у списка стратегий она не отнимает. Закрытая крестиком
карточка для этого сервиса больше не появляется до перезапуска программы.
"""

from __future__ import annotations

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap, QRegion
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QStackedLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    FluentIcon,
    FlyoutViewBase,
    IconWidget,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TransparentToolButton,
    isDarkTheme,
)

from profile.icons import resolve_profile_icon
from profile.ui.profile_icon import profile_icon_pixmap
from ui.accessibility import set_control_accessibility
from ui.dialog_static_shadow import DialogStaticShadow
from ui.one_shot_worker_runtime import OneShotWorkerRuntime
from ui.widgets.tone_group import mute


CARD_WIDTH = 400
# Отступ карточки от правого и нижнего края страницы.
CARD_MARGIN = 28
# Радиус углов подложки FlyoutViewBase.
_CARD_RADIUS = 8
_ICON_SIZE = 20
_APPEAR_MS = 200
# На столько карточка приподнимается, пока проявляется.
_APPEAR_RISE = 12
# Проверка списка сайтов ждёт, пока страница профиля соберёт свой список
# стратегий: иначе фоновая работа и появление карточки попадают на самый
# занятый момент и дёргаются вместе со страницей.
_CHECK_DELAY_MS = 250
# В заголовке называем не больше стольких сервисов, остальные — числом.
_NAMED_SERVICES = 2


def geo_notice_title(services: tuple[str, ...]) -> str:
    names = [str(name) for name in services if str(name or "").strip()]
    shown = ", ".join(names[:_NAMED_SERVICES])
    rest = len(names) - _NAMED_SERVICES
    if rest > 0:
        shown = f"{shown} и ещё {rest}"
    return f"{shown}: стратегия не поможет"


def geo_notice_text(services: tuple[str, ...]) -> str:
    if len(services) > 1:
        subject = "Эти сервисы сами не пускают посетителей из России"
    else:
        subject = "Этот сервис сам не пускает посетителей из России"
    return (
        f"{subject} — провайдер тут ни при чём, поэтому перебирать стратегии бесполезно. "
        "Помогает DNS-профиль в «Редакторе hosts» или другой DNS-сервер."
    )


class _AppearSnapshot(QWidget):
    """Снимок карточки с тенью, который проявляется вместо неё самой.

    Пока карточка появляется, двигать и перерисовывать настоящие кнопки,
    подписи и тень на каждом кадре дорого. Поэтому они рисуются один раз в
    картинки, а кадр анимации — это две готовые картинки с прозрачностью.
    """

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._shadow = QPixmap()
        self._shadow_pos = QPoint()
        self._card = QImage()
        self._card_rect = QRectF()
        self._progress = 0.0
        self.hide()

    def set_pictures(self, shadow: QPixmap, shadow_pos: QPoint, card: QImage, card_rect: QRectF) -> None:
        self._shadow = shadow
        self._shadow_pos = shadow_pos
        self._card = card
        self._card_rect = card_rect

    def set_progress(self, progress: float) -> None:
        self._progress = max(0.0, min(1.0, float(progress)))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt API)
        if self._card.isNull():
            return
        painter = QPainter(self)
        painter.setOpacity(self._progress)
        painter.translate(0.0, (1.0 - self._progress) * _APPEAR_RISE)
        painter.drawPixmap(self._shadow_pos, self._shadow)
        # Картинка карточки непрозрачная (так текст на ней сглажен как в
        # настоящей), скруглённые углы ей даёт обрезка.
        corners = QPainterPath()
        corners.addRoundedRect(self._card_rect, _CARD_RADIUS, _CARD_RADIUS)
        painter.setClipPath(corners)
        painter.drawImage(self._card_rect.topLeft(), self._card)


class ProfileGeoNoticeCard(FlyoutViewBase):
    """Карточка подсказки: значок, заголовок, пояснение и две кнопки."""

    open_hosts_clicked = pyqtSignal()
    open_dns_clicked = pyqtSignal()
    close_clicked = pyqtSignal()

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self._host = host
        self.setFixedWidth(CARD_WIDTH)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 12, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        # Значок сервиса — тот же, что у профиля в списке; глобус — запасной.
        icon_box = QWidget(self)
        icon_box.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        self._icon_stack = QStackedLayout(icon_box)
        self._icon_stack.setContentsMargins(0, 0, 0, 0)
        self.globe_icon = IconWidget(FluentIcon.GLOBE, icon_box)
        self._icon_stack.addWidget(self.globe_icon)
        self.site_icon = QLabel(icon_box)
        self.site_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_stack.addWidget(self.site_icon)
        header.addWidget(icon_box, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title_label = StrongBodyLabel("", self)
        self.title_label.setWordWrap(True)
        header.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)
        self.close_button = TransparentToolButton(FluentIcon.CLOSE, self)
        self.close_button.setFixedSize(28, 28)
        self.close_button.setIconSize(QSize(12, 12))
        set_control_accessibility(
            self.close_button,
            name="Закрыть подсказку",
            description="Убирает подсказку про гео-сервис до перезапуска программы.",
        )
        self.close_button.clicked.connect(self.close_clicked)
        header.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.text_label = mute(BodyLabel("", self))
        self.text_label.setWordWrap(True)
        text_row = QHBoxLayout()
        text_row.setContentsMargins(0, 0, 6, 0)
        text_row.addWidget(self.text_label, 1)
        root.addLayout(text_row)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 4, 6, 0)
        buttons.setSpacing(8)
        self.hosts_button = PrimaryPushButton("Редактор hosts", self)
        set_control_accessibility(
            self.hosts_button,
            name="Открыть «Редактор hosts»",
            description="Открывает «Редактор hosts», где сервису включается DNS-профиль.",
        )
        self.hosts_button.clicked.connect(self.open_hosts_clicked)
        buttons.addWidget(self.hosts_button)
        self.dns_button = PushButton("Настройка DNS", self)
        set_control_accessibility(
            self.dns_button,
            name="Настройка DNS",
            description="Открывает раздел «Настройка DNS», где меняется DNS-сервер.",
        )
        self.dns_button.clicked.connect(self.open_dns_clicked)
        buttons.addWidget(self.dns_button)
        buttons.addStretch(1)
        root.addLayout(buttons)

        # Готовая тень вместо живого размытия: карточка не перерисовывает её
        # при каждом наведении мыши на кнопку.
        self._shadow = DialogStaticShadow(host, self)
        self._shadow.hide()
        self._snapshot = _AppearSnapshot(host)
        self._appear = QVariantAnimation(self)
        self._appear.setDuration(_APPEAR_MS)
        self._appear.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._appear.setStartValue(0.0)
        self._appear.setEndValue(1.0)
        self._appear.valueChanged.connect(self._snapshot.set_progress)
        self._appear.finished.connect(self._show_real_card)
        self.hide()

    def backgroundColor(self):  # noqa: N802 (API qfluentwidgets)
        # Чуть светлее подложки списка: карточка читается как лежащая поверх.
        return QColor(50, 50, 50) if isDarkTheme() else QColor(252, 252, 252)

    def borderColor(self):  # noqa: N802 (API qfluentwidgets)
        return QColor(255, 255, 255, 22) if isDarkTheme() else QColor(0, 0, 0, 24)

    def set_services(self, services: tuple[str, ...]) -> None:
        self.title_label.setText(geo_notice_title(services))
        self.text_label.setText(geo_notice_text(services))
        self.setFixedHeight(self._height_for_content())
        self.place()

    def set_site_icon(self, icon_name: str, color: str) -> None:
        """Значок сервиса вместо глобуса; пустое имя возвращает глобус."""
        pixmap = QPixmap()
        if str(icon_name or "").strip():
            pixmap = profile_icon_pixmap(str(icon_name), color=str(color or ""), size=_ICON_SIZE)
        if pixmap.isNull():
            self._icon_stack.setCurrentWidget(self.globe_icon)
            return
        self.site_icon.setPixmap(pixmap)
        self._icon_stack.setCurrentWidget(self.site_icon)

    def _height_for_content(self) -> int:
        layout = self.layout()
        layout.activate()
        height = layout.totalHeightForWidth(CARD_WIDTH)
        return max(int(height), int(layout.totalMinimumSize().height()))

    def _corner_pos(self) -> QPoint:
        return QPoint(
            max(0, self._host.width() - self.width() - CARD_MARGIN),
            max(0, self._host.height() - self.height() - CARD_MARGIN),
        )

    def is_shown(self) -> bool:
        """Карточка на экране: уже стоит или ещё проявляется."""
        return self.isVisible() or self._snapshot.isVisible()

    def place(self) -> None:
        """Ставит карточку в правый нижний угол страницы (и обрывает появление)."""
        if self._snapshot.isVisible():
            self._appear.stop()
            self._show_real_card()
        self.move(self._corner_pos())

    def popup(self) -> None:
        if self.is_shown():
            return
        self.move(self._corner_pos())
        self._shadow.set_shadow(28, (0, 8), QColor(0, 0, 0, 110 if isDarkTheme() else 60))
        if not self._host.isVisible():
            self._show_real_card()
            return
        self._start_appearing()

    def _start_appearing(self) -> None:
        shadow_rect = self._shadow.geometry()
        area = shadow_rect.united(self.geometry()).adjusted(0, 0, 0, _APPEAR_RISE)
        dpr = self._host.devicePixelRatioF()
        picture = QImage(
            max(1, round(self.width() * dpr)),
            max(1, round(self.height() * dpr)),
            QImage.Format.Format_RGB32,
        )
        picture.setDevicePixelRatio(dpr)
        picture.fill(self.backgroundColor())
        self.render(picture, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
        # grab() подложил бы под тень фон страницы — рисуем её на прозрачном.
        shadow = QPixmap(
            max(1, round(shadow_rect.width() * dpr)),
            max(1, round(shadow_rect.height() * dpr)),
        )
        shadow.setDevicePixelRatio(dpr)
        shadow.fill(Qt.GlobalColor.transparent)
        self._shadow.render(shadow, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
        # Под карточкой тень вырезается: иначе сквозь полупрозрачную карточку
        # она просвечивала бы тёмной каймой по краям.
        card_in_shadow = QRectF(self.geometry().translated(-shadow_rect.topLeft()))
        hole = QPainterPath()
        hole.addRoundedRect(card_in_shadow, _CARD_RADIUS, _CARD_RADIUS)
        cutter = QPainter(shadow)
        cutter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cutter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        cutter.fillPath(hole, Qt.GlobalColor.black)
        cutter.end()
        self._snapshot.set_pictures(
            shadow,
            shadow_rect.topLeft() - area.topLeft(),
            picture,
            QRectF(self.geometry().translated(-area.topLeft())),
        )
        self._snapshot.setGeometry(area)
        self._snapshot.set_progress(0.0)
        self._snapshot.show()
        self._snapshot.raise_()
        self._appear.start()

    def _show_real_card(self) -> None:
        self._shadow.show()
        self.show()
        self._shadow.raise_()
        self.raise_()
        self._snapshot.hide()

    def finish_appearing(self) -> None:
        """Сразу ставит настоящую карточку, если она ещё проявляется."""
        if self._snapshot.isVisible():
            self._appear.stop()
            self._show_real_card()

    def dismiss(self) -> None:
        self._appear.stop()
        self._snapshot.hide()
        self._shadow.hide()
        self.hide()

    def moveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().moveEvent(event)
        self._shadow.sync_geometry()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._shadow.sync_geometry()


# Сервис, на котором экскурсия показывает карточку.
EXAMPLE_SERVICES = ("Gemini",)


class ProfileGeoNotice(QObject):
    """Решает, показывать ли карточку открытому профилю, и держит её в углу страницы.

    Список сайтов профиля сверяется с каталогом hosts в фоне; сюда приходит
    готовый ответ — названия гео-сервисов или пусто.
    """

    def __init__(
        self,
        host: QWidget,
        *,
        create_worker=None,
        open_hosts_editor=None,
        open_dns_settings=None,
    ) -> None:
        super().__init__(host)
        self._host = host
        self._create_worker = create_worker
        self._open_hosts_editor = open_hosts_editor
        self._open_dns_settings = open_dns_settings
        self._runtime = OneShotWorkerRuntime()
        self._cleanup_in_progress = False
        self._profile_key = ""
        self._services: tuple[str, ...] = ()
        # Сервисы, чью карточку человек закрыл крестиком в этом запуске программы.
        self._dismissed: set[tuple[str, ...]] = set()
        # На экране пример обучающей экскурсии, а не итог проверки профиля.
        self._example_shown = False
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(_CHECK_DELAY_MS)
        self._check_timer.timeout.connect(self._start_check)
        self.card = ProfileGeoNoticeCard(host)
        self.card.open_hosts_clicked.connect(self._on_open_hosts)
        self.card.open_dns_clicked.connect(self._on_open_dns)
        self.card.close_clicked.connect(self._on_close)
        host.installEventFilter(self)

    def request(self, profile_key: str, item=None) -> None:
        """Профиль открыт или перечитан: проверить его список сайтов в фоне.

        ``item`` — строка профиля: по её названию и условиям берётся значок.
        """
        key = str(profile_key or "").strip()
        if key != self._profile_key:
            # Карточка прошлого профиля не должна висеть над новым.
            self._hide()
        self._profile_key = key
        if item is not None:
            icon = resolve_profile_icon(
                getattr(item, "display_name", ""),
                tuple(getattr(item, "match_lines", ()) or ()),
            )
            self.card.set_site_icon(icon.icon_name, icon.color)
        else:
            self.card.set_site_icon("", "")
        if self._create_worker is None or not key or self._cleanup_in_progress:
            self._check_timer.stop()
            return
        # Перезапуск таймера: несколько перечитываний подряд дают одну проверку.
        self._check_timer.start()

    def _start_check(self) -> None:
        key = self._profile_key
        if self._create_worker is None or not key or self._cleanup_in_progress:
            return
        self._runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._create_worker(request_id, key, self._host),
            on_loaded=self._on_loaded,
        )

    def _on_loaded(self, request_id: int, profile_key: str, services) -> None:
        if not self._runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        if str(profile_key or "") != self._profile_key:
            return
        found = tuple(str(name) for name in (services or ()) if str(name or "").strip())
        if not found or found in self._dismissed:
            self._hide()
            return
        if found != self._services:
            self._services = found
            self.card.set_services(found)
        # Настоящая карточка заняла место примера: экскурсия её уже не уберёт.
        self._example_shown = False
        self.card.popup()

    def _hide(self) -> None:
        self._services = ()
        if self._example_shown:
            # Пример держит экскурсия: проверка профиля, которая ничего не нашла, его не снимает.
            return
        self.card.dismiss()

    def show_example(self) -> None:
        """Обучающая экскурсия показывает карточку на примере, если настоящей на экране нет."""
        if self.card.is_shown() or self._cleanup_in_progress:
            return
        self._example_shown = True
        self.card.set_services(EXAMPLE_SERVICES)
        self.card.popup()
        # Экскурсии нужна сама карточка сразу, а не её плавное появление.
        self.card.finish_appearing()

    def hide_example(self) -> None:
        if not self._example_shown:
            return
        self._example_shown = False
        self.card.dismiss()

    def _on_close(self) -> None:
        if self._services:
            self._dismissed.add(self._services)
        self._hide()

    def _on_open_hosts(self) -> None:
        if self._open_hosts_editor is not None:
            self._open_hosts_editor()

    def _on_open_dns(self) -> None:
        if self._open_dns_settings is not None:
            self._open_dns_settings()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt API)
        if watched is self._host and event.type() == QEvent.Type.Resize and self.card.is_shown():
            self.card.place()
        return False

    def cleanup(self) -> None:
        self._cleanup_in_progress = True
        self._check_timer.stop()
        self._runtime.stop(blocking=False, warning_prefix="profile geo notice worker")
        self._runtime.cancel()
        self.card.dismiss()


__all__ = [
    "ProfileGeoNotice",
    "ProfileGeoNoticeCard",
    "geo_notice_text",
    "geo_notice_title",
]
