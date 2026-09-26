from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.db.enums import MatchKeyMethod
from app.ingestion.normalizers import normalize_record_ref, parse_amount, parse_iso_date
from app.schemas.rows import SystemARow, SystemBRow


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("REC-1001", ("REC-1001", MatchKeyMethod.EXACT)),
        ("rec1034", ("REC-1034", MatchKeyMethod.NORMALIZED)),
        (" REC - 1070 ", ("REC-1070", MatchKeyMethod.NORMALIZED)),
        ("1112", ("REC-1112", MatchKeyMethod.NORMALIZED)),
        ("", (None, MatchKeyMethod.UNPARSABLE)),
        (None, (None, MatchKeyMethod.UNPARSABLE)),
        ("XYZ-12", (None, MatchKeyMethod.UNPARSABLE)),
    ],
)
def test_normalize_record_ref(raw, expected):
    assert normalize_record_ref(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("88969.92", Decimal("88969.92")),
        ("1,25,400.00", Decimal("125400.00")),  # Indian lakh grouping
        ("1,234,567.89", Decimal("1234567.89")),
        ("", None),
        ("  ", None),
        (None, None),
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", ["12,34", "1.2.3", "abc", "1,2345.00", "10.123"])
def test_parse_amount_rejects_garbage(raw):
    with pytest.raises(ValueError):
        parse_amount(raw)


def test_parse_iso_date():
    assert parse_iso_date("2026-03-31") == date(2026, 3, 31)
    assert parse_iso_date("") is None
    with pytest.raises(ValueError):
        parse_iso_date("31/03/2026")


def test_system_b_row_blank_value_is_none_not_zero():
    row = SystemBRow.model_validate(
        {
            "entry_id": "ENT/2026/4050",
            "record_ref": "REC-1050",
            "location_id": "LOC-202",
            "recorded_on": "2026-03-20",
            "value": "",
            "label": "Entry for CAT-03",
        }
    )
    assert row.value is None


def test_system_b_row_keeps_raw_record_ref():
    row = SystemBRow.model_validate(
        {
            "entry_id": "ENT/2026/4070",
            "record_ref": " REC - 1070 ",
            "location_id": "LOC-202",
            "recorded_on": "2026-03-18",
            "value": "1608.95",
            "label": "Entry for CAT-05",
        }
    )
    assert row.record_ref == " REC - 1070 "


def test_system_a_row_requires_total_value():
    with pytest.raises(ValidationError):
        SystemARow.model_validate(
            {
                "record_id": "REC-1",
                "location_id": "LOC-101",
                "event_date": "2026-03-01",
                "total_value": "",
                "state": "CONFIRMED",
            }
        )
