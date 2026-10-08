"""Application settings. Everything can be overridden with CERTGEN_* env vars."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    # Any SQLAlchemy URL works (e.g. postgresql+psycopg://user:pw@host/db).
    database_url: str = "sqlite:///./data/certificates.db"
    # Where generated PDFs are written.
    storage_dir: Path = Path("./data/certificates")
    # "background": jobs run on a worker thread pool, POST returns immediately.
    # "inline":     jobs run inside the POST request (used by most tests).
    processing_mode: str = "background"
    worker_threads: int = 2
    max_recipients_per_job: int = 5000
    # Optional TTF font for non-Latin names (e.g. Devanagari). See README.
    font_path: str | None = None

    def __post_init__(self) -> None:
        if self.processing_mode not in {"background", "inline"}:
            raise ValueError("processing_mode must be 'background' or 'inline'")
        if self.worker_threads < 1:
            raise ValueError("worker_threads must be >= 1")

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            database_url=env("CERTGEN_DATABASE_URL", cls.database_url),
            storage_dir=Path(env("CERTGEN_STORAGE_DIR", str(cls.storage_dir))),
            processing_mode=env("CERTGEN_PROCESSING_MODE", cls.processing_mode),
            worker_threads=int(env("CERTGEN_WORKER_THREADS", cls.worker_threads)),
            max_recipients_per_job=int(
                env("CERTGEN_MAX_RECIPIENTS", cls.max_recipients_per_job)
            ),
            font_path=env("CERTGEN_FONT_PATH") or None,
        )
