import pytest
from fastapi import HTTPException

from app.api.deps import require_admin
from app.core.config import settings

CLIENT_ID = settings.INTERNAL_CLIENT_ID
SECRET = settings.INTERNAL_CLIENT_SECRET.get_secret_value()


async def test_valid_internal_client_returns_principal():
    assert await require_admin(CLIENT_ID, SECRET) == f"internal:{CLIENT_ID}"


@pytest.mark.parametrize(
    ("client_id", "secret"),
    [
        (CLIENT_ID, "wrong"),
        ("00000000-0000-0000-0000-000000000000", SECRET),
        ("", ""),
        (CLIENT_ID, ""),
    ],
)
async def test_invalid_internal_client_is_rejected(client_id, secret):
    with pytest.raises(HTTPException) as ex:
        await require_admin(client_id, secret)
    assert ex.value.status_code == 401
