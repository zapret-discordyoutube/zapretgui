"""Всплывающая подсказка на странице профиля: это гео-сервис, стратегия ему не поможет.

Гео-сервис сам не пускает посетителей из России (ChatGPT, Gemini, Spotify).
Провайдер его не режет, поэтому перебор готовых стратегий ничего не даст —
помогает DNS-профиль в «Редакторе hosts» или другой DNS.

Подсказка — отдельная карточка поверх страницы, в правом нижнем углу: её
видно сразу, но места у списка стратегий она не отнимает. Закрытая крестиком
карточка для этого сервиса больше не появляется до перезапуска программы.
"""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
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

from ui.accessibility import set_control_accessibility
from ui.dialog_static_shadow import DialogStaticShadow
from ui.one_shot_worker_runtime import OneShotWorkerRuntime
from ui.widgets.tone_group import mute


CARD_WIDTH = 400
# Отступ карточки от правого и нижнего края страницы.
CARD_MARGIN = 28
_SLIDE_MS = 220
_SLIDE_OFFSET = 18
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
        self.icon = IconWidget(FluentIcon.GLOBE, self)
        self.icon.setFixedSize(18, 18)
        header.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
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
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(_SLIDE_MS)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
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
        self.place(animated=False)

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

    def place(self, *, animated: bool) -> None:
        """Ставит карточку в правый нижний угол страницы; ``animated`` — с выездом снизу."""
        target = self._corner_pos()
        self._slide.stop()
        if animated and self._host.isVisible():
            self._slide.setStartValue(target + QPoint(0, _SLIDE_OFFSET))
            self._slide.setEndValue(target)
            self._slide.start()
        else:
            self.move(target)

    def popup(self) -> None:
        was_visible = self.isVisible()
        self._shadow.set_shadow(28, (0, 8), QColor(0, 0, 0, 110 if isDarkTheme() else 60))
        self._shadow.show()
        self.show()
        self._shadow.raise_()
        self.raise_()
        self.place(animated=not was_visible)

    def dismiss(self) -> None:
        self._slide.stop()
        self._shadow.hide()
        self.hide()

    def moveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().moveEvent(event)
        self._shadow.sync_geometry()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._shadow.sync_geometry()


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
        self.card = ProfileGeoNoticeCard(host)
        self.card.open_hosts_clicked.connect(self._on_open_hosts)
        self.card.open_dns_clicked.connect(self._on_open_dns)
        self.card.close_clicked.connect(self._on_close)
        host.installEventFilter(self)

    def request(self, profile_key: str) -> None:
        """Профиль открыт или перечитан: проверить его список сайтов в фоне."""
        key = str(profile_key or "").strip()
        if key != self._profile_key:
            # Карточка прошлого профиля не должна висеть над новым.
            self._hide()
        self._profile_key = key
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
        self.card.popup()

    def _hide(self) -> None:
        self._services = ()
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
        if watched is self._host and event.type() == QEvent.Type.Resize and self.card.isVisible():
            self.card.place(animated=False)
        return False

    def cleanup(self) -> None:
        self._cleanup_in_progress = True
        self._runtime.stop(blocking=False, warning_prefix="profile geo notice worker")
        self._runtime.cancel()
        self.card.dismiss()


__all__ = [
    "ProfileGeoNotice",
    "ProfileGeoNoticeCard",
    "geo_notice_text",
    "geo_notice_title",
]
