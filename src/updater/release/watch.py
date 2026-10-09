from __future__ import annotations

"""Сигнал о новом выпуске: «длинный запрос» к Forgejo и зеркалам.

Программа спрашивает сервер: «я знаю версию X — есть новее?». Сервер не
отвечает, пока новой версии нет, и отвечает в момент её выхода. Так
обновление доходит за минуты, а не при следующем запуске.

Сигнал — это ещё и разрешение. Пользователей у проекта около миллиона, и если
бы все программы пошли за установщиком разом, сервер встал бы. Поэтому
очередь на скачивание ведёт сервер: «можно обновляться» он говорит программам
в меру своего канала, а остальным отвечает «версия есть, вы в очереди»
(``queued``). Сама программа без этого разрешения ничего не ставит. Место в
очереди не теряется: сервер выдаёт «талон» (время постановки), и программа
называет его в следующих вопросах.

Версию сервер раздаёт по ступеням: сначала малой доле программ. Остальным он
отвечает «ждите» (``held``) — их очередь придёт, когда первые обновятся и
снова выйдут на связь. Для этого слушатель один раз добавляет к вопросу, чем
кончилось прошлое обновление (``updater.release.outcome``). Если человек
занят — игра на весь экран, идёт проверка сети — слушатель говорит об этом
серверу (``busy=1``), и тот разрешения не выдаёт: оно не пропадёт зря.
Заодно программа называет, чем занята (``act``: окно открыто, в трее, игра,
проверка) и включён ли обход (``run``): сервер считает это общим числом,
как и версии, — адресов и имён в вопросе нет.

Ответ несёт только номер версии и запускает обычную проверку
(``resolver.lookup_latest_release``): адрес, размер и SHA-256 установщика
программа по-прежнему берёт сама. Ложный сигнал ничего установить не может —
в худшем случае он стоит одной лишней проверки.

Слушатель держит одно соединение: сначала Forgejo, при неудаче — зеркала по
очереди. После ответа зеркала следующий вопрос снова идёт к Forgejo: зеркала
слабее и нужны только пока он недоступен. Первый вопрос к источнику — без
ожидания (``hold=0``): так сразу видно, на месте ли служба. Если она
недоступна, программа живёт как раньше — сама проверяет обновления и
предлагает их окном.

Модуль не импортирует Qt: слушатель живёт в своём фоновом (daemon) потоке.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlencode

from log.log import log

from ..channel_utils import normalize_update_channel
from ..versions import compare_versions, normalize_version
from .http import new_session, short_error


UPDATE_LOG_LEVEL = "🔁 UPDATE"

FORGEJO_WAIT_URL = "https://git.zapret.moe/api/zapret/wait"
MIRROR_WAIT_PATH = "/api/wait"

CONNECT_TIMEOUT_SECONDS = 8
# Вопрос без ожидания: сервер отвечает сразу.
PROBE_READ_TIMEOUT_SECONDS = 15
# Сервер держит запрос до четырёх с половиной минут; ждём немного дольше.
READ_TIMEOUT_SECONDS = 330
# Занятая программа спрашивает короче: освободилась — и через минуту уже
# просит разрешение по-настоящему.
BUSY_HOLD_SECONDS = 60
# Вместе с вопросом программа называет своё занятие, и сервер помнит его,
# пока держит вопрос. В первые минуты после запуска оно быстро меняется
# (обход ещё запускается, окно уходит в трей), поэтому программа спрашивает
# короче — иначе на сайте несколько минут висело бы состояние первых секунд.
WARMUP_SECONDS = 120.0
WARMUP_HOLD_SECONDS = 45
# Честный ответ «новостей нет» приходит не раньше срока ожидания сервера.
# Быстрый пустой ответ — признак неисправности (чужой прокси, старый сервер):
# без паузы слушатель завалил бы сервер запросами.
QUICK_ANSWER_SECONDS = 20.0
QUICK_ANSWER_PAUSE_SECONDS = 120.0
# После неудачи на одном источнике — короткая пауза и следующий источник.
NEXT_SOURCE_PAUSE_SECONDS = 3.0
# Не ответил никто: паузы растут до этого предела.
FIRST_ROUND_PAUSE_SECONDS = 60.0
MAX_ROUND_PAUSE_SECONDS = 15 * 60.0
# Автообновление выключено: слушатель не держит соединений и изредка
# смотрит, не включили ли его обратно.
DISABLED_RECHECK_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class WaitEndpoint:
    name: str
    url: str
    verify_ssl: bool


def wait_endpoints() -> tuple[WaitEndpoint, ...]:
    """Forgejo, затем зеркала в порядке из конфигурации сборки."""
    from .mirrors import mirror_servers
    from ..server_config import should_verify_ssl

    endpoints = [WaitEndpoint("Forgejo", FORGEJO_WAIT_URL, True)]
    for server in mirror_servers():
        name = str(server.get("name") or server.get("host") or "")
        endpoints.append(
            WaitEndpoint(
                name,
                f"https://{server['host']}:{server['https_port']}{MIRROR_WAIT_PATH}",
                bool(should_verify_ssl()),
            )
        )
    return tuple(endpoints)


class ReleaseWatcher:
    """Ждёт разрешение сервера обновиться и сообщает о нём один раз на версию.

    ``on_release(version)`` — сервер разрешил ставить версию новее известной.
    ``on_queued(version)`` — версия вышла, но очередь на скачивание ещё не
    дошла. Оба вызываются из фонового потока слушателя.
    """

    def __init__(
        self,
        *,
        channel: str,
        current_version: str,
        on_release: Callable[[str], None],
        on_queued: Callable[[str], None] = lambda _version: None,
        is_enabled: Callable[[], bool] = lambda: True,
        is_busy: Callable[[], bool] = lambda: False,
        activity: Callable[[], dict] = dict,
        pending_report: Callable[[], dict] = dict,
        report_delivered: Callable[[dict], None] = lambda _report: None,
        endpoints: Callable[[], tuple[WaitEndpoint, ...]] = wait_endpoints,
        session_factory: Callable[[], object] = new_session,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._channel = normalize_update_channel(channel)
        self._known = normalize_version(current_version)
        self._on_release = on_release
        self._on_queued = on_queued
        self._is_enabled = is_enabled
        self._is_busy = is_busy
        self._activity = activity
        self._pending_report = pending_report
        self._report_delivered = report_delivered
        # О какой версии уже сказано в журнале «ждём своей ступени».
        self._held_version = ""
        self._started_at: float | None = None
        self._endpoints = endpoints
        self._session_factory = session_factory
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Ответила ли служба: None — ещё не знаем, идёт первый вопрос.
        self._reachable: bool | None = None
        self._probed = threading.Event()
        self._queued_version = ""
        # Талон очереди: время, когда сервер поставил программу в очередь.
        self._ticket = 0

    @property
    def known_version(self) -> str:
        return self._known

    @property
    def reachable(self) -> bool | None:
        """True — сервер ведёт очередь обновлений для этой программы."""
        return self._reachable

    @property
    def queued_version(self) -> str:
        """Версия, которая вышла, но до скачивания которой очередь не дошла."""
        return self._queued_version

    def wait_until_probed(self, timeout: float) -> bool | None:
        """Ждёт итог первого вопроса к серверу не дольше ``timeout`` секунд."""
        self._probed.wait(max(float(timeout), 0.0))
        return self._reachable

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self.run, name="update-release-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Слушатель больше ни о чём не сообщит; висящий запрос бросается."""
        self._stop.set()

    def _pause(self, seconds: float) -> bool:
        """Пауза, которую прерывает остановка. True — пора выходить."""
        return self._stop.wait(max(float(seconds), 0.0))

    def _set_reachable(self, value: bool) -> None:
        self._reachable = value
        self._probed.set()

    def run(self) -> None:
        try:
            endpoints = tuple(self._endpoints())
        except Exception as exc:
            log(f"Слушатель выпусков не запущен: {exc}", "WARNING")
            self._set_reachable(False)
            return
        if not endpoints:
            self._set_reachable(False)
            return

        index = 0
        failed_in_a_row = 0
        connected_to = ""
        session = None
        try:
            while not self._stop.is_set():
                if not self._enabled():
                    if session is not None:
                        session.close()
                        session = None
                    connected_to = ""
                    self._set_reachable(False)
                    if self._pause(DISABLED_RECHECK_SECONDS):
                        return
                    continue

                endpoint = endpoints[index % len(endpoints)]
                if session is None:
                    session = self._session_factory()
                # Первый вопрос к источнику — без ожидания: по нему видно,
                # что служба на месте.
                probe = connected_to != endpoint.name
                started = self._clock()
                try:
                    answer = self._ask(session, endpoint, probe=probe)
                except Exception as exc:
                    session.close()
                    session = None
                    connected_to = ""
                    index += 1
                    failed_in_a_row += 1
                    if failed_in_a_row % len(endpoints):
                        pause = NEXT_SOURCE_PAUSE_SECONDS
                    else:
                        self._set_reachable(False)
                        rounds = failed_in_a_row // len(endpoints)
                        pause = min(FIRST_ROUND_PAUSE_SECONDS * 2 ** (rounds - 1), MAX_ROUND_PAUSE_SECONDS)
                        if rounds == 1:
                            log(
                                "Очередь обновлений на сервере сейчас недоступна "
                                f"({short_error(exc)}): программа проверяет обновления сама",
                                UPDATE_LOG_LEVEL,
                            )
                    if self._pause(pause):
                        return
                    continue

                failed_in_a_row = 0
                self._set_reachable(True)
                if probe:
                    connected_to = endpoint.name
                    log(f"Ждём сигнал о новой версии от источника {endpoint.name}", UPDATE_LOG_LEVEL)
                elif index % len(endpoints):
                    # Зеркало — запасной путь: отстояв на нём один полный
                    # вопрос, слушатель снова пробует Forgejo.
                    index = 0
                    connected_to = ""
                if self._stop.is_set():
                    return
                allowed = self._accept(answer)
                if probe or allowed:
                    continue
                # Вопрос с ожиданием вернулся слишком быстро и без разрешения.
                if self._clock() - started < QUICK_ANSWER_SECONDS:
                    if self._pause(QUICK_ANSWER_PAUSE_SECONDS):
                        return
        finally:
            if session is not None:
                session.close()

    def _enabled(self) -> bool:
        try:
            return bool(self._is_enabled())
        except Exception:
            return False

    def _call(self, callback: Callable, default):
        try:
            return callback()
        except Exception:
            return default

    def _ask(self, session, endpoint: WaitEndpoint, *, probe: bool) -> dict:
        params = {"channel": self._channel, "known": self._known}
        busy = bool(self._call(self._is_busy, False))
        if self._started_at is None:
            self._started_at = self._clock()
        if probe:
            params["hold"] = "0"
        elif self._clock() - self._started_at < WARMUP_SECONDS:
            params["hold"] = str(WARMUP_HOLD_SECONDS)
        elif busy:
            params["hold"] = str(BUSY_HOLD_SECONDS)
        if busy:
            params["busy"] = "1"
        told = self._call(self._activity, {})
        if isinstance(told, dict):
            params.update({key: str(told[key]) for key in ("act", "run", "scr") if told.get(key) not in (None, "")})
        report = self._call(self._pending_report, {})
        report = {str(key): str(value) for key, value in report.items()} if isinstance(report, dict) else {}
        params.update(report)
        if self._ticket:
            # С талоном программа стоит по времени первой постановки, а не
            # уходит в конец очереди при каждом новом вопросе.
            params["ticket"] = str(self._ticket)
        response = session.get(
            f"{endpoint.url}?{urlencode(params)}",
            timeout=(CONNECT_TIMEOUT_SECONDS, PROBE_READ_TIMEOUT_SECONDS if probe else READ_TIMEOUT_SECONDS),
            verify=endpoint.verify_ssl,
            headers={"Accept": "application/json", "Cache-Control": "no-cache"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("сервер ответил не тем, чего ждал слушатель")
        if report:
            # Сервер ответил — сообщение об исходе обновления он получил.
            try:
                self._report_delivered(report)
            except Exception as exc:
                log(f"Исход обновления не отмечен как отправленный: {exc}", "WARNING")
        return payload

    def _newer_version(self, answer: dict) -> str:
        """Версия из ответа, если она новее известной, иначе пустая строка."""
        try:
            version = normalize_version(str(answer.get("version") or ""))
            if compare_versions(self._known, version) < 0:
                return version
        except ValueError:
            pass
        return ""

    def _accept(self, answer: dict) -> bool:
        """True — сервер разрешил обновиться до версии новее известной."""
        version = self._newer_version(answer)
        if not version:
            return False
        if answer.get("changed"):
            # Запоминаем до вызова: что бы ни случилось с проверкой дальше,
            # об одной версии разрешение приходит один раз.
            self._known = version
            self._queued_version = ""
            self._ticket = 0
            log(f"Сервер разрешил обновиться до v{version}", UPDATE_LOG_LEVEL)
            self._notify(self._on_release, version)
            return True
        if answer.get("queued"):
            if self._queued_version != version:
                # Очередь у каждой версии своя: талон прежней не годится.
                self._ticket = 0
            if not self._ticket:
                try:
                    self._ticket = max(int(answer.get("ticket") or 0), 0)
                except (TypeError, ValueError):
                    self._ticket = 0
            if self._queued_version != version:
                self._queued_version = version
                try:
                    wait = max(int(answer.get("eta") or 0), 0)
                except (TypeError, ValueError):
                    wait = 0
                about = f", примерно {max(wait // 60, 1)} мин" if wait else ""
                log(f"Вышла версия v{version}: ждём очереди на скачивание{about}", UPDATE_LOG_LEVEL)
                self._notify(self._on_queued, version)
            return False
        held = str(answer.get("held") or "")
        if held and self._held_version != f"{held}:{version}":
            self._held_version = f"{held}:{version}"
            reason = {
                "stage": "сервер раздаёт её по ступеням, очередь этой программы ещё не подошла",
                "halted": "сервер остановил её раздачу",
                "busy": "программа занята — обновится, когда освободится",
            }.get(held, "сервер просит подождать")
            log(f"Вышла версия v{version}: {reason}", UPDATE_LOG_LEVEL)
        return False

    @staticmethod
    def _notify(callback: Callable[[str], None], version: str) -> None:
        try:
            callback(version)
        except Exception as exc:
            log(f"Сигнал о версии v{version} не обработан: {exc}", "WARNING")


__all__ = [
    "FORGEJO_WAIT_URL",
    "MIRROR_WAIT_PATH",
    "ReleaseWatcher",
    "WaitEndpoint",
    "wait_endpoints",
]
