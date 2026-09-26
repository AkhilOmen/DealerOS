from sqlalchemy import func, select

from app.db.enums import DatasetType, DiscrepancyStatus, DiscrepancyType, IngestionJobStatus, SourceSystem
from app.db.models import Discrepancy, Event, Tenant
from app.db.repositories.discrepancy import discrepancy_repository
from app.reconciliation.service import run_reconciliation
from tests.test_ingestion import dataset, ingest, load_dataset

# Hand-verified from reading the three files (see the dataset review). This is the answer.
GOLDEN = {
    ("ORG-A", "REC-1015", DiscrepancyType.MISSING_IN_B),
    ("ORG-A", "REC-1027", DiscrepancyType.VALUE_MISMATCH),
    ("ORG-A", "REC-1042", DiscrepancyType.DUPLICATE_IN_B),
    ("ORG-A", "REC-1064", DiscrepancyType.VALUE_MISMATCH),
    ("ORG-A", "REC-1088", DiscrepancyType.VALUE_MISMATCH),
    ("ORG-A", "REC-1999", DiscrepancyType.ORPHAN_IN_B),
    ("ORG-B", "REC-1003", DiscrepancyType.VALUE_MISMATCH),
    ("ORG-B", "REC-1009", DiscrepancyType.DATE_MISMATCH),
    ("ORG-B", "REC-1019", DiscrepancyType.STATUS_MISMATCH),
    ("ORG-B", "REC-1050", DiscrepancyType.MISSING_VALUE),
    ("ORG-B", "REC-1061", DiscrepancyType.MISSING_IN_B),
    (None, "REC-1077", DiscrepancyType.TENANT_CONFLICT),
}


async def _tenants(ads) -> dict:
    return dict((await ads.db.execute(select(Tenant.id, Tenant.external_org_id))).all())


async def _open_discrepancies(ads) -> set:
    orgs = await _tenants(ads)
    rows = (await ads.db.execute(select(Discrepancy).where(Discrepancy.status == DiscrepancyStatus.OPEN))).scalars()
    return {(orgs.get(d.tenant_id), d.match_key, d.type) for d in rows}


async def _discrepancy(ads, match_key: str, type_: DiscrepancyType) -> Discrepancy:
    result = await ads.db.execute(
        select(Discrepancy).where(Discrepancy.match_key == match_key, Discrepancy.type == type_)
    )
    return result.scalar_one()


async def test_golden_discrepancies(ads, publisher, file_store):
    await load_dataset(ads, publisher, file_store)
    summary = await run_reconciliation(ads)

    assert await _open_discrepancies(ads) == GOLDEN
    assert summary.open_discrepancies == 12
    assert (summary.events, summary.visible_events, summary.hidden_events) == (241, 239, 2)


async def test_traps_are_not_reported(ads, publisher, file_store):
    await load_dataset(ads, publisher, file_store)
    await run_reconciliation(ads)
    reported = {key for _, key, _ in await _open_discrepancies(ads)}
    # dirty refs (rec1034, ' REC - 1070 ', '1112') and the legitimate split (1055)
    assert reported.isdisjoint({"REC-1034", "REC-1070", "REC-1112", "REC-1055"})


async def test_discrepancy_details(ads, publisher, file_store):
    await load_dataset(ads, publisher, file_store)
    await run_reconciliation(ads)

    d1003 = await _discrepancy(ads, "REC-1003", DiscrepancyType.VALUE_MISMATCH)
    assert d1003.values_by_system == {"SYSTEM_A": "121388.01", "SYSTEM_B": "94834.38"}
    assert d1003.details["b_equals_a_base_value"] is True

    d1064 = await _discrepancy(ads, "REC-1064", DiscrepancyType.VALUE_MISMATCH)
    assert d1064.values_by_system == {"SYSTEM_A": "183244.16", "SYSTEM_B": "125400.00"}  # lakh-parsed
    assert "b_equals_a_base_value" not in d1064.details

    d1009 = await _discrepancy(ads, "REC-1009", DiscrepancyType.DATE_MISMATCH)
    assert d1009.values_by_system == {"SYSTEM_A": "2026-03-31", "SYSTEM_B": "2026-04-02"}

    d1077 = await _discrepancy(ads, "REC-1077", DiscrepancyType.TENANT_CONFLICT)
    assert d1077.values_by_system == {"SYSTEM_A": "LOC-102", "SYSTEM_B": "LOC-201"}
    assert len(d1077.event_ids) == 2


async def test_tenant_assignment_and_isolation(ads, publisher, file_store):
    await load_dataset(ads, publisher, file_store)
    await run_reconciliation(ads)
    orgs = {org: tid for tid, org in (await _tenants(ads)).items()}

    hidden = (await ads.db.execute(select(Event.external_event_id).where(Event.tenant_id.is_(None)))).scalars()
    assert set(hidden) == {"REC-1077", "ENT/2026/4077"}

    org_a = {d.match_key for d in await discrepancy_repository.list(ads, orgs["ORG-A"])}
    org_b = {d.match_key for d in await discrepancy_repository.list(ads, orgs["ORG-B"])}
    assert org_a == {"REC-1015", "REC-1027", "REC-1042", "REC-1064", "REC-1088", "REC-1999"}
    assert org_b == {"REC-1003", "REC-1009", "REC-1019", "REC-1050", "REC-1061"}
    assert "REC-1077" not in org_a | org_b


async def test_rerun_is_idempotent(ads, publisher, file_store):
    await load_dataset(ads, publisher, file_store)
    await run_reconciliation(ads)
    first = {(d.id, d.first_detected_at, d.last_detected_at) for d in (await ads.db.execute(select(Discrepancy))).scalars()}

    summary = await run_reconciliation(ads)
    ads.db.expire_all()
    second = {(d.id, d.first_detected_at, d.last_detected_at) for d in (await ads.db.execute(select(Discrepancy))).scalars()}

    assert {i for i, _, _ in first} == {i for i, _, _ in second}  # same rows, updated in place
    assert {f for _, f, _ in first} == {f for _, f, _ in second}  # first_detected_at unchanged
    assert all(last2 > last1 for (_, _, last1), (_, _, last2) in zip(sorted(first), sorted(second), strict=True))
    assert (summary.tenant_changes, summary.resolved_discrepancies) == (0, 0)


async def test_fixed_data_resolves_discrepancy(ads, publisher, file_store):
    await load_dataset(ads, publisher, file_store)
    await run_reconciliation(ads)

    fix = (
        b"entry_id,record_ref,location_id,recorded_on,value,label\n"
        b"ENT/2026/4009,REC-1009,LOC-201,2026-03-31,111699.30,Entry for CAT-02\n"
    )
    job = await ingest(ads, publisher, file_store, DatasetType.SYSTEM_B_ENTRIES, fix, "fix_1009.csv")
    assert job.status == IngestionJobStatus.SUCCEEDED
    summary = await run_reconciliation(ads)

    d1009 = await _discrepancy(ads, "REC-1009", DiscrepancyType.DATE_MISMATCH)
    assert (d1009.status, d1009.resolved_at is not None) == (DiscrepancyStatus.RESOLVED, True)
    assert summary.resolved_discrepancies == 1
    assert len(await _open_discrepancies(ads)) == 11

    # the re-ingested entry was hidden by ingestion and is visible again after reconciliation
    entry = (
        await ads.db.execute(
            select(Event).where(Event.source_system == SourceSystem.SYSTEM_B, Event.external_event_id == "ENT/2026/4009")
        )
    ).scalar_one()
    assert entry.tenant_id is not None


async def test_empty_database_reconciles_to_nothing(ads):
    summary = await run_reconciliation(ads)
    assert (summary.events, summary.open_discrepancies) == (0, 0)
    assert (await ads.db.execute(select(func.count()).select_from(Discrepancy))).scalar() == 0


async def test_locations_only_does_not_create_discrepancies(ads, publisher, file_store):
    await ingest(ads, publisher, file_store, DatasetType.LOCATIONS, dataset("locations.csv"))
    summary = await run_reconciliation(ads)
    assert summary.open_discrepancies == 0
