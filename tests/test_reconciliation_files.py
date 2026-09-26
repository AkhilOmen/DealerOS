"""The code slice: CSVs in -> reconciled.csv + exceptions.csv out, no database."""

import csv
import json
import re
from pathlib import Path

import pytest

from app.db.enums import DiscrepancyType
from app.reconciliation.files import run
from app.reconciliation.taxonomy import REASONS, Kind, reason
from tests.conftest import DATASET
from tests.test_reconciliation import GOLDEN

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def output(tmp_path):
    reconciled, exceptions = run(
        DATASET / "locations.csv", DATASET / "system_a.csv", DATASET / "system_b.csv", tmp_path
    )
    return {r["match_key"]: r for r in reconciled}, exceptions, tmp_path


def test_exceptions_are_the_golden_set(output):
    _, exceptions, _ = output
    assert {(e["org"] or None, e["match_key"], DiscrepancyType(e["reason_code"])) for e in exceptions} == GOLDEN


def test_every_record_gets_a_canonical_row(output):
    records, _, _ = output
    assert len(records) == 121  # 120 System A records + the orphan REC-1999
    statuses = [r["status"] for r in records.values()]
    assert (statuses.count("RECONCILED"), statuses.count("EXCEPTION"), statuses.count("HIDDEN")) == (109, 11, 1)


@pytest.mark.parametrize(
    ("key", "status", "notes"),
    [
        ("REC-1034", "RECONCILED", "REF_NORMALIZED"),  # 'rec1034'
        ("REC-1070", "RECONCILED", "REF_NORMALIZED"),  # ' REC - 1070 '
        ("REC-1112", "RECONCILED", "REF_NORMALIZED"),  # '1112'
        ("REC-1055", "RECONCILED", "SPLIT_ENTRIES_SUMMED"),  # two entries summing to A's total
        ("REC-1064", "EXCEPTION", "AMOUNT_REFORMATTED"),  # '1,25,400.00' read correctly, still wrong
        ("REC-1050", "EXCEPTION", "SOURCE_FIELD_BLANK:actor_id"),
    ],
)
def test_interpretations_are_visible_not_silent(output, key, status, notes):
    records, _, _ = output
    assert (records[key]["status"], records[key]["notes"]) == (status, notes)


def test_conflicted_record_belongs_to_no_org(output):
    records, exceptions, _ = output
    assert (records["REC-1077"]["org"], records["REC-1077"]["status"]) == ("", "HIDDEN")
    (conflict,) = [e for e in exceptions if e["reason_code"] == "TENANT_CONFLICT"]
    assert conflict["severity"] == "CRITICAL" and conflict["org"] == ""


def test_exceptions_are_actionable_and_ordered(output):
    _, exceptions, _ = output
    for e in exceptions:
        assert e["meaning"] and e["action"] and json.loads(e["evidence"])
    assert exceptions[0]["severity"] == "CRITICAL"  # most serious first
    value = next(e for e in exceptions if e["match_key"] == "REC-1003")
    assert json.loads(value["evidence"]) == {
        "values": {"SYSTEM_A": "121388.01", "SYSTEM_B": "94834.38"},
        "b_entry_count": 1,
        "b_equals_a_base_value": True,
    }


def test_files_are_written(output):
    _, _, out = output
    assert len(list(csv.DictReader((out / "reconciled.csv").open()))) == 121
    assert len(list(csv.DictReader((out / "exceptions.csv").open()))) == 12


def test_rows_that_cannot_be_used_become_input_exceptions(tmp_path):
    (tmp_path / "locations.csv").write_text("location_id,org_id,location_name\nLOC-101,ORG-A,x\n")
    (tmp_path / "a.csv").write_text(
        "record_id,location_id,event_date,category_code,actor_id,base_value,adjustment,total_value,state\n"
        "REC-1,LOC-101,2026-03-01,CAT-01,U,1.00,0.00,1.00,CONFIRMED\n"
        "REC-2,LOC-999,2026-03-01,CAT-01,U,1.00,0.00,1.00,CONFIRMED\n"
        "REC-3,LOC-101,03/01/2026,CAT-01,U,1.00,0.00,1.00,CONFIRMED\n"
    )
    (tmp_path / "b.csv").write_text(
        "entry_id,record_ref,location_id,recorded_on,value,label\n"
        "E1,REC-1,LOC-101,2026-03-01,1.00,x\n"
        "E1,REC-1,LOC-101,2026-03-01,2.00,x\n"
    )
    records, exceptions = run(tmp_path / "locations.csv", tmp_path / "a.csv", tmp_path / "b.csv", tmp_path / "out")
    by_source = {e["source"]: e["reason_code"] for e in exceptions if e["kind"] == "INPUT"}
    assert by_source == {
        "a.csv:3": "UNKNOWN_LOCATION",
        "a.csv:4": "VALIDATION_ERROR",
        "b.csv:2": "CONFLICTING_DUPLICATE_KEY",
        "b.csv:3": "CONFLICTING_DUPLICATE_KEY",
    }
    # both conflicting B rows were refused, so REC-1 now has no B entry
    assert [(r["match_key"], r["exception_codes"]) for r in records] == [("REC-1", "MISSING_IN_B")]


def test_every_reason_code_in_the_code_is_catalogued():
    used = set(DiscrepancyType.__members__)
    for path in (ROOT / "app").rglob("*.py"):
        used |= set(
            re.findall(
                r'(?:reject\(\s*[^,]+,\s*[^,]+,|RowRejection\(|InputReject\([^,]+,[^,]+,)\s*"([A-Z_]+)"',
                path.read_text(),
            )
        )
        used |= set(
            re.findall(
                r'"(REF_NORMALIZED|AMOUNT_REFORMATTED|SPLIT_ENTRIES_SUMMED|SOURCE_FIELD_BLANK)', path.read_text()
            )
        )
    missing = used - set(REASONS)
    assert not missing, f"reason codes without a catalogue entry: {missing}"
    assert all(reason(code).kind == Kind.DISAGREEMENT for code in DiscrepancyType.__members__)
