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
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

from diagnostics import block_cause, block_kind, protocol_probe, quic_probe, volume_probe
from diagnostics.limits import RUN_DEADLINE
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
        self.pool.shutdown(wait=False, cancel_futures=True)
