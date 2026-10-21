"""Открытое имя сайтов проекта: выбирается один раз и для обновления, и для кнопок со ссылками.

Основные имена ``git.zapret.moe`` и ``wiki.zapret.moe`` режут по TLS, а те же
сайты под именами ``git2…``/``wiki2…`` открываются. Здесь, и только здесь,
программа выбирает, какое имя использовать: по одному списку на семейство
(``git``, ``wiki``), запомненный ответ общий для всех, кто спрашивает (проверка
обновления, очередь, хранилище файлов, кнопки «Открыть»).

Все ссылки в программе пишутся на основных именах (``config/urls.py``), а
имя подменяется в последний момент. Два разных вопроса:

* ссылка для браузера (кнопки): ``resolve`` / ``open_in_browser`` — мгновенно, без
  сети. Проба из самой программы не показывает, что откроет браузер (фильтр
  провайдера может сбрасывать рукопожатие Python и пускать браузер), поэтому
  берётся имя, уже доказавшее себя, иначе лучшее из списка сервера;
* запрос самой программы (очередь обновлений, хранилище файлов): ``chosen_host`` /
  ``resolve_probed`` — гонка по именам тем же сетевым путём, не дольше срока.

Выбор готовится заранее (``warm_up`` при запуске).
"""

from __future__ import annotations

import threading
import webbrowser

import requests

from log.log import log
from utils.https_dns_fallback import request_with_dns_fallback
from zapret_update import mirrors

_LIST_TIMEOUT = (3, 3)
_USER_AGENT = "Zapret-Updater/5.0"

_lists: dict[str, mirrors.MirrorList] = {}
_guard = threading.Lock()


def _fetch(url: str) -> bytes | None:
    """Короткий запрос списка имён: одна попытка, мимо системного прокси."""
    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": None, "https": None}
    session.headers.update({"User-Agent": _USER_AGENT})
    try:
        response = request_with_dns_fallback(
            session, "GET", url, headers={"Accept": "application/json"}, timeout=_LIST_TIMEOUT, verify=True
        )
        return response.content if response.status_code == 200 else None
    finally:
        session.close()


def _report(message: str) -> None:
    log(f"[UPDATE] Имена сайтов проекта: {message}", "INFO")


def mirror_list(family: str = "git") -> mirrors.MirrorList:
    """Общий список имён семейства; один на всю программу."""
    with _guard:
        found = _lists.get(family)
        if found is None:
            found = _lists[family] = mirrors.MirrorList(_fetch, family=family, report=_report)
        return found


def chosen_host(family: str = "git") -> str:
    """Имя для запросов самой программы (очередь обновлений, хранилище файлов).

    Готовый выбор отдаётся сразу; иначе идёт гонка по именам (не дольше нескольких
    секунд, см. ``zapret_update.mirrors``) — она проверяет тем же сетевым путём, каким
    пойдёт запрос. Только из фонового потока.
    """
    return mirror_list(family).choose()


def best_host(family: str = "git") -> str:
    """Имя для ссылки, которую откроет браузер: мгновенно, без сети."""
    return mirror_list(family).best()


def warm_up() -> None:
    """Выбрать имена git и вики заранее, в фоне: узнать список сервера и рабочие имена.

    Дёшево (одна гонка на семейство, обычно доли секунды) и безопасно звать
    повторно: готовый выбор не пересчитывается.
    """
    for family in mirrors.FAMILIES:
        threading.Thread(target=chosen_host, args=(family,), name=f"site-names-{family}", daemon=True).start()


def resolve(url: str) -> str:
    """Тот же адрес под лучшим именем для браузера. Адрес не сайта проекта — как есть.

    Мгновенно и без сети: можно из любого потока, в том числе из окна.
    """
    if not mirrors.is_rewritable(url):
        return str(url)
    return mirrors.with_host(url, best_host(mirrors.family_of(mirrors.host_of(url))))


def resolve_probed(url: str) -> str:
    """Тот же адрес под именем, до которого дошла сама программа (для её запросов).

    Может ждать гонку (не дольше нескольких секунд): только из фонового потока.
    """
    if not mirrors.is_rewritable(url):
        return str(url)
    return mirrors.with_host(url, chosen_host(mirrors.family_of(mirrors.host_of(url))))


def open_in_browser(url: str) -> None:
    """Открыть адрес в браузере под лучшим именем. Не ждёт сети."""
    target = str(url)
    try:
        target = resolve(url)
        webbrowser.open(target)
        log(f"Открыта ссылка: {target}", "INFO")
    except Exception as exc:
        log(f"Не удалось открыть {target}: {type(exc).__name__}: {exc}", "WARNING")


__all__ = ["best_host", "chosen_host", "mirror_list", "open_in_browser", "resolve", "resolve_probed", "warm_up"]
