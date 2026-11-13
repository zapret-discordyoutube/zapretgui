"""Список проблем BlockCheck: что из найденного показать человеку и в каком порядке.

На входе — готовые выводы по каждому сайту и по отдельным проверкам (звонки,
обрыв, IPv6, компьютер, Telegram). На выходе — строки итога: что не так, чем
это подтверждено, что делать и какая кнопка нужна.

Здесь же два общих правила, которые отменяют частные советы:

- «нет интернета» — до контрольных сайтов нет дороги вовсе;
- «белые списки» — дорога есть только до российских сайтов.

Оба требуют, чтобы не ответил ни один эталонный DNS-сервер: это зарубежные
адреса, к которым программа ходит напрямую. В сеть модуль не ходит.
"""

from __future__ import annotations

from collections.abc import Callable

from diagnostics.levels import level_rank
from diagnostics import (
    block_cause,
    block_kind,
    ipv6_check,
    quic_probe,
    service_view,
    subnet_whitelist,
    system_state,
    telegram_check,
    video_fix_journal,
    video_fixes,
    video_sweep,
)
from diagnostics.run_context import Probe
from diagnostics.services import Service
from diagnostics.verdict import ADVICE_DNS, ADVICE_VIA_ZAPRET, DnsState, Level, ReachState, ServiceVerdict, advice_geo_site
from hosts.geo_sites import only_vpn_helps

ADVICE_IPV6 = (
    "Из-за этого сайты могут открываться с задержкой: браузер сначала пробует IPv6. Перезагрузите роутер; "
    "если не поможет — снимите галочку «IP версии 6» в свойствах сетевого адаптера Windows."
)
SYSTEM_PROBLEM_LEVEL = {system_state.LEVEL_FAIL: Level.FAIL, system_state.LEVEL_WARN: Level.WARN}
# Сайты, которые QUIC поддерживают наверняка: по ним отличают «у сайта нет QUIC» от «UDP 443 закрыт».
QUIC_CONTROL_HOSTS = ("www.google.com", "www.youtube.com", "www.cloudflare.com")
ADVICE_QUIC = (
    "В пресете должен быть profile для UDP 443 (QUIC) с этими сайтами. Проще всего выбрать готовый пресет, "
    "где он есть, или отключить QUIC в браузере (в Chrome: chrome://flags → Experimental QUIC protocol)."
)
ADVICE_BLOCKED_REFERENCE = (
    "Похоже, этот сервер закрыт у вашего провайдера. Не выбирайте его для защищённого DNS: "
    "Windows молча вернётся к обычным запросам, которые видны и подменяются."
)


# Блокировки, которые обходит стратегия Zapret.
BYPASSABLE = (ReachState.DPI, ReachState.FREEZE)



def _zapret_action(zapret_running: bool | None) -> str:
    """Кнопка у обходимой блокировки. «Запустить» — только когда точно известно, что Zapret не запущен."""
    return "start_zapret" if zapret_running is False else "strategy"


def _coverage_action(probes) -> str:
    """Кнопка вместо «Подобрать стратегию», когда подбор не поможет; пусто — подбор и нужен.

    Ни одно закрытое имя не ловит профиль со стратегией (``probe.coverage`` из «Профиль пресета»):
    Zapret эти соединения не трогает, и совет — перенести имя в профиль. Кнопка ведёт туда же:
    на «Профили пресета», а при оркестраторе — в его настройки (белый список).
    """
    coverages = [getattr(probe, "coverage", None) for probe in probes]
    if not coverages or any(item is None or (item.covered and item.strategy) for item in coverages):
        return ""
    return "orchestra_settings" if any(item.orchestra for item in coverages) else "profiles"


def burst_problems(burst) -> list[dict]:
    """Проблема «UDP замирает после первых пакетов». Пусто — заморозки нет или вывода нет."""
    if burst is None or burst.code != "freeze":
        return []
    return [
        problem(
            Level.WARN,
            f"UDP замирает: {burst.text}",
            ("Нажмите «Подобрать стратегию для звонков» — нужный пункт «Голосовые звонки» выберется сам.",),
            action="strategy_voice",
            kind=block_kind.KIND_VOICE,
            section=SECTION_VOICE,
        )
    ]


def _whitelist_kind(foreign: list[str], collected: dict[str, list[Probe]]) -> str:
    """Как именно устроен «белый список» — по прямым пробам к зарубежным контрольным сайтам."""
    causes = {
        probe.cause.code for key in foreign for probe in collected.get(key, ()) if probe.cause is not None
    }
    if causes == {block_cause.CAUSE_NAME_WHITELIST}:
        return (
            "к зарубежным адресам проходят только соединения с разрешёнными именами сайтов — "
            "с такими именами те же серверы отвечают"
        )
    if block_cause.CAUSE_BY_ADDRESS in causes:
        return (
            "соединение с зарубежными адресами устанавливается, но шифрование не проходит ни с каким именем, "
            "даже с разрешённым, — пропускают только разрешённые адреса"
        )
    return "провайдер пропускает только разрешённые адреса"


def no_geo_service(_host: str) -> str:
    return ""


# Разделы отчёта, из которых выводятся строки итога (ключи ``report[...]``).
SECTION_SERVICES = "services"
SECTION_FREEZE = "freeze"
SECTION_VOICE = "voice"
SECTION_TELEGRAM = "telegram"
SECTION_NETWORK = "network"
SECTION_IPV6 = "ipv6"
SECTION_SYSTEM = "system"
SECTION_DNS = "dns"
SECTION_DNS_SERVERS = "dns_servers"
SECTION_QUIC = "quic"
SECTION_SPEED = "speed"
SECTION_CROWD = "crowd"

# Ключ сайта Telegram в ``services``: закрытому по адресу Telegram помогает встроенный прокси.
TELEGRAM_SITE = "telegram"
_TELEGRAM_PROXY_KINDS = frozenset({block_kind.KIND_IP, block_kind.KIND_OTHER, block_kind.KIND_NO_CONNECT})


def problem(
    level: Level,
    text: str,
    advice=(),
    *,
    action: str = "",
    target: str = "",
    kind: str = block_kind.KIND_OTHER,
    title: str = "",
    evidence=(),
    parts: dict | None = None,
    site: str = "",
    section: str = "",
) -> dict:
    """Строка итога. ``kind`` — вид блокировки: по нему экран собирает строки в группы.

    ``site`` — ключ сайта из ``report["services"]``, о котором эта строка; ``section`` —
    раздел отчёта, из которого она выведена (``freeze``, ``voice``, ``ipv6``…). По ним
    экран находит карточку с подробностями — данными, а не разбором текста фразы.

    ``title`` — короткое название для строки внутри группы (вид блокировки там
    уже назван в заголовке). ``evidence`` — на чём основан вывод; эти же фразы
    стоят первыми в ``advice``. ``parts`` — та же фраза готовыми частями
    (``title``, ``servers``, ``note``) у находок про DNS-серверы; лежит отдельным
    ключом, потому что ``title`` самой проблемы — это название сайта.
    """
    record = {
        "level": level.value,
        "text": text,
        "advice": list(advice),
        "action": action,
        "target": target,
        "kind": kind,
        "title": title,
        "evidence": list(evidence),
    }
    if parts:
        record["parts"] = dict(parts)
    if site:
        record["site"] = site
    if section:
        record["section"] = section
    return record


# «Не открывается» у контрольного сайта — только когда до него нет дороги.
# Обрыв после 16 КБ или чужой сертификат — это блокировка, которую лечат
# иначе, а не «закрыты все зарубежные адреса».
_NO_ROAD = (ReachState.IP_BLOCK, ReachState.DPI)


def _control_keys(services: dict[str, Service]) -> tuple[list[str], list[str], list[str]]:
    """(все контрольные, зарубежные, российские) — ключи сервисов."""
    controls = [key for key, service in services.items() if service.control]
    return (
        controls,
        [key for key in controls if not services[key].domestic],
        [key for key in controls if services[key].domestic],
    )


def _control_down(collected: dict[str, list[Probe]], key: str) -> bool:
    # Только настоящий провал контрольного сайта: «не успели проверить» (лимит
    # времени) — не «нет интернета», иначе такой прогон спрятал бы найденные
    # блокировки остальных сайтов.
    probes = collected.get(key) or ()
    # TLS, который режут по ответу сервера, обходится малым окном: такой адрес не «закрыт», и режим
    # «белых списков» (где Zapret не помогает) по нему не назначается.
    return bool(probes) and all(
        probe.reach_state in _NO_ROAD
        and not (probe.cause is not None and probe.cause.code == block_cause.CAUSE_REPLY_WINDOW)
        for probe in probes
    )


def _foreign_reachable(reference: list[dict] | None) -> bool:
    # Эталонные DNS-серверы — зарубежные адреса, к которым программа ходит напрямую.
    # Если хоть один ответил, зарубежные адреса не закрыты и интернет есть.
    return any(item.get("ok") for item in reference or ())


def whitelist_suspected(
    services: dict[str, Service],
    verdicts: dict[str, ServiceVerdict],
    collected: dict[str, list[Probe]],
    reference: list[dict] | None,
) -> bool:
    """Признак «белых списков» по контрольным сайтам: российские открываются, зарубежные нет.

    Движок по нему решает, звать ли проверку подсетей (``subnet_whitelist``),
    которая этот признак подтверждает или снимает.
    """
    _controls, foreign, domestic = _control_keys(services)
    foreign_down = (
        bool(foreign) and all(_control_down(collected, key) for key in foreign) and not _foreign_reachable(reference)
    )
    domestic_up = bool(domestic) and all(verdicts[key].level == Level.OK for key in domestic)
    return foreign_down and domestic_up


def collect_problems(
    services: dict[str, Service],
    verdicts: dict[str, ServiceVerdict],
    collected: dict[str, list[Probe]],
    *,
    voice,
    freeze,
    zapret_running: bool | None,
    geo_service_for: Callable[[str], str] | None = None,
    reference: list[dict] | None = None,
    ipv6: ipv6_check.Ipv6Verdict | None = None,
    system: tuple[system_state.SystemItem, ...] = (),
    telegram: telegram_check.TelegramReport | None = None,
    subnets: subnet_whitelist.SubnetVerdict | None = None,
    cache_block: tuple[str, ...] = (),
    fix_journal: list[dict] | None = None,
) -> tuple[list[dict], list[str], list[str]]:
    """Итог для экрана: проблемы по важности, открывающиеся сервисы, подменённые DNS.

    О запущенных VPN и программах обхода итог говорит один раз, над строками
    (``summary_model.bypass_warning`` / ``bypass_note``), а не в каждой строке.
    """
    problems: list[dict] = []

    controls, foreign, domestic = _control_keys(services)
    whitelisted = whitelist_suspected(services, verdicts, collected, reference)
    if whitelisted and subnets is not None and subnets.code == subnet_whitelist.CODE_REFUTED:
        # Зарубежные сети отвечают: закрыты именно контрольные сайты, а не всё зарубежное.
        whitelisted = False
    offline = (
        bool(controls)
        and all(_control_down(collected, key) for key in controls)
        and not _foreign_reachable(reference)
    )
    names = ", ".join(services[key].label for key in foreign)
    if whitelisted:
        confirmed = subnets is not None and subnets.code == subnet_whitelist.CODE_CONFIRMED
        sites = (
            f"Открываются только российские сайты ({', '.join(services[key].label for key in domestic)}), "
            f"а зарубежные контрольные ({names}) — нет."
        )
        how = _whitelist_kind(foreign, collected)
        text = (
            f"{sites} Это режим «белых списков»: {subnets.text}; {how}"
            if confirmed
            else f"{sites} Похоже на режим «белых списков»: {how}"
        )
        problems.append(
            problem(
                Level.FAIL,
                text,
                (
                    "В таком режиме Zapret не помогает: закрыты сами адреса, а не отдельные сайты. "
                    "Обычно это временное ограничение, чаще в мобильных сетях — проверьте другую сеть.",
                ),
                kind=block_kind.KIND_NETWORK,
                section=SECTION_NETWORK,
            )
        )
    elif offline:
        problems.append(
            problem(
                Level.FAIL,
                f"Не открываются даже контрольные сайты ({', '.join(services[key].label for key in controls)}) — "
                "похоже, нет интернета или всё соединение режет антивирус, прокси или VPN",
                ("Проверьте подключение к интернету и повторите проверку.",),
                kind=block_kind.KIND_NETWORK,
                section=SECTION_NETWORK,
            )
        )
    # В обоих случаях причина общая и уже названа: совет «подберите стратегию»
    # у каждого сайта был бы неправдой и шумом.
    offline = offline or whitelisted

    # Сервисы идут в том же порядке, что и в отчёте: «Открываются: …» не должен
    # начинаться с сайтов, у которых просто подменён DNS. Проблемы по важности
    # сортируются в конце, и внутри одного уровня этот порядок сохраняется.
    working: list[str] = []
    for key, service in services.items():
        if service.control:
            continue
        verdict = verdicts[key]
        broken = [probe for probe in collected.get(key, ()) if probe.reach_state != ReachState.OK]
        # Сайт открылся по настоящему адресу, а запись в hosts ведёт на нерабочий: для человека это поломка.
        stale = [probe for probe in collected.get(key, ()) if probe.hosts_stale] if not broken else []
        # Сайт открылся, но через раз: это тоже повод для строки в итоге, и лечится он как блокировка.
        shaky = [probe for probe in collected.get(key, ()) if probe.unstable] if not (broken or stale) else []
        broken = broken or stale or shaky
        if verdict.level in (Level.FAIL, Level.WARN) and broken:
            if offline:
                # Без интернета «Zapret не обходит блокировку» у каждого сайта —
                # неправда и шум: причина одна, она уже написана первой строкой.
                continue
            advice = tuple(item for item in verdict.advice if item != ADVICE_DNS)
            # Стратегия помогает только от DPI и обрыва. При чужом сертификате,
            # недоступном адресе или без адреса кнопка подбора увела бы не туда.
            # Сайт, который не открылся и из-за рубежа, не блокируют — он лежит сам: подбор не поможет.
            # Видеосервер, который не пускают ни с каким именем, стратегия тоже не откроет — это доказано пробами.
            bypassable = next(
                (
                    probe
                    for probe in broken
                    if probe.reach_state in BYPASSABLE and probe.kind != block_kind.KIND_DOWN and not probe.strategy_futile
                ),
                None,
            )
            action = ""
            if bypassable is not None:
                action = _zapret_action(zapret_running)
                if action == "strategy":
                    action = (
                        _coverage_action(
                            [probe for probe in broken if probe.reach_state in BYPASSABLE and probe.kind != block_kind.KIND_DOWN]
                        )
                        or action
                    )
            target = (bypassable or broken[0]).host
            if stale:
                action = "hosts"
            if shaky:
                action = _zapret_action(zapret_running)
            # Гео-сайт сам ограничивает доступ из России: стратегия его не
            # чинит, и совет «подберите стратегию» увёл бы пользователя не туда.
            geo = next(
                ((probe.host, name) for probe in broken if (name := (geo_service_for or no_geo_service)(probe.host))),
                None,
            )
            if geo is not None:
                target, geo_service = geo
                advice = (advice_geo_site(geo_service),) + tuple(
                    item for item in advice if item not in ADVICE_VIA_ZAPRET
                )
                # Кнопка «Редактор hosts» — только тем, кому hosts помогает.
                action = "" if only_vpn_helps((geo_service,)) else "hosts"
            if not action and verdict.kind == block_kind.KIND_DNS:
                # Адрес сайта не узнать — помогает другой DNS: кнопка ведёт в его настройку.
                action = "dns"
            if not action and key == TELEGRAM_SITE and (verdict.kind or block_kind.KIND_OTHER) in _TELEGRAM_PROXY_KINDS:
                # Telegram закрывают по адресу: стратегия тут бессильна, а у программы есть свой прокси.
                action = "telegram_proxy"
            causes = tuple(
                dict.fromkeys(service_view.sentence(probe.cause.text) + "." for probe in broken if probe.cause is not None)
            )
            # Чужой сертификат: кто именно ответил вместо сайта и что с этим делать — первым делом.
            causes += tuple(dict.fromkeys(service_view.sentence(probe.unstable) + "." for probe in shaky))
            certs = [probe.cert for probe in broken if probe.cert is not None]
            causes = tuple(dict.fromkeys(service_view.sentence(item.text) + "." for item in certs)) + causes
            advice = tuple(dict.fromkeys(item.advice for item in certs if item.advice)) + advice
            problems.append(
                problem(
                    verdict.level,
                    verdict.headline,
                    causes + advice,
                    action=action,
                    target=target,
                    kind=verdict.kind or block_kind.KIND_OTHER,
                    # Сервис не открывается целиком — в группе хватит названия;
                    # «открывается, но не работают картинки» нужно сказать полностью.
                    title=service.label if verdict.level == Level.FAIL else "",
                    evidence=causes,
                    site=key,
                    section=SECTION_SERVICES,
                )
            )
        elif verdict.level == Level.UNKNOWN:
            if not offline:
                problems.append(problem(Level.UNKNOWN, verdict.headline, verdict.advice, site=key, section=SECTION_SERVICES))
        else:
            working.append(service.label)

    if freeze is not None and freeze.level in (Level.FAIL, Level.WARN):
        problems.append(
            problem(
                freeze.level,
                freeze.headline,
                freeze.advice,
                action=_zapret_action(zapret_running),
                kind=block_kind.KIND_CUT,
                section=SECTION_FREEZE,
            )
        )
    elif freeze is not None and freeze.level == Level.UNKNOWN and not offline:
        problems.append(problem(freeze.level, freeze.headline, freeze.advice))
    if voice is not None and voice.level != Level.OK and not offline:
        problems.append(
            problem(
                voice.level, voice.headline, voice.advice, action="strategy_voice", kind=block_kind.KIND_VOICE, section=SECTION_VOICE
            )
        )
    # Один молчащий дата-центр — не проблема для человека: приложение возьмёт другой.
    if telegram is not None and telegram.level == Level.FAIL and not offline:
        problems.append(
            problem(
                telegram.level,
                telegram.headline,
                telegram.advice,
                action="telegram_proxy",
                title="Telegram",
                section=SECTION_TELEGRAM,
            )
        )

    spoofed = [
        probe.host
        for probes in collected.values()
        for probe in probes
        if probe.judgement is not None and probe.judgement.state == DnsState.SPOOFED
    ]
    if spoofed:
        shown = ", ".join(spoofed[:5]) + (f" и ещё {len(spoofed) - 5}" if len(spoofed) > 5 else "")
        problems.append(
            problem(
                Level.WARN,
                f"DNS подменяет ответы для {shown}. Браузер с защищённым DNS этого не замечает, а программы, "
                "которые спрашивают адрес у Windows, эти сайты не откроют",
                (ADVICE_DNS,),
                action="dns",
                kind=block_kind.KIND_DNS,
                section=SECTION_DNS,
            )
        )
    # Сайты, которые QUIC заведомо поддерживают, — контроль: если молчат и они,
    # закрыт весь UDP 443, а не QUIC отдельных сайтов.
    quic_known = [
        probe
        for probes in collected.values()
        for probe in probes
        if probe.host in QUIC_CONTROL_HOSTS and getattr(probe, "quic", None) is not None
    ]
    quic_answers = any(
        getattr(probe, "quic", None) is not None and probe.quic.code == quic_probe.QUIC_OK
        for probes in collected.values()
        for probe in probes
    )
    if (
        len(quic_known) >= 2
        and all(probe.quic.code == quic_probe.QUIC_SILENT for probe in quic_known)
        and not quic_answers
        and not offline
    ):
        problems.append(
            problem(
                Level.WARN,
                "QUIC (UDP 443) не отвечает ни у одного сайта, включая "
                f"{', '.join(sorted(probe.host for probe in quic_known))}, которые его точно поддерживают, — "
                "похоже, UDP 443 закрыт целиком: провайдером, роутером или пресетом Zapret. Сайты открываются "
                "обычным соединением, но браузер сначала пробует QUIC и теряет на этом время",
                (ADVICE_QUIC,),
                kind=block_kind.KIND_QUIC,
                section=SECTION_QUIC,
            )
        )

    # QUIC заблокирован, а сам сайт открывается: это не «сайт не работает», но
    # браузер сначала пробует QUIC и переходит на обычное соединение с задержкой.
    quic_blocked_keys = [
        key
        for key, probes in collected.items()
        if any(
            probe.quic is not None
            and probe.quic.code == quic_probe.QUIC_BLOCKED_BY_NAME
            and probe.reach_state == ReachState.OK
            for probe in probes
        )
    ]
    quic_blocked = [services[key].label for key in quic_blocked_keys]
    if quic_blocked and not offline:
        problems.append(
            problem(
                Level.WARN,
                f"QUIC (UDP 443) не проходит по имени для: {', '.join(quic_blocked)}. Сайты открываются обычным "
                "соединением, но браузер сначала пробует QUIC, поэтому открытие и начало видео могут запаздывать"
                + (
                    ". Так режет провайдер, но так же действует и сам Zapret, если в пресете QUIC отключён намеренно"
                    if zapret_running
                    else ""
                ),
                (ADVICE_QUIC,),
                kind=block_kind.KIND_QUIC,
                # Подробности — на карточке первого названного сайта.
                site=quic_blocked_keys[0],
                section=SECTION_QUIC,
            )
        )
    # Неполадки самого компьютера показываются всегда: они объясняют и «нет интернета».
    for item in system:
        level = SYSTEM_PROBLEM_LEVEL.get(item.level)
        if level is not None:
            problems.append(
                problem(
                    level,
                    f"{item.title}: {item.text}",
                    (item.advice,) if item.advice else (),
                    kind=block_kind.KIND_SYSTEM,
                    section=SECTION_SYSTEM,
                )
            )
    if ipv6 is not None and ipv6.code == ipv6_check.IPV6_BROKEN and not offline:
        problems.append(
            problem(Level.WARN, f"IPv6 {ipv6.text}", (ADVICE_IPV6,), kind=block_kind.KIND_NETWORK, section=SECTION_IPV6)
        )
    for item in blocked_references(reference or []):
        problems.append(
            problem(
                Level.WARN,
                reference_text(item),
                (ADVICE_BLOCKED_REFERENCE,),
                action="dns",
                kind=block_kind.KIND_DNS,
                section=SECTION_DNS,
            )
        )
    if not offline:
        problems.extend(video_fix_problems(collected, cache_block=cache_block, journal=fix_journal or []))
    problems.sort(key=lambda item: level_rank(item["level"]))
    return problems, working, spoofed


# Пресет, на который ведёт кнопка исправления видео; вариант «(game filter)» выбирает страница по текущему пресету.
YOUTUBE_FIX_PRESET = "Default + YouTube no QUIC + wssize.txt"
YOUTUBE_SITE = "youtube"


# Названия кнопок исправлений видео — те же, что на кнопках итога (``check_results._ACTION_TEXT``).
_FIX_ACTION_NAMES = {
    "youtube_block_caches": "Закрыть кэши YouTube",
    "youtube_unblock_caches": "Вернуть кэши YouTube",
    "youtube_preset": "Включить пресет без QUIC",
}
# Какое действие стоит за исправлением: по нему журнал помнит, что в этой сети уже не помогло.
_FIX_ACTION = {
    video_fixes.FIX_BLOCK_CACHES: "youtube_block_caches",
    video_fixes.FIX_QUIC_OFF: "youtube_preset",
    video_fixes.FIX_WSSIZE_PRESET: "youtube_preset",
}


def _video_probes(collected: dict[str, list[Probe]]) -> list[Probe]:
    """Пробы видеосервера YouTube, у которых был обход. Итог собирают и из неполных данных — поля читаются бережно."""
    return [
        probe
        for probes in collected.values()
        for probe in probes
        if getattr(getattr(probe, "target", None), "discover_googlevideo", False) and getattr(probe, "video_sweep", ())
    ]


def video_sweep_of(collected: dict[str, list[Probe]]) -> list[dict]:
    """Обход видеосерверов этой проверки строками отчёта (как ``report_text.target_report["video_sweep"]``)."""
    return [node.as_dict() for probe in _video_probes(collected) for node in video_sweep.judge(probe.video_sweep)]


def load_video_fix_journal() -> list[dict]:
    """Журнал исправлений видео из настроек (``video_fix_journal.load``)."""
    return video_fix_journal.load()


def settle_video_fix(collected: dict[str, list[Probe]], journal: list[dict]) -> None:
    """Записать итог прошлого исправления видео: в этой сети не предлагать то, что не помогло."""
    _entry, verdict, _after = journal_verdict(collected, journal)
    if verdict:
        try:
            video_fix_journal.settle(verdict)
        except Exception:
            pass


def journal_verdict(collected: dict[str, list[Probe]], journal: list[dict]) -> tuple[dict | None, str, dict]:
    """(исправление без итога, итог, что стало). Итог пустой — сравнить не с чем: видео не проверяли или сеть другая."""
    entry = video_fix_journal.pending(journal)
    sweep = video_sweep_of(collected)
    after = video_fix_journal.summary(sweep)
    if entry is None:
        return None, "", after
    network = video_fix_journal.network_key(sweep)
    if entry.get("network") and network and entry["network"] != network:
        return entry, "", after
    return entry, video_fix_journal.judge(entry.get("before") or {}, after), after


def _verdict_problem(entry: dict, verdict: str, after: dict) -> dict | None:
    name = _FIX_ACTION_NAMES.get(str(entry.get("action")), "исправления")
    before = entry.get("before") or {}
    counts = f"отвечает {after['answering']} из {after['total']} адресов видеосерверов (было {before.get('answering', 0)} из {before.get('total', 0)})"
    if verdict == video_fix_journal.VERDICT_HELPED:
        return problem(Level.OK, f"После «{name}» {counts}: помогло", title="Исправление видео помогло", site=YOUTUBE_SITE)
    if verdict == video_fix_journal.VERDICT_WORSE:
        undo = entry.get("action") == "youtube_block_caches"
        return problem(
            Level.WARN,
            f"После «{name}» {counts}: стало хуже",
            ("Верните кэши кнопкой ниже." if undo else "Верните прежний пресет на странице пресетов.",),
            action="youtube_unblock_caches" if undo else "",
            title="Исправление видео не помогло",
            site=YOUTUBE_SITE,
        )
    if verdict == video_fix_journal.VERDICT_SAME:
        return problem(
            Level.UNKNOWN,
            f"После «{name}» {counts}: без перемен. В этой сети программа больше его не предложит",
            title="Исправление видео ничего не изменило",
            site=YOUTUBE_SITE,
        )
    return None


def video_fix_problems(
    collected: dict[str, list[Probe]], *, cache_block: tuple[str, ...] = (), journal: list[dict] | None = None
) -> list[dict]:
    """Строки итога с кнопками по исправлениям видео (``video_fixes``), итог прошлого исправления и «вернуть кэши».

    Что предложить, решает та же чистая функция, что и строки «Можно сделать» в карточке
    (``video_fixes.recommend``): кнопки итога и подробности не расходятся. Журнал (``video_fix_journal``)
    добавляет урок: что в этой сети уже не помогло, второй раз не предлагается.
    """
    journal = journal or []
    rows: list[dict] = []
    entry, verdict, after = journal_verdict(collected, journal)
    if entry is not None and verdict:
        row = _verdict_problem(entry, verdict, after)
        if row is not None:
            rows.append(row)
        # Итог этой проверки уже известен: урок действует сразу, не дожидаясь записи.
        journal = [*journal[:-1], {**journal[-1], "verdict": verdict}] if journal and journal[-1] is entry else journal
    offered: set[str] = set()
    network = video_fix_journal.network_key(video_sweep_of(collected))
    for probe in _video_probes(collected):
        sweep = [node.as_dict() for node in video_sweep.judge(probe.video_sweep)]
        for fix in video_fixes.recommend(sweep, quic=probe.quic.code if probe.quic else ""):
            key = str(fix["key"])
            if video_fix_journal.tried_without_luck(journal, _FIX_ACTION.get(key, ""), network):
                continue
            if key == video_fixes.FIX_BLOCK_CACHES and key not in offered:
                groups = ", ".join(fix["groups"])
                rows.append(
                    problem(
                        Level.WARN,
                        f"Кэши YouTube у провайдера ({groups}) не отвечают, а серверы Google отвечают: "
                        "их можно закрыть в hosts, и плеер перейдёт на серверы Google",
                        (
                            "Помогает не всем: если видео станет хуже, верните кэши кнопкой «Вернуть кэши YouTube».",
                            "После изменения перезапустите браузер.",
                        ),
                        action="youtube_block_caches",
                        target=",".join(fix["groups"]),
                        title="Молчат кэши YouTube у провайдера",
                        site=YOUTUBE_SITE,
                    )
                )
                offered.add(key)
            elif key in (video_fixes.FIX_QUIC_OFF, video_fixes.FIX_WSSIZE_PRESET) and "preset" not in offered:
                window = any(item["key"] == video_fixes.FIX_WSSIZE_PRESET for item in video_fixes.recommend(sweep))
                rows.append(
                    problem(
                        Level.WARN,
                        "Ответ видеосервера режут, но с малым окном приёма он приходит"
                        if window
                        else "QUIC до видеосерверов YouTube режут",
                        (
                            "Пресет «Default + YouTube no QUIC + wssize» включает малое окно приёма для видео YouTube "
                            "и выключает для него QUIC: видео пойдёт по TCP.",
                        ),
                        action="youtube_preset",
                        target=YOUTUBE_FIX_PRESET,
                        title="Видео YouTube: нужен пресет без QUIC",
                        site=YOUTUBE_SITE,
                    )
                )
                offered.add("preset")
    if cache_block:
        rows.append(
            problem(
                Level.OK,
                f"Кэши YouTube закрыты в hosts ({', '.join(cache_block)}). Если видео стало хуже, их можно вернуть",
                ("После изменения перезапустите браузер.",),
                action="youtube_unblock_caches",
                title="Кэши YouTube закрыты",
                site=YOUTUBE_SITE,
            )
        )
    return rows


def blocked_references(reference: list[dict]) -> list[dict]:
    """Эталонные серверы, которые не ответили ни разу, хотя другие отвечали.

    Если молчат все, дело не в отдельном сервере: это уже «нет интернета» или
    «эталон недоступен», и об этом сказано в другом месте отчёта.
    """
    down = [item for item in reference if not item["ok"]]
    return down if len(down) < len(reference) else []


def reference_text(item: dict) -> str:
    reason = f": {item['reason']}" if item["reason"] else ""
    return f"Шифрованный DNS {item['label']} ({item['address']}) недоступен{reason}"
