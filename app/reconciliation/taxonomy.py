from dataclasses import dataclass
from enum import Enum


class Kind(str, Enum):
    DISAGREEMENT = "DISAGREEMENT"
    INPUT = "INPUT"
    NOTE = "NOTE"


class Severity(str, Enum):
    CRITICAL = "CRITICAL"  # isolation or ownership is in doubt
    HIGH = "HIGH"  # money or existence differs
    MEDIUM = "MEDIUM"  # a detail differs
    INFO = "INFO"


@dataclass(frozen=True)
class Reason:
    code: str
    kind: Kind
    severity: Severity
    meaning: str
    action: str


_REASONS = [
    # --- disagreements between the systems -----------------------------------------------
    Reason(
        "TENANT_CONFLICT",
        Kind.DISAGREEMENT,
        Severity.CRITICAL,
        "The two systems place this record in locations that belong to different orgs.",
        "Hidden from both orgs until fixed. Confirm which org owns the record and correct the "
        "location in the system that has it wrong.",
    ),
    Reason(
        "DUPLICATE_IN_A",
        Kind.DISAGREEMENT,
        Severity.HIGH,
        "More than one System A record resolves to the same record id, so it can't be compared.",
        "Merge or renumber the System A records.",
    ),
    Reason(
        "MISSING_IN_B",
        Kind.DISAGREEMENT,
        Severity.HIGH,
        "System A has the record; System B has no entry for it.",
        "Add the entry in System B, or confirm the event didn't happen and void it in System A.",
    ),
    Reason(
        "ORPHAN_IN_B",
        Kind.DISAGREEMENT,
        Severity.HIGH,
        "System B has an entry pointing at a record System A doesn't have.",
        "Find the System A record (the reference may be wrong) or remove the System B entry.",
    ),
    Reason(
        "VALUE_MISMATCH",
        Kind.DISAGREEMENT,
        Severity.HIGH,
        "The amounts differ (System B's entries are summed first).",
        "Decide which amount is right and correct the other system. If 'b_equals_a_base_value' "
        "is set, System B probably recorded the amount before the adjustment.",
    ),
    Reason(
        "MISSING_VALUE",
        Kind.DISAGREEMENT,
        Severity.HIGH,
        "System B has the entry but no amount.",
        "Fill in the amount in System B.",
    ),
    Reason(
        "STATUS_MISMATCH",
        Kind.DISAGREEMENT,
        Severity.HIGH,
        "System A voided the record, but System B still carries an amount for it.",
        "Reverse the entry in System B, or un-void the record in System A if the void was a mistake.",
    ),
    Reason(
        "DUPLICATE_IN_B",
        Kind.DISAGREEMENT,
        Severity.MEDIUM,
        "System B has the same entry more than once (same amount, date and location).",
        "Remove the extra entry in System B. It was counted once when comparing amounts.",
    ),
    Reason(
        "DATE_MISMATCH",
        Kind.DISAGREEMENT,
        Severity.MEDIUM,
        "The systems record different dates for the event.",
        "Correct the date in the system that has it wrong.",
    ),
    Reason(
        "LOCATION_MISMATCH",
        Kind.DISAGREEMENT,
        Severity.MEDIUM,
        "The systems record different locations (both belong to the same org).",
        "Correct the location in the system that has it wrong.",
    ),
    # --- rows that could not be used ------------------------------------------------------
    Reason(
        "UNKNOWN_LOCATION",
        Kind.INPUT,
        Severity.CRITICAL,
        "The row's location isn't in locations.csv, so it can't be assigned to any org. Not loaded.",
        "Add the location to locations.csv (with its org) or correct the row's location, then reload.",
    ),
    Reason(
        "LOCATION_TENANT_CHANGED",
        Kind.INPUT,
        Severity.CRITICAL,
        "locations.csv moves an existing location to a different org. Refused.",
        "Confirm the ownership change; moving a location changes who can see its history.",
    ),
    Reason(
        "CONFLICTING_DUPLICATE_KEY",
        Kind.INPUT,
        Severity.HIGH,
        "The same id appears on several rows with different content; we can't tell which is right. None were loaded.",
        "Keep one correct row in the export and reload.",
    ),
    Reason(
        "UNPARSABLE_RECORD_ID",
        Kind.INPUT,
        Severity.HIGH,
        "A System A record id isn't in a recognisable form. Not loaded.",
        "Correct the record id in the System A export.",
    ),
    Reason(
        "VALIDATION_ERROR",
        Kind.INPUT,
        Severity.MEDIUM,
        "A required value is missing or unreadable (e.g. a date that isn't YYYY-MM-DD). Not loaded.",
        "Correct the value named in the detail and reload.",
    ),
    Reason(
        "WRONG_COLUMN_COUNT",
        Kind.INPUT,
        Severity.MEDIUM,
        "The row has more or fewer columns than the header (often an unquoted comma). Not loaded.",
        "Fix the row's quoting in the export and reload.",
    ),
    Reason(
        "DUPLICATE_ROW",
        Kind.INPUT,
        Severity.INFO,
        "An exact copy of an earlier row; the first copy was used.",
        "None needed; remove the copy from the export if convenient.",
    ),
    # --- notes: interpretations we made, visible on the reconciled record ----------------
    Reason(
        "REF_NORMALIZED",
        Kind.NOTE,
        Severity.INFO,
        "The record reference was written in a non-standard way (e.g. 'rec1034', ' REC - 1070 ', "
        "'1112') and was read as the standard id.",
        "None needed.",
    ),
    Reason(
        "AMOUNT_REFORMATTED",
        Kind.NOTE,
        Severity.INFO,
        "The amount used digit grouping (e.g. '1,25,400.00') and was read as a plain number.",
        "None needed.",
    ),
    Reason(
        "SPLIT_ENTRIES_SUMMED",
        Kind.NOTE,
        Severity.INFO,
        "System B split the record over several different entries; they were summed before comparing.",
        "None needed if the total matches.",
    ),
    Reason(
        "SOURCE_FIELD_BLANK",
        Kind.NOTE,
        Severity.INFO,
        "An optional field is blank in the source (e.g. System A's actor).",
        "Fill it in at the source if it matters for reporting.",
    ),
]

REASONS: dict[str, Reason] = {r.code: r for r in _REASONS}


def reason(code: str) -> Reason:
    return REASONS[code.split(":", 1)[0]]  # notes may carry a detail suffix, e.g. SOURCE_FIELD_BLANK:actor_id
