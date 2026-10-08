"""Слова отчёта BlockCheck: как результат проверки сайта превращается в текст.

Проверка собирает факты в ``run_context.Probe``. Здесь из них получаются:

- строки журнала (``probe_lines``) — то, что человек читает в отчёте;
- запись о цели для экрана и файла (``target_report``);
- строки и записи разделов: звонки, обрыв, Telegram (``section_lines``, ``section_report``).

В сеть модуль не ходит и ничего не решает: выводы уже сделаны, здесь им
подбираются слова. Поэтому всё проверяется тестом без сети.
"""

from __future__ import annotations

from diagnostics import block_kind, quic_probe, telegram_check, volume_probe
from diagnostics.limits import DNS_ATTEMPTS, HTTPS_TIMEOUT, SOURCE_HOSTS, SOURCE_REFERENCE
from diagnostics.run_context import RECHECK_OPENED, RECHECK_SAME, Probe
from diagnostics.verdict import DnsState, Level, ReachState, describe_reach

# Молчание по QUIC — не поломка: сервер может его не поддерживать.
QUIC_ICON = {
    quic_probe.QUIC_OK: "✅",
    quic_probe.QUIC_BLOCKED_BY_NAME: "❌",
    quic_probe.QUIC_SILENT: "ℹ️",
}


# Короткие названия шагов для строки «сколько шёл каждый шаг».
# Части проверки одного сайта — на что ушло его время.
STAGE_TITLES = {"dns": "адрес", "reach": "соединение", "cause": "причина", "volume": "объём", "roads": "дороги"}


def stages_text(stages: dict | None) -> str:
    """«соединение 6 с, причина 3 с» — только части длиннее секунды, от долгих к коротким."""
    parts = sorted(((float(value), name) for name, value in (stages or {}).items() if float(value) >= 1.0), reverse=True)
    return ", ".join(f"{STAGE_TITLES.get(name, name)} {value:.0f} с" for value, name in parts)


STEP_TITLES = {
    "sites": "сайты",
    "hostings": "хостинги",
    "voice": "звонки",
    "ipv6": "IPv6",
    "system": "компьютер и сеть",
    "dns_servers": "DNS-серверы",
    "filter": "место фильтра",
}

def timed_out_line(deadline: float) -> str:
    return (
        f"⚠️ Часть проверок не уложилась в {deadline:.0f} с и была прервана — "
        "их результат неизвестен."
    )


def fail_text(probe: Probe) -> str:
    """Почему адрес не открылся — одной фразой."""
    if probe.volume is not None and probe.volume.code == volume_probe.VOLUME_CUT:
        return f"{probe.volume.text} — так провайдер обрывает загрузку"
    if probe.reach_state == ReachState.CERT and probe.cert is not None:
        return probe.cert.text
    return describe_reach(probe.reach, timeout=HTTPS_TIMEOUT)


LEVEL_ICON = {Level.OK: "✅", Level.WARN: "⚠️", Level.FAIL: "❌", Level.UNKNOWN: "❔"}


DNS_ICON = {DnsState.OK: "✅", DnsState.SPOOFED: "❌", DnsState.LOCAL: "ℹ️", DnsState.UNKNOWN: "❔"}


SOURCE_NOTE = {
    SOURCE_HOSTS: ", адрес из файла hosts",
    SOURCE_REFERENCE: ", адрес по DNS-over-HTTPS",
}


def ips_text(ips: tuple[str, ...], limit: int = 3) -> str:
    if not ips:
        return "—"
    shown = ", ".join(ips[:limit])
    return f"{shown} и ещё {len(ips) - limit}" if len(ips) > limit else shown


def reach_text(probe: Probe) -> str:
    """Одна строка: открывается ли адрес и почему нет."""
    result = probe.reach
    source = SOURCE_NOTE.get(probe.reach_source, "")
    if probe.reach_state == ReachState.OK and probe.browser_only and result is not None:
        return (
            f"открывается в браузере: приветствие, какое шлёт Chrome, до сервера доходит, "
            f"а простое соединение других программ рвут ({result.ip}{source})"
        )
    if probe.reach_state == ReachState.OK and result is not None:
        tls = f", {result.tls_version.replace('TLSv', 'TLS ')}" if result.tls_version else ""
        if ":" in result.ip:
            return f"открывается по IPv6, по IPv4 — нет ({result.elapsed_ms:.0f} мс{tls}, {result.ip})"
        stale = ""
        if probe.hosts_stale:
            what = probe.cert.text if probe.cert is not None else "адрес из файла hosts не работает, запись в нём устарела"
            stale = f" — {what}; браузер пойдёт по этой записи и сайт не откроет"
        again = " — со второй проверки, поодиночке: первый сбой дала нагрузка самой проверки" if probe.rechecked == RECHECK_OPENED else ""
        return f"открывается ({result.elapsed_ms:.0f} мс{tls}, {result.ip}{source}){stale}{again}"
    text = fail_text(probe)
    if result is None or not result.ip:
        return text
    addresses = len({ip for ip, _kind in probe.tried})
    if addresses > 1:
        tries = f", не ответил ни один из {addresses} адресов"
    else:
        tries = f", попыток: {probe.attempts}" if probe.attempts > 1 else ""
    ipv6 = ", по IPv6 тоже не открылся" if probe.ipv6_result is not None else ""
    again = ", повторная проверка поодиночке дала то же" if probe.rechecked == RECHECK_SAME else ""
    return f"{text} ({result.ip}{source}{tries}{ipv6}{again})"


def dns_detail(probe: Probe) -> str:
    parts: list[str] = []
    if probe.hosts_ips:
        parts.append(f"hosts: {ips_text(probe.hosts_ips)}")
    if probe.dns.ips:
        flaky = f" (а {probe.dns_nxdomain} из {DNS_ATTEMPTS} раз — «сайта нет»)" if probe.dns_nxdomain else ""
        parts.append(f"DNS системы: {ips_text(probe.dns.ips)}{flaky}")
    else:
        parts.append(f"DNS системы: нет адреса ({probe.dns.detail})")
    if probe.reference_ips:
        parts.append(f"эталон: {ips_text(probe.reference_ips)}")
    elif not probe.reference_ok:
        parts.append("эталон: недоступен")
    return " · ".join(parts)


def dns_lines(probe: Probe, indent: str) -> list[str]:
    judgement = probe.judgement
    lines: list[str] = []
    if judgement is not None:
        lines.append(f"{indent}{DNS_ICON[judgement.state]} DNS: {judgement.reason}")
    lines.append(f"{indent}   {dns_detail(probe)}")
    return lines


def sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def probe_lines(probe: Probe, *, full: bool) -> list[str]:
    title = f"{probe.host} — {probe.target.purpose}"
    if not full:
        lines = [title]
        if probe.discovery_note:
            lines.append(f"  ℹ️ {probe.discovery_note}")
        lines.extend(dns_lines(probe, "  "))
        return lines

    icon = "✅" if probe.reach_state == ReachState.OK else "❌"
    if probe.reach_state == ReachState.UNKNOWN:
        icon = "❔"
    if probe.hosts_stale and probe.reach_state == ReachState.OK:
        # Проверка сайт открыла, а браузер по записи в hosts — не откроет.
        icon = "⚠️"
    lines = [f"{icon} {title}: {reach_text(probe)}"]
    if probe.kind:
        lines.append(f"   🏷 Вид блокировки: {block_kind.kind_info(probe.kind).title}")
    if probe.cause is not None:
        lines.append(f"   🔎 {sentence(probe.cause.text)}")
    if probe.volume is not None and probe.volume.code != volume_probe.VOLUME_CUT:
        icon = "✅" if probe.volume.code == volume_probe.VOLUME_OK else "ℹ️"
        lines.append(f"   {icon} Обрыв после 16 КБ: {probe.volume.text}")
    if probe.quic is not None:
        lines.append(f"   {QUIC_ICON[probe.quic.code]} QUIC (UDP 443): {probe.quic.text}")
    if probe.discovery_note:
        lines.append(f"   ℹ️ {probe.discovery_note}")
    lines.extend(dns_lines(probe, "   "))
    return lines


def short_text(probe: Probe) -> str:
    if probe.reach_state != ReachState.OK:
        return fail_text(probe)
    if probe.browser_only:
        return "открывается в браузере"
    if probe.reach is not None and ":" in probe.reach.ip:
        return "открывается только по IPv6"
    return "открывается"


def target_report(probe: Probe) -> dict:
    return {
        "host": probe.host,
        "purpose": probe.target.purpose,
        "main": probe.target.main,
        "state": probe.reach_state.value,
        "ok": probe.reach_state == ReachState.OK,
        "text": reach_text(probe),
        "short": short_text(probe),
        "dns_state": probe.judgement.state.value if probe.judgement else "",
        "dns_reason": probe.judgement.reason if probe.judgement else "",
        # Вид блокировки одним словом (ip / sni / cut16 / …) и его название.
        "kind": probe.kind,
        "kind_title": block_kind.kind_info(probe.kind).title if probe.kind else "",
        "volume": probe.volume.code if probe.volume else "",
        "volume_text": probe.volume.text if probe.volume else "",
        "cause": probe.cause.code if probe.cause else "",
        "cause_text": sentence(probe.cause.text) if probe.cause else "",
        "quic": probe.quic.code if probe.quic else "",
        "quic_text": probe.quic.text if probe.quic else "",
        # Тот же адрес по TLS 1.2, TLS 1.3, «как Chrome» и HTTP отдельно.
        "protocols": [
            {"key": line.key, "title": line.title, "state": line.state, "word": line.word, "text": line.text,
             "ms": None if line.ms is None else round(line.ms, 1), "code": line.code}
            for line in probe.protocols
        ],
        "address": probe.reach.ip if probe.reach is not None else "",
        # Какие адреса сайта пробовали и чем кончилось: видно, на чём держится вывод.
        "tried": [{"address": ip, "result": kind} for ip, kind in probe.tried],
        "address_confirmed": probe.address_confirmed,
        "hosts_stale": probe.hosts_stale,
        # Повторная проверка поодиночке: "opened" — открылся со второго раза, "same" — сбой повторился.
        "rechecked": probe.rechecked,
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
        # На что ушло время: части проверки по порядку (названия — в ``STAGE_TITLES``).
        "stages": dict(probe.stages),
        "note": probe.discovery_note,
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


def section_lines(title: str, report, rows) -> list[str]:
    icon = {Level.OK: "✅", Level.WARN: "⚠️", Level.FAIL: "❌", Level.UNKNOWN: "❔"}
    lines = ["", f"━━━━━━━━ {title} ━━━━━━━━"]
    for mark, name, text in rows:
        lines.append(f"{mark} {name}: {text}")
    lines.append(f"{icon[report.level]} {report.headline}")
    return lines


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
    """Сервисы с итогом и всеми их адресами — в том порядке, в каком проверялись."""
    return [
        {
            "key": key,
            "label": service.label,
            "control": service.control,
            "domestic": service.domestic,
            "level": verdicts[key].level.value,
            "kind": verdicts[key].kind,
            "headline": verdicts[key].headline,
            "advice": list(verdicts[key].advice),
            "dns_note": verdicts[key].dns_note,
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
