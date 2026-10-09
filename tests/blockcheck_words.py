"""Слова сайта для образцов отчёта в тестах: итог, дороги и метки — как их кладёт в отчёт проверка.

Экран этих слов сам не выводит: он показывает ``status``, ``roads`` и ``tags``
из отчёта. Образцы в тестах собираются из сырых данных адреса, поэтому здесь
они дополняются теми же словами, что дал бы настоящий отчёт
(``diagnostics.report_text.services_report``). Это помощник тестов, не программы.
"""

from __future__ import annotations

from diagnostics.block_kind import KIND_OTHER, KIND_UNSTABLE, KINDS, kind_info

OK, WARN, FAIL, UNKNOWN = "ok", "warn", "fail", "unknown"
_SITE_STATUS = {OK: "Открывается", WARN: "Есть проблемы", FAIL: "Не открывается", UNKNOWN: "Не удалось проверить"}


def _state(value) -> str:
    text = str(value or "")
    return text if text in (OK, WARN, FAIL, UNKNOWN) else UNKNOWN


def _capital(text: str) -> str:
    return f"{text[:1].upper()}{text[1:]}"


def with_words(service: dict) -> dict:
    """Сервис образца с готовыми словами отчёта: уровень, вид, итог, дороги и метки."""
    if "status" in service and "roads" in service:
        return service
    level = _legacy_site_level(service)
    kind = _legacy_site_kind(service, level, str(service.get("kind") or ""))
    kind = kind if level in (FAIL, WARN) and kind in KINDS and kind != KIND_OTHER else ""
    status, roads, tags = _legacy_site_words(service, level, kind)
    return {**service, "level": level, "kind": kind or str(service.get("kind") or ""), "status": status, "roads": roads, "tags": tags}


def report_with_words(report: dict) -> dict:
    """Отчёт образца, у каждого сайта которого есть готовые слова."""
    return {**report, "services": [with_words(dict(item)) for item in report.get("services") or ()]}


def _legacy_site_level(service: dict) -> str:
    level = _state(service.get("level"))
    targets = list(service.get("targets") or ())
    if level in (OK, WARN) and targets and all(item.get("ok") for item in targets):
        fingerprint = any(proto.get("code") == "fingerprint" for item in targets for proto in item.get("protocols") or ())
        return WARN if fingerprint or any(item.get("hosts_stale") or item.get("unstable") for item in targets) else OK
    return level


def _legacy_site_kind(service: dict, level: str, kind: str) -> str:
    if not kind and level == WARN and any(item.get("unstable") for item in service.get("targets") or ()):
        return KIND_UNSTABLE
    return kind


_LEGACY_CERT_STATUS = {
    "antivirus": "Сертификат подменяет антивирус",
    "debug_proxy": "Сертификат подменяет прокси-отладчик",
    "state_ca": "Сертификат государственного центра",
    "hosts": "Адрес из hosts ведёт не туда",
    "other_site": "Отвечает другой сайт",
    "self_signed": "Самоподписанный сертификат",
    "unknown_issuer": "Сертификат от неизвестного центра",
}
_LEGACY_QUIC_HINT = (
    "QUIC — быстрый способ соединения поверх UDP: им браузер открывает YouTube и многие крупные сайты. "
    "Если он закрыт, браузер сам переходит на обычное соединение."
)
_LEGACY_DNS_HINT = "DNS — справочная, которая по имени сайта выдаёт его адрес. Провайдер может подменять её ответы."
# Слово причины по её коду у адреса: свежий отчёт отдаёт его готовой меткой (tags, key=cause).
_LEGACY_CAUSE_WORDS = {
    "by_name": "блокировка по имени",
    "name_whitelist": "проходят только разрешённые имена",
    "by_address": "закрыт адрес",
    "stub_page": "страница провайдера",
    "address_closed": "адрес закрыт",
    "address_silent": "адрес молчит",
}
_LEGACY_ROAD_KEYS = {"TLS 1.2": "tls12", "TLS 1.3": "tls13", "Как Chrome": "browser", "HTTP": "http"}


def _legacy_site_words(service: dict, level: str, kind: str) -> tuple[str, list[dict], list[dict]]:
    """Слово итога, дороги и метки старого отчёта — в том же виде, в каком их отдаёт свежий."""
    targets = list(service.get("targets") or ())
    status = _capital(kind_info(kind).short) if kind else _SITE_STATUS[level]
    cert = next((str((item.get("cert") or {}).get("code") or "") for item in targets if (item.get("cert") or {}).get("code")), "")
    if cert:
        status = _LEGACY_CERT_STATUS.get(cert, "Чужой сертификат")
    elif any(item.get("hosts_stale") for item in targets):
        status = "Мешает запись в hosts"

    main = next((item for item in targets if item.get("main")), targets[0] if targets else {})
    roads = [
        {
            "key": _LEGACY_ROAD_KEYS.get(str(proto.get("title") or ""), str(proto.get("key") or "")),
            "label": str(proto.get("title") or "").removeprefix("Как "),
            "state": str(proto.get("state") or "unknown"),
            "word": str(proto.get("word") or ""),
            "text": str(proto.get("text") or ""),
            "hint": str(proto.get("hint") or ""),
        }
        for proto in main.get("protocols") or ()
    ]
    # Совсем старый отчёт дорог по протоколам не содержит: тогда QUIC и DNS показываются,
    # только если про них что-то известно, — прочерки без остальных дорог ничего не говорят.
    probed = bool(roads)
    quic = {str(item.get("quic") or "") for item in targets}
    if "blocked_by_name" in quic:
        roads.append({"key": "quic", "label": "QUIC", "state": "warn", "word": "закрыт", "text": "", "hint": _LEGACY_QUIC_HINT})
    elif "ok" in quic:
        roads.append({"key": "quic", "label": "QUIC", "state": "ok", "word": "работает", "text": "", "hint": _LEGACY_QUIC_HINT})
    elif probed:
        roads.append({"key": "quic", "label": "QUIC", "state": "unknown", "word": "—", "text": "", "hint": _LEGACY_QUIC_HINT})
    dns_states = {str(item.get("dns_state") or "") for item in targets}
    if service.get("dns_note") or "spoofed" in dns_states:
        roads.append({"key": "dns", "label": "DNS", "state": "warn", "word": "подменён", "text": "", "hint": _LEGACY_DNS_HINT})
    elif probed and dns_states and not dns_states & {"", "unknown"}:
        roads.append({"key": "dns", "label": "DNS", "state": "ok", "word": "честный", "text": "", "hint": _LEGACY_DNS_HINT})
    elif probed:
        roads.append({"key": "dns", "label": "DNS", "state": "unknown", "word": "—", "text": "", "hint": _LEGACY_DNS_HINT})

    causes = dict.fromkeys(_LEGACY_CAUSE_WORDS[item["cause"]] for item in targets if item.get("cause") in _LEGACY_CAUSE_WORDS)
    tags = [{"key": "cause", "text": word, "state": "fail"} for word in causes]
    if any(item.get("volume") == "cut" for item in targets):
        tags.append({"key": "cut16", "text": "обрыв на 16 КБ", "state": "warn"})
    if any(item.get("hosts_stale") for item in targets):
        tags.append({"key": "hosts", "text": "запись в hosts устарела", "state": "warn"})
    if any((item.get("registry") or {}).get("listed") for item in targets):
        tags.append({"key": "registry", "text": "в реестре РКН", "state": "info"})
    if service.get("control"):
        tags.append({"key": "control", "text": "контрольный", "state": "info"})
    return status, roads, tags
