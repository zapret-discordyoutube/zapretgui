"""Слова отчёта BlockCheck: как результат проверки сайта превращается в текст.

Проверка собирает факты в ``run_context.Probe``. Здесь из них получаются:

- строки журнала (``probe_lines``) — то, что человек читает в отчёте;
- запись о цели для экрана и файла (``target_report``);
- строки и записи разделов: звонки, обрыв, Telegram (``section_lines``, ``section_report``).

В сеть модуль не ходит и ничего не решает: выводы уже сделаны, здесь им
подбираются слова. Поэтому всё проверяется тестом без сети.
"""

from __future__ import annotations

from diagnostics import block_kind, path_report, protocol_probe, quic_probe, service_view, telegram_check, transfer_probe, volume_probe
from diagnostics.limits import DNS_ATTEMPTS, HTTPS_TIMEOUT, SOURCE_HOSTS, SOURCE_REFERENCE
from diagnostics.run_context import RECHECK_OPENED, RECHECK_SAME, Probe
from diagnostics.service_view import STAGE_TITLES, stages_text  # noqa: F401  (слова отчёта живут в service_view)
from diagnostics.services import (
    STEP_DNS_SERVERS,
    STEP_FILTER,
    STEP_HOSTINGS,
    STEP_IPV6,
    STEP_SITES,
    STEP_SYSTEM,
    STEP_VOICE,
)
from diagnostics.verdict import DnsState, Level, ReachState, describe_reach

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


def fail_text(probe: Probe) -> str:
    """Почему адрес не открылся — одной фразой."""
    if probe.volume is not None and probe.volume.code == volume_probe.VOLUME_CUT:
        return f"{probe.volume.text} — так провайдер обрывает загрузку"
    if probe.reach_state == ReachState.CERT and probe.cert is not None:
        return probe.cert.text
    return describe_reach(probe.reach, timeout=HTTPS_TIMEOUT)


LEVEL_ICON = {Level.OK: "✅", Level.WARN: "⚠️", Level.FAIL: "❌", Level.UNKNOWN: "❔"}




SOURCE_NOTE = {
    SOURCE_HOSTS: ", адрес из файла hosts",
    SOURCE_REFERENCE: ", адрес по DNS-over-HTTPS",
}


def ips_text(ips: tuple[str, ...], limit: int = 3) -> str:
    if not ips:
        return "—"
    shown = ", ".join(ips[:limit])
    return f"{shown} и ещё {len(ips) - limit}" if len(ips) > limit else shown


def not_probed(probe: Probe) -> bool:
    """Адрес не проверялся вовсе (видеосервера YouTube найти не удалось): сказать нечего, кроме причины."""
    return probe.reach is None and probe.reach_state == ReachState.UNKNOWN and bool(probe.discovery_note)


def reach_text(probe: Probe) -> str:
    """Одна строка: открывается ли адрес и почему нет."""
    if not_probed(probe):
        return "не проверялся"
    result = probe.reach
    source = SOURCE_NOTE.get(probe.reach_source, "")
    if probe.reach_state == ReachState.OK and result is not None:
        tls = f", {result.tls_version.replace('TLSv', 'TLS ')}" if result.tls_version else ""
        if ":" in result.ip:
            return f"открывается по IPv6, по IPv4 — нет ({result.elapsed_ms:.0f} мс{tls}, {result.ip})"
        stale = ""
        if probe.hosts_stale:
            what = probe.cert.text if probe.cert is not None else "адрес из файла hosts не работает, запись в нём устарела"
            stale = f" — {what}; браузер пойдёт по этой записи и сайт не откроет"
        again = " — со второй проверки, поодиночке: первый сбой дала нагрузка самой проверки" if probe.rechecked == RECHECK_OPENED else ""
        if probe.unstable:
            again = f" — но через раз: {probe.unstable}"
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


def sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


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


def short_text(probe: Probe) -> str:
    if probe.reach_state != ReachState.OK:
        return fail_text(probe)
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
        "dns_state": probe.judgement.state.value if probe.judgement and not not_probed(probe) else "",
        "dns_reason": probe.judgement.reason if probe.judgement and not not_probed(probe) else "",
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
        "cause_text": sentence(probe.cause.text) if probe.cause else "",
        # Видеосервер не открылся: ступени до него, чья сеть, запрос через другие адреса Google и вывод.
        "video_node": probe.video_node.as_dict() if probe.video_node is not None else None,
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
        "dns_detail": "" if not_probed(probe) else dns_detail(probe),
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


# Слово итога сайта, когда вид блокировки не назван: по уровню.
_STATUS_BY_LEVEL = {
    Level.OK: "Открывается",
    Level.WARN: "Есть проблемы",
    Level.FAIL: "Не открывается",
    Level.UNKNOWN: "Не удалось проверить",
}
# Кто ответил вместо сайта — словом итога (код из ``cert_owner``).
_STATUS_BY_CERT = {
    "antivirus": "Сертификат подменяет антивирус",
    "debug_proxy": "Сертификат подменяет прокси-отладчик",
    "state_ca": "Сертификат государственного центра",
    "hosts": "Адрес из hosts ведёт не туда",
    "other_site": "Отвечает другой сайт",
    "self_signed": "Самоподписанный сертификат",
    "unknown_issuer": "Сертификат от неизвестного центра",
}
HINT_QUIC = (
    "QUIC — быстрый способ соединения поверх UDP: им браузер открывает YouTube и многие крупные сайты. "
    "Если он закрыт, браузер сам переходит на обычное соединение."
)
HINT_DNS = "DNS — справочная, которая по имени сайта выдаёт его адрес. Провайдер может подменять её ответы."
_ROAD_LABELS = {
    protocol_probe.PROTO_TLS12: "TLS 1.2",
    protocol_probe.PROTO_TLS13: "TLS 1.3",
    protocol_probe.PROTO_BROWSER: "Chrome",
    protocol_probe.PROTO_HTTP: "HTTP",
}


def service_status(verdict, probes) -> str:
    """Слово итога сайта — одно на всю программу: карточка, история и сравнение берут его отсюда.

    Причина не у провайдера (чужой сертификат, запись в hosts) называется прямо:
    общее «Есть проблемы» про неё ничего не говорит.
    """
    cert = next((probe.cert.code for probe in probes if probe.cert is not None), "")
    if cert:
        return _STATUS_BY_CERT.get(cert, "Чужой сертификат")
    if any(probe.hosts_stale for probe in probes):
        return "Мешает запись в hosts"
    if verdict.kind and verdict.level in (Level.WARN, Level.FAIL) and verdict.kind in block_kind.KINDS:
        if verdict.kind != block_kind.KIND_OTHER:
            return sentence(block_kind.kind_info(verdict.kind).short)
    return _STATUS_BY_LEVEL[verdict.level]


def _road(key: str, label: str, state: str, word: str, text: str, hint: str) -> dict:
    return {"key": key, "label": label, "state": state, "word": word, "text": text, "hint": hint}


HINT_TRANSFER = (
    "Передача — как быстро сайт начинает отвечать и не отвечает ли он паузами. Соединение может установиться "
    "и пройти все проверки, а видео и крупные файлы всё равно идти рывками."
)
_TRANSFER_WORDS = {
    transfer_probe.TRANSFER_OK: "быстро",
    transfer_probe.TRANSFER_PAUSE: "с паузами",
    transfer_probe.TRANSFER_SLOW_REPLY: "медленно",
    transfer_probe.TRANSFER_SLOW_BODY: "медленно",
}


def _transfer_road(verdict) -> dict:
    return _road(
        "transfer", "Передача", verdict.level, _TRANSFER_WORDS.get(verdict.code, "—"), verdict.text, HINT_TRANSFER
    )


def service_roads(probes, dns_note: str = "") -> list[dict]:
    """Дороги к сайту — всегда в одном порядке: TLS 1.2, TLS 1.3, Chrome, HTTP, QUIC, передача, DNS.

    Каждая дорога приходит готовой: состояние, слово для метки, полная фраза и
    пояснение, что это за дорога. Экран их только показывает. Пусто — дороги не
    проверялись.
    """
    main = next((probe for probe in probes if probe.target.main), probes[0] if probes else None)
    roads = [
        _road(line.key, _ROAD_LABELS.get(line.key, line.title), line.state, line.word, line.text, protocol_probe.HINTS.get(line.key, ""))
        for line in (main.protocols if main is not None else ())
    ]
    if not roads:
        return []
    quic = {probe.quic.code for probe in probes if probe.quic is not None}
    if quic_probe.QUIC_BLOCKED_BY_NAME in quic:
        roads.append(_road("quic", "QUIC", "warn", "закрыт", "QUIC к этому сайту режут: сайт откроется обычным способом, чуть медленнее", HINT_QUIC))
    elif quic_probe.QUIC_OK in quic:
        roads.append(_road("quic", "QUIC", "ok", "работает", "QUIC к этому сайту проходит", HINT_QUIC))
    else:
        roads.append(_road("quic", "QUIC", "unknown", "—", "у этого сайта QUIC не проверялся или сервер его не поддерживает", HINT_QUIC))
    transfer = main.transfer if main is not None else None
    if transfer is not None and transfer.code != transfer_probe.TRANSFER_UNKNOWN:
        roads.append(_transfer_road(transfer))
    dns = {probe.judgement.state for probe in probes if probe.judgement is not None}
    if dns_note or DnsState.SPOOFED in dns:
        roads.append(_road("dns", "DNS", "warn", "подменён", "ответ DNS подменён: лечится DNS с шифрованием в разделе «Настройка DNS»", HINT_DNS))
    elif dns and DnsState.UNKNOWN not in dns:
        roads.append(_road("dns", "DNS", "ok", "честный", "адрес сайта DNS выдаёт верно", HINT_DNS))
    else:
        roads.append(_road("dns", "DNS", "unknown", "—", "сверить ответ DNS не удалось", HINT_DNS))
    return roads


# Как именно блокируют — коротким словом для метки (код из ``block_cause``).
_CAUSE_WORDS = {
    "by_name": "блокировка по имени",
    "name_whitelist": "проходят только разрешённые имена",
    "by_address": "закрыт адрес",
    "stub_page": "страница провайдера",
    "address_closed": "адрес закрыт",
    "address_silent": "адрес молчит",
}


def service_tags(service, probes) -> list[dict]:
    """Метки сайта о том, чего в дорогах нет. Метку реестра РКН дописывает ``registry.annotate``."""
    causes = dict.fromkeys(_CAUSE_WORDS[probe.cause.code] for probe in probes if probe.cause is not None and probe.cause.code in _CAUSE_WORDS)
    tags = [{"key": "cause", "text": word, "state": "fail"} for word in causes]
    if any(probe.volume is not None and probe.volume.code == volume_probe.VOLUME_CUT for probe in probes):
        tags.append({"key": "cut16", "text": "обрыв на 16 КБ", "state": "warn"})
    if any(probe.transfer is not None and probe.transfer.code == transfer_probe.TRANSFER_PAUSE for probe in probes):
        tags.append({"key": "pause", "text": "паузы в ответах", "state": "warn"})
    if any(probe.hosts_stale for probe in probes):
        tags.append({"key": "hosts", "text": "запись в hosts устарела", "state": "warn"})
    if service.control:
        tags.append({"key": "control", "text": "контрольный", "state": "info"})
    return tags


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
            "status": service_status(verdicts[key], collected[key]),
            "headline": verdicts[key].headline,
            "advice": list(verdicts[key].advice),
            "dns_note": verdicts[key].dns_note,
            "roads": service_roads(collected[key], verdicts[key].dns_note),
            "tags": service_tags(service, collected[key]),
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
