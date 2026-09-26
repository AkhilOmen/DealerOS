from typing import Any

from app.db.enums import MatchKeyMethod, SourceSystem

_AMOUNT_COLUMN = {SourceSystem.SYSTEM_A: "total_value", SourceSystem.SYSTEM_B: "value"}
_OPTIONAL_A_FIELDS = ("actor_id", "category_code")


def notes_for(
    source_system: SourceSystem, match_key_method: MatchKeyMethod, raw_row: dict[str, Any]
) -> tuple[str, ...]:
    notes = []
    if match_key_method == MatchKeyMethod.NORMALIZED:
        notes.append("REF_NORMALIZED")
    if "," in (raw_row.get(_AMOUNT_COLUMN[source_system]) or ""):
        notes.append("AMOUNT_REFORMATTED")
    if source_system == SourceSystem.SYSTEM_A:
        notes.extend(f"SOURCE_FIELD_BLANK:{f}" for f in _OPTIONAL_A_FIELDS if not (raw_row.get(f) or "").strip())
    return tuple(notes)
