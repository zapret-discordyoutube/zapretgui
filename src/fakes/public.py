"""Публичный вход реестра фейков winws2 и своих фейков пользователя."""

from __future__ import annotations

from fakes.catalog_repository import (
    CATALOG_FILE_NAME,
    FakeEntry,
    FakesCatalog,
    FakesCatalogError,
    invalidate_fakes_catalog_cache,
    load_fakes_catalog,
)
from fakes.names import BLOB_REFERENCE_ARG_NAMES, NFQWS2_BUILTIN_BLOBS, NFQWS2_LUA_RESERVED_NAMES
from fakes.user_fakes import (
    USER_FAKE_MAX_BYTES,
    USER_FAKES_REFERENCE_PREFIX,
    FakeNameRules,
    FakeRow,
    FakesPageSnapshot,
    UserFakeEntry,
    UserFakeError,
    build_fakes_page_snapshot,
    current_fake_name_rules,
    delete_user_fake,
    detect_fake_kind,
    import_user_fake,
    load_effective_fakes_catalog,
    merge_fakes_catalogs,
    open_user_fakes_folder,
    read_user_fakes,
)

__all__ = [
    "BLOB_REFERENCE_ARG_NAMES",
    "CATALOG_FILE_NAME",
    "FakeEntry",
    "FakeNameRules",
    "FakeRow",
    "FakesCatalog",
    "FakesCatalogError",
    "FakesPageSnapshot",
    "NFQWS2_BUILTIN_BLOBS",
    "NFQWS2_LUA_RESERVED_NAMES",
    "USER_FAKES_REFERENCE_PREFIX",
    "USER_FAKE_MAX_BYTES",
    "UserFakeEntry",
    "UserFakeError",
    "build_fakes_page_snapshot",
    "current_fake_name_rules",
    "delete_user_fake",
    "detect_fake_kind",
    "import_user_fake",
    "invalidate_fakes_catalog_cache",
    "load_effective_fakes_catalog",
    "load_fakes_catalog",
    "merge_fakes_catalogs",
    "open_user_fakes_folder",
    "read_user_fakes",
]
