"""Runtime/settings helper слой Telegram Proxy page."""

from __future__ import annotations

import telegram_proxy.ui.page_runtime as telegram_proxy_page_runtime
from qfluentwidgets import FluentIcon
from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_widgets import set_tooltip
from telegram_proxy.ui.build import update_telegram_proxy_pivot_accessibility
from telegram_proxy.ui.text_plan import TELEGRAM_PROXY_SETTINGS_TEXT


def refresh_pivot_texts(pivot) -> None:
    try:
        pivot.setItemText("settings", "Настройки")
        pivot.setItemText("logs", "Логи")
        pivot.setItemText("diag", "Диагностика")
        update_telegram_proxy_pivot_accessibility(pivot)
    except Exception:
        pass


def refresh_status_texts(*, manager, status_label, btn_toggle, restarting: bool, starting: bool) -> None:
    running = bool(manager.is_running)
    plan = telegram_proxy_page_runtime.build_status_plan(
        running=running,
        restarting=bool(restarting),
        starting=bool(starting),
        host=manager.host,
        port=manager.port,
    )

    if status_label is not None:
        status_label.setText(plan.status_text)
        set_state_text(status_label, f"Статус Telegram Proxy: {plan.status_text}")

    if btn_toggle is not None:
        btn_toggle.setText(plan.toggle_text)
        btn_toggle.setIcon(FluentIcon.CANCEL if "Останов" in plan.toggle_text else FluentIcon.PLAY)
        btn_toggle.setMinimumWidth(140)
        if "Останов" in plan.toggle_text:
            set_control_accessibility(
                btn_toggle,
                name="Остановить Telegram Proxy",
                description="Останавливает локальный Telegram Proxy.",
            )
        else:
            set_control_accessibility(
                btn_toggle,
                name="Запустить Telegram Proxy",
                description="Запускает локальный Telegram Proxy.",
            )


def apply_upstream_preset_ui(
    *,
    upstream_toggle,
    upstream_catalog,
    upstream_preset_row,
    upstream_manual_rows,
    mtproxy_action_card,
    upstream_mode_toggle,
    upstream_udp_toggle,
    index: int,
) -> str:
    """Показывает строки внешнего прокси под выбранный сервер.

    Возвращает id выбранного MTProxy-сервера или пустую строку.
    """
    upstream_enabled = upstream_toggle.isChecked()
    preset = upstream_catalog.preset_at(index)
    is_manual = bool(preset is not None and upstream_catalog.is_manual(index))
    is_mtproxy = bool(preset is not None and upstream_catalog.is_mtproxy(index))

    # Строка «Сервер» видна всегда при включённом внешнем прокси: даже без
    # готовых серверов в ней выбран ручной ввод и описание объясняет почему.
    upstream_preset_row.setVisible(upstream_enabled)
    for row in upstream_manual_rows:
        row.setVisible(upstream_enabled and is_manual)
    mtproxy_action_card.setVisible(upstream_enabled and is_mtproxy)
    upstream_mode_toggle.setVisible(upstream_enabled)
    upstream_udp_toggle.setVisible(upstream_enabled)

    if preset is not None and is_mtproxy:
        return str(preset.get("id") or "").strip()
    return ""


def format_upstream_runtime_state(state) -> str:
    selected = str(getattr(state, "selected_name", "") or "").strip() or "не выбран"
    active = str(getattr(state, "active_name", "") or "").strip()
    mode = str(getattr(state, "state", "unavailable") or "unavailable")
    if mode == "primary":
        text = f"Выбрано: {selected} · Сейчас используется: {active or selected}"
    elif mode == "fallback":
        text = f"Выбрано: {selected} · Сейчас используется: {active or 'резервный сервер'} (резерв)"
    elif mode == "checking_primary":
        if active:
            reserve = " (резерв)" if active != selected else ""
            text = (
                f"Выбрано: {selected} · Сейчас используется: {active}{reserve} "
                "· Проверяем основной сервер"
            )
        else:
            text = f"Выбрано: {selected} · Сейчас: проверяем основной сервер"
    else:
        text = f"Выбрано: {selected} · Сейчас: сервер временно недоступен"
    queued = max(0, int(getattr(state, "queued_connections", 0) or 0))
    if queued:
        text += f" · В очереди: {queued}"
    return text


def apply_upstream_runtime_state(row, state, default_description: str) -> None:
    """Пишет в описание строки «Сервер» фактический SOCKS-сервер или обычный текст."""
    if row is None:
        return
    title = TELEGRAM_PROXY_SETTINGS_TEXT.upstream_preset_title
    if state is None:
        row.set_texts(title, str(default_description or ""))
        row.setToolTip("")
        return
    text = format_upstream_runtime_state(state)
    row.set_texts(title, text)
    reason = str(getattr(state, "reason", "") or "").strip()
    row.setToolTip(reason or "Фактический SOCKS-сервер, который сейчас использует Telegram Proxy.")


def apply_telegram_hosts_row(row, button, plan) -> None:
    """Показывает состояние записей Telegram в hosts и нужную кнопку."""
    if row is not None:
        # Состояние и пояснение — две строки описания; в узком окне конец
        # строки обрезается, поэтому полный текст дублируется подсказкой.
        row.set_texts(TELEGRAM_PROXY_SETTINGS_TEXT.hosts_title, plan.description)
        set_tooltip(row, plan.description)
    if button is None:
        return
    button.setText(plan.button_text)
    button.setIcon(FluentIcon.DELETE if plan.button_action == "remove" else FluentIcon.ADD)
    button.setEnabled(bool(plan.button_enabled))
    set_control_accessibility(
        button,
        name=plan.button_accessible_name,
        description=plan.description,
    )
    set_state_text(button, plan.button_accessible_name)


def apply_ui_texts(
    *,
    refresh_pivot_texts_callback,
    refresh_status_texts_callback,
    settings_card,
    setup_title_label,
    host_port_row,
    mtproxy_secret_row,
    fake_tls_domain_row,
    advanced_nav_row,
    diag_desc_label,
    setup_open_btn,
    setup_copy_btn,
    fake_tls_nginx_btn,
    btn_copy_logs,
    btn_open_log_file,
    btn_clear_logs,
    btn_copy_diag,
    btn_run_diag,
    host_edit,
    mtproxy_secret_edit,
    fake_tls_domain_edit,
    log_edit,
    diag_edit,
    auto_deeplink_toggle,
    proxy_mode_row,
    proxy_protocol_toggle,
) -> None:
    try:
        text = TELEGRAM_PROXY_SETTINGS_TEXT
        refresh_pivot_texts_callback()
        refresh_status_texts_callback()

        title_label = getattr(settings_card, "titleLabel", None)
        if title_label is not None:
            title_label.setText(text.settings_title)
        if setup_title_label is not None:
            setup_title_label.setText(text.setup_title)
            set_tooltip(setup_title_label, text.setup_description)
        if host_port_row is not None:
            host_port_row.set_texts(text.host_port_title, text.host_port_description)
        if mtproxy_secret_row is not None:
            mtproxy_secret_row.set_texts(text.mtproxy_secret_title, text.mtproxy_secret_description)
        if fake_tls_domain_row is not None:
            fake_tls_domain_row.set_texts(text.fake_tls_domain_title, text.fake_tls_domain_description)
        if advanced_nav_row is not None:
            advanced_nav_row.set_texts(text.advanced_nav_title, text.advanced_nav_description)
        if diag_desc_label is not None:
            diag_desc_label.setText(text.diag_description)

        if setup_open_btn is not None:
            setup_open_btn.setText("Открыть")
            set_tooltip(
                setup_open_btn,
                "Открыть ссылку для автоматической настройки прокси внутри Telegram."
            )
        if setup_copy_btn is not None:
            setup_copy_btn.setText("Копировать")
            set_tooltip(
                setup_copy_btn,
                "Сохранить ссылку в буфер обмена, если Telegram не открылся автоматически."
            )
        if fake_tls_nginx_btn is not None:
            fake_tls_nginx_btn.setText("Nginx")
            set_tooltip(fake_tls_nginx_btn, "Скопировать stream-конфиг Nginx для MTProxy Fake TLS.")
        if btn_copy_logs is not None:
            btn_copy_logs.setText("Копировать все")
        if btn_open_log_file is not None:
            btn_open_log_file.setText("Открыть файл лога")
        if btn_clear_logs is not None:
            btn_clear_logs.setText("Очистить")
        if btn_copy_diag is not None:
            btn_copy_diag.setText("Копировать результат")
        if btn_run_diag is not None and btn_run_diag.isEnabled():
            btn_run_diag.setText("Запустить диагностику")

        if host_edit is not None:
            host_edit.setPlaceholderText("127.0.0.1")
        if mtproxy_secret_edit is not None:
            mtproxy_secret_edit.setPlaceholderText("32 символа: 0-9 и a-f")
        if fake_tls_domain_edit is not None:
            fake_tls_domain_edit.setPlaceholderText("front.example.com")
        if log_edit is not None:
            log_edit.setPlaceholderText("Лог подключений появится здесь...")
        if diag_edit is not None:
            diag_edit.setPlaceholderText("Нажмите 'Запустить диагностику'...")

        if auto_deeplink_toggle is not None:
            auto_deeplink_toggle.set_texts(
                text.auto_setup_title,
                text.auto_setup_description,
            )
        if proxy_mode_row is not None:
            proxy_mode_row.set_texts(
                text.proxy_mode_title,
                text.proxy_mode_description,
            )
        if proxy_protocol_toggle is not None:
            proxy_protocol_toggle.set_texts(
                text.proxy_protocol_title,
                text.proxy_protocol_description,
            )
    except Exception:
        pass
