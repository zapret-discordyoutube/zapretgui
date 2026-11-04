from __future__ import annotations

from blockcheck.strategy_search.probe_profile import PROTOCOL_STUN_VOICE, PROTOCOL_TCP_HTTPS, PROTOCOL_UDP_GAMES
from pathlib import Path

from blockcheck.strategy_scan_state import (
    STRATEGY_ZAPRET_FAILED,
    STRATEGY_ZAPRET_OTHER_METHOD,
    STRATEGY_ZAPRET_RESTARTING,
    STRATEGY_ZAPRET_SCAN_RUNNING,
    STRATEGY_ZAPRET_STARTED,
    StrategyScanPanelOutcome,
    StrategyApplyResult,
    StrategyScanFinishPlan,
    StrategyScanInteractionPlan,
    StrategyScanLanguagePlan,
    StrategyScanProgressPlan,
    StrategyScanProtocolUiPlan,
    StrategyScanQuickMenuPlan,
    StrategyScanResultPresentation,
    StrategyScanStartPlan,
    StrategyScanSupportContext,
    StrategyScanUiMessagePlan,
    StrategyScanUdpHintPlan,
)
from blockcheck.strategy_scan_targeting import (
    default_target_for_protocol,
    load_quick_domains,
    load_quick_stun_targets,
    normalize_target_input,
    resolve_games_ipset_paths,
    scan_protocol_from_value,
)


def tr_catalog(key: str, *, language: str | None = None, default: str = "", **kwargs) -> str:
    from app.ui_texts import tr

    return tr(key, language=language, default=default, **kwargs)


def build_protocol_ui_plan(*, scan_protocol: str, current_value: str) -> StrategyScanProtocolUiPlan:
    current = str(current_value or "")
    is_udp_games = scan_protocol == PROTOCOL_UDP_GAMES
    show_target_controls = scan_protocol != PROTOCOL_UDP_GAMES

    if scan_protocol in {PROTOCOL_STUN_VOICE, PROTOCOL_UDP_GAMES} and current and ":" not in current and not current.upper().startswith("STUN:"):
        current = ""
    # Обратный переход: STUN-сервер из режима звонков/игр — не сайт.
    lowered = current.strip().lower()
    if scan_protocol == PROTOCOL_TCP_HTTPS and (":" in lowered or lowered.startswith("stun")):
        current = ""

    normalized = normalize_target_input(current, scan_protocol)
    if not normalized:
        normalized = default_target_for_protocol(scan_protocol)

    return StrategyScanProtocolUiPlan(
        scan_protocol=scan_protocol,
        is_udp_games=is_udp_games,
        show_target_controls=show_target_controls,
        normalized_target=normalized,
        placeholder_text=default_target_for_protocol(scan_protocol),
        show_one_site_hint=scan_protocol == PROTOCOL_TCP_HTTPS,
    )


def build_udp_scope_hint_plan(
    *,
    scan_protocol: str,
    udp_games_scope: str,
    scope_all_label: str,
    scope_games_only_label: str,
) -> StrategyScanUdpHintPlan:
    if scan_protocol != PROTOCOL_UDP_GAMES:
        return StrategyScanUdpHintPlan(
            visible=False,
            text="",
            tooltip="",
        )

    paths = resolve_games_ipset_paths(udp_games_scope)
    scope_label = scope_games_only_label if udp_games_scope == "games_only" else scope_all_label

    short_names = [Path(p).name or p for p in paths]
    preview = ", ".join(short_names[:4])
    if len(short_names) > 4:
        preview += f", ... (+{len(short_names) - 4})"

    return StrategyScanUdpHintPlan(
        visible=True,
        text=f"Будут проверены адреса из {len(paths)} файл(ов) списков: {preview}. Набор: {scope_label}.",
        tooltip="\n".join(paths),
    )


def build_quick_target_menu_plan(*, scan_protocol: str, current_value: str) -> StrategyScanQuickMenuPlan:
    current = normalize_target_input(current_value, scan_protocol)
    options = load_quick_domains() if scan_protocol == PROTOCOL_TCP_HTTPS else load_quick_stun_targets()
    return StrategyScanQuickMenuPlan(
        options=options,
        current_value=current,
    )


def build_running_interaction_plan() -> StrategyScanInteractionPlan:
    return StrategyScanInteractionPlan(
        start_enabled=False,
        stop_enabled=True,
        protocol_enabled=False,
        games_scope_enabled=False,
        mode_enabled=False,
        target_enabled=False,
        quick_domain_enabled=False,
    )


def build_idle_interaction_plan(*, is_udp_games: bool) -> StrategyScanInteractionPlan:
    return StrategyScanInteractionPlan(
        start_enabled=True,
        stop_enabled=False,
        protocol_enabled=True,
        games_scope_enabled=bool(is_udp_games),
        mode_enabled=True,
        target_enabled=True,
        quick_domain_enabled=True,
    )


# Подписи страницы подбора: их берут и сборка страницы, и смена языка.
PROTOCOL_ITEMS = (
    ("page.strategy_scan.protocol_tcp", "Сайты и приложения", PROTOCOL_TCP_HTTPS),
    ("page.strategy_scan.protocol_stun", "Голосовые звонки", PROTOCOL_STUN_VOICE),
    ("page.strategy_scan.protocol_games", "Онлайн-игры", PROTOCOL_UDP_GAMES),
)
# Пояснение и значок плитки для каждого пункта PROTOCOL_ITEMS (по порядку).
PROTOCOL_TILE_DETAILS = (
    ("page.strategy_scan.protocol_tcp.hint", "YouTube, Discord, Instagram — всё, что в браузере", "fa5s.globe-europe"),
    ("page.strategy_scan.protocol_stun.hint", "Звонки в Discord и Telegram", "fa5s.headset"),
    ("page.strategy_scan.protocol_games.hint", "Roblox, Steam, Amazon и другие", "fa5s.gamepad"),
)
MODE_ITEMS = (
    ("page.strategy_scan.mode_quick", "Быстро · 30", "quick"),
    ("page.strategy_scan.mode_standard", "Тщательно · 80", "standard"),
    ("page.strategy_scan.mode_full", "Все стратегии", "full"),
)
# Подсказка о времени для каждого пункта MODE_ITEMS (по порядку).
MODE_HINTS = (
    ("page.strategy_scan.mode_quick.hint", "до первой рабочей, обычно 1–3 минуты"),
    ("page.strategy_scan.mode_standard.hint", "до трёх рабочих, ≈ 3–7 минут"),
    ("page.strategy_scan.mode_full.hint", "долго — самое время для чая ☕"),
)


def mode_hint_text(index: int, *, language: str | None = None) -> str:
    if not 0 <= index < len(MODE_HINTS):
        return ""
    key, default = MODE_HINTS[index]
    return tr_catalog(key, language=language, default=default)


def geo_service_for_target(geo_sites, *, scan_protocol: str, target_input: str) -> str:
    """Название гео-сервиса каталога hosts для введённой цели, иначе пустая строка.

    Гео-сайт сам ограничивает доступ из России: стратегия Zapret его не чинит.
    Проверяются только сайты — у голоса и игр цель не сайт.
    """
    if geo_sites is None or scan_protocol != PROTOCOL_TCP_HTTPS:
        return ""
    return str(geo_sites.service_for(normalize_target_input(target_input, scan_protocol)) or "")


def geo_site_question_text(service: str, target_input: str, *, language: str | None = None) -> str:
    # Тот же адрес, с которым запустился бы подбор.
    target = normalize_target_input(target_input, PROTOCOL_TCP_HTTPS)
    from hosts.geo_sites import only_proxy_helps, only_vpn_helps

    if only_proxy_helps((service,)):
        return tr_catalog(
            "page.strategy_scan.geo_site.question_text_proxy",
            language=language,
            default=(
                "{target} — это {service}. {service} закрыт в России по IP-адресам своих серверов: ни одна стратегия "
                "Zapret этого не изменит, а подбор зря займёт время.\n\nhosts и DNS тоже не помогут. Нужен прокси — "
                "он есть в программе, раздел «Telegram Proxy» — или приложение ZaStoGram (@zastogram)."
            ),
        ).format(service=service, target=target)
    if only_vpn_helps((service,)):
        return tr_catalog(
            "page.strategy_scan.geo_site.question_text_vpn",
            language=language,
            default=(
                "{target} — это {service}. Сервис сам ограничивает посетителей из России: он узнаёт страну по адресу "
                "вашего подключения, провайдер тут ни при чём. Ни одна стратегия Zapret этого не изменит, а подбор "
                "зря займёт время.\n\nhosts и DNS тоже не помогут — только подключение через другую страну (VPN)."
            ),
        ).format(service=service, target=target)
    return tr_catalog(
        "page.strategy_scan.geo_site.question_text",
        language=language,
        default=(
            "{target} — это {service}. Сервис сам ограничивает доступ из России: провайдер тут ни при чём, "
            "поэтому ни одна стратегия Zapret его не откроет, а подбор зря займёт время.\n\n"
            "Что помогает: DNS-профиль для этого сервиса в «Редакторе hosts» или другой DNS в «Настройке DNS»."
        ),
    ).format(service=service, target=target)


# Заметка под карточками сервисов: ТСПУ связывает сайты, поэтому сразу несколько сайтов проверять нельзя.
ONE_SITE_HINT_RU = (
    "Проверяйте сайты по одному. ТСПУ — сложные устройства, и сайты могут влиять друг на друга: "
    "вы открыли YouTube — а ТСПУ заблокировал Discord. Если проверять несколько сайтов сразу, "
    "они путают друг друга, и стратегию для небольшой блокировки найти не получится."
)


def build_language_plan(*, language: str) -> StrategyScanLanguagePlan:
    def _tr(key: str, default: str) -> str:
        return tr_catalog(key, language=language, default=default)

    return StrategyScanLanguagePlan(
        log_button_text=_tr("page.strategy_scan.log", "Подробный лог"),
        protocol_label=_tr("page.strategy_scan.protocol", "Что должно заработать?"),
        target_label=_tr("page.strategy_scan.target", "Какой сайт проверить:"),
        mode_label=_tr("page.strategy_scan.mode", "Тщательность:"),
        mode_items=[_tr(key, default) for key, default, _value in MODE_ITEMS],
        start_text=_tr("page.strategy_scan.start", "Найти рабочую стратегию"),
        stop_text=_tr("page.strategy_scan.stop", "Остановить"),
        prepare_support_text=_tr("page.strategy_scan.prepare_support", "Подготовить обращение"),
        protocol_items=[_tr(key, default) for key, default, _value in PROTOCOL_ITEMS],
        protocol_hints=[_tr(key, default) for key, default, _icon in PROTOCOL_TILE_DETAILS],
        udp_scope_label=_tr("page.strategy_scan.udp_scope", "Какие адреса игр:"),
        udp_scope_items=[
            _tr("page.strategy_scan.udp_scope_all", "Все списки адресов (по умолчанию)"),
            _tr("page.strategy_scan.udp_scope_games_only", "Только игровые списки"),
        ],
        quick_domains_text=_tr("page.strategy_scan.quick_domains", "Выбрать из списка"),
        quick_domains_tooltip=_tr(
            "page.strategy_scan.quick_domains_hint",
            "Готовые адреса: Discord, YouTube, Telegram и другие",
        ),
        one_site_hint=_tr("page.strategy_scan.one_site_hint", ONE_SITE_HINT_RU),
    )


# Что сказать о Zapret после «Применить» и что делать дальше.
_ZAPRET_AFTER_APPLY = {
    STRATEGY_ZAPRET_RESTARTING: "Zapret перезапускается с новой стратегией. Потом проверьте сайт на вкладке «Проверка сайтов».",
    STRATEGY_ZAPRET_STARTED: "Zapret запущен с новой стратегией. Проверьте сайт на вкладке «Проверка сайтов».",
    STRATEGY_ZAPRET_FAILED: "Запустить Zapret не удалось — запустите его на главной странице, чтобы стратегия заработала.",
    STRATEGY_ZAPRET_OTHER_METHOD: (
        "Стратегия записана в пресет Zapret 2, а сейчас выбран другой способ запуска — "
        "переключитесь на Zapret 2, чтобы она заработала."
    ),
    STRATEGY_ZAPRET_SCAN_RUNNING: "Подбор ещё идёт — когда он закончится, запустите Zapret, если он выключен.",
}


def build_apply_success_plan(result: StrategyApplyResult, *, zapret: str = "") -> StrategyScanUiMessagePlan:
    operation = str(getattr(result, "operation", "") or "").strip().lower()
    if operation == "updated":
        title_default = "Стратегия обновлена"
        body_text = f"{result.strategy_name} записана в профиль сайта {result.applied_profile}"
    else:
        title_default = "Стратегия применена"
        body_text = f"{result.strategy_name} записана в новый профиль: {result.applied_profile}"
    after = _ZAPRET_AFTER_APPLY.get(str(zapret or ""), "")
    blob_warnings = tuple(getattr(result, "blob_warnings", ()) or ())
    body_text = "\n".join(part for part in (body_text, after, *blob_warnings) if part)
    trouble = bool(blob_warnings) or zapret in (STRATEGY_ZAPRET_FAILED, STRATEGY_ZAPRET_OTHER_METHOD)
    return StrategyScanUiMessagePlan(
        kind="warning" if trouble else "success",
        title_key="page.strategy_scan.applied",
        title_default=title_default,
        body_text=body_text,
    )


def build_apply_error_plan(error_text: str) -> StrategyScanUiMessagePlan:
    return StrategyScanUiMessagePlan(
        kind="warning",
        title_key="common.error",
        title_default="Ошибка",
        body_text=str(error_text or ""),
    )


def build_support_success_plan(feedback) -> StrategyScanUiMessagePlan:
    return StrategyScanUiMessagePlan(
        kind="success",
        title_key="page.strategy_scan.support_prepared_title",
        title_default="Обращение подготовлено",
        body_text=str(getattr(feedback, "info_text", "") or ""),
        status_text=str(getattr(feedback, "status_text", "") or ""),
    )


def build_support_error_plan(error_text: str) -> StrategyScanUiMessagePlan:
    return StrategyScanUiMessagePlan(
        kind="warning",
        title_key="page.strategy_scan.error",
        title_default="Ошибка сканирования",
        body_text=f"Не удалось подготовить обращение:\n{error_text}",
        status_text="Ошибка подготовки",
    )


def build_support_context(
    *,
    stored_scan_protocol: str,
    stored_scan_target: str,
    raw_protocol_value,
    raw_target_input: str,
    raw_protocol_label: str,
    raw_mode_label: str,
    stored_mode: str,
) -> StrategyScanSupportContext:
    scan_protocol = stored_scan_protocol or scan_protocol_from_value(raw_protocol_value)
    target = stored_scan_target or normalize_target_input(raw_target_input, scan_protocol)
    if not target:
        target = default_target_for_protocol(scan_protocol)

    protocol_label = str(raw_protocol_label or "").strip() or scan_protocol
    mode_label = str(raw_mode_label or "").strip() or str(stored_mode or "")
    return StrategyScanSupportContext(
        scan_protocol=scan_protocol,
        target=target,
        protocol_label=protocol_label,
        mode_label=mode_label,
    )


def count_working_results(result_rows: list[dict]) -> int:
    return sum(1 for row in result_rows if row.get("success"))


def build_progress_plan(
    *,
    strategy_name: str,
    index: int,
    total: int,
    result_rows: list[dict],
) -> StrategyScanProgressPlan:
    working = count_working_results(result_rows)
    return StrategyScanProgressPlan(
        total=max(0, int(total)),
        status_text=f"[{index + 1}/{total}] {strategy_name}  |  надёжно работают: {working}",
    )


_VERDICT_PRESENTATION = {
    # verdict: (текст в таблице, тон)
    "working": ("Работает", "success"),
    "unstable": ("Нестабильно", "timeout"),
    "failed": ("Не работает", "fail"),
    "crash": ("Сбой winws2", "timeout"),
    "not_counted": ("Не засчитано", "timeout"),
}


def build_result_presentation(result, *, row_number: int) -> StrategyScanResultPresentation:
    verdict = str(getattr(result, "verdict", "") or ("working" if result.success else "failed"))
    status_text, status_tone = _VERDICT_PRESENTATION.get(verdict, ("Не работает", "fail"))
    attempts_ok = int(getattr(result, "attempts_ok", 0) or 0)
    attempts_total = int(getattr(result, "attempts_total", 0) or 0)
    if verdict in ("working", "unstable", "not_counted") and attempts_total:
        status_text = f"{status_text} {attempts_ok}/{attempts_total}"

    error_text = str(getattr(result, "error", "") or "")
    tip_parts = [result.strategy_args]
    if error_text:
        tip_parts.append(f"\n--- Причина ---\n{error_text}")
    if verdict == "working":
        if attempts_total > attempts_ok:
            status_tooltip = (
                f"Открылось {attempts_ok} раза из {attempts_total} на свежих соединениях: "
                "один раз сервер промолчал — так бывает при потере пакета"
            )
        else:
            status_tooltip = f"Открылось {attempts_ok} раза подряд на свежих соединениях"
        if (getattr(result, "raw_data", None) or {}).get("volume") == "unknown":
            status_tooltip += ". Объём не проверен: сайт отвечает коротко"
        raw = getattr(result, "raw_data", None) or {}
        if raw.get("window_addresses"):
            # Малое окно замедляет эти адреса само: это свойство приёма, а не сервера.
            status_tooltip += (
                ". С малым окном приёма (wssize) для " + ", ".join(map(str, raw["window_addresses"]))
                + ": окно приёма там не больше 128 КБ — при пинге 20 мс это около 50 Мбит/с"
            )
        elif raw.get("transfer"):
            status_tooltip += ". Сервер отвечает медленно или с паузами — от стратегии это не зависит"
    elif verdict == "unstable":
        status_tooltip = f"Открылось не каждый раз ({attempts_ok} из {attempts_total}): {error_text}"
    else:
        status_tooltip = error_text or status_text

    time_ms = float(getattr(result, "time_ms", 0) or 0)
    time_text = f"{time_ms:.0f}" if time_ms > 0 else "—"
    forced = bool((getattr(result, "raw_data", None) or {}).get("forced"))
    can_apply = bool(result.success) and bool(getattr(result, "apply_lines", ())) and not forced

    return StrategyScanResultPresentation(
        number_text=str(int(row_number)),
        strategy_name=result.strategy_name,
        strategy_tooltip="".join(tip_parts),
        status_text=status_text,
        status_tone=status_tone,
        status_tooltip=status_tooltip,
        time_text=time_text,
        can_apply=can_apply,
        stored_row={
            "id": getattr(result, "strategy_id", ""),
            "name": result.strategy_name,
            "args": result.strategy_args,
            "success": bool(result.success),
            "verdict": verdict,
            "time_ms": time_ms,
        },
    )


def count_resumable_strategies(*, target: str, scan_protocol: str, udp_games_scope: str, now: float | None = None) -> int:
    """Сколько стратегий для этой цели уже проверено и не сработало (за память подбора).

    Больше нуля — подбор может продолжить с непроверенных, и окно спросит,
    продолжать или начать заново. Ошибка чтения истории — считаем, что нечего.
    """
    import time

    from blockcheck.strategy_search.history import count_recent_failures, history_key

    key = history_key(scan_protocol, target, udp_games_scope)
    if not key:
        return 0
    try:
        return count_recent_failures(key, now=time.time() if now is None else float(now))
    except Exception:
        return 0


def plan_scan_start(
    *,
    raw_target_input: str,
    scan_protocol: str,
    udp_games_scope: str,
    mode: str,
    starting_status_text: str,
) -> StrategyScanStartPlan:
    target = normalize_target_input(raw_target_input, scan_protocol)
    if not target:
        target = default_target_for_protocol(scan_protocol)
    return StrategyScanStartPlan(
        target=target,
        scan_protocol=scan_protocol,
        udp_games_scope=udp_games_scope,
        mode=mode,
        status_text=starting_status_text,
    )


def finalize_scan_report(
    report,
    *,
    scan_protocol: str,
    result_rows: list[dict],
) -> StrategyScanFinishPlan:
    working = count_working_results(result_rows)

    if report is None:
        return StrategyScanFinishPlan(
            total_available=0,
            working_count=working,
            total_count=len(result_rows),
            cancelled=False,
            baseline_accessible=False,
            status_text="Ошибка подбора",
            support_status_code="ready_after_error",
        )

    total_available = max(0, int(getattr(report, "total_available", 0) or 0))
    total_count = int(report.total_tested)
    elapsed = report.elapsed_seconds
    fatal_error = str(getattr(report, "fatal_error", "") or "")

    if fatal_error:
        status_text = f"Остановлено. Проверено: {total_count}, надёжно работают: {working} ({elapsed:.0f} с)"
    elif report.cancelled:
        status_text = f"Отменено. Проверено: {total_count}, надёжно работают: {working} ({elapsed:.0f} с)"
    else:
        status_text = f"Готово. Проверено: {total_count}, надёжно работают: {working} ({elapsed:.0f} с)"

    outcome = build_panel_outcome(report, result_rows)
    return StrategyScanFinishPlan(
        total_available=total_available,
        working_count=working,
        total_count=total_count,
        cancelled=bool(report.cancelled),
        baseline_accessible=bool(report.baseline_accessible),
        status_text=status_text,
        support_status_code="ready_after_error" if fatal_error else "ready",
        fatal_error=fatal_error,
        outcome=outcome,
    )


def _plural(count: int, one: str, few: str, many: str) -> str:
    """1 стратегия, 2 стратегии, 5 стратегий (с учётом 11–14)."""
    tail = count % 100
    if 11 <= tail <= 14:
        return many
    last = count % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


_STOP_TITLES = {
    "no_internet": "Интернет куда-то убежал",
    "address_block": "Тут стратегии бессильны",
    "dns_stub": "Провайдер подменяет адрес сайта",
    "unresolved": "Не удалось узнать адрес цели",
    "winws": "winws2 не запускается",
    "network_lost": "Интернет пропал посреди подбора",
}


def best_working_index(result_rows: list[dict]) -> int:
    """Какую из надёжных стратегий применять: самую быструю по медиане попыток. -1 — надёжных нет."""
    working = [(float(row.get("time_ms") or 1e9), index) for index, row in enumerate(result_rows) if row.get("success")]
    return min(working)[1] if working else -1


def build_panel_outcome(report, result_rows: list[dict]) -> StrategyScanPanelOutcome:
    """Что показать в панели итога: заголовок с улыбкой, точное объяснение, лучшая стратегия."""
    target = str(getattr(report, "target", "") or "")
    tested = int(getattr(report, "total_tested", 0) or 0)
    available = int(getattr(report, "total_available", 0) or 0)
    working = [
        (index, row)
        for index, row in enumerate(result_rows)
        if row.get("success")
    ]
    fatal_error = str(getattr(report, "fatal_error", "") or "")
    stop_kind = str(getattr(report, "stop_kind", "") or "")

    if working and getattr(report, "baseline_accessible", False):
        # Цель открывалась и без обхода: «сработали» все, потому что сайт и так
        # открыт. Праздновать и применять тут нечего.
        return StrategyScanPanelOutcome(
            kind="open",
            title="Сайт открывается и без обхода — стратегии тут ни при чём",
            detail=(
                f"Проверено для сведения: {len(working)} {_plural(len(working), 'стратегия открыла', 'стратегии открыли', 'стратегий открыли')} "
                f"{target}, но он открывается и без них. Применять их незачем. Если в браузере сайт всё равно "
                "не грузится — проверьте DNS, прокси или сам браузер."
            ),
        )
    if working:
        best_index = best_working_index(result_rows)
        best = result_rows[best_index]
        count = len(working)
        noun = _plural(count, "надёжная стратегия", "надёжные стратегии", "надёжных стратегий")
        best_time = float(best.get("time_ms") or 0)
        best_text = f"Лучшая — «{best.get('name', '')}»" + (f", ответ за {best_time:.0f} мс" if best_time > 0 else "")
        detail = (
            "Каждая открыла сайт три раза на свежих соединениях и выдержала объём, а без обхода он закрыт. "
            "Нажмите «Применить лучшую» — стратегия запишется в выбранный пресет, и программа сама "
            "проверит сайт уже через него."
        )
        verb = "Нашлась" if count % 10 == 1 and count % 100 != 11 else "Нашлось"
        return StrategyScanPanelOutcome(
            kind="found",
            title=f"Ура! {verb} {count} {noun}",
            detail=detail,
            best_text=best_text,
            best_index=best_index,
            celebrate=True,
        )
    if fatal_error:
        return StrategyScanPanelOutcome(
            kind=stop_kind or "error",
            title=_STOP_TITLES.get(stop_kind, "Подбор остановлен"),
            detail=fatal_error,
        )
    if getattr(report, "baseline_accessible", False):
        return StrategyScanPanelOutcome(
            kind="open",
            title="А сайт-то и так открывается 🙂",
            detail=(
                f"Без обхода {target} открывается — подбирать нечего. Если в браузере он всё равно "
                "не грузится, дело не в блокировке: проверьте DNS, прокси или сам браузер."
            ),
        )
    if getattr(report, "cancelled", False):
        return StrategyScanPanelOutcome(
            kind="cancelled",
            title="Подбор остановлен",
            detail=(
                f"Проверено стратегий: {tested}. При следующем запуске можно будет выбрать: "
                "продолжить с непроверенных или начать заново."
            ),
        )
    if available and tested >= available:
        return StrategyScanPanelOutcome(
            kind="not_found",
            title="Ни одна стратегия не открыла сайт",
            detail=(
                f"Проверены все {available}. Так бывает, когда сайт закрыт по адресу или сам не пускает "
                "из России: тогда помогает не стратегия, а «Редактор hosts», свой DNS на странице "
                "«Настройка DNS» или VPN. Проверьте сайт на вкладке «Проверка сайтов» — там будет "
                "видно, как именно его закрывают."
            ),
        )
    return StrategyScanPanelOutcome(
        kind="not_found",
        title="Пока ни одна не подошла",
        detail=(
            f"Проверено {tested} из {available}. Запустите подбор ещё раз — проверятся следующие, "
            "а провалившиеся уйдут в конец очереди, — или выберите «Все стратегии». Если не поможет "
            "и полный перебор, сайт, скорее всего, закрыт по адресу: тогда нужны hosts, свой DNS или VPN."
        ),
    )
