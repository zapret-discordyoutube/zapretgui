"""Один прогон BlockCheck: общий пул потоков и всё, что узнали про один адрес.

- ``Run`` — то, что общее на весь прогон: пул потоков, общий срок, «Стоп»,
  учёт ответов эталонных DNS-серверов;
- ``Probe`` — всё, что собрано про один адрес сайта: ответы DNS, попытки
  соединения, уточняющие пробы. Выводы из этого (``kind``,
  ``address_confirmed``) считаются здесь же, чистыми свойствами;
- ``Stopped`` — проверку остановил человек.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

from diagnostics import block_cause, block_kind, protocol_probe, quic_probe, volume_probe
from diagnostics.limits import REFERENCE_AT_ONCE, RUN_DEADLINE
from diagnostics.services import Target
from diagnostics.tls_probe import KIND_CONNECT, ProbeResult
from diagnostics.verdict import DnsJudgement, ReachState
from utils.dns_reference import REFERENCE_RESOLVERS, ReferenceResolver
from utils.dns_wire import FAILURE_CANCELLED, DnsQueryResult, failure_text
from utils.socket_cancel import SocketCancel
from utils.windows_dns_query import DnsAnswer

ShouldStop = Callable[[], bool]

RECHECK_OPENED = "opened"
RECHECK_SAME = "same"


class Stopped(Exception):
    pass


@dataclass(slots=True)
class Probe:
    target: Target
    service: str
    host: str
    discovery_note: str = ""
    dns: DnsAnswer = field(default_factory=DnsAnswer)
    # Сколько из DNS_ATTEMPTS запросов получили «такого сайта нет».
    dns_nxdomain: int = 0
    hosts_ips: tuple[str, ...] = ()
    reference_ips: tuple[str, ...] = ()
    reference_ok: bool = False
    reference_ipv6: tuple[str, ...] = ()
    # HTTPS-запрос к адресу из hosts или DNS системы (проверка сертификата).
    local_check: ProbeResult | None = None
    # DNS ответил и адресом из эталона, и другим: запрос к этому другому
    # адресу решает, CDN это или подмена «через раз».
    suspect_check: ProbeResult | None = None
    # Итоговый запрос «открывается ли» и откуда взят его адрес.
    reach: ProbeResult | None = None
    reach_source: str = ""
    attempts: int = 0
    # Была ли запасная попытка по IPv6 и чем она кончилась.
    ipv6_result: ProbeResult | None = None
    judgement: DnsJudgement | None = None
    reach_state: ReachState = ReachState.UNKNOWN
    # Как именно блокируют (по имени сайта, по адресу, страницей провайдера), если удалось выяснить.
    cause: block_cause.Cause | None = None
    # Проходит ли QUIC (UDP 443) к этому сайту. None — не проверяли или проверку сняли.
    quic: quic_probe.QuicVerdict | None = None
    # Проходит ли по одному соединению больше 16 КБ. None — не проверяли:
    # сайт не открылся или главная страница и так больше.
    volume: volume_probe.VolumeVerdict | None = None
    # Тот же адрес по TLS 1.2, TLS 1.3 и HTTP отдельно. Пусто — не проверяли.
    protocols: tuple[protocol_probe.ProtocolLine, ...] = ()
    # Все попытки основного запроса по порядку: (адрес, исход).
    tried: tuple[tuple[str, str], ...] = ()
    # Все попытки кончились одинаково: ответа на соединение не было вовсе.
    tried_silent: bool = False
    # Адрес из файла hosts не ответил, а настоящий адрес сайта открылся.
    hosts_stale: bool = False
    # Чем кончилась повторная проверка поодиночке: ``RECHECK_OPENED`` — сайт открылся
    # (первый сбой дала нагрузка самой проверки), ``RECHECK_SAME`` — не открылся снова.
    rechecked: str = ""

    @property
    def browser_only(self) -> bool:
        """Обычное соединение рвут, а приветствие, какое шлёт Chrome, до сервера доходит.

        Человек открывает сайт браузером, поэтому такой сайт для него открывается:
        фильтр (или стратегия обхода) пропускает именно браузерное приветствие.
        """
        return any(line.code == protocol_probe.CODE_BROWSER_ONLY for line in self.protocols)

    def settle_protocols(self, lines: tuple) -> None:
        """Принимает строки «TLS 1.2 / TLS 1.3 / как Chrome / HTTP» и поправляет по ним вывод."""
        self.protocols = lines
        if self.reach_state == ReachState.DPI and self.browser_only:
            self.reach_state = ReachState.OK

    @property
    def address_confirmed(self) -> bool:
        """Несоединение перепроверено: молчат все адреса сайта, и другим путём он не открылся.

        Только тогда оно называется «баном по адресу». Одно неудачное соединение
        бывает из-за устаревшей записи в hosts, потерянного пакета или антивируса.
        """
        if not self.tried or any(kind != KIND_CONNECT for _ip, kind in self.tried):
            return False
        # Отказ адреса и ошибка системы (нет сети, запрет сетевого экрана) — не молчание:
        # так фильтр провайдера адреса не закрывает.
        if not self.tried_silent:
            return False
        # Нужна перепроверка: второй адрес или повтор на том же после паузы.
        if len(self.tried) < 2:
            return False
        # Адреса только из hosts — это проверка записи в hosts, а не сайта.
        if self.hosts_ips and {ip for ip, _kind in self.tried} <= set(self.hosts_ips):
            return False
        # QUIC к тому же адресу отвечает — дорога до адреса открыта, браузер сайт откроет.
        if self.quic is not None and self.quic.code == quic_probe.QUIC_OK:
            return False
        return True

    @property
    def kind(self) -> str:
        """Вид блокировки: по адресу, по имени сайта, обрыв после 16 КБ… Пусто — сайт открывается."""
        return block_kind.site_kind(
            self.reach_state.value,
            self.cause.code if self.cause else "",
            address_confirmed=self.address_confirmed,
        )


class Steps:
    """Ход проверки по шагам: сообщает экрану и запоминает, сколько шёл каждый шаг.

    Вызывается как функция: ``step(шаг, готово, всего)``. Время шагов попадает в
    конец отчёта — по нему видно, на что ушла проверка, а не приходится гадать.
    """

    def __init__(self, progress: Callable[[str, int, int], None] | None) -> None:
        self._progress = progress
        self._lock = threading.Lock()
        # Шаг → [когда начался, когда отметился последний раз].
        self._times: dict[str, list[float]] = {}

    def __call__(self, name: str, done: int = 1, total: int = 1) -> None:
        now = time.monotonic()
        with self._lock:
            self._times.setdefault(name, [now, now])[1] = now
        if self._progress is None:
            return
        try:
            self._progress(name, done, total)
        except Exception:
            pass

    def seconds(self) -> dict[str, float]:
        with self._lock:
            return {name: round(last - first, 1) for name, (first, last) in self._times.items()}

    def line(self, titles: dict[str, str]) -> str:
        parts = [f"{titles.get(name, name)} {value:.0f} с" for name, value in self.seconds().items()]
        return "⏱ По шагам: " + " · ".join(parts) if parts else ""


class Live:
    """Отчёт, который растёт по ходу проверки: единственное место, где лежит её итог.

    Каждый раздел кладётся сюда, как только готов (``put``), и тут же уходит
    наружу — экран рисует его сразу, не дожидаясь конца проверки. Итоговый отчёт
    — это тот же словарь, а не отдельная сборка: показанное по ходу и сохранённое
    в конце разойтись не могут.
    """

    def __init__(self, publish: Callable[[dict], None] | None, **start) -> None:
        self._publish = publish
        self._lock = threading.Lock()
        self.data: dict = dict(start)

    def put(self, **parts) -> dict:
        # Разделы кладут и рабочие потоки (сайты по мере готовности), поэтому под замком.
        with self._lock:
            self.data.update(parts)
            # Копия верхнего уровня: получатель не должен видеть, как словарь дополняется дальше.
            snapshot = dict(self.data)
        if self._publish is not None:
            try:
                self._publish(snapshot)
            except Exception:
                pass
        return self.data


class Lane:
    """Очередь задач с пределом «не больше ``limit`` одновременно».

    Задача, которой ещё не пришла очередь, просто лежит в списке и потока не
    занимает. Раньше предел держал семафор: каждая ждущая задача уже сидела в
    своём потоке, и полная проверка поднимала сотни потоков только ради ожидания.
    """

    def __init__(self, pool: ThreadPoolExecutor, limit: int) -> None:
        self._pool = pool
        self._limit = max(1, int(limit))
        self._lock = threading.Lock()
        self._waiting: deque[tuple[Future, Callable, tuple, dict]] = deque()
        self._running = 0
        self._closed = False

    def submit(self, fn, *args, **kwargs) -> Future:
        future: Future = Future()
        with self._lock:
            if self._closed:
                future.cancel()
                return future
            self._waiting.append((future, fn, args, kwargs))
        self._pump()
        return future

    def _pump(self) -> None:
        while True:
            with self._lock:
                if self._closed or self._running >= self._limit or not self._waiting:
                    return
                item = self._waiting.popleft()
                self._running += 1
            try:
                self._pool.submit(self._work, *item)
            except RuntimeError:
                # Пул уже закрыт («Стоп» или конец прогона): задача не состоится.
                with self._lock:
                    self._running -= 1
                item[0].cancel()

    def _work(self, future: Future, fn, args, kwargs) -> None:
        try:
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(fn(*args, **kwargs))
                except BaseException as error:
                    future.set_exception(error)
        finally:
            with self._lock:
                self._running -= 1
            self._pump()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            waiting, self._waiting = list(self._waiting), deque()
        for future, *_rest in waiting:
            future.cancel()


class Run:
    """Общие для одного прогона пул потоков, дедлайн и отмена.

    «Стоп» пользователя и истёкший общий лимит — разные вещи: после «Стопа»
    отчёт не печатается, а по лимиту незавершённые запросы снимаются, и отчёт
    выводится с пометкой, что часть проверок не успела.
    """

    def __init__(self, should_stop: ShouldStop | None, *, workers: int, deadline: float | None = None) -> None:
        self._should_stop = should_stop
        self._user_stopped = False
        self.timed_out = False
        self.probe_cancel = SocketCancel()
        # Сервер проверки обрыва → адрес, по которому к нему ходили.
        self.freeze_addresses: dict[str, str] = {}
        self._reference_lock = threading.Lock()
        # Эталонный сервер → [сколько раз ответил, сколько раз нет, последняя причина].
        self._reference: dict[ReferenceResolver, list] = {}
        self.deadline_seconds = RUN_DEADLINE if deadline is None else float(deadline)
        self.deadline = time.monotonic() + self.deadline_seconds
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="diag")
        self._lanes: list[Lane] = []
        # Шаг → отложенные задачи: (куда положить итог, функция, аргументы).
        self._staged: dict[str, list[tuple[Future, Callable, tuple, dict]]] = {}
        # Вопросы к эталонным DNS-серверам: каждый — отдельное шифрованное соединение.
        self.reference_lane = self.lane(REFERENCE_AT_ONCE)

    def _cancel_all(self) -> None:
        self.probe_cancel.cancel()

    def note_reference(self, resolver: ReferenceResolver, result: DnsQueryResult) -> None:
        # Снятый по «Стопу» или по лимиту времени запрос ничего не говорит о сервере.
        if result.failure == FAILURE_CANCELLED:
            return
        with self._reference_lock:
            entry = self._reference.setdefault(resolver, [0, 0, ""])
            if result.answered:
                entry[0] += 1
            else:
                entry[1] += 1
                entry[2] = failure_text(result)

    def reference_report(self) -> list[dict]:
        """Состояние эталонных серверов за прогон, в постоянном порядке."""
        with self._reference_lock:
            seen = {resolver: tuple(entry) for resolver, entry in self._reference.items()}
        report: list[dict] = []
        for resolver in REFERENCE_RESOLVERS:
            if resolver not in seen:
                continue
            answered, failed, reason = seen[resolver]
            report.append(
                {
                    "label": resolver.label,
                    "address": resolver.address,
                    # Сервер считается недоступным, только если не ответил ни разу.
                    "ok": answered > 0,
                    "answered": answered,
                    "failed": failed,
                    "reason": reason if not answered else "",
                }
            )
        return report

    def stopped(self) -> bool:
        if self._user_stopped:
            return True
        try:
            if self._should_stop is not None and self._should_stop():
                self._user_stopped = True
                self._cancel_all()
        except Exception:
            return False
        return self._user_stopped

    def expired(self) -> bool:
        if time.monotonic() < self.deadline:
            return False
        if not self.timed_out:
            self.timed_out = True
            # Всё, что ещё висит, снимаем: запросы и DNS вернутся сразу.
            self._cancel_all()
        return True

    def dns_cancelled(self) -> bool:
        return self.stopped() or self.expired()

    def submit(self, fn, *args, **kwargs) -> Future:
        return self.pool.submit(fn, *args, **kwargs)

    def lane(self, limit: int) -> Lane:
        """Очередь для однотипных задач: не больше ``limit`` разом, остальные ждут без потока."""
        lane = Lane(self.pool, limit)
        self._lanes.append(lane)
        return lane

    def later(self, stage: str, fn, *args, **kwargs) -> Future:
        """Откладывает задачу до своего шага (см. ``start_stages``). Итог придёт в возвращённый Future."""
        future: Future = Future()
        self._staged.setdefault(stage, []).append((future, fn, args, kwargs))
        return future

    def start_stages(self, order, note: Callable[[str, bool], None]) -> None:
        """Запускает отложенные задачи шаг за шагом: следующий шаг ждёт конца предыдущего.

        Внутри шага его задачи идут вместе. ``note(шаг, закончен)`` зовётся в начале
        и в конце шага — так экран показывает «идёт» и «готово» в тот момент, когда
        это происходит, а шаги не мешают друг другу и не грузят компьютер все разом.
        """

        def work() -> None:
            for stage in order:
                tasks = self._staged.pop(stage, [])
                note(stage, False)
                try:
                    started = [(target, self.pool.submit(fn, *args, **kwargs)) for target, fn, args, kwargs in tasks]
                except RuntimeError as error:
                    # Пул закрыт («Стоп» или конец прогона): шаг не состоится.
                    started = []
                    for target, *_rest in tasks:
                        target.set_exception(error)
                for target, future in started:
                    try:
                        target.set_result(future.result())
                    except BaseException as error:
                        target.set_exception(error)
                note(stage, True)

        self.submit(work)

    def wait(self, future: Future):
        """Ждёт результат, не пропуская «Стоп» и общий дедлайн."""
        while True:
            if self.stopped():
                raise Stopped()
            self.expired()
            try:
                return future.result(timeout=0.1)
            except TimeoutError:
                continue

    def close(self) -> None:
        self._cancel_all()
        for lane in self._lanes:
            lane.close()
        self.pool.shutdown(wait=False, cancel_futures=True)
