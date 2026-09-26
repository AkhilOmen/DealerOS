import secrets
import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.config import settings
from app.db.session import AsyncDataStore, get_async_data_store
from app.messaging.publisher import Publisher
from app.query.planner import QueryPlanner
from app.tenancy.service import TenantPrincipal, authenticate

INTERNAL_PRINCIPAL_PREFIX = "internal"


def _matches(given: str, expected: str) -> bool:
    return secrets.compare_digest(given.encode(), expected.encode())


async def require_admin(
    x_internal_client_id: str = Header(default=""),
    x_internal_client_secret: str = Header(default=""),
) -> str:
    id_ok = _matches(x_internal_client_id, settings.INTERNAL_CLIENT_ID)
    secret_ok = _matches(x_internal_client_secret, settings.INTERNAL_CLIENT_SECRET.get_secret_value())

    if not (id_ok and secret_ok):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid internal client credentials")

    return f"{INTERNAL_PRINCIPAL_PREFIX}:{x_internal_client_id}"


def get_publisher(request: Request) -> Publisher:
    return request.app.state.publisher


def get_planner(request: Request) -> QueryPlanner | None:
    return request.app.state.planner


async def require_tenant(
    ads: Annotated[AsyncDataStore, Depends(get_async_data_store)],
    x_client_id: str = Header(default=""),
    x_client_secret: str = Header(default=""),
) -> TenantPrincipal:
    unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid client credentials")
    try:
        client_id = uuid.UUID(x_client_id)
    except ValueError:
        raise unauthorized from None

    principal = await authenticate(ads, client_id, x_client_secret)
    if principal is None:
        raise unauthorized
    return principal

