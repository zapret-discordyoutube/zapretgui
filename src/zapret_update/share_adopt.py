"""Список установленной версии для программы, поставленной установщиком.

Раздавать соседям можно только файлы из подписанного списка своей версии
(``system/update/installed-manifest.json``). Обновление по файлам кладёт его
само, а установщик Inno — нет: подписанный список содержит контрольную сумму
самого установщика, и вложить его внутрь установщика нельзя (круг).

Поэтому программа берёт список сама: если списка нет или он от другой версии,
она спрашивает у источников новейший выпуск канала, и если это ровно та
версия, что запущена, сохраняет его подписанный список рядом с собой. Лишних
скачиваний нет: это тот же маленький список, что и при проверке обновлений.
Подпись проверяется так же, как при обновлении; чужую версию программа не берёт.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path


def adopt_installed_list(
    root: Path,
    *,
    channel: str,
    version: str,
    list_path: tuple[str, ...],
    lookup: Callable[[str], object],
    fetch: Callable[..., object],
    report: Callable[[str], None] = lambda _text: None,
):
    """Сохраняет подписанный список запущенной версии; возвращает его или ``None``."""
    found = lookup(channel)
    release = getattr(found, "release", None)
    if not release:
        report(f"Список установленной версии не взят: {getattr(found, 'error', '') or 'источники не ответили'}")
        return None
    if str(release.get("version") or "") != version:
        report(
            f"Список установленной версии не взят: новейший выпуск {release.get('version')}, "
            f"а запущена {version}"
        )
        return None
    signed = fetch(release, channel=channel, current_version=version, allow_same=True)
    if signed.manifest.version != version:
        report("Список установленной версии не взят: версия в подписанном списке другая")
        return None
    target = Path(root).joinpath(*list_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    for path, data in ((target.with_name(target.name + ".sig"), signed.signature), (target, signed.raw)):
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)
    report(f"Подписанный список версии {version} сохранён: программа сможет делиться обновлениями")
    return signed.manifest


__all__ = ["adopt_installed_list"]
