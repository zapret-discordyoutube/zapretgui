"""Видеосерверы YouTube, которые эта сеть получает на самом деле.

YouTube выбирает кэш Google для ролика по адресу того, кто смотрит (см. заметку про GGC), поэтому
список серверов нельзя взять из встроенных таблиц: проверять нужно именно те узлы, что вернулись
пользователю, который запустил проверку. Источников два, и они дополняют друг друга:

- страница ролика на youtube.com — то, что получил бы плеер, обычно несколько узлов;
- ``redirector.googlevideo.com/report_mapping`` по обычному HTTP — ближайший к вам узел. Этот ответ
  приходит и тогда, когда сам youtube.com закрыт, и тогда, когда страница грузится слишком медленно.

Список — сначала узлы плеера, затем узел редиректора; если нет ни тех, ни другого, видеосервер не
проверяется: общий адрес googlevideo.com не видеосервер. У каждого узла помнится, откуда он взят, чтобы отчёт мог сказать,
на чём держится вывод. Как и остальные «collect», модуль ничего не решает о блокировке: он только находит узлы.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from diagnostics import net_access, transfer_probe, volume_probe
from diagnostics.limits import DISCOVERY_READ_BUDGET_S, DISCOVERY_TIMEOUT, VIDEO_SERVERS
from diagnostics.services import YOUTUBE_HOST
from diagnostics.verdict import ReachState

__all__ = [
    "SOURCE_PLAYER",
    "SOURCE_REDIRECTOR",
    "VideoNodes",
    "combine",
    "discover",
    "goodput_volume",
    "is_google",
    "node_owner",
    "owner_note",
    "owner_text",
    "page_failure_reason",
    "spare_note",
]

SOURCE_PLAYER = "player"
SOURCE_REDIRECTOR = "redirector"

# Два настоящих ролика: если одна страница не открылась или не назвала узел, пробуется другая.
_WATCH_PAGES = ("/watch?v=dQw4w9WgXcQ&hl=en", "/watch?v=58HsQ3Nlknw&hl=en")
# Сколько адресов YouTube пробуется, пока страница не откроется, и общий срок поиска узла.
_PAGE_ADDRESSES = 3
_DISCOVERY_TOTAL_S = 8.0
_WATCH_PAGE_MAX_BYTES = 2_000_000
# Сколько ещё читать страницу после имён узлов, пока не встретится адрес замера скорости.
_PROBE_URL_WAIT_BYTES = 60_000
_MAPPING_PATH = "/report_mapping?di=no"
# Сколько ждать ответ редиректора: это одна короткая строка, живой ответ приходит за доли секунды.
MAPPING_TIMEOUT_S = 3.0

# Кто из владельцев сетей — сам Google: узел в такой сети не «кэш у провайдера».
_GOOGLE_ASNS = frozenset({"15169", "36040", "43515", "19527", "396982"})


@dataclass(frozen=True, slots=True)
class VideoNodes:
    # Видеосерверы по порядку проверки и то, откуда взят каждый (``SOURCE_*``).
    hosts: tuple[str, ...]
    sources: tuple[str, ...]
    # Пояснение для отчёта: откуда список и чем он ограничен.
    note: str
    # Адрес, по которому плеер сам замеряет скорость до узла: (узел, путь с подписью). Пусто — страница его не дала.
    goodput: tuple[str, str] = ("", "")

    def source_of(self, host: str) -> str:
        return dict(zip(self.hosts, self.sources, strict=False)).get(host, "")


def combine(
    player: Sequence[str],
    mapped: str,
    *,
    page_failure: str = "",
    limit: int = VIDEO_SERVERS,
    goodput: tuple[str, str] = ("", ""),
) -> VideoNodes:
    """Узлы плеера, затем узел редиректора (если он не повторяет их), иначе общий адрес.

    ``page_failure`` — почему страницы нет («страница YouTube не открылась»): без него узлы плеера
    описываются как полученные от YouTube.
    """
    hosts = list(dict.fromkeys(player))[:limit]
    sources = [SOURCE_PLAYER] * len(hosts)
    if mapped and mapped not in hosts:
        hosts.append(mapped)
        sources.append(SOURCE_REDIRECTOR)
    if player and mapped:
        note = (
            "адрес видеосервера получен от YouTube и совпал с ответом redirector.googlevideo.com"
            if mapped in player
            else "адрес видеосервера получен от YouTube; узел сети по redirector.googlevideo.com добавлен запасным"
        )
    elif player:
        note = "адрес видеосервера получен от YouTube"
    elif mapped:
        note = f"{page_failure or 'YouTube не назвал видеосервер'}; взят узел вашей сети по ответу redirector.googlevideo.com"
    else:
        # Голый googlevideo.com и redirector.googlevideo.com — не видеосерверы: прямой запрос к ним всегда
        # кончается ошибкой. Поэтому без настоящего узла видеосервер не проверяется, и отчёт так и говорит.
        return VideoNodes(
            (),
            (),
            f"{page_failure or 'YouTube не назвал видеосервер'}, а узел вашей сети узнать не удалось: "
            "видеосервер не проверялся (общий адрес googlevideo.com — не видеосервер и отвечает ошибкой)",
        )
    return VideoNodes(tuple(hosts), tuple(sources), note, goodput)


def _mapped_host(run) -> str:
    """Узел вашей сети по ответу редиректора. Пусто — не ответил или ответ не похож на настоящий."""
    from blockcheck.googlevideo_discovery import fetch_mapped_host_at, parse_report_mapping

    # Сначала шифрованный redirector.gvt1.com: он отвечает и там, где обычный HTTP к googlevideo.com молчит.
    # Адрес берётся так, как его взял бы браузер: hosts, DNS системы, а если не дал — шифрованный эталон.
    for address in net_access.browser_ipv4(run, "redirector.gvt1.com")[:2]:
        if run.dns_cancelled():
            return ""
        result = net_access.fetch(run, "redirector.gvt1.com", address, _MAPPING_PATH, timeout=MAPPING_TIMEOUT_S, read_limit=2048, decode_body=True)
        found = parse_report_mapping(bytes(result.body).decode("utf-8", errors="ignore")) if result.ok and result.body else ""
        if found:
            return found
    for address in net_access.browser_ipv4(run, "redirector.googlevideo.com")[:2]:
        if run.dns_cancelled():
            return ""
        found = fetch_mapped_host_at(address, "redirector.googlevideo.com", timeout=MAPPING_TIMEOUT_S)
        if found:
            return found
    return ""


def _read_page(run, ip: str, path: str, stop: threading.Event) -> tuple[tuple[str, ...], tuple[str, str], bool, object]:
    """Одна страница ролика по одному адресу YouTube. (узлы, адрес замера, вышел срок чтения, итог запроса).

    ``stop`` ставит тот, кто уже нашёл узлы: остальные попытки обрывают чтение.
    """
    from blockcheck.googlevideo_discovery import extract_googlevideo_hosts, extract_probe_url

    found: list[str] = []
    probe: list[tuple[str, str]] = []
    reading: list[float] = []
    late: list[bool] = []
    seen_at: list[int] = []

    def _done(body: bytes) -> bool:
        if stop.is_set() and not found:
            return True
        if b"googlevideo" in body:
            text = body.decode("utf-8", errors="ignore")
            hosts = extract_googlevideo_hosts(text)
            if hosts:
                if not seen_at:
                    seen_at.append(len(body))
                found[:] = list(hosts[:VIDEO_SERVERS])
                # Адрес замера скорости лежит рядом с адресом потока, но может прийти следующим куском.
                got = extract_probe_url(text)
                probe[:] = [got] if got[0] else []
                if probe or len(body) - seen_at[0] > _PROBE_URL_WAIT_BYTES:
                    return True
        if not reading:
            reading.append(time.monotonic())
        if time.monotonic() - reading[0] >= DISCOVERY_READ_BUDGET_S:
            late.append(True)
            return True
        return False

    # Имя видеосервера ищется в самой странице: её нужно распаковать.
    page = dict(timeout=DISCOVERY_TIMEOUT, read_limit=_WATCH_PAGE_MAX_BYTES, body_done=_done, decode_body=True)
    result = net_access.fetch(run, YOUTUBE_HOST, ip, path, **page)
    return tuple(found), (probe[0] if probe else ("", "")), bool(late), result


def _player_hosts(run) -> tuple[tuple[str, ...], str, tuple[str, str]]:
    """Узлы со страницы ролика и адрес замера скорости. (узлы, почему страницы нет — пусто, если она была, замер).

    Адрес YouTube — тот, которым воспользовался бы браузер. Первый ролик пробуется на нескольких адресах сразу,
    второй — на первом: один сбой одного адреса не доказывает, что YouTube не открывается, а ждать адреса по
    очереди при блокировке значило бы тратить срок всей проверки. Кто первым нашёл узлы, тот и побеждает.
    """
    addresses = list(net_access.browser_ipv4(run, YOUTUBE_HOST))[:_PAGE_ADDRESSES]
    attempts = [(ip, _WATCH_PAGES[0]) for ip in addresses]
    if addresses and len(_WATCH_PAGES) > 1:
        attempts.append((addresses[0], _WATCH_PAGES[1]))
    if not attempts:
        return (), "страница YouTube не открылась: адрес YouTube не определился", ("", "")
    stop = threading.Event()
    failure = "страница YouTube не открылась"
    named_nothing = False
    pool = ThreadPoolExecutor(max_workers=len(attempts), thread_name_prefix="video-page")
    try:
        futures = {pool.submit(_read_page, run, ip, path, stop): ip for ip, path in attempts}
        deadline = time.monotonic() + _DISCOVERY_TOTAL_S
        pending = set(futures)
        while pending and time.monotonic() < deadline:
            if run.dns_cancelled():
                return (), "проверка остановлена до того, как страница YouTube открылась", ("", "")
            done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    hosts, probe, late, result = future.result()
                except Exception:
                    continue
                if hosts:
                    return hosts, "", probe
                if late:
                    failure = "страница YouTube грузилась слишком медленно"
                elif getattr(result, "ok", False):
                    named_nothing = True
                else:
                    failure = f"страница YouTube не открылась ({page_failure_reason(result)}, адрес {futures[future]})"
    finally:
        stop.set()
        pool.shutdown(wait=False, cancel_futures=True)
    if named_nothing:
        return (), "YouTube не назвал видеосервер", ("", "")
    return (), failure, ("", "")


def page_failure_reason(result) -> str:
    """Почему страница не открылась — словами, по виду сбоя запроса."""
    kind = str(getattr(result, "kind", "") or "")
    status = getattr(result, "status", None)
    if kind == "cancelled":
        return "проверка остановлена"
    if kind == "timeout":
        return "нет ответа за отведённое время"
    if kind == "reset":
        return "соединение сброшено"
    if kind == "connect":
        return "соединение не установилось"
    if kind in ("tls", "cert"):
        return "защищённое соединение не сложилось"
    if status and int(status) >= 400:
        return f"сайт ответил кодом {int(status)}"
    return "запрос не удался"


def discover(run, *, player: Callable | None = None, mapped: Callable | None = None) -> VideoNodes:
    """Узлы, которые YouTube отдаёт этой сети. ``player`` и ``mapped`` — подмена источников для тестов."""
    # Редиректор и страница ролика независимы: спрашиваются одновременно, а не друг за другом.
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="video-nodes") as pool:
        mapped_future = pool.submit(mapped or _mapped_host, run)
        player_future = pool.submit(player or _player_hosts, run)
        mapped_host = mapped_future.result()
        hosts, failure, *rest = player_future.result()
    return combine(hosts, mapped_host, page_failure=failure, goodput=rest[0] if rest else ("", ""))


def goodput_volume(run, probe) -> None:
    """Объём и скорость по настоящему адресу замера, который плеер YouTube получил для вас.

    Видеоузел на ``generate_204`` ответа не имеет, и объём по нему не проверить. Запрос замера скорости —
    настоящий, подписан под ваш адрес и отвечает без входа, поэтому серия таких запросов даёт и объём, и паузы.
    """
    host, path = run.video_goodput
    if not host or not path or run.dns_cancelled():
        return
    if probe.volume is not None and probe.volume.code != volume_probe.VOLUME_UNKNOWN:
        return
    ips = net_access.browser_ipv4(run, host)
    if not ips:
        return
    facts = volume_probe.collect(host, ips[0], path, cancel=run.probe_cancel)
    if run.dns_cancelled():
        return
    verdict = volume_probe.judge(facts)
    if verdict.code == volume_probe.VOLUME_UNKNOWN:
        return
    probe.volume = verdict
    probe.transfer = transfer_probe.judge(facts.first.samples)
    probe.unstable = probe.unstable or transfer_probe.shaky_reason(probe.transfer)
    if verdict.code == volume_probe.VOLUME_CUT:
        probe.reach_state = ReachState.FREEZE
    probe.discovery_note = "; ".join(
        part for part in (probe.discovery_note, f"объём и скорость сняты по запросу замера скорости самого YouTube ({host})") if part
    )


def node_owner(run, ip: str):
    """Владелец сети узла (``utils.ip_owner.IpOwner``). None — узнать не удалось."""
    from utils.ip_owner import lookup_ip_owner

    try:
        owner = lookup_ip_owner(ip, lambda name, rtype: net_access.doh_ask(run, name, rtype))
    except Exception:
        return None
    return owner if owner is not None and owner.asn else None


def is_google(owner) -> bool:
    """Сеть самого Google, а не кэш Google внутри сети провайдера."""
    return owner is not None and owner.asn in _GOOGLE_ASNS


def owner_text(owner) -> str:
    """Чья сеть у узла словами. Пусто — владельца нет."""
    if owner is None or not owner.asn:
        return ""
    name = f"AS{owner.asn}" + (f" ({owner.owner})" if owner.owner else "")
    if is_google(owner):
        return f"узел в сети Google, {name}"
    return f"узел в сети провайдера, {name}: это кэш Google внутри сети, а не сам Google"


def owner_note(run, ip: str) -> str:
    """Чья сеть у узла: кэш Google внутри провайдера или сам Google. Пусто — владельца узнать не удалось."""
    return owner_text(node_owner(run, ip))


def spare_note(first, spare) -> str:
    """Сравнение двух узлов: если первый отвечает с паузами, а запасной быстро, дело в самом узле."""
    first_pause = first.transfer is not None and first.transfer.level == "warn"
    spare_pause = spare.transfer is not None and spare.transfer.level == "warn"
    if spare.transfer is None or spare.transfer.code == "unknown":
        return ""
    if first_pause and not spare_pause:
        return f"запасной видеосервер {spare.host} отвечает быстро — дело в первом узле, а не в вашей линии"
    if first_pause and spare_pause:
        return f"запасной видеосервер {spare.host} тоже отвечает медленно — дело не в одном узле"
    return ""
