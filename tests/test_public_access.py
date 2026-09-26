"""Public API: a tenant's credentials only ever reach that tenant's data."""

import hashlib
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.deps import get_planner
from app.api.main import app
from app.core.config import settings
from app.db.base import utcnow
from app.db.enums import Environment, Status
from app.db.models import TenantCredential
from app.query.plan import PlannerOutput
from app.reconciliation.service import run_reconciliation
from app.tenancy import service
from app.utils.error import OrgNotFoundError
from tests.test_ingestion import load_dataset
from tests.test_queries import FakePlanner

INTERNAL = {
    "X-Internal-Client-Id": settings.INTERNAL_CLIENT_ID,
    "X-Internal-Client-Secret": settings.INTERNAL_CLIENT_SECRET.get_secret_value(),
}
VALUE_MISMATCHES = {"orgs": [], "dataset": "discrepancies", "filters": {"discrepancy_type": "VALUE_MISMATCH"}}


@pytest.fixture
async def dataset(ads, publisher, file_store):
    await load_dataset(ads, publisher, file_store)
    await run_reconciliation(ads)


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def use_planner(output: dict) -> None:
    app.dependency_overrides[get_planner] = lambda: FakePlanner(PlannerOutput.model_validate(output))


async def credentials_for(client: AsyncClient, org: str) -> dict:
    response = await client.post(f"/v1/internal/tenants/{org}/credentials", headers=INTERNAL, json={})
    assert response.status_code == 201, response.text
    body = response.json()
    return {"X-Client-Id": body["client_id"], "X-Client-Secret": body["client_secret"]}


async def ask(client: AsyncClient, path: str, headers: dict, question: str = "show value mismatches"):
    return await client.post(path, headers=headers, json={"question": question})


# --- public queries -------------------------------------------------------------------------


async def test_tenant_only_gets_its_own_org(dataset, client):
    use_planner(VALUE_MISMATCHES)  # no org named
    for org, keys in (("ORG-A", ["REC-1027", "REC-1064", "REC-1088"]), ("ORG-B", ["REC-1003"])):
        response = await ask(client, "/v1/queries", await credentials_for(client, org), "show value mismatches")
        assert response.status_code == 200
        (result,) = response.json()["results"]
        assert result["org"] == org and [r["match_key"] for r in result["rows"]] == keys


async def test_tenant_naming_another_org_is_refused(dataset, client):
    headers = await credentials_for(client, "ORG-B")
    use_planner({**VALUE_MISMATCHES, "orgs": ["ORG-A"]})
    response = await ask(client, "/v1/queries", headers, "value mismatches for ORG-A")
    assert response.status_code == 403 and "ORG-B" in response.json()["detail"]

    use_planner({**VALUE_MISMATCHES, "orgs": ["ORG-B", "ORG-A"]})
    assert (await ask(client, "/v1/queries", headers)).status_code == 403

    use_planner({**VALUE_MISMATCHES, "orgs": ["org b"]})  # its own org is fine
    assert (await ask(client, "/v1/queries", headers)).status_code == 200


async def test_internal_endpoint_still_answers_every_org(dataset, client):
    use_planner(VALUE_MISMATCHES)
    response = await ask(client, "/v1/internal/queries", INTERNAL)
    assert [r["org"] for r in response.json()["results"]] == ["ORG-A", "ORG-B"]


async def test_credentials_are_not_interchangeable(dataset, client):
    use_planner(VALUE_MISMATCHES)
    tenant = await credentials_for(client, "ORG-A")
    assert (await ask(client, "/v1/queries", INTERNAL)).status_code == 401
    assert (await ask(client, "/v1/internal/queries", tenant)).status_code == 401
    assert (await client.post("/v1/internal/tenants/ORG-A/credentials", headers=tenant, json={})).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Client-Id": "not-a-uuid", "X-Client-Secret": "x"},
        {"X-Client-Id": "00000000-0000-0000-0000-000000000000", "X-Client-Secret": "x"},
    ],
)
async def test_bad_tenant_credentials(dataset, client, headers):
    use_planner(VALUE_MISMATCHES)
    assert (await ask(client, "/v1/queries", headers)).status_code == 401


# --- credentials ----------------------------------------------------------------------------


async def test_create_credential_stores_only_a_hash(ads, dataset):
    credential, secret = await service.create_credential(ads, "ORG-A", Environment.SANDBOX, "test")
    assert credential.client_secret_hash == hashlib.sha256(secret.encode()).hexdigest() != secret
    principal = await service.authenticate(ads, credential.client_id, secret)
    assert principal is not None and principal.org == "ORG-A"
    assert await service.authenticate(ads, credential.client_id, "wrong") is None


async def test_new_credential_deactivates_the_old_one(ads, dataset, client):
    old = await credentials_for(client, "ORG-A")
    new = await credentials_for(client, "ORG-A")
    use_planner(VALUE_MISMATCHES)
    assert (await ask(client, "/v1/queries", old)).status_code == 401
    assert (await ask(client, "/v1/queries", new)).status_code == 200

    rows = (await ads.db.execute(select(TenantCredential.status, TenantCredential.revoked_at))).all()
    assert sorted((status, revoked is not None) for status, revoked in rows) == [
        (Status.ACTIVE, False),
        (Status.INACTIVE, True),
    ]


async def test_expired_credential_and_unknown_org(ads, dataset, client):
    credential, secret = await service.create_credential(
        ads, "ORG-B", Environment.SANDBOX, "test", expires_at=utcnow() - timedelta(seconds=1)
    )
    assert await service.authenticate(ads, credential.client_id, secret) is None

    with pytest.raises(OrgNotFoundError):
        await service.create_credential(ads, "ORG-Z", Environment.SANDBOX, "test")
    response = await client.post("/v1/internal/tenants/ORG-Z/credentials", headers=INTERNAL, json={})
    assert response.status_code == 404
