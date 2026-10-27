"""Видеосервер YouTube не открылся: на какой ступени он закрыт и поможет ли стратегия.

Видео YouTube идёт не с youtube.com, а с видеосервера вида ``rr4---sn-….googlevideo.com``.
Часто это кэш Google внутри сети провайдера (GGC). Когда такой узел не открывается,
человеку нужен ответ на один вопрос: поможет ли Zapret. Стратегии Zapret прячут от
фильтра имя сайта в начале соединения. Если фильтр режет имя — помогут. Если узел не
пускают ни с каким именем, даже без имени, — не помогут, и перебор сотен стратегий
только тратит время (так было у абонента МТС в октябре 2026: адрес 212.188.37.15
сбрасывал любое шифрованное соединение).

Проверка идёт по ступеням, от самой грубой к самой тонкой:

1. соединение TCP с портом 443 — есть ли дорога до адреса вообще;
2. обычный HTTP на порту 80 — жив ли адрес, если 443 молчит;
3. шифрование без имени, с посторонним и с разрешённым именем — жив ли адрес для TLS;
4. то же имя другими буквами — ищет ли фильтр имя буква в букву;
5. шифрование с настоящим именем узла.

Видеосерверы Google принимают любое имя (замеры из Москвы, 28 проб из 28): отказ на
всех именах здесь значит закрытый адрес, а не привередливый сервер.

Отдельно — тот же запрос узла через другой адрес Google. Плеер YouTube получает
подписанный запрос замера скорости; если через адрес, например, i.ytimg.com он отдаёт
данные, видео этого узла можно получить мимо закрытого адреса.

Сбор (``collect``) отделён от вывода (``judge``): вывод — чистая функция над фактами.
Ступени 1–5 сняты пробами причины (``block_cause``), здесь они только читаются.
"""

from __future__ import annotations

from dataclasses import dataclass

from diagnostics import block_cause
from diagnostics.block_cause import CauseFacts, HelloResult
from diagnostics.levels import FAIL, OK, UNKNOWN, WARN
from diagnostics.tls_probe import KIND_CONNECT, KIND_RESET, KIND_TIMEOUT, ProbeResult

__all__ = [
    "NODE_ADDRESS",
    "NODE_DOWN",
    "NODE_NAME",
    "NODE_NO_ROUTE",
    "NODE_UNCLEAR",
    "VIA_NAMES",
    "LadderStep",
    "NodeFacts",
    "NodeVerdict",
    "ViaAddress",
    "collect",
    "judge",
]

# Справка без оценки: строка отчёта, а не поломка (как ``service_view.INFO``).
INFO = "info"

# Режут имя: это стратегия Zapret обходит.
NODE_NAME = "name"
# Соединение есть, а шифрование не проходит ни с каким именем: закрыт адрес.
NODE_ADDRESS = "address"
# Соединение TCP не устанавливается: до адреса нет дороги.
NODE_NO_ROUTE = "no_route"
# Не открывается и снаружи: не работает сам узел.
NODE_DOWN = "down"
# Пробы не дали ясного ответа.
NODE_UNCLEAR = "unclear"

# Через чьи адреса Google пробуется запрос узла: сайты YouTube, которые открываются почти всегда.
VIA_NAMES = ("i.ytimg.com", "youtubei.googleapis.com", "www.youtube.com")
# Сколько читать ответа на запрос замера скорости: хватит, чтобы увидеть, что данные идут.
VIA_READ_BYTES = 64 * 1024
VIA_TIMEOUT_S = 4.0

_HELLO_WORDS = {
    block_cause.HELLO_OK: (OK, "отвечает"),
    block_cause.HELLO_ALERT: (OK, "отвечает отказом — значит, адрес доступен"),
    block_cause.HELLO_RESET: (FAIL, "соединение сброшено"),
    block_cause.HELLO_TIMEOUT: (FAIL, "ответа нет"),
    block_cause.HELLO_CONNECT: (FAIL, "соединение не установилось"),
}


@dataclass(frozen=True, slots=True)
class ViaAddress:
    """Запрос узла через чужой адрес Google. ``status`` None — до ответа HTTP не дошло."""

    name: str
    ip: str
    hello: HelloResult
    # Проверялись ли данные: без подписанного запроса замера проверить можно только шифрование.
    fetched: bool = False
    status: int | None = None
    size: int = 0
    failure: str = ""


@dataclass(frozen=True, slots=True)
class NodeFacts:
    host: str
    result: ProbeResult
    # Пробы причины (``block_cause.collect``). None — их не было.
    cause: CauseFacts | None = None
    # Владелец сети узла (``utils.ip_owner.IpOwner``). None — узнать не удалось.
    owner: object | None = None
    via: tuple[ViaAddress, ...] = ()


@dataclass(frozen=True, slots=True)
class LadderStep:
    key: str
    title: str
    level: str
    text: str

    def as_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "level": self.level, "text": self.text}


@dataclass(frozen=True, slots=True)
class NodeVerdict:
    code: str
    level: str
    text: str
    ladder: tuple[LadderStep, ...]
    owner: str = ""
    # Строки «через адрес Google»: (имя, адрес, уровень, слова).
    via: tuple[tuple[str, str, str, str], ...] = ()
    # Что делать вместо совета по виду блокировки. Пусто — общий совет подходит.
    advice: tuple[str, ...] = ()
    strategy_helps: bool = True

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "level": self.level,
            "text": self.text,
            "owner": self.owner,
            "ladder": [step.as_dict() for step in self.ladder],
            "via": [{"name": name, "ip": ip, "level": level, "text": text} for name, ip, level, text in self.via],
            "advice": list(self.advice),
            "strategy_helps": self.strategy_helps,
        }


def collect(run, host: str, result: ProbeResult, cause: CauseFacts | None) -> NodeFacts:
    """Владелец сети узла и запрос узла через другие адреса Google. Сеть — только здесь."""
    from diagnostics import net_access, video_nodes

    owner = video_nodes.node_owner(run, result.ip) if result.ip else None
    via: tuple[ViaAddress, ...] = ()
    if result.kind != KIND_CONNECT and not run.dns_cancelled():
        goodput_host, path = getattr(run, "video_goodput", ("", ""))
        # Подписанный запрос годится только для своего узла.
        path = path if goodput_host == host else ""
        addresses: dict[str, str] = {}
        for name in VIA_NAMES:
            ip = next((item for item in net_access.browser_ipv4(run, name) if item != result.ip), "")
            if ip and ip not in addresses.values():
                addresses[name] = ip
        futures = [run.submit(_via_one, run, host, name, ip, path) for name, ip in addresses.items()]
        via = tuple(future.result() for future in futures)
    return NodeFacts(host=host, result=result, cause=cause, owner=owner, via=via)


def _via_one(run, host: str, name: str, ip: str, path: str) -> ViaAddress:
    from diagnostics import net_access

    hello = block_cause.tls_hello(ip, host, cancel=run.probe_cancel, timeout=VIA_TIMEOUT_S)
    if not hello.answered or not path or run.dns_cancelled():
        return ViaAddress(name, ip, hello)
    answer = net_access.fetch(run, host, ip, path, timeout=VIA_TIMEOUT_S, read_limit=VIA_READ_BYTES)
    from diagnostics.video_nodes import page_failure_reason

    failure = "" if answer.status is not None else page_failure_reason(answer)
    return ViaAddress(name, ip, hello, fetched=True, status=answer.status, size=answer.body_size, failure=failure)


def _hello_step(key: str, title: str, hello: HelloResult | None) -> LadderStep | None:
    if hello is None or hello.kind == block_cause.HELLO_CANCELLED:
        return None
    level, text = _HELLO_WORDS.get(hello.kind, (UNKNOWN, "проверить не удалось"))
    return LadderStep(key, title, level, text)


def _ladder(facts: NodeFacts) -> tuple[LadderStep, ...]:
    result, cause = facts.result, facts.cause
    connected = result.kind != KIND_CONNECT
    steps = [
        LadderStep(
            "tcp",
            "Соединение TCP, порт 443",
            OK if connected else FAIL,
            f"с {result.ip} устанавливается" if connected else f"с {result.ip} не устанавливается",
        )
    ]
    if cause is not None and cause.http is not None:
        status = cause.http.status
        steps.append(
            LadderStep("http", "Обычный HTTP, порт 80", OK if status is not None else FAIL, f"отвечает (код {status})" if status is not None else "ответа нет")
        )
    if not connected or cause is None:
        return tuple(steps)
    for step in (
        _hello_step("nameless", "Шифрование без имени", cause.nameless),
        _hello_step("foreign", f"Шифрование с посторонним именем {block_cause.NEUTRAL_NAME}", cause.neutral),
        _hello_step("allowed", f"Шифрование с разрешённым именем {cause.allowed_name}", cause.allowed),
        _hello_step("recased", f"Шифрование с тем же именем другими буквами ({cause.recased_name})", cause.recased),
    ):
        if step is not None:
            steps.append(step)
    steps.append(_real_step(result, cause.real_again))
    return tuple(steps)


def _real_step(result: ProbeResult, again: HelloResult | None) -> LadderStep:
    """Настоящее имя: основная попытка и повтор, если он был."""
    title = "Шифрование с именем видеосервера"
    if result.kind not in (KIND_RESET, KIND_TIMEOUT):
        return LadderStep("real", title, UNKNOWN, "сервер ответил ошибкой шифрования — по ней не понять, режут ли имя")
    text = "соединение сброшено" if result.kind == KIND_RESET else "ответа нет"
    if again is not None and again.kind == block_cause.HELLO_OK:
        return LadderStep("real", title, WARN, f"{text}, но со второго раза прошло")
    if _cut(again):
        text += " — и при повторе"
    return LadderStep("real", title, FAIL, text)


def _via_rows(facts: NodeFacts) -> tuple[tuple[str, str, str, str], ...]:
    rows = []
    for item in facts.via:
        if not item.hello.answered:
            level, text = INFO, "с именем этого видеосервера адрес не отвечает"
        elif not item.fetched:
            level, text = INFO, (
                "с именем этого видеосервера адрес отвечает; отдаст ли он видео, не проверялось — "
                "страница YouTube не дала подписанный запрос этого узла"
            )
        elif item.status is not None and 200 <= item.status < 300 and item.size > 0:
            level, text = OK, f"запрос видеосервера отдал данные ({max(1, item.size // 1024)} КБ): этот узел отвечает через этот адрес"
        elif item.status is not None:
            level, text = INFO, f"ответил кодом {item.status}: через этот адрес видео этого узла не получить"
        else:
            level, text = INFO, f"запрос не прошёл: {item.failure or 'ответа нет'}"
        rows.append((item.name, item.ip, level, text))
    return tuple(rows)


def _working_via(facts: NodeFacts) -> ViaAddress | None:
    return next(
        (item for item in facts.via if item.fetched and item.status is not None and 200 <= item.status < 300 and item.size > 0),
        None,
    )


def _cut(hello: HelloResult | None) -> bool:
    return hello is not None and hello.kind in (block_cause.HELLO_RESET, block_cause.HELLO_TIMEOUT)


def _answered(hello: HelloResult | None) -> bool:
    return hello is not None and hello.answered


def _advice(code: str, facts: NodeFacts, provider_cache: bool) -> tuple[str, ...]:
    lines: list[str] = []
    if code == NODE_DOWN:
        return (
            "Узел не работает сам, и ни стратегия, ни настройки программы его не починят. Обычно YouTube "
            "переключается на другой узел сам — повторите проверку позже.",
        )
    if code == NODE_ADDRESS:
        lines.append(
            "Стратегии Zapret прячут от фильтра имя сайта, а этот видеосервер не пускают и без имени, поэтому "
            "«Подбор стратегии» его не откроет. Перебирать стратегии для видео не нужно."
        )
    else:
        lines.append(
            "Zapret меняет содержимое соединения, но не открывает дорогу к адресу, до которого соединение не доходит, "
            "поэтому «Подбор стратегии» этот видеосервер не откроет."
        )
    working = _working_via(facts)
    if working is not None:
        lines.append(
            f"Этот же видеосервер сейчас отдаёт данные через адрес Google {working.ip} ({working.name}). Строка "
            f"«{working.ip} {facts.host}» в файле hosts вернёт видео, пока YouTube выдаёт вам именно этот узел."
        )
    whose = " (а этот узел — кэш Google в сети вашего провайдера)" if provider_cache else ""
    lines.append(
        f"YouTube выбирает видеосервер по вашему адресу в интернете{whose}, поэтому другой DNS его не меняет. "
        "Другой видеосервер YouTube выдаст, если запросы к youtube.com пойдут через прокси или VPN другого региона; "
        "остальной трафик можно оставить напрямую."
    )
    return tuple(lines)


def judge(facts: NodeFacts | None, *, outside: str = "") -> NodeVerdict | None:
    """Вывод о видеосервере, который не открылся. ``outside`` — ответ сервера за рубежом («up» / «down»)."""
    if facts is None:
        return None
    from diagnostics import video_nodes

    result, cause = facts.result, facts.cause
    ladder = _ladder(facts)
    owner = video_nodes.owner_text(facts.owner)
    provider_cache = facts.owner is not None and not video_nodes.is_google(facts.owner)
    via = _via_rows(facts)

    def verdict(code: str, level: str, text: str, helps: bool) -> NodeVerdict:
        advice = () if helps else _advice(code, facts, provider_cache)
        return NodeVerdict(code, level, text, ladder, owner, via, advice, helps)

    if outside == "down":
        return verdict(
            NODE_DOWN, FAIL, "видеосервер не открывается и с сервера Zapret DNS в Нидерландах: не работает сам узел, а не ваша линия", False
        )
    http_alive = cause is not None and cause.http is not None and cause.http.status is not None
    if result.kind == KIND_CONNECT:
        text = "соединение с видеосервером не устанавливается: до адреса не доходит даже начало соединения"
        if http_alive:
            text += ", хотя по обычному HTTP на порту 80 тот же адрес отвечает — закрыт именно порт 443"
        return verdict(NODE_NO_ROUTE, FAIL, text, False)
    if cause is None or (cause.real_again is not None and cause.real_again.kind == block_cause.HELLO_OK):
        # Проб причины нет или настоящее имя прошло со второго раза: судить не о чем.
        return verdict(NODE_UNCLEAR, INFO, "по пробам не видно, режут имя видеосервера или его адрес", True)
    real_cut = result.kind in (KIND_RESET, KIND_TIMEOUT)
    if real_cut and _answered(cause.recased):
        return verdict(
            NODE_NAME,
            WARN,
            f"с именем видеосервера соединение рвётся, а то же имя другими буквами ({cause.recased_name}) проходит: "
            "фильтр на пути ищет имя буква в букву. Режут именно имя — такую блокировку стратегия Zapret обходит",
            True,
        )
    others = [item for item in (cause.nameless, cause.neutral, cause.allowed, cause.recased) if item is not None and item.kind != block_cause.HELLO_CANCELLED]
    if real_cut and any(item.answered for item in others):
        return verdict(
            NODE_NAME,
            WARN,
            "с именем видеосервера соединение рвётся, а с другим именем или без имени тот же адрес отвечает: "
            "режут именно имя — такую блокировку стратегия Zapret обходит",
            True,
        )
    if real_cut and others and all(_cut(item) for item in others):
        nameless = " даже без имени," if _cut(cause.nameless) else ""
        text = (
            f"видеосервер не принимает шифрованные соединения ни с каким именем,{nameless} хотя соединение с ним "
            "устанавливается"
            + (" и по обычному HTTP он отвечает" if http_alive else "")
            + ". Видеосерверы Google принимают любое имя, поэтому дело не в имени: на пути закрыт сам адрес "
            "для шифрованных соединений"
        )
        return verdict(NODE_ADDRESS, FAIL, text, False)
    return verdict(NODE_UNCLEAR, INFO, "по пробам не видно, режут имя видеосервера или его адрес", True)

