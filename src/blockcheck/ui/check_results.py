"""Экран итогов BlockCheck: панель «что с сетью и что делать» и список сайтов.

Панель итога отвечает на главный вопрос одной фразой («Всё открывается» /
«Найдены проблемы: 2») и перечисляет проблемы по важности — каждая с советом и,
где можно, кнопкой («Подобрать стратегию»). Список сайтов — одна строка на
сервис: открывается ли он и что именно не так.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QHeaderView, QLabel, QSizePolicy, QTableWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, PushButton, SimpleCardWidget, StrongBodyLabel, TableWidget

from ui.accessibility import set_control_accessibility, set_state_text
from ui.theme import get_cached_qta_pixmap
from ui.theme_refresh import ThemeRefreshBinding
from ui.widgets.fluent_item_tooltip import install_fluent_item_tooltips, set_fluent_item_tooltip
from ui.widgets.fun import FunTicker, Mascot, burst_confetti
from ui.widgets.fun.mascot import MOOD_ALARM, MOOD_BUSY, MOOD_HAPPY, MOOD_IDLE, MOOD_SAD
from ui.widgets.stagger_float_in import float_in

ActionHandler = Callable[[str, str], None]

_LEVEL_ICONS = {
    "ok": ("fa5s.check-circle", "success"),
    "warn": ("fa5s.exclamation-triangle", "warning"),
    "fail": ("fa5s.times-circle", "error"),
    "unknown": ("fa5s.question-circle", "muted"),
    "pending": ("fa5s.hourglass-half", "muted"),
    "idle": ("fa5s.stethoscope", "muted"),
}
_RESULT_WORDS = {
    "ok": "✓ Открывается",
    "warn": "⚠ Есть проблемы",
    "fail": "✗ Не открывается",
    "unknown": "? Не удалось проверить",
}
_SECTION_WORDS = {
    "voice": {
        "ok": "✓ Работают",
        "warn": "⚠ С перебоями",
        "fail": "✗ Не работают",
        "unknown": "? Не удалось проверить",
    },
    "freeze": {
        "ok": "✓ Обрыва нет",
        "warn": "✗ Обрывается",
        "fail": "✗ Обрывается",
        "unknown": "? Не удалось проверить",
    },
}
# Сервер, который не удалось проверить, — «?», а не «✗»: это не «не работает».
_ITEM_MARKS = {"ok": "✓", "fail": "✗", "freeze": "✗", "unknown": "?"}


def _item_mark(item: dict) -> str:
    state = str(item.get("state") or ("ok" if item.get("ok") else "fail"))
    return _ITEM_MARKS.get(state, "?")


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


class _ProblemRow(QWidget):
    def __init__(self, problem: dict, on_action: ActionHandler | None, parent=None) -> None:
        super().__init__(parent)
        self._level = str(problem.get("level") or "unknown")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(10)

        self._icon = QLabel(self)
        self._icon.setFixedSize(18, 18)
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.text_label = BodyLabel(str(problem.get("text") or ""), self)
        self.text_label.setWordWrap(True)
        texts.addWidget(self.text_label)
        for advice in problem.get("advice") or ():
            advice_label = CaptionLabel(f"→ {advice}", self)
            advice_label.setWordWrap(True)
            texts.addWidget(advice_label)
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
        set_state_text(self, str(problem.get("text") or ""))
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        icon_name, tone = _LEVEL_ICONS.get(self._level, _LEVEL_ICONS["unknown"])
        color = tone_color(tone, tokens) or None
        try:
            self._icon.setPixmap(get_cached_qta_pixmap(icon_name, color=color, size=16, muted_fallback=color is None))
        except Exception:
            pass


class BlockcheckSummaryPanel(_HeightKeeper, SimpleCardWidget):
    """Итог проверки: одна фраза, строка про окружение, проблемы с советами."""

    def __init__(self, on_action: ActionHandler | None = None, parent=None) -> None:
        super().__init__(parent)
        self._on_action = on_action
        self._level = "idle"

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

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
        self._icon.setFixedSize(24, 24)
        title_row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title_label = StrongBodyLabel("", self)
        self.title_label.setWordWrap(True)
        title_row.addWidget(self.title_label, 1)
        titles.addLayout(title_row)
        self.env_label = CaptionLabel("", self)
        self.env_label.setWordWrap(True)
        titles.addWidget(self.env_label)
        self.ticker = FunTicker(self)
        self.ticker.setVisible(False)
        titles.addWidget(self.ticker)
        header.addLayout(titles, 1)
        root.addLayout(header)

        self._problems_host = QWidget(self)
        self._problems_layout = QVBoxLayout(self._problems_host)
        self._problems_layout.setContentsMargins(56, 4, 0, 0)
        self._problems_layout.setSpacing(6)
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
            if isinstance(widget, _ProblemRow):
                rows.append(widget)
        return rows

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_min_height()

    def _clear_problems(self) -> None:
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
            "Медоед готов проверить вашу сеть",
            "Нажмите «Проверить»: посмотрим, какие сайты открываются, и подскажем, что делать с остальными.",
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
            level, title, mood = "ok", "Всё открывается — провайдер сегодня добрый 🎉", MOOD_HAPPY
        elif not blocking:
            level, title, mood = "unknown", "Часть проверок не дала ответа", MOOD_IDLE
        else:
            level = "fail" if any(item.get("level") == "fail" for item in blocking) else "warn"
            title = f"Найдены проблемы: {len(blocking)} — ниже, что с ними делать"
            mood = MOOD_ALARM if level == "fail" else MOOD_SAD
        rows = [_ProblemRow(problem, self._on_action, self._problems_host) for problem in problems]
        working = list(report.get("working") or ())
        if working and problems:
            rows.append(_ProblemRow({"level": "ok", "text": f"Открываются: {', '.join(working)}"}, None, self._problems_host))
        for row in rows:
            self._problems_layout.addWidget(row)
        self._problems_host.setVisible(bool(rows))
        self._set_state(level, title, _environment_text(report), mood=mood)
        # Проблемы выплывают по очереди, а если всё хорошо — салют.
        for order, row in enumerate(rows):
            float_in(row, delay_ms=120 + order * 90)
        if level == "ok":
            burst_confetti(self)

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        icon_name, tone = _LEVEL_ICONS.get(self._level, _LEVEL_ICONS["unknown"])
        color = tone_color(tone, tokens) or None
        try:
            self._icon.setPixmap(get_cached_qta_pixmap(icon_name, color=color, size=24, muted_fallback=color is None))
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


def _target_mark(item: dict) -> str:
    """✓ открывается, ? проверка не дала ответа, ✗ не открывается."""
    if item.get("ok"):
        return "✓"
    return "?" if str(item.get("state") or "") == "unknown" else "✗"


# Состояние IPv6 → (уровень строки, слово результата). «Нет в сети» — норма,
# поэтому зелёным или красным она не красится.
_IPV6_ROW = {
    "ok": ("ok", "Работает"),
    "absent": ("unknown", "Нет в сети"),
    "broken": ("warn", "Не работает"),
    "unknown": ("unknown", "Не проверено"),
}

# Как блокируют — коротко для ячейки; полный текст причины идёт в подсказку строки.
_CAUSE_WORDS = {
    "by_name": "блокировка по имени сайта",
    "by_address": "закрыт адрес или его сеть",
    "stub_page": "страница провайдера о блокировке",
    "address_closed": "адрес закрыт для соединений",
    "address_silent": "адрес не отвечает",
}


def _service_details(service: dict) -> tuple[str, str]:
    """(коротко для ячейки, подробно для подсказки)."""
    targets = list(service.get("targets") or ())
    if len(targets) > 1:
        short = " · ".join(f"{_target_mark(item)} {item.get('purpose', '')}" for item in targets)
    elif targets:
        short = str(targets[0].get("short") or "")
    else:
        short = ""
    causes = list(dict.fromkeys(_CAUSE_WORDS[item["cause"]] for item in targets if item.get("cause") in _CAUSE_WORDS))
    if causes:
        short = f"{short} · {', '.join(causes)}" if short else ", ".join(causes)
    if any(item.get("quic") == "blocked_by_name" for item in targets):
        short = f"{short} · QUIC заблокирован" if short else "QUIC заблокирован"
    if service.get("dns_note"):
        short = f"{short} · DNS подменён" if short else "DNS подменён"
    lines = []
    for item in targets:
        lines.append(f"{item.get('host', '')}: {item.get('text', '')}")
        if item.get("cause_text"):
            lines.append(f"   {item['cause_text']}")
        if item.get("quic_text"):
            lines.append(f"   QUIC (UDP 443): {item['quic_text']}")
    tooltip = "\n".join(lines)
    headline = str(service.get("headline") or "")
    if headline:
        tooltip = f"{headline}\n\n{tooltip}" if tooltip else headline
    return short, tooltip


def _row_level(service: dict) -> str:
    """Уровень для строки списка. Подмена DNS при открывающемся сайте — не «проблема
    сайта»: о ней одна общая строка в итоге, а здесь — пометка в подробностях."""
    level = str(service.get("level") or "unknown")
    targets = list(service.get("targets") or ())
    if level == "warn" and targets and all(item.get("ok") for item in targets):
        return "ok"
    return level


class BlockcheckSitesTable(TableWidget):
    """Одна строка на сервис: название, открывается ли, что именно не так."""

    COLUMNS = ("Сайт", "Результат", "Подробности")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setColumnCount(len(self.COLUMNS))
        self.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.verticalHeader().setVisible(False)
        self.setWordWrap(False)
        # Высота — ровно по строкам (см. _fit_height): таблица не растягивает карточку.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        set_control_accessibility(
            self,
            name="Результаты BlockCheck по сайтам",
            description="Для каждого сайта: открывается ли он и что именно не так. Подробности — в подсказке строки.",
        )
        set_state_text(self, "Результаты BlockCheck по сайтам: пока нет результатов")
        install_fluent_item_tooltips(self)
        self._levels: list[str] = []
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)

    def clear_rows(self) -> None:
        self.setRowCount(0)
        self._levels = []
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        set_state_text(self, "Результаты BlockCheck по сайтам: пока нет результатов")

    def _add_row(self, name: str, level: str, result_text: str, details: str, tooltip: str) -> None:
        row = self.rowCount()
        self.insertRow(row)
        for column, text in enumerate((name, result_text, details)):
            item = QTableWidgetItem(text)
            set_fluent_item_tooltip(item, tooltip or details)
            self.setItem(row, column, item)
        self._levels.append(level)

    def show_report(self, report: dict) -> None:
        self.clear_rows()
        services = list(report.get("services") or ())
        # Сначала то, что сломано: пользователь ищет глазами именно это.
        order = {"fail": 0, "warn": 1, "unknown": 2, "ok": 3}
        for service in sorted(services, key=lambda item: order.get(_row_level(item), 9)):
            level = _row_level(service)
            short, tooltip = _service_details(service)
            name = str(service.get("label") or "")
            if service.get("control"):
                name = f"{name} (контрольный)"
            self._add_row(name, level, _RESULT_WORDS.get(level, ""), short, tooltip)
        for key, title in (("freeze", "Обрыв на 16–20 КБ"), ("voice", "Голосовые звонки (UDP)")):
            section = report.get(key)
            if not section:
                continue
            level = str(section.get("level") or "unknown")
            tooltip = "\n".join(
                f"{_item_mark(item)} {item.get('name', '')}: {item.get('text', '')}"
                for item in section.get("items") or ()
            )
            headline = str(section.get("headline") or "")
            # «Голосовые звонки: отвечают 1 из 2…» — название уже в первой колонке.
            if headline.startswith("Голосовые звонки: "):
                headline = headline[len("Голосовые звонки: "):]
            self._add_row(title, level, _SECTION_WORDS[key].get(level, ""), headline, tooltip)
        ipv6 = report.get("ipv6")
        if ipv6:
            level, word = _IPV6_ROW.get(str(ipv6.get("state") or ""), ("unknown", "Не проверено"))
            text = f"IPv6 {ipv6.get('text', '')}".strip()
            self._add_row("IPv6", level, word, text, text)
        self._apply_theme_refresh()
        self._fit_height()
        broken = sum(1 for level in self._levels if level in ("fail", "warn"))
        set_state_text(
            self,
            f"Результаты BlockCheck по сайтам: {self.rowCount()} строк, с проблемами {broken}",
        )

    def _fit_height(self) -> None:
        """Таблица во всю высоту: строки «Звонки» и «Обрыв 16 КБ» не прячутся под прокруткой."""
        height = self.horizontalHeader().height() + 2 * self.frameWidth() + 4
        for row in range(self.rowCount()):
            height += self.rowHeight(row)
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        for row, level in enumerate(self._levels):
            item = self.item(row, 1)
            if item is None:
                continue
            color = tone_color(_level_tone(level), tokens)
            if color:
                item.setForeground(QColor(color))
