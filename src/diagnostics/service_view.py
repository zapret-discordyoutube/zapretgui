"""Что сказать про сайт: единственное место, где запись сервиса превращается в строки отчёта.

Проверка отдаёт запись сервиса словарём (``report_text.services_report``). Из неё
строятся и карточки BlockCheck, и текст отчёта (окно «Отчёт», копирование, обращение
в поддержку), и итог «Проверки домена». Раньше у каждого был свой набор строк со
своими названиями, и один и тот же факт читался по-разному. Теперь слова, порядок
и уровень каждой строки решаются здесь, а карточка и текст только рисуют готовое:

- ``service_sections(service)`` — разделы записи сервиса: по разделу на адрес, путь запроса
  главного адреса, что делать, DNS, замедление по имени, вид блокировки;
- ``render_text(sections)`` — те же разделы строками текста.

Новое измерение показывают одной правкой в ``target_rows``: оно появится и на карточке,
и в тексте. Модуль не знает ни про окна, ни про пробы — только про словарь отчёта.
"""

from __future__ import annotations

from config import zapret_dns

from collections.abc import Iterable
from dataclasses import dataclass

from diagnostics.block_kind import KIND_OTHER, KINDS, kind_info
from diagnostics import block_kind, protocol_probe, quic_probe, transfer_probe, volume_probe
from diagnostics.limits import DNS_ATTEMPTS, HTTPS_TIMEOUT, SOURCE_HOSTS, SOURCE_REFERENCE
from diagnostics.run_context import RECHECK_OPENED, RECHECK_SAME, Probe
from diagnostics.verdict import DnsState, Level, ReachState, describe_reach

__all__ = [
    "INFO",
    "DNS_ROWS",
    "STAGE_TITLES",
    "ViewRow",
    "ViewSection",
    "name_speed_sections",
    "render_text",
    "service_sections",
    "shown_level",
    "site_kind",
    "site_level",
    "stages_text",
    "target_sections",
]

# Уровень строки: от него зависят значок на экране и в тексте.
OK = "ok"
WARN = "warn"
FAIL = "fail"
UNKNOWN = "unknown"
INFO = "info"
_LEVELS = (OK, WARN, FAIL, UNKNOWN, INFO)

TEXT_ICONS = {OK: "✅", WARN: "⚠️", FAIL: "❌", UNKNOWN: "❔", INFO: "ℹ️"}

# Исход попытки основного запроса — словом для подробностей.
_TRIED_WORDS = {
    "ok": "открылся",
    "connect": "нет соединения",
    "timeout": "нет ответа",
    "reset": "сброс",
    "tls": "обрыв на шифровании",
    "cert": "чужой сертификат",
    "error": "ошибка",
}
_PROTOCOL_STATES = {"ok": OK, "fail": FAIL, "info": INFO, "unknown": UNKNOWN}
# Ответ DNS: подмена — предупреждение, совпадение с эталоном — норма, свой адрес — справка.
_DNS_LEVELS = {"ok": OK, "spoofed": WARN, "local": INFO, "unknown": UNKNOWN}
# QUIC: блокировка по имени — предупреждение, ответ — норма, молчание — справка (сервер мог не поддерживать QUIC).
_QUIC_LEVELS = {"ok": OK, "blocked_by_name": WARN, "silent": INFO}
_OUTSIDE_WORDS = {
    "up": (INFO, "открывается — значит, закрыт он именно на вашей линии"),
    "down": (INFO, "тоже не открывается — сайт не работает сам"),
}
# Кто и на каком уровне сети мешает: слои по виду блокировки. Слова подробные — это объяснение, а не метка.
_BLOCK_LAYERS = {
    "ip": (
        "IP-адрес (сетевой уровень)",
        "закрыт сам адрес сервера: до него не доходит ничего, или соединение с ним молчит либо обрывается, "
        "каким именем сайта ни называйся. "
        "Так бывает, когда оператор связи внёс адрес в чёрный список на своём оборудовании или на маршруте оборван путь. "
        "Стратегия Zapret адрес не открывает: нужен другой маршрут (VPN, прокси).",
    ),
    "tls_reply": (
        "TLS поверх TCP 443 (ответ сервера в пределах окна приёма)",
        "соединение с адресом устанавливается, но любое шифрованное соединение с ним обрывают, каким именем ни называйся: "
        "фильтр на пути узнаёт шифрование по первым трём байтам ответа сервера. Читает он ответ только в пределах окна "
        "приёма, которое объявил компьютер, поэтому при окне в 1–2 байта ответ проходит. Так стала работать часть ТСПУ "
        "с октября 2026 года на адресах Google. Стратегия Zapret обходит это малым окном (wssize) только в профиле по "
        "адресам; скорость через него ограничена: окно приёма там не больше 128 КБ — при пинге 20 мс это около 50 Мбит/с.",
    ),
    "sni": (
        "TLS поверх TCP 443 (имя сайта в начале соединения)",
        "сервер жив, но соединение рвётся, как только в его начале открытым текстом названо имя сайта (поле SNI). "
        "Так работает фильтр на пути: оборудование оператора связи читает имя и обрывает соединение; сам сайт его не рвёт, "
        "потому что на посторонние имена тот же адрес отвечает. Блокировку по имени стратегия Zapret обходит.",
    ),
    "cut16": (
        "TCP 443, передача данных после рукопожатия",
        "соединение устанавливается и начало страницы приходит, а затем поток замирает или обрывается около 16 КБ. "
        "Так ведёт себя фильтр на пути, который пропускает рукопожатие и режет передачу. Обрыв стратегия Zapret обходит.",
    ),
    "stub": (
        "HTTP или TLS (вместо сайта отдана подмена)",
        "вместо настоящего ответа приходит страница-заглушка или код 451: подмену вставляет оборудование оператора связи на пути.",
    ),
    "cert": (
        "TLS (подмена сертификата)",
        "сервер или тот, кто стоит на пути, отдаёт сертификат, выданный не сайту: перехват делает оборудование на маршруте.",
    ),
    "fingerprint": (
        "TLS (отпечаток приветствия клиента)",
        "обычная программа соединяется, а приветствие, составленное как у браузера, — нет: фильтр на пути сравнивает состав приветствия.",
    ),
}
_QUIC_LAYER = (
    "QUIC поверх UDP 443 (имя сайта в первом пакете)",
    "в первом пакете QUIC имя сайта читается так же, как в TLS. Пакет с именем этого сайта теряется даже при повторе, "
    "а тот же сервер на постороннее имя отвечает: значит, его теряет фильтр на маршруте (оборудование оператора связи), "
    "а не сам сервер и не DNS. Браузер при этом откатывается на TCP, поэтому сайт открывается, но медленнее "
    "и без преимуществ QUIC.",
)
_DNS_LAYER = (
    "DNS (какой адрес у сайта)",
    "ответ DNS отличается от эталона: адрес подменён на пути запроса. Это делает оборудование на маршруте до DNS-сервера "
    "или сам DNS-сервер; на сайт это влияет, только если программа берёт адрес из такого ответа.",
)
# Строки адреса, которые относятся к ответу DNS: короткая проверка показывает только их.
DNS_ROWS = ("DNS", "Адреса DNS", "Заметка")
_CONFIDENCE_WORDS = {"low": "низкая", "medium": "средняя", "high": "высокая"}

# Части проверки одного сайта — на что ушло его время.
STAGE_TITLES = {"dns": "адрес", "reach": "соединение", "cause": "причина", "volume": "объём", "roads": "дороги"}


@dataclass(frozen=True, slots=True)
class ViewRow:
    level: str
    title: str
    text: str = ""
    # Данные для экрана, которые текст не показывает (например, находка про DNS-серверы по частям).
    extra: object = None


@dataclass(frozen=True, slots=True)
class ViewSection:
    title: str
    rows: tuple[ViewRow, ...] = ()
    # Текст как есть, моноширинным: таблица узлов или полный отчёт.
    text: str = ""
    # Строки — однотипный перечень: экран может показать его сеткой плиток.
    tiles: bool = False
    tile_icon: str = ""
    # Пояснение «что это за проверка»: на экране оно нужно, в текстовом отчёте повторять его незачем.
    screen_only: bool = False


def shown_level(value: object, default: str = UNKNOWN) -> str:
    """Уровень из отчёта; незнакомое слово показывается как ``default``."""
    text = str(value or "")
    return text if text in _LEVELS else default


def site_level(service: dict) -> str:
    """Уровень сайта — как в отчёте: ни экран, ни текст его не пересчитывают."""
    return shown_level(service.get("level"))


def site_kind(service: dict, level: str | None = None) -> str:
    """Вид блокировки сайта — как в отчёте. Пусто — сайт открывается или вид неизвестен."""
    level = site_level(service) if level is None else level
    kind = str(service.get("kind") or "")
    return kind if level in (FAIL, WARN) and kind in KINDS and kind != KIND_OTHER else ""


def stages_text(stages: dict | None) -> str:
    """«соединение 6 с, причина 3 с» — только части длиннее секунды, от долгих к коротким."""
    parts = sorted(((float(value), name) for name, value in (stages or {}).items() if float(value) >= 1.0), reverse=True)
    return ", ".join(f"{STAGE_TITLES.get(name, name)} {value:.0f} с" for value, name in parts)


def _target_level(item: dict) -> str:
    if item.get("ok"):
        return OK
    return UNKNOWN if str(item.get("state") or "") == UNKNOWN else FAIL


def _registry_text(item: dict) -> str:
    mark = item.get("registry") or {}
    parts = []
    name = str(mark.get("name") or "")
    if name:
        parts.append("сайт значится в реестре" if name == item.get("host") else f"в реестре значится {name}")
    if mark.get("network"):
        parts.append(f"адрес сервера входит в список заблокированных ({mark['network']})")
    if parts:
        return "; ".join(parts)
    # Для открывающегося сайта оговорка лишняя, для закрытого — главное.
    if item.get("ok"):
        return "не значится"
    return "не значится — блокировать могут и без записи в реестре"


def _coverage_row(coverage) -> ViewRow | None:
    """Строка «Профиль пресета»: ловит ли запущенный пресет это имя и каким профилем."""
    if not isinstance(coverage, dict):
        return None
    profile = str(coverage.get("profile") or "")
    preset = str(coverage.get("preset") or "")
    if not profile:
        named = f" «{preset}»" if preset else ""
        return ViewRow(
            FAIL,
            "Профиль пресета",
            f"имя не попадает ни в один профиль запущенного пресета{named} — Zapret это соединение не трогает",
        )
    via = str(coverage.get("via") or "")
    where = f"«{profile}»" + (f" пресета «{preset}»" if preset else "") + (f" ({via})" if via else "")
    if not coverage.get("strategy", True):
        return ViewRow(
            WARN,
            "Профиль пресета",
            f"имя попадает в профиль {where}, а в нём нет стратегии: Zapret это соединение нарочно пропускает",
        )
    strategy = str(coverage.get("strategy_name") or "")
    if strategy:
        which = f" — «{strategy}»"
    elif coverage.get("custom_strategy"):
        which = " — своя, среди готовых стратегий её нет"
    else:
        which = ""
    return ViewRow(
        INFO, "Профиль пресета", f"имя попадает в профиль {where}: Zapret применяет к нему стратегию этого профиля{which}"
    )


def target_rows(item: dict) -> tuple[ViewRow, ...]:
    """Все измерения одного адреса сайта, в одном порядке для экрана и для текста."""
    rows = [ViewRow(_target_level(item), "Соединение", str(item.get("text") or ""))]
    if item.get("address"):
        rows.append(ViewRow(INFO, "Адрес сервера", str(item["address"])))
    tried = list(item.get("tried") or ())
    if len(tried) > 1 or (tried and not item.get("ok")):
        words = ", ".join(f"{step.get('address', '')} — {_TRIED_WORDS.get(str(step.get('result')), 'сбой')}" for step in tried)
        opened = [step for step in tried if step.get("result") == "ok"]
        broken = [step for step in tried if step.get("result") != "ok"]
        if opened and broken:
            words += (
                f". Адреса {', '.join(str(step.get('address', '')) for step in broken)} не пустили соединение с именем сайта, "
                f"а {', '.join(str(step.get('address', '')) for step in opened)} пустил: результат зависит от адреса. "
                "Браузер перебирает адреса и в итоге открывает сайт, но часть попыток уходит впустую, а программы, "
                "которые берут первый адрес, могут не открыть его. Подробное сравнение имён и адресов с контролями — в таблице ниже"
            )
            rows.append(ViewRow(WARN, "Какие адреса пробовали", words))
        else:
            rows.append(ViewRow(INFO, "Какие адреса пробовали", words))
    if item.get("unstable"):
        rows.append(ViewRow(WARN, "Открывается через раз", str(item["unstable"])))
    elif item.get("rechecked") == "opened":
        rows.append(ViewRow(INFO, "Повторная проверка", "открылся со второго раза, поодиночке — первый сбой дала нагрузка самой проверки"))
    elif item.get("rechecked") == "same":
        rows.append(ViewRow(INFO, "Повторная проверка", "поодиночке, когда остальные проверки закончились, — результат тот же"))
    if item.get("hosts_stale"):
        rows.append(
            ViewRow(
                WARN,
                "Файл hosts",
                "записанный в нём адрес не работает. Проверка открыла сайт по настоящему адресу, "
                "а браузер пойдёт по записи и сайт не откроет — обновите её в «Редакторе hosts»",
            )
        )
    cert = item.get("cert") or {}
    if cert:
        rows.append(ViewRow(FAIL, "Чужой сертификат", str(cert.get("text") or "")))
        if cert.get("issuer"):
            rows.append(ViewRow(INFO, "Кем выдан", str(cert["issuer"])))
        if cert.get("names"):
            rows.append(ViewRow(INFO, "Каким сайтам выдан", ", ".join(map(str, cert["names"][:6]))))
        if cert.get("advice"):
            rows.append(ViewRow(INFO, "Что делать", str(cert["advice"])))
    for proto in item.get("protocols") or ():
        rows.append(ViewRow(_PROTOCOL_STATES.get(str(proto.get("state")), UNKNOWN), str(proto.get("title") or ""), str(proto.get("text") or "")))
    if item.get("kind_title"):
        rows.append(ViewRow(INFO, "Вид блокировки", str(item["kind_title"])))
    coverage_row = _coverage_row(item.get("coverage"))
    if coverage_row is not None:
        rows.append(coverage_row)
    if item.get("cause_text"):
        rows.append(ViewRow(FAIL, "Как блокируют", str(item["cause_text"])))
    rows.extend(video_node_rows(item.get("video_node")))
    rows.extend(video_sweep_rows(item.get("video_sweep")))
    rows.extend(video_fix_rows(item.get("video_fixes")))
    if "registry" in item:
        rows.append(ViewRow(INFO, "Реестр РКН", _registry_text(item)))
    if item.get("seconds"):
        parts = stages_text(item.get("stages"))
        rows.append(ViewRow(INFO, "Проверка заняла", f"{float(item['seconds']):.0f} с" + (f" ({parts})" if parts else "")))
    if item.get("quic_text"):
        rows.append(ViewRow(_QUIC_LEVELS.get(str(item.get("quic")), INFO), "QUIC (UDP 443)", str(item["quic_text"])))
    if item.get("volume_text"):
        rows.append(ViewRow(WARN if item.get("volume") == "cut" else INFO, "Объём загрузки", str(item["volume_text"])))
    transfer = item.get("transfer") or {}
    if transfer.get("text") and transfer.get("code") != "unknown":
        rows.append(ViewRow(shown_level(transfer.get("level")), "Скорость ответа", str(transfer["text"])))
    outside = _OUTSIDE_WORDS.get(str(item.get("outside") or ""))
    if outside:
        rows.append(ViewRow(outside[0], f"С сервера Zapret DNS {zapret_dns.COUNTRY_RU_IN}", outside[1]))
    if item.get("dns_reason"):
        rows.append(ViewRow(_DNS_LEVELS.get(str(item.get("dns_state")), INFO), "DNS", str(item["dns_reason"])))
    if item.get("dns_detail"):
        rows.append(ViewRow(INFO, "Адреса DNS", str(item["dns_detail"])))
    rows.extend(blocker_rows(item))
    if item.get("note"):
        rows.append(ViewRow(INFO, "Заметка", str(item["note"])))
    return tuple(rows)


def video_node_rows(node: object) -> tuple[ViewRow, ...]:
    """Видеосервер, который не открылся: чья сеть, ступени до него, запрос через другие адреса Google и вывод."""
    if not isinstance(node, dict):
        return ()
    rows = []
    if node.get("owner"):
        rows.append(ViewRow(INFO, "Чей видеосервер", str(node["owner"])))
    for step in node.get("ladder") or ():
        rows.append(ViewRow(shown_level(step.get("level")), f"Видеосервер: {step.get('title', '')}", str(step.get("text") or "")))
    for via in node.get("via") or ():
        rows.append(
            ViewRow(shown_level(via.get("level")), f"Через адрес Google {via.get('ip', '')} ({via.get('name', '')})", str(via.get("text") or ""))
        )
    if node.get("text"):
        rows.append(ViewRow(shown_level(node.get("level")), "Вывод по видеосерверу", str(node["text"])))
    return tuple(rows)


# Код узла из ``video_sweep``: (уровень, слова).
_SWEEP_WORDS = {
    "ok": (OK, "ответ сервера приходит"),
    "cut_window": (WARN, "ответ сервера режут; с окном приёма в 1 байт приходит — поможет wssize"),
    "silent": (FAIL, "соединение есть, а ответ сервера не приходит"),
    "reset": (FAIL, "соединение сбрасывают"),
    "no_route": (FAIL, "соединение не устанавливается"),
    "unclear": (UNKNOWN, "проверить не удалось"),
}


# Слова исправлений из ``video_fixes``: ключ -> (заголовок, слова). ``{groups}`` — группы узлов через запятую.
_FIX_WORDS = {
    "block_caches": (
        "Можно сделать: закрыть молчащие кэши провайдера",
        "Кэш YouTube у вашего провайдера ({groups}) не отвечает, а серверы Google отвечают. Если закрыть эти кэши в файле "
        "hosts, плеер перейдёт на серверы Google. Помогает не всем: если видео станет хуже, верните как было. После "
        "изменения перезапустите браузер.",
    ),
    "quic_off": (
        "Можно сделать: выключить QUIC для YouTube",
        "Видео может идти по QUIC (UDP), а на нём приёмы Zapret и малое окно приёма не действуют. Без QUIC браузер "
        "возьмёт обычное соединение TCP.",
    ),
    "wssize_preset": (
        "Можно сделать: пресет с малым окном приёма",
        "Ответ видеосервера режут, но с окном приёма в 1 байт он приходит. Пресет «Default + YouTube no QUIC + wssize» "
        "включает такое окно для видео YouTube и выключает для него QUIC.",
    ),
    "prefer_ipv6": (
        "Можно сделать: пользоваться IPv6",
        "Видеосерверы отвечают по IPv6, а по IPv4 нет. Не отключайте IPv6 в настройках сети: браузер сам возьмёт его.",
    ),
    "wait_reload": (
        "Если ролик всё равно грузится долго",
        "YouTube сам выбирает видеосервер для каждого ролика, а фильтр закрывает серверы по одному: плеер пробует их по "
        "очереди, поэтому видео может начаться не сразу. Если ролика нет в кэше вашего провайдера (по сообщениям "
        "пользователей, чаще у зарубежных и новых видео), кэш забирает его из-за границы, и этот путь настройками Zapret "
        "не исправить: помогает подождать несколько минут и обновить страницу.",
    ),
}


def video_fix_rows(fixes: object) -> tuple[ViewRow, ...]:
    """Что можно сделать с видео (``video_fixes.recommend``): строка на исправление. ``extra`` — ключ и данные для кнопки."""
    rows = []
    for fix in fixes or ():
        if not isinstance(fix, dict) or str(fix.get("key")) not in _FIX_WORDS:
            continue
        title, words = _FIX_WORDS[str(fix["key"])]
        groups = ", ".join(str(group) for group in fix.get("groups") or ())
        rows.append(ViewRow(INFO, title, words.format(groups=groups), extra=dict(fix)))
    return tuple(rows)


def video_sweep_rows(sweep: object) -> tuple[ViewRow, ...]:
    """Обход всех найденных видеосерверов: сколько открывается и чем закрыты остальные, по IPv4 и IPv6.

    Показывается, когда узлов (с учётом IPv4 и IPv6) больше одного: один узел и так описан выше.
    """
    nodes = [node for node in (sweep or ()) if isinstance(node, dict)]
    if len(nodes) < 2:
        return ()
    working = [node for node in nodes if node.get("code") == "ok"]
    level = OK if len(working) == len(nodes) else (WARN if working else FAIL)
    rows = [ViewRow(level, "Видеосерверы YouTube", f"отвечают {len(working)} из {len(nodes)} проверенных адресов")]
    for node in nodes:
        node_level, words = _SWEEP_WORDS.get(str(node.get("code")), _SWEEP_WORDS["unclear"])
        rows.append(ViewRow(node_level, f"Видеосервер {node.get('host', '')} ({node.get('family', '')})", f"{node.get('ip', '')}: {words}"))
    return tuple(rows)


def blocker_rows(item: dict) -> tuple[ViewRow, ...]:
    """Кто мешает и на каком уровне сети: по строке на каждый найденный уровень, с доказательством и следствием."""
    found: list[tuple[str, tuple[str, str], str]] = []
    kind = str(item.get("kind") or "")
    if kind in _BLOCK_LAYERS and not item.get("ok"):
        found.append((FAIL, _BLOCK_LAYERS[kind], str(item.get("cause_text") or "")))
    if item.get("quic") == "blocked_by_name":
        found.append((WARN, _QUIC_LAYER, str(item.get("quic_text") or "")))
    if item.get("dns_state") == "spoofed":
        found.append((WARN, _DNS_LAYER, str(item.get("dns_reason") or "")))
    rows = []
    for level, (layer, meaning), evidence in found:
        text = f"{meaning} Что показала проверка: {evidence}." if evidence else meaning
        rows.append(ViewRow(level, f"Кто мешает: {layer}", text))
    return tuple(rows)


# Слои, по которым сравниваются адреса одного сервиса: (название, ключ, значения «плохо» и «хорошо», слова).
_COMPARED_LAYERS = (
    # «Молчит» не сравнивается: сервер, не отвечающий на QUIC ни с каким именем (кэш провайдера), его просто не поддерживает.
    ("QUIC (UDP 443)", "quic", {"blocked_by_name": "пакеты теряются"}, {"ok": "отвечает"}),
    ("Объём загрузки", "volume", {"cut": "замирает"}, {"ok": "без обрыва"}),
    ("DNS", "dns_state", {"spoofed": "адрес подменён"}, {"ok": "совпал с эталоном"}),
)


def selectivity_section(service: dict) -> ViewSection | None:
    """Сравнение адресов сервиса по слоям: блокировка бывает избирательной, и угадывать по одному адресу нельзя.

    Строка появляется, только если на слое есть и плохие, и хорошие адреса: тогда видно, что результат зависит
    от имени или узла, а не от всей сети разом.
    """
    targets = [item for item in service.get("targets") or () if isinstance(item, dict)]
    if len(targets) < 2:
        return None
    rows: list[ViewRow] = []
    opens = [str(item.get("host") or "") for item in targets if item.get("ok")]
    closed = [str(item.get("host") or "") for item in targets if not item.get("ok")]
    if opens and closed:
        rows.append(ViewRow(FAIL, "Соединение: по адресам", f"не открываются: {', '.join(closed)}; открываются: {', '.join(opens)}"))
    for title, key, bad_words, good_words in _COMPARED_LAYERS:
        bad = [f"{item.get('host')} ({bad_words[str(item.get(key))]})" for item in targets if str(item.get(key)) in bad_words]
        good = [f"{item.get('host')} ({good_words[str(item.get(key))]})" for item in targets if str(item.get(key)) in good_words]
        if bad and good:
            rows.append(ViewRow(WARN, f"{title}: по адресам", f"мешает: {'; '.join(bad)}. Проходит: {'; '.join(good)}"))
    if not rows:
        return None
    rows.append(
        ViewRow(
            INFO,
            "Что это значит",
            "результат разный у разных имён и узлов сервиса, хотя сеть и линия у вас одни. Значит, мешает не обрыв всей связи, "
            "а фильтр, который смотрит на имя сайта, узел или протокол. Вывод по одному адресу сервиса был бы догадкой: "
            "блокировка бывает избирательной и задевает только часть имён, узлов или протоколов.",
        )
    )
    return ViewSection("Избирательность: чем адреса сервиса отличаются", tuple(rows))


_CELL_WORDS = {"answered": "ответ", "cut": "обрыв", "unknown": "?"}
_FINDING_LEVELS = {"ok": OK, "warn": WARN, "info": INFO, "fail": FAIL}


def _matrix_text(matrix: dict) -> str:
    """Таблица «имя × адрес»: у каждой ячейки исход TLS и QUIC словами, чтобы сравнение было видно глазами."""
    addresses = [(str(item.get("host") or ""), str(item.get("ip") or "")) for item in matrix.get("addresses") or ()]
    heads = [f"{host} {ip}".strip() for host, ip in addresses]
    lines = []
    for row in matrix.get("table") or ():
        cells = []
        for cell in row.get("cells") or ():
            tls = _CELL_WORDS.get(str(cell.get("tls")), "?")
            quic = _CELL_WORDS.get(str(cell.get("quic")), "?")
            cells.append(f"TLS {tls}, QUIC {quic}")
        lines.append(f"{row.get('name', '')}:")
        lines.extend(f"    на {head} → {cell}" for head, cell in zip(heads, cells, strict=False))
    return "\n".join(lines)


def matrix_sections(service: dict) -> tuple[ViewSection, ...]:
    """Таблица избирательности (имя × адрес × протокол с контролями) и выводы по ней, подробно."""
    matrix = service.get("selectivity")
    if not isinstance(matrix, dict) or not matrix.get("findings"):
        return ()
    findings = tuple(
        ViewRow(_FINDING_LEVELS.get(str(item.get("level")), INFO), str(item.get("title") or ""), str(item.get("text") or ""))
        for item in matrix["findings"]
    )
    return (
        ViewSection("Избирательность: выводы по таблице имён и адресов", findings),
        ViewSection("Таблица: имя × адрес (исход TLS и QUIC)", text=_matrix_text(matrix)),
        ViewSection(
            "Как читать таблицу",
            (
                ViewRow(
                    INFO,
                    "Каждое имя сайта проверено на каждом адресе сервиса двумя протоколами: приветствием TLS и первым пакетом QUIC. "
                    "Контроли — постороннее имя и приветствие без имени — показывают, жив ли адрес сам по себе. "
                    "Третий контроль — первое имя сервиса другими буквами: сервер регистр не различает, и если такое имя "
                    "проходит, а обычное нет, их различает фильтр, который ищет имя буква в букву. "
                    "Если контроль отвечает, а имя сайта нет, режут имя. Если не отвечает ничего, закрыт адрес или протокол. "
                    "Если имя проходит на одном адресе и обрывается на другом, результат зависит от узла. "
                    "Проба отправляется только при сигнале беды: обычная проверка таких пакетов не шлёт.",
                ),
            ),
            screen_only=True,
        ),
    )


def path_section(item: dict) -> ViewSection | None:
    """Путь запроса по шагам — только у главного адреса: у остальных он повторял бы тот же вывод."""
    steps = [step for step in item.get("path") or () if isinstance(step, dict)]
    if not steps or not item.get("main"):
        return None
    rows = []
    for step in steps:
        text = str(step.get("text") or "")
        confidence = _CONFIDENCE_WORDS.get(str(step.get("confidence") or ""))
        if confidence:
            text = f"{text} (уверенность {confidence})"
        rows.append(ViewRow(shown_level(step.get("level")), str(step.get("title") or step.get("key") or ""), text))
    return ViewSection("Путь запроса", tuple(rows))


def target_sections(item: dict) -> tuple[ViewSection, ...]:
    """Разделы одного адреса: его измерения и, у главного, путь запроса."""
    purpose, host = str(item.get("purpose") or ""), str(item.get("host") or "")
    sections = [ViewSection(f"{host} — {purpose}" if purpose else host, target_rows(item))]
    path = path_section(item)
    if path is not None:
        sections.append(path)
    return tuple(sections)


def name_speed_sections(names: dict) -> tuple[ViewSection, ...]:
    """Замедление по имени сайта: скорость одного сервера с его именем и с именами проверенных сайтов."""
    rows = tuple(
        ViewRow(shown_level(item.get("state")), f"{item.get('name', '')} ({item.get('host', '')})", str(item.get("text") or ""))
        for item in names.get("items") or ()
    )
    if not rows:
        return ()
    control = ViewRow(INFO, f"Контроль: {names.get('server', '')} под своим именем", str(names.get("control") or ""))
    return (
        ViewSection(str(names.get("headline") or "Замедление по имени сайта"), (control, *rows)),
        ViewSection(
            "Как проверяется замедление по имени",
            (
                ViewRow(
                    INFO,
                    "С одного и того же постороннего сервера качается один файл: сначала под его собственным именем, "
                    "затем под именем каждого открывшегося сайта. Сервер и дорога те же, отличается только имя, которое "
                    "видит фильтр. Если с именем сайта скорость в разы ниже — замедляют по имени. Медленный замер "
                    "перепроверяется, контроль в конце повторяется.",
                ),
            ),
            screen_only=True,
        ),
    )


def service_sections(service: dict) -> tuple[ViewSection, ...]:
    """Подробности сайта: адреса с путём запроса, что делать, DNS, замедление по имени, вид блокировки."""
    sections: list[ViewSection] = []
    for item in service.get("targets") or ():
        sections.extend(target_sections(item))
    selectivity = selectivity_section(service)
    if selectivity is not None:
        sections.append(selectivity)
    sections.extend(matrix_sections(service))
    advice = [str(item) for item in service.get("advice") or ()]
    if advice:
        sections.append(ViewSection("Что делать", tuple(ViewRow(INFO, item) for item in advice)))
    if service.get("dns_note"):
        sections.append(ViewSection("DNS", (ViewRow(WARN, str(service["dns_note"])),)))
    if service.get("names"):
        # Замер потока по имени сайта и видеосервера: есть только у проверки одного сайта («Проверка домена»).
        sections.extend(name_speed_sections(service["names"]))
    kind = site_kind(service)
    if kind and kind_info(kind).about:
        sections.append(ViewSection(kind_info(kind).title, (ViewRow(INFO, kind_info(kind).about),)))
    return tuple(sections)


def render_text(sections: Iterable[ViewSection], indent: str = "") -> list[str]:
    """Разделы строками текста: заголовок раздела и под ним строки со значком уровня."""
    lines: list[str] = []
    for section in sections:
        if section.screen_only:
            continue
        lines.append(f"{indent}{section.title}")
        lines.extend(f"{indent}   {line}" for line in section.text.splitlines())
        for row in section.rows:
            body = f"{row.title}: {row.text}" if row.text else row.title
            lines.append(f"{indent}   {TEXT_ICONS.get(row.level, TEXT_ICONS[UNKNOWN])} {body}")
    return lines


# --- Слова о сайте из фактов проверки -------------------------------------------
# Строка «открывается ли адрес», короткий итог, слово итога сайта, дороги и метки.
# ``report_text`` только собирает факты и берёт слова отсюда: один факт — одни
# слова на карточке, в журнале и в истории.


def fail_text(probe: Probe) -> str:
    """Почему адрес не открылся — одной фразой."""
    if probe.volume is not None and probe.volume.code == volume_probe.VOLUME_CUT:
        return f"{probe.volume.text} — так провайдер обрывает загрузку"
    if probe.reach_state == ReachState.CERT and probe.cert is not None:
        return probe.cert.text
    return describe_reach(probe.reach, timeout=HTTPS_TIMEOUT)


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


def short_text(probe: Probe) -> str:
    if probe.reach_state != ReachState.OK:
        return fail_text(probe)
    if probe.reach is not None and ":" in probe.reach.ip:
        return "открывается только по IPv6"
    return "открывается"


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
    "reply_window": "режут ответ сервера",
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


def add_name_speed(site: dict, names: dict, speed_problems) -> None:
    """Дописывает к записи сайта замер потока по имени; замедление или обрыв меняют итог.

    ``speed_problems`` — проблемы скорости из ``problems.speed_problems``: из них
    берутся советы.
    """
    site["names"] = names
    if names.get("level") != "warn":
        return
    hurt = [str(item.get("host") or "") for item in names.get("items") or () if item.get("state") == "warn"]
    if site.get("level") == "ok":
        # Иначе итогом осталось бы «открывается», хотя пользоваться сайтом нельзя.
        site["level"] = "warn"
        site["status"] = "Поток режут"
        site["headline"] = f"{site.get('label', '')} открывается, но поток данных режут по имени: {', '.join(hurt)}"
    for problem in speed_problems:
        advice = problem.get("advice") or ()
        for item in [advice] if isinstance(advice, str) else advice:
            if item and item not in site.setdefault("advice", []):
                site["advice"].append(str(item))
