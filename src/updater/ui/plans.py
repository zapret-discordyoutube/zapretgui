from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class UpdateStatusCardPlan:
    title: str
    subtitle: str
    button_text: str


@dataclass(slots=True)
class ServerRowPlan:
    server_text: str
    server_accent: bool
    status_text: str
    status_color: tuple[int, int, int]
    time_text: str
    extra_text: str


@dataclass(slots=True)
class UpdateStatusTransitionPlan:
    is_checking: bool
    state: str
    state_version: str
    state_source: str
    state_message: str
    state_elapsed: float
    icon_mode: str
    loading_mode: str
    stop_loading_text: str
    check_enabled: bool | None


@dataclass(slots=True)
class ChangelogProgressPlan:
    mode: str
    show_progress_bar: bool
    hide_indeterminate: bool
    progress_value: int
    progress_label_text: str
    version_text: str
    speed_label_text: str
    eta_label_text: str
    download_percent: int
    download_done_bytes: int
    download_total_bytes: int
    last_bytes: int
    last_speed_time: float
    last_speed_bytes: int
    smoothed_speed: float
    download_speed_kb: float | None
    download_eta_seconds: float | None


def update_flow_text(language: str, key: str, default: str) -> str:
    """Тексты окна обновления: ключи ``update_dialog.*`` в ``app/ui_texts.py``."""
    return _tr(language, f"update_dialog.{key}", default)


def _tr(language: str, key: str, default: str) -> str:
    from app.ui_texts import tr as tr_catalog

    return tr_catalog(key, language=language, default=default)

_URL_RE = None
_MONTHS_RU = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_MONTHS_EN = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _linkify(escaped: str, accent_hex: str) -> str:
    """Ссылки в уже экранированном тексте делает кликабельными."""
    import re

    global _URL_RE
    if _URL_RE is None:
        _URL_RE = re.compile(r"(https?://[^\s<>\"']+)")

    def replace_url(match):
        url = match.group(1)
        tail = ""
        while url and url[-1] in ".,;:!?)":
            tail = url[-1] + tail
            url = url[:-1]
        return f'<a href="{url}" style="color: {accent_hex}; text-decoration: none;">{url}</a>{tail}'

    return _URL_RE.sub(replace_url, escaped)


def versions_word(count: int, language: str) -> str:
    """«2 версии», «5 версий», «21 версию» — по правилам русского языка."""
    n = abs(int(count))
    if str(language or "").lower().startswith("en"):
        return "version" if n == 1 else "versions"
    if n % 10 == 1 and n % 100 != 11:
        return "версию"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "версии"
    return "версий"


def format_release_date(published_at: str, language: str) -> str:
    """``2026-09-29T21:40:30+03:00`` → «29 сентября 2026». Пусто, если не разобрать."""
    text = str(published_at or "").strip()
    if len(text) < 10:
        return ""
    try:
        year, month, day = int(text[0:4]), int(text[5:7]), int(text[8:10])
    except ValueError:
        return ""
    if not 1 <= month <= 12:
        return ""
    if str(language or "").lower().startswith("en"):
        return f"{_MONTHS_EN[month - 1]} {day}, {year}"
    return f"{day} {_MONTHS_RU[month - 1]} {year}"


def release_notes_body_html(notes: str, *, accent_hex: str) -> str:
    """Текст одного выпуска: строки «- …» / «• …» — список, «# …» — подзаголовок."""
    import html

    blocks: list[str] = []
    items: list[str] = []

    def flush_items() -> None:
        if items:
            blocks.append("<ul style='margin: 2px 0 6px 0;'>" + "".join(items) + "</ul>")
            items.clear()

    for raw_line in str(notes or "").replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            flush_items()
            continue
        bullet = None
        for marker in ("- ", "* ", "• ", "— ", "– "):
            if line.startswith(marker):
                bullet = line[len(marker):].strip()
                break
        if bullet is not None:
            items.append(f"<li style='margin-bottom: 3px;'>{_linkify(html.escape(bullet), accent_hex)}</li>")
            continue
        flush_items()
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            blocks.append(f"<p style='margin: 8px 0 2px 0;'><b>{_linkify(html.escape(heading), accent_hex)}</b></p>")
        else:
            blocks.append(f"<p style='margin: 2px 0 4px 0;'>{_linkify(html.escape(line), accent_hex)}</p>")
    flush_items()
    return "".join(blocks)


def release_history_html(
    history,
    *,
    accent_hex: str,
    muted_hex: str,
    language: str,
    empty_text: str,
    badge_fg_hex: str = "#000000",
    new_badge_text: str = "новое",
    earlier_text: str = "Ранее",
    new_badge_image: tuple[str, int, int] | None = None,
) -> str:
    """Выпуски для окна обновления и «Что нового»: версия, дата и текст.

    Новые для пользователя (``is_new``; без флага — новые) идут сверху с
    пометкой, предыдущие — под заголовком «Ранее», приглушённо. Если все
    выпуски новые, пометок и заголовка нет.
    """
    import html

    entries = [entry for entry in history or () if isinstance(entry, dict)]
    fresh = [entry for entry in entries if entry.get("is_new", True)]
    earlier = [entry for entry in entries if not entry.get("is_new", True)]
    mixed = bool(fresh) and bool(earlier)

    def block(entry: dict, *, is_new: bool) -> str:
        version = html.escape(str(entry.get("version") or ""))
        date = html.escape(format_release_date(str(entry.get("published_at") or ""), language))
        if is_new:
            header = f"<span style='font-size: 15pt; font-weight: 600; color: {accent_hex};'>v{version}</span>"
        else:
            header = f"<span style='font-size: 13pt; font-weight: 600; color: {muted_hex};'>v{version}</span>"
        if is_new and mixed:
            if new_badge_image is not None:
                # Скруглённая метка — картинка: движок текста Qt не умеет
                # скругления и отступы у надписей.
                src, width, height = new_badge_image
                header += (
                    f"&nbsp;&nbsp;<img src='{html.escape(src)}' width='{int(width)}' height='{int(height)}' "
                    f"style='vertical-align: middle;' alt='{html.escape(new_badge_text)}'>"
                )
            else:
                header += (
                    f"&nbsp;&nbsp;<span style='color: {accent_hex}; font-size: 9pt; font-weight: 600;'>"
                    f"{html.escape(new_badge_text)}</span>"
                )
        if date:
            header += f"<span style='color: {muted_hex};'>&nbsp;&nbsp;·&nbsp;&nbsp;{date}</span>"
        body = release_notes_body_html(str(entry.get("notes") or ""), accent_hex=accent_hex if is_new else muted_hex)
        if not body:
            body = f"<p style='color: {muted_hex};'>{html.escape(empty_text)}</p>"
        tone = "" if is_new else f" color: {muted_hex};"
        return f"<div style='margin-bottom: 18px;{tone}'><p style='margin: 0 0 6px 0;'>{header}</p>{body}</div>"

    parts = [block(entry, is_new=True) for entry in fresh]
    if earlier:
        if fresh:
            parts.append(
                f"<p style='margin: 10px 0 12px 0; font-size: 11pt; font-weight: 600; color: {muted_hex};'>"
                f"{html.escape(earlier_text)}</p>"
            )
        parts.extend(block(entry, is_new=not fresh) for entry in earlier)
    return "".join(parts)


def count_new_versions(history) -> int:
    """Сколько версий в списке новые для пользователя (без флага — новые)."""
    return sum(1 for entry in history or () if isinstance(entry, dict) and entry.get("is_new", True))


def build_update_status_card_plan(
    *,
    state: str,
    version: str,
    source: str,
    message: str,
    elapsed: float,
    app_version: str,
    language: str,
) -> UpdateStatusCardPlan:
    from app.ui_texts import tr as tr_catalog

    def tr(key: str, default: str) -> str:
        return tr_catalog(key, language=language, default=default)

    if state == "checking":
        return UpdateStatusCardPlan(
            title=tr("page.servers.update.title.checking", "Проверка обновлений..."),
            subtitle=tr("page.servers.update.subtitle.checking", "Подождите, идёт проверка серверов"),
            button_text=tr("page.servers.update.button.check", "Проверить обновления"),
        )
    if state == "available":
        return UpdateStatusCardPlan(
            title=tr("page.servers.update.title.available_template", "Доступно обновление v{version}").format(version=version),
            subtitle=tr(
                "page.servers.update.subtitle.available",
                "Нажмите «Подробнее», чтобы посмотреть изменения и установить",
            ),
            button_text=tr("page.servers.update.button.recheck", "ПРОВЕРИТЬ СНОВА"),
        )
    if state == "up_to_date":
        return UpdateStatusCardPlan(
            title=tr("page.servers.update.title.none", "Обновлений нет"),
            subtitle=tr(
                "page.servers.update.subtitle.latest_template",
                "Установлена последняя версия {version}",
            ).format(version=app_version),
            button_text=tr("page.servers.update.button.recheck", "ПРОВЕРИТЬ СНОВА"),
        )
    if state == "error":
        return UpdateStatusCardPlan(
            title=tr("page.servers.update.title.error", "Ошибка проверки"),
            subtitle=str(message or "")[:60],
            button_text=tr("page.servers.update.button.retry", "Повторить"),
        )
    if state == "found":
        return UpdateStatusCardPlan(
            title=tr("page.servers.update.title.found_template", "Найдено обновление v{version}").format(version=version),
            subtitle=tr("page.servers.update.subtitle.source_template", "Источник: {source}").format(source=source),
            button_text=tr("page.servers.update.button.recheck", "ПРОВЕРИТЬ СНОВА"),
        )
    if state == "downloading":
        return UpdateStatusCardPlan(
            title=tr(
                "page.servers.update.title.downloading_template",
                "Загрузка обновления v{version}",
            ).format(version=version),
            subtitle=str(message or "")
            or tr("page.servers.update.subtitle.downloading", "Загрузка идёт в фоне"),
            button_text=tr("page.servers.update.button.recheck", "ПРОВЕРИТЬ СНОВА"),
        )
    if state == "download_error":
        return UpdateStatusCardPlan(
            title=tr("page.servers.update.title.download_error", "Ошибка загрузки"),
            subtitle=tr("page.servers.update.subtitle.try_again", "Попробуйте снова"),
            button_text=tr("page.servers.update.button.recheck", "ПРОВЕРИТЬ СНОВА"),
        )
    if state == "deferred":
        return UpdateStatusCardPlan(
            title=tr("page.servers.update.title.deferred_template", "Обновление v{version} отложено").format(version=version),
            subtitle=tr("page.servers.update.subtitle.recheck_hint", "Нажмите для повторной проверки"),
            button_text=tr("page.servers.update.button.recheck", "ПРОВЕРИТЬ СНОВА"),
        )
    if state == "checked_ago":
        mins_ago = int(max(float(elapsed or 0.0), 0.0) // 60)
        secs_ago = int(max(float(elapsed or 0.0), 0.0) % 60)
        if mins_ago > 0:
            subtitle = tr(
                "page.servers.update.subtitle.checked_ago_min_sec_template",
                "Проверено {minutes}м {seconds}с назад",
            ).format(minutes=mins_ago, seconds=secs_ago)
        else:
            subtitle = tr(
                "page.servers.update.subtitle.checked_ago_sec_template",
                "Проверено {seconds}с назад",
            ).format(seconds=secs_ago)
        return UpdateStatusCardPlan(
            title=tr("page.servers.update.title.default", "Проверка обновлений"),
            subtitle=subtitle,
            button_text=tr("page.servers.update.button.recheck", "ПРОВЕРИТЬ СНОВА"),
        )
    if state == "auto_on":
        return UpdateStatusCardPlan(
            title=tr("page.servers.update.title.default", "Проверка обновлений"),
            subtitle=tr("page.servers.update.subtitle.auto_on", "Автообновление включено"),
            button_text=tr("page.servers.update.button.recheck", "ПРОВЕРИТЬ СНОВА"),
        )
    if state == "manual":
        return UpdateStatusCardPlan(
            title=tr("page.servers.update.title.default", "Проверка обновлений"),
            subtitle=tr("page.servers.update.subtitle.press_button", "Нажмите кнопку для проверки"),
            button_text=tr("page.servers.update.button.manual", "ПРОВЕРИТЬ ВРУЧНУЮ"),
        )
    return UpdateStatusCardPlan(
        title=tr("page.servers.update.title.default", "Проверка обновлений"),
        subtitle=tr(
            "page.servers.update.subtitle.default",
            "Нажмите для проверки доступных обновлений",
        ),
        button_text=tr("page.servers.update.button.check", "Проверить обновления"),
    )

def build_server_row_plan(
    *,
    row_server_name: str,
    status: dict,
    channel: str,
    language: str,
) -> ServerRowPlan:
    from app.ui_texts import tr as tr_catalog
    from updater.channel_utils import is_dev_update_channel

    def tr(key: str, default: str) -> str:
        return tr_catalog(key, language=language, default=default)

    server_accent = bool(status.get("is_current"))
    # Активный сервер помечает значок в ячейке (updater/ui/active_server_icon),
    # а не символ в тексте: название остаётся чистым.
    server_text = row_server_name

    state = status.get("status")
    if state == "online":
        status_text = tr("page.servers.table.status.online", "● Онлайн")
        status_color = (134, 194, 132)
    elif state == "blocked":
        status_text = tr("page.servers.table.status.blocked", "● Блок")
        status_color = (230, 180, 100)
    elif state == "skipped":
        status_text = tr("page.servers.table.status.waiting", "● Ожидание")
        status_color = (160, 160, 160)
    else:
        status_text = tr("page.servers.table.status.offline", "● Офлайн")
        status_color = (220, 130, 130)

    if status.get("response_time"):
        time_text = tr("page.servers.table.time.ms_template", "{ms}мс").format(
            ms=f"{status.get('response_time', 0) * 1000:.0f}"
        )
    else:
        time_text = tr("page.servers.table.time.empty", "—")

    if row_server_name == "Telegram Bot":
        if status.get("status") == "online":
            if is_dev_update_channel(channel):
                extra_text = tr("page.servers.table.versions.dev_template", "D: {version}").format(
                    version=status.get("dev_version", "—")
                )
            else:
                extra_text = tr("page.servers.table.versions.stable_template", "S: {version}").format(
                    version=status.get("stable_version", "—")
                )
        else:
            extra_text = str(status.get("error", ""))[:40]
    elif row_server_name == "Forgejo API":
        extra_text = (
            str(status.get("details") or tr("page.servers.status.api_available", "API доступен"))
            if status.get("status") == "online"
            else str(status.get("error", ""))[:40]
        )
    elif status.get("status") == "online":
        extra_text = tr("page.servers.table.versions.both_template", "S: {stable}, D: {dev}").format(
            stable=status.get("stable_version", "—"),
            dev=status.get("dev_version", "—"),
        )
    else:
        extra_text = str(status.get("error", ""))[:40]

    return ServerRowPlan(
        server_text=server_text,
        server_accent=server_accent,
        status_text=status_text,
        status_color=status_color,
        time_text=time_text,
        extra_text=extra_text,
    )

def build_update_status_transition_plan(
    *,
    target_state: str,
    language: str,
    version: str = "",
    source: str = "",
    message: str = "",
    elapsed: float = 0.0,
    found_update: bool | None = None,
) -> UpdateStatusTransitionPlan:
    tr = lambda key, default: _tr(language, key, default)
    state = str(target_state or "").strip().lower()

    if state == "checking":
        return UpdateStatusTransitionPlan(
            is_checking=True,
            state="checking",
            state_version="",
            state_source="",
            state_message="",
            state_elapsed=0.0,
            icon_mode="checking",
            loading_mode="start",
            stop_loading_text="",
            check_enabled=None,
        )

    if state == "result":
        resolved_state = "available" if bool(found_update) else "up_to_date"
        return UpdateStatusTransitionPlan(
            is_checking=False,
            state=resolved_state,
            state_version=str(version or ""),
            state_source="",
            state_message="",
            state_elapsed=0.0,
            icon_mode="idle",
            loading_mode="stop",
            stop_loading_text=tr("page.servers.update.button.recheck", "ПРОВЕРИТЬ СНОВА"),
            check_enabled=None,
        )

    if state == "error":
        return UpdateStatusTransitionPlan(
            is_checking=False,
            state="error",
            state_version="",
            state_source="",
            state_message=str(message or "")[:60],
            state_elapsed=0.0,
            icon_mode="error",
            loading_mode="stop",
            stop_loading_text=tr("page.servers.update.button.retry", "Повторить"),
            check_enabled=None,
        )

    if state == "found":
        return UpdateStatusTransitionPlan(
            is_checking=False,
            state="found",
            state_version=str(version or ""),
            state_source=str(source or ""),
            state_message="",
            state_elapsed=0.0,
            icon_mode="idle",
            loading_mode="stop",
            stop_loading_text=tr("page.servers.update.button.recheck", "ПРОВЕРИТЬ СНОВА"),
            check_enabled=True,
        )

    if state == "downloading":
        return UpdateStatusTransitionPlan(
            is_checking=False,
            state="downloading",
            state_version=str(version or ""),
            state_source="",
            state_message=str(message or ""),
            state_elapsed=0.0,
            icon_mode="checking",
            loading_mode="stop",
            stop_loading_text=tr("page.servers.update.button.recheck", "ПРОВЕРИТЬ СНОВА"),
            check_enabled=False,
        )

    if state == "download_error":
        return UpdateStatusTransitionPlan(
            is_checking=False,
            state="download_error",
            state_version="",
            state_source="",
            state_message="",
            state_elapsed=0.0,
            icon_mode="error",
            loading_mode="stop",
            stop_loading_text=tr("page.servers.update.button.recheck", "ПРОВЕРИТЬ СНОВА"),
            check_enabled=True,
        )

    if state == "deferred":
        return UpdateStatusTransitionPlan(
            is_checking=False,
            state="deferred",
            state_version=str(version or ""),
            state_source="",
            state_message="",
            state_elapsed=0.0,
            icon_mode="idle",
            loading_mode="stop",
            stop_loading_text=tr("page.servers.update.button.recheck", "ПРОВЕРИТЬ СНОВА"),
            check_enabled=True,
        )

    if state == "checked_ago":
        return UpdateStatusTransitionPlan(
            is_checking=False,
            state="checked_ago",
            state_version="",
            state_source="",
            state_message="",
            state_elapsed=float(elapsed or 0.0),
            icon_mode="idle",
            loading_mode="stop",
            stop_loading_text=tr("page.servers.update.button.recheck", "ПРОВЕРИТЬ СНОВА"),
            check_enabled=None,
        )

    if state == "auto_on":
        return UpdateStatusTransitionPlan(
            is_checking=False,
            state="auto_on",
            state_version="",
            state_source="",
            state_message="",
            state_elapsed=0.0,
            icon_mode="idle",
            loading_mode="stop",
            stop_loading_text=tr("page.servers.update.button.recheck", "ПРОВЕРИТЬ СНОВА"),
            check_enabled=None,
        )

    if state == "manual":
        return UpdateStatusTransitionPlan(
            is_checking=False,
            state="manual",
            state_version="",
            state_source="",
            state_message="",
            state_elapsed=0.0,
            icon_mode="idle",
            loading_mode="stop",
            stop_loading_text=tr("page.servers.update.button.manual", "ПРОВЕРИТЬ ВРУЧНУЮ"),
            check_enabled=None,
        )

    return UpdateStatusTransitionPlan(
        is_checking=False,
        state="idle",
        state_version="",
        state_source="",
        state_message="",
        state_elapsed=0.0,
        icon_mode="idle",
        loading_mode="stop",
        stop_loading_text=tr("page.servers.update.button.check", "Проверить обновления"),
        check_enabled=None,
    )

def build_changelog_progress_plan(
    *,
    percent: int,
    done_bytes: int,
    total_bytes: int,
    last_speed_time: float,
    last_speed_bytes: int,
    smoothed_speed: float,
    download_speed_kb: float | None = None,
    download_eta_seconds: float | None = None,
    language: str,
    now: float,
    progress_bar_visible: bool,
) -> ChangelogProgressPlan:
    tr = lambda key, default: _tr(language, key, default)

    hide_indeterminate = not progress_bar_visible and done_bytes > 0
    show_progress_bar = progress_bar_visible or done_bytes > 0
    progress_label_text = f"{int(percent)}%"
    done_mb = done_bytes / (1024 * 1024)
    total_mb = total_bytes / (1024 * 1024)
    version_text = tr(
        "page.servers.changelog.progress.downloaded_mb_template",
        "Загружено {done:.1f} / {total:.1f} МБ",
    ).format(done=done_mb, total=total_mb)

    next_last_speed_time = float(last_speed_time)
    next_last_speed_bytes = int(last_speed_bytes)
    next_smoothed_speed = float(smoothed_speed)
    next_download_speed_kb: float | None = download_speed_kb
    next_download_eta_seconds: float | None = download_eta_seconds
    speed_label_text = tr("page.servers.changelog.progress.speed_unknown", "Скорость: —")
    eta_label_text = tr("page.servers.changelog.progress.eta_unknown", "Осталось: —")

    if next_download_speed_kb is not None:
        if next_download_speed_kb > 1024:
            speed_label_text = tr(
                "page.servers.changelog.progress.speed_mb_template",
                "Скорость: {value:.1f} МБ/с",
            ).format(value=next_download_speed_kb / 1024)
        else:
            speed_label_text = tr(
                "page.servers.changelog.progress.speed_kb_template",
                "Скорость: {value:.0f} КБ/с",
            ).format(value=next_download_speed_kb)

    if next_download_eta_seconds is not None:
        if next_download_eta_seconds < 60:
            eta_label_text = tr(
                "page.servers.changelog.progress.eta_sec_template",
                "Осталось: {seconds} сек",
            ).format(seconds=int(next_download_eta_seconds))
        else:
            eta_label_text = tr(
                "page.servers.changelog.progress.eta_min_template",
                "Осталось: {minutes} мин",
            ).format(minutes=int(next_download_eta_seconds / 60))

    dt = float(now) - float(last_speed_time)
    if dt >= 1.0 and done_bytes > 0:
        delta_bytes = int(done_bytes) - int(last_speed_bytes)
        if delta_bytes <= 0:
            next_smoothed_speed = 0.0
            next_last_speed_time = float(now)
            next_last_speed_bytes = int(done_bytes)
            next_download_speed_kb = None
            next_download_eta_seconds = None
        else:
            instant_speed = delta_bytes / dt
            if next_smoothed_speed <= 0:
                next_smoothed_speed = instant_speed
            else:
                next_smoothed_speed = next_smoothed_speed * 0.4 + instant_speed * 0.6

            next_last_speed_time = float(now)
            next_last_speed_bytes = int(done_bytes)

            speed = next_smoothed_speed
            next_download_speed_kb = speed / 1024
            if next_download_speed_kb > 1024:
                speed_label_text = tr(
                    "page.servers.changelog.progress.speed_mb_template",
                    "Скорость: {value:.1f} МБ/с",
                ).format(value=next_download_speed_kb / 1024)
            else:
                speed_label_text = tr(
                    "page.servers.changelog.progress.speed_kb_template",
                    "Скорость: {value:.0f} КБ/с",
                ).format(value=next_download_speed_kb)

            if speed > 0:
                remaining = (total_bytes - done_bytes) / speed
                next_download_eta_seconds = remaining
                if remaining < 60:
                    eta_label_text = tr(
                        "page.servers.changelog.progress.eta_sec_template",
                        "Осталось: {seconds} сек",
                    ).format(seconds=int(remaining))
                else:
                    eta_label_text = tr(
                        "page.servers.changelog.progress.eta_min_template",
                        "Осталось: {minutes} мин",
                    ).format(minutes=int(remaining / 60))

    return ChangelogProgressPlan(
        mode="downloading",
        show_progress_bar=show_progress_bar,
        hide_indeterminate=hide_indeterminate,
        progress_value=int(percent),
        progress_label_text=progress_label_text,
        version_text=version_text,
        speed_label_text=speed_label_text,
        eta_label_text=eta_label_text,
        download_percent=int(percent),
        download_done_bytes=int(done_bytes),
        download_total_bytes=int(total_bytes),
        last_bytes=int(done_bytes),
        last_speed_time=next_last_speed_time,
        last_speed_bytes=next_last_speed_bytes,
        smoothed_speed=next_smoothed_speed,
        download_speed_kb=next_download_speed_kb,
        download_eta_seconds=next_download_eta_seconds,
    )

