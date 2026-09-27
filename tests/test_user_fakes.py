"""Свои фейки пользователя: проверка имени, копирование, удаление, общий реестр."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from fakes.catalog_repository import FakeEntry, FakesCatalog
from fakes.user_fakes import (
    USER_FAKE_MAX_BYTES,
    FakeNameRules,
    UserFakeEntry,
    UserFakeError,
    build_fake_name_rules,
    build_fakes_page_snapshot,
    count_fake_usage,
    delete_user_fake,
    detect_fake_kind,
    import_user_fake,
    load_effective_fakes_catalog,
    lua_init_names,
    merge_fakes_catalogs,
    read_user_fakes,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = REPO_ROOT.parent / "private_zapretgui"
PRIVATE_CATALOG = PRIVATE_ROOT / "resources" / "system" / "fakes_catalog.sqlite3"
PRIVATE_BIN = PRIVATE_ROOT / "dist" / "bin"


# ------------------------------------------------------------------ helpers


def _client_hello(sni: str | None) -> bytes:
    extensions = b""
    if sni is not None:
        host = sni.encode("ascii")
        entry = b"\x00" + len(host).to_bytes(2, "big") + host
        server_name_list = len(entry).to_bytes(2, "big") + entry
        extensions += b"\x00\x00" + len(server_name_list).to_bytes(2, "big") + server_name_list
    extensions = b"\x00\x0b\x00\x02\x01\x00" + extensions  # ec_point_formats перед SNI
    body = (
        b"\x03\x03"
        + b"\x11" * 32
        + b"\x20" + b"\x22" * 32
        + b"\x00\x04\x13\x01\x13\x02"
        + b"\x01\x00"
        + len(extensions).to_bytes(2, "big")
        + extensions
    )
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + len(handshake).to_bytes(2, "big") + handshake


def _shipped(*names: str) -> FakesCatalog:
    entries = {
        name: FakeEntry(
            name=name,
            source_kind="file",
            file_name=f"{name}.bin",
            hex_value=None,
            kind="tls",
            sni=None,
            description="",
            same_bytes_as=None,
        )
        for name in names
    }
    return FakesCatalog(entries=entries, catalog_version="2026.01.01.1")


@pytest.fixture()
def settings_root(tmp_path, monkeypatch):
    from settings import store as settings_store

    settings_store.close_settings_database()
    monkeypatch.setattr(settings_store, "MAIN_DIRECTORY", str(tmp_path))
    yield tmp_path
    settings_store.close_settings_database()


def _paths(root: Path):
    from config.runtime_layout import ApplicationPaths

    return ApplicationPaths.from_root(root)


# ------------------------------------------------------- что внутри файла


def test_detect_tls_client_hello_with_sni():
    assert detect_fake_kind(_client_hello("www.example.com")) == ("tls", "www.example.com")
    assert detect_fake_kind(_client_hello(None)) == ("tls", None)
    # Голый Handshake без заголовка записи TLS.
    assert detect_fake_kind(_client_hello("a.b")[5:]) == ("tls", "a.b")


def test_detect_other_kinds():
    assert detect_fake_kind(b"\x00" * 16) == ("zeros", None)
    assert detect_fake_kind(b"\xc3\x00\x00\x00\x01" + b"\x08" * 40) == ("quic", None)
    assert detect_fake_kind(b"GET / HTTP/1.1\r\nHost: iana.org\r\n\r\n") == ("http", "iana.org")
    assert detect_fake_kind(b"\x12\x34random") == ("other", None)
    assert detect_fake_kind(b"") == ("other", None)


def test_detect_never_raises_on_truncated_client_hello():
    data = _client_hello("truncated.example.org")
    for size in range(len(data)):
        kind, sni = detect_fake_kind(data[:size])
        assert kind in {"tls", "other", "zeros"}
        assert sni in {None, "truncated.example.org"}


@pytest.mark.skipif(not PRIVATE_CATALOG.is_file() or not PRIVATE_BIN.is_dir(), reason="нет private_zapretgui")
def test_detect_matches_registry_for_shipped_tls_files():
    from fakes.catalog_repository import read_fakes_catalog

    catalog = read_fakes_catalog(PRIVATE_CATALOG)
    checked = 0
    for entry in catalog.entries.values():
        if entry.source_kind != "file" or entry.kind != "tls":
            continue
        data = (PRIVATE_BIN / entry.file_name).read_bytes()
        assert detect_fake_kind(data) == ("tls", entry.sni), entry.name
        checked += 1
    assert checked > 10


# ------------------------------------------------------------------ имена


def test_name_rules_reject_taken_and_bad_names(tmp_path):
    lua_dir = tmp_path / "lua"
    lua_dir.mkdir()
    (lua_dir / "custom_funcs.lua").write_text(
        "function my_lua_func(ctx)\nend\nmy_global = 1\nlocal x = 2\n", encoding="utf-8"
    )
    rules = build_fake_name_rules(
        shipped_names=["tls_google"],
        lua_names=lua_init_names(tmp_path),
        user_names=["mine"],
    )
    assert rules.problem("")
    assert rules.problem("1abc")
    assert rules.problem("bad-name")
    assert rules.problem("имя")
    assert rules.problem("a" * 65)
    assert "встроенных" in rules.problem("TLS_Google")  # без учёта регистра
    for engine_name in ("fake_default_tls", "string", "table", "rawsend", "fake_unknown_256", "my_lua_func", "my_global"):
        assert "winws2" in rules.problem(engine_name), engine_name
    assert rules.problem("MINE") == "Свой фейк с таким именем уже есть."
    assert rules.problem("x") == ""  # «local x» не глобал
    assert rules.problem("tls_my_site") == ""


def test_lua_init_names_skip_missing_files(tmp_path):
    assert lua_init_names(tmp_path) == frozenset()


def test_name_suggestion_is_free_and_valid():
    rules = FakeNameRules(shipped=frozenset({"my_file"}), engine=frozenset(), user=frozenset({"my_file_2"}))
    assert rules.suggest("my file.bin") == "my_file_3"
    assert rules.suggest("123.bin") == "fake_123"
    assert rules.suggest("---.bin") == "fake"


# ----------------------------------------------------- добавить / удалить


def test_import_copies_file_and_writes_row(settings_root):
    paths = _paths(settings_root)
    source = settings_root / "picked.bin"
    payload = _client_hello("my.site")
    source.write_bytes(payload)

    entry = import_user_fake(
        source,
        name="tls_my_site",
        description="  мой сайт  ",
        user_fakes_dir=paths.user_fakes_dir,
        rules=FakeNameRules(),
        now=lambda: "2026-09-27T10:00:00+00:00",
    )

    assert entry.blob_line() == "--blob=tls_my_site:@user/fakes/tls_my_site.bin"
    assert (paths.user_fakes_dir / "tls_my_site.bin").read_bytes() == payload
    assert [p.name for p in paths.user_fakes_dir.iterdir()] == ["tls_my_site.bin"]

    from settings.store import get_user_fakes_settings

    # Строка переживает нормализацию настроек целиком.
    assert get_user_fakes_settings()["fakes"] == {
        "tls_my_site": {
            "file_name": "tls_my_site.bin",
            "kind": "tls",
            "sni": "my.site",
            "description": "мой сайт",
            "created_at": "2026-09-27T10:00:00+00:00",
        }
    }
    assert read_user_fakes() == (entry,)


def test_import_rejects_bad_files_and_names(settings_root):
    paths = _paths(settings_root)
    empty = settings_root / "empty.bin"
    empty.write_bytes(b"")
    big = settings_root / "big.bin"
    big.write_bytes(b"\x01" * (USER_FAKE_MAX_BYTES + 1))
    ok = settings_root / "ok.bin"
    ok.write_bytes(b"\x01\x02")

    with pytest.raises(UserFakeError, match="пустой"):
        import_user_fake(empty, name="a", user_fakes_dir=paths.user_fakes_dir, rules=FakeNameRules())
    with pytest.raises(UserFakeError, match="большой"):
        import_user_fake(big, name="a", user_fakes_dir=paths.user_fakes_dir, rules=FakeNameRules())
    with pytest.raises(UserFakeError):
        import_user_fake(ok, name="fake_default_tls", user_fakes_dir=paths.user_fakes_dir,
                         rules=build_fake_name_rules(shipped_names=(), lua_names=(), user_names=()))
    from settings.store import get_user_fakes_settings

    assert get_user_fakes_settings()["fakes"] == {}


def test_import_rechecks_name_inside_transaction_without_overwriting(settings_root):
    paths = _paths(settings_root)
    first = settings_root / "first.bin"
    first.write_bytes(b"\x01first")
    second = settings_root / "second.bin"
    second.write_bytes(b"\x02second")
    import_user_fake(first, name="Foo", user_fakes_dir=paths.user_fakes_dir, rules=FakeNameRules())

    # Устаревшие правила (своих имён не знают) — ловит проверка в транзакции,
    # файл «Foo.bin» не перезаписан (на Windows foo.bin — тот же файл).
    with pytest.raises(UserFakeError, match="уже есть"):
        import_user_fake(second, name="foo", user_fakes_dir=paths.user_fakes_dir, rules=FakeNameRules())

    assert (paths.user_fakes_dir / "Foo.bin").read_bytes() == b"\x01first"
    assert sorted(p.name for p in paths.user_fakes_dir.iterdir()) == ["Foo.bin"]
    assert [entry.name for entry in read_user_fakes()] == ["Foo"]


def test_delete_removes_row_then_file_and_only_own(settings_root):
    paths = _paths(settings_root)
    source = settings_root / "x.bin"
    source.write_bytes(b"\x05")
    import_user_fake(source, name="mine", user_fakes_dir=paths.user_fakes_dir, rules=FakeNameRules())

    with pytest.raises(UserFakeError, match="только свой"):
        delete_user_fake("tls_google", user_fakes_dir=paths.user_fakes_dir)

    delete_user_fake("mine", user_fakes_dir=paths.user_fakes_dir)
    assert read_user_fakes() == ()
    assert not (paths.user_fakes_dir / "mine.bin").exists()

    # Файл уже пропал — удаление строки всё равно проходит.
    import_user_fake(source, name="mine", user_fakes_dir=paths.user_fakes_dir, rules=FakeNameRules())
    (paths.user_fakes_dir / "mine.bin").unlink()
    delete_user_fake("mine", user_fakes_dir=paths.user_fakes_dir)
    assert read_user_fakes() == ()


def test_settings_without_user_fakes_section_read_default(settings_root):
    from settings import store as settings_store

    settings_store.prepare_settings_database()
    database = settings_store.get_settings_database_path()
    settings_store.close_settings_database()
    with sqlite3.connect(database) as connection:
        deleted = connection.execute("DELETE FROM settings_sections WHERE section = 'user_fakes'").rowcount
    connection.close()
    assert deleted == 1

    assert settings_store.get_user_fakes_settings() == {"version": 1, "fakes": {}}


def test_normalize_drops_broken_rows():
    from settings.normalize import normalize_user_fakes

    section = normalize_user_fakes(
        {
            "fakes": {
                "good": {"file_name": "good.bin", "kind": "weird", "sni": "", "description": "d", "created_at": 5},
                "bad-name": {"file_name": "x.bin"},
                "path": {"file_name": "../evil.bin"},
                "noext": {"file_name": "noext.txt"},
            }
        }
    )
    assert section == {
        "version": 1,
        "fakes": {
            "good": {"file_name": "good.bin", "kind": "other", "sni": None, "description": "d", "created_at": "5"},
        },
    }


# ------------------------------------------------------ общий реестр фейков


def test_merge_prefers_registry_and_skips_missing_files(tmp_path):
    (tmp_path / "mine.bin").write_bytes(b"\x01")
    user = [
        UserFakeEntry("mine", "mine.bin", "other", None, "", ""),
        UserFakeEntry("TLS_GOOGLE", "TLS_GOOGLE.bin", "other", None, "", ""),
        UserFakeEntry("lost", "lost.bin", "other", None, "", ""),
    ]
    merged = merge_fakes_catalogs(_shipped("tls_google"), user, user_fakes_dir=tmp_path)

    assert list(merged.entries) == ["tls_google", "mine"]
    assert merged.entries["tls_google"].blob_line() == "--blob=tls_google:@bin/tls_google.bin"
    assert merged.entries["mine"].blob_line() == "--blob=mine:@user/fakes/mine.bin"
    assert merged.catalog_version == "2026.01.01.1"


def test_effective_catalog_declares_user_fake_on_strategy_pick(settings_root, monkeypatch):
    import fakes.user_fakes as user_fakes
    from profile.preset_blob_declarations import plan_blob_declaration_lines

    paths = _paths(settings_root)
    source = settings_root / "x.bin"
    source.write_bytes(b"\x07\x07")
    import_user_fake(source, name="my_fake", user_fakes_dir=paths.user_fakes_dir, rules=FakeNameRules())
    monkeypatch.setattr(user_fakes, "load_fakes_catalog", lambda _path: _shipped("tls_google"))

    catalog = load_effective_fakes_catalog(paths)
    lines, report = plan_blob_declaration_lines(
        ["--lua-desync=fake:blob=my_fake:repeats=2 --lua-desync=fake:blob=tls_google"],
        {},
        lambda: catalog,
    )
    assert lines == [
        "--blob=my_fake:@user/fakes/my_fake.bin",
        "--blob=tls_google:@bin/tls_google.bin",
    ]
    assert report.unknown == ()


def test_feature_assembly_uses_effective_catalog():
    import inspect

    from app import feature_assembly

    source = inspect.getsource(feature_assembly.load_installed_fakes_catalog)
    assert "load_effective_fakes_catalog" in source


def test_runner_accepts_user_fake_path_relative_to_install_root(tmp_path):
    from winws_runtime.runners.zapret2_runner import Winws2StrategyRunner

    runner = object.__new__(Winws2StrategyRunner)
    runner.work_dir = str(tmp_path)
    runner.lists_dir = str(tmp_path / "lists")
    runner.bin_dir = str(tmp_path / "bin")
    source = "--blob=my_fake:@user/fakes/my_fake.bin\n"

    missing = runner._collect_missing_preset_references_from_text(source)
    assert len(missing) == 1
    assert Path(missing[0][1]) == tmp_path / "user" / "fakes" / "my_fake.bin"

    (tmp_path / "user" / "fakes").mkdir(parents=True)
    (tmp_path / "user" / "fakes" / "my_fake.bin").write_bytes(b"\x01")
    assert runner._collect_missing_preset_references_from_text(source) == []


# ------------------------------------------------------ данные страницы


def test_usage_counts_and_page_snapshot(settings_root, monkeypatch):
    import fakes.user_fakes as user_fakes

    strategies = {
        "tcp": {
            "a": SimpleNamespace(args="--lua-desync=fake:blob=tls_google\n--lua-desync=fake:blob=tls_google"),
            "b": SimpleNamespace(args="--lua-desync=fake:blob=tls_google:tls_mod=rnd"),
            "c": SimpleNamespace(args="--lua-desync=multisplit:pos=2"),
        }
    }
    assert count_fake_usage(strategies) == {"tls_google": 2}

    paths = _paths(settings_root)
    source = settings_root / "x.bin"
    source.write_bytes(b"\x07")
    import_user_fake(source, name="mine", user_fakes_dir=paths.user_fakes_dir, rules=FakeNameRules())
    (paths.user_fakes_dir / "mine.bin").unlink()
    monkeypatch.setattr(user_fakes, "load_fakes_catalog", lambda _path: _shipped("tls_google", "tls_other"))

    snapshot = build_fakes_page_snapshot(application_paths=paths, load_strategy_catalogs=lambda: strategies)

    rows = {row.name: row for row in snapshot.rows}
    assert rows["tls_google"].used_by == 2 and not rows["tls_google"].is_user
    assert rows["tls_other"].used_by == 0
    assert rows["mine"].is_user and rows["mine"].used_by is None and rows["mine"].file_missing
    assert rows["mine"].blob_line == "--blob=mine:@user/fakes/mine.bin"
    assert snapshot.rules.problem("tls_google") and snapshot.rules.problem("Mine")
    assert snapshot.catalog_error == "" and snapshot.usage_error == ""


def test_page_snapshot_survives_registry_error(settings_root, monkeypatch):
    import fakes.user_fakes as user_fakes

    def _broken(_path):
        raise RuntimeError("битый реестр")

    monkeypatch.setattr(user_fakes, "load_fakes_catalog", _broken)
    snapshot = build_fakes_page_snapshot(application_paths=_paths(settings_root), load_strategy_catalogs=None)
    assert snapshot.rows == ()
    assert snapshot.catalog_error == "битый реестр"
