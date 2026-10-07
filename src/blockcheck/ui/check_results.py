"""Экран итогов BlockCheck: панель «что с сетью и что делать» и список сайтов.

Панель итога отвечает на главный вопрос одной фразой («Всё открывается» /
«Найдены проблемы: 2»). Под ней — картина блокировок (``block_kinds_view``):
полоса и плитки показывают, сколько сайтов открывается, сколько закрыто по
адресу (IP), сколько режут по имени сайта (SNI) и у скольких загрузка
обрывается после 16 КБ.

Проблемы собраны в группы по виду блокировки: у группы цветная метка, одно
пояснение простыми словами и общий совет, а внутри — строки сайтов с кнопкой
(«Подобрать стратегию»). Карточки сайтов и остальных проверок лежат отдельно —
в ``result_cards``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, FlowLayout, PushButton, TransparentPushButton, TransparentToolButton, SimpleCardWidget, StrongBodyLabel, SubtitleLabel

from blockcheck.ui.block_kinds_view import KindsOverview, kind_color, site_groups
from blockcheck.ui.result_cards import _ElidedLabel
from blockcheck.ui.result_cards_model import build_cards
from blockcheck.ui.brand_icons import BrandIcon, named_brand, site_brand
from diagnostics.block_kind import KIND_ORDER, KIND_OTHER, KINDS, kind_info
from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_widgets import set_tooltip
from ui.theme import get_cached_qta_pixmap
from ui.theme_refresh import ThemeRefreshBinding
from ui.widgets.fun import FunTicker, Mascot, burst_confetti
from ui.widgets.fun.mascot import MOOD_ALARM, MOOD_BUSY, MOOD_HAPPY, MOOD_IDLE, MOOD_SAD
from ui.widgets.hover_hint import HoverHint
from ui.widgets.stagger_float_in import float_in
from ui.widgets.tone_group import ToneDot, ToneGroup, dot_on_first_line, mute

ActionHandler = Callable[[str, str], None]
# Открыть полный отчёт по карточке: получает её ключ («site:youtube», «hostings» …).
OpenHandler = Callable[[str], None]

_LEVEL_ICONS = {
    "ok": ("fa5s.check-circle", "success"),
    "warn": ("fa5s.exclamation-triangle", "warning"),
    "fail": ("fa5s.times-circle", "error"),
    "unknown": ("fa5s.question-circle", "muted"),
    "pending": ("fa5s.hourglass-half", "muted"),
    "idle": ("fa5s.stethoscope", "muted"),
}
_ACTION_TEXT = {
    "strategy": "Подобрать стратегию",
    "strategy_voice": "Подобрать стратегию для звонков",
    "start_zapret": "Запустить Zapret",
    "dns": "Настройка DNS",
    "hosts": "Редактор hosts",
}
_ACTION_DESCRIPTION = {
    "strategy": "Открывает подбор стратегии",
    "strategy_voice": "Открывает подбор стратегии для голосовых звонков",
    "start_zapret": "Открывает страницу управления Zapret",
    "dns": "Открывает раздел «Настройка DNS», где включается DNS с шифрованием",
    "hosts": "Открывает «Редактор hosts», где сервису включается DNS-профиль",
}


def tone_color(tone: str, tokens=None) -> str:
    try:
        from ui.theme_semantic import get_semantic_palette

        palette = get_semantic_palette(getattr(tokens, "theme_name", None))
        return {
            "success": palette.success_text,
            "warning": palette.warning_text,
            "error": palette.error_text,
        }.get(tone, "")
    except Exception:
        return {"success": "#6ccb5f", "warning": "#ff9800", "error": "#ff6b6b"}.get(tone, "")


# Точка строки без своего цвета («нет ответа»).
_NEUTRAL_DOT = "#9aa0aa"


def _level_tone(level: str) -> str:
    return _LEVEL_ICONS.get(level, _LEVEL_ICONS["unknown"])[1]


class _HeightKeeper:
    """Текст с переносом не передаёт высоту через вложенную прокрутку: держим её сами."""

    def _sync_min_height(self) -> None:
        layout = self.layout()
        if layout is None or self.width() <= 0:
            return
        needed = layout.totalHeightForWidth(self.width()) if layout.hasHeightForWidth() else -1
        if needed <= 0:
            needed = layout.totalSizeHint().height()
        if needed != self.minimumHeight():
            self.setMinimumHeight(needed)

    def _schedule_min_height_sync(self) -> None:
        self._sync_min_height()
        QTimer.singleShot(0, self._sync_min_height)


def _own_advice(problem: dict) -> list[str]:
    """Советы строки без фраз-свидетельств («с именем сайта соединение обрывается…»)."""
    evidence = set(problem.get("evidence") or ())
    return [str(item) for item in problem.get("advice") or () if item not in evidence]


def shared_advice(problems: list[dict]) -> list[str]:
    """Советы, одинаковые у всех строк группы: их показывают один раз, в заголовке."""
    if len(problems) < 2:
        return []
    others = [set(_own_advice(problem)) for problem in problems[1:]]
    return [item for item in _own_advice(problems[0]) if all(item in other for other in others)]


def group_problems(problems: list[dict]) -> list[tuple[str, list[dict]]]:
    """Проблемы по видам блокировки. Группы идут по важности худшей строки, внутри — как пришли."""
    level_order = {"fail": 0, "warn": 1, "unknown": 2, "ok": 3}
    kind_order = {kind: index for index, kind in enumerate(KIND_ORDER)}
    groups: dict[str, list[dict]] = {}
    for problem in problems:
        kind = str(problem.get("kind") or KIND_OTHER)
        groups.setdefault(kind if kind in KINDS else KIND_OTHER, []).append(problem)

    def _rank(item: tuple[str, list[dict]]) -> tuple[int, int]:
        kind, rows = item
        return min(level_order.get(str(row.get("level")), 9) for row in rows), kind_order.get(kind, 99)

    return sorted(groups.items(), key=_rank)


# Важность словом — в подсказке к точке строки.
_LEVEL_WORDS = {"fail": "Мешает работе", "warn": "Работает не полностью", "unknown": "Нет ответа"}
_SITE_ICON = "fa5s.globe"
# Виды, которые не относятся к одному сайту: у таких строк точка важности, а не логотип.
# Всё остальное — про сайт, в том числе виды, которых здесь ещё не знают.
_NOT_SITE_KINDS = frozenset({"dns", "quic", "system", "network"})
_SERVER = re.compile(r"\s*([^,()]+?) \(([^()]+)\)")
_MORE = re.compile(r"\s*и ещё (\d+)")
_FOR_SITES = re.compile(r"^(.*?) для: (.+)$")


def split_problem_text(problem: dict) -> tuple[str, str]:
    """Заголовок строки и пояснение к нему.

    У сайта, который не открывается целиком, заголовок — его название. Остальные
    проблемы приходят одной фразой: находки по DNS пишутся как «что случилось:
    подробности», прочие — «что случилось. Что это значит» или «что: как».
    """
    title = str(problem.get("title") or "").strip()
    text = str(problem.get("text") or "").strip()
    if title:
        return title, ""
    separators = (": ", ". ") if problem.get("kind") == "dns" else (". ", ": ")
    head, tail = text, ""
    for separator in separators:
        head, found, tail = text.partition(separator)
        tail = tail.strip()
        if found and tail:
            break
    else:
        return text, ""
    tail = f"{tail[:1].upper()}{tail[1:]}"
    return head.rstrip("."), tail if tail.endswith((".", "!", "?")) else f"{tail}."


def split_server_list(detail: str) -> tuple[list[tuple[str, list[str]]], int, str]:
    """Перечень «Cloudflare (1.1.1.1), Cloudflare (1.0.0.1) и ещё 6. Пояснение» по частям.

    Возвращает серверы по названиям с их адресами, число не названных и
    остаток текста. Если пояснение начинается не с перечня — серверов нет, а
    остаток равен всему тексту.
    """
    text = str(detail or "")
    servers: dict[str, list[str]] = {}
    position = 0
    while True:
        match = _SERVER.match(text, position)
        if match is None:
            break
        servers.setdefault(match.group(1).strip(), []).append(match.group(2).strip())
        position = match.end()
        if not text.startswith(", ", position):
            break
        position += 2
    if not servers:
        return [], 0, text
    more = 0
    match = _MORE.match(text, position)
    if match is not None:
        more = int(match.group(1))
        position = match.end()
    return list(servers.items()), more, text[position:].lstrip(". ").strip()


def split_named_sites(title: str) -> tuple[str, list[str]]:
    """«QUIC блокируется по имени для: YouTube, X» → заголовок без перечня и сами сайты."""
    match = _FOR_SITES.match(str(title or ""))
    if match is None:
        return title, []
    return match.group(1), [name.strip() for name in match.group(2).split(",") if name.strip()]


def cut_providers(report: dict) -> list[tuple[str, list[str]]]:
    """Хостинги, у которых нашёлся обрыв загрузки или отправки, и их серверы с обрывом."""
    providers: dict[str, list[str]] = {}
    for server in (report.get("freeze") or {}).get("servers") or ():
        if server.get("state") == "freeze":
            providers.setdefault(str(server.get("provider") or "Без названия"), []).append(str(server.get("host") or ""))
    return list(providers.items())


# Вид проблемы → карточка с её полным отчётом (когда проблема не про один сайт).
_KIND_CARDS = {"voice": "voice", "system": "system", "cut16": "hostings"}


def problem_card_key(problem: dict, report: dict) -> str:
    """Ключ карточки с полным отчётом по этой проблеме. Пусто — такой карточки нет.

    Сайт ищется по названию, по адресу из проблемы или по первому слову фразы
    («YouTube открывается, но…»); у QUIC — по первому названному сайту.
    """
    kind = str(problem.get("kind") or KIND_OTHER)
    text = str(problem.get("text") or "")
    title = str(problem.get("title") or "")
    target = str(problem.get("target") or "")
    names = {title, text.split(" ", 1)[0].rstrip(":,")}
    if kind == "quic":
        names.update(split_named_sites(text.partition(". ")[0])[1][:1])
    names.discard("")
    for service in report.get("services") or ():
        hosts = {str(item.get("host") or "") for item in service.get("targets") or ()}
        if str(service.get("label") or "") in names or (target and target in hosts):
            return f"site:{service.get('key') or ''}"
    if kind == "dns":
        return "dns_servers" if report.get("dns_servers") else "dns"
    if kind == "network":
        return "ipv6" if text.startswith("IPv6") else "network"
    return _KIND_CARDS.get(kind, "")


def problem_brand(problem: dict) -> tuple[str, str] | None:
    """(значок, фирменный цвет) строки про сайт. ``None`` — строка не про сайт: у неё точка важности."""
    kind = str(problem.get("kind") or KIND_OTHER)
    if kind == "voice":
        # «Звонки в Telegram могут не работать» — логотип того, чьи звонки.
        brand = site_brand(str(problem.get("text") or "").partition(":")[0])
        return (brand.icon, brand.color) if brand is not None else None
    if kind in _NOT_SITE_KINDS:
        return None
    first_word = str(problem.get("text") or "").split(" ", 1)[0]
    brand = site_brand(
        str(problem.get("site_key") or ""), str(problem.get("title") or ""), str(problem.get("target") or ""), first_word
    )
    if brand is not None:
        return brand.icon, brand.color
    # Своего логотипа нет — значок с карточки этого сайта, нейтральным цветом.
    if problem.get("card_icon"):
        return str(problem["card_icon"]), ""
    return (_SITE_ICON, "") if problem.get("title") or problem.get("target") else None


# Значок кнопки действия на карточке сайта; что она делает, сказано в подсказке.
_ACTION_ICONS = {
    "strategy": "fa5s.magic",
    "strategy_voice": "fa5s.magic",
    "start_zapret": "fa5s.play",
    "dns": "fa5s.network-wired",
    "hosts": "fa5s.edit",
}


def is_site_problem(problem: dict) -> bool:
    """Проблема про один сайт: такие идут карточками, остальные (DNS, компьютер, сеть) — строками."""
    kind = str(problem.get("kind") or KIND_OTHER)
    if kind in _NOT_SITE_KINDS or kind == "voice":
        return False
    return bool(problem.get("title") or problem.get("target") or problem.get("site_key"))


def site_name(problem: dict) -> str:
    """Название сайта для карточки: своё, с карточки под итогом или первое слово фразы."""
    name = str(problem.get("title") or problem.get("site_label") or "").strip()
    return name or str(problem.get("text") or "").split(" ", 1)[0].rstrip(":,")


def site_note(problem: dict) -> str:
    """Вторая строка карточки: адрес и причина в два-три слова, а у частично работающего — что не работает."""
    if not problem.get("title"):
        head = str(problem.get("text") or "").partition(": ")[0]
        rest = head.removeprefix(site_name(problem)).strip(" ,")
        if rest:
            return rest
    return " · ".join(part for part in (str(problem.get("target") or ""), str(problem.get("cause_word") or "")) if part)


def site_explanation(problem: dict) -> tuple[str, ...]:
    """Полное объяснение по сайту для подсказки: причина, факты проверки и советы."""
    evidence = set(problem.get("evidence") or ())
    lines = [str(item) if item in evidence else f"→ {item}" for item in problem.get("advice") or ()]
    if not problem.get("title"):
        # Частично работающий сайт: причина стоит во фразе после двоеточия.
        tail = str(problem.get("text") or "").partition(": ")[2].strip()
        if tail:
            lines.insert(0, f"{tail[:1].upper()}{tail[1:]}{'' if tail.endswith('.') else '.'}")
    return tuple(lines)


class _SiteCard(QWidget):
    """Сайт карточкой: логотип, название, адрес и значок-кнопка действия.

    В карточке только то, чем сайты различаются: адрес и причина в два-три слова.
    Полное объяснение и советы — в подсказке и в отчёте по нажатию.
    """

    HEIGHT = 48

    def __init__(
        self,
        problem: dict,
        on_action: ActionHandler | None,
        parent=None,
        *,
        card_key: str = "",
        on_open: OpenHandler | None = None,
    ) -> None:
        super().__init__(parent)
        self.problems = [problem]
        caption = site_explanation(problem)
        self._level = str(problem.get("level") or "unknown")
        self._on_open = on_open
        self.card_key = card_key if on_open is not None else ""
        self._hover = False
        self.setFixedHeight(self.HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        if self.card_key:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 0, 6, 0)
        layout.setSpacing(10)

        icon_name, icon_color = problem_brand(problem) or (_SITE_ICON, "")
        self.icon = BrandIcon(icon_name, icon_color, self, size=20)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)

        self.title = site_name(problem)
        self.note = site_note(problem)
        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(0)
        texts.addStretch(1)
        self.text_label = _ElidedLabel(self.title, self, strong=True)
        texts.addWidget(self.text_label)
        self.note_label: _ElidedLabel | None = None
        if self.note:
            self.note_label = mute(_ElidedLabel(self.note, self))
            texts.addWidget(self.note_label)
        texts.addStretch(1)
        layout.addLayout(texts, 1)

        # Частично работающий сайт помечен кольцом: это не полная блокировка.
        self.dot: ToneDot | None = None
        if self._level != "fail":
            tone = _level_tone(self._level)
            self.dot = ToneDot(lambda tokens: tone_color(tone, tokens) or _NEUTRAL_DOT, self, size=7, hollow=True)
            set_tooltip(self.dot, _LEVEL_WORDS.get(self._level, ""))
            layout.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)

        action = str(problem.get("action") or "")
        self.action_button: TransparentToolButton | None = None
        self._action_icon = _ACTION_ICONS.get(action, "")
        if on_action is not None and action in _ACTION_TEXT:
            target = str(problem.get("target") or "")
            self.action_button = TransparentToolButton(self)
            self.action_button.setFixedSize(30, 30)
            self.action_button.clicked.connect(lambda _checked=False, a=action, t=target: on_action(a, t))
            what = f"{_ACTION_TEXT[action]}{f' для {target}' if target and action == 'strategy' else ''}"
            set_tooltip(self.action_button, what)
            set_control_accessibility(self.action_button, name=what, description=f"{_ACTION_DESCRIPTION.get(action, '')}.")
            layout.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignVCenter)

        hint = [self.title, *([self.note] if self.note else []), *caption]
        self.hint_text = "\n".join(hint)
        set_tooltip(self, self.hint_text + ("\nНажмите, чтобы открыть полный отчёт" if self.card_key else ""))
        state = str(problem.get("text") or self.title)
        if self.card_key:
            set_control_accessibility(self, name=state, description="Нажмите, чтобы открыть полный отчёт.")
        set_state_text(self, state)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        if self.action_button is not None and self._action_icon:
            try:
                color = _theme_color("icon_fg_muted", QColor("#d2d7df")).name()
                self.action_button.setIcon(QIcon(get_cached_qta_pixmap(self._action_icon, color=color, size=14)))
            except Exception:
                pass
        self.update()

    def open_report(self) -> None:
        if self.card_key and self._on_open is not None:
            self._on_open(self.card_key)

    def event(self, event) -> bool:
        if event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self._hover = event.type() == QEvent.Type.HoverEnter
            self.update()
        return super().event(event)

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.open_report()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self.card_key and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.open_report()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        lit = self._hover or self.hasFocus()
        painter.setBrush(_theme_color("surface_bg_hover" if lit else "surface_bg", QColor(255, 255, 255, 18 if lit else 10)))
        painter.drawRoundedRect(self.rect(), 6, 6)
        painter.end()


class _CardsFlow(QWidget):
    """Сетка карточек сайтов: колонок столько, сколько помещается, карточки делят ширину поровну."""

    MIN_WIDTH = 210
    GAP = 6

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cards: list[_SiteCard] = []
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def cards(self) -> list[_SiteCard]:
        return list(self._cards)

    def add(self, card: _SiteCard) -> None:
        card.setParent(self)
        self._cards.append(card)

    def columns_for(self, width: int) -> int:
        return max(1, (int(width) + self.GAP) // (self.MIN_WIDTH + self.GAP))

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        rows = -(-len(self._cards) // self.columns_for(width))
        return max(0, rows * (_SiteCard.HEIGHT + self.GAP) - self.GAP)

    def sizeHint(self) -> QSize:  # noqa: N802
        width = max(self.MIN_WIDTH, self.width())
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.MIN_WIDTH, _SiteCard.HEIGHT)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        columns = self.columns_for(self.width())
        width = (self.width() - self.GAP * (columns - 1)) // columns
        for index, card in enumerate(self._cards):
            row, column = divmod(index, columns)
            card.setGeometry(column * (width + self.GAP), row * (_SiteCard.HEIGHT + self.GAP), width, _SiteCard.HEIGHT)
        height = self.heightForWidth(self.width())
        if height != self.minimumHeight():
            self.setFixedHeight(height)


class _ServerChip(QWidget):
    """Метка сервиса (DNS, хостинг, сайт): значок, название и сколько его адресов названо. Адреса — в подсказке."""

    def __init__(self, name: str, addresses: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(22)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 0, 8, 0)
        layout.setSpacing(5)
        brand = named_brand(name)
        self.icon: BrandIcon | None = None
        if brand is not None:
            self.icon = BrandIcon(brand.icon, brand.color, self, size=13)
            layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text = name if len(addresses) < 2 else f"{name} ×{len(addresses)}"
        layout.addWidget(CaptionLabel(self.text, self), 0, Qt.AlignmentFlag.AlignVCenter)
        if addresses:
            set_tooltip(self, f"{name}: {', '.join(addresses)}")
        self._theme_refresh = ThemeRefreshBinding(self, lambda *_args, **_kwargs: self.update())

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_theme_color("surface_bg_hover", QColor(255, 255, 255, 18)))
        painter.drawRoundedRect(self.rect(), 4, 4)
        painter.end()


def _theme_color(token: str, fallback: QColor) -> QColor:
    try:
        from ui.theme import get_theme_tokens, to_qcolor

        return to_qcolor(getattr(get_theme_tokens(), token), fallback)
    except Exception:
        return QColor(fallback)


class _ProblemRow(QWidget):
    """Находка не про один сайт (DNS, компьютер, сеть, звонки): точка важности, заголовок, пояснение, совет, кнопка.

    Перечень DNS-серверов и сайтов показан метками. ``grouped`` — строка стоит
    в группе: заголовок отделяется от пояснения. ``bare`` — строка вне группы:
    «Открываются: …» под проблемами. ``card_key`` — карточка с полным отчётом:
    строка подсвечивается под мышью и открывает его по нажатию или Enter.
    """

    def __init__(
        self,
        problem: dict,
        on_action: ActionHandler | None,
        parent=None,
        *,
        grouped: bool = False,
        hidden_advice=(),
        bare: bool = False,
        divided: bool = False,
        card_key: str = "",
        on_open: OpenHandler | None = None,
    ) -> None:
        super().__init__(parent)
        self._level = str(problem.get("level") or "unknown")
        self._divided = divided
        self.problems = [problem]
        self._on_open = on_open
        self.card_key = card_key if on_open is not None else ""
        self._hover = False
        if self.card_key:
            self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        # Текст нажимаемой строки не выделяется мышью: иначе нажатие по нему не дошло бы до строки.
        text_flags = (
            Qt.TextInteractionFlag.NoTextInteraction if self.card_key else Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout = QHBoxLayout(self)
        pad = 2 if bare else 9
        layout.setContentsMargins(0, pad, 0, pad)
        layout.setSpacing(12)

        tone = _level_tone(self._level)
        hollow = self._level not in ("fail", "ok")
        self.icon: BrandIcon | None = None
        self.dot: ToneDot | None = None
        brand = None if bare else problem_brand(problem)
        if brand is not None:
            self.icon = BrandIcon(brand[0], brand[1], self, size=18)
            layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        else:
            # Ошибка — закрашенная точка, остальное — кольцо; слово важности в подсказке.
            self.dot = ToneDot(lambda tokens: tone_color(tone, tokens) or _NEUTRAL_DOT, self, hollow=hollow)
            holder = dot_on_first_line(self.dot)
            if not bare:
                holder.setFixedWidth(18)
                self.dot.move(5, self.dot.y())
                set_tooltip(holder, _LEVEL_WORDS.get(self._level, ""))
            layout.addWidget(holder, 0, Qt.AlignmentFlag.AlignTop)

        texts = QVBoxLayout()
        texts.setSpacing(3)
        if bare or not grouped:
            title, detail, named_sites = str(problem.get("text") or ""), "", []
        else:
            title, detail = split_problem_text(problem)
            # Сайты из заголовка («… для: YouTube, X») уходят в метки с логотипами.
            title, named_sites = split_named_sites(title)
        self.text_label = BodyLabel(title, self) if bare or not grouped else StrongBodyLabel(title, self)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(text_flags)
        texts.addWidget(self.text_label)

        # Перечень DNS-серверов — метками: по одной на сервис, адреса в подсказке.
        self.server_chips: list[_ServerChip] = []
        self.more_label: CaptionLabel | None = None
        servers, more, rest = split_server_list(detail) if problem.get("kind") == "dns" else ([], 0, detail)
        servers = [*[(name, []) for name in named_sites], *servers, *(problem.get("chips") or ())]
        if servers:
            chips = QWidget(self)
            flow = FlowLayout(chips, needAni=False)
            flow.setContentsMargins(0, 2, 0, 2)
            flow.setHorizontalSpacing(6)
            flow.setVerticalSpacing(4)
            for name, addresses in servers:
                chip = _ServerChip(name, addresses, chips)
                flow.addWidget(chip)
                self.server_chips.append(chip)
            if more:
                self.more_label = mute(CaptionLabel(f"и ещё {more}", chips))
                self.more_label.setFixedHeight(22)
                flow.addWidget(self.more_label)
            texts.addWidget(chips)
        self.detail_label: BodyLabel | None = None
        if rest:
            self.detail_label = mute(BodyLabel(rest, self))
            self.detail_label.setWordWrap(True)
            self.detail_label.setTextInteractionFlags(text_flags)
            texts.addWidget(self.detail_label)
        evidence = set(problem.get("evidence") or ())
        self.advice_labels: list[CaptionLabel] = []
        for advice in problem.get("advice") or ():
            if advice in hidden_advice:
                continue
            # Свидетельство — это факт, а не действие: стрелка только у советов.
            advice_label = BodyLabel(str(advice), self) if advice in evidence else CaptionLabel(f"→ {advice}", self)
            mute(advice_label)
            advice_label.setWordWrap(True)
            texts.addWidget(advice_label)
            self.advice_labels.append(advice_label)
        texts.addStretch(1)
        layout.addLayout(texts, 1)

        action = str(problem.get("action") or "")
        self.action_button = None
        if on_action is not None and action in _ACTION_TEXT:
            button = PushButton(_ACTION_TEXT[action], self)
            target = str(problem.get("target") or "")
            button.clicked.connect(lambda _checked=False, a=action, t=target: on_action(a, t))
            set_control_accessibility(
                button,
                name=_ACTION_TEXT[action],
                description=(
                    f"{_ACTION_DESCRIPTION.get(action, '')}"
                    f"{f' для {target}' if target and action == 'strategy' else ''}."
                ),
            )
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
            self.action_button = button
        state = str(problem.get("text") or "")
        if self.card_key:
            # Стрелка справа — знак, что строка открывается.
            self.chevron = BrandIcon("fa5s.chevron-right", "", self, size=10)
            layout.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignVCenter)
            set_control_accessibility(self, name=state, description="Нажмите, чтобы открыть полный отчёт.")
            set_tooltip(self, "Открыть полный отчёт")
        set_state_text(self, state)
        self._theme_refresh = ThemeRefreshBinding(self, lambda *_args, **_kwargs: self.update())

    def open_report(self) -> None:
        if self.card_key and self._on_open is not None:
            self._on_open(self.card_key)

    def event(self, event) -> bool:
        if event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self._hover = event.type() == QEvent.Type.HoverEnter
            self.update()
        return super().event(event)

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.open_report()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self.card_key and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.open_report()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        if not self._divided and not self.card_key:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.card_key and (self._hover or self.hasFocus()):
            # Мягкая подсветка под мышью: окно без рамок, поэтому фон, а не обводка.
            painter.setBrush(_theme_color("surface_bg_hover", QColor(255, 255, 255, 18)))
            painter.drawRoundedRect(self.rect().adjusted(-8, 1, 6, 0), 5, 5)
        elif self._divided:
            # Тонкая линия между строками группы — вместо отдельной подложки у каждой.
            painter.setBrush(_theme_color("divider_strong", QColor(255, 255, 255, 26)))
            painter.drawRect(0, 0, self.width(), 1)
        painter.end()


class _ProblemGroup(ToneGroup):
    """Проблемы одного вида блокировки: заголовок с цветной точкой, пояснение, карточки сайтов и строки.

    Сайты идут одной сеткой карточек, без текста между ними: что это за
    блокировка, сказано в заголовке группы. Находки не про сайт идут строками ниже.
    """

    def __init__(
        self,
        kind: str,
        problems: list[dict],
        on_action: ActionHandler | None,
        parent=None,
        *,
        card_key_for: Callable[[dict], str] | None = None,
        on_open: OpenHandler | None = None,
    ) -> None:
        info = kind_info(kind)
        # «Остальное» — не вид блокировки: идёт без заголовка.
        plain = kind == KIND_OTHER
        key_for = card_key_for or (lambda _problem: "")
        super().__init__(
            info.title,
            lambda tokens: kind_color(kind, tokens),
            parent,
            count=len(problems),
            about=info.about,
            plain=plain,
        )
        self._kind = kind
        hidden = () if plain else tuple(shared_advice(problems))
        self.shared_labels = [self.add_note(f"→ {advice}") for advice in hidden]
        sites = [problem for problem in problems if is_site_problem(problem)]
        others = [problem for problem in problems if not is_site_problem(problem)]

        self.rows: list[_SiteCard | _ProblemRow] = []
        self.flow: _CardsFlow | None = None
        if sites:
            self.flow = _CardsFlow(self)
            for problem in sites:
                card = _SiteCard(problem, on_action, self.flow, card_key=key_for(problem), on_open=on_open)
                self.flow.add(card)
                self.rows.append(card)
            self.add_widget(self.flow)
        for problem in others:
            row = _ProblemRow(
                problem,
                on_action,
                self,
                grouped=True,
                hidden_advice=hidden,
                divided=not plain,
                card_key=key_for(problem),
                on_open=on_open,
            )
            self.add_widget(row)
            self.rows.append(row)
        set_state_text(self, f"{info.title}, проблем: {len(problems)}")

    def kind(self) -> str:
        return self._kind


class BlockcheckSummaryPanel(_HeightKeeper, SimpleCardWidget):
    """Итог проверки: одна фраза, картина блокировок и проблемы по видам с советами."""

    def __init__(
        self, on_action: ActionHandler | None = None, parent=None, *, on_open: OpenHandler | None = None
    ) -> None:
        super().__init__(parent)
        self._on_action = on_action
        self._on_open = on_open
        self._level = "idle"

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(12)
        # Медоед-талисман: работает, пока идёт проверка, и реагирует на итог.
        self.mascot = Mascot(self, size=44)
        header.addWidget(self.mascot, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self._icon = QLabel(self)
        self._icon.setFixedSize(20, 20)
        title_row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title_label = SubtitleLabel("", self)
        self.title_label.setWordWrap(True)
        title_row.addWidget(self.title_label, 1)
        titles.addLayout(title_row)
        self.env_label = mute(CaptionLabel("", self))
        self.env_label.setWordWrap(True)
        titles.addWidget(self.env_label)
        # Что изменилось с прошлой такой же проверки.
        self.changes_label = mute(CaptionLabel("", self))
        self.changes_label.setWordWrap(True)
        self.changes_label.setVisible(False)
        titles.addWidget(self.changes_label)
        self.ticker = FunTicker(self)
        self.ticker.setVisible(False)
        titles.addWidget(self.ticker)
        header.addLayout(titles, 1)
        root.addLayout(header)

        # Картина блокировок: полоса и плитки по видам.
        self.overview = KindsOverview(self)
        self.overview.setVisible(False)
        root.addWidget(self.overview)

        self._problems_host = QWidget(self)
        self._problems_layout = QVBoxLayout(self._problems_host)
        self._problems_layout.setContentsMargins(0, 0, 0, 0)
        self._problems_layout.setSpacing(8)
        root.addWidget(self._problems_host)

        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self.set_idle()

    @property
    def level(self) -> str:
        return self._level

    def problem_rows(self) -> list[_ProblemRow]:
        rows = []
        for index in range(self._problems_layout.count()):
            widget = self._problems_layout.itemAt(index).widget()
            if isinstance(widget, _ProblemGroup):
                rows.extend(widget.rows)
            elif isinstance(widget, _ProblemRow):
                rows.append(widget)
        return rows

    def problem_groups(self) -> list[_ProblemGroup]:
        return [
            widget
            for index in range(self._problems_layout.count())
            if isinstance(widget := self._problems_layout.itemAt(index).widget(), _ProblemGroup)
        ]

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_min_height()

    def _show_changes(self, report: dict | None) -> None:
        changes = [str(item) for item in (report or {}).get("changes") or ()]
        text = ""
        if changes:
            from diagnostics.history import format_time

            when = format_time(str((report or {}).get("previous_time") or ""))
            text = f"С прошлой проверки ({when}) {'; '.join(changes)}."
        self.changes_label.setText(text)
        self.changes_label.setVisible(bool(text))

    def _clear_problems(self) -> None:
        self._show_changes(None)
        self.overview.clear()
        self.overview.setVisible(False)
        while self._problems_layout.count():
            item = self._problems_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._problems_host.setVisible(False)

    def _set_state(self, level: str, title: str, env: str = "", *, mood: str = MOOD_IDLE) -> None:
        self._level = level if level in _LEVEL_ICONS else "unknown"
        if level != "pending":
            self.ticker.stop()
            self.ticker.setVisible(False)
        self.mascot.set_mood(mood)
        self.title_label.setText(title)
        self.env_label.setText(env)
        self.env_label.setVisible(bool(env))
        self._apply_theme_refresh()
        set_state_text(self, f"Итог BlockCheck: {title}")
        self._schedule_min_height_sync()

    def set_idle(self) -> None:
        self._clear_problems()
        self._set_state(
            "idle",
            "Сеть ещё не проверялась",
            "Нажмите «Проверить»: покажем, какие сайты открываются и что делать с остальными.",
        )

    def set_pending(self) -> None:
        from blockcheck.ui.fun_texts import phrases

        self._clear_problems()
        self._set_state("pending", "Проверяем, что у вас открывается…", "", mood=MOOD_BUSY)
        self.ticker.setVisible(True)
        self.ticker.set_phrases(phrases("blockcheck"))
        self.ticker.start()

    def set_stopped(self, text: str = "Проверка остановлена") -> None:
        self._clear_problems()
        failed = "ошибк" in str(text or "").lower()
        self._set_state("unknown", text, "", mood=MOOD_ALARM if failed else MOOD_IDLE)

    def show_report(self, report: dict) -> None:
        self._clear_problems()
        problems = list(report.get("problems") or ())
        # У общей строки про обрыв на 16 КБ — метки хостингов, где он найден.
        providers = cut_providers(report)
        if providers:
            problems = [
                {**item, "chips": providers}
                if item.get("kind") == "cut16" and not item.get("title") and not item.get("target")
                else item
                for item in problems
            ]
        blocking = [item for item in problems if item.get("level") in ("fail", "warn")]
        if not problems:
            level, title, mood = "ok", "Всё открывается", MOOD_HAPPY
        elif not blocking:
            level, title, mood = "unknown", "Часть проверок не дала ответа", MOOD_IDLE
        else:
            level = "fail" if any(item.get("level") == "fail" for item in blocking) else "warn"
            title = f"Найдены проблемы: {len(blocking)}"
            mood = MOOD_ALARM if level == "fail" else MOOD_SAD
        self._show_changes(report)
        # Строка открывает полный отчёт, только если под итогом есть такая карточка;
        # с карточки сайта берётся и значок, когда своего логотипа у сайта нет.
        cards = {card.key: card for card in build_cards(report)}
        keys = [problem_card_key(item, report) for item in problems]
        problems = [
            {
                **item,
                "site_key": key.removeprefix("site:"),
                "card_icon": cards[key].icon,
                "site_label": cards[key].title,
                # Причина в два-три слова — та же метка, что на карточке сайта под итогом.
                "cause_word": next((text for text, state in cards[key].chips if state == "fail"), ""),
            }
            if key.startswith("site:") and key in cards
            else item
            for item, key in zip(problems, keys)
        ]

        def card_key_for(problem: dict) -> str:
            key = problem_card_key(problem, report)
            return key if key in cards and self._on_open is not None else ""

        rows: list[QWidget] = [
            _ProblemGroup(
                kind, items, self._on_action, self._problems_host, card_key_for=card_key_for, on_open=self._on_open
            )
            for kind, items in group_problems(problems)
        ]
        working = list(report.get("working") or ())
        if working and problems:
            rows.append(
                _ProblemRow(
                    {"level": "ok", "text": f"Открываются: {', '.join(working)}"}, None, self._problems_host, bare=True
                )
            )
        for row in rows:
            self._problems_layout.addWidget(row)
        self._problems_host.setVisible(bool(rows))
        groups = site_groups(report)
        self.overview.setVisible(bool(groups))
        self.overview.show_groups(groups)
        self._set_state(level, title, _environment_text(report), mood=mood)
        # Группы выплывают по очереди, а если всё хорошо — салют.
        for order, row in enumerate(rows):
            float_in(row, delay_ms=160 + order * 90)
        if level == "ok":
            burst_confetti(self)

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        icon_name, tone = _LEVEL_ICONS.get(self._level, _LEVEL_ICONS["unknown"])
        color = tone_color(tone, tokens) or None
        try:
            self._icon.setPixmap(get_cached_qta_pixmap(icon_name, color=color, size=20, muted_fallback=color is None))
        except Exception:
            pass


def _environment_text(report: dict) -> str:
    running = report.get("zapret_running")
    parts = []
    if running is True:
        parts.append("Zapret включён — проверено с обходом")
    elif running is False:
        parts.append("Zapret выключен — проверено без обхода")
    elapsed = report.get("elapsed")
    if isinstance(elapsed, (int, float)):
        parts.append(f"проверка заняла {elapsed:.0f} с")
    if report.get("timed_out"):
        parts.append("часть проверок не успела")
    return " · ".join(parts)


# Сколько прошлых проверок видно сразу и сколько — по кнопке «Показать все».
HISTORY_SHOWN = 6
HISTORY_ALL = 50
# Столько проблем одного прогона хранит история (settings.schema.CHECK_HISTORY_PROBLEMS_LIMIT).
HISTORY_PROBLEMS_KEPT = 12
_HISTORY_MARKS = {"ok": "✓", "warn": "!", "fail": "✗", "unknown": "?"}


def history_lines(runs, limit: int = HISTORY_SHOWN) -> list[tuple[str, str]]:
    """Строки карточки «Прошлые проверки», новые сверху: (уровень, текст)."""
    from diagnostics.history import format_time

    lines: list[tuple[str, str]] = []
    for run in reversed(list(runs or ())[-max(1, int(limit)) :]):
        level = str(run.get("level") or "unknown")
        level = level if level in _HISTORY_MARKS else "unknown"
        parts = [format_time(str(run.get("time") or "")), str(run.get("title") or "")]
        text = " · ".join(part for part in parts if part)
        lines.append((level, f"{_HISTORY_MARKS[level]} {text} — {run.get('headline') or 'итог не записан'}"))
    return lines


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """Одна прошлая проверка для таблицы: когда, что проверяли, чем кончилось и что изменилось."""

    level: str
    when: str
    scope: str
    # «Открывается 4 из 9 · проблем: 14».
    outcome: str
    # По сравнению с предыдущей такой же проверкой.
    changes: str
    opened: int = 0
    blocked: int = 0
    unknown: int = 0
    # Первая проблема прогона — для подсказки.
    headline: str = ""


_OPEN_STATES = ("ok", "warn")


def history_rows(runs, limit: int = HISTORY_SHOWN) -> list[HistoryRow]:
    """Строки таблицы «Прошлые проверки», новые сверху.

    Итог — сколько сайтов открывалось и сколько было проблем, а не одна первая
    проблема: по ней шесть разных прогонов выглядели одинаково. «Что
    изменилось» — сравнение с предыдущей проверкой того же набора сайтов.
    """
    from diagnostics.history import describe_changes, format_time, previous_run

    runs = list(runs or ())
    rows: list[HistoryRow] = []
    for index in range(len(runs) - 1, max(-1, len(runs) - 1 - max(1, int(limit))), -1):
        run = runs[index]
        level = str(run.get("level") or "unknown")
        level = level if level in _HISTORY_MARKS else "unknown"
        states = [str(state) for state in (run.get("states") or {}).values()]
        opened = sum(1 for state in states if state in _OPEN_STATES)
        blocked = sum(1 for state in states if state == "fail")
        problems = len(run.get("problems") or ())
        if states:
            parts = [f"Открывается {opened} из {len(states)}"]
            if problems:
                # В истории хранится не больше стольких проблем прогона — дальше счёт неточный.
                parts.append(f"проблем: {problems}{'+' if problems >= HISTORY_PROBLEMS_KEPT else ''}")
            outcome = " · ".join(parts)
        else:
            outcome = str(run.get("headline") or "итог не записан")
        previous = previous_run(runs[:index], run)
        if previous is None:
            changes = "первая такая проверка"
        else:
            found = describe_changes(previous, run)
            changes = "; ".join(found) if found else "без изменений"
        rows.append(
            HistoryRow(
                level=level,
                when=format_time(str(run.get("time") or "")),
                scope=str(run.get("title") or ""),
                outcome=outcome,
                changes=f"{changes[:1].upper()}{changes[1:]}",
                opened=opened,
                blocked=blocked,
                unknown=len(states) - opened - blocked,
                headline=str(run.get("headline") or ""),
            )
        )
    return rows


class _HistoryTable(QWidget):
    """Таблица прошлых проверок, которую рисует один виджет: значок итога, время, набор, полоса, итог, перемены."""

    HEADER = 24
    ROW = 30
    _COLUMNS = (("Когда", 104), ("Что проверяли", 160), ("Итог", 350))
    _LAST = "Что изменилось с прошлой такой проверки"
    _ICONS = {"ok": "fa5s.check-circle", "warn": "fa5s.exclamation-triangle", "fail": "fa5s.times-circle", "unknown": "fa5s.question-circle"}

    # Нажали строку: её номер сверху.
    opened = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[HistoryRow] = []
        self._hover = -1
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hint = HoverHint(self)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(0)
        self._theme_refresh = ThemeRefreshBinding(self, lambda *_args, **_kwargs: self.update())

    def rows(self) -> list[HistoryRow]:
        return list(self._rows)

    def set_rows(self, rows: list[HistoryRow]) -> None:
        self._rows = list(rows)
        self.setFixedHeight(self.HEADER + self.ROW * len(self._rows) if self._rows else 0)
        self.update()

    def row_at(self, y: float) -> int:
        index = int((y - self.HEADER) // self.ROW) if y >= self.HEADER else -1
        return index if 0 <= index < len(self._rows) else -1

    def hint(self, index: int) -> str:
        row = self._rows[index]
        lines = [f"{row.when} · {row.scope}", row.outcome, f"{self._LAST}: {row.changes[:1].lower()}{row.changes[1:]}"]
        if row.headline and row.headline != row.outcome:
            lines.append(f"Первая проблема: {row.headline}")
        return "\n".join(lines)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        hover = self.row_at(event.position().y())
        if hover != self._hover:
            self._hover = hover
            self.update()
            text = self.hint(hover) + "\nНажмите, чтобы открыть эту проверку" if hover >= 0 else ""
            self._hint.show(text, event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = -1
        self._hint.hide()
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        index = self.row_at(event.position().y())
        self._hint.hide()
        if event.button() == Qt.MouseButton.LeftButton and index >= 0:
            self.opened.emit(index)
        super().mouseReleaseEvent(event)

    def event(self, event) -> bool:
        # Системную подсказку не показываем: на тёмной теме она мелькает белым окном.
        if event.type() == QEvent.Type.ToolTip:
            return True
        return super().event(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        if not self._rows:
            return
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        text = _theme_color("fg", QColor(255, 255, 255, 235))
        muted = _theme_color("fg_muted", QColor(255, 255, 255, 165))
        line = _theme_color("divider_strong", QColor(255, 255, 255, 26))
        hover = _theme_color("surface_bg_hover", QColor(255, 255, 255, 18))
        font = QFont(self.font())
        font.setPixelSize(13)
        small = QFont(self.font())
        small.setPixelSize(12)
        metrics = QFontMetrics(font)
        left, bar_width = 30, 64
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        painter.setFont(small)
        painter.setPen(muted)
        x = left
        for caption, width in self._COLUMNS:
            painter.drawText(QRectF(x, 0, width, self.HEADER), flags, caption)
            x += width
        painter.drawText(QRectF(x, 0, max(0, self.width() - x), self.HEADER), flags, self._LAST)

        for index, row in enumerate(self._rows):
            top = self.HEADER + index * self.ROW
            painter.setPen(Qt.PenStyle.NoPen)
            if index == self._hover:
                painter.setBrush(hover)
                painter.drawRoundedRect(QRectF(0, top + 1, self.width(), self.ROW - 1), 5, 5)
            else:
                painter.setBrush(line)
                painter.drawRect(QRectF(0, top, self.width(), 1))
            color = tone_color(_level_tone(row.level)) or _NEUTRAL_DOT
            try:
                icon = get_cached_qta_pixmap(self._ICONS[row.level], color=color, size=14)
                painter.drawPixmap(6, int(top + (self.ROW - 14) / 2), 14, 14, icon)
            except Exception:
                pass
            painter.setFont(font)
            x = left
            painter.setPen(muted)
            painter.drawText(QRectF(x, top, self._COLUMNS[0][1], self.ROW), flags, row.when)
            x += self._COLUMNS[0][1]
            painter.setPen(text)
            width = self._COLUMNS[1][1]
            painter.drawText(QRectF(x, top, width, self.ROW), flags, metrics.elidedText(row.scope, Qt.TextElideMode.ElideRight, width - 10))
            x += width
            # Полоса: какая доля сайтов открывалась, была закрыта и осталась без ответа.
            total = row.opened + row.blocked + row.unknown
            width = self._COLUMNS[2][1]
            text_left = x
            if total:
                painter.setPen(Qt.PenStyle.NoPen)
                bar_x = float(x)
                for share, tone in ((row.opened, "success"), (row.blocked, "error"), (row.unknown, "")):
                    if not share:
                        continue
                    part = (bar_width - 4) * share / total
                    painter.setBrush(QColor(tone_color(tone) or _NEUTRAL_DOT))
                    painter.drawRoundedRect(QRectF(bar_x, top + self.ROW / 2 - 2.5, max(2.0, part), 5), 2.5, 2.5)
                    bar_x += part + 2
                text_left = x + bar_width + 10
            painter.setPen(text)
            painter.drawText(
                QRectF(text_left, top, x + width - text_left, self.ROW),
                flags,
                metrics.elidedText(row.outcome, Qt.TextElideMode.ElideRight, int(x + width - text_left - 10)),
            )
            x += width
            painter.setPen(muted)
            rest = max(0, self.width() - x - 26)
            painter.drawText(QRectF(x, top, rest, self.ROW), flags, metrics.elidedText(row.changes, Qt.TextElideMode.ElideRight, rest))
            # Стрелка справа — знак, что строка открывается.
            painter.drawText(QRectF(self.width() - 20, top, 14, self.ROW), int(Qt.AlignmentFlag.AlignCenter), "›")
        painter.end()


class BlockcheckHistoryList(QWidget):
    """Прошлые проверки таблицей: когда, что проверяли, сколько открывалось и что изменилось.

    Строка открывает ту проверку целиком. Сразу видны последние, остальные (до
    ``HISTORY_ALL``) — по кнопке «Показать все».
    """

    # Просят открыть прошлую проверку: её запись из истории.
    run_opened = pyqtSignal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        header = QHBoxLayout()
        self.title_label = StrongBodyLabel("Прошлые проверки", self)
        header.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        self.more_button = TransparentPushButton("", self)
        self.more_button.clicked.connect(self._toggle_all)
        header.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._layout.addLayout(header)
        self.table = _HistoryTable(self)
        self.table.opened.connect(self._on_row_opened)
        self._layout.addWidget(self.table)
        self._lines: list[tuple[str, str]] = []
        self._runs: list[dict] = []
        self._all = False

    def _toggle_all(self) -> None:
        self._all = not self._all
        self._fill()

    def _on_row_opened(self, index: int) -> None:
        # Строки идут новыми сверху, записи истории — старыми сверху.
        if 0 <= index < len(self._runs):
            self.run_opened.emit(dict(self._runs[len(self._runs) - 1 - index]))

    def _fill(self) -> None:
        total = min(len(self._runs), HISTORY_ALL)
        self.table.set_rows(history_rows(self._runs, HISTORY_ALL if self._all else HISTORY_SHOWN))
        self.more_button.setVisible(total > HISTORY_SHOWN)
        self.more_button.setText("Свернуть" if self._all else f"Показать все: {total}")

    def lines(self) -> list[tuple[str, str]]:
        return list(self._lines)

    def rows(self) -> list[HistoryRow]:
        return self.table.rows()

    def show_history(self, runs) -> None:
        self._lines = history_lines(runs)
        self._runs = [dict(run) for run in runs or ()]
        self._fill()
        set_state_text(
            self,
            "Прошлые проверки: "
            + ("; ".join(f"{row.when}, {row.scope}: {row.outcome}, {row.changes}" for row in self.rows()) or "пока нет"),
        )
