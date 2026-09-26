from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.db.enums import DatasetType, IngestionJobStatus, MatchKeyMethod, SourceSystem, TriggerType
from app.db.models import Event, IngestionJob, Location, Tenant
from app.db.repositories.ingestion_job import ingestion_job_repository
from app.ingestion import service
from app.utils.error import EnqueueError
from tests.conftest import DATASET, FakePublisher


async def _chunks(data: bytes):
    yield data


async def submit(ads, publisher, file_store, dataset_type: DatasetType, data: bytes, name: str = "file.csv"):
    return await service.submit_job(
        ads,
        publisher,
        dataset_type=dataset_type,
        file_name=name,
        chunks=_chunks(data),
        trigger_type=TriggerType.MANUAL,
        triggered_by="test",
        store=file_store,
    )


async def run(ads, job_id, file_store) -> IngestionJob:
    job = await service.run_job(ads, job_id, store=file_store)
    assert job is not None
    return job


async def ingest(ads, publisher, file_store, dataset_type: DatasetType, data: bytes, name: str = "file.csv"):
    job = await submit(ads, publisher, file_store, dataset_type, data, name)
    return await run(ads, job.id, file_store)


def dataset(name: str) -> bytes:
    return (DATASET / name).read_bytes()


async def load_dataset(ads, publisher, file_store):
    for dataset_type, name in [
        (DatasetType.LOCATIONS, "locations.csv"),
        (DatasetType.SYSTEM_A_EVENTS, "system_a.csv"),
        (DatasetType.SYSTEM_B_ENTRIES, "system_b.csv"),
    ]:
        job = await ingest(ads, publisher, file_store, dataset_type, dataset(name), name)
        assert job.status == IngestionJobStatus.SUCCEEDED, (name, job.error_message, job.rejected_row_details)


async def event(ads, source_system: SourceSystem, external_id: str) -> Event:
    result = await ads.db.execute(
        select(Event).where(Event.source_system == source_system, Event.external_event_id == external_id)
    )
    return result.scalar_one()


# --- full dataset -------------------------------------------------------------------------


async def test_full_dataset_loads(ads, publisher, file_store):
    await load_dataset(ads, publisher, file_store)

    tenants = dict((await ads.db.execute(select(Tenant.external_org_id, Tenant.id))).all())
    assert set(tenants) == {"ORG-A", "ORG-B"}
    locations = dict((await ads.db.execute(select(Location.external_location_id, Location.tenant_id))).all())
    assert locations == {
        "LOC-101": tenants["ORG-A"],
        "LOC-102": tenants["ORG-A"],
        "LOC-103": tenants["ORG-A"],
        "LOC-201": tenants["ORG-B"],
        "LOC-202": tenants["ORG-B"],
    }

    counts = dict((await ads.db.execute(select(Event.source_system, func.count()).group_by(Event.source_system))).all())
    assert counts == {SourceSystem.SYSTEM_A: 120, SourceSystem.SYSTEM_B: 121}

    # Fail-closed: nothing is visible to any tenant until reconciliation assigns it.
    assert (await ads.db.execute(select(func.count()).where(Event.tenant_id.is_not(None)))).scalar() == 0


async def test_system_b_dirty_values_are_normalized_not_lost(ads, publisher, file_store):
    await load_dataset(ads, publisher, file_store)

    for entry_id, expected_key in [
        ("ENT/2026/4034", "REC-1034"),
        ("ENT/2026/4070", "REC-1070"),
        ("ENT/2026/4112", "REC-1112"),
    ]:
        e = await event(ads, SourceSystem.SYSTEM_B, entry_id)
        assert (e.match_key, e.match_key_method) == (expected_key, MatchKeyMethod.NORMALIZED)

    lakh = await event(ads, SourceSystem.SYSTEM_B, "ENT/2026/4064")
    assert lakh.amount == Decimal("125400.00")
    assert lakh.raw_row["value"] == "1,25,400.00"

    blank = await event(ads, SourceSystem.SYSTEM_B, "ENT/2026/4050")
    assert blank.amount is None  # blank is missing, never 0

    orphan = await event(ads, SourceSystem.SYSTEM_B, "ENT/2026/4901")
    assert orphan.match_key == "REC-1999"


async def test_system_a_mapping(ads, publisher, file_store):
    await load_dataset(ads, publisher, file_store)
    a = await event(ads, SourceSystem.SYSTEM_A, "REC-1019")
    assert (a.match_key, a.amount, a.status) == ("REC-1019", Decimal("57092.35"), "VOIDED")
    assert a.attributes == {
        "base_value": "44603.40",
        "adjustment": "12488.95",
        "category_code": "CAT-04",
        "actor_id": "USR-22",
    }
    assert (await event(ads, SourceSystem.SYSTEM_A, "REC-1050")).attributes["actor_id"] is None


# --- job lifecycle ------------------------------------------------------------------------


async def test_submit_queues_message(ads, publisher, file_store):
    job = await submit(ads, publisher, file_store, DatasetType.LOCATIONS, dataset("locations.csv"))
    assert job.status == IngestionJobStatus.QUEUED
    assert job.file_uri == f"local://locations/{job.id}/file.csv"
    assert file_store.path_for(job.file_uri).exists()
    assert publisher.published == [(settings.INGESTION_MAIN_QUEUE, {"ingestion_job_id": str(job.id)}, {})]


async def test_same_file_twice_is_skipped_and_not_queued(ads, publisher, file_store):
    first = await ingest(ads, publisher, file_store, DatasetType.LOCATIONS, dataset("locations.csv"))
    publisher.published.clear()

    second = await submit(ads, publisher, file_store, DatasetType.LOCATIONS, dataset("locations.csv"))
    assert second.status == IngestionJobStatus.SKIPPED_DUPLICATE
    assert second.duplicate_of_job_id == first.id
    assert publisher.published == []


async def test_duplicate_queued_before_first_finished_is_skipped_by_consumer(ads, publisher, file_store):
    a = await submit(ads, publisher, file_store, DatasetType.LOCATIONS, dataset("locations.csv"))
    b = await submit(ads, publisher, file_store, DatasetType.LOCATIONS, dataset("locations.csv"))
    assert (a.status, b.status) == (IngestionJobStatus.QUEUED, IngestionJobStatus.QUEUED)

    assert (await run(ads, a.id, file_store)).status == IngestionJobStatus.SUCCEEDED
    b = await run(ads, b.id, file_store)
    assert (b.status, b.duplicate_of_job_id) == (IngestionJobStatus.SKIPPED_DUPLICATE, a.id)


async def test_redelivered_message_does_not_rerun_finished_job(ads, publisher, file_store):
    job = await ingest(ads, publisher, file_store, DatasetType.LOCATIONS, dataset("locations.csv"))
    finished_at = job.finished_at
    again = await run(ads, job.id, file_store)
    assert (again.status, again.finished_at) == (IngestionJobStatus.SUCCEEDED, finished_at)


async def test_enqueue_failure_marks_job_failed(ads, file_store):
    with pytest.raises(EnqueueError):
        await submit(ads, FakePublisher(fail=True), file_store, DatasetType.LOCATIONS, dataset("locations.csv"))
    job = (await ads.db.execute(select(IngestionJob))).scalar_one()
    assert job.status == IngestionJobStatus.FAILED
    assert "could not enqueue" in (job.error_message or "")


# --- rejections ---------------------------------------------------------------------------


async def test_events_before_locations_are_all_rejected(ads, publisher, file_store):
    job = await ingest(ads, publisher, file_store, DatasetType.SYSTEM_A_EVENTS, dataset("system_a.csv"))
    assert job.status == IngestionJobStatus.FAILED
    assert job.error_message == "all rows rejected"
    assert job.rows_rejected == 120
    assert {r["reason"] for r in job.rejected_row_details or []} == {"UNKNOWN_LOCATION"}


async def test_missing_columns_fail_the_file(ads, publisher, file_store):
    job = await ingest(ads, publisher, file_store, DatasetType.LOCATIONS, b"location_id,name\nLOC-1,x\n")
    assert job.status == IngestionJobStatus.FAILED
    assert "missing columns" in (job.error_message or "")


async def test_bad_rows_give_partial_success_with_reasons(ads, publisher, file_store):
    await ingest(ads, publisher, file_store, DatasetType.LOCATIONS, dataset("locations.csv"))
    data = (
        b"entry_id,record_ref,location_id,recorded_on,value,label\r\n"
        b"ENT-1,REC-1,LOC-101,2026-03-01,10.00,ok\r\n"
        b"ENT-2,REC-2,LOC-999,2026-03-01,10.00,unknown location\r\n"
        b"ENT-3,REC-3,LOC-101,31/03/2026,10.00,bad date\r\n"
        b"ENT-4,REC-4,LOC-101,2026-03-01,12,34,too many columns\r\n"
    )
    job = await ingest(ads, publisher, file_store, DatasetType.SYSTEM_B_ENTRIES, data)
    assert job.status == IngestionJobStatus.PARTIALLY_SUCCEEDED
    assert (job.rows_received, job.rows_loaded, job.rows_rejected) == (4, 1, 3)
    assert {(r["row"], r["reason"]) for r in job.rejected_row_details or []} == {
        (3, "UNKNOWN_LOCATION"),
        (4, "VALIDATION_ERROR"),
        (5, "WRONG_COLUMN_COUNT"),
    }


async def test_in_file_duplicates(ads, publisher, file_store):
    await ingest(ads, publisher, file_store, DatasetType.LOCATIONS, dataset("locations.csv"))
    data = (
        b"entry_id,record_ref,location_id,recorded_on,value,label\n"
        b"ENT-1,REC-1,LOC-101,2026-03-01,10.00,x\n"
        b"ENT-1,REC-1,LOC-101,2026-03-01,10.00,x\n"  # identical -> keep first
        b"ENT-2,REC-2,LOC-101,2026-03-01,10.00,x\n"
        b"ENT-2,REC-2,LOC-101,2026-03-01,99.00,x\n"  # same key, different content -> reject both
    )
    job = await ingest(ads, publisher, file_store, DatasetType.SYSTEM_B_ENTRIES, data)
    assert {(r["row"], r["reason"]) for r in job.rejected_row_details or []} == {
        (3, "DUPLICATE_ROW"),
        (4, "CONFLICTING_DUPLICATE_KEY"),
        (5, "CONFLICTING_DUPLICATE_KEY"),
    }
    assert job.rows_loaded == 1


async def test_location_never_moves_to_another_tenant(ads, publisher, file_store):
    await ingest(ads, publisher, file_store, DatasetType.LOCATIONS, dataset("locations.csv"))
    job = await ingest(
        ads, publisher, file_store, DatasetType.LOCATIONS, b"location_id,org_id,location_name\nLOC-101,ORG-B,Moved\n"
    )
    assert job.status == IngestionJobStatus.FAILED
    assert (job.rejected_row_details or [{}])[0].get("reason") == "LOCATION_TENANT_CHANGED"
    loc = (await ads.db.execute(select(Location).where(Location.external_location_id == "LOC-101"))).scalar_one()
    org = (await ads.db.execute(select(Tenant.external_org_id).where(Tenant.id == loc.tenant_id))).scalar_one()
    assert org == "ORG-A"


async def test_reingesting_changed_file_updates_and_rehides_events(ads, publisher, file_store):
    await load_dataset(ads, publisher, file_store)
    e = await event(ads, SourceSystem.SYSTEM_B, "ENT/2026/4001")
    tenant_id = (await ads.db.execute(select(Tenant.id).limit(1))).scalar_one()
    e.tenant_id = tenant_id  # pretend reconciliation assigned it
    await ads.db.commit()

    data = (
        b"entry_id,record_ref,location_id,recorded_on,value,label\nENT/2026/4001,REC-1001,LOC-201,2026-04-03,1.00,x\n"
    )
    job = await ingest(ads, publisher, file_store, DatasetType.SYSTEM_B_ENTRIES, data, "system_b_fix.csv")
    assert job.status == IngestionJobStatus.SUCCEEDED
    job_id = job.id

    ads.db.expire_all()
    e = await event(ads, SourceSystem.SYSTEM_B, "ENT/2026/4001")
    assert (e.amount, e.tenant_id, e.ingestion_job_id) == (Decimal("1.00"), None, job_id)


def test_file_uri_cannot_escape_data_dir(file_store):
    with pytest.raises(ValueError):
        file_store.path_for("local://../../etc/passwd")
    with pytest.raises(ValueError):
        file_store.path_for("file:///etc/passwd")


async def test_missing_file_fails_without_retry(ads, publisher, file_store):
    job = await submit(ads, publisher, file_store, DatasetType.LOCATIONS, dataset("locations.csv"))
    file_store.path_for(job.file_uri).unlink()
    job = await run(ads, job.id, file_store)
    assert job.status == IngestionJobStatus.FAILED
    assert "cannot open" in (job.error_message or "")


async def test_non_utf8_file_fails(ads, publisher, file_store):
    job = await ingest(
        ads, publisher, file_store, DatasetType.LOCATIONS, b"location_id,org_id,location_name\n\xff\xfe,x,y\n"
    )
    assert job.status == IngestionJobStatus.FAILED
    assert "UnicodeDecodeError" in (job.error_message or "")


async def test_concurrent_load_of_same_file_rolls_back_and_skips(ads, publisher, file_store, monkeypatch):
    """Both jobs pass the duplicate pre-check (race); the partial unique index stops the second."""
    a = await submit(ads, publisher, file_store, DatasetType.SYSTEM_A_EVENTS, dataset("system_a.csv"))
    b = await submit(ads, publisher, file_store, DatasetType.SYSTEM_A_EVENTS, dataset("system_a.csv"))
    await ingest(ads, publisher, file_store, DatasetType.LOCATIONS, dataset("locations.csv"))
    assert (await run(ads, a.id, file_store)).status == IngestionJobStatus.SUCCEEDED

    real = ingestion_job_repository.get_loaded_by_hash
    calls = {"n": 0}

    async def miss_first_check(*args, **kwargs):
        calls["n"] += 1
        return None if calls["n"] == 1 else await real(*args, **kwargs)

    monkeypatch.setattr(ingestion_job_repository, "get_loaded_by_hash", miss_first_check)
    b = await run(ads, b.id, file_store)
    assert (b.status, b.duplicate_of_job_id) == (IngestionJobStatus.SKIPPED_DUPLICATE, a.id)
    # b's writes were rolled back: events still belong to job a.
    job_ids = (await ads.db.execute(select(Event.ingestion_job_id).distinct())).scalars().all()
    assert job_ids == [a.id]


async def test_large_file_is_loaded_in_batches(ads, publisher, file_store, monkeypatch):
    """2,500 rows -> 3 upsert statements (1000 + 1000 + 500), all committed together."""
    from app.db.repositories import event as event_repo_module

    await ingest(ads, publisher, file_store, DatasetType.LOCATIONS, dataset("locations.csv"))
    header = b"entry_id,record_ref,location_id,recorded_on,value,label\n"
    rows = b"".join(f"ENT-{i},REC-{i},LOC-101,2026-03-01,{i}.00,x\n".encode() for i in range(2500))

    statements = []
    real_execute = ads.db.execute

    async def counting_execute(stmt, *args, **kwargs):
        if getattr(stmt, "table", None) is not None and stmt.table.name == "event":
            statements.append(len(stmt._multi_values[0]))
        return await real_execute(stmt, *args, **kwargs)

    monkeypatch.setattr(ads.db, "execute", counting_execute)
    job = await ingest(ads, publisher, file_store, DatasetType.SYSTEM_B_ENTRIES, header + rows)
    monkeypatch.undo()

    assert event_repo_module.UPSERT_BATCH_SIZE == 1000
    assert statements == [1000, 1000, 500]
    assert (job.status, job.rows_loaded) == (IngestionJobStatus.SUCCEEDED, 2500)
    assert (await ads.db.execute(select(func.count()).select_from(Event))).scalar() == 2500


async def test_storage_failure_raises_clear_error_and_creates_no_job(ads, publisher, tmp_path):
    from app.ingestion.storage import LocalFileStore
    from app.utils.error import FileStorageError

    read_only = tmp_path / "ro"
    read_only.mkdir()
    read_only.chmod(0o500)  # no write permission
    try:
        with pytest.raises(FileStorageError):
            await submit(ads, publisher, LocalFileStore(root=str(read_only)), DatasetType.LOCATIONS, b"x")
    finally:
        read_only.chmod(0o700)
    assert (await ads.db.execute(select(func.count()).select_from(IngestionJob))).scalar() == 0
    assert publisher.published == []
