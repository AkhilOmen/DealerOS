import csv
import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.ingestion.ingestors.base import BaseIngestor
from app.ingestion.ingestors.events import LOCATION_CODE, EventIngestor, SystemAIngestor, SystemBIngestor
from app.ingestion.ingestors.locations import LocationsIngestor
from app.reconciliation.engine import EventView, ReconciliationResult, reconcile
from app.reconciliation.notes import notes_for
from app.reconciliation.taxonomy import Kind, Severity, reason
from app.utils.error import RowRejection

_NS = uuid.UUID("5b0e1b6a-3c3c-4d2e-9a61-2f7c7c1f0d11")  # stable ids for file-based runs
_SEVERITY_ORDER = {s: i for i, s in enumerate(Severity)}


def _id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_NS, ":".join(parts))


@dataclass(frozen=True)
class InputReject:
    file: str
    row: int
    code: str
    detail: str
    raw: dict[str, Any]


@dataclass
class LoadedData:
    events: list[EventView] = field(default_factory=list)
    rejects: list[InputReject] = field(default_factory=list)
    org_by_tenant: dict[uuid.UUID, str] = field(default_factory=dict)


def _parse(ingestor: BaseIngestor, path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return ingestor.parse(fh)


def load(locations: Path, system_a: Path, system_b: Path) -> LoadedData:
    data = LoadedData()

    rows, result = _parse(LocationsIngestor(), locations)
    data.rejects += [
        InputReject(locations.name, r["row"], r["reason"], r["detail"], r["raw"]) for r in result.rejected_row_details
    ]
    org_by_location = {p.row.location_id: p.row.org_id for p in rows}
    data.org_by_tenant = {_id("org", org): org for org in org_by_location.values()}

    ingestor: EventIngestor
    for ingestor, path in ((SystemAIngestor(), system_a), (SystemBIngestor(), system_b)):
        rows, result = _parse(ingestor, path)
        data.rejects += [
            InputReject(path.name, r["row"], r["reason"], r["detail"], r["raw"]) for r in result.rejected_row_details
        ]
        for p in rows:
            try:
                values = ingestor.to_event(p.row)
            except RowRejection as ex:
                data.rejects.append(InputReject(path.name, p.row_number, ex.reason, ex.detail, p.raw))
                continue
            location = values[LOCATION_CODE]
            org = org_by_location.get(location)
            if org is None:
                data.rejects.append(
                    InputReject(path.name, p.row_number, "UNKNOWN_LOCATION", f"{location!r} is not in locations", p.raw)
                )
                continue

            system = ingestor.source_system
            base = values["attributes"].get("base_value")
            data.events.append(
                EventView(
                    id=_id(system.value, values["external_event_id"]),
                    source_system=system,
                    external_event_id=values["external_event_id"],
                    match_key=values["match_key"],
                    location_id=_id("location", location),
                    location_code=location,
                    location_tenant_id=_id("org", org),
                    event_date=values["event_date"],
                    amount=values["amount"],
                    status=values["status"],
                    base_value=Decimal(base) if base else None,
                    notes=notes_for(system, values["match_key_method"], p.raw),
                )
            )
    return data


# --- output ---------------------------------------------------------------------------------

RECONCILED_COLUMNS = [
    "org",
    "match_key",
    "status",
    "a_record_ids",
    "b_entry_ids",
    "amount_a",
    "amount_b",
    "event_date",
    "location",
    "exception_codes",
    "notes",
]
EXCEPTION_COLUMNS = ["severity", "kind", "reason_code", "org", "match_key", "source", "meaning", "action", "evidence"]


def _money(value: Decimal | None) -> str:
    return "" if value is None else f"{value:.2f}"


def reconciled_rows(result: ReconciliationResult, org_by_tenant: dict[uuid.UUID, str]) -> list[dict[str, str]]:
    return [
        {
            "org": org_by_tenant.get(r.tenant_id, "") if r.tenant_id else "",  # blank = hidden from every org
            "match_key": r.match_key,
            "status": r.status.value,
            "a_record_ids": " ".join(r.a_record_ids),
            "b_entry_ids": " ".join(r.b_entry_ids),
            "amount_a": _money(r.amount_a),
            "amount_b": _money(r.amount_b),
            "event_date": r.event_date.isoformat() if r.event_date else "",
            "location": r.location_code or "",
            "exception_codes": " ".join(r.exception_codes),
            "notes": " ".join(r.notes),
        }
        for r in result.records
    ]


def exception_rows(
    result: ReconciliationResult, rejects: list[InputReject], org_by_tenant: dict[uuid.UUID, str]
) -> list[dict[str, str]]:
    rows = []
    for d in result.discrepancies:
        r = reason(d.type.value)
        rows.append(
            {
                "severity": r.severity.value,
                "kind": r.kind.value,
                "reason_code": r.code,
                "org": org_by_tenant.get(d.tenant_id, "") if d.tenant_id else "",
                "match_key": d.match_key,
                "source": "reconciliation",
                "meaning": r.meaning,
                "action": r.action,
                "evidence": json.dumps({"values": d.values_by_system, **d.details}, sort_keys=True),
            }
        )
    for x in rejects:
        r = reason(x.code)
        rows.append(
            {
                "severity": r.severity.value,
                "kind": r.kind.value,
                "reason_code": r.code,
                "org": "",
                "match_key": "",
                "source": f"{x.file}:{x.row}",
                "meaning": r.meaning,
                "action": r.action,
                "evidence": json.dumps({"detail": x.detail, "row": x.raw}, sort_keys=True),
            }
        )
    assert all(reason(row["reason_code"]).kind != Kind.NOTE for row in rows)
    return sorted(
        rows, key=lambda row: (_SEVERITY_ORDER[Severity(row["severity"])], row["org"], row["match_key"], row["source"])
    )


def write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def run(locations: Path, system_a: Path, system_b: Path, out_dir: Path) -> tuple[list[dict], list[dict]]:
    data = load(locations, system_a, system_b)
    result = reconcile(data.events)
    reconciled = reconciled_rows(result, data.org_by_tenant)
    exceptions = exception_rows(result, data.rejects, data.org_by_tenant)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "reconciled.csv", RECONCILED_COLUMNS, reconciled)
    write_csv(out_dir / "exceptions.csv", EXCEPTION_COLUMNS, exceptions)
    return reconciled, exceptions
