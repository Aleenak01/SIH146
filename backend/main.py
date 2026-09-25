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
from .routers import analysis, clusters, graph, health, imports, leads, monitor as monitor_router, network, transactions, wallets
from .services.monitor import Monitor


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    db = Database(settings.database_url)
    monitor = Monitor(db, settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.init_db()                    # additive: creates missing tables only
        if settings.monitor_autostart:
            monitor.start()             # inbox watcher + automatic (micro-batch) analysis; synthetic data only
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

    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    install_error_handlers(app)
    for module in (health, transactions, wallets, imports, analysis, leads, monitor_router, network, clusters, graph):
        app.include_router(module.router)
    return app


app = create_app()
