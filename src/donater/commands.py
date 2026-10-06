from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from donater.state import PremiumState, premium_state_from_activation_info


@dataclass(frozen=True, slots=True)
class PremiumCheckerBundle:
    checker: object | None
    storage: object | None
    init_ok: bool


@dataclass(frozen=True, slots=True)
class PremiumActionResult:
    ok: bool
    message: str


def get_premium_checker():
    from donater.service import get_premium_service

    return get_premium_service()


def resolve_checker_bundle() -> PremiumCheckerBundle:
    try:
        from donater.storage import PremiumStorage

        return PremiumCheckerBundle(
            checker=get_premium_checker(),
            storage=PremiumStorage,
            init_ok=True,
        )
    except Exception:
        return PremiumCheckerBundle(checker=None, storage=None, init_ok=False)


def create_status_runtime(*, thread_parent, ui_actions):
    from donater.status_runtime import PremiumStatusRuntime

    return PremiumStatusRuntime(
        thread_parent=thread_parent,
        ui_actions=ui_actions,
        get_premium_checker=get_premium_checker,
        check_device_activation=check_device_activation,
    )


def start_pairing(checker: object | None = None, *, device_name: str | None = None):
    service = checker if checker is not None else get_premium_checker()
    return service.pair_start(device_name=device_name)


def check_device_activation(
    checker: object | None = None,
    *,
    use_cache: bool = False,
) -> dict[str, Any]:
    service = checker if checker is not None else get_premium_checker()
    return dict(service.check_device_activation(use_cache=use_cache) or {})


def get_premium_state(
    checker: object | None = None,
    *,
    use_cache: bool = True,
) -> PremiumState:
    info = check_device_activation(checker, use_cache=use_cache)
    return premium_state_from_activation_info(info)


def create_premium_worker_thread(target, args=None):
    from donater.premium_worker import PremiumWorkerThread

    return PremiumWorkerThread(target, args=args)


def reset_premium_storage(checker, storage) -> None:
    _ = storage
    if checker is None:
        raise RuntimeError("Сервис Premium не инициализирован")
    if checker.clear_activation() is not True:
        raise RuntimeError("Сервер не подтвердил отвязку устройства")


def read_device_storage_snapshot(storage, *, current_time: int) -> dict:
    if storage is None:
        return {
            "device_token": None,
            "pair_code": None,
            "last_check": None,
        }

    device_token = None
    pair_code = None
    pair_expires_at = None
    last_check = None

    try:
        device_token = storage.get_device_token()
    except Exception:
        pass

    try:
        pair_code = storage.get_pair_code()
        pair_expires_at = storage.get_pair_expires_at()
    except Exception:
        pass

    if pair_code and pair_expires_at:
        try:
            if int(pair_expires_at) < int(current_time):
                storage.clear_pair_code()
                pair_code = None
        except Exception:
            pass

    try:
        last_check = storage.get_last_check()
    except Exception:
        pass

    return {
        "device_token": device_token,
        "pair_code": pair_code,
        "last_check": last_check,
    }


def open_extend_bot() -> PremiumActionResult:
    try:
        from config.telegram_links import open_telegram_link

        open_telegram_link("zapretvpns_bot")
        return PremiumActionResult(ok=True, message="zapretvpns_bot")
    except Exception:
        try:
            import webbrowser

            webbrowser.open("https://t.me/zapretvpns_bot")
            return PremiumActionResult(ok=True, message="https://t.me/zapretvpns_bot")
        except Exception as exc:
            return PremiumActionResult(ok=False, message=str(exc))
