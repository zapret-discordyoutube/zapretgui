from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class UpdateChannelActionResult:
    ok: bool
    message: str


@dataclass(slots=True)
class ServerFullCheckGateResult:
    telegram_only: bool
    keep_existing_rows: bool
    message: str = ""


def is_auto_update_enabled() -> bool:
    from settings.store import get_auto_update_enabled

    return bool(get_auto_update_enabled())


def set_auto_update_enabled(enabled: bool) -> None:
    from settings.store import set_auto_update_enabled

    set_auto_update_enabled(bool(enabled))


def run_startup_update_check() -> dict:
    from updater.startup_update_check import check_for_update_sync

    return check_for_update_sync()


def check_installation_integrity(*, deep: bool = False):
    from install_integrity import verify_installation

    return verify_installation(deep=bool(deep))


def repair_installation(report=None, *, allow_download: bool = True):
    from updater.install.repair import repair_installation as _repair_installation

    return _repair_installation(report, allow_download=bool(allow_download))


def open_update_channel(channel: str) -> UpdateChannelActionResult:
    from config.telegram_links import open_telegram_link
    from updater.channel_utils import is_dev_update_channel

    try:
        domain = "zapretguidev" if is_dev_update_channel(channel) else "zapretnetdiscordyoutube"
        open_telegram_link(domain)
        return UpdateChannelActionResult(True, domain)
    except Exception as exc:
        return UpdateChannelActionResult(False, str(exc))


def prepare_server_full_check(*, skip_rate_limit: bool = False) -> ServerFullCheckGateResult:
    """Полная проверка серверов разрешена всегда.

    Все источники опрашиваются параллельно и недолго, ограничивать частоту
    больше незачем. Шаг уходит вместе со старой страницей обновлений.
    """
    _ = skip_rate_limit
    return ServerFullCheckGateResult(
        telegram_only=False,
        keep_existing_rows=False,
    )


def retry_server_check_without_dpi(*, is_any_running, shutdown_sync) -> tuple[bool, bool, str]:
    if not is_any_running():
        return False, False, ""

    shutdown_result = shutdown_sync(
        reason="server_status_probe_retry",
        include_cleanup=True,
    )
    if bool(getattr(shutdown_result, "still_running", False)):
        return False, False, "DPI не остановился"
    return True, True, ""


def restart_dpi_after_update(*, is_available, restart) -> bool:
    if not is_available():
        return False
    return bool(restart())


def stop_dpi_for_download(*, is_any_running, shutdown_sync) -> bool:
    if not is_any_running():
        return False
    shutdown_sync(reason="updater_download_connectivity", include_cleanup=True)
    return True


def stop_dpi_for_update(*, is_any_running, shutdown_sync, reason: str) -> tuple[bool, bool, str]:
    """Останавливает DPI в отдельной управляемой стадии обновления."""
    if not is_any_running():
        return False, True, ""

    result = shutdown_sync(
        reason=str(reason or "updater_pipeline"),
        include_cleanup=True,
        update_runtime_state=False,
    )
    if bool(getattr(result, "still_running", False)):
        return True, False, "DPI не остановился"
    return True, True, ""
