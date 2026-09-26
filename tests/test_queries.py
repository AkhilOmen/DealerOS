from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.db.enums import QueryStatus
from app.db.models import Event, Location, QueryLog, Tenant
from app.query import service
from app.query.plan import Dataset, PlannerOutput
from app.query.planner import QueryPlanner
from app.reconciliation.service import run_reconciliation
from app.utils.error import (
    OrgNotFoundError,
    PlannerError,
    PlannerNotConfiguredError,
    UnsupportedQuestionError,
)
from tests.test_ingestion import load_dataset


class FakePlanner(QueryPlanner):
    model = "fake"

    def __init__(self, output: PlannerOutput | None = None, error: Exception | None = None):
        self.output, self.error = output, error

    async def plan(self, question, today):
        if self.error:
            raise self.error
        assert self.output is not None
        return self.output


def planner(**output) -> FakePlanner:
    return FakePlanner(PlannerOutput.model_validate(output))


@pytest.fixture
async def dataset(ads, publisher, file_store) -> dict:
    await load_dataset(ads, publisher, file_store)
    await run_reconciliation(ads)
    return dict((await ads.db.execute(select(Tenant.external_org_id, Tenant.id))).all())


async def ask(ads, p, question="q"):
    return await service.answer_question(ads, p, question, asked_by="test")


async def last_log(ads) -> QueryLog:
    ads.db.expire_all()
    return (await ads.db.execute(select(QueryLog).order_by(QueryLog.created_at.desc()).limit(1))).scalar_one()


# --- answers --------------------------------------------------------------------------------


async def test_events_for_an_org_in_a_date_range(ads, dataset):
    p = planner(orgs=["ORG-A"], dataset="events", filters={"date_from": "2026-03-01", "date_to": "2026-03-15"})
    answer = await ask(ads, p, "What are the events for ORG-A between 1 and 15 March?")

    expected = (
        await ads.db.execute(
            select(func.count())
            .select_from(Event)
            .where(Event.tenant_id == dataset["ORG-A"], Event.event_date.between(date(2026, 3, 1), date(2026, 3, 15)))
        )
    ).scalar_one()
    org_a_locations = set(
        (
            await ads.db.execute(select(Location.external_location_id).where(Location.tenant_id == dataset["ORG-A"]))
        ).scalars()
    )

    (result,) = answer.results
    assert result.org == "ORG-A" and len(result.rows) == expected > 0
    assert {r["location"] for r in result.rows} <= org_a_locations
    assert all("2026-03-01" <= r["event_date"] <= "2026-03-15" for r in result.rows)
    s = result.summary
    assert s["system_a_records"] + s["system_b_entries"] == expected  # counted per system, never blended
    assert answer.answer.startswith(
        f"Events between 2026-03-01 and 2026-03-15. ORG-A: {s['system_a_records']} System A records and "
        f"{s['system_b_entries']} System B entries"
    )


async def test_org_spelling_is_normalized(ads, dataset):
    (result,) = (await ask(ads, planner(orgs=["org_b"]))).results
    assert result.org == "ORG-B" and {r["location"] for r in result.rows} == {"LOC-201", "LOC-202"}


async def test_discrepancies_for_an_org(ads, dataset):
    answer = await ask(
        ads, planner(orgs=["ORG-B"], dataset="discrepancies", filters={"discrepancy_type": "VALUE_MISMATCH"})
    )
    (result,) = answer.results
    assert [r["match_key"] for r in result.rows] == ["REC-1003"]
    assert result.rows[0]["values_by_system"] == {"SYSTEM_A": "121388.01", "SYSTEM_B": "94834.38"}


async def test_another_orgs_location_or_the_conflicted_record_returns_nothing(ads, dataset):
    assert (await ask(ads, planner(orgs=["ORG-A"], filters={"location": "LOC-201"}))).results[0].rows == []
    all_orgs = await ask(ads, planner(orgs=[], filters={"match_key": "REC-1077"}))
    assert [(r.org, r.rows) for r in all_orgs.results] == [("ORG-A", []), ("ORG-B", [])]


async def test_answered_query_is_logged(ads, dataset):
    await ask(ads, planner(orgs=["ORG-A"], filters={"match_key": "REC-1001"}), "REC-1001 for ORG-A")
    log = await last_log(ads)
    assert (log.status, log.external_org_id, log.tenant_id, log.model) == (
        QueryStatus.ANSWERED,
        "ORG-A",
        dataset["ORG-A"],
        "fake",
    )
    assert log.plan is not None and log.plan["orgs"] == ["ORG-A"]


# --- rejections -----------------------------------------------------------------------------


VALUE_MISMATCHES = {"ORG-A": ["REC-1027", "REC-1064", "REC-1088"], "ORG-B": ["REC-1003"]}


@pytest.mark.parametrize("orgs", [[], ["ORG-A", "ORG-B"], ["org b", "ORG-A"]])
async def test_no_org_or_several_orgs_are_answered_per_org(ads, dataset, orgs):
    p = planner(orgs=orgs, dataset="discrepancies", filters={"discrepancy_type": "VALUE_MISMATCH"})
    answer = await ask(ads, p, "give me the value mismatches between ORG-A and ORG-B")

    # One result per org, each fetched separately; rows are never mixed.
    assert {r.org: [row["match_key"] for row in r.rows] for r in answer.results} == VALUE_MISMATCHES
    assert answer.answer == (
        "Discrepancies. ORG-A: 3 VALUE_MISMATCH discrepancies. ORG-B: 1 VALUE_MISMATCH discrepancy. "
        "Discrepancies are between System A and System B within each org."
    )
    assert answer.results[0].rows[0]["meaning"] and answer.results[0].rows[0]["action"]
    log = await last_log(ads)
    assert (log.external_org_id, log.tenant_id, log.row_count) == ("ORG-A,ORG-B", None, 4)


async def test_unknown_org_is_rejected(ads, dataset):
    with pytest.raises(OrgNotFoundError, match="ORG-Z"):
        await ask(ads, planner(orgs=["ORG-A", "ORG-Z"]))
    assert (await last_log(ads)).status == QueryStatus.REJECTED


async def test_unsupported_question(ads, dataset):
    with pytest.raises(UnsupportedQuestionError, match="Weather"):
        await ask(ads, planner(unsupported_reason="Weather isn't in this data."))
    assert (await last_log(ads)).status == QueryStatus.UNSUPPORTED


async def test_planner_failure_and_missing_configuration(ads, dataset):
    with pytest.raises(PlannerError):
        await ask(ads, FakePlanner(error=PlannerError("timeout")))
    assert (await last_log(ads)).status == QueryStatus.FAILED
    with pytest.raises(PlannerNotConfiguredError):
        await ask(ads, None)


# --- provider adapters (no network) ---------------------------------------------------------


async def test_anthropic_planner(monkeypatch):
    from app.query.planners import anthropic as anthropic_planner

    p = anthropic_planner.AnthropicPlanner()
    good = PlannerOutput(orgs=["ORG-A"])
    for stop_reason, parsed, ok in (("end_turn", good, True), ("refusal", None, False), ("max_tokens", None, False)):

        async def fake_parse(_s=stop_reason, _p=parsed, **kwargs):
            assert kwargs["output_format"] is PlannerOutput
            return SimpleNamespace(stop_reason=_s, parsed_output=_p, model="m", _request_id="r")

        monkeypatch.setattr(p._client.beta.messages, "parse", fake_parse)
        if ok:
            assert await p.plan("q", date(2026, 9, 26)) == good
        else:
            with pytest.raises(PlannerError):
                await p.plan("q", date(2026, 9, 26))


async def test_openai_compatible_planner(monkeypatch):
    from app.query.planners import openai_compatible

    monkeypatch.setattr(openai_compatible.settings, "LLM_API_KEY", SecretStr("test-key"))
    monkeypatch.setattr(openai_compatible.settings, "LLM_BASE_URL", "http://localhost:1/v1")
    p = openai_compatible.OpenAICompatiblePlanner()

    def reply(content):
        async def create(**kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=content))]
            )

        return create

    monkeypatch.setattr(p._client.chat.completions, "create", reply('{"orgs": ["ORG-A"], "dataset": "discrepancies"}'))
    assert (await p.plan("q", date(2026, 9, 26))).dataset == Dataset.DISCREPANCIES

    monkeypatch.setattr(p._client.chat.completions, "create", reply('{"dataset": "weather"}'))
    with pytest.raises(PlannerError, match="invalid plan"):
        await p.plan("q", date(2026, 9, 26))
