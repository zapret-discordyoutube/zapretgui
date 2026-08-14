from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from settings import store as settings_store


SCHEMA_VERSION = 1
_LOCK = threading.RLock()


SCHEMA = """
CREATE TABLE IF NOT EXISTS client_meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_pairing(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  pairing_id TEXT,
  start_request_id TEXT NOT NULL,
  finish_request_id TEXT NOT NULL,
  pair_code TEXT,
  expires_at INTEGER,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS current_binding(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  binding_id TEXT NOT NULL,
  binding_generation INTEGER NOT NULL CHECK(binding_generation>0),
  device_token BLOB NOT NULL,
  created_at INTEGER NOT NULL,
  local_disabled INTEGER NOT NULL DEFAULT 0 CHECK(local_disabled IN (0,1))
);

CREATE TABLE IF NOT EXISTS signed_status_cache(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  kid TEXT,
  signature TEXT,
  signed_json TEXT NOT NULL,
  cached_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_operations(
  operation_id TEXT PRIMARY KEY,
  operation_kind TEXT NOT NULL CHECK(operation_kind='device_revoke'),
  payload_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error_code TEXT
);
"""


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _dpapi_encrypt(raw: bytes) -> bytes:
    if sys.platform != "win32":
        raise OSError("DPAPI is available only on Windows")
    import win32crypt

    return bytes(
        win32crypt.CryptProtectData(raw, "Zapret Premium", None, None, None, 0)
    )


def _dpapi_decrypt(raw: bytes) -> bytes:
    if sys.platform != "win32":
        raise OSError("DPAPI is available only on Windows")
    import win32crypt

    _description, decrypted = win32crypt.CryptUnprotectData(
        raw, None, None, None, 0
    )
    return bytes(decrypted)


class PremiumStorage:
    """Транзакционное состояние Premium в отдельной защищённой базе."""

    @classmethod
    def _path(cls) -> Path:
        return settings_store.get_settings_database_path().parent / "premium.sqlite3"

    @classmethod
    def _key_path(cls) -> Path:
        return cls._path().with_suffix(".key")

    @classmethod
    def _connect(cls) -> sqlite3.Connection:
        path = cls._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10.0, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        cls._retire_legacy_trust(conn)
        return conn

    @staticmethod
    def _retire_legacy_trust(conn: sqlite3.Connection) -> None:
        done = conn.execute(
            "SELECT value FROM client_meta WHERE key='legacy_trust_retired'"
        ).fetchone()
        if done is not None:
            return
        # Old identifiers, tokens and caches are deliberately discarded.  They
        # came from the retired server model and cannot prove current access.
        settings_store.prepare_settings_database()
        conn.execute(
            "INSERT INTO client_meta(key,value) VALUES('legacy_trust_retired',?)",
            (str(int(time.time())),),
        )
        conn.commit()

    @classmethod
    def _local_key(cls) -> bytes:
        path = cls._key_path()
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raw = os.urandom(32)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            fd = os.open(path, flags, 0o600)
            try:
                os.write(fd, raw)
                os.fsync(fd)
            finally:
                os.close(fd)
        if len(raw) != 32:
            raise RuntimeError("Premium local encryption key is invalid")
        return raw

    @classmethod
    def _encrypt_token(cls, token: str) -> bytes:
        raw = str(token).encode("utf-8")
        if sys.platform == "win32":
            return b"dpapi:" + _dpapi_encrypt(raw)
        key = cls._local_key()
        nonce = os.urandom(32)
        encrypted = cls._xor_hmac_stream(key, nonce, raw)
        tag = hmac.new(
            key, b"zapret-premium-token-v1:" + nonce + encrypted, hashlib.sha256
        ).digest()
        return b"hmac-stream:" + nonce + tag + encrypted

    @classmethod
    def _decrypt_token(cls, value: bytes) -> str:
        raw = bytes(value)
        if raw.startswith(b"dpapi:"):
            return _dpapi_decrypt(raw[len(b"dpapi:") :]).decode("utf-8")
        if raw.startswith(b"hmac-stream:"):
            protected = raw[len(b"hmac-stream:") :]
            nonce, tag, body = protected[:32], protected[32:64], protected[64:]
            key = cls._local_key()
            expected = hmac.new(
                key, b"zapret-premium-token-v1:" + nonce + body, hashlib.sha256
            ).digest()
            if not hmac.compare_digest(tag, expected):
                raise RuntimeError("Premium token integrity check failed")
            return cls._xor_hmac_stream(key, nonce, body).decode("utf-8")
        raise RuntimeError("Premium token encoding is invalid")

    @staticmethod
    def _xor_hmac_stream(key: bytes, nonce: bytes, value: bytes) -> bytes:
        stream = bytearray()
        counter = 0
        while len(stream) < len(value):
            stream.extend(
                hmac.new(
                    key,
                    b"zapret-premium-stream:" + nonce + counter.to_bytes(8, "big"),
                    hashlib.sha256,
                ).digest()
            )
            counter += 1
        return bytes(left ^ right for left, right in zip(value, stream))

    @classmethod
    def _get_meta(cls, key: str) -> str | None:
        with _LOCK, cls._connect() as conn:
            row = conn.execute(
                "SELECT value FROM client_meta WHERE key=?", (key,)
            ).fetchone()
            return str(row[0]) if row is not None else None

    @classmethod
    def _set_meta(cls, key: str, value: str | None) -> bool:
        with _LOCK, cls._connect() as conn:
            if value is None:
                conn.execute("DELETE FROM client_meta WHERE key=?", (key,))
            else:
                conn.execute(
                    """
                    INSERT INTO client_meta(key,value) VALUES(?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (key, str(value)),
                )
            return True

    @classmethod
    def get_device_id(cls) -> str:
        with _LOCK, cls._connect() as conn:
            row = conn.execute(
                "SELECT value FROM client_meta WHERE key='device_id'"
            ).fetchone()
            if row is not None and str(row[0]).strip():
                return str(row[0])
            device_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO client_meta(key,value) VALUES('device_id',?)",
                (device_id,),
            )
            return device_id

    @classmethod
    def begin_pairing(cls) -> dict[str, str]:
        device_id = cls.get_device_id()
        with _LOCK, cls._connect() as conn:
            start_request_id = str(uuid.uuid4())
            finish_request_id = str(uuid.uuid4())
            conn.execute("DELETE FROM pending_pairing")
            conn.execute(
                """
                INSERT INTO pending_pairing(
                    singleton, pairing_id, start_request_id,
                    finish_request_id, created_at
                ) VALUES(1,NULL,?,?,?)
                """,
                (start_request_id, finish_request_id, int(time.time())),
            )
            return {
                "device_id": device_id,
                "start_request_id": start_request_id,
                "finish_request_id": finish_request_id,
            }

    @classmethod
    def store_pairing_started(
        cls,
        *,
        request_id: str,
        pairing_id: str,
        code: str,
        expires_at: int,
    ) -> bool:
        with _LOCK, cls._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE pending_pairing
                SET pairing_id=?, pair_code=?, expires_at=?
                WHERE singleton=1 AND start_request_id=?
                """,
                (pairing_id, code.upper(), int(expires_at), request_id),
            )
            return int(cursor.rowcount or 0) == 1

    @classmethod
    def get_pending_pairing(cls) -> dict[str, Any] | None:
        with _LOCK, cls._connect() as conn:
            row = conn.execute("SELECT * FROM pending_pairing WHERE singleton=1").fetchone()
            return dict(row) if row is not None else None

    @classmethod
    def get_device_token(cls) -> Optional[str]:
        with _LOCK, cls._connect() as conn:
            row = conn.execute(
                """
                SELECT device_token FROM current_binding
                WHERE singleton=1 AND local_disabled=0
                """
            ).fetchone()
            return cls._decrypt_token(row[0]) if row is not None else None

    @classmethod
    def get_binding(cls, *, include_disabled: bool = False) -> dict[str, Any] | None:
        with _LOCK, cls._connect() as conn:
            sql = "SELECT * FROM current_binding WHERE singleton=1"
            if not include_disabled:
                sql += " AND local_disabled=0"
            row = conn.execute(sql).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["device_token"] = cls._decrypt_token(result.pop("device_token"))
            result["device_id"] = cls.get_device_id()
            return result

    @classmethod
    def store_after_pairing(
        cls,
        *,
        device_id: str,
        binding_id: str,
        binding_generation: int,
        device_token: str,
        signed_payload: Dict[str, Any],
        kid: Optional[str],
        sig: Optional[str],
    ) -> bool:
        if str(device_id) != cls.get_device_id():
            return False
        with _LOCK, cls._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO current_binding(
                    singleton, binding_id, binding_generation,
                    device_token, created_at, local_disabled
                ) VALUES(1,?,?,?,?,0)
                ON CONFLICT(singleton) DO UPDATE SET
                  binding_id=excluded.binding_id,
                  binding_generation=excluded.binding_generation,
                  device_token=excluded.device_token,
                  created_at=excluded.created_at,
                  local_disabled=0
                """,
                (
                    str(binding_id),
                    int(binding_generation),
                    cls._encrypt_token(device_token),
                    int(time.time()),
                ),
            )
            conn.execute("DELETE FROM pending_pairing")
            conn.execute("DELETE FROM pending_operations")
            cls._store_cache(conn, signed_payload, kid, sig)
            cls._set_meta_in_conn(conn, "last_check", datetime.now().isoformat())
            return True

    @staticmethod
    def _set_meta_in_conn(conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            """
            INSERT INTO client_meta(key,value) VALUES(?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )

    @staticmethod
    def _store_cache(
        conn: sqlite3.Connection,
        signed_payload: dict[str, Any],
        kid: str | None,
        sig: str | None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO signed_status_cache(
                singleton,kid,signature,signed_json,cached_at
            ) VALUES(1,?,?,?,?)
            ON CONFLICT(singleton) DO UPDATE SET
              kid=excluded.kid, signature=excluded.signature,
              signed_json=excluded.signed_json, cached_at=excluded.cached_at
            """,
            (
                kid,
                sig,
                json.dumps(signed_payload, ensure_ascii=False, sort_keys=True),
                int(time.time()),
            ),
        )

    @classmethod
    def prepare_revoke(cls) -> dict[str, Any] | None:
        device_id = cls.get_device_id()
        with _LOCK, cls._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM current_binding WHERE singleton=1"
            ).fetchone()
            if row is None:
                conn.execute("DELETE FROM signed_status_cache")
                conn.execute("DELETE FROM pending_pairing")
                return None
            current = dict(row)
            existing = conn.execute(
                "SELECT operation_id,payload_json FROM pending_operations WHERE operation_kind='device_revoke'"
            ).fetchone()
            if existing is not None:
                payload = json.loads(str(existing[1]))
                payload["operation_id"] = str(existing[0])
            else:
                operation_id = str(uuid.uuid4())
                payload = {
                    "device_id": device_id,
                    "binding_id": str(current["binding_id"]),
                    "binding_generation": int(current["binding_generation"]),
                }
                conn.execute(
                    """
                    INSERT INTO pending_operations(
                        operation_id,operation_kind,payload_json,created_at
                    ) VALUES(?,'device_revoke',?,?)
                    """,
                    (
                        operation_id,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        int(time.time()),
                    ),
                )
                payload["operation_id"] = operation_id
            # The durable retry record contains only immutable identifiers.
            # The credential remains encrypted in current_binding and exists in
            # plaintext only in memory for the immediate HTTP request.
            payload["device_token"] = cls._decrypt_token(current["device_token"])
            conn.execute("UPDATE current_binding SET local_disabled=1 WHERE singleton=1")
            conn.execute("DELETE FROM signed_status_cache")
            conn.execute("DELETE FROM pending_pairing")
            return payload

    @classmethod
    def complete_revoke(cls, operation_id: str) -> bool:
        with _LOCK, cls._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT 1 FROM pending_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM current_binding")
            conn.execute("DELETE FROM pending_operations WHERE operation_id=?", (operation_id,))
            conn.execute("DELETE FROM signed_status_cache")
            cls._set_meta_in_conn(conn, "last_check", datetime.now().isoformat())
            return True

    @classmethod
    def mark_revoke_failed(cls, operation_id: str, error_code: str) -> None:
        with _LOCK, cls._connect() as conn:
            conn.execute(
                """
                UPDATE pending_operations
                SET attempts=attempts+1,last_error_code=? WHERE operation_id=?
                """,
                (str(error_code)[:64], operation_id),
            )

    @classmethod
    def get_last_check(cls) -> Optional[datetime]:
        raw = cls._get_meta("last_check")
        try:
            return datetime.fromisoformat(raw) if raw else None
        except ValueError:
            return None

    @classmethod
    def save_last_check(cls) -> bool:
        return cls._set_meta("last_check", datetime.now().isoformat())

    @classmethod
    def get_last_network_failure_ts(cls) -> Optional[int]:
        raw = cls._get_meta("last_network_failure_ts")
        try:
            return int(raw) if raw is not None else None
        except ValueError:
            return None

    @classmethod
    def save_last_network_failure_now(cls) -> bool:
        return cls._set_meta("last_network_failure_ts", str(int(time.time())))

    @classmethod
    def clear_last_network_failure(cls) -> bool:
        return cls._set_meta("last_network_failure_ts", None)

    @classmethod
    def get_pair_code(cls) -> Optional[str]:
        row = cls.get_pending_pairing()
        if not row:
            return None
        return str(row.get("pair_code") or "") or None

    @classmethod
    def get_pair_expires_at(cls) -> Optional[int]:
        row = cls.get_pending_pairing()
        return int(row["expires_at"]) if row and row.get("expires_at") else None

    @classmethod
    def clear_pair_code(cls) -> bool:
        with _LOCK, cls._connect() as conn:
            conn.execute("DELETE FROM pending_pairing")
            return True

    @classmethod
    def get_premium_cache(cls) -> Optional[Dict[str, Any]]:
        if cls.get_binding() is None:
            return None
        with _LOCK, cls._connect() as conn:
            row = conn.execute("SELECT * FROM signed_status_cache WHERE singleton=1").fetchone()
            if row is None:
                return None
            return {
                "kid": row["kid"],
                "sig": row["signature"],
                "signed": json.loads(str(row["signed_json"])),
                "cached_at": int(row["cached_at"]),
            }

    @classmethod
    def store_status_active(
        cls,
        *,
        signed_payload: Dict[str, Any],
        kid: Optional[str],
        sig: Optional[str],
    ) -> bool:
        with _LOCK, cls._connect() as conn:
            cls._store_cache(conn, signed_payload, kid, sig)
            cls._set_meta_in_conn(conn, "last_check", datetime.now().isoformat())
            return True

    @classmethod
    def apply_status_inactive(cls, *, message: str) -> bool:
        _ = message
        with _LOCK, cls._connect() as conn:
            conn.execute("DELETE FROM signed_status_cache")
            cls._set_meta_in_conn(conn, "last_check", datetime.now().isoformat())
            return True

    @classmethod
    def clear_device_token(cls) -> bool:
        with _LOCK, cls._connect() as conn:
            conn.execute("DELETE FROM current_binding")
            return True

    @classmethod
    def clear_premium_cache(cls) -> bool:
        with _LOCK, cls._connect() as conn:
            conn.execute("DELETE FROM signed_status_cache")
            return True


__all__ = ["PremiumStorage", "SCHEMA_VERSION"]
