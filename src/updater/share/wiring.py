"""Раздача обновлений в Zapret 2 GUI: что подставить в общий ``SharingService``.

Сама раздача (сервер файлов, поиск соседей, правило брандмауэра) лежит в общем
ядре ``zapret_update`` (``share_*``) и одинакова с Zapret KVN. Здесь только то,
что знает одна эта программа: где она стоит, как называется продукт, откуда
взять включённость, подписанный список версии и куда писать журнал.
"""

from __future__ import annotations

import sys
import threading

from zapret_update import share_service

_default: share_service.SharingService | None = None
_default_lock = threading.Lock()


def default_service() -> share_service.SharingService:
    global _default
    with _default_lock:
        if _default is None:
            from config.build_info import APP_VERSION, CHANNEL
            from log.log import log
            from settings.store import get_share_updates_lan
            from updater.install import file_update
            from zapret_update import share_adopt

            from ..release.resolver import lookup_latest_release
            from ..release.signed import PRODUCT, fetch_signed_release

            root = file_update.install_root()
            report = lambda text: log(text, file_update.UPDATE_LOG_LEVEL)  # noqa: E731
            _default = share_service.SharingService(
                root=root if sys.platform == "win32" else None,
                enabled=get_share_updates_lan,
                load_installed=lambda: file_update.installed_manifest(root, channel=CHANNEL) if root else None,
                product=PRODUCT,
                current_version=APP_VERSION,
                adopt=lambda: share_adopt.adopt_installed_list(
                    root,
                    channel=CHANNEL,
                    version=APP_VERSION,
                    list_path=file_update.INSTALLED_MANIFEST,
                    lookup=lookup_latest_release,
                    fetch=fetch_signed_release,
                    report=report,
                )
                if root
                else None,
                exe_path=(root / file_update.APP_EXE) if root else None,
                private_network=share_service.windows_private_network,
                open_firewall=share_service.windows_open_firewall,
                report=report,
            )
        return _default


def refresh_default() -> bool:
    """Из фонового потока: включить или выключить раздачу по настройке."""
    return default_service().refresh()


__all__ = ["default_service", "refresh_default"]
