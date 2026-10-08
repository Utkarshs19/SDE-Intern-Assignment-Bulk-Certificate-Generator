from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    parsed = make_url(url)
    kwargs: dict = {}
    is_sqlite = parsed.get_backend_name() == "sqlite"
    if is_sqlite:
        # The API threads and the worker threads all share this engine.
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")  # readers don't block the worker
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine
