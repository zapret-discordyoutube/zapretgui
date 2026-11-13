"""Слова отчёта BlockCheck: как результат проверки сайта превращается в текст.

Проверка собирает факты в ``run_context.Probe``. Здесь из них получаются:

- строки журнала (``probe_lines``) — то, что человек читает в отчёте;
- запись о цели для экрана и файла (``target_report``);
- строки и записи разделов: звонки, обрыв, Telegram (``section_lines``, ``section_report``).

В сеть модуль не ходит и ничего не решает: выводы уже сделаны, здесь им
подбираются слова. Поэтому всё проверяется тестом без сети.
"""

from __future__ import annotations

from diagnostics import block_kind, path_report, protocol_probe, service_view, telegram_check, video_fixes, video_sweep
from diagnostics.run_context import Probe
from diagnostics.services import (
    STEP_DNS_SERVERS,
    STEP_FILTER,
    STEP_HOSTINGS,
    STEP_IPV6,
    STEP_SITES,
    STEP_SYSTEM,
    STEP_VOICE,
)
from diagnostics.verdict import DnsState, ReachState

STEP_TITLES = {
    STEP_SITES: "сайты",
    STEP_HOSTINGS: "хостинги",
    STEP_VOICE: "звонки",
    STEP_IPV6: "IPv6",
    STEP_SYSTEM: "компьютер и сеть",
    STEP_DNS_SERVERS: "DNS-серверы",
    STEP_FILTER: "место фильтра",
}

def timed_out_line(deadline: float) -> str:
    return (
        f"⚠️ Часть проверок не уложилась в {deadline:.0f} с и была прервана — "
        "их результат неизвестен."
    )


def probe_lines(probe: Probe, *, full: bool) -> list[str]:
    """Адрес сайта строками журнала. Строки идут из общего модуля слов, как на карточке и в «Проверке домена».

    Короткая проверка только сверяет DNS, поэтому показывает из строк адреса лишь то, что к нему относится.
    """
    sections = service_view.target_sections(target_report(probe))
    if full:
        return service_view.render_text(sections)
    first = sections[0]
    rows = tuple(row for row in first.rows if row.title in service_view.DNS_ROWS)
    return service_view.render_text([service_view.ViewSection(first.title, rows)])


def target_report(probe: Probe) -> dict:
    sweep = [node.as_dict() for node in video_sweep.judge(probe.video_sweep)]
    return {
        "host": probe.host,
        "purpose": probe.target.purpose,
        "main": probe.target.main,
        "state": probe.reach_state.value,
        "ok": probe.reach_state == ReachState.OK,
        "text": service_view.reach_text(probe),
        "short": service_view.short_text(probe),
        "dns_state": probe.judgement.state.value if probe.judgement and not service_view.not_probed(probe) else "",
        "dns_reason": probe.judgement.reason if probe.judgement and not service_view.not_probed(probe) else "",
        # Вид блокировки одним словом (ip / sni / cut16 / …) и его название.
        "kind": probe.kind,
        "kind_title": block_kind.kind_info(probe.kind).title if probe.kind else "",
        "volume": probe.volume.code if probe.volume else "",
        "volume_text": probe.volume.text if probe.volume else "",
        # Скорость ответов по замерам пробы объёма: пауза, медленный ответ, число замеров и уверенность.
        "transfer": probe.transfer.as_dict() if probe.transfer else None,
        # Путь запроса по шагам (DNS → TCP → TLS → QUIC → передача): уровень, слова и уверенность каждого.
        "path": [step.as_dict() for step in path_report.build(probe)] if probe.reach is not None else None,
        "cause": probe.cause.code if probe.cause else "",
        "cause_text": service_view.sentence(probe.cause.text) if probe.cause else "",
        # Видеосервер не открылся: ступени до него, чья сеть, запрос через другие адреса Google и вывод.
        "video_node": probe.video_node.as_dict() if probe.video_node is not None else None,
        # Обход всех найденных видеосерверов по IPv4 и IPv6: код каждого узла (``video_sweep.SWEEP_*``).
        "video_sweep": sweep,
        # Что можно сделать с видео по итогам обхода: ключ исправления и его данные (``video_fixes.FIX_*``).
        "video_fixes": video_fixes.recommend(sweep, quic=probe.quic.code if probe.quic else ""),
        "quic": probe.quic.code if probe.quic else "",
        "quic_text": probe.quic.text if probe.quic else "",
        # Тот же адрес по TLS 1.2, TLS 1.3, «как Chrome» и HTTP отдельно.
        "protocols": [
            {"key": line.key, "title": line.title, "state": line.state, "word": line.word, "text": line.text,
             "ms": None if line.ms is None else round(line.ms, 1), "code": line.code,
             # Что это за дорога — пояснение для подсказки на экране.
             "hint": protocol_probe.HINTS.get(line.key, "")}
            for line in probe.protocols
        ],
        "address": probe.reach.ip if probe.reach is not None else "",
        # Какие адреса сайта пробовали и чем кончилось: видно, на чём держится вывод.
        "tried": [{"address": ip, "result": kind} for ip, kind in probe.tried],
        "address_confirmed": probe.address_confirmed,
        "hosts_stale": probe.hosts_stale,
        # Повторная проверка поодиночке: "opened" — открылся со второго раза, "same" — сбой повторился.
        "rechecked": probe.rechecked,
        # Какой профиль запущенного пресета ловит имя: profile пусто — ни один. None — не проверяли.
        "coverage": None
        if probe.coverage is None
        else {
            "profile": probe.coverage.profile,
            "via": probe.coverage.via,
            "strategy": probe.coverage.strategy,
            "strategy_name": probe.coverage.strategy_name,
            "custom_strategy": probe.coverage.custom_strategy,
            "preset": probe.coverage.preset,
        },
        # Что ответил сервер за рубежом: "up" — сайт жив, "down" — лежит и там. Пусто — не спрашивали.
        "outside": probe.outside,
        # Сайт открылся, но через раз или не по каждому адресу: что именно шатается. Пусто — стабилен.
        "unstable": probe.unstable,
        # Чужой сертификат: кто ответил вместо сайта и что с этим делать. None — сертификат в порядке.
        "cert": None
        if probe.cert is None
        else {
            "code": probe.cert.code,
            "text": probe.cert.text,
            "advice": probe.cert.advice,
            "subject": probe.cert.subject,
            "issuer": probe.cert.issuer,
            "names": list(probe.cert.names),
        },
        "seconds": round(probe.seconds, 1),
        # На что ушло время: части проверки по порядку (названия — в ``service_view.STAGE_TITLES``).
        "stages": dict(probe.stages),
        "note": probe.discovery_note,
        # Какие адреса вернули hosts, DNS системы и эталон: на чём держится вывод про DNS.
        "dns_detail": "" if service_view.not_probed(probe) else service_view.dns_detail(probe),
    }


def telegram_text(item: telegram_check.DcResult) -> str:
    if item.connected is None:
        return "проверку прервали"
    if item.connected:
        took = "меньше чем за 1 мс" if (item.ms or 0) < 1 else f"за {round(item.ms or 0)} мс"
        return f"соединение {took}" + (" (со второй попытки)" if item.attempts > 1 else "")
    return f"не соединился, попыток: {item.attempts}"


def telegram_state(item: telegram_check.DcResult) -> str:
    return "unknown" if item.connected is None else ("ok" if item.connected else "fail")


def section_report(report, rows) -> dict:
    return {
        "level": report.level.value,
        "headline": report.headline,
        "advice": list(report.advice),
        # state: ok / fail / freeze / unknown — «не удалось проверить» не должно
        # выглядеть как «не работает».
        "items": [{"name": name, "ok": state == "ok", "state": state, "text": text} for name, state, text in rows],
    }


# ---------------------------------------------------------------------------
# Отчёт целиком: части словаря, который получают экран и файл
# ---------------------------------------------------------------------------

_SUMMARY_ICON = {"ok": "✅", "warn": "⚠️", "fail": "❌", "unknown": "❔"}


def summary_lines(problems: list[dict], working: list[str], *, timed_out: bool, deadline: float, elapsed: float) -> list[str]:
    """Раздел «Итог» журнала: проблемы с советами, что открывается и сколько шла проверка."""
    lines = ["", "━━━━━━━━ 📊 Итог ━━━━━━━━"]
    if timed_out:
        lines.append(timed_out_line(deadline))
    for item in problems:
        lines.append(f"{_SUMMARY_ICON[item['level']]} {item['text']}")
        lines.extend(f"   👉 {advice}" for advice in item["advice"])
    if working:
        lines.append(f"✅ Открываются: {', '.join(working)}")
    lines.append(f"Проверка заняла {elapsed:.1f} с.")
    return lines


def services_report(services: dict, verdicts: dict, collected: dict) -> list[dict]:
    """Сервисы с итогом и всеми их адресами — в том порядке, в каком проверялись.

    Всё, что экран показывает о сайте словами (итог, дороги, метки), лежит
    здесь готовым: отчёт — единственный источник истины, экран его не дополняет.
    """
    return [
        {
            "key": key,
            "label": service.label,
            "control": service.control,
            "domestic": service.domestic,
            "level": verdicts[key].level.value,
            "kind": verdicts[key].kind,
            "status": service_view.service_status(verdicts[key], collected[key]),
            "opens": verdicts[key].opens,
            "broken_parts": list(verdicts[key].broken_parts),
            "headline": verdicts[key].headline,
            "advice": list(verdicts[key].advice),
            "dns_note": verdicts[key].dns_note,
            "roads": service_view.service_roads(collected[key], verdicts[key].dns_note),
            "tags": service_view.service_tags(service, collected[key]),
            "targets": [target_report(probe) for probe in collected[key]],
        }
        for key, service in services.items()
    ]


def voice_report(voice, burst=None) -> dict | None:
    if not voice:
        return None
    report = _voice_section(voice)
    if burst is not None:
        # Серия из тридцати пакетов: не замирает ли UDP после первых двух десятков.
        report["burst"] = {
            "state": burst.code,
            "text": burst.text,
            "servers": [{"name": name, "series": list(series)} for name, series in burst.servers],
        }
    return report


def _voice_section(voice) -> dict:
    return section_report(voice, [
            (item.name, "ok" if item.answered else ("fail" if item.decided else "unknown"), item.text)
            for item in voice.servers
        ],
    )


def freeze_report(freeze) -> dict | None:
    """Обрыв по хостингам: общий итог и запись на каждый сервер."""
    if not freeze:
        return None
    report = section_report(freeze, [(item.name, item.state.value, item.text) for item in freeze.servers])
    # По серверу: провайдер, метка, в какую сторону оборвалось и за сколько проверили.
    report["servers"] = [
        {
            "provider": item.provider,
            "host": item.host,
            "id": item.ident,
            "state": item.state.value,
            "text": item.text,
            "direction": item.direction,
            "seconds": round(item.seconds, 1),
        }
        for item in freeze.servers
    ]
    return report


def telegram_report(telegram) -> dict | None:
    if not telegram:
        return None
    return {
        "level": telegram.level.value,
        "headline": telegram.headline,
        "advice": list(telegram.advice),
        "items": [
            {
                "name": item.center.name,
                "address": item.center.address,
                "state": telegram_state(item),
                "text": telegram_text(item),
            }
            for item in telegram.servers
        ],
    }


def system_report(system) -> list[dict]:
    return [
        {"key": item.key, "title": item.title, "level": item.level, "text": item.text, "advice": item.advice}
        for item in system
    ]


def dns_check_verdict(collected: dict[str, list[Probe]]) -> tuple[bool, list[str]]:
    """Итог вкладки «Проверка DNS подмены»: есть ли подмена и строки вывода."""
    probes = [probe for items in collected.values() for probe in items if probe.judgement is not None]
    spoofed = [probe for probe in probes if probe.judgement.state == DnsState.SPOOFED]
    if spoofed:
        return True, [
            "❌ Обнаружена DNS подмена:",
            *(f"   • {probe.host} — {probe.judgement.reason}" for probe in spoofed),
            "Браузер с защищённым DNS этого не замечает, а программы, которые спрашивают адрес "
            "у Windows, получат неверный ответ.",
            "👉 Откройте «Настройка DNS» и включите DNS с шифрованием (DoH) — его провайдер перехватить не сможет.",
        ]
    if any(probe.judgement.state == DnsState.UNKNOWN for probe in probes):
        return False, ["✅ Явной подмены не найдено. Часть адресов (❔) отличается от эталона, а проверить их не удалось — для CDN это обычно нормально."]
    return False, ["✅ DNS работает честно. Если сайты не открываются — дело не в DNS, а в блокировке соединения."]
