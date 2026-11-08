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


def activity_snapshot() -> dict:
    """Что показывает страница «Кто подключается»: состояние раздачи и журнал событий.

    Только читает уже созданное в памяти (службу не создаёт и на диск не ходит): страница
    спрашивает это каждые пару секунд из окна. Службы ещё нет — «пока ничего».
    """
    service = _default
    if service is None:
        return {"available": False}
    side = service._internet
    mapping = getattr(side, "mapping", None) if side is not None else None
    notes = [text for _when, text in list(service.notes) + (list(side.notes) if side is not None else [])]
    return {
        "available": True,
        "lan_port": service.port,
        "internet_running": service.internet_running,
        "internet_address": f"{mapping.external_ip}:{mapping.external_port}" if mapping is not None else "",
        "notes": notes[-6:],
        "events": service.activity.snapshot(),
        "summary": service.activity.summary(),
    }


def clear_activity() -> None:
    if _default is not None:
        _default.activity.clear()


def manifest_blob():
    """Подписанный список своей версии (текст и подпись) для соседей без доступа к серверам; нет — ``None``."""
    from updater.install import file_update

    root = file_update.install_root()
    if root is None:
        return None
    path = Path(root).joinpath(*file_update.INSTALLED_MANIFEST)
    try:
        return path.read_bytes(), path.with_name(path.name + ".sig").read_bytes()
    except OSError:
        return None


def registry_bases() -> list[str]:
    """Адреса, где сервер очереди ведёт книгу раздающих: основной и зеркала (только с проверяемым TLS)."""
    from ..release import watch

    bases = []
    for endpoint in watch.wait_endpoints():
        if endpoint.verify_ssl and endpoint.url.endswith("/wait"):
            bases.append(endpoint.url[: -len("/wait")])
    return bases


def release_from_peers(channel: str):
    """Новейший выпуск, названный соседями по интернету, когда серверы не отвечают; иначе ``None``.

    Работает только при включённой раздаче через интернет. Выпуск собирается из
    ПРОВЕРЕННОГО подписанного списка (название установщика, размер, SHA-256), а адрес
    установщика — обычный адрес выпуска на Forgejo: от соседа ничего не берётся на веру.
    Сами файлы потом идут тем же запасным путём (серверы, затем соседи).
    """
    try:
        from settings.store import get_share_updates_internet

        if not get_share_updates_internet():
            return None
    except Exception:  # noqa: BLE001
        return None
    from config.build_info import APP_VERSION
    from zapret_update import share_manifest

    from ..release import forgejo
    from ..release.signed import PRODUCT, TRUSTED_KEYS

    neighbours = share_internet.find_internet_neighbours(
        peer_book(), PRODUCT, None, bases=registry_bases()
    )
    found = share_manifest.fetch_from_peers(
        neighbours, trusted=TRUSTED_KEYS, product=PRODUCT, channel=channel, current_version=APP_VERSION
    )
    if found is None or found.manifest.installer is None:
        return None
    installer = found.manifest.installer
    version = found.manifest.version
    return {
        "version": version,
        "tag_name": version,
        "update_url": f"{forgejo.FORGEJO_ORIGIN}{forgejo.FORGEJO_RELEASE_DOWNLOAD_PREFIX}{version}/{installer.name}",
        "file_name": installer.name,
        "file_size": installer.size,
        "sha256": installer.sha256,
        "release_notes": "",
        "prerelease": channel != "stable",
        "name": f"Zapret {version}",
        "source": "соседи по интернету",
        "release_url": forgejo.release_page_url(version),
        "signed_blob": (found.raw, found.signature),
    }


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
                manifest_blob=manifest_blob,
            )
        return _default


def refresh_default() -> bool:
    """Из фонового потока: включить или выключить раздачу по настройке."""
    return default_service().refresh()


__all__ = [
    "activity_snapshot", "clear_activity", "default_service", "internet_finder", "manifest_blob", "peer_book", "refresh_default", "registry_bases",
    "release_from_peers",
]
