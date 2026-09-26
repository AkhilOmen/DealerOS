"""The grounding contract's worked example (docs/GROUNDING.md):

    "What was ORG-A's total amount in March?"

A naive implementation sums one system (or both) and states a confident number. Our answer states
only numbers derived from the returned rows: per-system counts, the disputed records by id, and a
total over the records both systems agree on.
"""

import csv
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import Tenant
from app.query import service
from app.reconciliation.service import run_reconciliation
from tests.conftest import DATASET
from tests.test_ingestion import load_dataset
from tests.test_queries import FakePlanner, planner

MARCH = {"date_from": "2026-03-01", "date_to": "2026-03-31"}


def _csv(name):
    return list(csv.DictReader((DATASET / name).open(encoding="utf-8-sig", newline="")))


def naive_answers() -> dict:
    orgs = {r["location_id"]: r["org_id"] for r in _csv("locations.csv")}
    a = [
        r
        for r in _csv("system_a.csv")
        if orgs[r["location_id"]] == "ORG-A" and "2026-03-01" <= r["event_date"] <= "2026-03-31"
    ]
    b = [
        r
        for r in _csv("system_b.csv")
        if orgs.get(r["location_id"]) == "ORG-A" and "2026-03-01" <= r["recorded_on"] <= "2026-03-31"
    ]

    def parse(v):
        try:
            return Decimal(v)
        except ArithmeticError:
            return Decimal(0)  # the naive parser silently drops '1,25,400.00'

    return {
        "sum_a": sum(Decimal(r["total_value"]) for r in a),
        "sum_b": sum(parse(r["value"]) for r in b),
        "count": len(a) + len(b),
        "a_rows": {r["record_id"]: Decimal(r["total_value"]) for r in a},
    }


@pytest.fixture
async def answer(ads, publisher, file_store):
    await load_dataset(ads, publisher, file_store)
    await run_reconciliation(ads)
    assert (await ads.db.execute(select(Tenant.id).where(Tenant.external_org_id == "ORG-A"))).scalar_one()
    return await service.answer_question(
        ads, planner(orgs=["ORG-A"], dataset="events", filters=MARCH), "What was ORG-A's total amount in March?", "test"
    )


async def test_naive_answers_are_confident_and_wrong():
    naive = naive_answers()
    assert (naive["sum_a"], naive["sum_b"], naive["count"]) == (Decimal("6312179.31"), Decimal("6335172.12"), 130)


async def test_grounded_answer(answer):
    (result,) = answer.results
    s = result.summary
    naive = naive_answers()

    # The disputed March records are named, not blended into a total.
    assert s["disputed_records"] == ["REC-1027", "REC-1042", "REC-1088", "REC-1999"]
    # REC-1077 (tenant conflict) is not ORG-A's to report: it is absent, not counted.
    assert "REC-1077" not in {r["match_key"] for r in result.rows}
    assert (s["system_a_records"], s["system_b_entries"]) == (63, 66)

    # The total covers only records both systems agree on, and can be recomputed from the rows.
    excluded = {"REC-1077", "REC-1027", "REC-1042", "REC-1088"}
    expected_total = sum(v for k, v in naive["a_rows"].items() if k not in excluded)
    assert Decimal(s["agreed_amount_total"]) == expected_total
    assert s["agreed_records"] == 60
    agreed_rows = [r for r in result.rows if r["source_system"] == "SYSTEM_A" and not r["open_discrepancies"]]
    assert sum(Decimal(r["amount"]) for r in agreed_rows) == expected_total

    assert answer.answer == (
        "Events between 2026-03-01 and 2026-03-31. ORG-A: 63 System A records and "
        f"{s['system_b_entries']} System B entries; 4 records disputed (REC-1027, REC-1042, REC-1088, REC-1999); "
        f"agreed total {expected_total:.2f} over 60 undisputed records."
    )


async def test_no_totals_when_the_result_is_incomplete(monkeypatch, ads, publisher, file_store):
    from app.query import service as query_service

    monkeypatch.setattr(query_service, "MAX_ROWS", 10)
    await load_dataset(ads, publisher, file_store)
    await run_reconciliation(ads)
    result = (await query_service.answer_question(ads, planner(orgs=["ORG-A"]), "events for ORG-A", "test")).results[0]
    assert result.truncated and result.summary["agreed_amount_total"] is None
    assert result.summary["complete"] is False


def test_the_model_cannot_state_a_number():
    from app.query.plan import Filters, PlannerOutput

    numeric = {"int", "number"}
    for model in (PlannerOutput, Filters):
        for name, prop in model.model_json_schema()["properties"].items():
            types = {prop.get("type")} | {t.get("type") for t in prop.get("anyOf", [])}
            assert not types & numeric, f"{model.__name__}.{name} would let the model emit a number"


__all__ = ["FakePlanner"]
