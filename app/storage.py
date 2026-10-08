"""File storage for generated certificates (local disk).

The rest of the app only deals in opaque string keys, so swapping this for S3 /
GCS means re-implementing these four methods.
"""
from __future__ import annotations

import os
from pathlib import Path


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, job_id: str, certificate_id: str, data: bytes) -> str:
        key = f"{job_id}/{certificate_id}.pdf"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)  # atomic: readers never see a half-written PDF
        return key

    def path(self, key: str) -> Path:
        return self._path(key)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("invalid storage key")
        return path
