from __future__ import annotations

"""Заплатки bsdiff в общем ядре обновления: применение, список выпуска, сборка."""

import bz2
import hashlib
import random

import pytest

from zapret_update import bsdiff, manifest


def _off(value: int) -> bytes:
    return (value if value >= 0 else (-value) | (1 << 63)).to_bytes(8, "little")


def _patch(new_size: int, steps, diff: bytes, extra: bytes) -> bytes:
    """Заплатка BSDIFF40 из готовых шагов (copy, insert, seek)."""
    control = b"".join(_off(copy) + _off(insert) + _off(seek) for copy, insert, seek in steps)
    blocks = [bz2.compress(control), bz2.compress(diff), bz2.compress(extra)]
    return b"BSDIFF40" + _off(len(blocks[0])) + _off(len(blocks[1])) + _off(new_size) + b"".join(blocks)


def _same_length_patch(old: bytes, new: bytes) -> bytes:
    """Простейшая заплатка: весь файл — «прежний плюс разница»."""
    diff = bytes((b - a) & 255 for a, b in zip(old, new))
    return _patch(len(new), [(len(new), 0, 0)], diff, b"")


def test_apply_adds_byte_differences_without_carry_between_bytes() -> None:
    rng = random.Random(3)
    old = rng.randbytes(50_000)
    new = bytearray(old)
    for index in range(0, len(new), 7):
        new[index] = (new[index] + rng.randrange(1, 256)) & 255
    new[0], new[1] = 0xFF, 0x00  # 0xFF + 1 не должно перенестись в соседний байт

    assert bsdiff.apply(old, _same_length_patch(old, bytes(new))) == new


def test_apply_inserts_extra_and_reads_zeros_outside_the_old_file() -> None:
    old = b"ABCDEFGH"
    # Переход к позиции 6, 4 байта (за концом — нули), вставка «xy», возврат к началу, 3 байта.
    diff = bytes([0, 0, 1, 2]) + bytes(3)
    patch = _patch(9, [(0, 0, 6), (4, 2, -10), (3, 0, 0)], diff, b"xy")

    assert bytes(bsdiff.apply(old, patch)) == b"GH\x01\x02xyABC"


def test_apply_reads_zeros_before_the_start_of_the_old_file() -> None:
    patch = _patch(4, [(0, 0, -2), (4, 0, 0)], bytes([7, 0, 0, 0]), b"")

    assert bytes(bsdiff.apply(b"ABCDEFGH", patch)) == b"\x07\x00AB"


@pytest.mark.parametrize(
    "patch",
    [
        b"not a patch at all, definitely not",
        _patch(10, [(20, 0, 0)], bytes(20), b""),  # выходит за размер нового файла
        _patch(10, [(4, 0, 0)], bytes(4), b""),  # собирает файл не целиком
        _patch(4, [(4, 0, 0)], bytes(2), b""),  # блок разницы короче нужного
        b"BSDIFF40" + _off(5) + _off(5) + _off(4) + b"\x00" * 10,  # блоки не bz2
    ],
)
def test_broken_patch_is_rejected(patch: bytes) -> None:
    with pytest.raises(bsdiff.BsdiffError):
        bsdiff.apply(b"abcd", patch)


def test_size_is_checked_before_memory_is_taken() -> None:
    huge = b"BSDIFF40" + _off(0) + _off(0) + _off(10**12)

    with pytest.raises(bsdiff.BsdiffError, match="другого размера"):
        bsdiff.apply(b"", huge, expected_size=4)


def test_real_bsdiff4_patches_apply() -> None:
    bsdiff4 = pytest.importorskip("bsdiff4")
    rng = random.Random(5)
    old = rng.randbytes(200_000)
    new = old[:1000] + b"inserted" + bytes((b + 3) & 255 for b in old[1000:90_000]) + old[95_000:] + b"tail"

    assert bsdiff.apply(old, bsdiff4.diff(old, new)) == new


# ── список выпуска ──────────────────────────────────────────────────────────

SHA_A, SHA_B, SHA_C = "a" * 64, "b" * 64, "c" * 64


def _payload(**extra) -> dict:
    return {
        "schema": 1, "product": "zapret-gui", "channel": "dev", "version": "1.0.0.2", "created": 1, "key": "k",
        "files": [{"path": "_internal/Zapret.exe", "size": 10, "sha256": SHA_B, "zsize": 9}],
        **extra,
    }


def test_bsdiff_patches_live_in_their_own_field_that_older_versions_skip() -> None:
    built = manifest.build(
        product="zapret-gui", channel="dev", version="1.0.0.2", key="k", created=1,
        files=_payload()["files"],
        patches=[{"from": SHA_A, "to": SHA_B, "size": 8}, {"from": SHA_A, "to": SHA_B, "size": 2, "kind": "bsdiff"}],
    )

    assert built["patches"] == [{"from": SHA_A, "to": SHA_B, "size": 8}]
    assert built["bsdiff"] == [{"from": SHA_A, "to": SHA_B, "size": 2}]
    parsed = manifest.parse(built)
    assert parsed.patch_for(SHA_A, SHA_B).kind == "bsdiff"
    # Версия без bsdiff читает только «patches» и видит одну заплатку zstd.
    older = manifest.parse({key: value for key, value in built.items() if key != "bsdiff"})
    assert [(item.kind, item.size) for item in older.patches] == [("zstd", 8)]


def test_patch_of_one_kind_in_the_other_field_is_rejected() -> None:
    with pytest.raises(manifest.ManifestError):
        manifest.parse(_payload(patches=[{"from": SHA_A, "to": SHA_B, "size": 2, "kind": "bsdiff"}]))
    with pytest.raises(manifest.ManifestError):
        manifest.build(
            product="zapret-gui", channel="dev", version="1.0.0.2", key="k", files=_payload()["files"],
            patches=[{"from": SHA_A, "to": SHA_B, "size": 2, "kind": "xdelta"}],
        )


def test_store_names_differ_by_kind() -> None:
    from zapret_update import store

    entry = manifest.PatchEntry(SHA_A, SHA_C, 1, "bsdiff")

    assert store.patch_file_name(entry) == f"{SHA_A}-{SHA_C}.bsdiff"
    assert store.patch_file_name(manifest.PatchEntry(SHA_A, SHA_C, 1)) == f"{SHA_A}-{SHA_C}.zst"
    assert store.is_store_name(f"{SHA_A}-{SHA_C}.bsdiff")
    assert not store.is_store_name(f"{SHA_A}.bsdiff")


# ── сборка хранилища и скачивание (Python 3.14) ─────────────────────────────


def test_builder_adds_bsdiff_only_when_zstd_patch_is_big_and_checks_it(tmp_path, monkeypatch) -> None:
    pytest.importorskip("compression.zstd", reason="сжатие zstd есть только в Python 3.14")
    from zapret_update import builder, plan, store

    rng = random.Random(11)
    old = rng.randbytes(400_000)
    # Сдвиг каждого байта на единицу: zstd не находит совпадений, bsdiff — одни единицы.
    new = bytes((b + 1) & 255 for b in old)
    for version, data in (("1", old), ("2", new)):
        (tmp_path / version).mkdir()
        (tmp_path / version / "Zapret.exe").write_bytes(data)
    cache = tmp_path / "cache"
    first = builder.build_store(layout={"_internal/Zapret.exe": tmp_path / "1" / "Zapret.exe"}, cache_dir=cache)
    previous = manifest.parse(_payload(files=first.files))
    calls = []

    def make(old_bytes: bytes, new_bytes: bytes) -> bytes:
        calls.append(len(old_bytes))
        return _same_length_patch(old_bytes, new_bytes)

    monkeypatch.setattr(builder, "BSDIFF_WHEN_ZSTD_FROM", 0)
    second = builder.build_store(
        layout={"_internal/Zapret.exe": tmp_path / "2" / "Zapret.exe"}, cache_dir=cache, previous=[previous], make_bsdiff=make
    )

    assert calls == [len(old)]
    kinds = {item.get("kind", "zstd"): item for item in second.patches}
    assert set(kinds) == {"bsdiff"}  # заплатка zstd не меньше файла целиком
    sha_new = hashlib.sha256(new).hexdigest()
    assert store.patch_name(first.files[0]["sha256"], sha_new, "bsdiff") in second.names

    # Повторная сборка берёт заплатку из кэша.
    again = builder.build_store(
        layout={"_internal/Zapret.exe": tmp_path / "2" / "Zapret.exe"}, cache_dir=cache, previous=[previous], make_bsdiff=make
    )
    assert calls == [len(old)] and again.patches == second.patches

    listed = manifest.parse(manifest.build(
        product="zapret-gui", channel="dev", version="1.0.0.3", key="k", files=second.files, patches=second.patches
    ))
    root = tmp_path / "app" / "_internal"
    root.mkdir(parents=True)
    (root / "Zapret.exe").write_bytes(old)
    planned = plan.make_plan(listed, tmp_path / "app")
    assert planned.want[0].name.endswith(".bsdiff")
    assert planned.download_size < len(new) / 10


def test_builder_drops_a_broken_bsdiff_maker_result(tmp_path, monkeypatch) -> None:
    pytest.importorskip("compression.zstd", reason="сжатие zstd есть только в Python 3.14")
    from zapret_update import builder

    old = random.Random(2).randbytes(300_000)
    new = bytes((b + 1) & 255 for b in old)
    for version, data in (("1", old), ("2", new)):
        (tmp_path / version).mkdir()
        (tmp_path / version / "Zapret.exe").write_bytes(data)
    cache = tmp_path / "cache"
    first = builder.build_store(layout={"_internal/Zapret.exe": tmp_path / "1" / "Zapret.exe"}, cache_dir=cache)
    monkeypatch.setattr(builder, "BSDIFF_WHEN_ZSTD_FROM", 0)

    messages: list[str] = []
    built = builder.build_store(
        layout={"_internal/Zapret.exe": tmp_path / "2" / "Zapret.exe"},
        cache_dir=cache,
        previous=[manifest.parse(_payload(files=first.files))],
        make_bsdiff=lambda o, n: _same_length_patch(o, n[:-1] + b"!"),
        log=messages.append,
    )

    assert not any(item.get("kind") == "bsdiff" for item in built.patches)
    assert not list(cache.glob("*.bsdiff"))
    assert any("не прошла проверку" in message for message in messages)


def test_builder_makes_patches_from_previous_releases_at_once_in_serial_order(tmp_path, monkeypatch) -> None:
    """Заплатки от трёх прежних версий делаются одновременно, итог как при поочерёдной сборке."""
    pytest.importorskip("compression.zstd", reason="сжатие zstd есть только в Python 3.14")
    import threading

    from zapret_update import builder

    rng = random.Random(5)
    base = rng.randbytes(300_000)
    versions = [bytes((b + shift) & 255 for b in base) for shift in range(4)]
    for number, data in enumerate(versions):
        (tmp_path / str(number)).mkdir()
        (tmp_path / str(number) / "Zapret.exe").write_bytes(data)
    layout = lambda number: {"_internal/Zapret.exe": tmp_path / str(number) / "Zapret.exe"}  # noqa: E731
    seed_cache = tmp_path / "seed"
    previous = [
        manifest.parse(_payload(files=builder.build_store(layout=layout(number), cache_dir=seed_cache).files))
        for number in (2, 1, 0)
    ]
    monkeypatch.setattr(builder, "BSDIFF_WHEN_ZSTD_FROM", 0)

    def run(cache_name: str, workers: int, make):
        cache = tmp_path / cache_name
        cache.mkdir()
        for blob in seed_cache.glob("*.zst"):
            (cache / blob.name).write_bytes(blob.read_bytes())
        return builder.build_store(
            layout=layout(3), cache_dir=cache, previous=previous, make_bsdiff=make, patch_workers=workers
        )

    serial = run("serial", 1, _same_length_patch)
    # Барьер пропускает, только когда все три заплатки bsdiff делаются разом.
    barrier = threading.Barrier(3, timeout=10)

    def together(old_bytes: bytes, new_bytes: bytes) -> bytes:
        barrier.wait()
        return _same_length_patch(old_bytes, new_bytes)

    parallel = run("parallel", 3, together)

    assert len(serial.patches) == 3 and all(item.get("kind") == "bsdiff" for item in serial.patches)
    assert parallel.patches == serial.patches
    assert parallel.names == serial.names
    assert (parallel.files, parallel.patched) == (serial.files, serial.patched)
    assert not list((tmp_path / "parallel").glob("*.tmp"))


# ── что программа умеет распаковать ─────────────────────────────────────────


def test_plan_refuses_before_download_without_zstd_and_skips_unknown_patch_kinds(tmp_path) -> None:
    from zapret_update import plan

    old = b"old content"
    sha_old = hashlib.sha256(old).hexdigest()
    listed = manifest.parse(manifest.build(
        product="zapret-gui", channel="dev", version="1.0.0.2", key="k",
        files=[{"path": "a.exe", "size": 10, "sha256": SHA_B, "zsize": 100}],
        patches=[{"from": sha_old, "to": SHA_B, "size": 50}, {"from": sha_old, "to": SHA_B, "size": 5, "kind": "bsdiff"}],
    ))
    (tmp_path / "a.exe").write_bytes(old)

    with pytest.raises(plan.PlanError, match="zstd"):
        plan.make_plan(listed, tmp_path, codecs=frozenset({"bsdiff"}))
    assert plan.make_plan(listed, tmp_path, codecs=frozenset({"zstd"})).want[0].patch.kind == "zstd"
    assert plan.make_plan(listed, tmp_path, codecs=frozenset({"zstd", "bsdiff"})).want[0].patch.kind == "bsdiff"


def test_zstandard_unpacks_store_files_where_python_has_no_compression_zstd(monkeypatch) -> None:
    pytest.importorskip("compression.zstd", reason="сжатие zstd есть только в Python 3.14")
    pytest.importorskip("zstandard")
    from zapret_update import store

    rng = random.Random(9)
    old = rng.randbytes(300_000)
    new = old[:1000] + b"changed" + old[1000:]
    blob, patch = store.compress(new), store.make_patch(old, new)
    sha = hashlib.sha256(new).hexdigest()

    def no_module():
        raise ImportError("compression.zstd")

    monkeypatch.setattr(store, "_zstd", no_module)
    assert store.unpack_blob(blob, sha256=sha, size=len(new)) == new
    assert store.apply_patch(old, patch, sha256=sha, size=len(new)) == new
    with pytest.raises(store.StoreError):
        store.unpack_blob(blob[:-10] + bytes(10), sha256=sha, size=len(new))


def test_zstd_patch_is_not_published_when_bsdiff_of_the_same_pair_is_smaller(tmp_path, monkeypatch) -> None:
    pytest.importorskip("compression.zstd", reason="сжатие zstd есть только в Python 3.14")
    from zapret_update import builder, store

    rng = random.Random(4)
    old = rng.randbytes(400_000)
    # Половина файла без изменений (zstd находит её — заплатка zstd «выгодна»),
    # другая половина сдвинута на единицу (её берёт только bsdiff).
    new = old[:200_000] + bytes((b + 1) & 255 for b in old[200_000:])
    for version, data in (("1", old), ("2", new)):
        (tmp_path / version).mkdir()
        (tmp_path / version / "Zapret.exe").write_bytes(data)
    cache = tmp_path / "cache"
    first = builder.build_store(layout={"_internal/Zapret.exe": tmp_path / "1" / "Zapret.exe"}, cache_dir=cache)
    monkeypatch.setattr(builder, "BSDIFF_WHEN_ZSTD_FROM", 0)

    built = builder.build_store(
        layout={"_internal/Zapret.exe": tmp_path / "2" / "Zapret.exe"}, cache_dir=cache,
        previous=[manifest.parse(_payload(files=first.files))], make_bsdiff=_same_length_patch,
    )

    assert [item.get("kind", "zstd") for item in built.patches] == ["bsdiff"]
    sha_old, sha_new = first.files[0]["sha256"], hashlib.sha256(new).hexdigest()
    assert store.patch_name(sha_old, sha_new) not in built.names
    assert store.patch_name(sha_old, sha_new, "bsdiff") in built.names
