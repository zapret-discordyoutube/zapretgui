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

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, FlowLayout, PushButton, TransparentPushButton, SimpleCardWidget, StrongBodyLabel, SubtitleLabel

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
# Ширина столбца с названиями сайтов: объяснения справа начинаются с одной линии.
NAME_COLUMN = 190
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


def cluster_problems(problems: list[dict], hidden_advice=()) -> list[list[dict]]:
    """Сайты с одним и тем же объяснением — в одну строку: текст пишется один раз.

    Объединяются только названные сайты без кнопки: у кнопки своя цель на каждый сайт.
    """
    clusters: list[list[dict]] = []
    index: dict[tuple, list[dict]] = {}
    for problem in problems:
        own = tuple(item for item in problem.get("advice") or () if item not in hidden_advice)
        if not problem.get("title") or problem.get("action") or not own:
            clusters.append([problem])
            continue
        key = (problem.get("level"), own)
        if key in index:
            index[key].append(problem)
        else:
            index[key] = [problem]
            clusters.append(index[key])
    return clusters


class _SiteBadge(QWidget):
    """Логотип и название сайта одной строкой."""

    def __init__(self, problem: dict, parent=None, *, card_key: str = "", on_open: OpenHandler | None = None) -> None:
        super().__init__(parent)
        self.card_key = card_key if on_open is not None else ""
        self._on_open = on_open
        if self.card_key:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            set_tooltip(self, f"Открыть полный отчёт: {problem.get('title') or ''}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        icon_name, icon_color = problem_brand(problem) or (_SITE_ICON, "")
        self.icon = BrandIcon(icon_name, icon_color, self, size=18)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.name_label = StrongBodyLabel(str(problem.get("title") or ""), self)
        layout.addWidget(self.name_label, 1, Qt.AlignmentFlag.AlignVCenter)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        # В строке из нескольких сайтов каждый открывает свой отчёт.
        if self.card_key and event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self._on_open(self.card_key)
            event.accept()
            return
        super().mouseReleaseEvent(event)


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
    """Строка списка проблем: слева — что сломано, справа — объяснение, совет и кнопка.

    Строка про сайт — в одну линию, как в таблице: логотип и название в левом
    столбце, справа короткое объяснение (что не поместилось и советы — в
    подсказке и в полном отчёте), кнопка без рамки. ``also`` — другие сайты с
    тем же объяснением: они встают в тот же столбец, а текст пишется один раз.
    Остальные строки (DNS, компьютер, сеть): точка важности, заголовок и под
    ним пояснение; перечень DNS-серверов показан метками.

    ``grouped`` — строка стоит в группе, вид блокировки назван в её заголовке.
    ``hidden_advice`` — советы, уже показанные в заголовке группы.
    ``bare`` — строка вне группы: «Открываются: …» под проблемами.
    ``card_keys`` — ключи карточек с полным отчётом, по одному на каждый сайт
    строки. Если отчёт есть, строка подсвечивается под мышью и открывает его
    по нажатию или Enter.
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
        also=(),
        divided: bool = False,
        card_keys=(),
        on_open: OpenHandler | None = None,
    ) -> None:
        super().__init__(parent)
        self._level = str(problem.get("level") or "unknown")
        self._divided = divided
        self.problems = [problem, *also]
        self._on_open = on_open
        keys = [*card_keys, *[""] * len(self.problems)][: len(self.problems)] if on_open is not None else []
        self.card_key = next((key for key in keys if key), "")
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
        titled_site = grouped and not bare and bool(problem.get("title"))
        pad = 2 if bare else 6 if titled_site else 9
        layout.setContentsMargins(0, pad, 0, pad)
        layout.setSpacing(12)

        tone = _level_tone(self._level)
        hollow = self._level not in ("fail", "ok")
        self.badges: list[_SiteBadge] = []
        self.icon: BrandIcon | None = None
        self.dot: ToneDot | None = None
        self.text_label: BodyLabel | None = None
        texts = QVBoxLayout()
        texts.setSpacing(3)

        if titled_site:
            # Левый столбец: названия сайтов, по одному в строке.
            names = QVBoxLayout()
            names.setContentsMargins(0, 0, 0, 0)
            names.setSpacing(4)
            for order, item in enumerate(self.problems):
                badge = _SiteBadge(item, self, card_key=keys[order] if keys else "", on_open=on_open)
                badge.setFixedWidth(NAME_COLUMN)
                names.addWidget(badge)
                self.badges.append(badge)
            # Один сайт стоит по центру строки, несколько — столбцом от её верха.
            if len(self.problems) > 1:
                names.addStretch(1)
            layout.addLayout(names)
            self.icon = self.badges[0].icon
            self.text_label = self.badges[0].name_label
            title, detail = str(problem.get("title") or ""), ""
        else:
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
            if bare or not grouped:
                title, detail = str(problem.get("text") or ""), ""
            else:
                title, detail = split_problem_text(problem)
            # Сайты из заголовка («… для: YouTube, X») уходят в метки с логотипами.
            title, named_sites = split_named_sites(title) if grouped and not bare else (title, [])
            self.text_label = BodyLabel(title, self) if bare or not grouped else StrongBodyLabel(title, self)
            self.text_label.setWordWrap(True)
            self.text_label.setTextInteractionFlags(text_flags)
            texts.addWidget(self.text_label)

        # Перечень DNS-серверов — метками: по одной на сервис, адреса в подсказке.
        self.server_chips: list[_ServerChip] = []
        self.more_label: CaptionLabel | None = None
        servers, more, rest = split_server_list(detail) if problem.get("kind") == "dns" else ([], 0, detail)
        if not titled_site:
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
        own_advice = [str(item) for item in problem.get("advice") or () if item not in hidden_advice]
        if titled_site and own_advice:
            # Одна линия: свидетельство проверки (или первый совет). Остальное — под мышью и в отчёте.
            facts = [item for item in own_advice if item in evidence]
            # Совет, в отличие от факта, помечен стрелкой.
            shown = " ".join(facts) if facts else f"→ {own_advice[0]}"
            summary = mute(_ElidedLabel(shown, self))
            font = summary.font()
            font.setPixelSize(13)
            summary.setFont(font)
            texts.addWidget(summary)
            self.advice_labels.append(summary)
            advices = [f"→ {item}" for item in own_advice if item not in evidence]
            self.hint_text = "\n".join([*facts, *advices])
            set_tooltip(self, self.hint_text + ("\nНажмите, чтобы открыть полный отчёт" if self.card_key else ""))
            own_advice = []
        for advice in own_advice:
            if advice in hidden_advice:
                continue
            # Свидетельство — это факт, а не действие: стрелка только у советов.
            advice_label = BodyLabel(str(advice), self) if advice in evidence else CaptionLabel(f"→ {advice}", self)
            mute(advice_label)
            advice_label.setWordWrap(True)
            texts.addWidget(advice_label)
            self.advice_labels.append(advice_label)
        if not titled_site or len(self.problems) > 1:
            texts.addStretch(1)
        layout.addLayout(texts, 1)

        action = str(problem.get("action") or "")
        self.action_button = None
        if on_action is not None and action in _ACTION_TEXT:
            # В плотной строке сайта кнопка без рамки: девять рамок подряд спорили бы с текстом.
            button = (TransparentPushButton if titled_site else PushButton)(_ACTION_TEXT[action], self)
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
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter if titled_site else Qt.AlignmentFlag.AlignTop)
            self.action_button = button
        state = " ".join(str(item.get("text") or "") for item in self.problems)
        if self.card_key:
            # Стрелка справа — знак, что строка открывается.
            self.chevron = BrandIcon("fa5s.chevron-right", "", self, size=10)
            layout.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignVCenter)
            set_control_accessibility(self, name=state, description="Нажмите, чтобы открыть полный отчёт.")
            if not self.advice_labels or not titled_site:
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
    """Проблемы одного вида блокировки: заголовок с цветной точкой, пояснение, общий совет и строки."""

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
        key_for = card_key_for or (lambda _problem: "")
        # «Остальное» — не вид блокировки: строки идут без заголовка.
        plain = kind == KIND_OTHER
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
        clusters = [[problem] for problem in problems] if plain else cluster_problems(problems, hidden)
        self.rows = [
            _ProblemRow(
                cluster[0],
                on_action,
                self,
                # Строки без вида блокировки оформлены так же: заголовок отдельно от пояснения.
                grouped=True,
                hidden_advice=hidden,
                also=cluster[1:],
                divided=not plain,
                card_keys=[key_for(item) for item in cluster],
                on_open=on_open,
            )
            for cluster in clusters
        ]
        for row in self.rows:
            self.add_widget(row)
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
            {**item, "site_key": key.removeprefix("site:"), "card_icon": cards[key].icon}
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


HISTORY_SHOWN = 6
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


class BlockcheckHistoryList(_HeightKeeper, QWidget):
    """Прошлые проверки: когда, что проверяли и чем кончилось."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self.title_label = StrongBodyLabel("Прошлые проверки", self)
        self._layout.addWidget(self.title_label)
        self._labels: list[CaptionLabel] = []
        self._lines: list[tuple[str, str]] = []
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)

    def lines(self) -> list[tuple[str, str]]:
        return list(self._lines)

    def show_history(self, runs) -> None:
        self._lines = history_lines(runs)
        while len(self._labels) < len(self._lines):
            label = CaptionLabel("", self)
            label.setWordWrap(True)
            self._layout.addWidget(label)
            self._labels.append(label)
        for index, label in enumerate(self._labels):
            visible = index < len(self._lines)
            label.setVisible(visible)
            if visible:
                label.setText(self._lines[index][1])
        self._apply_theme_refresh()
        set_state_text(self, "Прошлые проверки: " + ("; ".join(text for _level, text in self._lines) or "пока нет"))
        self._schedule_min_height_sync()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_min_height()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        for label, (level, _text) in zip(self._labels, self._lines):
            color = tone_color(_level_tone(level), tokens)
            label.setStyleSheet(f"color: {color};" if color else "")
