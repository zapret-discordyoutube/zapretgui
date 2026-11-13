"""Сборка хранилища выпуска на машине сборки (Python 3.14).

Берёт раскладку установленной программы (путь в программе → файл сборки),
сжимает каждый новый файл один раз и делает заплатки для больших файлов,
изменившихся с прежних версий. Всё складывается в ``cache_dir`` под именами
хранилища (``store``); кэш живёт между выпусками, поэтому не изменившиеся
файлы второй раз не сжимаются.

Прежний файл для заплатки берётся из кэша, а если его там нет — через
``read_old(sha256, size)`` (сборка качает его из хранилища и проверяет).

Заплатку bsdiff делает ``make_bsdiff(прежний, новый) → байты BSDIFF40`` — её
даёт машина сборки (``bsdiff4``, в ядре только применение). Делается она лишь
там, где заплатка zstd вышла большой: bsdiff долгий (полминуты на
``Zapret.exe``) и прожорливый, а маленькую заплатку zstd ему не обогнать.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from . import store
from .manifest import Manifest, file_sha256, safe_relative_path

# Заплатка нужна, только если она заметно меньше файла целиком.
PATCH_WORTH_SHARE = 0.8
# bsdiff пробуется, только если заплатка zstd не меньше этого.
BSDIFF_WHEN_ZSTD_FROM = 1024 * 1024
# Сколько заплаток делать одновременно: по одной на прежнюю версию. Больше не
# надо — bsdiff на 90 МБ берёт больше гигабайта памяти.
PATCH_WORKERS = store.PATCH_FROM_VERSIONS


@dataclass
class StoreBuild:
    files: list[dict] = field(default_factory=list)
    patches: list[dict] = field(default_factory=list)
    # Имена файлов хранилища, на которые ссылается выпуск (все лежат в кэше).
    names: set[str] = field(default_factory=set)
    # Сколько файлов сжато и заплаток сделано заново в этот раз.
    compressed: int = 0
    patched: int = 0


def _write_atomic(path: Path, data: bytes) -> None:
    # Своё временное имя у каждого потока: заплатки пишутся одновременно.
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _cached_blob(cache_dir: Path, sha256: str) -> Path:
    return cache_dir / store.blob_name(sha256)


def _old_bytes(cache_dir: Path, sha256: str, size: int, read_old) -> bytes | None:
    cached = _cached_blob(cache_dir, sha256)
    if cached.is_file():
        try:
            return store.unpack_blob(cached.read_bytes(), sha256=sha256, size=size)
        except store.StoreError:
            cached.unlink(missing_ok=True)
    return read_old(sha256, size) if read_old is not None else None


@dataclass
class _BasePatches:
    """Заплатки одного файла от одной прежней версии: собираются в своём потоке."""

    patches: list[dict] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    patched: int = 0
    messages: list[str] = field(default_factory=list)


def _patches_from_base(
    *,
    cache: Path,
    path: str,
    base,
    sha256: str,
    size: int,
    zsize: int,
    new_bytes: Callable[[], bytes],
    read_old,
    make_bsdiff,
) -> _BasePatches:
    """Заплатка zstd и, если она большая, bsdiff — от прежней версии ``base``.

    Ничего общего не меняет: всё возвращается, итог собирает ``build_store`` в
    прежнем порядке, поэтому от прежних версий заплатки делаются одновременно.
    """
    out = _BasePatches()
    old_bytes: bytes | None = None
    name = store.patch_name(base.sha256, sha256)
    patch_path = cache / name
    if not patch_path.is_file():
        old_bytes = _old_bytes(cache, base.sha256, base.size, read_old)
        if old_bytes is None:
            out.messages.append(f"заплатка для {path} от {base.sha256[:12]} не сделана: прежнего файла нет")
            return out
        _write_atomic(patch_path, store.make_patch(old_bytes, new_bytes()))
        out.patched += 1
    patch_size = patch_path.stat().st_size
    if patch_size < zsize * PATCH_WORTH_SHARE:
        out.patches.append({"from": base.sha256, "to": sha256, "size": patch_size})
        out.names.append(name)
    if make_bsdiff is None or patch_size < BSDIFF_WHEN_ZSTD_FROM:
        return out
    bs_name = store.patch_name(base.sha256, sha256, "bsdiff")
    bs_path = cache / bs_name
    if not bs_path.is_file():
        if old_bytes is None:
            old_bytes = _old_bytes(cache, base.sha256, base.size, read_old)
        if old_bytes is None:
            return out
        try:
            made = make_bsdiff(old_bytes, new_bytes())
        except Exception as exc:  # noqa: BLE001 — без bsdiff выпуск всё равно годен
            out.messages.append(f"заплатка bsdiff для {path} от {base.sha256[:12]} не сделана: {exc}")
            return out
        # Сборка проверяет заплатку тем же кодом, что программа.
        try:
            store.apply_patch(old_bytes, made, sha256=sha256, size=size, kind="bsdiff")
        except store.StoreError as exc:
            out.messages.append(f"заплатка bsdiff для {path} от {base.sha256[:12]} не прошла проверку: {exc}")
            return out
        _write_atomic(bs_path, made)
        out.patched += 1
    bs_size = bs_path.stat().st_size
    if bs_size < min(patch_size, zsize) * PATCH_WORTH_SHARE:
        # Заплатка zstd той же пары больше никому не нужна: программы, которые
        # умеют zstd (Zapret 2 GUI 158+, Zapret KVN 0.8.27+), умеют и bsdiff и
        # берут меньшую. Не выкладываем лишние мегабайты.
        out.patches = [item for item in out.patches if item.get("kind", "zstd") != "zstd"]
        out.names = [item for item in out.names if item != name]
        out.patches.append({"from": base.sha256, "to": sha256, "size": bs_size, "kind": "bsdiff"})
        out.names.append(bs_name)
    return out


def build_store(
    *,
    layout: Mapping[str, Path],
    cache_dir: Path,
    previous: Iterable[Manifest] = (),
    read_old: Callable[[str, int], bytes | None] | None = None,
    make_bsdiff: Callable[[bytes, bytes], bytes] | None = None,
    log: Callable[[str], None] = lambda _message: None,
    patch_workers: int = PATCH_WORKERS,
) -> StoreBuild:
    """Файлы выпуска с ``zsize`` и заплатки; файлы хранилища — в ``cache_dir``.

    Заплатки (zstd уровня 19 и bsdiff, по полминуты каждая на большом exe) от
    разных прежних версий делаются одновременно в ``patch_workers`` потоках:
    zstd отпускает GIL, bsdiff идёт отдельным процессом. Порядок итога тот же,
    что при поочерёдной сборке.
    """
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    result = StoreBuild()
    earlier = list(previous)[: store.PATCH_FROM_VERSIONS]
    jobs: list[Callable[[], _BasePatches]] = []

    for relative in sorted(layout):
        source = Path(layout[relative])
        path = safe_relative_path(relative)
        size = source.stat().st_size
        sha256 = file_sha256(source)
        blob = _cached_blob(cache, sha256)
        if not blob.is_file():
            _write_atomic(blob, store.compress(source.read_bytes()))
            result.compressed += 1
        zsize = blob.stat().st_size
        result.files.append({"path": path, "size": size, "sha256": sha256, "zsize": zsize})
        result.names.add(blob.name)

        if size < store.PATCH_MIN_SIZE:
            continue
        bases = []
        for manifest in earlier:
            old = next((entry for entry in manifest.files if entry.path == path), None)
            if old is not None and old.sha256 != sha256 and old.sha256 not in {base.sha256 for base in bases}:
                bases.append(old)
        if not bases:
            continue
        new_bytes = _lazy_bytes(source)
        for base in bases:
            jobs.append(
                partial(
                    _patches_from_base,
                    cache=cache,
                    path=path,
                    base=base,
                    sha256=sha256,
                    size=size,
                    zsize=zsize,
                    new_bytes=new_bytes,
                    read_old=read_old,
                    make_bsdiff=make_bsdiff,
                )
            )

    if len(jobs) > 1 and patch_workers > 1:
        with ThreadPoolExecutor(max_workers=min(patch_workers, len(jobs)), thread_name_prefix="store-patch") as pool:
            done = list(pool.map(lambda job: job(), jobs))
    else:
        done = [job() for job in jobs]
    for item in done:
        for message in item.messages:
            log(message)
        result.patches.extend(item.patches)
        result.names.update(item.names)
        result.patched += item.patched
    return result


def _lazy_bytes(source: Path) -> Callable[[], bytes]:
    """Новый файл читается один раз и только если заплатку правда надо делать."""
    lock = threading.Lock()
    loaded: list[bytes] = []

    def read() -> bytes:
        with lock:
            if not loaded:
                loaded.append(source.read_bytes())
            return loaded[0]

    return read


def prune_cache(cache_dir: Path, keep: Iterable[str]) -> int:
    """Убирает из кэша файлы, на которые не ссылаются последние выпуски."""
    wanted = set(keep)
    removed = 0
    for path in Path(cache_dir).iterdir():
        if store.is_store_name(path.name) and path.name not in wanted:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def _read_old_from(template: str):
    """Прежний файл из хранилища по шаблону ссылки ``…/{name}``."""
    import urllib.request

    def read_old(sha256: str, size: int) -> bytes | None:
        try:
            with urllib.request.urlopen(template.format(name=store.blob_name(sha256)), timeout=120) as response:
                return store.unpack_blob(response.read(), sha256=sha256, size=size)
        except (OSError, ValueError):
            return None

    return read_old


def main(argv: list[str] | None = None) -> int:
    """Сборка хранилища из командной строки (сборке на Python старше 3.14).

    ::

        python3.14 -m zapret_update.builder --tree папка --cache кэш \
            --previous прежний.json … --store-url "https://…/{name}" --out итог.json

    ``--previous`` — уже проверенные списки прежних выпусков; в ``--out``
    пишутся ``files``, ``patches`` и ``names``.
    """
    import argparse
    import json

    from . import manifest as manifest_module

    parser = argparse.ArgumentParser(description="Сборка хранилища выпуска.")
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--previous", type=Path, action="append", default=[])
    parser.add_argument("--store-url", default="")
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--bsdiff", action="store_true", help="делать заплатки bsdiff (нужен пакет bsdiff4)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    skipped = tuple(item.strip("/") for item in args.exclude if item.strip("/"))
    layout = {}
    for path in sorted(args.tree.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(args.tree).as_posix()
        if any(relative == item or relative.startswith(item + "/") for item in skipped):
            continue
        layout[relative] = path
    previous = [manifest_module.parse(json.loads(path.read_text(encoding="utf-8"))) for path in args.previous]
    result = build_store(
        layout=layout,
        cache_dir=args.cache,
        previous=previous,
        read_old=_read_old_from(args.store_url) if args.store_url else None,
        make_bsdiff=__import__("bsdiff4").diff if args.bsdiff else None,
        log=print,
    )
    args.out.write_text(
        json.dumps(
            {
                "files": result.files,
                "patches": result.patches,
                "names": sorted(result.names),
                "compressed": result.compressed,
                "patched": result.patched,
            }
        ),
        encoding="utf-8",
    )
    print(f"файлов {len(result.files)}, сжато {result.compressed}, заплаток {len(result.patches)} (новых {result.patched})")
    return 0


__all__ = ["StoreBuild", "build_store", "main", "prune_cache"]


if __name__ == "__main__":
    import sys

    sys.exit(main())
