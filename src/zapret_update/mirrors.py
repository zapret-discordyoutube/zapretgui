"""Имена сайтов проекта и выбор открытого из них (общее ядро обновления).

Один и тот же сайт открывается под несколькими именами: основное
``git.zapret.moe`` и зеркала ``git2.zapret.moe``, ``git3.zapret.moe`` и так
далее; так же устроена вики (``wiki.zapret.moe``, ``wiki2…``). С 2026-10-19
российские провайдеры режут TLS по основному имени, а зеркала открываются,
поэтому программа, которой нужен сайт (проверка обновления, загрузка, кнопки со
ссылками), сама находит открытое имя.

Список имён отдаёт сам сервер: ``https://<любое имя>/_sites/mirrors.json``
(схема 1: ``families.<семейство>.preferred`` и ``families.<семейство>.names[]``
с признаками ``blocked``/``open``; семейства ``git`` и ``wiki``). Он пополняется
новыми зеркалами сам, поэтому свой длинный список держать не нужно: запасной
список ``BOOTSTRAP_HOSTS`` нужен только чтобы скачать этот файл (и как запас,
когда скачать его не вышло).

Адрес ссылок и проверки доверия остаются на основном имени: любое имя вида
``git*.zapret.moe`` приводится к нему функцией ``canonical_url``, а для обмена
подставляется открытое — ``with_host``. Подставить можно только имя того же
семейства: адрес вики под именем git не превращается. Модуль без Qt и сторонних
библиотек.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

CANONICAL_HOST = "git.zapret.moe"
FAMILIES = ("git", "wiki")
MIRRORS_PATH = "/_sites/mirrors.json"
# Запасной список: сначала зеркала, основное имя в конце (пометка «заблокировано»
# в списке сервера измерена из российских сетей, а не с этой машины, но на
# практике основное имя закрывают первым).
BOOTSTRAP_HOSTS = (*(f"git{number}.zapret.moe" for number in range(2, 9)), CANONICAL_HOST)
WIKI_BOOTSTRAP_HOSTS = (*(f"wiki{number}.zapret.moe" for number in range(2, 9)), "wiki.zapret.moe")
MAX_PUBLISHED_HOSTS = 16
LIST_TTL_S = 600.0
# Не ответило ни одно имя: запасной список держится недолго, гонка не повторяется.
FALLBACK_TTL_S = 60.0
# Выигравшее имя считается рабочим столько; потом выбор повторяется.
GOOD_TTL_S = 1800.0
# Гонка: имена спрашиваются параллельно, i-е — через i * RACE_STAGGER_S секунд (лучшее
# первым, остальные подключаются, только если оно молчит), не дольше срока.
RACE_DEADLINE_S = 4.0
RACE_STAGGER_S = 0.25
MAX_RACERS = 8

_HOST_RE = re.compile(r"^(git|wiki)\d*\.zapret\.moe$", re.IGNORECASE)


def family_of(host: str | None) -> str:
    """Семейство имени (``git`` или ``wiki``); пусто, если это не имя сайта проекта."""
    match = _HOST_RE.fullmatch(str(host or ""))
    return match.group(1).lower() if match else ""


def canonical_host(family: str) -> str:
    """Основное имя семейства: ``git.zapret.moe`` или ``wiki.zapret.moe``."""
    return f"{family}.zapret.moe" if family in FAMILIES else ""


def is_git_host(host: str | None) -> bool:
    """Имя git-сервера проекта: основное или зеркало (git, git2, git3, … .zapret.moe)."""
    return family_of(host) == "git"


def is_site_host(host: str | None) -> bool:
    """Имя любого сайта проекта: git или вики, основное или зеркало."""
    return bool(family_of(host))


def bootstrap_hosts(family: str) -> tuple[str, ...]:
    """Запасной список имён семейства: сначала зеркала, основное имя последним."""
    return WIKI_BOOTSTRAP_HOSTS if family == "wiki" else BOOTSTRAP_HOSTS


def _plain_https(parsed) -> bool:
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
    )


def host_of(url: str) -> str:
    """Имя хоста из адреса в нижнем регистре; пусто, если адрес не разобрать."""
    try:
        return (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def is_rewritable(url: str) -> bool:
    """Обычный https-адрес сайта проекта (без порта и учётных данных): имя в нём можно заменить."""
    try:
        parsed = urlsplit(str(url))
    except ValueError:
        return False
    return _plain_https(parsed) and bool(family_of(parsed.hostname))


def with_host(url: str, host: str) -> str:
    """Тот же адрес под другим именем того же сайта проекта.

    Меняется только имя хоста, и только у https-адреса сайта проекта без порта и
    учётных данных, на имя того же семейства (git на git, вики на вики). Иначе
    адрес возвращается как есть: подменить хост чужого адреса через этот модуль
    нельзя.
    """
    family = family_of(host)
    if not family:
        return str(url)
    try:
        parsed = urlsplit(str(url))
    except ValueError:
        return str(url)
    if not _plain_https(parsed) or family_of(parsed.hostname) != family:
        return str(url)
    return parsed._replace(netloc=host.lower()).geturl()


def canonical_url(url: str) -> str:
    """Адрес на любом имени сайта проекта приводится к основному (для проверок доверия)."""
    return with_host(url, canonical_host(family_of(host_of(url))))


def ordered_hosts(document: Any, family: str = "git") -> list[str]:
    """Имена семейства из списка сервера в порядке предпочтения; пусто, если список не разобрать.

    Сначала ``preferred`` (если он не помечен заблокированным), затем остальные,
    не помеченные заблокированными, в конце заблокированные: помечены они из
    российских домашних сетей, а с этой машины всё равно могут открываться.
    """
    try:
        entry = document["families"][family]
        items = [item for item in entry["names"] if isinstance(item, dict)]
        preferred = str(entry.get("preferred") or "").strip().lower()
    except (KeyError, TypeError, AttributeError):
        return []
    open_names: list[str] = []
    blocked_names: list[str] = []
    for item in items:
        name = str(item.get("name") or "").strip().lower()
        if family_of(name) != family or name in open_names or name in blocked_names:
            continue
        (blocked_names if item.get("blocked") is True else open_names).append(name)
    if preferred in open_names:
        open_names.remove(preferred)
        open_names.insert(0, preferred)
    return (open_names + blocked_names)[:MAX_PUBLISHED_HOSTS]


def reader_host(fetch: Callable[[str], bytes | None], family: str = "git") -> str:
    """Имя для ссылки в тексте, который читают люди (пост о выпуске, список изменений).

    Не гонка с этой машины, а ``preferred`` из списка сервера: его отмечают по
    замерам из российских домашних сетей, а машина сборки может видеть любое имя.
    Список берётся с первого ответившего имени; не ответило ни одно — первое
    запасное (нумерованное зеркало, а не основное имя, которое режут по TLS).
    """
    for host in bootstrap_hosts(family):
        try:
            body = fetch(f"https://{host}{MIRRORS_PATH}")
            ordered = ordered_hosts(json.loads(body), family) if body else []
        except Exception:
            continue
        if ordered:
            return ordered[0]
    return bootstrap_hosts(family)[0]


class MirrorList:
    """Открытые имена сайта (семейство ``git`` или ``wiki``) и выбор рабочего.

    Выбор — гонка, а не перебор: у имён параллельно (со сдвигом в долю секунды,
    лучшее имя первым) запрашивается ``/_sites/mirrors.json``, и первое
    ответившее верным документом выигрывает. Один такой запрос и доказывает, что
    имя открывается с этого компьютера, и отдаёт список имён сервера. Дольше
    ``RACE_DEADLINE_S`` гонка не идёт, сколько бы имён ни молчало: закрытое имя
    стоит программе секунд, а не минут, как при переборе по одному.

    ``fetch(url)`` — короткий (одна попытка) запрос без повторов, возвращает
    тело ответа или ``None``/бросает ошибку; вызывается из нескольких потоков
    сразу. Работает на маршруте вызывающего: прямом или через прокси.

    ``report(строка)`` — необязательный журнал: кто ответил и за сколько, и по
    какой причине не вышло с каждым именем (без него по записи «Forgejo
    недоступен» не понять, чем именно закрыты зеркала на этом компьютере).

    Все вызовы безопасны из любых потоков; одновременные вопросы ждут одну и ту
    же гонку, а не запускают свою.
    """

    def __init__(
        self,
        fetch: Callable[[str], bytes | None],
        *,
        family: str = "git",
        clock: Callable[[], float] = time.monotonic,
        report: Callable[[str], None] | None = None,
    ) -> None:
        self._fetch = fetch
        self._family = family
        self._clock = clock
        self._report = report
        self._lock = threading.RLock()
        self._hosts: list[str] | None = None
        self._expires_at = 0.0
        self._good: str | None = None
        self._good_until = 0.0
        # Гонку не повторяют, пока не истёк срок: неудача помнится недолго.
        self._failed_until = 0.0

    def remember(self, host: str) -> None:
        """Под этим именем только что получили ответ: пробовать его первым."""
        if family_of(host) == self._family:
            with self._lock:
                self._good = host.lower()
                self._good_until = self._clock() + GOOD_TTL_S

    def forget(self) -> None:
        with self._lock:
            self._hosts = None
            self._good = None
            self._failed_until = 0.0

    def hosts(self) -> list[str]:
        """Имена в порядке, в котором их пробовать."""
        with self._lock:
            now = self._clock()
            if self._hosts is None or now >= self._expires_at:
                self._refresh()
            hosts = list(self._hosts or bootstrap_hosts(self._family))
            if self._good in hosts:
                hosts.remove(self._good)
                hosts.insert(0, self._good)
            return hosts

    def choose(self) -> str:
        """Имя, под которым сайт открывается сейчас, и оно же запоминается.

        Запомненное имя отдаётся сразу (выбор делается один раз). Иначе идёт
        гонка (не дольше ``RACE_DEADLINE_S``). Не ответило ни одно — первое по
        порядку списка (пометка «заблокировано» в списке сервера измерена из
        российских сетей, поэтому это лучшая догадка), и следующие
        ``FALLBACK_TTL_S`` секунд гонка не повторяется: кнопка не должна каждый
        раз ждать.
        """
        with self._lock:
            if self._good is not None and self._clock() < self._good_until:
                return self._good
            self._good = None
            if self._clock() >= self._failed_until:
                self._refresh()
            hosts = self._hosts or list(bootstrap_hosts(self._family))
            return self._good or (hosts[0] if hosts else canonical_host(self._family))

    def best(self) -> str:
        """Имя для ссылки, которую откроет браузер: мгновенно, без сети и без ожидания гонки.

        Своя сеть программы (запрос из Python) и браузер доходят до сайта по-разному:
        фильтр провайдера может сбрасывать рукопожатие программы и пускать
        браузер, поэтому проба из программы не говорит, что откроет браузер, и
        кнопка не должна её ждать. Берётся имя, уже доказавшее себя (``remember``
        или победитель гонки), иначе первое имя из списка сервера (лучшее первым,
        закрытые из России последними), иначе запасной список.

        Читает состояние без блокировки: гонка в другом потоке не задерживает.
        """
        good = self._good
        if good is not None and self._clock() < self._good_until:
            return good
        hosts = self._hosts or bootstrap_hosts(self._family)
        return hosts[0] if hosts else canonical_host(self._family)

    def note_failure(self, host: str, exc: BaseException) -> None:
        """Журнал: под этим именем сервер не ответил (причина — для разбора на компьютере пользователя)."""
        self._say(f"{host}: {type(exc).__name__}: {str(exc)[:160]}")

    def _say(self, message: str) -> None:
        if self._report is None:
            return
        try:
            self._report(message)
        except Exception:
            pass

    def _refresh(self) -> None:
        """Гонка по именам: запоминает победителя и список сервера (или неудачу)."""
        started = time.monotonic()
        winner, ordered, failures = self._race()
        now = self._clock()
        if winner is None:
            self._hosts, self._expires_at = list(bootstrap_hosts(self._family)), now + FALLBACK_TTL_S
            self._failed_until = now + FALLBACK_TTL_S
            self._say("ни одно имя сервера не ответило, берётся запасной список; " + "; ".join(failures))
            return
        # Имя, под которым уже был настоящий обмен (``remember``), весомее гонки.
        if self._good is None or now >= self._good_until:
            self._good, self._good_until = winner, now + GOOD_TTL_S
        self._failed_until = 0.0
        if ordered:
            self._hosts, self._expires_at = ordered, now + LIST_TTL_S
        else:
            self._hosts, self._expires_at = list(bootstrap_hosts(self._family)), now + FALLBACK_TTL_S
        self._say(
            f"{winner} ответило за {time.monotonic() - started:.1f} с"
            + (f"; список имён: {', '.join(ordered)}" if ordered else "; списка имён нет, берётся запасной")
            + (f"; не ответили: {'; '.join(failures)}" if failures else "")
        )

    def _candidates(self) -> list[str]:
        """Кого спрашивать: имена из прошлого списка сервера, иначе запасной список."""
        names = list(self._hosts) if self._hosts else []
        if not names:
            names = list(bootstrap_hosts(self._family))
        if self._good in names:
            names.remove(self._good)
            names.insert(0, self._good)
        return names[:MAX_RACERS]

    def _race(self) -> tuple[str | None, list[str], list[str]]:
        """(победитель, имена из его списка, причины неудач). Не дольше ``RACE_DEADLINE_S``."""
        names = self._candidates()
        answers: queue.Queue = queue.Queue()
        stop = threading.Event()

        def ask(host: str, delay: float) -> None:
            if delay and stop.wait(delay):
                answers.put((host, None, None))
                return
            try:
                body = self._fetch(f"https://{host}{MIRRORS_PATH}")
                if not body:
                    raise ValueError("пустой ответ")
                document = json.loads(body)
                if not isinstance(document, dict):
                    raise ValueError("ответ не похож на список имён")
            except Exception as exc:  # noqa: BLE001 — любая причина идёт в журнал
                answers.put((host, None, f"{host}: {type(exc).__name__}: {str(exc)[:120]}"))
                return
            answers.put((host, document, None))

        for index, host in enumerate(names):
            threading.Thread(
                target=ask, args=(host, index * RACE_STAGGER_S), name="site-name-race", daemon=True
            ).start()
        failures: list[str] = []
        answered: set[str] = set()
        deadline = time.monotonic() + RACE_DEADLINE_S
        pending = len(names)
        try:
            while pending:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                try:
                    host, document, failure = answers.get(timeout=left)
                except queue.Empty:
                    break
                pending -= 1
                answered.add(host)
                if failure:
                    failures.append(failure)
                elif document is not None:
                    return host, ordered_hosts(document, self._family), failures
            silent = [host for host in names if host not in answered]
            failures.extend(f"{host}: нет ответа за {RACE_DEADLINE_S:.0f} с" for host in silent)
            return None, [], failures
        finally:
            stop.set()


__all__ = [
    "BOOTSTRAP_HOSTS",
    "CANONICAL_HOST",
    "FAMILIES",
    "MIRRORS_PATH",
    "MirrorList",
    "WIKI_BOOTSTRAP_HOSTS",
    "bootstrap_hosts",
    "canonical_host",
    "canonical_url",
    "family_of",
    "host_of",
    "is_git_host",
    "is_rewritable",
    "is_site_host",
    "ordered_hosts",
    "reader_host",
    "with_host",
]
