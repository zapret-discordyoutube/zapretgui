"""Присутствие окна: смотрит ли сейчас человек на программу и что делать при уходе и возврате.

Раньше за «окно ушло в фон» отвечали два независимых наблюдателя: один чистил
память, другой переключал приоритет потоков. Сам момент возврата никого не
касался: окно из панели задач разворачивает Windows, и программа узнавала об
этом, когда Qt уже начал рисовать. На слабом ПК после долгого фона это давало
«ничего не работает» на десятки секунд (в журналах win10 — «Интерфейс не
отвечает», блокировка 20–134 с, главный поток стоит то в одном, то в другом
месте): Windows за время фона отобрала память программы, и окно подтягивало её
с диска по 4 КБ за обращение.

Теперь одно место знает состояние и делает всё нужное на двух границах.

Уход в фон (свёрнуто или скрыто в трей). Самый ранний сигнал — ``away_intent``
(команда Windows «свернуть», сворачивание в трей): Windows ещё не успела
сжать рабочий набор свёрнутого окна, поэтому именно сейчас видно, какие
страницы программе нужны. Раньше это смотрели уже после ухода, когда набор был
сжат (в журнале пользователя: «память не удерживалась» или удержан обрезок).
- здесь же запоминается, сколько памяти программа держала на виду (самое
  большое значение за время показа: ``_hot_bytes``), и снимок нужных страниц;
- рабочий набор объявляется «жёстким минимумом» (``hold_working_set``) по этому
  размеру: Windows не отбирает эти страницы, пока окно скрыто, и возврат идёт
  из памяти, а не с диска. Через AWAY_SETTLE_MS размер уточняется;
- интервал переключения замка Python возвращается к обычному — рывков никто не
  видит, а ждущие замка потоки зря крутили бы процессор;
- через AWAY_SETTLE_MS, если окно всё ещё в фоне, снимок берётся ещё раз, но
  более скудный (уже сжатый) снимок не затирает полный, взятый на виду;
- если «свернуть» пришло не командой (Win+D и т. п.), сигналом служит событие
  Qt, а снимок и размер берутся из того, что запомнено через SAMPLE_AFTER_SHOW_MS
  после показа окна.

Сборки мусора и возврата кучи системе при уходе в фон здесь нет. Раньше они
были: через 2 минуты и затем каждые 30 минут. Но собирали они единицы объектов
(в журналах win10: 6, 18, 216), зато обходили всю кучу Python и сами тянули с
диска страницы, которые Windows уже отобрала (замер на настоящей сборке: рабочий
набор 66 → 119 МБ ради ~100 объектов; 40 мс, пока память на месте, и 613 мс
после выгрузки). Автоматическая сборка мусора Python и так идёт сама.

Возврат (самый ранний из сигналов: команда Windows «развернуть» ``SC_RESTORE``,
показ из трея, событие показа Qt):
- сразу включается приоритет окна над фоновыми потоками;
- хватка рабочего набора снимается (окно снова на виду, система вправе
  распоряжаться памятью как обычно);
- если окно было в фоне заметно долго, в отдельном потоке запрашивается
  подтягивание запомненных страниц большими пачками (PrefetchVirtualMemory) —
  до того, как главный поток начнёт ходить по ним по одной;
- через RETURN_REPORT_MS в журнал пишется итог возврата, чтобы на настоящем ПК
  было видно, сколько подкачек пришлось на возврат.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from PyQt6.QtCore import QEvent, QObject, QTimer

from log.log import log
from ui.gui_thread_priority import set_gui_gil_priority
from platforms import Cap, current as current_platform
from platforms.base import PrefetchResult, WorkingSetHold, WorkingSetSnapshot
from platforms.hold_policy import hold_size_for


# Сколько окно должно пробыть в фоне, прежде чем запомнить страницы: короткое
# сворачивание и разворот ничего не стоят.
AWAY_SETTLE_MS = 5 * 1000
# Короче этого срока Windows страницы не отбирает, подтягивать нечего.
MIN_AWAY_FOR_PREFETCH_S = 20.0
# Через сколько после возврата писать итог в журнал.
RETURN_REPORT_MS = 3 * 1000
# Через сколько после показа окна запомнить, сколько памяти и каких страниц ему
# нужно (окно уже отрисовано и «прогрелось»), — на случай ухода без команды «свернуть».
SAMPLE_AFTER_SHOW_MS = 4 * 1000
# Сколько ждать, что окно, собравшееся уйти, действительно ушло.
INTENT_CONFIRM_MS = 2 * 1000
# Пока окно скрыто, раз в столько проверяется, не выросла ли память программы
# настолько, что удержание пора увеличить. Запуск в трей: хватка берётся на
# пятой секунде, когда программа ещё не закончила стартовые работы (в журнале
# пользователя «удержано 181 МБ», а подтянуть при возврате пришлось ещё больше),
# и всё, что выросло потом, Windows вправе отобрать.
AWAY_REFRESH_MS = 60 * 1000
# На сколько должно вырасти, чтобы хватку пересчитывать.
HOLD_GROWTH_BYTES = 16 * 1024 * 1024

_WATCHED_EVENTS = (QEvent.Type.Hide, QEvent.Type.Show, QEvent.Type.WindowStateChange)


# Работа с памятью — дело платформы (Windows: WinAPI напрямую; раньше здесь подгружался
# psutil, и его первый импорт читал диск ровно на пути возврата из фона).
# Обёртки на уровне модуля нужны тестам: они подменяют эти имена.
def snapshot_working_set() -> WorkingSetSnapshot | None:
    return current_platform().working_set.snapshot_working_set()


def prefetch_working_set(snapshot: WorkingSetSnapshot | None) -> PrefetchResult | None:
    return current_platform().working_set.prefetch_working_set(snapshot)


def hold_working_set(size_bytes: int) -> WorkingSetHold | None:
    return current_platform().working_set.hold_working_set(size_bytes)


def release_working_set_hold() -> bool:
    return current_platform().working_set.release_working_set_hold()


def process_page_faults() -> int | None:
    return current_platform().working_set.process_page_faults()


def current_working_set_bytes() -> int | None:
    return current_platform().working_set.current_working_set_bytes()


def available_physical_bytes() -> int | None:
    return current_platform().working_set.available_physical_bytes()


_page_faults = process_page_faults


def _run_in_thread(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="window-presence", daemon=True).start()


class WindowPresence(QObject):
    """Следит за окном и делает всё нужное при уходе в фон и при возврате."""

    def __init__(
        self,
        window,
        *,
        settle_ms: int = AWAY_SETTLE_MS,
        report_ms: int = RETURN_REPORT_MS,
        sample_ms: int = SAMPLE_AFTER_SHOW_MS,
        min_away_for_prefetch_s: float = MIN_AWAY_FOR_PREFETCH_S,
        apply_priority: Callable[[bool], object] = set_gui_gil_priority,
        take_snapshot: Callable[[], WorkingSetSnapshot | None] = snapshot_working_set,
        prefetch: Callable[[WorkingSetSnapshot | None], PrefetchResult | None] = prefetch_working_set,
        hold: Callable[[], WorkingSetHold | None] | None = None,
        release_hold: Callable[[], object] = release_working_set_hold,
        start_worker: Callable[[Callable[[], None]], object] = _run_in_thread,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._report_ms = int(report_ms)
        self._min_away_for_prefetch_s = float(min_away_for_prefetch_s)
        self._apply_priority = apply_priority
        self._take_snapshot = take_snapshot
        self._prefetch = prefetch
        # Без своей подмены удерживаем по размеру, запомненному на виду.
        self._hold = hold if hold is not None else self._hold_hot_set
        self._hot_bytes = 0
        self._release_hold = release_hold
        self._start_worker = start_worker
        self._clock = clock

        self._away = True
        self._away_since: float | None = None
        self._snapshot: WorkingSetSnapshot | None = None
        self._snapshot_lock = threading.Lock()
        self._last_prefetch: PrefetchResult | None = None
        # Хватку включает и снимает рабочий поток; замок держит их по порядку, а
        # проверка «окно всё ещё в фоне» внутри замка не даёт хватке пережить возврат.
        self._hold_lock = threading.Lock()
        self._held: WorkingSetHold | None = None
        self._return_faults: int | None = None
        self._return_away_s = 0.0
        self._return_hold: WorkingSetHold | None = None

        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(int(settle_ms))
        self._settle_timer.timeout.connect(self._on_settle_timeout)

        self._sample_timer = QTimer(self)
        self._sample_timer.setSingleShot(True)
        self._sample_timer.setInterval(int(sample_ms))
        self._sample_timer.timeout.connect(self._on_sample_timeout)

        self._intent_timer = QTimer(self)
        self._intent_timer.setSingleShot(True)
        self._intent_timer.setInterval(INTENT_CONFIRM_MS)
        self._intent_timer.timeout.connect(self._on_intent_timeout)

        self._away_refresh_timer = QTimer(self)
        self._away_refresh_timer.setInterval(AWAY_REFRESH_MS)
        self._away_refresh_timer.timeout.connect(self._on_away_refresh)

        self._report_timer = QTimer(self)
        self._report_timer.setSingleShot(True)
        self._report_timer.setInterval(self._report_ms)
        self._report_timer.timeout.connect(self._report_return)

        window.installEventFilter(self)
        self._sync(initial=True)
        if not self._away:
            self._sample_timer.start()

    # ---- состояние -----------------------------------------------------

    @property
    def away(self) -> bool:
        return self._away

    def _window_on_screen(self) -> bool:
        window = self._window
        return bool(window.isVisible()) and not bool(window.isMinimized())

    def _sync(self, *, initial: bool = False) -> None:
        on_screen = self._window_on_screen()
        if on_screen and self._away:
            self._enter_shown()
        elif not on_screen and (not self._away or initial):
            self._enter_away()

    def eventFilter(self, obj, event):  # noqa: N802 (Qt API)
        if obj is self._window and event.type() in _WATCHED_EVENTS:
            self._sync()
        return False

    def return_intent(self) -> None:
        """Окно сейчас вернут (команда Windows «развернуть», показ из трея).

        Это самый ранний сигнал: Qt ещё ничего не показал и не нарисовал, а
        подтягивание страниц уже может идти. Повторные вызовы и позднее
        событие показа ничего не повторяют.
        """
        if self._away:
            self._enter_shown()

    def away_intent(self) -> None:
        """Окно сейчас уйдёт в фон (команда Windows «свернуть», сворачивание в трей).

        Windows сжимает рабочий набор свёрнутого окна сразу, поэтому защита
        ставится здесь, пока память на месте: запоминается, сколько программа
        держала на виду, тут же (в потоке окна, это один быстрый вызов, а фоновый
        поток мог бы не успеть) ставится удержание, а снимок страниц берётся в
        фоне — удержание уже не даст набору сжаться. Состояние «в фоне» сам этот
        сигнал не ставит: сворачивание могло не состояться (трей выключен, окно
        осталось). Его по-прежнему переключают события Qt; если окно так и не
        ушло, защита снимается через INTENT_CONFIRM_MS.
        """
        if self._away:
            return
        self._remember_hot_bytes()
        with self._hold_lock:
            try:
                self._held = self._hold()
            except Exception:
                self._held = None
        self._start_worker(lambda: self._store_snapshot(from_view=True))
        self._intent_timer.start()

    def _on_intent_timeout(self) -> None:
        """Окно, которое собралось уйти, осталось на экране: защита больше не нужна."""
        if not self._away:
            self._held = None
            self._start_worker(self._release_if_shown)

    def _remember_hot_bytes(self) -> None:
        try:
            current = current_working_set_bytes() or 0
        except Exception:
            current = 0
        self._hot_bytes = max(self._hot_bytes, int(current))

    def _hold_hot_set(self) -> WorkingSetHold | None:
        """Удержать столько, сколько программа держала на виду (в разумных пределах)."""
        try:
            current = current_working_set_bytes() or 0
        except Exception:
            current = 0
        size = hold_size_for(max(int(current), self._hot_bytes), available_physical_bytes())
        return None if size is None else hold_working_set(size)

    def _on_sample_timeout(self) -> None:
        if self._away:
            return
        self._remember_hot_bytes()
        self._start_worker(lambda: self._store_snapshot(from_view=True))

    # ---- границы --------------------------------------------------------

    def _enter_away(self) -> None:
        self._away = True
        self._away_since = self._clock()
        self._report_timer.stop()
        self._sample_timer.stop()
        self._intent_timer.stop()
        self._safe_apply_priority(False)
        self._settle_timer.start()
        self._away_refresh_timer.start()
        self._start_worker(self._hold_if_away)

    def _on_settle_timeout(self) -> None:
        if self._away:
            self._start_worker(self._hold_if_away)
            self._start_worker(self._store_snapshot)

    def _on_away_refresh(self) -> None:
        if self._away:
            self._start_worker(self._refresh_hold_if_grown)

    def _refresh_hold_if_grown(self) -> None:
        """Память выросла после того, как хватка была взята: берёт её заново, побольше."""
        try:
            current = int(current_working_set_bytes() or 0)
        except Exception:
            return
        held = self._held
        if held is not None and held.ok and current < held.bytes + HOLD_GROWTH_BYTES:
            return
        self._hold_if_away()
        self._store_snapshot()

    def _hold_if_away(self) -> None:
        with self._hold_lock:
            if not self._away:
                return
            try:
                self._held = self._hold()
            except Exception:
                self._held = None

    def _release_if_shown(self) -> None:
        with self._hold_lock:
            if self._away:
                return
            try:
                self._release_hold()
            except Exception:
                pass

    def _store_snapshot(self, from_view: bool = False) -> None:
        """Запоминает страницы, нужные программе.

        Снимок, взятый на виду, полный. Взятый уже в фоне (Windows успела сжать
        набор) беднее: он заменяет запомненное, только если не меньше его.
        """
        try:
            snapshot = self._take_snapshot()
        except Exception:
            snapshot = None
        if snapshot is None:
            return
        with self._snapshot_lock:
            current = self._snapshot
            if from_view or current is None or snapshot.total_bytes >= current.total_bytes:
                self._snapshot = snapshot

    def _enter_shown(self) -> None:
        self._away = False
        self._settle_timer.stop()
        self._away_refresh_timer.stop()
        self._sample_timer.start()
        self._safe_apply_priority(True)
        since, self._away_since = self._away_since, None
        away_s = 0.0 if since is None else max(0.0, self._clock() - since)
        with self._snapshot_lock:
            snapshot = self._snapshot
        self._last_prefetch = None
        held, self._held = self._held, None
        self._return_hold = held
        if snapshot is not None and away_s >= self._min_away_for_prefetch_s:
            self._start_worker(lambda: self._run_prefetch(snapshot))
        self._start_worker(self._release_if_shown)
        if away_s >= self._min_away_for_prefetch_s:
            self._return_away_s = away_s
            self._return_faults = _page_faults()
            self._report_timer.start()

    def _run_prefetch(self, snapshot: WorkingSetSnapshot) -> None:
        try:
            self._last_prefetch = self._prefetch(snapshot)
        except Exception:
            self._last_prefetch = None

    def _report_return(self) -> None:
        faults_now = _page_faults()
        faults = "?" if faults_now is None or self._return_faults is None else str(faults_now - self._return_faults)
        prefetch = self._last_prefetch
        if prefetch is None:
            pulled = "страницы не подтягивались"
        else:
            pulled = f"подтянуто {prefetch.total_bytes / (1024 * 1024):.0f} МБ за {prefetch.elapsed_ms:.0f} мс"
        hold = self._return_hold
        if hold is None:
            kept = "память не удерживалась"
        elif hold.ok:
            kept = f"удержано {hold.bytes / (1024 * 1024):.0f} МБ"
        else:
            kept = f"удержать не вышло (код {hold.error})"
        log(
            f"Окно вернулось из фона ({self._return_away_s:.0f} с): {kept}; {pulled}; "
            f"подкачек за {self._report_ms / 1000:.0f} с после возврата: {faults}",
            "INFO",
        )

    def _safe_apply_priority(self, shown: bool) -> None:
        try:
            self._apply_priority(shown)
        except Exception:
            pass


def install_window_presence(window) -> WindowPresence | None:
    """None, если система сама хорошо держит память скрытого окна (нет возможности WORKING_SET)."""
    if not current_platform().has(Cap.WORKING_SET):
        return None
    return WindowPresence(window)


__all__ = [
    "AWAY_REFRESH_MS",
    "AWAY_SETTLE_MS",
    "HOLD_GROWTH_BYTES",
    "INTENT_CONFIRM_MS",
    "MIN_AWAY_FOR_PREFETCH_S",
    "RETURN_REPORT_MS",
    "SAMPLE_AFTER_SHOW_MS",
    "WindowPresence",
    "install_window_presence",
]
