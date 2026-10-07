"""Примеры для экскурсии по BlockCheck.

У нового пользователя проверок ещё не было: страница пустая, и показать на ней
нечего. Поэтому экскурсия на время своих шагов рисует здесь вымышленную
проверку — тем же кодом, что и настоящую: отчёт, прошлые проверки и итог
подбора стратегии.

Это только данные. Они нигде не сохраняются: ни в истории проверок, ни в
журналах, ни в настройках. Когда экскурсия уходит со страницы, та возвращает
то, что было на ней до примера.
"""

from __future__ import annotations

from blockcheck.scan_models import StrategyProbeResult, StrategyScanReport


def _protocols(*, tls12: bool = True, tls13: bool = True, http: bool = True) -> list[dict]:
    def line(key: str, title: str, ok: bool, ms: float) -> dict:
        return {
            "key": key,
            "title": title,
            "state": "ok" if ok else "fail",
            "word": "работает" if ok else "закрыт",
            "text": f"открывается за {ms:.0f} мс" if ok else "соединение сброшено после приветствия",
            "ms": ms if ok else None,
            "code": "",
        }

    return [
        line("tls12", "TLS 1.2", tls12, 41),
        line("tls13", "TLS 1.3", tls13, 38),
        line("http", "HTTP", http, 22),
    ]


def _target(host: str, purpose: str, *, ok: bool = True, main: bool = False, **extra) -> dict:
    item = {
        "host": host,
        "purpose": purpose,
        "main": main,
        "ok": ok,
        "state": "ok" if ok else "dpi",
        "short": "открывается" if ok else "соединение сброшено",
        "text": "открывается (38 мс)" if ok else "соединение сброшено сразу после приветствия",
    }
    item.update(extra)
    return item


def _blocked_by_name(host: str, purpose: str, address: str, *, main: bool = False) -> dict:
    return _target(
        host,
        purpose,
        ok=False,
        main=main,
        address=address,
        cause="by_name",
        cause_text="Блокировка по имени сайта: тот же адрес с другим именем отвечает.",
        protocols=_protocols(tls12=False, tls13=False) if main else [],
        tried=[{"address": address, "result": "reset"}],
        rechecked="same",
    )


def _service(key: str, label: str, level: str, targets: list[dict], **extra) -> dict:
    return {"key": key, "label": label, "level": level, "kind": "", "control": False, "targets": targets, **extra}


_STRATEGY_ADVICE = "Подберите стратегию Zapret для сайта: вкладка «Подбор стратегии»."
_QUIC_ADVICE = (
    "В пресете должен быть profile для UDP 443 (QUIC) с этими сайтами. Проще всего выбрать готовый пресет, "
    "где он есть."
)


def demo_report() -> dict:
    """Отчёт вымышленной проверки: два сайта закрыты по имени, у одного закрыт QUIC, остальное открывается."""
    services = [
        _service(
            "discord",
            "Discord",
            "fail",
            [
                _blocked_by_name("discord.com", "сайт", "162.159.137.232", main=True),
                _blocked_by_name("gateway.discord.gg", "вход в приложение", "162.159.135.234"),
                _target("cdn.discordapp.com", "картинки и файлы"),
            ],
            kind="sni",
            headline="Discord не открывается",
            advice=[_STRATEGY_ADVICE],
        ),
        _service(
            "x",
            "X (Twitter)",
            "fail",
            [_blocked_by_name("x.com", "сайт", "104.244.42.193", main=True)],
            kind="sni",
            headline="X (Twitter) не открывается",
            advice=[_STRATEGY_ADVICE],
        ),
        _service(
            "youtube",
            "YouTube",
            "warn",
            [
                _target(
                    "www.youtube.com",
                    "сайт",
                    main=True,
                    address="142.250.74.110",
                    protocols=_protocols(),
                    quic="blocked_by_name",
                    quic_text="блокируется по имени сайта: тот же сервер с другим именем по QUIC отвечает",
                ),
                _target("i.ytimg.com", "превью видео"),
                _target("rr1---sn-gvnuxaxjvh-n8vs.googlevideo.com", "видео"),
            ],
            kind="quic",
            headline="YouTube открывается, но QUIC закрыт",
            advice=[_QUIC_ADVICE],
        ),
        _service(
            "telegram",
            "Telegram",
            "ok",
            [_target("web.telegram.org", "сайт", main=True, protocols=_protocols())],
        ),
        _service(
            "google",
            "Google",
            "ok",
            [_target("www.google.com", "сайт", main=True, protocols=_protocols(), quic="ok", quic_text="отвечает")],
            control=True,
        ),
        _service(
            "yandex",
            "Яндекс",
            "ok",
            [_target("ya.ru", "сайт", main=True, protocols=_protocols())],
            control=True,
            domestic=True,
        ),
    ]
    problems = [
        {
            "level": "fail",
            "text": "Discord не открывается",
            "advice": ["Блокировка по имени сайта: тот же адрес с другим именем отвечает.", _STRATEGY_ADVICE],
            "action": "strategy",
            "target": "discord.com",
            "kind": "sni",
            "title": "Discord",
            "evidence": ["Блокировка по имени сайта: тот же адрес с другим именем отвечает."],
        },
        {
            "level": "fail",
            "text": "X (Twitter) не открывается",
            "advice": ["Блокировка по имени сайта: тот же адрес с другим именем отвечает.", _STRATEGY_ADVICE],
            "action": "strategy",
            "target": "x.com",
            "kind": "sni",
            "title": "X (Twitter)",
            "evidence": ["Блокировка по имени сайта: тот же адрес с другим именем отвечает."],
        },
        {
            "level": "warn",
            "text": "QUIC (UDP 443) закрыт по имени сайта: YouTube. Видео может долго запускаться",
            "advice": [_QUIC_ADVICE],
            "action": "",
            "target": "",
            "kind": "quic",
            "title": "",
            "evidence": [],
        },
    ]
    return {
        "scope": "full",
        "zapret_running": False,
        "elapsed": 46.0,
        "timed_out": False,
        "services": services,
        "problems": problems,
        "working": ["Telegram"],
        "freeze": {
            "level": "warn",
            "headline": "Загрузка с части зарубежных хостингов обрывается",
            "advice": [_STRATEGY_ADVICE],
            "items": [],
            "servers": [
                _server("AWS", "DE.AWS-01", "freeze", "загрузка оборвалась на 16 КБ"),
                _server("AWS", "FR.AWS-02", "ok", "получено 32 КБ без обрыва"),
                _server("Akamai", "SE.AKM-01", "ok", "получено 32 КБ без обрыва"),
                _server("Cloudflare", "NL.CF-01", "ok", "получено 32 КБ без обрыва"),
                _server("OVH", "FR.OVH-01", "freeze", "загрузка оборвалась на 16 КБ"),
                _server("Hetzner", "DE.HZ-01", "ok", "получено 32 КБ без обрыва"),
            ],
        },
        "voice": {
            "level": "ok",
            "headline": "Голосовые серверы отвечают",
            "advice": [],
            "items": [
                {"name": "Google STUN", "ok": True, "state": "ok", "text": "отвечает за 31 мс"},
                {"name": "Cloudflare STUN", "ok": True, "state": "ok", "text": "отвечает за 28 мс"},
                {"name": "Discord (голос)", "ok": True, "state": "ok", "text": "отвечает за 54 мс"},
            ],
        },
        "reference": [
            {"label": "Cloudflare", "address": "1.1.1.1", "ok": True, "reason": ""},
            {"label": "Google", "address": "8.8.8.8", "ok": True, "reason": ""},
        ],
        "spoofed_hosts": [],
        "ipv6": {"state": "absent", "text": "в этой сети его нет"},
        "filter": {
            "host": "discord.com",
            "address": "162.159.137.232",
            "found": True,
            "hop": 4,
            "text": "фильтр стоит между узлом 3 и узлом 4 — это сеть провайдера",
            "hops": [
                {"ttl": 1, "address": "192.168.1.1", "rtt_ms": 0.6},
                {"ttl": 2, "address": "10.44.0.1", "rtt_ms": 3.1},
                {"ttl": 3, "address": "10.44.12.9", "rtt_ms": 4.8},
                {"ttl": 4, "address": "", "rtt_ms": None},
            ],
        },
        "system": [
            {"key": "admin", "title": "Права администратора", "level": "ok", "text": "есть", "advice": ""},
            {"key": "bfe", "title": "Служба BFE", "level": "ok", "text": "работает", "advice": ""},
            {"key": "proxy", "title": "Системный прокси", "level": "ok", "text": "выключен", "advice": ""},
        ],
    }


def _server(provider: str, server_id: str, state: str, text: str) -> dict:
    return {
        "provider": provider,
        "id": server_id,
        "host": f"{server_id.lower()}.example",
        "state": state,
        "text": text,
        "direction": "download",
        "seconds": 1.2,
    }


def _run(time: str, title: str, level: str, states: dict, problems: list[str], headline: str) -> dict:
    return {
        "kind": "blockcheck",
        "time": time,
        "title": title,
        "level": level,
        "headline": headline,
        "problems": problems,
        "states": states,
        # Файла отчёта у примера нет: экскурсия подаёт отчёт сама.
        "log_file": "",
    }


def demo_history() -> list[dict]:
    """Прошлые проверки, от старой к новой. Последняя — та же, что в ``demo_report``."""
    full = "Полная проверка"
    return [
        _run(
            "2026-10-05T19:12:00",
            full,
            "fail",
            {"Discord": "fail", "X (Twitter)": "fail", "YouTube": "fail", "Telegram": "ok"},
            ["Discord не открывается", "X (Twitter) не открывается", "YouTube не открывается"],
            "Найдены проблемы: 3",
        ),
        _run(
            "2026-10-06T08:40:00",
            "Discord и YouTube",
            "fail",
            {"Discord": "fail", "YouTube": "ok"},
            ["Discord не открывается"],
            "Найдены проблемы: 1",
        ),
        _run(
            "2026-10-06T21:03:00",
            full,
            "ok",
            {"Discord": "ok", "X (Twitter)": "ok", "YouTube": "ok", "Telegram": "ok"},
            [],
            "Всё открывается",
        ),
        _run(
            "2026-10-07T20:30:00",
            full,
            "fail",
            {"Discord": "fail", "X (Twitter)": "fail", "YouTube": "warn", "Telegram": "ok"},
            ["Discord не открывается", "X (Twitter) не открывается", "QUIC (UDP 443) закрыт: YouTube"],
            "Найдены проблемы: 3",
        ),
    ]


def demo_scan_report() -> StrategyScanReport:
    """Итог вымышленного подбора для discord.com: две стратегии надёжно работают, остальные — нет."""
    target = "discord.com"

    def result(name: str, args: str, verdict: str, *, time_ms: float = 0.0, ok: int = 0, error: str = "") -> StrategyProbeResult:
        working = verdict == "working"
        return StrategyProbeResult(
            strategy_name=name,
            strategy_id=name,
            strategy_args=args,
            target=target,
            success=working,
            time_ms=time_ms,
            error=error,
            verdict=verdict,
            attempts_ok=ok,
            attempts_total=3 if verdict in ("working", "unstable") else 0,
            apply_lines=(args,) if working else (),
        )

    working = [
        result("multisplit sni", "--lua-desync=multisplit:pos=1,midsld", "working", time_ms=64, ok=3),
        result("fake + multidisorder", "--lua-desync=fake:blob=fake_default_tls --lua-desync=multidisorder:pos=midsld", "working", time_ms=91, ok=3),
    ]
    failed = [
        result("fakedsplit", "--lua-desync=fakedsplit:pos=2", "unstable", time_ms=120, ok=2, error="соединение сброшено"),
        result("multisplit 2", "--lua-desync=multisplit:pos=2", "failed", error="соединение сброшено после приветствия"),
        result("syndata", "--lua-desync=syndata:blob=fake_default_tls", "failed", error="сайт не ответил"),
        result("oob", "--lua-desync=oob", "failed", error="соединение сброшено после приветствия"),
    ]
    return StrategyScanReport(
        target=target,
        total_tested=len(working) + len(failed),
        total_available=len(working) + len(failed),
        working_strategies=working,
        failed_strategies=failed,
        elapsed_seconds=52.0,
    )


__all__ = ["demo_history", "demo_report", "demo_scan_report"]
