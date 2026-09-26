import dataclasses
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.db.enums import DiscrepancyField, DiscrepancyType, SourceSystem

VOIDED = "VOIDED"
UNPARSABLE_KEY_PREFIX = "UNPARSABLE:"


@dataclass(frozen=True)
class EventView:
    id: uuid.UUID
    source_system: SourceSystem
    external_event_id: str
    match_key: str | None
    location_id: uuid.UUID
    location_code: str
    location_tenant_id: uuid.UUID
    event_date: date | None
    amount: Decimal | None
    status: str | None
    base_value: Decimal | None = None


@dataclass(frozen=True)
class DiscrepancyDraft:
    tenant_id: uuid.UUID | None
    match_key: str
    type: DiscrepancyType
    field: DiscrepancyField
    event_ids: list[uuid.UUID]
    values_by_system: dict[str, Any]
    details: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclass
class ReconciliationResult:
    tenant_by_event: dict[uuid.UUID, uuid.UUID | None] = dataclasses.field(default_factory=dict)
    discrepancies: list[DiscrepancyDraft] = dataclasses.field(default_factory=list)


def group_key(event: EventView) -> str:
    # Rows without a usable key can't match anything; each becomes its own group (an orphan).
    return event.match_key or f"{UNPARSABLE_KEY_PREFIX}{event.source_system.value}:{event.external_event_id}"


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def _one_or_many(values: list[Any]) -> Any:
    return values[0] if len(values) == 1 else values


def reconcile(events: list[EventView]) -> ReconciliationResult:
    groups: dict[str, list[EventView]] = defaultdict(list)
    for event in events:
        groups[group_key(event)].append(event)

    result = ReconciliationResult()
    for key in sorted(groups):
        _reconcile_group(key, groups[key], result)
    return result


def _reconcile_group(key: str, group: list[EventView], result: ReconciliationResult) -> None:
    a_events = sorted((e for e in group if e.source_system == SourceSystem.SYSTEM_A), key=lambda e: e.external_event_id)
    b_events = sorted((e for e in group if e.source_system == SourceSystem.SYSTEM_B), key=lambda e: e.external_event_id)
    event_ids = [e.id for e in (*a_events, *b_events)]

    def add(tenant_id, type_, field_, values, details=None, ids=None):
        result.discrepancies.append(
            DiscrepancyDraft(tenant_id, key, type_, field_, ids or event_ids, values, details or {})
        )

    # 1. Tenant: every event in the group must resolve to the same tenant, or nobody sees any of them.
    tenants = {e.location_tenant_id for e in group}
    if len(tenants) > 1:
        for e in group:
            result.tenant_by_event[e.id] = None
        add(
            None,
            DiscrepancyType.TENANT_CONFLICT,
            DiscrepancyField.LOCATION,
            {
                SourceSystem.SYSTEM_A.value: _one_or_many([e.location_code for e in a_events]) if a_events else None,
                SourceSystem.SYSTEM_B.value: _one_or_many([e.location_code for e in b_events]) if b_events else None,
            },
        )
        return

    tenant_id = tenants.pop()
    for e in group:
        result.tenant_by_event[e.id] = tenant_id

    # 2. Record-level checks.
    if len(a_events) > 1:
        add(
            tenant_id,
            DiscrepancyType.DUPLICATE_IN_A,
            DiscrepancyField.RECORD,
            {SourceSystem.SYSTEM_A.value: [e.external_event_id for e in a_events]},
            {"reason": "several System A records share this key; not compared"},
        )
        return

    if not a_events:
        add(
            tenant_id,
            DiscrepancyType.ORPHAN_IN_B,
            DiscrepancyField.RECORD,
            {
                SourceSystem.SYSTEM_A.value: None,
                SourceSystem.SYSTEM_B.value: _one_or_many([_money(e.amount) for e in b_events]),
            },
            {"b_entry_ids": [e.external_event_id for e in b_events]},
        )
        return

    a = a_events[0]
    if not b_events:
        add(
            tenant_id,
            DiscrepancyType.MISSING_IN_B,
            DiscrepancyField.RECORD,
            {SourceSystem.SYSTEM_A.value: _money(a.amount), SourceSystem.SYSTEM_B.value: None},
        )
        return

    # 3. Several B entries: identical ones are duplicates; different ones are a split (summed).
    distinct: dict[tuple, list[EventView]] = defaultdict(list)
    for b in b_events:
        distinct[(b.amount, b.event_date, b.location_id)].append(b)
    duplicates = [copies for copies in distinct.values() if len(copies) > 1]
    if duplicates:
        add(
            tenant_id,
            DiscrepancyType.DUPLICATE_IN_B,
            DiscrepancyField.RECORD,
            {
                SourceSystem.SYSTEM_A.value: _money(a.amount),
                SourceSystem.SYSTEM_B.value: [_money(e.amount) for e in b_events],
            },
            {"duplicate_entry_ids": [[e.external_event_id for e in copies] for copies in duplicates]},
        )
    entries = [copies[0] for copies in distinct.values()]

    # 4. Field comparisons.
    b_amounts = [e.amount for e in entries]
    if any(v is None for v in b_amounts):
        add(
            tenant_id,
            DiscrepancyType.MISSING_VALUE,
            DiscrepancyField.AMOUNT,
            {SourceSystem.SYSTEM_A.value: _money(a.amount), SourceSystem.SYSTEM_B.value: None},
        )
    else:
        b_total = sum(b_amounts, Decimal(0))  # type: ignore[arg-type]
        if b_total != a.amount:
            details: dict[str, Any] = {"b_entry_count": len(entries)}
            if a.base_value is not None and b_total == a.base_value:
                details["b_equals_a_base_value"] = True
            add(
                tenant_id,
                DiscrepancyType.VALUE_MISMATCH,
                DiscrepancyField.AMOUNT,
                {SourceSystem.SYSTEM_A.value: _money(a.amount), SourceSystem.SYSTEM_B.value: _money(b_total)},
                details,
            )

    b_dates = sorted({e.event_date for e in entries if e.event_date is not None})
    if any(d != a.event_date for d in b_dates):
        add(
            tenant_id,
            DiscrepancyType.DATE_MISMATCH,
            DiscrepancyField.EVENT_DATE,
            {
                SourceSystem.SYSTEM_A.value: a.event_date.isoformat() if a.event_date else None,
                SourceSystem.SYSTEM_B.value: _one_or_many([d.isoformat() for d in b_dates]),
            },
        )

    b_locations = sorted({e.location_code for e in entries})
    if any(code != a.location_code for code in b_locations):
        add(
            tenant_id,
            DiscrepancyType.LOCATION_MISMATCH,
            DiscrepancyField.LOCATION,
            {SourceSystem.SYSTEM_A.value: a.location_code, SourceSystem.SYSTEM_B.value: _one_or_many(b_locations)},
        )

    if (a.status or "").upper() == VOIDED and any(v for v in b_amounts):
        add(
            tenant_id,
            DiscrepancyType.STATUS_MISMATCH,
            DiscrepancyField.STATUS,
            {
                SourceSystem.SYSTEM_A.value: a.status,
                SourceSystem.SYSTEM_B.value: _money(sum((v for v in b_amounts if v is not None), Decimal(0))),
            },
            {"reason": "voided in System A but System B still has a value"},
        )
