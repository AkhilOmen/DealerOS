import uuid

import pytest
from sqlalchemy import select, update

from app.db.enums import SourceSystem
from app.db.models import Event, IngestionJob, Location, Tenant
from app.db.repositories.base import AsyncBaseTenantRepository
from app.db.repositories.event import event_repository
from app.utils.error import MissingTenantError
from tests.test_ingestion import load_dataset


async def _assign_tenants_like_reconciliation(ads) -> dict[str, uuid.UUID]:
    """Stand-in for reconciliation: tenant = own location's tenant, except REC-1077 (tenant
    conflict), whose events stay NULL."""
    await ads.db.execute(
        update(Event)
        .where(Event.match_key != "REC-1077")
        .values(tenant_id=select(Location.tenant_id).where(Location.id == Event.location_id).scalar_subquery())
    )
    await ads.db.commit()
    return dict((await ads.db.execute(select(Tenant.external_org_id, Tenant.id))).all())


async def _event(ads, source_system: SourceSystem, external_id: str) -> Event:
    result = await ads.db.execute(
        select(Event).where(Event.source_system == source_system, Event.external_event_id == external_id)
    )
    return result.scalar_one()


async def test_tenant_only_sees_its_own_events(ads, publisher, file_store):
    await load_dataset(ads, publisher, file_store)
    tenants = await _assign_tenants_like_reconciliation(ads)

    org_a = await event_repository.list(ads, tenants["ORG-A"], limit=1000)
    org_b = await event_repository.list(ads, tenants["ORG-B"], limit=1000)

    assert org_a and org_b
    assert {e.tenant_id for e in org_a} == {tenants["ORG-A"]}
    assert {e.tenant_id for e in org_b} == {tenants["ORG-B"]}
    assert not {e.id for e in org_a} & {e.id for e in org_b}


async def test_cannot_read_another_tenants_event_by_id(ads, publisher, file_store):
    await load_dataset(ads, publisher, file_store)
    tenants = await _assign_tenants_like_reconciliation(ads)
    org_b_event = await _event(ads, SourceSystem.SYSTEM_A, "REC-1001")  # LOC-201 -> ORG-B

    assert await event_repository.get_by_id(ads, tenants["ORG-B"], org_b_event.id) is not None
    assert await event_repository.get_by_id(ads, tenants["ORG-A"], org_b_event.id) is None


async def test_unresolved_rows_are_visible_to_nobody(ads, publisher, file_store):
    await load_dataset(ads, publisher, file_store)
    tenants = await _assign_tenants_like_reconciliation(ads)
    conflict_a = await _event(ads, SourceSystem.SYSTEM_A, "REC-1077")
    conflict_b = await _event(ads, SourceSystem.SYSTEM_B, "ENT/2026/4077")
    assert conflict_a.tenant_id is None and conflict_b.tenant_id is None

    for tenant_id in tenants.values():
        visible = {e.id for e in await event_repository.list(ads, tenant_id, limit=1000)}
        assert conflict_a.id not in visible and conflict_b.id not in visible
        assert await event_repository.get_by_id(ads, tenant_id, conflict_b.id) is None


async def test_everything_hidden_before_reconciliation(ads, publisher, file_store):
    await load_dataset(ads, publisher, file_store)
    for tenant_id in dict((await ads.db.execute(select(Tenant.external_org_id, Tenant.id))).all()).values():
        assert await event_repository.list(ads, tenant_id) == []


async def test_tenant_id_is_mandatory(ads):
    with pytest.raises(MissingTenantError):
        await event_repository.list(ads, None)  # type: ignore[arg-type]
    with pytest.raises(MissingTenantError):
        await event_repository.get_by_id(ads, None, None)  # type: ignore[arg-type]


def test_tenant_repository_requires_a_tenant_column():
    with pytest.raises(TypeError):
        AsyncBaseTenantRepository(IngestionJob)
