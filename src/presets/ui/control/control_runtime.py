from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ControlRuntimeState:
    phase: str
    last_error: str


@dataclass(slots=True)
class ControlStatusPlan:
    phase: str
    title: str
    description: str
    dot_color: str
    pulsing: bool
    # Кнопка в сцене статуса — это выключатель. clickable=False, пока идёт остановка.
    clickable: bool
    # Что сделает нажатие на кнопку: имя для диктора и подсказка.
    action_name: str
    show_close: bool


@dataclass(slots=True)
class ControlConfirmationDialogPlan:
    title: str
    content: str
    revert_checked: bool


@dataclass(slots=True)
class ControlToggleActionStartPlan:
    blocked: bool
    blocked_title: str
    blocked_content: str
    blocked_revert_checked: bool | None
    confirmations: tuple[ControlConfirmationDialogPlan, ...]
    start_status: str


def resolve_runtime_state(*, snapshot_state=None, last_known_dpi_running: bool = False) -> ControlRuntimeState:
    if snapshot_state is not None:
        try:
            phase = str(snapshot_state.launch_phase or "").strip().lower() or (
                "running" if snapshot_state.launch_running else "stopped"
            )
            return ControlRuntimeState(
                phase=phase,
                last_error=str(snapshot_state.launch_last_error or "").strip(),
            )
        except Exception:
            pass

    return ControlRuntimeState(
        phase="running" if bool(last_known_dpi_running) else "stopped",
        last_error="",
    )

def short_dpi_error(last_error: str) -> str:
    text = str(last_error or "").strip()
    if not text:
        return ""
    first_line = text.splitlines()[0].strip()
    if len(first_line) <= 160:
        return first_line
    return first_line[:157] + "..."

STATUS_COLOR_RUNNING = "#6ccb5f"
STATUS_COLOR_BUSY = "#f5a623"
STATUS_COLOR_STOPPED = "#ff6b6b"


def build_status_plan(*, state: str | bool, last_error: str, language: str) -> ControlStatusPlan:
    return build_status_plan_for(
        text_prefix="page.control",
        state=state,
        last_error=last_error,
        language=language,
        autostart_description="Ждём завершения стартовой инициализации перед запуском",
    )


def build_status_plan_for(
    *,
    text_prefix: str,
    state: str | bool,
    last_error: str,
    language: str,
    autostart_description: str,
) -> ControlStatusPlan:
    """План карточки «Статус работы»: тексты, цвет кнопки и что делает нажатие на неё."""
    from app.ui_texts import tr as tr_catalog

    phase = str(state or "").strip().lower()
    if phase not in {"autostart_pending", "starting", "running", "stopping", "failed", "stopped"}:
        phase = "running" if bool(state) else "stopped"

    stop_name = tr_catalog("launch.action.stop", language=language, default="Остановить Zapret")
    start_name = tr_catalog("launch.action.start", language=language, default="Запустить Zapret")

    if phase == "running":
        return ControlStatusPlan(
            phase=phase,
            title=tr_catalog(f"{text_prefix}.status.running", language=language, default="Zapret работает"),
            description=tr_catalog(
                f"{text_prefix}.status.bypass_active",
                language=language,
                default="Обход блокировок активен · нажмите на кнопку, чтобы остановить",
            ),
            dot_color=STATUS_COLOR_RUNNING,
            pulsing=True,
            clickable=True,
            action_name=stop_name,
            show_close=True,
        )
    if phase == "autostart_pending":
        return ControlStatusPlan(
            phase=phase,
            title="Автозапуск Zapret запланирован",
            description=autostart_description,
            dot_color=STATUS_COLOR_BUSY,
            pulsing=True,
            clickable=True,
            action_name=stop_name,
            show_close=True,
        )
    if phase == "starting":
        return ControlStatusPlan(
            phase=phase,
            title="Zapret запускается",
            description="Ждём подтверждение процесса winws",
            dot_color=STATUS_COLOR_BUSY,
            pulsing=True,
            clickable=True,
            action_name=stop_name,
            show_close=True,
        )
    if phase == "stopping":
        return ControlStatusPlan(
            phase=phase,
            title="Zapret останавливается",
            description="Завершаем процесс и освобождаем WinDivert",
            dot_color=STATUS_COLOR_BUSY,
            pulsing=True,
            clickable=False,
            action_name=tr_catalog("launch.action.stopping", language=language, default="Zapret останавливается"),
            show_close=False,
        )
    if phase == "failed":
        return ControlStatusPlan(
            phase=phase,
            title="Ошибка запуска Zapret",
            description=short_dpi_error(last_error) or "Процесс не подтвердился или завершился сразу",
            dot_color=STATUS_COLOR_STOPPED,
            pulsing=False,
            clickable=True,
            action_name=start_name,
            show_close=False,
        )
    return ControlStatusPlan(
        phase="stopped",
        title=tr_catalog(f"{text_prefix}.status.stopped", language=language, default="Zapret остановлен"),
        description=tr_catalog(
            f"{text_prefix}.status.press_start",
            language=language,
            default="Нажмите на кнопку, чтобы запустить",
        ),
        dot_color=STATUS_COLOR_STOPPED,
        pulsing=False,
        clickable=True,
        action_name=start_name,
        show_close=False,
    )

def build_defender_toggle_start_plan(*, disable: bool, language: str, is_admin: bool) -> ControlToggleActionStartPlan:
    from app.ui_texts import tr as tr_catalog

    if not is_admin:
        return ControlToggleActionStartPlan(
            blocked=True,
            blocked_title="Требуются права администратора",
            blocked_content=(
                "Для управления Windows Defender требуются права администратора. "
                "Перезапустите программу от имени администратора."
            ),
            blocked_revert_checked=not bool(disable),
            confirmations=(),
            start_status="",
        )

    if disable:
        return ControlToggleActionStartPlan(
            blocked=False,
            blocked_title="",
            blocked_content="",
            blocked_revert_checked=None,
            confirmations=(
                ControlConfirmationDialogPlan(
                    title=tr_catalog(
                        "page.control.dialog.defender_disable.title",
                        language=language,
                        default="⚠️ Отключение Windows Defender",
                    ),
                    content=(
                        "Вы собираетесь отключить встроенную антивирусную защиту Windows.\n\n"
                        "Что произойдёт:\n"
                        "• Защита в реальном времени будет отключена\n"
                        "• Облачная защита и SmartScreen будут отключены\n"
                        "• Автоматическая отправка образцов будет отключена\n"
                        "• Мониторинг поведения программ будет отключён\n\n"
                        "⚠️ Ваш компьютер станет уязвим для вирусов и вредоносного ПО.\n"
                        "Отключайте только если вы понимаете, что делаете.\n"
                        "Вы сможете включить Defender обратно в любой момент."
                    ),
                    revert_checked=False,
                ),
                ControlConfirmationDialogPlan(
                    title="Подтверждение",
                    content=(
                        "Вы уверены? Нажимая «ОК», вы подтверждаете, что:\n\n"
                        "• Вы самостоятельно приняли решение отключить Windows Defender\n"
                        "• Вы осознаёте риски работы без антивирусной защиты\n"
                        "• Вы знаете, что можете включить защиту обратно\n\n"
                        "Может потребоваться перезагрузка для полного применения."
                    ),
                    revert_checked=False,
                ),
            ),
            start_status="Отключение Windows Defender...",
        )

    return ControlToggleActionStartPlan(
        blocked=False,
        blocked_title="",
        blocked_content="",
        blocked_revert_checked=None,
        confirmations=(
            ControlConfirmationDialogPlan(
                title=tr_catalog(
                    "page.control.dialog.defender_enable.title",
                    language=language,
                    default="Включение Windows Defender",
                ),
                content=(
                    "Включить Windows Defender обратно?\n\n"
                    "Это восстановит защиту вашего компьютера."
                ),
                revert_checked=True,
            ),
        ),
        start_status="Включение Windows Defender...",
    )

def build_max_block_toggle_start_plan(*, enable: bool, language: str) -> ControlToggleActionStartPlan:
    from app.ui_texts import tr as tr_catalog

    if enable:
        return ControlToggleActionStartPlan(
            blocked=False,
            blocked_title="",
            blocked_content="",
            blocked_revert_checked=None,
            confirmations=(
                ControlConfirmationDialogPlan(
                    title=tr_catalog(
                        "page.control.dialog.max_block_enable.title",
                        language=language,
                        default="Блокировка MAX",
                    ),
                    content=(
                        "Включить блокировку установки и работы программы MAX?\n\n"
                        "• Заблокирует запуск max.exe, max.msi и других файлов MAX\n"
                        "• Добавит правила блокировки в Windows Firewall\n"
                        "• Заблокирует домены MAX в файле hosts"
                    ),
                    revert_checked=False,
                ),
            ),
            start_status="",
        )

    return ControlToggleActionStartPlan(
        blocked=False,
        blocked_title="",
        blocked_content="",
        blocked_revert_checked=None,
        confirmations=(
            ControlConfirmationDialogPlan(
                title=tr_catalog(
                    "page.control.dialog.max_block_disable.title",
                    language=language,
                    default="Отключение блокировки MAX",
                ),
                content=(
                    "Отключить блокировку программы MAX?\n\n"
                    "Это удалит все созданные блокировки и правила."
                ),
                revert_checked=True,
            ),
        ),
        start_status="",
    )


def build_state_media_block_toggle_start_plan(*, enable: bool, language: str) -> ControlToggleActionStartPlan:
    from app.ui_texts import tr as tr_catalog

    if enable:
        return ControlToggleActionStartPlan(
            blocked=False,
            blocked_title="",
            blocked_content="",
            blocked_revert_checked=None,
            confirmations=(
                ControlConfirmationDialogPlan(
                    title=tr_catalog(
                        "page.control.dialog.state_media_block_enable.title",
                        language=language,
                        default="Блокировка государственных СМИ РФ",
                    ),
                    content=(
                        "Включить блокировку базового списка государственных новостных сайтов РФ?\n\n"
                        "Что будет сделано:\n"
                        "• В файл hosts будет добавлен отдельный блок ZapretGUI\n"
                        "• Домены из списка будут направлены на 127.0.0.1 и ::1\n"
                        "• При выключении будет удалён только этот блок\n\n"
                        "Для записи в hosts могут потребоваться права администратора."
                    ),
                    revert_checked=False,
                ),
            ),
            start_status="",
        )

    return ControlToggleActionStartPlan(
        blocked=False,
        blocked_title="",
        blocked_content="",
        blocked_revert_checked=None,
        confirmations=(
            ControlConfirmationDialogPlan(
                title=tr_catalog(
                    "page.control.dialog.state_media_block_disable.title",
                    language=language,
                    default="Отключение блокировки государственных СМИ РФ",
                ),
                content=(
                    "Отключить блокировку государственных СМИ РФ?\n\n"
                    "Из hosts будет удалён только блок, созданный ZapretGUI."
                ),
                revert_checked=True,
            ),
        ),
        start_status="",
    )


def build_internet_cleanup_start_plan(*, language: str) -> ControlToggleActionStartPlan:
    from app.ui_texts import tr as tr_catalog

    return ControlToggleActionStartPlan(
        blocked=False,
        blocked_title="",
        blocked_content="",
        blocked_revert_checked=None,
        confirmations=(
            ControlConfirmationDialogPlan(
                title=tr_catalog(
                    "page.control.dialog.internet_cleanup.title",
                    language=language,
                    default="Сброс сети Windows",
                ),
                content=(
                    "Сбросить сеть Windows?\n\n"
                    "Что будет сделано:\n"
                    "• Очистка кэша DNS, адресов и маршрутов\n"
                    "• Сброс прокси WinHTTP, если он задан\n"
                    "• Отключение системного прокси, если программа, на которую он указывает, не отвечает\n"
                    "• Удаление из Winsock надстроек посторонних программ\n\n"
                    "Адреса и DNS-серверы сетевых адаптеров не меняются. Перезагрузка не нужна."
                ),
                revert_checked=False,
            ),
        ),
        start_status="",
    )
