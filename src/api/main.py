from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from accounts.service import AccountService
from accounts.vault import EncryptedFileVault
from broker.broker import SqliteTaskBroker
from broker.outbox import OutboxReader
from broker.schema import create_broker_schema
from storage.database import Database

from .errors import register_exception_handlers
from .routers import accounts as accounts_router
from .routers import events as events_router
from .routers import experiments as experiments_router
from .routers import health as health_router
from .routers import tasks as tasks_router
from .settings import ApiSettings
from .ws import OutboxPump, WsHub
from .ws import endpoint as ws_router


def create_app(
    settings: ApiSettings | None = None,
    *,
    db: Database | None = None,
    http_client: httpx.AsyncClient | None = None,
    broker: SqliteTaskBroker | None = None,
    accounts: AccountService | None = None,
    hub: WsHub | None = None,
) -> FastAPI:
    resolved_settings = settings or ApiSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app_db = (
            db or getattr(app.state, "db", None) or Database(resolved_settings.db_path)
        )
        app.state.db = app_db

        await app_db.create_all()
        await create_broker_schema(app_db.engine)

        app_broker = (
            broker or getattr(app.state, "broker", None) or SqliteTaskBroker(app_db)
        )
        app.state.broker = app_broker

        vault = EncryptedFileVault.from_env(resolved_settings.vault_root)
        app_accounts = (
            accounts
            or getattr(app.state, "accounts", None)
            or AccountService(app_broker, vault=vault)
        )
        app.state.accounts = app_accounts

        app_hub = hub or getattr(app.state, "hub", None) or WsHub()
        app.state.hub = app_hub

        outbox_reader = OutboxReader(app_db)
        app.state.outbox_reader = outbox_reader

        pump = OutboxPump(outbox_reader, app_hub)
        app.state.pump = pump
        pump_task = asyncio.create_task(pump.run())

        app_client = (
            http_client
            or getattr(app.state, "http_client", None)
            or httpx.AsyncClient(timeout=10.0)
        )
        app.state.http_client = app_client

        try:
            yield
        finally:
            await pump.stop()
            pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump_task
            await app_client.aclose()
            await app_db.dispose()

    app = FastAPI(
        title="Auto Ticket API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.settings = resolved_settings
    if db is not None:
        app.state.db = db
    if broker is not None:
        app.state.broker = broker
    if accounts is not None:
        app.state.accounts = accounts
    if hub is not None:
        app.state.hub = hub
    if http_client is not None:
        app.state.http_client = http_client

    register_exception_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    resolved_settings.screenshot_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/static/screenshots",
        StaticFiles(directory=str(resolved_settings.screenshot_dir), check_dir=False),
        name="screenshots",
    )

    app.include_router(health_router.router)
    app.include_router(events_router.router)
    app.include_router(tasks_router.router)
    app.include_router(accounts_router.router)
    app.include_router(experiments_router.router)
    app.include_router(ws_router.router)

    return app
