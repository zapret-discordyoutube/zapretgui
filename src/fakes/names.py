"""Имена blob-ов, которые знает сам winws2 (nfqws2), и аргументы-ссылки на blob.

Это правила самого движка, а не данные реестра:

- ``NFQWS2_BUILTIN_BLOBS`` nfqws2 загружает сам при старте
  (``load_const_blob_to_collection`` в nfq2/nfqws.c). Повторное объявление
  ``--blob=fake_default_tls:...`` winws2 отвергает ошибкой
  «duplicate blob name», поэтому в реестре и в ``--blob=`` их быть не должно;
- ``BLOB_REFERENCE_ARG_NAMES`` — аргументы ``--lua-desync``, значение которых
  является именем blob-а (или hex-литералом ``0x..``).
"""

from __future__ import annotations


NFQWS2_BUILTIN_BLOBS: frozenset[str] = frozenset(
    {"fake_default_tls", "fake_default_http", "fake_default_quic"}
)

BLOB_REFERENCE_ARG_NAMES: frozenset[str] = frozenset(
    {"blob", "fake_blob", "pattern", "seqovl_pattern", "fallback"}
)


__all__ = ["BLOB_REFERENCE_ARG_NAMES", "NFQWS2_BUILTIN_BLOBS"]
