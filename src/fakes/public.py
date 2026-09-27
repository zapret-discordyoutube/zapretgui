"""Публичный вход реестра фейков winws2."""

from __future__ import annotations

from fakes.catalog_repository import (
    CATALOG_FILE_NAME,
    FakeEntry,
    FakesCatalog,
    FakesCatalogError,
    invalidate_fakes_catalog_cache,
    load_fakes_catalog,
)
from fakes.names import BLOB_REFERENCE_ARG_NAMES, NFQWS2_BUILTIN_BLOBS

__all__ = [
    "BLOB_REFERENCE_ARG_NAMES",
    "CATALOG_FILE_NAME",
    "FakeEntry",
    "FakesCatalog",
    "FakesCatalogError",
    "NFQWS2_BUILTIN_BLOBS",
    "invalidate_fakes_catalog_cache",
    "load_fakes_catalog",
]
