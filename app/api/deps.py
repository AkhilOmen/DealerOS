import secrets

from fastapi import Header, HTTPException, Request, status

from app.core.config import settings
from app.messaging.publisher import Publisher

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
