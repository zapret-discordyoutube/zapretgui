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

from collections.abc import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, PushButton, SimpleCardWidget, StrongBodyLabel, SubtitleLabel

from blockcheck.ui.block_kinds_view import KindsOverview, kind_color, site_groups
from blockcheck.ui.brand_icons import BrandIcon, brands_in_text, site_brand
from diagnostics.block_kind import KIND_ORDER, KIND_OTHER, KINDS, kind_info
from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_widgets import set_tooltip
from ui.theme import get_cached_qta_pixmap
from ui.theme_refresh import ThemeRefreshBinding
from ui.widgets.fun import FunTicker, Mascot, burst_confetti
from ui.widgets.fun.mascot import MOOD_ALARM, MOOD_BUSY, MOOD_HAPPY, MOOD_IDLE, MOOD_SAD
from ui.widgets.stagger_float_in import float_in
from ui.widgets.tone_group import ToneDot, ToneGroup, mute

ActionHandler = Callable[[str, str], None]

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


# Важность словом. У ошибки слова нет: в списке проблем она и так подразумевается,
# а пометка на каждой карточке превратилась бы в шум. Помечаются исключения.
_LEVEL_WORDS = {"warn": "работает не полностью", "unknown": "нет ответа"}
# Значок карточки, когда у неё нет логотипа сайта: по виду проблемы.
_KIND_ICONS = {
    "dns": "fa5s.network-wired",
    "quic": "fa5s.bolt",
    "voice": "fa5s.phone-alt",
    "cut16": "fa5s.server",
    "system": "fa5s.desktop",
    "network": "fa5s.wifi",
    "cert": "fa5s.certificate",
}
_SITE_ICON = "fa5s.globe"
# Виды, которые относятся к одному сайту: у таких карточек логотип сайта.
_SITE_KINDS = frozenset({"ip", "sni", "cut16", "stub", "cert", "unclear", KIND_OTHER})


def split_problem_text(problem: dict) -> tuple[str, str]:
    """Заголовок карточки и пояснение к нему.

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


def problem_brand(problem: dict) -> tuple[str, str]:
    """(значок, фирменный цвет) карточки. Цвет пустой — значок нейтральный."""
    kind = str(problem.get("kind") or KIND_OTHER)
    if kind in _SITE_KINDS:
        first_word = str(problem.get("text") or "").split(" ", 1)[0]
        brand = site_brand(str(problem.get("title") or ""), str(problem.get("target") or ""), first_word)
        if brand is not None:
            return brand.icon, brand.color
    return _KIND_ICONS.get(kind, _SITE_ICON if problem.get("target") or problem.get("title") else "fa5s.info-circle"), ""


class _ProblemRow(QWidget):
    """Одна проблема карточкой: значок, заголовок, важность, пояснение, совет и кнопка.

    ``grouped`` — карточка стоит в группе, вид блокировки назван в заголовке
    группы. ``hidden_advice`` — советы, уже показанные в заголовке группы.
    ``bare`` — строка без подложки и значка: «Открываются: …» под проблемами.
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
    ) -> None:
        super().__init__(parent)
        self._level = str(problem.get("level") or "unknown")
        self._bare = bare
        self._surface = QColor()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(*((0, 2, 0, 2) if bare else (14, 12, 12, 12)))
        layout.setSpacing(12)

        tone = _level_tone(self._level)
        hollow = self._level not in ("fail", "ok")
        self.icon: BrandIcon | None = None
        if bare:
            self.dot = ToneDot(lambda tokens: tone_color(tone, tokens) or _NEUTRAL_DOT, self, hollow=hollow)
            dot_box = QVBoxLayout()
            dot_box.setContentsMargins(0, 6, 0, 0)
            dot_box.addWidget(self.dot)
            dot_box.addStretch(1)
            layout.addLayout(dot_box)
        else:
            icon_name, icon_color = problem_brand(problem)
            self.icon = BrandIcon(icon_name, icon_color, self, size=22)
            layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)

        texts = QVBoxLayout()
        texts.setSpacing(3)
        if bare or not grouped:
            title, detail = str(problem.get("text") or ""), ""
        else:
            title, detail = split_problem_text(problem)
        self.text_label = BodyLabel(title, self) if bare else StrongBodyLabel(title, self)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        texts.addWidget(self.text_label)

        # Важность — кольцом и словом, и только когда это не обычная ошибка.
        self.level_label: CaptionLabel | None = None
        # DNS-сервисы, названные в находке, — их значками.
        brands = brands_in_text(str(problem.get("text") or "")) if problem.get("kind") == "dns" and not bare else []
        self.brand_icons: list[BrandIcon] = []
        if not bare and (self._level in _LEVEL_WORDS or brands):
            level_row = QHBoxLayout()
            level_row.setSpacing(6)
            if self._level in _LEVEL_WORDS:
                self.dot = ToneDot(lambda tokens: tone_color(tone, tokens) or _NEUTRAL_DOT, self, size=7, hollow=hollow)
                level_row.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
                self.level_label = mute(CaptionLabel(_LEVEL_WORDS[self._level], self))
                level_row.addWidget(self.level_label, 0, Qt.AlignmentFlag.AlignVCenter)
                if brands:
                    level_row.addSpacing(6)
            for brand in brands:
                icon = BrandIcon(brand.icon, brand.color, self, size=14)
                set_tooltip(icon, brand.name)
                level_row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
                level_row.addWidget(mute(CaptionLabel(brand.name, self)), 0, Qt.AlignmentFlag.AlignVCenter)
                level_row.addSpacing(6)
                self.brand_icons.append(icon)
            level_row.addStretch(1)
            texts.addLayout(level_row)

        self.detail_label: BodyLabel | None = None
        if detail:
            self.detail_label = BodyLabel(detail, self)
            self.detail_label.setWordWrap(True)
            self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            mute(self.detail_label)
            texts.addSpacing(3)
            texts.addWidget(self.detail_label)
        evidence = set(problem.get("evidence") or ())
        self.advice_labels: list[CaptionLabel] = []
        for advice in problem.get("advice") or ():
            if advice in hidden_advice:
                continue
            # Свидетельство — это факт, а не действие: стрелка только у советов.
            is_evidence = advice in evidence
            advice_label = BodyLabel(str(advice), self) if is_evidence else CaptionLabel(f"→ {advice}", self)
            mute(advice_label)
            advice_label.setWordWrap(True)
            if is_evidence and not self.advice_labels and not detail:
                texts.addSpacing(3)
            texts.addWidget(advice_label)
            self.advice_labels.append(advice_label)
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
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
            self.action_button = button
        set_state_text(self, str(problem.get("text") or ""))
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        try:
            from ui.theme import get_theme_tokens, to_qcolor

            self._surface = to_qcolor((tokens or get_theme_tokens()).surface_bg, "#0affffff")
        except Exception:
            self._surface = QColor(255, 255, 255, 10)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        if self._bare:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._surface)
        painter.drawRoundedRect(self.rect(), 6, 6)
        painter.end()


class _ProblemGroup(ToneGroup):
    """Проблемы одного вида блокировки: заголовок с цветной точкой, пояснение, общий совет и карточки."""

    def __init__(self, kind: str, problems: list[dict], on_action: ActionHandler | None, parent=None) -> None:
        info = kind_info(kind)
        # «Остальное» — не вид блокировки: строки идут как раньше, без заголовка и подложки.
        plain = kind == KIND_OTHER
        super().__init__(
            info.title,
            lambda tokens: kind_color(kind, tokens),
            parent,
            count=len(problems),
            about=info.about,
            plain=plain,
            flat=not plain,
        )
        self._kind = kind
        hidden = () if plain else tuple(shared_advice(problems))
        self.shared_labels = [self.add_note(f"→ {advice}") for advice in hidden]
        self.rows = [
            _ProblemRow(problem, on_action, self, grouped=not plain, hidden_advice=hidden) for problem in problems
        ]
        for row in self.rows:
            self.add_widget(row)
        set_state_text(self, f"{info.title}, проблем: {len(problems)}")

    def kind(self) -> str:
        return self._kind


class BlockcheckSummaryPanel(_HeightKeeper, SimpleCardWidget):
    """Итог проверки: одна фраза, картина блокировок и проблемы по видам с советами."""

    def __init__(self, on_action: ActionHandler | None = None, parent=None) -> None:
        super().__init__(parent)
        self._on_action = on_action
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
        self._problems_layout.setSpacing(16)
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
        rows: list[QWidget] = [
            _ProblemGroup(kind, items, self._on_action, self._problems_host)
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
