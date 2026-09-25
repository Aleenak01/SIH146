"""
FastAPI application factory.

Run locally from the project root:
    .\\.venv\\Scripts\\python.exe -m backend            (serves http://127.0.0.1:8000, docs at /docs)
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import Settings, load_settings
from .database import Database
from .errors import install_error_handlers
from .routers import analysis, cases, clusters, graph, health, imports, leads, monitor as monitor_router, network, search, settings as settings_router, sources, transactions, wallets
from .services import app_settings
from .services.monitor import Monitor
from .services.real_source import RealSourceState


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    db = Database(settings.database_url)
    monitor = Monitor(db, settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.init_db()                    # additive: creates missing tables only
        app_settings.apply_persisted(db, monitor, settings.monitor_autostart)   # saved choices, else the environment default
        yield
        monitor.stop()
        db.dispose()

    app = FastAPI(
        title="SIH146 Bitcoin Transaction Intelligence API",
        version=__version__,
        description="Local investigator backend. Synthetic/demo data unless a real-data source is explicitly configured.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.monitor = monitor
    app.state.real_source = RealSourceState()          # what this process did with the optional real Bitcoin source

    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    install_error_handlers(app)
    for module in (health, transactions, wallets, imports, analysis, leads, monitor_router, network, clusters, graph, cases, search, settings_router, sources):
        app.include_router(module.router)
    return app


app = create_app()
