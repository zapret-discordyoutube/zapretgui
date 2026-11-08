"""Раздача обновлений в Zapret 2 GUI: что подставить в общий ``SharingService``.

Сама раздача (сервер файлов, поиск соседей, правило брандмауэра) лежит в общем
ядре ``zapret_update`` (``share_*``) и одинакова с Zapret KVN. Здесь только то,
что знает одна эта программа: где она стоит, как называется продукт, откуда
взять включённость, подписанный список версии и куда писать журнал.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from zapret_update import share_internet, share_peers, share_service

_default: share_service.SharingService | None = None
_default_lock = threading.Lock()
_book: share_peers.PeerBook | None = None
_book_lock = threading.Lock()  # отдельный замок: default_service() зовёт peer_book() под своим


def peer_book() -> share_peers.PeerBook:
    """Книга соседей по интернету: файл лежит в папке ``user``, которую обновление не трогает."""
    global _book
    with _book_lock:
        if _book is None:
            from updater.install import file_update

            root = file_update.install_root()
            path = Path(root) / "user" / "update-peers.json" if root else None
            _book = share_peers.PeerBook(path)
        return _book


def registry_bases() -> list[str]:
    """Адреса, где сервер очереди ведёт книгу раздающих: основной и зеркала (только с проверяемым TLS)."""
    from ..release import watch

    bases = []
    for endpoint in watch.wait_endpoints():
        if endpoint.verify_ssl and endpoint.url.endswith("/wait"):
            bases.append(endpoint.url[: -len("/wait")])
    return bases


def internet_finder(version: str):
    """Поиск соседей по интернету для запасного пути или ``None``, если человек не включил раздачу."""
    try:
        from settings.store import get_share_updates_internet

        if not get_share_updates_internet():
            return None
    except Exception:  # noqa: BLE001 — не прочиталось: считаем, что не включено
        return None
    from ..release.signed import PRODUCT

    return lambda: share_internet.find_internet_neighbours(peer_book(), PRODUCT, version, bases=registry_bases())


def default_service() -> share_service.SharingService:
    global _default
    with _default_lock:
        if _default is None:
            from config.build_info import APP_VERSION, CHANNEL
            from log.log import log
            from settings.store import get_share_updates_internet, get_share_updates_lan
            from updater.install import file_update
            from zapret_update import share_adopt

            from ..release.resolver import lookup_latest_release
            from ..release.signed import PRODUCT, fetch_signed_release

            root = file_update.install_root()
            report = lambda text: log(text, file_update.UPDATE_LOG_LEVEL)  # noqa: E731
            exe = (root / file_update.APP_EXE) if root else None
            internet = share_internet.InternetSide(
                product=PRODUCT,
                book=peer_book(),
                bases=registry_bases,
                open_firewall=(
                    lambda port: share_service.windows_open_firewall(
                        exe, name=share_service.FIREWALL_RULE_NAME_INTERNET, remote="any", port=port
                    )
                )
                if exe
                else None,
                report=report,
            )
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
                exe_path=exe,
                private_network=share_service.windows_private_network,
                open_firewall=share_service.windows_open_firewall,
                report=report,
                internet_enabled=get_share_updates_internet,
                internet=internet,
            )
        return _default


def refresh_default() -> bool:
    """Из фонового потока: включить или выключить раздачу по настройке."""
    return default_service().refresh()


__all__ = ["default_service", "internet_finder", "peer_book", "refresh_default", "registry_bases"]
