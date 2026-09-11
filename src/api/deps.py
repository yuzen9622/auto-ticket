from __future__ import annotations

from typing import Any

import httpx
from fastapi import Request

from accounts.service import AccountService
from adapters.ticketing.kktix.resolver import KKTIXEventResolver
from broker.broker import SqliteTaskBroker
from storage.database import Database

from .settings import ApiSettings


def get_settings(request: Request) -> ApiSettings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_broker(request: Request) -> SqliteTaskBroker:
    return request.app.state.broker


def get_accounts(request: Request) -> AccountService:
    return request.app.state.accounts


def get_hub(request: Request) -> Any:
    return getattr(request.app.state, "hub", None)


def get_resolver(request: Request) -> KKTIXEventResolver:
    resolver = getattr(request.app.state, "resolver", None)
    if resolver is not None:
        return resolver
    client = getattr(request.app.state, "http_client", None)
    if not isinstance(client, httpx.AsyncClient):
        raise RuntimeError("HTTP client is not initialized in app state")
    settings: ApiSettings = request.app.state.settings
    return KKTIXEventResolver(client, orgs=settings.resolver_orgs)
