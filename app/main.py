from __future__ import annotations

import functools
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from app import api, service
from app.config import Settings
from app.database import Base, make_engine
from app.pdf import render_certificate
from app.runner import InlineRunner, ThreadRunner
from app.storage import LocalStorage

log = logging.getLogger("certgen")


def create_app(settings: Settings | None = None, generator: service.Generator | None = None) -> FastAPI:
    """Application factory. `generator` lets tests swap the PDF renderer."""
    settings = settings or Settings.from_env()

    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    storage = LocalStorage(settings.storage_dir)
    generator = generator or functools.partial(render_certificate, font_path=settings.font_path)

    process = functools.partial(
        service.process_job, session_factory=session_factory, storage=storage, generator=generator
    )
    if settings.processing_mode == "inline":
        runner = InlineRunner(process)
    else:
        runner = ThreadRunner(process, settings.worker_threads)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resumed = service.recover_unfinished_jobs(session_factory, app.state.runner)
        if resumed:
            log.info("re-queued %d unfinished job(s)", resumed)
        yield
        app.state.runner.shutdown()
        engine.dispose()

    app = FastAPI(
        title="Bulk Certificate Generator",
        version="1.0.0",
        description="Submit a list of recipients, get a PDF certificate for each one.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.storage = storage
    app.state.runner = runner
    app.state.process_job = process
    app.include_router(api.router)
    return app
