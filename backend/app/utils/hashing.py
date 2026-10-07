from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

CHUNK = 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_stream(stream: BinaryIO) -> str:
    h = hashlib.sha256()
    for chunk in iter(lambda: stream.read(CHUNK), b""):
        h.update(chunk)
    return h.hexdigest()


def sha256_file(path: str | Path) -> str:
    with open(path, "rb") as f:
        return sha256_stream(f)
