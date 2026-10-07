"""Storage abstraction. Local filesystem now; an S3/MinIO backend can implement the same interface.

Originals are content-addressed and write-once: an existing object is never overwritten.
"""

from __future__ import annotations

import json
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, BinaryIO

from app.config import get_settings


class StorageError(Exception):
    pass


class StorageBackend(ABC):
    @abstractmethod
    def put_file(self, key: str, src_path: Path) -> str:
        """Store a file under key (no-op if an identical object already exists). Returns key."""

    @abstractmethod
    def put_json(self, key: str, payload: Any) -> str: ...

    @abstractmethod
    def open(self, key: str) -> BinaryIO: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def local_path(self, key: str) -> Path:
        """Path to a local copy usable by parsers (local backend: the object itself)."""


class LocalStorage(StorageBackend):
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents and path != self.root:
            raise StorageError(f"Storage key escapes storage root: {key!r}")
        return path

    def put_file(self, key: str, src_path: Path) -> str:
        dest = self._resolve(key)
        if dest.exists():
            # Content-addressed: same key => same bytes. Never overwrite an original.
            return key
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        shutil.copyfile(src_path, tmp)
        tmp.replace(dest)
        return key

    def put_json(self, key: str, payload: Any) -> str:
        dest = self._resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_text(json.dumps(payload, default=str, indent=1), encoding="utf-8")
        tmp.replace(dest)
        return key

    def open(self, key: str) -> BinaryIO:
        path = self._resolve(key)
        if not path.exists():
            raise StorageError(f"Stored object not found: {key}")
        return open(path, "rb")

    def exists(self, key: str) -> bool:
        return self._resolve(key).exists()

    def local_path(self, key: str) -> Path:
        return self._resolve(key)


_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _storage
    if _storage is None:
        _storage = LocalStorage(get_settings().storage_dir)
    return _storage


def set_storage(backend: StorageBackend) -> None:
    """Override the storage backend (tests)."""
    global _storage
    _storage = backend


def original_key(application_id: Any, sha256: str, extension: str) -> str:
    return f"applications/{application_id}/originals/{sha256}{extension.lower()}"


def raw_extraction_key(application_id: Any, document_id: Any) -> str:
    return f"applications/{application_id}/raw/{document_id}.json"
