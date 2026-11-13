"""Что проверяет BlockCheck: сервисы, их адреса и режимы проверки.

Сервис — то, что человек называет одним словом («Discord»). У него один или
несколько адресов: сайт, чат, картинки. Главный адрес (``main``) решает,
открывается ли сервис вообще.

Режимы:

- ``SCOPE_MAIN`` — Discord и YouTube, ради которых Zapret ставят чаще всего;
- ``SCOPE_ALL`` — ещё популярные сайты и контрольные;
- ``SCOPE_FULL`` — ещё длинный список сайтов, которые блокируют чаще всего
  (собран по спискам проектов dpi-detector и rkn-block-checker, лицензия MIT),
  и свои домены пользователя;
- ``service_scope(ключ)`` — один сервис («только YouTube»): его выбирают карточкой
  сервиса в BlockCheck. Остальные разделы проверки — как в коротком режиме.

Контрольные сайты нужны для сравнения: если не открываются и они, дело в
подключении, а не в блокировках.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

__all__ = [
    "EXTRA_SERVICES",
    "FULL_SERVICES",
    "GOOGLEVIDEO_FALLBACK_HOST",
    "SCOPE_ALL",
    "SCOPE_FULL",
    "SCOPE_MAIN",
    "SCOPE_TITLES",
    "PROGRESS_STEPS",
    "STEP_DNS_SERVERS",
    "STEP_FILTER",
    "STEP_HOSTINGS",
    "STEP_IPV6",
    "STEP_SITES",
    "STEP_SYSTEM",
    "STEP_VOICE",
    "progress_steps",
    "related_targets",
    "scope_service",
    "scope_title",
    "service_scope",
    "SERVICES",
    "YOUTUBE_HOST",
    "Service",
    "Target",
    "build_services",
    "site_service",
]

YOUTUBE_HOST = "www.youtube.com"
GOOGLEVIDEO_FALLBACK_HOST = "redirector.googlevideo.com"


@dataclass(frozen=True, slots=True)
class Target:
    host: str
    purpose: str
    path: str = "/"
    read_body: bool = False
    # Главный адрес сервиса: если он не открывается, не открывается сервис.
    main: bool = False
    # Хост видеосервера каждый раз узнаётся у YouTube: он свой у каждого
    # провайдера и меняется со временем.
    discover_googlevideo: bool = False


@dataclass(frozen=True, slots=True)
class Service:
    key: str
    label: str
    targets: tuple[Target, ...]
    # Контрольный сайт: его почти никогда не блокируют. Если не открываются
    # даже контрольные — дело в подключении, а не в блокировках.
    control: bool = False
    # Российский контрольный сайт. Если открываются только такие, а зарубежные
    # контрольные нет — провайдер пропускает лишь разрешённые адреса.
    domestic: bool = False


def _site(key: str, label: str, host: str, *, control: bool = False, domestic: bool = False) -> Service:
    return Service(
        key, label, (Target(host, "сайт", read_body=True, main=True),), control=control, domestic=domestic
    )


def site_service(host: str) -> Service:
    """Один сайт по его адресу — так же, как «свой домен» в BlockCheck.

    Если адрес принадлежит известному сервису из нескольких адресов (YouTube,
    Discord…), вместе с ним проверяются и остальные адреса сервиса: «youtube.com
    открывается» мало что значит, когда не отвечают видеосерверы.
    """
    host = str(host or "").strip().lower().rstrip(".")
    main = Target(host, "сайт", read_body=True, main=True)
    return Service(f"user:{host}", host, (main, *related_targets(host)))


def related_targets(host: str) -> tuple[Target, ...]:
    """Остальные адреса сервиса, которому принадлежит ``host``. Пусто — адрес сам по себе.

    Адрес принадлежит сервису, если он один из его адресов либо лежит в домене
    его главного адреса (``m.youtube.com`` — в домене ``www.youtube.com``).
    Главным в такой проверке остаётся введённый адрес, остальные — побочные.
    """
    host = str(host or "").strip().lower().rstrip(".")
    for service in (*SERVICES.values(), *EXTRA_SERVICES, *FULL_SERVICES):
        if len(service.targets) < 2:
            continue
        base = next((target.host for target in service.targets if target.main), service.targets[0].host)
        base = base.removeprefix("www.")
        if host != base and not host.endswith(f".{base}") and all(host != target.host for target in service.targets):
            continue
        return tuple(
            replace(target, main=False, read_body=False, purpose="основной адрес" if target.main else target.purpose)
            for target in service.targets
            if target.host != host
        )
    return ()


SCOPE_MAIN = "main"
SCOPE_ALL = "all"
# Всё, что в «Все сайты», плюс DNS-серверы и поиск места фильтра.
SCOPE_FULL = "full"
SCOPE_TITLES = {SCOPE_MAIN: "Discord и YouTube", SCOPE_ALL: "Все сайты", SCOPE_FULL: "Глубокая диагностика"}

# Шаги хода проверки: что и в каком порядке показывает экран, пока она идёт.
STEP_SITES = "sites"
STEP_HOSTINGS = "hostings"
STEP_VOICE = "voice"
STEP_IPV6 = "ipv6"
STEP_SYSTEM = "system"
STEP_DNS_SERVERS = "dns_servers"
STEP_FILTER = "filter"
PROGRESS_STEPS = (STEP_SITES, STEP_HOSTINGS, STEP_VOICE, STEP_IPV6, STEP_SYSTEM, STEP_DNS_SERVERS, STEP_FILTER)
# Шаги, которые идут только в полной проверке.
_FULL_ONLY_STEPS = (STEP_DNS_SERVERS, STEP_FILTER)


def progress_steps(scope: str) -> tuple[str, ...]:
    """Шаги хода проверки для режима: движок отмечает их, экран показывает строками."""
    if scope == SCOPE_FULL:
        return PROGRESS_STEPS
    return tuple(step for step in PROGRESS_STEPS if step not in _FULL_ONLY_STEPS)

# «Discord и YouTube» — то, ради чего Zapret ставят чаще всего.
SERVICES: dict[str, Service] = {
    "discord": Service(
        "discord",
        "Discord",
        (
            Target("discord.com", "сайт и вход", read_body=True, main=True),
            Target("gateway.discord.gg", "чат и статусы"),
            Target("cdn.discordapp.com", "картинки и файлы"),
        ),
    ),
    "youtube": Service(
        "youtube",
        "YouTube",
        (
            Target(YOUTUBE_HOST, "сайт", read_body=True, main=True),
            Target("i.ytimg.com", "превью видео", path="/generate_204"),
            # Через него плеер спрашивает, что и откуда играть: закрыт он — страница есть, а видео крутит загрузку.
            Target("youtubei.googleapis.com", "служба плеера"),
            Target(
                GOOGLEVIDEO_FALLBACK_HOST,
                "видео",
                path="/generate_204",
                discover_googlevideo=True,
            ),
        ),
    ),
}

# Добавляются в режиме «Все сайты».
EXTRA_SERVICES: tuple[Service, ...] = (
    Service(
        "telegram",
        "Telegram",
        (
            Target("telegram.org", "сайт", read_body=True, main=True),
            Target("web.telegram.org", "веб-версия"),
        ),
    ),
    _site("instagram", "Instagram", "www.instagram.com"),
    _site("facebook", "Facebook", "www.facebook.com"),
    _site("x", "X (Twitter)", "x.com"),
    _site("linkedin", "LinkedIn", "www.linkedin.com"),
    _site("spotify", "Spotify", "www.spotify.com"),
    _site("rutracker", "RuTracker", "rutracker.org"),
    _site("google", "Google", "www.google.com", control=True),
    _site("cloudflare", "Cloudflare", "www.cloudflare.com", control=True),
    _site("yandex", "Яндекс", "ya.ru", control=True, domestic=True),
    _site("vk", "ВКонтакте", "vk.com", control=True, domestic=True),
)


# Добавляются только в полной проверке: сайты, которые блокируют чаще всего.
# Отобраны по спискам dpi-detector (Runnin4ik) и rkn-block-checker (MayersScott),
# оба под лицензией MIT. Здесь нет сайтов, недоступных из России по решению
# самого сайта: они выглядели бы как блокировка провайдера.
FULL_SERVICES: tuple[Service, ...] = (
    Service(
        "whatsapp",
        "WhatsApp",
        (Target("web.whatsapp.com", "веб-версия", read_body=True, main=True), Target("www.whatsapp.com", "сайт")),
    ),
    _site("signal", "Signal", "signal.org"),
    _site("messenger", "Messenger", "www.messenger.com"),
    _site("twitch", "Twitch", "www.twitch.tv"),
    _site("soundcloud", "SoundCloud", "soundcloud.com"),
    _site("dailymotion", "Dailymotion", "www.dailymotion.com"),
    _site("patreon", "Patreon", "www.patreon.com"),
    Service(
        "github",
        "GitHub",
        (Target("github.com", "сайт", read_body=True, main=True), Target("raw.githubusercontent.com", "файлы")),
    ),
    _site("docker", "Docker Hub", "hub.docker.com"),
    _site("chatgpt", "ChatGPT", "chatgpt.com"),
    _site("deepl", "DeepL", "www.deepl.com"),
    _site("canva", "Canva", "www.canva.com"),
    _site("coursera", "Coursera", "www.coursera.org"),
    _site("proton", "Proton", "proton.me"),
    _site("torproject", "Tor Project", "www.torproject.org"),
    _site("amnezia", "Amnezia", "amnezia.org"),
    _site("meduza", "Meduza", "meduza.io"),
    _site("dw", "Deutsche Welle", "www.dw.com"),
    _site("bbc", "BBC", "www.bbc.com"),
    _site("svoboda", "Радио Свобода", "www.svoboda.org"),
    _site("moscowtimes", "The Moscow Times", "www.themoscowtimes.com"),
    _site("nnmclub", "NNM-Club", "nnmclub.to"),
    _site("rezka", "HDRezka", "rezka.ag"),
    _site("speedtest", "Speedtest", "www.speedtest.net"),
    _site("gosuslugi", "Госуслуги", "www.gosuslugi.ru", control=True, domestic=True),
)


_SERVICE_SCOPE_PREFIX = "service:"


def service_scope(key: str) -> str:
    """Режим «только этот сервис» — так его зовут движок, история и экран."""
    return f"{_SERVICE_SCOPE_PREFIX}{key}"


def scope_service(scope: str) -> Service | None:
    """Сервис режима «только он». ``None`` — режим не про один сервис или сервис неизвестен."""
    scope = str(scope or "").strip().lower()
    if not scope.startswith(_SERVICE_SCOPE_PREFIX):
        return None
    key = scope[len(_SERVICE_SCOPE_PREFIX):]
    return next((item for item in (*SERVICES.values(), *EXTRA_SERVICES, *FULL_SERVICES) if item.key == key), None)


def scope_title(scope: str) -> str:
    """Что проверяли — словами: «Все сайты», «YouTube». Неизвестный режим — как есть."""
    service = scope_service(scope)
    if service is not None:
        return service.label
    return SCOPE_TITLES.get(str(scope or ""), str(scope or ""))


def build_services(scope: str, user_domains=()) -> dict[str, Service]:
    """Сервисы для проверки: основные, при «Все сайты» — остальные и свои домены,
    при полной проверке — ещё длинный список часто блокируемых сайтов, в режиме
    одного сервиса — только он."""
    single = scope_service(scope)
    if single is not None:
        return {single.key: single}
    services = dict(SERVICES)
    scope = str(scope or "").strip().lower()
    if scope not in (SCOPE_ALL, SCOPE_FULL):
        # Свои домены — часть режима «Все сайты»: так написано на экране.
        return services
    for service in EXTRA_SERVICES + (FULL_SERVICES if scope == SCOPE_FULL else ()):
        services[service.key] = service
    known_hosts = {target.host for service in services.values() for target in service.targets}
    for domain in user_domains or ():
        host = str(domain or "").strip().lower().rstrip(".")
        if host and host not in known_hosts:
            known_hosts.add(host)
            services[f"user:{host}"] = _site(f"user:{host}", host, host)
    return services


