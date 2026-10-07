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

from diagnostics import block_kind, ipv6_check, quic_probe, report_text, system_state, telegram_check
from diagnostics.run_context import Probe
from diagnostics.services import Service
from diagnostics.verdict import ADVICE_DNS, ADVICE_VIA_ZAPRET, DnsState, Level, ReachState, ServiceVerdict, advice_geo_site

ADVICE_IPV6 = (
    "Из-за этого сайты открываются с задержкой: браузер сначала ждёт IPv6. Перезагрузите роутер; "
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


LEVEL_ORDER = {Level.FAIL: 0, Level.WARN: 1, Level.UNKNOWN: 2, Level.OK: 3}


# Блокировки, которые обходит стратегия Zapret.
BYPASSABLE = (ReachState.DPI, ReachState.FREEZE)


def no_geo_service(_host: str) -> str:
    return ""


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
) -> dict:
    """Строка итога. ``kind`` — вид блокировки: по нему экран собирает строки в группы.

    ``title`` — короткое название для строки внутри группы (вид блокировки там
    уже назван в заголовке). ``evidence`` — на чём основан вывод; эти же фразы
    стоят первыми в ``advice``.
    """
    return {
        "level": level.value,
        "text": text,
        "advice": list(advice),
        "action": action,
        "target": target,
        "kind": kind,
        "title": title,
        "evidence": list(evidence),
    }


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
) -> tuple[list[dict], list[str], list[str]]:
    """Итог для экрана: проблемы по важности, открывающиеся сервисы, подменённые DNS."""
    problems: list[dict] = []

    controls = [key for key, service in services.items() if service.control]
    foreign = [key for key in controls if not services[key].domestic]
    domestic = [key for key in controls if services[key].domestic]
    # Только настоящий провал контрольных сайтов: «не успели проверить»
    # (лимит времени) — не «нет интернета», иначе такой прогон спрятал бы
    # найденные блокировки остальных сайтов.
    # «Не открывается» у контрольного сайта — только когда до него нет дороги.
    # Обрыв после 16 КБ или чужой сертификат — это блокировка, которую лечат
    # иначе, а не «закрыты все зарубежные адреса».
    no_road = (ReachState.IP_BLOCK, ReachState.DPI)

    def _down(key: str) -> bool:
        probes = collected.get(key) or ()
        return bool(probes) and all(probe.reach_state in no_road for probe in probes)

    # Эталонные DNS-серверы — зарубежные адреса, к которым программа ходит напрямую.
    # Если хоть один ответил, зарубежные адреса не закрыты и интернет есть.
    foreign_reachable = any(item.get("ok") for item in reference or ())
    foreign_down = bool(foreign) and all(_down(key) for key in foreign) and not foreign_reachable
    domestic_up = bool(domestic) and all(verdicts[key].level == Level.OK for key in domestic)
    whitelisted = foreign_down and domestic_up
    offline = bool(controls) and all(_down(key) for key in controls) and not foreign_reachable
    names = ", ".join(services[key].label for key in foreign)
    if whitelisted:
        problems.append(
            problem(
                Level.FAIL,
                f"Открываются только российские сайты ({', '.join(services[key].label for key in domestic)}), "
                f"а зарубежные контрольные ({names}) — нет. Похоже на режим «белых списков»: провайдер "
                "пропускает только разрешённые адреса",
                (
                    "В таком режиме Zapret не помогает: закрыты сами адреса, а не отдельные сайты. "
                    "Обычно это временное ограничение, чаще в мобильных сетях — проверьте другую сеть.",
                ),
                kind=block_kind.KIND_NETWORK,
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
        if verdict.level in (Level.FAIL, Level.WARN) and broken:
            if offline:
                # Без интернета «Zapret не обходит блокировку» у каждого сайта —
                # неправда и шум: причина одна, она уже написана первой строкой.
                continue
            advice = tuple(item for item in verdict.advice if item != ADVICE_DNS)
            # Стратегия помогает только от DPI и обрыва. При чужом сертификате,
            # недоступном адресе или без адреса кнопка подбора увела бы не туда.
            bypassable = next((probe for probe in broken if probe.reach_state in BYPASSABLE), None)
            action = ""
            if bypassable is not None:
                action = "strategy" if zapret_running else "start_zapret"
            target = (bypassable or broken[0]).host
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
                action = "hosts"
            causes = tuple(
                dict.fromkeys(report_text.sentence(probe.cause.text) + "." for probe in broken if probe.cause is not None)
            )
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
                )
            )
        elif verdict.level == Level.UNKNOWN:
            if not offline:
                problems.append(problem(Level.UNKNOWN, verdict.headline, verdict.advice))
        else:
            working.append(service.label)

    if freeze is not None and freeze.level in (Level.FAIL, Level.WARN):
        problems.append(
            problem(
                freeze.level,
                freeze.headline,
                freeze.advice,
                action="strategy" if zapret_running else "start_zapret",
                kind=block_kind.KIND_CUT,
            )
        )
    elif freeze is not None and freeze.level == Level.UNKNOWN and not offline:
        problems.append(problem(freeze.level, freeze.headline, freeze.advice))
    if voice is not None and voice.level != Level.OK and not offline:
        problems.append(
            problem(voice.level, voice.headline, voice.advice, action="strategy_voice", kind=block_kind.KIND_VOICE)
        )
    # Один молчащий дата-центр — не проблема для человека: приложение возьмёт другой.
    if telegram is not None and telegram.level == Level.FAIL and not offline:
        problems.append(problem(telegram.level, telegram.headline, telegram.advice, title="Telegram"))

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
            )
        )

    # QUIC заблокирован, а сам сайт открывается: это не «сайт не работает», но
    # браузер сначала пробует QUIC и переходит на обычное соединение с задержкой.
    quic_blocked = [
        services[key].label
        for key, probes in collected.items()
        if any(
            probe.quic is not None
            and probe.quic.code == quic_probe.QUIC_BLOCKED_BY_NAME
            and probe.reach_state == ReachState.OK
            for probe in probes
        )
    ]
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
                )
            )
    if ipv6 is not None and ipv6.code == ipv6_check.IPV6_BROKEN and not offline:
        problems.append(problem(Level.WARN, f"IPv6 {ipv6.text}", (ADVICE_IPV6,), kind=block_kind.KIND_NETWORK))
    for item in blocked_references(reference or []):
        problems.append(
            problem(
                Level.WARN,
                reference_text(item),
                (ADVICE_BLOCKED_REFERENCE,),
                action="dns",
                kind=block_kind.KIND_DNS,
            )
        )
    problems.sort(key=lambda item: LEVEL_ORDER.get(Level(item["level"]), 9))
    return problems, working, spoofed


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
