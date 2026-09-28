from __future__ import annotations

from pathlib import Path

from blockcheck.strategy_scan_state import (
    StrategyScanPanelOutcome,
    StrategyApplyResult,
    StrategyScanFinishPlan,
    StrategyScanInteractionPlan,
    StrategyScanLanguagePlan,
    StrategyScanLogExpandPlan,
    StrategyScanNotificationPlan,
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
    is_udp_games = scan_protocol == "udp_games"
    show_target_controls = scan_protocol != "udp_games"

    if scan_protocol in {"stun_voice", "udp_games"} and current and ":" not in current and not current.upper().startswith("STUN:"):
        current = ""
    # Обратный переход: STUN-сервер из режима звонков/игр — не сайт.
    lowered = current.strip().lower()
    if scan_protocol == "tcp_https" and (":" in lowered or lowered.startswith("stun")):
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
    )


def build_udp_scope_hint_plan(
    *,
    scan_protocol: str,
    udp_games_scope: str,
    scope_all_label: str,
    scope_games_only_label: str,
) -> StrategyScanUdpHintPlan:
    if scan_protocol != "udp_games":
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
    options = load_quick_domains() if scan_protocol == "tcp_https" else load_quick_stun_targets()
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
    ("page.strategy_scan.protocol_tcp", "Сайты и приложения", "tcp_https"),
    ("page.strategy_scan.protocol_stun", "Голосовые звонки", "stun_voice"),
    ("page.strategy_scan.protocol_games", "Онлайн-игры", "udp_games"),
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
    ("page.strategy_scan.mode_quick.hint", "≈ 1–3 минуты"),
    ("page.strategy_scan.mode_standard.hint", "≈ 3–7 минут"),
    ("page.strategy_scan.mode_full.hint", "долго — самое время для чая ☕"),
)


def mode_hint_text(index: int, *, language: str | None = None) -> str:
    if not 0 <= index < len(MODE_HINTS):
        return ""
    key, default = MODE_HINTS[index]
    return tr_catalog(key, language=language, default=default)


def build_log_expand_plan(*, expanded: bool, language: str) -> StrategyScanLogExpandPlan:
    """Лог раскрывается внутри своей карточки, остальная вкладка остаётся на месте."""
    return StrategyScanLogExpandPlan(
        control_visible=True,
        warning_visible=True,
        results_visible=True,
        log_min_height=200,
        log_max_height=360,
        button_text=_log_button_text(expanded, language),
    )


def _log_button_text(expanded: bool, language: str | None) -> str:
    if expanded:
        return tr_catalog("page.strategy_scan.collapse_log", language=language, default="Скрыть подробный лог")
    return tr_catalog("page.strategy_scan.expand_log", language=language, default="Подробный лог")


def build_language_plan(*, language: str, log_expanded: bool) -> StrategyScanLanguagePlan:
    def _tr(key: str, default: str) -> str:
        return tr_catalog(key, language=language, default=default)

    return StrategyScanLanguagePlan(
        log_caption=_tr("page.strategy_scan.log", "Подробный лог"),
        expand_log_text=_log_button_text(log_expanded, language),
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
    )


def build_apply_success_plan(result: StrategyApplyResult) -> StrategyScanUiMessagePlan:
    operation = str(getattr(result, "operation", "") or "").strip().lower()
    if operation == "updated":
        title_default = "Стратегия обновлена"
        body_text = f"{result.strategy_name} заменена в существующем profile: {result.applied_profile}"
    else:
        title_default = "Стратегия применена"
        body_text = f"{result.strategy_name} применена к profile: {result.applied_profile}"
    blob_warnings = tuple(getattr(result, "blob_warnings", ()) or ())
    if blob_warnings:
        body_text = "\n".join((body_text, *blob_warnings))
    return StrategyScanUiMessagePlan(
        kind="warning" if blob_warnings else "success",
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
        status_tooltip = f"Открылось {attempts_ok} раза подряд на свежих соединениях"
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
    working = sum(1 for row in result_rows if row.get("success"))
    baseline_variant = "stun" if scan_protocol in {"stun_voice", "udp_games"} else "tcp"

    if report is None:
        return StrategyScanFinishPlan(
            total_available=0,
            working_count=working,
            total_count=len(result_rows),
            cancelled=False,
            baseline_accessible=False,
            status_text="Ошибка подбора",
            log_message="ERROR: Strategy scan execution failed",
            support_status_code="ready_after_error",
            notification_kind="none",
            baseline_variant=baseline_variant,
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

    if fatal_error or (report.cancelled and total_count == 0):
        notification_kind = "none"
    elif report.baseline_accessible:
        notification_kind = "baseline_accessible"
    elif report.cancelled:
        notification_kind = "none"
    elif working > 0:
        notification_kind = "found"
    else:
        notification_kind = "not_found"

    outcome = build_panel_outcome(report, result_rows)
    return StrategyScanFinishPlan(
        total_available=total_available,
        working_count=working,
        total_count=total_count,
        cancelled=bool(report.cancelled),
        baseline_accessible=bool(report.baseline_accessible),
        status_text=status_text,
        log_message=f"\n{status_text}",
        support_status_code="ready_after_error" if fatal_error else "ready",
        notification_kind=notification_kind,
        baseline_variant=baseline_variant,
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
        best_index, best = min(working, key=lambda item: float(item[1].get("time_ms") or 1e9))
        count = len(working)
        noun = _plural(count, "надёжная стратегия", "надёжные стратегии", "надёжных стратегий")
        best_time = float(best.get("time_ms") or 0)
        best_text = f"Лучшая — «{best.get('name', '')}»" + (f", ответ за {best_time:.0f} мс" if best_time > 0 else "")
        detail = (
            "Каждая открыла сайт три раза подряд, а без обхода он закрыт. "
            "Нажмите «Применить лучшую» — стратегия запишется в выбранный пресет."
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
            detail=f"Проверено стратегий: {tested}. Можно продолжить позже — повторный запуск начнёт с непроверенных.",
        )
    return StrategyScanPanelOutcome(
        kind="not_found",
        title="Пока ни одна не подошла",
        detail=(
            f"Проверено {tested} из {available}. Запустите подбор ещё раз — проверятся следующие стратегии, "
            "— или выберите «Все стратегии». Провалившиеся уйдут в конец очереди."
        ),
    )


def build_finish_notification_plan(finish_plan: StrategyScanFinishPlan, *, scan_protocol: str) -> StrategyScanNotificationPlan:
    if finish_plan.notification_kind == "baseline_accessible":
        if scan_protocol == "udp_games":
            title_default = "UDP уже доступен"
        elif finish_plan.baseline_variant == "stun":
            title_default = "STUN уже доступен"
        else:
            title_default = "Домен уже доступен"

        if finish_plan.baseline_variant == "stun":
            return StrategyScanNotificationPlan(
                kind="warning",
                title_key="page.strategy_scan.baseline_ok_title_stun",
                title_default=title_default,
                body_key="page.strategy_scan.baseline_ok_text_stun",
                body_default="Цель отвечает и без обхода: результаты подбора — только для сведения",
                body_text="",
            )

        return StrategyScanNotificationPlan(
            kind="warning",
            title_key="page.strategy_scan.baseline_ok_title",
            title_default=title_default,
            body_key="page.strategy_scan.baseline_ok_text",
            body_default="Сайт открывается и без обхода: результаты подбора — только для сведения",
            body_text="",
        )

    if finish_plan.notification_kind == "found":
        return StrategyScanNotificationPlan(
            kind="success",
            title_key="page.strategy_scan.found",
            title_default="Найдены надёжные стратегии",
            body_key="",
            body_default="",
            body_text=f"{finish_plan.working_count} из {finish_plan.total_count}",
        )

    if finish_plan.notification_kind == "not_found":
        return StrategyScanNotificationPlan(
            kind="warning",
            title_key="page.strategy_scan.not_found",
            title_default="Рабочих стратегий не найдено",
            body_key="page.strategy_scan.try_full",
            body_default="Запустите подбор ещё раз: проверятся следующие стратегии, или выберите «Все стратегии»",
            body_text="",
        )

    return StrategyScanNotificationPlan(
        kind="none",
        title_key="",
        title_default="",
        body_key="",
        body_default="",
        body_text="",
    )
