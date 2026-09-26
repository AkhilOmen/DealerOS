import uuid
from datetime import date
from decimal import Decimal

from app.db.enums import DiscrepancyField, DiscrepancyType, SourceSystem
from app.reconciliation.engine import EventView, reconcile

ORG_A, ORG_B = uuid.uuid4(), uuid.uuid4()
LOC_101, LOC_102, LOC_201 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
LOCATIONS = {LOC_101: ("LOC-101", ORG_A), LOC_102: ("LOC-102", ORG_A), LOC_201: ("LOC-201", ORG_B)}
D = date(2026, 3, 1)


def a(key, amount, *, loc=LOC_101, day=D, status="CONFIRMED", base=None, ext=None):
    code, tenant = LOCATIONS[loc]
    return EventView(
        uuid.uuid4(), SourceSystem.SYSTEM_A, ext or key, key, loc, code, tenant, day, Decimal(amount), status,
        Decimal(base) if base else None,
    )


def b(key, amount, ext, *, loc=LOC_101, day=D):
    code, tenant = LOCATIONS[loc]
    return EventView(
        uuid.uuid4(), SourceSystem.SYSTEM_B, ext, key, loc, code, tenant, day,
        Decimal(amount) if amount is not None else None, None,
    )


def kinds(result):
    return {(d.match_key, d.type) for d in result.discrepancies}


def test_exact_match_has_no_discrepancy_and_is_visible_to_its_tenant():
    events = [a("REC-1", "100.00"), b("REC-1", "100.00", "E1")]
    result = reconcile(events)
    assert result.discrepancies == []
    assert set(result.tenant_by_event.values()) == {ORG_A}


def test_split_entries_are_summed():
    result = reconcile([a("REC-1", "100.00"), b("REC-1", "60.00", "E1"), b("REC-1", "40.00", "E2")])
    assert result.discrepancies == []


def test_split_that_does_not_add_up_is_a_value_mismatch():
    result = reconcile([a("REC-1", "100.00"), b("REC-1", "60.00", "E1"), b("REC-1", "30.00", "E2")])
    (d,) = result.discrepancies
    assert (d.type, d.values_by_system["SYSTEM_B"], d.details["b_entry_count"]) == (
        DiscrepancyType.VALUE_MISMATCH, "90.00", 2
    )


def test_identical_entries_are_a_duplicate_and_counted_once():
    result = reconcile([a("REC-1", "100.00"), b("REC-1", "100.00", "E1"), b("REC-1", "100.00", "E2")])
    assert kinds(result) == {("REC-1", DiscrepancyType.DUPLICATE_IN_B)}  # no VALUE_MISMATCH: 100 != 200


def test_b_recorded_before_adjustment_is_flagged_with_a_hint():
    result = reconcile([a("REC-1", "128.00", base="100.00"), b("REC-1", "100.00", "E1")])
    (d,) = result.discrepancies
    assert d.type == DiscrepancyType.VALUE_MISMATCH and d.details["b_equals_a_base_value"] is True


def test_missing_and_orphan():
    result = reconcile([a("REC-1", "1.00"), b("REC-9", "5.00", "E9")])
    assert kinds(result) == {("REC-1", DiscrepancyType.MISSING_IN_B), ("REC-9", DiscrepancyType.ORPHAN_IN_B)}
    orphan = next(d for d in result.discrepancies if d.type == DiscrepancyType.ORPHAN_IN_B)
    assert orphan.tenant_id == ORG_A  # its own location's tenant


def test_blank_b_value_is_missing_value_not_zero():
    result = reconcile([a("REC-1", "100.00"), b("REC-1", None, "E1")])
    assert kinds(result) == {("REC-1", DiscrepancyType.MISSING_VALUE)}


def test_date_location_and_status_checks():
    result = reconcile(
        [
            a("REC-1", "1.00"), b("REC-1", "1.00", "E1", day=date(2026, 3, 3)),
            a("REC-2", "1.00"), b("REC-2", "1.00", "E2", loc=LOC_102),
            a("REC-3", "1.00", status="VOIDED"), b("REC-3", "1.00", "E3"),
        ]
    )
    assert kinds(result) == {
        ("REC-1", DiscrepancyType.DATE_MISMATCH),
        ("REC-2", DiscrepancyType.LOCATION_MISMATCH),
        ("REC-3", DiscrepancyType.STATUS_MISMATCH),
    }


def test_cross_tenant_group_is_hidden_from_everyone_and_not_compared():
    events = [a("REC-1", "1.00", loc=LOC_102), b("REC-1", "999.00", "E1", loc=LOC_201)]
    result = reconcile(events)
    (d,) = result.discrepancies
    assert (d.type, d.field, d.tenant_id) == (DiscrepancyType.TENANT_CONFLICT, DiscrepancyField.LOCATION, None)
    assert set(result.tenant_by_event.values()) == {None}
    assert d.values_by_system == {"SYSTEM_A": "LOC-102", "SYSTEM_B": "LOC-201"}


def test_unparsable_ref_becomes_its_own_orphan():
    result = reconcile([b(None, "5.00", "E1"), b(None, "6.00", "E2")])
    assert {d.match_key for d in result.discrepancies} == {"UNPARSABLE:SYSTEM_B:E1", "UNPARSABLE:SYSTEM_B:E2"}


def test_two_a_records_on_one_key_are_not_guessed():
    result = reconcile([a("REC-1", "1.00", ext="REC-1"), a("REC-1", "2.00", ext="rec1"), b("REC-1", "1.00", "E1")])
    assert kinds(result) == {("REC-1", DiscrepancyType.DUPLICATE_IN_A)}
